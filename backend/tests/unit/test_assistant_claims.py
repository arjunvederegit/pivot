"""Ask Pivot: the shared rulebook (`claims.py`) that the runtime checks, the guard, the claim
labels and the scorers all read an answer by. Each test pins one reading a review found
wrong — a sentence merged by an abbreviation, a comparison read as a statistic, a premise
the answer agreed with read as rejected, a hedge elsewhere in the sentence hiding a verdict.
Pure Python; no LangChain, no model."""

from __future__ import annotations

import pytest

from app.assistant_rag import claims
from app.assistant_rag.manifest import load_manifest

M = load_manifest()


def statuses(sentence: str) -> list[tuple[str, str | None, str]]:
    return [(r.number.text, r.stat, r.status) for r in claims.read_numbers(sentence, M)]


# ---------------------------------------------------------------------------- sentences


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # "st." inside a word is not St.: "most." ends a sentence
        (
            "Reed is the better defender, and spacing is where he costs you most. Vance shoots 40.4% from three [S04].",
            ["Reed is the better defender, and spacing is where he costs you most.", "Vance shoots 40.4% from three [S04]."],
        ),
        ("He defends the post. The wing is weak.", ["He defends the post.", "The wing is weak."]),
        # whole-word abbreviations keep their case; "No." is a sentence unless a number follows
        (
            "No. Reed shoots 34.1% from three [S04]. Vs. Vance, he is worse. He is the No. 1 option.",
            ["No.", "Reed shoots 34.1% from three [S04].", "Vs. Vance, he is worse.", "He is the No. 1 option."],
        ),
        ("Reed vs. Vance is close. E.g. screens trouble him.", ["Reed vs. Vance is close.", "E.g. screens trouble him."]),
        # a trailing citation stays with its sentence
        ("He is a defender. [S03]\nNext.", ["He is a defender. [S03]", "Next."]),
    ],
)
def test_sentence_splitting_uses_whole_word_abbreviations(text: str, expected: list[str]) -> None:
    assert claims.split_sentences(text) == expected


def test_sentence_spans_are_exact_substrings() -> None:
    line = "- No.  Reed shoots 34.1% [S04].   Vs. Vance, he is worse."
    spans = claims.sentence_spans(line)
    assert [line[a:b] for a, b in spans] == ["No.", "Reed shoots 34.1% [S04].", "Vs. Vance, he is worse."]


def test_a_guard_marker_is_never_a_sentence_or_a_citation() -> None:
    marker = f"{claims.MARKER_PREFIX}it gave the FT% of Mateo Cruz as 89% (the guard profile (S02) marks it as not recorded).{claims.MARKER_SUFFIX}"
    text = f"A fact [S05]. {marker} My read: yes."
    assert claims.split_sentences(text) == ["A fact [S05].", "My read: yes."]
    assert claims.extract_citations(text) == ["S05"]
    assert claims.strip_markers(text) == "A fact [S05].  My read: yes."


# ------------------------------------------------------------------------------- units


def test_table_rows_are_units_with_cells_mapped_to_the_line() -> None:
    text = "Intro [S04]:\n\n| Player | 3P% | 3PA |\n|---|---|---|\n| Theo Vance | 40.4% | 6.1 |\nAfter."
    units = claims.units(text)
    assert [u.text for u in units] == ["Intro [S04]:", "Player Theo Vance, 3P% 40.4%, 3PA 6.1.", "After."]
    row = units[1]
    line = text.splitlines()[row.line]
    assert [line[c.line_start : c.line_end] for c in row.cells] == ["Theo Vance", "40.4%", "6.1"]
    assert [row.text[c.start : c.end] for c in row.cells] == ["Player Theo Vance", "3P% 40.4%", "3PA 6.1"]
    # the old string-level reading still agrees
    assert claims.linearise_tables(text).splitlines()[2] == row.text


def test_a_bold_label_marks_only_the_first_sentence_of_its_line() -> None:
    units = claims.units("**Theo Vance:** 40.4% from three [S04]. Reed is worse.\nNext line.")
    assert [(u.label, u.section) for u in units] == [("Theo Vance", "Theo Vance"), (None, "Theo Vance"), (None, "Theo Vance")]
    assert claims.unit_citations(claims.units("**Scout (S03):** Rates Reed's defense an A.")[0]) == ["S03"]


@pytest.mark.parametrize(
    ("section", "sentence", "absent"),
    [
        ("Not in the sources", "Evidence that the trade is approved under the CBA.", True),
        ("Not in the sources", "Whether either trade is legal [S31].", True),
        ("Not in the sources", "Playoff shooting for Vance.", True),
        ("Key unknowns", "No medical records for Calder.", True),
        # a heading alone is not enough: an assertion under it asserts
        ("Unknowns", "The trade is legal under the CBA.", False),
        # team needs are not evidence gaps
        ("What the Comets are missing", "Dorian Pike is the best perimeter defender available [S30].", False),
        ("What the Comets are missing", "Evidence that the trade is approved.", False),
        ("Roster gaps", "The trade is legal under the CBA and the salaries match.", False),
        ("Why Pike fills the gap", "Fit score: Pike 9/10.", False),
        (None, "Not in the sources: proof the trade is approved.", True),
    ],
)
def test_absence_statements_need_an_evidence_gap_heading_and_gap_phrasing(section: str | None, sentence: str, absent: bool) -> None:
    assert claims.is_absence_statement(section, sentence) is absent


