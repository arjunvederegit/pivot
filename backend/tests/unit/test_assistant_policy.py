"""Ask Pivot's deterministic routing — what is answered, clarified or declined before any
retrieval or model call — and the follow-up prompts built from the same context.

No LangChain and no model: the policy is plain code precisely so these rules can be pinned.
Every name here is fictional (the sandbox manifest); "unrecognized" names are invented too.
"""

from __future__ import annotations

import pytest

from app.assistant_rag import claims
from app.assistant_rag.corpus import Chunk
from app.assistant_rag.followups import MAX_FOLLOW_UPS, MIN_FOLLOW_UPS, suggest_follow_ups
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.policy import (
    SANDBOX_LIMITS_SOURCE_ID,
    abstention_answer,
    decide,
    deterministic_answer,
    deterministic_citations,
    in_scope_alternatives,
    relevant_unavailable_facts,
    top_priority_keys,
)
from app.assistant_rag.types import (
    Ambiguity,
    EntityRef,
    EntityResolution,
    PolicyDecision,
    QueryContext,
    RetrievalResult,
    RetrievedChunk,
)

M = load_manifest()
CRUZ = Ambiguity("Cruz", ("MATEO_CRUZ", "DANTE_CRUZ"))


def ctx(question, *, mode="research", team_id=None, priorities=None, previous=None, history=()):
    return QueryContext(
        question=question,
        mode=mode,
        team_id=team_id,
        priorities=dict(priorities or {}),
        previous_priorities=previous,
        constraints=(),
        history=tuple(history),
        history_received=len(history),
        history_truncated=False,
        prompt_variant="grounded",
        retrieval_variant="hybrid",
        k=6,
    )


def resolve(*player_ids, team_id=None, ambiguous=(), unrecognized=(), how="named"):
    refs = [EntityRef(pid, M.player(pid).name, "player", how) for pid in player_ids]
    if team_id:
        refs.append(EntityRef(team_id, M.team(team_id).name, "team", "selected_team"))
    return EntityResolution(tuple(refs), tuple(ambiguous), tuple(unrecognized))


def from_text(question, team_id=None):
    """A stand-in for entities.py: exact manifest aliases via claims.find_mentions."""
    refs, ambiguous = [], []
    for mention in claims.find_mentions(question, M):
        if mention.ambiguous:
            ambiguous.append(Ambiguity(mention.text, mention.entity_ids))
        elif mention.kind == "player":
            refs.append(EntityRef(mention.entity_ids[0], M.player(mention.entity_ids[0]).name, "player", "named"))
        elif mention.kind == "team":
            refs.append(EntityRef(mention.entity_ids[0], M.team(mention.entity_ids[0]).name, "team", "named"))
    if team_id:
        refs.append(EntityRef(team_id, M.team(team_id).name, "team", "selected_team"))
    return EntityResolution(tuple(refs), tuple(ambiguous), ())


def chunk(sid, *, rank=1):
    meta = M.document(sid)
    c = Chunk(f"{sid}@{meta.version}#1", sid, meta.version, 1, "Notes", "text", "0" * 64, meta)
    return RetrievedChunk(c, rank, 1 / (60 + rank), 0.6, rank, 1.0, rank, meta.entity_ids, False, ("test",))


def retrieval(*items, low=False):
    return RetrievalResult("q", "hybrid", 6, tuple(items), tuple(items), 0.7, 0.45, low)


def kinds(decision):
    return [u.kind for u in decision.unsupported]


def route_of(question, *, team_id="COMETS", mode="roster_fit"):
    return decide(ctx(question, mode=mode, team_id=team_id), from_text(question, team_id), M)


# ------------------------------------------------------------------------------ clarify


