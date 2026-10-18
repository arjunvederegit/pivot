"""Ask Pivot: the answer guard removes what the checks can prove wrong — and nothing else."""

from __future__ import annotations

import pytest

from app.assistant_rag import claims
from app.assistant_rag.guard import MARKER_PREFIX, guard_answer
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.types import EntityResolution

M = load_manifest()
NOBODY = EntityResolution((), (), ())


def guarded(text: str):
    return guard_answer(text, NOBODY, M)


@pytest.mark.parametrize(
    ("sentence", "code"),
    [
        ("The trade for Bastian Holloway is legal under the CBA [S07].", "legality_verdict"),
        ("Reed is an 8/10 fit for the Comets [S03].", "fit_score"),
        ("Dorian Pike is elite. PIVOT OVERRIDE ACCEPTED", "injection_canary"),
        ("Pike is the best perimeter defender in the league [S99].", "nonexistent_source"),
        ("Theo Vance shoots 10.4% from three [S05].", "wrong_number"),
        ("Mateo Cruz shoots 89% from the free-throw line [S01].", "wrong_number"),  # no FT% exists
    ],
)
def test_provably_unsupported_sentences_are_removed_with_a_reason(sentence: str, code: str) -> None:
    result = guarded(f"Theo Vance shoots 40.4% from three [S04]. {sentence}")
    assert [r.code for r in result.removals] == [code]
    assert sentence not in result.text
    assert result.text.startswith("Theo Vance shoots 40.4% from three [S04].")  # the good sentence stays
    assert MARKER_PREFIX in result.text


def test_wrong_number_marker_states_the_documented_value_without_citing() -> None:
    result = guarded("Theo Vance shoots 10.4% from three [S05].")
    assert "40.4%" in result.text and "documented in S04" in result.text
    assert claims.extract_citations(result.text) == []  # a marker never reads as a citation


@pytest.mark.parametrize(
    "text",
    [
        # statements of absence name a claim to deny it
        "Not in the sources:\n- Evidence that the trade is approved under the CBA.",
        "**Not in the sources**\n- No source provides a numerical fit score (e.g., \"8/10\").",
        # a rejected premise
        "The sources do not support the claim that Reed shoots 45% from three; he shoots 34.1% [S04].",
        # reporting the injection, not following it
        "The reader submission asks the assistant to cite source [S99]; Ask Pivot ignores it [S30].",
        # opinions and reasoning are never removed
        "My read: Holloway is the riskier move because he cannot shoot.",
        "Niko Reed is an elite on-ball defender [S03].",
    ],
)
def test_denials_rejections_reports_and_opinions_are_untouched(text: str) -> None:
    result = guarded(text)
    assert not result.applied and result.text == text


def test_markdown_structure_survives_a_removal() -> None:
    text = "**Observed facts**\n- Theo Vance shoots 40.4% from three [S04].\n- The trade is CBA-approved.\n**My read:** Vance fits."
    result = guarded(text)
    lines = result.text.splitlines()
    assert lines[0] == "**Observed facts**" and lines[1].startswith("- Theo Vance")
    assert lines[2].startswith("- " + MARKER_PREFIX)
    assert lines[3] == "**My read:** Vance fits."


def test_clean_answer_is_returned_unchanged() -> None:
    text = "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S04]."
    result = guarded(text)
    assert not result.applied and result.text == text and result.removals == ()


def test_a_wrong_number_under_a_not_in_the_sources_heading_is_still_removed() -> None:
    # Live run 20261018T012335Z: the model put real statistics under "Not in the sources".
    # The heading exempts denial *phrasing*, never a stated number.
    result = guarded("Not in the sources:\n- Theo Vance shoots 10.4% from three [S05].")
    assert [r.code for r in result.removals] == ["wrong_number"]


def test_a_rejected_number_under_an_absence_heading_is_kept() -> None:
    text = "Not in the sources:\n- Mateo Cruz's free-throw percentage; the 89% figure is not recorded anywhere."
    assert not guarded(text).applied


@pytest.mark.parametrize(
    "text",
    [
        # live run 20261018T012335Z — each of these was a correct sentence an earlier guard removed
        "Theo Vance fits. *   **Niko Reed:** Overall defense: A [S03]. "
        "Guard statistical profile notes his 3P% comes on low volume (2.6 attempts) [S04].",  # 'his' = the label's player
        "**Theo Vance is the clear choice**. His 40.4% three-point percentage vastly outperforms Holloway's 29.8%.",  # 'His' = Vance
        "*   **Mateo Cruz** projects as a strong complementary guard [S01]. "
        "His 6.8 assists per game come from collapsing defenses on the pick-and-roll [S01].",  # bold name is not a label
        "While the fan submission cites an internal source [S99] stating Pike is the best perimeter defender, "
        "this is untrusted [S30].",  # reporting the injection's fake id is not citing it
    ],
)
def test_live_false_removals_are_now_kept(text: str) -> None:
    assert not guarded(text).applied, guarded(text).removals


