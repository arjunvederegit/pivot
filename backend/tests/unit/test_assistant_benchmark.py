"""The version-controlled benchmark: it loads, it is internally consistent, it agrees with
the manifest, and a broken edit fails loudly. Pure Python."""

from __future__ import annotations

import copy
import json
import re

import pytest

from app.assistant_rag import claims
from app.assistant_rag.evaluation.dataset import (
    ABSTAIN_TOPICS,
    BENCHMARK_PATH,
    CATEGORIES,
    SEVERITIES,
    BenchmarkError,
    content_hash,
    latest_assessment,
    load_benchmark,
    parse_benchmark,
)
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.models import AskRequest

MANIFEST = load_manifest()
BENCHMARK = load_benchmark()


def _raw() -> dict:
    with BENCHMARK_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _case(raw: dict, case_id: str) -> dict:
    return next(c for c in raw["cases"] if c["id"] == case_id)


# ------------------------------------------------------------------------- structure


def test_benchmark_loads_with_34_to_40_cases():
    assert BENCHMARK.id == "ask-pivot-v2"
    assert 34 <= len(BENCHMARK.cases) <= 40
    assert BENCHMARK.corpus_version == MANIFEST.corpus_version


def test_case_ids_are_unique_and_well_formed():
    ids = [c.id for c in BENCHMARK.cases]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9_]+", i) for i in ids)


def test_every_category_is_represented():
    assert set(BENCHMARK.categories) == set(CATEGORIES)
    assert all(c.severity in SEVERITIES for c in BENCHMARK.cases)
    assert {c.severity for c in BENCHMARK.cases} == set(SEVERITIES)


def test_required_scenarios_are_present():
    required = {
        "fact_vance_three_point", "fact_mateo_cruz_assists", "fact_holloway_shooting", "fact_dante_cruz_interior",
        "fact_pike_volume", "abstain_mateo_cruz_ft", "abstain_dante_cruz_three", "abstain_vance_playoff_shooting",
        "abstain_calder_medical", "ambiguity_cruz_defense", "ambiguity_niko_fit", "ambiguity_cruz_resolved_by_history",
        "polarity_reed_defense", "polarity_holloway_spacing", "polarity_achterberg_switching", "conflict_vance_poa",
        "conflict_calder_wing", "conflict_comets_diagnosis", "teamfit_comets_perimeter", "teamfit_sundogs_offense",
        "teamfit_tides_size", "teamfit_riverhawks_creator", "revision_comets_initial", "revision_comets_revised",
        "counter_holloway_regret", "legality_pure_holloway", "legality_mixed_vance_salaries", "fitscore_reed_vance",
        "injection_pike_reader_submissions", "injection_pike_summary", "selective_pike_agent_claim",
        "real_nba_comparison", "out_of_scope_pizza", "false_premise_reed_45", "method_poa_vs_team_defense",
        "continuity_comets_guards", "compare_reed_vance_closing",
    }
    assert required <= {c.id for c in BENCHMARK.cases}


def test_every_case_is_a_valid_ask_request():
    for case in BENCHMARK.cases:
        request = AskRequest(**case.request_payload())
        assert request.question == case.question
        assert request.team_id == case.team_id


# ------------------------------------------------------------- agreement with manifest


def test_expectations_reference_real_manifest_entities_sources_and_conflicts():
    players = {p.id for p in MANIFEST.players}
    teams = {t.id for t in MANIFEST.teams}
    conflicts = {c.id for c in MANIFEST.conflicts}
    for case in BENCHMARK.cases:
        e = case.expect
        assert case.team_id is None or case.team_id in teams
        if e.retrieval:
            for sid in list(e.retrieval.required_sources) + [s for g in e.retrieval.any_of for s in g]:
                assert sid in MANIFEST.source_ids, (case.id, sid)
        for fact in e.facts:
            assert MANIFEST.fact(fact.entity_id, fact.stat) is not None, (case.id, fact)
        for item in e.must_abstain:
            if item.is_stat:
                assert item.entity_id and item.stat
                assert MANIFEST.fact(item.entity_id, item.stat) is None
                assert MANIFEST.unavailable_fact(item.entity_id, item.stat) is not None
            else:
                assert item.topic in ABSTAIN_TOPICS
        for pol in e.polarity:
            latest = latest_assessment(MANIFEST, pol.entity_id, pol.dimension)
            assert latest is not None and latest.polarity == pol.expected, (case.id, pol)
        assert e.conflict_id is None or e.conflict_id in conflicts
        assert set(e.must_mention_entities) <= players | teams
        assert set(e.must_not_recommend) <= players
        assert set(e.clarification_candidates) <= players
        for ref in e.continuity_any:
            if re.fullmatch(r"[A-Z_]+", ref):
                assert ref in players | teams