class TestAmbiguityIsClarifiedNotGuessed:
    def test_bare_surname_routes_to_clarify_naming_every_candidate(self):
        decision = decide(ctx("How does Cruz defend?"), resolve(ambiguous=(CRUZ,)), M)
        assert decision.route == "clarify"
        assert decision.clarification == (
            "Which Cruz do you mean — Mateo Cruz (PG, Mesa Foxes) or Dante Cruz (C, Albuquerque Sundogs)?"
        )

    def test_possessive_mention_is_normalised_in_the_question(self):
        decision = decide(ctx("Cruz's defense?"), resolve(ambiguous=(Ambiguity("Cruz's", CRUZ.candidate_ids),)), M)
        assert decision.clarification.startswith("Which Cruz do you mean")

    def test_clarify_answer_lists_candidates_and_asks_for_full_name(self):
        c, e = ctx("How does Cruz defend?"), resolve(ambiguous=(CRUZ,))
        decision = decide(c, e, M)
        text = deterministic_answer(decision, c, e, M)
        assert "Mateo Cruz" in text and "Dante Cruz" in text
        assert "Mesa Foxes" in text and "Albuquerque Sundogs" in text
        assert "full name" in text
        assert claims.extract_citations(text) == list(deterministic_citations(decision)) == []

    def test_clarify_keeps_unsupported_caveats_for_mixed_question(self):
        c, e = ctx("How does Cruz defend, and what is his injury history?"), resolve(ambiguous=(CRUZ,))
        decision = decide(c, e, M)
        assert decision.route == "clarify"
        assert "medical" in kinds(decision)
        assert "medical" in deterministic_answer(decision, c, e, M).lower()

    def test_clarify_follow_ups_are_the_disambiguated_questions(self):
        c, e = ctx("How does Cruz defend?"), resolve(ambiguous=(CRUZ,))
        follow = suggest_follow_ups(c, e, decide(c, e, M), retrieval(), M)
        assert "How does Mateo Cruz defend?" in follow
        assert "How does Dante Cruz defend?" in follow
        assert MIN_FOLLOW_UPS <= len(follow) <= MAX_FOLLOW_UPS


# ----------------------------------------------------------------------------- legality


class TestLegalityAndMoney:
    @pytest.mark.parametrize(
        "question",
        [
            "Is a Theo Vance trade legal under the CBA, and do the salaries match?",
            "Can we afford Bastian Holloway?",
            "Can we trade for Theo Vance?",
            "How much cap space do we have?",
            "Can we fit Vance under the cap?",
            "Would this deal put us over the apron?",
        ],
    )
    def test_legality_or_money_only_is_refused(self, question):
        decision = route_of(question)
        assert decision.route == "refuse", (question, decision)
        assert set(kinds(decision)) & {"cba_legality", "contracts_and_cap"}

    def test_salary_matching_names_both_kinds(self):
        decision = route_of("Do the salaries match in a Vance trade? Is it legal?")
        assert kinds(decision)[:2] == ["cba_legality", "contracts_and_cap"]

    @pytest.mark.parametrize(
        "question",
        [
            "Does Theo Vance fit our closing lineup, and is the trade legal?",
            "Should we trade for Theo Vance?",
            "Who defends better, Reed or Vance, and can we afford either?",
            "Compare Holloway and Reed on defense; what are their contracts?",
        ],
    )
    def test_mixed_question_is_answered_with_a_caveat(self, question):
        decision = route_of(question)
        assert decision.route == "answer", (question, decision)
        assert set(kinds(decision)) & {"cba_legality", "contracts_and_cap"}

    def test_making_salaries_work_is_a_trade_rule_question(self):
        decision = route_of("Would Theo Vance fit the Comets, and can we make the salaries work?")
        assert decision.route == "answer"
        assert kinds(decision) == ["cba_legality", "contracts_and_cap"]

    def test_afford_to_is_an_idiom_not_a_money_question(self):
        decision = route_of("We want size, but we can't afford to slow our pace down. Who fits?", team_id="TIDES")
        assert decision.route == "answer"
        assert decision.unsupported == ()

    def test_ambiguous_legality_only_question_is_refused_not_clarified(self):
        decision = decide(ctx("Is a Cruz trade legal?"), resolve(ambiguous=(CRUZ,)), M)
        assert decision.route == "refuse"

    def test_messages_come_from_the_manifest(self):
        decision = route_of("Is a Theo Vance trade legal under the CBA?")
        reasons = {t.key: t.reason for t in M.unavailable_topics}
        assert decision.unsupported[0].message == reasons["cba_legality"]

    def test_refusal_explains_why_and_what_would_be_needed(self):
        c = ctx("Is a Theo Vance trade legal under the CBA?", mode="roster_fit", team_id="COMETS")
        e = from_text(c.question, "COMETS")
        decision = decide(c, e, M)
        text = deterministic_answer(decision, c, e, M)
        lowered = text.lower()
        assert "cannot tell you whether this trade is legal" in lowered
        for needed in ("contract", "cap sheet", "cba engine", "read-only tools registry", "neither is connected"):
            assert needed in lowered, needed
        assert "What you can research here instead" in text
        assert f"[{SANDBOX_LIMITS_SOURCE_ID}]" in text
        assert "Theo Vance" in text  # the alternatives are tailored to what was asked

    def test_legality_refusal_never_states_a_verdict(self):
        c = ctx("Is a Theo Vance trade legal under the CBA?", team_id="COMETS")
        e = from_text(c.question, "COMETS")
        text = deterministic_answer(decide(c, e, M), c, e, M)
        assert claims.legality_verdicts(text) == []
        assert claims.fit_scores(text) == []

    def test_in_scope_alternatives_do_not_trip_the_refusal_again(self):
        c = ctx("Is a Theo Vance trade legal?", mode="roster_fit", team_id="COMETS")
        e = from_text(c.question, "COMETS")
        for alternative in in_scope_alternatives(c, e, M, limit=5):
            follow = decide(ctx(alternative, mode="roster_fit", team_id="COMETS"), from_text(alternative, "COMETS"), M)
            assert follow.route == "answer", alternative
            assert not set(kinds(follow)) & {"cba_legality", "contracts_and_cap", "fit_score"}, alternative

    def test_refusal_follow_ups_are_in_scope(self):
        c = ctx("Can we afford Bastian Holloway?", mode="roster_fit", team_id="COMETS")
        e = from_text(c.question, "COMETS")
        decision = decide(c, e, M)
        follow = suggest_follow_ups(c, e, decision, retrieval(), M)
        assert MIN_FOLLOW_UPS <= len(follow) <= MAX_FOLLOW_UPS
        assert any("Bastian Holloway" in f for f in follow)
        assert all(not claims.LEGALITY_TOPIC.search(f) for f in follow)