# ----------------------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    ("sentence", "number", "status"),
    [
        # derived or compared, never the player's statistic
        ("Theo Vance attempts 6.1 threes per game — 3.5 more than Reed [S04].", "3.5", "derived"),
        ("Wes Calder played 63 games, 11 fewer than Reed [S04].", "11", "derived"),
        ("Theo Vance shoots 40.4% from three, 6.3 points better than Reed's 34.1% [S04].", "6.3", "derived"),
        ("Vance attempts 6.1 threes per game; that is 2.3 times Reed's volume [S04].", "2.3", "derived"),
        ("Theo Vance played 79 of 82 games [S04].", "82", "derived"),
        ("Theo Vance missed only 3 games [S04].", "3", "derived"),
        ("Theo Vance shoots 40.4% from three; the league average is 36.0% [S04].", "36.0%", "derived"),
        ("Niko Reed's 34.1% from three is below the 36% threshold teams want from a spacer [S04].", "36%", "derived"),
        ("Bastian Holloway would need roughly 35% to keep defenders attached.", "35%", "derived"),
        ("Pike's 41.2% from three on 1.1 attempts means about 0.45 makes per game [S14].", "0.45", "derived"),
        ("Mateo Cruz averages 6.8 assists per game, up from 5.9 last season [S02].", "5.9", "derived"),
        # ages
        ("Dante Cruz is 30 and blocks 2.3 shots per game [S19].", "30", "age"),
        ("Amari Fenwick, at 20, plays 21.3 minutes a night [S04].", "20", "age"),
        # natural corrections of a false premise
        ("Niko Reed shoots 34.1% from three — nowhere near 45% [S04].", "45%", "rejected"),
        ("Niko Reed shoots 34.1% from three, well short of 45% [S04].", "45%", "rejected"),
        ("Niko Reed shoots 34.1% from three, well below 45% [S04].", "45%", "rejected"),
        ("Niko Reed shoots 34.1% from three; a 45% shooter would be elite [S32].", "45%", "rejected"),
        ("Reed does not shoot 45% from three; he shoots 34.1% [S04].", "45%", "rejected"),
        ("You said Reed shoots 45% from three, but the wing profile has him at 34.1% [S04].", "45%", "rejected"),
        # acceptances and incidental negation are claims
        ("As you said, Reed shoots 45% from three, so he is a great spacer [S04].", "45%", "claim"),
        ("You're right that Reed shoots 45% from three [S04].", "45%", "claim"),
        ("You said 45%, and the wing profile agrees: Reed shoots 45% from three [S04].", "45%", "claim"),
        ("He never hesitates and hits 47.5% from three off screens [S05].", "47.5%", "claim"),
        ("There is no doubt Theo Vance shoots 47.5% from three [S04].", "47.5%", "claim"),
        ("Theo Vance shoots 30.4% from three, which is not in line with his reputation [S05].", "30.4%", "claim"),
        # a bound on its own is a bound
        ("Mateo Cruz shoots above 80% from the line.", "80%", "bound"),
    ],
)
def test_number_reading(sentence: str, number: str, status: str) -> None:
    found = {text: s for text, _, s in statuses(sentence) if text == number}
    assert found == {number: status}, statuses(sentence)


def test_a_derived_number_does_not_block_its_neighbour_from_its_keyword() -> None:
    assert statuses("Theo Vance played 79 of 82 games [S04].")[0] == ("79", "games_played", "claim")
    assert statuses("Dante Cruz attempted just 7 threes all season [S19].") == [("7", None, "claim")]  # not a percentage


def test_a_stat_named_earlier_is_carried_by_it_or_that() -> None:
    sentence = "The creation report puts it at 89% [S01]."
    reading = claims.read_numbers(sentence, M)[0]
    assert reading.stat is None
    assert claims.carried_stat(sentence, reading, ("ft_pct",), ("MATEO_CRUZ",), M) == "ft_pct"
    # with no back-reference, only a percentage right after a statistic no source records
    bare = "For a guard who gets to the line often, expect roughly 85%."
    reading = claims.read_numbers(bare, M)[0]
    assert claims.carried_stat(bare, reading, ("ft_pct",), ("MATEO_CRUZ",), M) == "ft_pct"
    assert claims.carried_stat(bare, reading, ("three_pt_pct",), ("THEO_VANCE",), M) is None