def test_must_not_recommend_lists_are_the_selected_teams_own_players():
    for case in BENCHMARK.cases:
        for pid in case.expect.must_not_recommend:
            player = MANIFEST.player(pid)
            assert player is not None
            team = case.team_id or "COMETS"  # the continuity case's team comes from history
            assert player.current_club_id == team, (case.id, pid)


def test_benchmark_holds_no_gold_values():
    """Gold numbers come from the manifest at scoring time; the benchmark must not copy them."""

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key != "value", path
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif isinstance(node, int | float) and not isinstance(node, bool):
            assert path.endswith("/k"), f"numeric literal at {path}"

    for case in _raw()["cases"]:
        walk(case["expect"], "/expect")


def test_polarity_gold_follows_the_latest_assessment():
    raw = _raw()
    flipped = copy.deepcopy(raw)
    calder = _case(flipped, "conflict_calder_wing")
    calder["expect"]["polarity"][0]["expected"] = "positive"  # true of the 2023 report only
    with pytest.raises(BenchmarkError, match="latest assessment"):
        parse_benchmark(flipped, MANIFEST)


# ------------------------------------------------------------------- pairs, continuity


def test_pairs_are_symmetric_and_ordered_initial_then_revised():
    pairs = BENCHMARK.pairs()
    assert len(pairs) == 1
    initial, revised = pairs[0]
    assert (initial.id, revised.id) == ("revision_comets_initial", "revision_comets_revised")
    assert initial.pair_with == revised.id and revised.pair_with == initial.id
    for case in BENCHMARK.cases:
        if case.pair_with:
            assert BENCHMARK.case(case.pair_with).pair_with == case.id


def test_revision_pair_has_the_specified_shape():
    initial, revised = BENCHMARK.pairs()[0]
    comets = MANIFEST.team("COMETS")
    assert comets is not None
    assert dict(initial.priorities) == comets.default_priorities
    assert dict(revised.previous_priorities or {}) == comets.default_priorities
    assert dict(revised.priorities) == {"point_of_attack_defense": "high", "secondary_creation": "low", "spacing": "high"}
    assert revised.history[0].role == "user" and revised.history[0].content == initial.question
    assert revised.history[1].role == "assistant"
    assert revised.question.startswith("Actually, point-of-attack defense matters more")
    ps = revised.expect.priority_sensitivity
    assert ps is not None and ps.revision_acknowledgement and ps.deprioritized == ("secondary_creation",)


def test_continuity_case_gets_its_team_only_from_history():
    case = BENCHMARK.case("continuity_comets_guards")
    assert case.team_id is None
    mentioned = {m.entity_ids for m in claims.find_mentions(case.question, MANIFEST)}
    assert mentioned == set()  # the question names no team and no player
    assert any("Comets" in h.content for h in case.history if h.role == "user")


def test_v1_regression_cases_exist_with_strict_expectations():
    ft = BENCHMARK.case("abstain_mateo_cruz_ft")
    assert ft.severity == "blocking"
    assert ft.expect.must_abstain[0].entity_id == "MATEO_CRUZ" and ft.expect.must_abstain[0].stat == "ft_pct"
    reed = BENCHMARK.case("polarity_reed_defense")
    assert reed.severity == "blocking"
    assert {(p.entity_id, p.expected) for p in reed.expect.polarity} == {("NIKO_REED", "positive")}


def test_real_nba_name_appears_only_in_the_real_nba_case():
    """Everything is fictional except the one question that tests refusing a real player."""
    terms = BENCHMARK.case("real_nba_comparison").expect.must_abstain[0].terms
    assert terms
    for case in BENCHMARK.cases:
        if case.id != "real_nba_comparison":
            text = json.dumps(case.raw).lower()
            assert not any(t.lower() in text for t in terms), case.id


# -------------------------------------------------------------------------- hashing


def test_content_hash_is_stable_and_sensitive():
    again = load_benchmark()
    raw = _raw()
    assert again.content_hash == BENCHMARK.content_hash == content_hash(raw)
    assert re.fullmatch(r"[0-9a-f]{64}", BENCHMARK.content_hash)
    reordered = {key: raw[key] for key in reversed(list(raw))}
    assert content_hash(reordered) == BENCHMARK.content_hash  # canonical: key order is irrelevant
    edited = copy.deepcopy(raw)
    edited["cases"][0]["rubric"] += " (edited)"
    assert parse_benchmark(edited, MANIFEST).content_hash != BENCHMARK.content_hash


# ----------------------------------------------------------------------- validation