# ---------------------------------------------------------------------------- fit scores


class TestFitScores:
    @pytest.mark.parametrize(
        "question",
        [
            "Give me a fit score for Theo Vance.",
            "Rate Reed and Vance out of 10 for our closing lineup.",
            "Score each of Holloway, Reed and Vance.",
            "What's Niko Reed's impact score?",
        ],
    )
    def test_score_request_about_named_players_is_answered_qualitatively(self, question):
        decision = route_of(question)
        assert decision.route == "answer"
        assert "fit_score" in kinds(decision)

    def test_bare_score_request_with_nothing_to_research_is_refused(self):
        c = ctx("Rate them out of 10.")
        decision = decide(c, resolve(), M)
        assert decision.route == "refuse"
        assert kinds(decision) == ["fit_score"]
        text = deterministic_answer(decision, c, resolve(), M)
        assert "numeric fit or impact scores" in text
        assert claims.extract_citations(text) == list(deterministic_citations(decision)) == ["S31"]

    def test_carried_players_make_a_score_request_answerable(self):
        decision = decide(ctx("Rate them out of 10."), resolve("NIKO_REED", "THEO_VANCE", how="carried_from_history"), M)
        assert decision.route == "answer"
        assert "fit_score" in kinds(decision)

    def test_the_word_fit_inside_fit_score_is_not_a_research_cue(self):
        assert decide(ctx("fit score please"), resolve(), M).route == "refuse"


# ------------------------------------------------------------------- medical and playoffs


