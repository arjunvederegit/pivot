"""Entity resolution for Ask Pivot: fictional names, ambiguity, history, real NBA names.

Uses the packaged fictional manifest (stable ids and aliases), never document prose.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from app.assistant_rag import entities as entities_module
from app.assistant_rag.entities import detect_real_nba_names, player_mentions, resolve_entities
from app.assistant_rag.manifest import MANIFEST_PATH, Manifest, load_manifest, parse_manifest
from app.assistant_rag.types import Ambiguity, EntityRef, HistoryTurn


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return load_manifest()


def user(text: str) -> HistoryTurn:
    return HistoryTurn("user", text)


def assistant(text: str) -> HistoryTurn:
    return HistoryTurn("assistant", text)


def ids(resolution) -> list[tuple[str, str]]:
    return [(ref.id, ref.how) for ref in resolution.resolved]


# -------------------------------------------------------------------- named + ambiguous


def test_full_name_wins_over_the_bare_surname_inside_it(manifest: Manifest) -> None:
    result = resolve_entities("Is Mateo Cruz a good passer?", (), None, manifest)
    assert ids(result) == [("MATEO_CRUZ", "named")]
    assert result.ambiguous == ()


def test_bare_shared_surname_is_ambiguous_without_history(manifest: Manifest) -> None:
    result = resolve_entities("Is Cruz a good passer?", (), None, manifest)
    assert result.resolved == ()
    assert result.ambiguous == (Ambiguity("Cruz", ("DANTE_CRUZ", "MATEO_CRUZ")),)


def test_bare_shared_first_name_is_ambiguous_even_if_only_one_player_lists_it(manifest: Manifest) -> None:
    # The manifest aliases "Niko" to Niko Valdez only; Niko Reed is a Niko too.
    result = resolve_entities("Can Niko guard wings?", (), None, manifest)
    assert result.ambiguous == (Ambiguity("Niko", ("NIKO_REED", "NIKO_VALDEZ")),)
    assert result.player_ids == ()


def test_possessive_bare_name_is_reported_without_the_possessive(manifest: Manifest) -> None:
    result = resolve_entities("What is Cruz's role?", (), None, manifest)
    assert result.ambiguous[0].mention == "Cruz"


def test_history_disambiguates_a_bare_name(manifest: Manifest) -> None:
    history = (user("Tell me about Mateo Cruz."), assistant("Mateo Cruz is a lead guard [S01]."))
    result = resolve_entities("Is Cruz a good passer?", history, None, manifest)
    assert ids(result) == [("MATEO_CRUZ", "carried_from_history")]
    assert result.ambiguous == ()


def test_newest_history_turn_that_names_a_candidate_decides(manifest: Manifest) -> None:
    history = (user("Tell me about Mateo Cruz."), assistant("Sure."), user("And Dante Cruz?"))
    result = resolve_entities("Is Cruz the better rebounder?", history, None, manifest)
    assert result.player_ids == ("DANTE_CRUZ",)


def test_history_turn_naming_both_candidates_keeps_it_ambiguous(manifest: Manifest) -> None:
    history = (user("Compare Mateo Cruz and Dante Cruz."), assistant("Mateo Cruz creates; Dante Cruz protects the rim."))
    result = resolve_entities("Is Cruz the better passer?", history, None, manifest)
    assert result.player_ids == ()
    assert [a.mention for a in result.ambiguous] == ["Cruz"]


def test_history_lookback_is_limited_to_four_turns(manifest: Manifest) -> None:
    history = (user("Tell me about Mateo Cruz."), assistant("ok"), user("ok"), assistant("ok"), user("ok"))
    result = resolve_entities("Is Cruz a good passer?", history, None, manifest)
    assert result.ambiguous and not result.player_ids


def test_bare_name_absorbed_when_the_question_names_a_candidate_in_full(manifest: Manifest) -> None:
    result = resolve_entities("Mateo Cruz vs Dante Cruz: which Cruz fits better?", (), None, manifest)
    assert ids(result) == [("MATEO_CRUZ", "named"), ("DANTE_CRUZ", "named")]
    assert result.ambiguous == ()


def test_repeated_ambiguous_mention_is_reported_once(manifest: Manifest) -> None:
    result = resolve_entities("Cruz or Cruz?", (), None, manifest)
    assert len(result.ambiguous) == 1


def test_shared_first_name_is_ambiguous_even_when_no_alias_lists_it() -> None:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for player in raw["players"]:
        player["aliases"] = [a for a in player["aliases"] if a != "Niko"]
    stripped = parse_manifest(raw)
    result = resolve_entities("Can Niko guard wings?", (), None, stripped)
    assert result.ambiguous == (Ambiguity("Niko", ("NIKO_REED", "NIKO_VALDEZ")),)
    assert resolve_entities("can niko guard?", (), None, stripped).ambiguous == ()  # proper noun only


def test_player_mentions_widens_only_shared_names(manifest: Manifest) -> None:
    mentions = player_mentions("Mateo and Niko and Reed", manifest)
    assert [(m.text, m.entity_ids) for m in mentions] == [
        ("Mateo", ("MATEO_CRUZ",)),
        ("Niko", ("NIKO_REED", "NIKO_VALDEZ")),
        ("Reed", ("NIKO_REED",)),
    ]


# ----------------------------------------------------------------------- carry-forward


@pytest.mark.parametrize("question", ["Would you change your ranking?", "What about him?", "And the first target?"])
def test_follow_up_naming_nobody_carries_players_from_the_last_turns(manifest: Manifest, question: str) -> None:
    history = (
        user("Rank Bastian Holloway and Theo Vance for us."),
        assistant("Theo Vance first, then Bastian Holloway; Niko Reed is a strong alternative."),
    )
    result = resolve_entities(question, history, None, manifest)
    # most recent turn (assistant) first, in its text order; the user turn adds nothing new
    assert ids(result) == [
        ("THEO_VANCE", "carried_from_history"),
        ("BASTIAN_HOLLOWAY", "carried_from_history"),
        ("NIKO_REED", "carried_from_history"),
    ]


def test_carry_forward_is_capped_and_ignores_older_turns(manifest: Manifest) -> None:
    history = (
        user("What about Dorian Pike?"),  # older than the latest user turn: ignored
        assistant("Pike is covered in [S13]."),
        user("Compare Niko Reed and Theo Vance."),
        assistant("Bastian Holloway, Wes Calder, Amari Fenwick, Ilan Marchetti and Lucas Achterberg."),
    )
    result = resolve_entities("Would you change your ranking?", history, None, manifest)
    assert result.player_ids == ("BASTIAN_HOLLOWAY", "WES_CALDER", "AMARI_FENWICK", "ILAN_MARCHETTI")


def test_no_carry_forward_when_the_question_names_a_player(manifest: Manifest) -> None:
    history = (user("Tell me about Theo Vance."), assistant("Theo Vance shoots well."))
    result = resolve_entities("And Niko Reed?", history, None, manifest)
    assert result.player_ids == ("NIKO_REED",)


def test_no_carry_forward_when_the_question_has_an_unresolved_ambiguous_name(manifest: Manifest) -> None:
    history = (user("Tell me about Theo Vance."), assistant("Theo Vance shoots well."))
    result = resolve_entities("What about Cruz?", history, None, manifest)
    assert result.player_ids == ()
    assert result.ambiguous


def test_ambiguous_names_in_history_are_not_carried(manifest: Manifest) -> None:
    history = (user("Tell me about Cruz."), assistant("Which Cruz do you mean?"))
    assert resolve_entities("Would you change your ranking?", history, None, manifest).resolved == ()


# ------------------------------------------------------------------------------ teams


def test_selected_team_is_resolved_last(manifest: Manifest) -> None:
    result = resolve_entities("Is Niko Reed a fit?", (), "COMETS", manifest)
    assert result.resolved == (
        EntityRef("NIKO_REED", "Niko Reed", "player", "named"),
        EntityRef("COMETS", "Hartford Comets", "team", "selected_team"),
    )
    assert result.team_ids == ("COMETS",)


def test_named_selected_team_appears_once_as_named(manifest: Manifest) -> None:
    result = resolve_entities("What do the Comets need?", (), "COMETS", manifest)
    assert ids(result) == [("COMETS", "named")]


def test_named_teams_and_clubs_in_text_order(manifest: Manifest) -> None:
    result = resolve_entities("Could the Tides beat the Mesa Foxes?", (), "COMETS", manifest)
    assert [(r.id, r.kind, r.how) for r in result.resolved] == [
        ("TIDES", "team", "named"),
        ("MESA_FOXES", "club", "named"),
        ("COMETS", "team", "selected_team"),
    ]


def test_unknown_selected_team_is_ignored(manifest: Manifest) -> None:
    assert resolve_entities("Who defends?", (), "NOPE", manifest).resolved == ()


def test_order_is_named_then_carried_then_selected(manifest: Manifest) -> None:
    history = (user("Tell me about Mateo Cruz."), assistant("Mateo Cruz creates [S01]."))
    result = resolve_entities("Is Cruz better than Theo Vance?", history, "TIDES", manifest)
    assert ids(result) == [
        ("THEO_VANCE", "named"),
        ("MATEO_CRUZ", "carried_from_history"),
        ("TIDES", "selected_team"),
    ]


# --------------------------------------------------------------------- real NBA names

needs_nba_api = pytest.mark.skipif(
    importlib.util.find_spec("nba_api") is None, reason="nba_api static data not installed"
)


@needs_nba_api
def test_detects_real_player_full_names_case_insensitively() -> None:
    assert detect_real_nba_names("How does LeBron James compare?") == ("LeBron James",)
    assert detect_real_nba_names("how does lebron james compare?") == ("LeBron James",)
    assert detect_real_nba_names("Is LeBron James's defense good?") == ("LeBron James",)


@needs_nba_api
def test_accents_and_hyphens_fold_to_the_canonical_spelling() -> None:
    found = detect_real_nba_names("Luka Doncic or Shai Gilgeous-Alexander?")
    assert found == ("Luka Dončić", "Shai Gilgeous-Alexander")


@needs_nba_api
def test_bare_surnames_are_not_flagged() -> None:
    assert detect_real_nba_names("Is James a better shooter than Curry?") == ()


@needs_nba_api
def test_team_full_names_and_distinctive_nicknames() -> None:
    assert detect_real_nba_names("Trade with the Lakers") == ("Los Angeles Lakers",)
    assert detect_real_nba_names("the los angeles lakers") == ("Los Angeles Lakers",)
    assert detect_real_nba_names("Sixers and Trail Blazers") == ("Philadelphia 76ers", "Portland Trail Blazers")
    assert detect_real_nba_names("The Lakers and the Los Angeles Lakers") == ("Los Angeles Lakers",)


@needs_nba_api
@pytest.mark.parametrize(
    "text",
    [
        "the tides turned in the fourth",
        "that was a heat check",
        "lakers",  # nicknames are case-sensitive
        "The Comets need a stopper",
        "The magic of a good closeout; thunder in the building",
        "Niko Reed, Theo Vance, Mateo Cruz and the Savannah Tides",
    ],
)
def test_no_false_positives(text: str) -> None:
    assert detect_real_nba_names(text) == ()


@needs_nba_api
def test_fictional_names_are_never_flagged_even_if_real(manifest: Manifest) -> None:
    assert detect_real_nba_names("LeBron James", exclude=["LeBron James"]) == ()
    assert detect_real_nba_names("Lakers", exclude=["Lakers"]) == ()
    assert detect_real_nba_names("", exclude=()) == ()


@needs_nba_api
def test_resolution_reports_real_names_as_unrecognized(manifest: Manifest) -> None:
    result = resolve_entities("Would Niko Reed beat the Celtics?", (), None, manifest)
    assert result.player_ids == ("NIKO_REED",)
    assert result.unrecognized == ("Boston Celtics",)


def test_missing_nba_api_data_means_nothing_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entities_module, "_real_name_index", lambda: None)
    assert detect_real_nba_names("LeBron James and the Lakers") == ()


# ------------------------------------------------------------------- unknown names
# A basketball question about an invented name ("Corwin Ashby's three-point percentage")
# retrieves on-topic passages, so retrieval cannot catch it; entity resolution must.


@pytest.mark.parametrize(
    ("question", "unknown"),
    [
        ("What's Corwin Ashby's three-point percentage this season?", ("Corwin Ashby",)),
        ("Scout the Portland Ospreys' starting center for me.", ("Portland Ospreys",)),
        ("Who is Jordan Blake?", ("Jordan Blake",)),
        ("Is Niko Reed better than Theo Vance?", ()),
        ("We're the Hartford Comets. Who fits?", ()),
        ("What did Dana Whitfield say about Reed?", ()),  # an analyst in the corpus
        ("What does Pivot Intelligence know about the Mesa Foxes?", ()),  # product term + club
        ("In January Theo Vance looked better", ()),  # month + player
        ("Point Guard depth for the Comets?", ()),
    ],
)
def test_unknown_names_are_flagged_and_known_ones_are_not(question: str, unknown: tuple[str, ...]) -> None:
    manifest = load_manifest()
    assert resolve_entities(question, (), None, manifest).unknown_names == unknown


def test_a_name_sharing_a_fictional_surname_is_left_to_the_ambiguity_rules() -> None:
    entities = resolve_entities("Is Carlos Cruz any good?", (), None, load_manifest())
    assert entities.unknown_names == () and [a.mention for a in entities.ambiguous] == ["Cruz"]


def test_a_real_nba_name_is_not_double_reported_as_unknown() -> None:
    entities = resolve_entities("How does LeBron James compare to Niko Reed?", (), None, load_manifest())
    assert entities.unrecognized == ("LeBron James",) and entities.unknown_names == ()


@pytest.mark.parametrize(
    "question",
    [
        "Hey Pivot, who fits our spacing needs?",  # greeting + product name
        "Thanks Pivot. And his defense?",
        "What does Coach Grant want in the closing lineup?",  # title + corpus author (Hollis Grant)
        "Per Dana Whitfield, who is the best wing defender?",
        "Which wing is the Best Fit?",  # ordinary words the manifest writes in lowercase
        "Who is the best Rim Protector?",
        "Summarize the Method Guide on spacing.",
        "Quick Question: who is the best shooter?",
        "Is Theo Vance an All Star caliber defender?",
        "Who Is The Best Defender?",  # Title Case: capitals carry no signal
        "Which Players Fit Our Needs?",
        "Who is the best Pick And Roll defender?",
    ],
)
def test_capitalised_phrases_that_are_not_names_are_not_flagged(question: str) -> None:
    assert resolve_entities(question, (), None, load_manifest()).unknown_names == (), question


def test_a_title_case_question_flags_nothing_but_grades_do_not_make_title_case() -> None:
    manifest = load_manifest()
    assert resolve_entities("Tell Me About Corwin Ashby", (), None, manifest).unknown_names == ()
    # "A" is a grade in ordinary prose, not a Title Case signal
    assert resolve_entities("Is Corwin Ashby an A defender?", (), None, manifest).unknown_names == ("Corwin Ashby",)


# --------------------------------------------- carry-forward beside a newly named player

REED_HISTORY = (
    user("How good is Niko Reed's point-of-attack defense?"),
    assistant("Niko Reed grades A at the point of attack [S03]."),
)


@pytest.mark.parametrize(
    ("question", "named"),
    [
        ("Is he a better shooter than Theo Vance?", "THEO_VANCE"),
        ("Compare him with Holloway.", "BASTIAN_HOLLOWAY"),
        ("Is Theo Vance better than him?", "THEO_VANCE"),
        ("Is his three-point shooting better than Theo Vance's?", "THEO_VANCE"),
    ],
)
def test_a_pronoun_beside_a_new_name_carries_the_earlier_player_too(manifest: Manifest, question: str, named: str) -> None:
    result = resolve_entities(question, REED_HISTORY, None, manifest)
    assert ids(result) == [(named, "named"), ("NIKO_REED", "carried_from_history")]


def test_a_pronoun_after_the_name_means_that_player(manifest: Manifest) -> None:
    result = resolve_entities("Does Theo Vance fit, and can he guard wings?", REED_HISTORY, None, manifest)
    assert ids(result) == [("THEO_VANCE", "named")]


def test_carried_beside_a_named_player_is_capped_and_excludes_the_named(manifest: Manifest) -> None:
    history = (
        user("Compare Niko Reed, Bastian Holloway and Ilan Marchetti."),
        assistant("Niko Reed, then Bastian Holloway, then Ilan Marchetti."),
    )
    result = resolve_entities("Is he better than Bastian Holloway?", history, None, manifest)
    assert ids(result) == [
        ("BASTIAN_HOLLOWAY", "named"),
        ("NIKO_REED", "carried_from_history"),
        ("ILAN_MARCHETTI", "carried_from_history"),
    ]


# ------------------------------------------- deterministic replies are not discussion


def _refusal_and_abstention(manifest: Manifest) -> tuple[str, str]:
    from app.assistant_rag.policy import abstention_answer, decide, deterministic_answer
    from app.assistant_rag.types import QueryContext

    def ctx(question: str) -> QueryContext:
        return QueryContext(
            question=question, mode="research", team_id=None, priorities={}, previous_priorities=None,
            constraints=(), history=(), history_received=0, history_truncated=False,
            prompt_variant="grounded", retrieval_variant="hybrid", k=6,
        )

    question = "What's Corwin Ashby's three-point percentage this season?"
    entities = resolve_entities(question, (), None, manifest)
    refusal = deterministic_answer(decide(ctx(question), entities, manifest), ctx(question), entities, manifest)
    pizza = "What's the best pizza place downtown?"
    abstention = abstention_answer(ctx(pizza), resolve_entities(pizza, (), None, manifest), manifest)
    return refusal, abstention


def test_players_listed_by_a_refusal_or_abstention_are_not_carried(manifest: Manifest) -> None:
    refusal, abstention = _refusal_and_abstention(manifest)
    assert "Niko Reed" in refusal and "Theo Vance" in abstention  # roster / example questions
    for reply in (refusal, abstention):
        history = (user("Some question."), assistant(reply))
        assert resolve_entities("Which of them has the best defense?", history, None, manifest).resolved == ()


def test_a_refusal_does_not_hide_the_answer_before_it(manifest: Manifest) -> None:
    refusal, _ = _refusal_and_abstention(manifest)
    history = (*REED_HISTORY, user("Is it legal to trade for him?"), assistant(refusal))
    result = resolve_entities("What about his shooting?", history, None, manifest)
    assert ids(result) == [("NIKO_REED", "carried_from_history")]


def test_a_refusal_listing_both_cruzes_does_not_undo_a_resolved_cruz(manifest: Manifest) -> None:
    refusal, _ = _refusal_and_abstention(manifest)  # lists the whole roster, both Cruzes included
    history = (user("Tell me about Mateo Cruz."), assistant("Mateo Cruz creates [S01]."), user("x"), assistant(refusal))
    assert ids(resolve_entities("Is Cruz a good passer?", history, None, manifest)) == [("MATEO_CRUZ", "carried_from_history")]


# ------------------------------------------------------------- lowercase chat surnames


@pytest.mark.parametrize(
    ("question", "players"),
    [
        ("compare reed and vance", ("NIKO_REED", "THEO_VANCE")),
        ("how is reed's defense?", ("NIKO_REED",)),
        ("is pike a good shooter?", ("DORIAN_PIKE",)),
    ],
)
def test_an_all_lowercase_message_resolves_case_sensitive_surnames(manifest: Manifest, question: str, players: tuple[str, ...]) -> None:
    result = resolve_entities(question, (), None, manifest)
    assert result.player_ids == players
    assert [m.text for m in player_mentions(question, manifest) if m.kind == "player"][0].islower()  # GM's own spelling


@pytest.mark.parametrize("text", ["The reed bent in the wind.", "the tides turned", "How do the tides look?"])
def test_capitalised_text_and_lowercase_team_words_stay_case_sensitive(manifest: Manifest, text: str) -> None:
    assert resolve_entities(text, (), None, manifest).resolved == ()


def test_a_lowercase_history_turn_is_carried(manifest: Manifest) -> None:
    history = (user("how is reed's defense?"), assistant("Strong at the point of attack [S03]."))
    assert resolve_entities("And his shooting?", history, None, manifest).player_ids == ("NIKO_REED",)