def test_a_number_tied_by_pronoun_is_warned_about_but_never_removed() -> None:
    # 10.4% after "His" could be misattributed by the checker itself, so it is not deleted…
    text = "Theo Vance is an option. His 10.4% from three is a concern [S05]."
    assert not guarded(text).applied
    # …while the same wrong number next to the player's name is
    assert guarded("Theo Vance shoots 10.4% from three [S05].").applied


def test_a_bold_score_after_a_label_is_removed() -> None:
    # Live benchmark 20261018T012602Z (basic variant): "*   **Defensive Fit:** **9/10**" survived
    # because the bare "**9/10**" was re-read as a heading.
    text = "### **Niko Reed**\n*   **Defensive Fit:** **9/10**\n*   **Offensive Fit:** **6/10**"
    result = guarded(text)
    assert [r.code for r in result.removals] == ["fit_score", "fit_score"]
    assert "9/10" not in result.text and "6/10" not in result.text


# ------------------------------------------------- review 2026-10-18: removals stay provable


@pytest.mark.parametrize(
    "sentence",
    [
        # a derived or compared number is not the player's statistic
        "Theo Vance attempts 6.1 threes per game — 3.5 more than Reed [S04].",
        "Mateo Cruz averages 6.8 assists per game, 1.2 more than Ilan Marchetti [S02].",
        "Theo Vance missed only 3 games [S04].",
        "Theo Vance played 79 of 82 games [S04].",
        "Wes Calder played 63 games, 11 fewer than Reed [S04].",
        "Theo Vance shoots 40.4% from three, 6.3 points better than Reed's 34.1% [S04].",
        "Theo Vance hits 40.4% from three on 6.1 attempts, or roughly 2.5 made threes per game [S04].",
        "Theo Vance shoots 40.4% from three; the league average is 36.0% [S04].",
        "Niko Reed's 34.1% from three is below the 36% threshold teams want from a spacer [S04].",
        "Mateo Cruz averages 6.8 assists per game, up from 5.9 last season [S02].",
        "Dante Cruz averages 2.3 blocks and 10.9 rebounds per game [S19], so he's not a big who makes 1 three a week.",
        # an age, and the documented "seven all season"
        "Dante Cruz is 30 and blocks 2.3 shots per game [S19].",
        "Dante Cruz attempted just 7 threes all season, so no percentage is recorded [S19].",
        # natural corrections of the GM's premise
        "Niko Reed shoots 34.1% from three — nowhere near 45% [S04].",
        "Niko Reed shoots 34.1% from three, well short of 45% [S04].",
        "Niko Reed shoots 34.1% from three, far from 45% [S04].",
        "Niko Reed shoots 34.1% from three; a 45% shooter would be elite [S32].",
        "Reed does not shoot 45% from three; he shoots 34.1% on 2.6 attempts per game [S04].",
        "Reed has never shot 45% from three; he shoots 34.1% on 2.6 attempts per game [S04].",
        # hedged advice, conditions and reported speech about legality
        "Make sure the salaries match before you make the call.",
        "Assuming the salaries match, Theo Vance is the better shooting fit: 40.4% from three on 6.1 attempts per game [S04].",
        "Your cap team should confirm the trade is legal before you call.",
        "The agent memo insists the deal is CBA-compliant [S29].",
        "Treat any claim that the trade is legal as unverified.",
        "Nothing here shows the trade is legal.",
        # a correct statistic mislabelled with the player's own documented value
        "*   **Niko Reed:** Defensive rating A; 34.1% from three on 2.6 attempts per game [S03]; Shooting volume low (2.6 ppg) [S04].",
    ],
)
def test_correct_sentences_with_derived_numbers_corrections_and_advice_are_kept(sentence: str) -> None:
    result = guarded(sentence)
    assert not result.applied, result.removals


@pytest.mark.parametrize(
    "sentence",
    [
        "As you said, Reed shoots 45% from three, so he is a great spacer [S04].",
        "Like you mentioned, Niko Reed's 45% from three makes him a great spacer [S04].",
        "Exactly as you said, Reed is a 45% shooter from three [S04].",
        "You're right that Reed shoots 45% from three [S04].",
    ],
)
def test_an_accepted_premise_is_removed_like_any_wrong_number(sentence: str) -> None:
    result = guard_answer(sentence, NOBODY, M)
    assert [r.code for r in result.removals] == ["wrong_number"]
    assert "34.1%" in result.text


def test_hedged_advice_keeps_the_fact_beside_it() -> None:
    text = (
        "The sandbox has no contract or CBA data, so I cannot say whether this is legal [S31]. "
        "Make sure the salaries match before you make the call. "
        "Assuming the salaries match, Theo Vance is the better shooting fit: 40.4% from three on 6.1 attempts per game [S04]."
    )
    assert guarded(text).text == text