class TestMedicalAndPlayoffs:
    def test_medical_is_a_caveat_not_a_refusal(self):
        decision = route_of("What is Niko Reed's injury history?", mode="research", team_id=None)
        assert decision.route == "answer"
        assert kinds(decision) == ["medical"]

    @pytest.mark.parametrize(
        "question",
        [
            "What was Niko Reed's playoff 3P%?",
            "What are Theo Vance's stats in the playoffs?",
            "How did Theo Vance shoot in the playoffs?",
        ],
    )
    def test_playoff_statistics_are_a_caveat(self, question):
        decision = route_of(question, mode="research", team_id=None)
        assert decision.route == "answer"
        assert "playoff_statistics" in kinds(decision)

    def test_qualitative_playoff_question_is_unrestricted(self):
        decision = route_of("What does the playoff film say about Niko Reed?", mode="research", team_id=None)
        assert decision.route == "answer"
        assert kinds(decision) == []


# ------------------------------------------------------------------------------ real NBA


class TestNamesOutsideTheLeague:
    def test_only_outside_names_is_refused(self):
        c = ctx("How good is Zephyr Quillfeather?")
        e = resolve(unrecognized=("Zephyr Quillfeather",))
        decision = decide(c, e, M)
        assert decision.route == "refuse"
        assert kinds(decision) == ["real_nba_entity"]
        text = deterministic_answer(decision, c, e, M)
        assert "Zephyr Quillfeather" in text and "not part of the fictional" in text
        assert deterministic_citations(decision) == ()
        assert claims.extract_citations(text) == []

    def test_outside_name_with_a_fictional_player_is_answered_with_caveat(self):
        c = ctx("Compare Zephyr Quillfeather with Theo Vance.")
        e = resolve("THEO_VANCE", unrecognized=("Zephyr Quillfeather",))
        decision = decide(c, e, M)
        assert decision.route == "answer"
        assert kinds(decision) == ["real_nba_entity"]

    def test_selected_team_alone_does_not_count_as_naming_a_fictional_entity(self):
        e = resolve(team_id="COMETS", unrecognized=("Zephyr Quillfeather",))
        assert decide(ctx("Should we get Zephyr Quillfeather?", team_id="COMETS"), e, M).route == "refuse"

    def test_unknown_team_id_is_a_caveat(self):
        decision = decide(ctx("Who defends best?", team_id="NOPE"), resolve(), M)
        assert decision.route == "answer"
        assert kinds(decision) == ["unknown_team"]


# ------------------------------------------------------------------------- plain answers


class TestPlainResearchIsAnswered:
    @pytest.mark.parametrize(
        "question",
        [
            "Compare Niko Reed and Theo Vance for our closing lineup.",
            "How should I read the conflicting reports on Theo Vance's point-of-attack defense?",
            "What can this research sandbox not calculate, and why?",
            "Who rebounds best?",
        ],
    )
    def test_answer_without_caveats(self, question):
        decision = route_of(question)
        assert decision.route == "answer"
        assert decision.unsupported == ()

    def test_deterministic_answer_refuses_an_answer_route(self):
        with pytest.raises(ValueError):
            deterministic_answer(PolicyDecision(route="answer"), ctx("x"), resolve(), M)

    def test_deterministic_citations_for_an_answer_route_are_empty(self):
        decision = route_of("Should we trade for Theo Vance?")
        assert decision.route == "answer"
        assert deterministic_citations(decision) == ()


class TestDeterministicCitationsMatchTheText:
    @pytest.mark.parametrize(
        ("question", "entities"),
        [
            ("Is a Theo Vance trade legal under the CBA?", resolve("THEO_VANCE")),
            ("Can we afford him?", resolve()),
            ("Rate them out of 10.", resolve()),
            ("How good is Zephyr Quill?", resolve(unrecognized=("Zephyr Quill",))),
            ("Is a Zephyr Quill trade legal?", resolve(unrecognized=("Zephyr Quill",))),
            ("How does Cruz defend?", resolve(ambiguous=(CRUZ,))),
            ("How does Cruz defend, and is he legal to trade for?", resolve(ambiguous=(CRUZ,))),
        ],
    )
    def test_citations_in_the_text_are_exactly_the_declared_ones(self, question, entities):
        c = ctx(question, team_id="COMETS", mode="roster_fit")
        decision = decide(c, entities, M)
        assert decision.route in ("clarify", "refuse"), decision
        text = deterministic_answer(decision, c, entities, M)
        assert tuple(claims.extract_citations(text)) == deterministic_citations(decision)
        assert all(sid in M.source_ids for sid in deterministic_citations(decision))

    def test_the_limits_document_is_the_one_the_manifest_names(self):
        for topic in M.unavailable_topics:
            assert SANDBOX_LIMITS_SOURCE_ID in topic.source_ids
        assert M.document(SANDBOX_LIMITS_SOURCE_ID).title == "What this research sandbox cannot calculate"