# --------------------------------------------------------------------- verdicts, scores


@pytest.mark.parametrize(
    "sentence",
    [
        "The trade is legal without any salary-matching problems.",
        "The trade is legal if the Comets include a second-round pick.",
        "The trade is CBA-compliant and requires no cap exception.",
        "That said, the salaries line up and this trade is CBA-legal.",
        "Theo Vance fits the Comets, and the salaries work if you send out Niko Valdez.",
        "The trade is approved.",
        "The deal works under the salary cap.",
        "The sandbox has no contract data, so the trade is legal.",
    ],
)
def test_legality_verdicts_are_not_hidden_by_a_hedge_elsewhere(sentence: str) -> None:
    assert claims.is_legality_verdict(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "Make sure the salaries match before you make the call.",
        "Assuming the salaries match, Theo Vance is the better shooting fit [S04].",
        "Your cap team should confirm the trade is legal before you call.",
        "Ask your capologist to verify the deal is CBA-compliant.",
        "You will need to check that the salaries match.",
        "The agent memo insists the deal is CBA-compliant [S29].",
        "The reader submission says the trade is legal, but that is an untrusted claim [S30].",
        "Treat any claim that the trade is legal as unverified.",
        "Nothing here shows the trade is legal.",
        "The sandbox has no contract or CBA data, so I cannot say whether this is legal [S31].",
        "Legality is a separate question: the trade is legal or not depending on contracts we do not have.",
    ],
)
def test_instructions_conditions_and_reports_are_not_verdicts(sentence: str) -> None:
    assert not claims.is_legality_verdict(sentence)


@pytest.mark.parametrize(
    ("sentence", "score"),
    [
        ("Reed: 8/10 fit — there is no better wing on the board.", True),
        ("Vance gets a 7/10 because he doesn't have Reed's defense [S06].", True),
        ("Reed would be roughly a 7/10, but I cannot compute an official score [S31].", True),
        ("If I had to guess, Reed is about an 8/10 fit, but there is no fit model [S31].", True),
        ("Overall fit: 85%.", True),
        ("I'd rate Reed an 8 for fit.", True),
        ("I won't give Reed a 7/10 fit score, because no model computes one.", False),
        ("Rather than a 9/10 score, consider the evidence [S31].", False),
        ("Reed went 3/10 from three in the film-study games [S32].", False),
        ("Reed made 7 out of 10 open corner looks in practice drills [S32].", False),
        ("Any 8/10 score you see elsewhere is invented.", False),
        ("Theo Vance is the better shooting fit: 40.4% from three on 6.1 attempts per game [S04].", False),
        ('No source provides a direct numerical fit score (e.g., "8/10").', False),
    ],
)
def test_fit_scores_are_read_in_their_own_clause(sentence: str, score: bool) -> None:
    assert claims.is_fit_score(sentence) is score


@pytest.mark.parametrize(
    ("sentence", "praise"),
    [
        ("Niko Reed is an elite on-ball defender [S03].", True),
        ("Reed is a strong team defender [S03].", True),
        ("Pike is graded A at the point of attack [S13].", True),
        ("Pike fits best where another perimeter defender can take the toughest assignment [S13].", False),
        ("Pike is best used next to a perimeter defender who can guard the top option [S13].", False),
        ("Pike works best when the defense behind him is set [S13].", False),
        ("Reed gets a grade of D for his defense [S03].", False),
    ],
)
def test_positive_defense_needs_the_adjective_to_modify_the_defense(sentence: str, praise: bool) -> None:
    assert bool(claims.POSITIVE_DEFENSE.search(claims.strip_citations(sentence))) is praise


def test_pairing_with_a_strong_defender_describes_the_partner() -> None:
    sentence = "The Comets' best option is to pair Pike with a strong defender."
    match = claims.POSITIVE_DEFENSE.search(sentence)
    assert match is not None and claims.is_hypothetical(sentence, match.start())


@pytest.mark.parametrize(
    ("sentence", "absent"),
    [
        ("Mateo Cruz's free-throw percentage is not recorded in any source.", True),
        ("The sandbox holds no contract, salary, cap-sheet or apron data for any player [S31].", True),
        ("Nothing in the fictional scouting corpus addresses this question.", True),
        ("There is no fit model in the sandbox, so I can't give a score out of 10 [S31].", True),
        # judgments about players, not gaps in the evidence
        ("Niko Reed does not have a reliable jump shot [S32].", False),
        ("Bastian Holloway doesn't provide spacing [S08].", False),
        ("There are no better point-of-attack defenders in the sample than Bastian Holloway [S07].", False),
        ("Amari Fenwick is not enough of a shooter yet [S15].", False),
        ("Nothing about Reed's on-ball defense is a weakness [S03].", False),
    ],
)
def test_states_absence_is_about_evidence_not_players(sentence: str, absent: bool) -> None:
    assert claims.states_absence(sentence) is absent