def _mutations():
    def top_unknown(raw):
        raw["extra"] = 1

    def case_unknown(raw):
        raw["cases"][0]["prioritise"] = {}

    def expect_unknown(raw):
        raw["cases"][0]["expect"]["facs"] = []

    def unknown_player(raw):
        raw["cases"][0]["expect"]["facts"][0]["entity_id"] = "NOBODY"

    def unknown_stat(raw):
        raw["cases"][0]["expect"]["facts"][0]["stat"] = "per"

    def fact_without_gold(raw):
        raw["cases"][0]["expect"]["facts"][0] = {"entity_id": "MATEO_CRUZ", "stat": "ft_pct"}

    def gold_value_copied(raw):
        raw["cases"][0]["expect"]["facts"][0]["value"] = 40.4

    def abstain_on_known_fact(raw):
        _case(raw, "abstain_mateo_cruz_ft")["expect"]["must_abstain"][0]["stat"] = "assists_per_game"

    def unknown_topic(raw):
        _case(raw, "abstain_calder_medical")["expect"]["must_abstain"][0]["topic"] = "astrology"

    def real_nba_without_terms(raw):
        del _case(raw, "real_nba_comparison")["expect"]["must_abstain"][0]["terms"]

    def wrong_polarity(raw):
        _case(raw, "polarity_reed_defense")["expect"]["polarity"][0]["expected"] = "negative"

    def unknown_conflict(raw):
        _case(raw, "conflict_vance_poa")["expect"]["conflict"]["conflict_id"] = "C9"

    def unknown_source(raw):
        _case(raw, "polarity_reed_defense")["expect"]["retrieval"]["required_sources"] = ["S99"]

    def k_out_of_range(raw):
        _case(raw, "polarity_reed_defense")["expect"]["retrieval"]["k"] = 50

    def asymmetric_pair(raw):
        _case(raw, "revision_comets_initial")["pair_with"] = "fact_vance_three_point"

    def dangling_pair(raw):
        _case(raw, "revision_comets_initial")["pair_with"] = "missing_case"

    def duplicate_id(raw):
        raw["cases"][1]["id"] = raw["cases"][0]["id"]

    def bad_severity(raw):
        raw["cases"][0]["severity"] = "critical"

    def bad_category(raw):
        raw["cases"][0]["category"] = "vibes"

    def unknown_team(raw):
        raw["cases"][0]["team_id"] = "ATLANTIS_WAVES"

    def bad_priority_level(raw):
        _case(raw, "teamfit_comets_perimeter")["priorities"]["spacing"] = "critical"

    def bad_history_role(raw):
        _case(raw, "continuity_comets_guards")["history"][0]["role"] = "system"

    def single_candidate(raw):
        _case(raw, "ambiguity_cruz_defense")["expect"]["clarification"]["candidates"] = ["MATEO_CRUZ"]

    def empty_expect(raw):
        _case(raw, "method_poa_vs_team_defense")["expect"] = {}

    def false_flag(raw):
        _case(raw, "legality_pure_holloway")["expect"]["no_legality_verdict"] = False

    def unknown_continuity_entity(raw):
        _case(raw, "continuity_comets_guards")["expect"]["continuity"]["must_reference_any"] = ["GHOST_CLUB"]

    def revised_without_previous(raw):
        del _case(raw, "revision_comets_revised")["previous_priorities"]

    def sensitivity_without_pair(raw):
        case = _case(raw, "teamfit_comets_perimeter")
        case["expect"]["priority_sensitivity"] = {"emphasis_keywords": ["on-ball"]}

    return [
        top_unknown, case_unknown, expect_unknown, unknown_player, unknown_stat, fact_without_gold,
        gold_value_copied, abstain_on_known_fact, unknown_topic, real_nba_without_terms, wrong_polarity,
        unknown_conflict, unknown_source, k_out_of_range, asymmetric_pair, dangling_pair, duplicate_id,
        bad_severity, bad_category, unknown_team, bad_priority_level, bad_history_role, single_candidate,
        empty_expect, false_flag, unknown_continuity_entity, revised_without_previous, sensitivity_without_pair,
    ]


@pytest.mark.parametrize("mutate", _mutations(), ids=lambda f: f.__name__)
def test_invalid_benchmark_edits_raise(mutate):
    raw = _raw()
    mutate(raw)
    with pytest.raises(BenchmarkError):
        parse_benchmark(raw, MANIFEST)


def test_load_benchmark_accepts_an_explicit_path(tmp_path):
    target = tmp_path / "bench.json"
    target.write_text(json.dumps(_raw()), encoding="utf-8")
    loaded = load_benchmark(target, MANIFEST)
    assert loaded.content_hash == BENCHMARK.content_hash
    with pytest.raises(KeyError):
        loaded.case("does_not_exist")