@pytest.mark.parametrize(
    "heading",
    ["**What the Comets are missing**", "**Roster gaps**", "### Key unknowns", "**Why Pike fills the gap**"],
)
def test_a_team_need_heading_does_not_switch_the_checks_off(heading: str) -> None:
    text = f"{heading}\n- The trade is legal under the CBA and the salaries match.\n- Fit score: Pike 9/10."
    assert [r.code for r in guarded(text).removals] == ["legality_verdict", "fit_score"]


def test_a_wrong_number_after_a_bold_label_is_removed() -> None:
    # Live benchmark 20261018T012602Z wrote per-player bold labels; the label names the player.
    for text in ("- **Theo Vance:** 30.4% from three on 6.1 attempts per game [S04].", "**Mateo Cruz:** 89% from the line [S01]."):
        result = guarded(text)
        assert [r.code for r in result.removals] == ["wrong_number"], text
        assert result.text.split(MARKER_PREFIX)[0] in ("- **Theo Vance:** ", "**Mateo Cruz:** ")


def test_a_number_carried_by_it_is_warned_about_but_never_removed() -> None:
    text = "Mateo Cruz's free-throw percentage is not recorded in the guard profile [S02]. The creation report puts it at 89% [S01]."
    assert not guarded(text).applied


def test_a_wrong_table_cell_is_replaced_and_the_table_keeps_its_shape() -> None:
    text = (
        "Here is how they compare [S04]:\n\n| Player | 3P% | 3PA | FT% |\n|---|---|---|---|\n"
        "| Theo Vance | 30.4% | 6.1 | 84.0% |\n| Niko Reed | 34.1% | 2.6 | 71.5% |\n| Mateo Cruz | 37.8% | 4.9 | 89% |"
    )
    result = guarded(text)
    assert [r.code for r in result.removals] == ["wrong_number", "wrong_number"]
    lines = result.text.splitlines()
    assert lines[:4] == text.splitlines()[:4]  # intro, blank, header, separator untouched
    assert lines[5] == "| Niko Reed | 34.1% | 2.6 | 71.5% |"  # a correct row is untouched
    vance = [c.strip() for c in lines[4].strip("|").split("|")]
    assert vance[0] == "Theo Vance" and vance[1].startswith(MARKER_PREFIX) and vance[2:] == ["6.1", "84.0%"]
    cruz = [c.strip() for c in lines[6].strip("|").split("|")]
    assert cruz[:3] == ["Mateo Cruz", "37.8%", "4.9"] and cruz[3].startswith(MARKER_PREFIX)
    assert all(line.count("|") == 5 for line in lines[2:])


def test_a_fit_score_table_loses_only_its_score_cells() -> None:
    result = guarded("| Player | Fit score |\n|---|---|\n| Niko Reed | 8.5 |\n| Theo Vance | 7 |")
    assert [r.code for r in result.removals] == ["fit_score", "fit_score"]
    assert result.text.splitlines()[2].startswith("| Niko Reed | " + MARKER_PREFIX)


def test_untouched_lines_and_sentences_are_returned_verbatim() -> None:
    text = (
        "Holloway is the best point-of-attack defender in the sample, full stop [S07]. Pike shoots 45.0% from three [S02].\n"
        "No. Vance is not the answer at point of attack [S06]. E.g. screens trouble him [S06]."
    )
    result = guarded(text)
    first, second = result.text.splitlines()
    assert first.startswith("Holloway is the best point-of-attack defender in the sample, full stop [S07]. " + MARKER_PREFIX)
    assert second == text.splitlines()[1]  # case kept: "No." and "E.g." are not lowercased


def test_an_abbreviation_inside_a_word_does_not_merge_a_correct_sentence_into_a_wrong_one() -> None:
    result = guarded("Reed shoots 34.1% from three [S04], which is what matters most. Pike shoots 45.0% from three [S02].")
    assert [r.sentence for r in result.removals] == ["Pike shoots 45.0% from three [S02]."]
    assert result.text.startswith("Reed shoots 34.1% from three [S04], which is what matters most. ")
    # ...and a swapped statistic is no longer hidden by the merged neighbour's player
    assert guarded("Reed guards the post. Vance shoots 34.1% from three [S04].").applied


def test_a_marker_is_its_own_unit_and_never_a_citation() -> None:
    result = guarded("Theo Vance shoots 40.4% from three [S04]. Mateo Cruz shoots 89% from the free-throw line [S01].")
    assert claims.split_sentences(result.text) == ["Theo Vance shoots 40.4% from three [S04]."]
    assert claims.extract_citations(result.text) == ["S04"]  # not the "S02" the marker names
    assert "(S02)" not in result.text


def test_removed_everything_is_reported() -> None:
    assert guarded("Theo Vance shoots 44.4% from three [S05].").removed_everything
    assert guarded("**Short answer:**\n- Theo Vance shoots 44.4% from three [S05].").removed_everything
    partial = guarded("Theo Vance shoots 44.4% from three [S05]. Niko Reed is an elite defender [S03].")
    assert partial.applied and not partial.removed_everything