# ---------------------------------------------------------------------------- abstention


class TestAbstention:
    def test_abstention_says_nothing_relevant_and_does_not_guess(self):
        c = ctx("What is Theo Vance's favourite food?")
        text = abstention_answer(c, resolve("THEO_VANCE"), M)
        assert "Nothing in the fictional scouting corpus" in text
        assert "will not guess" in text
        assert "Theo Vance" in text
        assert "Questions it can answer" in text
        assert claims.extract_citations(text) == []

    def test_abstention_without_entities_offers_generic_in_scope_questions(self):
        text = abstention_answer(ctx("What's the weather?"), resolve(), M)
        assert text.count('- "') >= 2

    def test_abstention_with_a_team_offers_team_questions(self):
        text = abstention_answer(ctx("Best pizza near the arena?", team_id="TIDES"), resolve(team_id="TIDES"), M)
        assert "Savannah Tides' needs on rim protection and rebounding" in text


# ------------------------------------------------------------------------------- helpers


class TestHelpers:
    def test_relevant_unavailable_facts_unconditional_and_by_text(self):
        assert [g.stat for g in relevant_unavailable_facts(M, ["MATEO_CRUZ"])] == ["ft_pct"]
        assert relevant_unavailable_facts(M, ["MATEO_CRUZ"], "How does he defend?") == []
        assert [g.stat for g in relevant_unavailable_facts(M, ["MATEO_CRUZ"], "Is he a good shooter?")] == ["ft_pct"]
        assert [g.stat for g in relevant_unavailable_facts(M, ["DANTE_CRUZ"], "can he space the floor")] == ["three_pt_pct"]

    def test_top_priorities_use_levels_then_manifest_order_and_skip_low(self):
        c = ctx("x", team_id="COMETS")
        # Comets defaults: spacing must_preserve, POA high, wing high, ... rebounding low.
        assert top_priority_keys(c, M, limit=3) == ["spacing", "point_of_attack_defense", "wing_defense"]
        assert top_priority_keys(ctx("x", priorities={"rebounding": "low"}), M) == []


# ---------------------------------------------------------------------------- follow-ups


class TestFollowUps:
    def test_answer_follow_ups_use_actual_priorities_and_players(self):
        c = ctx("Compare Reed and Vance.", mode="roster_fit", team_id="COMETS")
        e = from_text(c.question, "COMETS")
        r = retrieval(chunk("S03", rank=1), chunk("S05", rank=2), chunk("S06", rank=3), chunk("S16", rank=4))
        follow = suggest_follow_ups(c, e, decide(c, e, M), r, M)
        assert MIN_FOLLOW_UPS <= len(follow) <= MAX_FOLLOW_UPS
        assert follow[0] == "What evidence would make this recommendation wrong?"
        assert "What if point-of-attack defense mattered more than spacing?" in follow
        assert "Compare Niko Reed and Theo Vance on spacing." in follow
        # S05 + S06 put conflict C1 on the table, so uncertainty points at it.
        assert any("conflicting reports on Theo Vance's point-of-attack defense" in f for f in follow)
        # Sorensen (S16) is already on the Comets: never suggested as a target.
        assert not any("Sorensen" in f for f in follow)

    def test_research_follow_ups_without_priorities(self):
        c = ctx("Tell me about Dorian Pike.")
        e = from_text(c.question)
        follow = suggest_follow_ups(c, e, decide(c, e, M), retrieval(chunk("S13")), M)
        assert follow[0] == "What evidence would contradict this read?"
        assert "Which of these claims rests on a single report?" in follow
        assert any("Dorian Pike" in f for f in follow)
        assert MIN_FOLLOW_UPS <= len(follow) <= MAX_FOLLOW_UPS
        assert not any(f.startswith("What if") for f in follow)

    def test_follow_ups_are_unique(self):
        c = ctx("x", mode="roster_fit", team_id="TIDES")
        follow = suggest_follow_ups(c, resolve(team_id="TIDES"), PolicyDecision("answer"), retrieval(), M)
        assert len(follow) == len(set(follow))
        assert MIN_FOLLOW_UPS <= len(follow) <= MAX_FOLLOW_UPS


# ------------------------------------------------------------------- unknown names


def test_question_only_about_an_invented_name_is_refused_without_a_model() -> None:
    from app.assistant_rag.entities import resolve_entities
    from app.assistant_rag.manifest import load_manifest
    from app.assistant_rag.policy import decide, deterministic_answer

    manifest = load_manifest()
    question = "What's Corwin Ashby's three-point percentage this season?"
    ctx = _ctx_for_unknown(question)
    entities = resolve_entities(question, (), None, manifest)
    decision = decide(ctx, entities, manifest)
    assert decision.route == "refuse"
    assert [u.kind for u in decision.unsupported] == ["unknown_entity"]
    text = deterministic_answer(decision, ctx, entities, manifest)
    assert "Corwin Ashby" in text and "will not invent" in text
    assert "%" not in text  # no number of any kind is offered for the invented player


def test_an_invented_name_beside_a_real_fictional_player_is_answered_with_a_caveat() -> None:
    from app.assistant_rag.entities import resolve_entities
    from app.assistant_rag.manifest import load_manifest
    from app.assistant_rag.policy import decide

    manifest = load_manifest()
    question = "Is Corwin Ashby a better shooter than Theo Vance?"
    decision = decide(_ctx_for_unknown(question), resolve_entities(question, (), None, manifest), manifest)
    assert decision.route == "answer"
    assert "unknown_entity" in {u.kind for u in decision.unsupported}


def _ctx_for_unknown(question: str):
    from app.assistant_rag.types import QueryContext

    return QueryContext(
        question=question, mode="research", team_id=None, priorities={}, previous_priorities=None,
        constraints=(), history=(), history_received=0, history_truncated=False,
        prompt_variant="grounded", retrieval_variant="hybrid", k=6,
    )


# ------------------------------------------------- review fixes: cues and false refusals


def _real_route(question, *, mode="research", team_id=None, history=()):
    from app.assistant_rag.entities import resolve_entities

    c = ctx(question, mode=mode, team_id=team_id, history=history)
    return decide(c, resolve_entities(question, tuple(history), team_id, M), M)


@pytest.mark.parametrize(
    "question",
    [
        "Is a Holloway trade legal, and how is his defense?",
        "Is it legal to trade for Theo Vance and does his defense hold up?",
        "Is a Holloway trade legal, and is he good defensively?",
        "Is Holloway's contract a problem, and what about his defense?",
        "Can we afford Holloway, and how is his defense?",
        "Can we afford Holloway, and is he a good passer?",
    ],
)
def test_a_legality_question_with_a_defense_part_is_answered_with_the_caveat(question):
    # "defense"/"defensive" (US spelling) used to miss the research cue, so these were refused
    decision = route_of(question, team_id=None, mode="research")
    assert decision.route == "answer", (question, decision)
    assert set(kinds(decision)) & {"cba_legality", "contracts_and_cap"}


@pytest.mark.parametrize(
    "question",
    [
        "How much does Niko Reed's defense matter to us?",
        "How much would Theo Vance improve us?",
        "How much is Holloway going to play?",
        "How much do the Comets value Sorensen?",
        "How much does Vance shoot from three?",
    ],
)
def test_how_much_without_money_is_not_a_money_question(question):
    decision = route_of(question, team_id=None, mode="research")
    assert decision.route == "answer" and "contracts_and_cap" not in kinds(decision), (question, decision)


def test_how_much_with_money_still_is():
    assert kinds(route_of("How much would Theo Vance cost us?", team_id=None, mode="research")) == ["contracts_and_cap"]


@pytest.mark.parametrize("question", ["Trade for Holloway or Vance?", "We want to trade for a wing. Options?"])
def test_trade_for_alone_is_research_with_a_legality_caveat(question):
    decision = route_of(question, team_id=None, mode="research")
    assert decision.route == "answer", (question, decision)
    assert kinds(decision) == ["cba_legality"]


def test_research_and_domain_cues_agree_on_skill_words():
    """`policy._RESEARCH_CUES` and `retrieval._DOMAIN_CUE` drifted apart once (one matched
    "defend" but not "defense", the other the reverse); pin the shared skill vocabulary."""
    from app.assistant_rag import policy, retrieval

    words = [
        "defense", "defence", "defensive", "defensively", "defend", "defends", "defending",
        "defender", "defenders", "passer", "passing", "handle", "ball-handling", "scorer", "scoring",
        "shooting", "shooter", "rebounding", "spacing", "rim", "playmaking",
    ]  # fmt: skip
    for word in words:
        assert policy._RESEARCH_CUES.search(word), word
        assert retrieval._DOMAIN_CUE.search(word), word


@pytest.mark.parametrize(
    ("question", "mode", "team_id"),
    [
        ("Hey Pivot, who fits our spacing needs?", "research", None),
        ("What does Coach Grant want in the closing lineup?", "roster_fit", "COMETS"),  # Hollis Grant, S21
        ("Which wing is the Best Fit?", "research", None),
        ("Who is the best Rim Protector?", "research", None),
        ("Who is the best Pick And Roll defender?", "research", None),
        ("Summarize the Method Guide on spacing.", "research", None),
        ("Per Dana Whitfield, who is the best wing defender?", "research", None),
        ("Quick Question: who is the best shooter?", "research", None),
        ("Who Is The Best Defender?", "research", None),
        ("Which Players Fit Our Needs?", "roster_fit", "COMETS"),
    ],
)
def test_capitalised_phrases_that_are_not_names_are_not_refused(question, mode, team_id):
    decision = _real_route(question, mode=mode, team_id=team_id)
    assert decision.route == "answer", (question, decision)
    assert "unknown_entity" not in kinds(decision)


def test_an_invented_name_beside_a_carried_player_is_a_caveat_not_a_refusal():
    from app.assistant_rag.types import HistoryTurn

    history = (HistoryTurn("user", "Tell me about Theo Vance."), HistoryTurn("assistant", "Theo Vance shoots 40.4% [S05]."))
    decision = _real_route("Is he better than Corwin Ashby?", history=history)
    assert decision.route == "answer" and "unknown_entity" in kinds(decision)
    # a greeting is not a name at all
    assert _real_route("Thanks Pivot. And his defense?", history=history).unsupported == ()


def test_an_invented_name_in_roster_fit_is_a_caveat_but_alone_in_research_is_refused():
    question = "What's Corwin Ashby's three-point percentage this season?"
    assert _real_route(question).route == "refuse"
    assert _real_route(question, team_id="COMETS").route == "refuse"  # research: the team is UI context
    roster = _real_route(question, mode="roster_fit", team_id="COMETS")
    assert roster.route == "answer" and kinds(roster) == ["unknown_entity"]


def test_deterministic_replies_are_recognised_and_model_answers_are_not():
    from app.assistant_rag.policy import is_deterministic_reply

    refusal_ctx = ctx("Is a Theo Vance trade legal under the CBA?")
    refusal = deterministic_answer(decide(refusal_ctx, resolve("THEO_VANCE"), M), refusal_ctx, resolve("THEO_VANCE"), M)
    abstention = abstention_answer(ctx("Best pizza?"), resolve(), M)
    assert is_deterministic_reply(refusal) and is_deterministic_reply(abstention)
    assert not is_deterministic_reply("**Short answer:** Niko Reed fits [S03].\n\n**My read:** Reed first.")
    clarify_ctx = ctx("How does Cruz defend?")
    clarify = deterministic_answer(decide(clarify_ctx, resolve(ambiguous=(CRUZ,)), M), clarify_ctx, resolve(ambiguous=(CRUZ,)), M)
    assert not is_deterministic_reply(clarify)  # its candidates are what the GM picks from
