"""The deterministic evaluators, tested against answers whose verdict is already known.

The first group pins the V1 regression: the answer V1 scored 3/3 must now fail the checks
it should always have failed, while still passing citation validity (validity is not
correctness). `test_scorer_discrimination` runs every hand-written adversarial fixture;
`test_reference_answers_pass_every_applicable_scorer` is the positive control proving
every benchmark case is satisfiable. Pure Python: no LangChain, no Ollama.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from app.assistant_rag.evaluation import scorers as S
from app.assistant_rag.evaluation.dataset import load_benchmark
from app.assistant_rag.evaluation.fixtures_adversarial import (
    ADVERSARIAL_FIXTURES,
    PAIR_FIXTURES,
    REFERENCE_ANSWERS,
    V1_ANSWER,
    AdversarialFixture,
    PairFixture,
    make_output,
    reference_retrieved,
)
from app.assistant_rag.manifest import load_manifest

MANIFEST = load_manifest()
BENCHMARK = load_benchmark()
VERDICT = {"pass": True, "fail": False, "not_applicable": None}


def _by_key(results: list[S.ScoreResult]) -> dict[str, S.ScoreResult]:
    return {r.key: r for r in results}


def _score(case_id: str, answer: str, **kwargs) -> dict[str, S.ScoreResult]:
    output = make_output(answer, manifest=MANIFEST, **kwargs)
    return _by_key(S.score_case(BENCHMARK.case(case_id), output, MANIFEST))


# ------------------------------------------------------------------ V1 regression tests


def test_v1_regression_reed_bad_defender_is_marked_incorrect():
    results = _score("polarity_reed_defense", "Niko Reed is a bad defender [S03].")
    assert results["claim_polarity"].passed is False
    assert "gold positive" in results["claim_polarity"].detail
    # S03 grades Reed A: the citation contradicts the sentence it is attached to.
    assert results["citation_support"].passed is False
    assert results["citation_support"].data["contradicted"] == 1
    # ...and yet the citation itself is perfectly valid.
    assert results["citation_validity"].score == 1.0


def test_v1_regression_cruz_89_ft_with_not_provided_is_marked_incorrect():
    results = _score("abstain_mateo_cruz_ft", "Cruz shoots 89% free throws [S01]. NOT PROVIDED elsewhere.")
    abstention = results["abstention_correctness"]
    assert abstention.passed is False
    assert abstention.score == 0.0
    assert "89%" in abstention.detail
    # In a Mateo Cruz case the bare surname resolves to Mateo, whose FT% S01 does not record.
    assert results["citation_support"].data["contradicted"] == 1


def test_v1_regression_combined_answer_scores_below_perfect():
    reed = _score("polarity_reed_defense", V1_ANSWER)
    cruz = _score("abstain_mateo_cruz_ft", V1_ANSWER)

    assert reed["claim_polarity"].passed is False
    assert cruz["abstention_correctness"].passed is False
    for results in (reed, cruz):
        assert results["citation_validity"].score == 1.0  # every id real and retrieved
        support = results["citation_support"]
        assert support.passed is False
        assert support.data["contradicted"] + support.data["unsupported"] >= 1
        assert support.score is not None and support.score < 1.0
    # Nothing blends these into one number that could hide the failure.
    summary = S.summarize(list(reed.values()) + list(cruz.values()))
    assert "overall" not in summary.metrics
    assert summary.metrics["claim_polarity"].pass_rate == 0.0
    assert summary.metrics["abstention_correctness"].pass_rate == 0.0
    assert summary.metrics["citation_validity"].mean == 1.0
    blocking = {(r.case_id, r.key) for r in summary.blocking_failures}
    assert ("polarity_reed_defense", "claim_polarity") in blocking
    assert ("abstain_mateo_cruz_ft", "abstention_correctness") in blocking


def test_wrong_stat_10_4_vs_gold_40_4_fails_even_with_valid_citation():
    results = _score("fact_vance_three_point", "Theo Vance shoots 10.4% from three on 6.1 attempts per game [S05].")
    fact = results["fact_correctness"]
    assert fact.passed is False
    assert "10.4" in fact.detail and "40.4" in fact.detail
    assert fact.score == 0.5  # the 6.1 attempts are right; the percentage is not
    assert results["citation_validity"].passed is True
    assert results["citation_support"].data["contradicted"] == 1  # S05 says 40.4


def test_correct_fact_with_wrong_citation_passes_fact_but_fails_support():
    results = _score("fact_vance_three_point", "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S03].")
    assert results["fact_correctness"].passed is True
    assert results["citation_validity"].passed is True
    assert results["citation_support"].passed is False
    assert results["citation_support"].data["unsupported"] == 1


# ---------------------------------------------------------------- scorer discrimination


@pytest.mark.parametrize("fixture", ADVERSARIAL_FIXTURES, ids=lambda f: f.id)
def test_scorer_discrimination(fixture: AdversarialFixture):
    case = BENCHMARK.case(fixture.case_id)
    result = S.SCORERS[fixture.scorer](case, fixture.output(MANIFEST), MANIFEST)
    assert result.key == fixture.scorer
    assert result.passed is VERDICT[fixture.expect], f"{fixture.why} -> {result.detail}"
    if fixture.expect == "not_applicable":
        assert result.score is None


@pytest.mark.parametrize("fixture", PAIR_FIXTURES, ids=lambda f: f.id)
def test_objective_sensitivity_discrimination(fixture: PairFixture):
    result = S.score_pair(
        BENCHMARK.case(fixture.case_a),
        make_output(fixture.answer_a, manifest=MANIFEST),
        BENCHMARK.case(fixture.case_b),
        make_output(fixture.answer_b, manifest=MANIFEST),
        MANIFEST,
    )
    assert result.key == "objective_sensitivity"
    assert result.passed is VERDICT[fixture.expect], f"{fixture.why} -> {result.detail}"


def test_every_scorer_has_a_true_and_a_false_fixture():
    for key in S.SCORERS:
        if key == "latency_ms":  # informational, no verdict
            continue
        verdicts = {f.expect for f in ADVERSARIAL_FIXTURES if f.scorer == key}
        assert {"pass", "fail"} <= verdicts, f"{key} lacks a passing or failing fixture"
    assert {f.expect for f in PAIR_FIXTURES} == {"pass", "fail"}


def test_fixtures_reference_real_cases_and_scorers():
    ids = [f.id for f in ADVERSARIAL_FIXTURES] + [p.id for p in PAIR_FIXTURES]
    assert len(ids) == len(set(ids))
    for f in ADVERSARIAL_FIXTURES:
        assert f.scorer in S.SCORERS
        assert f.expect in VERDICT
        BENCHMARK.case(f.case_id)


def test_fixture_answers_never_name_a_real_nba_player():
    # The one real-NBA benchmark question is probed without repeating the real name.
    real = BENCHMARK.case("real_nba_comparison").expect.must_abstain[0].terms
    texts = [f.answer for f in ADVERSARIAL_FIXTURES] + [r.answer for r in REFERENCE_ANSWERS.values()]
    texts += [p.answer_a + p.answer_b for p in PAIR_FIXTURES]
    for text in texts:
        for term in real:
            assert term.lower() not in text.lower()


# ------------------------------------------------------------------ positive control


@pytest.mark.parametrize("case_id", [c.id for c in BENCHMARK.cases])
def test_reference_answers_pass_every_applicable_scorer(case_id: str):
    case = BENCHMARK.case(case_id)
    ref = REFERENCE_ANSWERS[case_id]
    output = make_output(
        ref.answer,
        manifest=MANIFEST,
        answer_kind=ref.answer_kind,
        retrieved=reference_retrieved(case.expect.retrieval, ref.answer),
        **dict(ref.extra),
    )
    failures = [(r.key, r.detail) for r in S.score_case(case, output, MANIFEST) if r.passed is False]
    assert failures == []


def test_reference_answers_cover_every_case_and_the_pair_passes():
    assert set(REFERENCE_ANSWERS) == {c.id for c in BENCHMARK.cases}
    for initial, revised in BENCHMARK.pairs():
        result = S.score_pair(
            initial,
            make_output(REFERENCE_ANSWERS[initial.id].answer, manifest=MANIFEST),
            revised,
            make_output(REFERENCE_ANSWERS[revised.id].answer, manifest=MANIFEST),
            MANIFEST,
        )
        assert result.passed is True, result.detail


# ------------------------------------------------------------- score_case and summary


def test_score_case_selects_scorers_from_expectations():
    keys = S.applicable_scorers(BENCHMARK.case("fact_vance_three_point"))
    assert keys[:2] == ["citation_validity", "citation_support"]
    assert {"retrieval_recall_at_k", "retrieval_hit", "fact_correctness", "answer_kind"} <= set(keys)
    assert "abstention_correctness" not in keys and "injection_resistance" not in keys

    injection = S.applicable_scorers(BENCHMARK.case("injection_pike_reader_submissions"))
    assert {"injection_resistance", "legality_abstention", "claim_polarity"} <= set(injection)

    results = S.score_case(BENCHMARK.case("fact_vance_three_point"), {"answer": "x", "answer_kind": "grounded_answer"}, MANIFEST)
    assert [r.key for r in results][:2] == ["runtime_error", "latency_ms"]
    assert all(r.case_id == "fact_vance_three_point" for r in results)


def test_pipeline_error_fails_every_applicable_scorer_instead_of_passing_vacuously():
    case = BENCHMARK.case("injection_pike_reader_submissions")
    results = _by_key(S.score_case(case, {"error": {"type": "ProviderTimeoutError"}, "latency_ms": 30000.0}, MANIFEST))
    assert results["runtime_error"].passed is False
    assert results["runtime_error"].severity == "blocking"
    # An empty answer contains no canary and no legality verdict; it must still not pass.
    assert results["injection_resistance"].passed is False
    assert results["legality_abstention"].passed is False
    assert "not scored" in results["injection_resistance"].detail
    assert results["retrieval_hit"].severity == "minor"
    assert results["latency_ms"].score == 30000.0


def test_summarize_reports_each_metric_separately_with_blocking_failures():
    fact_case = BENCHMARK.case("fact_vance_three_point")  # blocking
    method_case = BENCHMARK.case("method_poa_vs_team_defense")  # minor
    good = S.score_case(fact_case, make_output("Theo Vance shoots 40.4% from three on 6.1 attempts per game [S04].", manifest=MANIFEST), MANIFEST)
    bad = S.score_case(fact_case, make_output("Theo Vance shoots 10.4% from three on 6.1 attempts per game [S05].", manifest=MANIFEST), MANIFEST)
    minor = S.score_case(method_case, make_output("Defense matters.", manifest=MANIFEST, answer_kind="abstention"), MANIFEST)
    summary = S.summarize(good + bad + minor)

    assert summary.cases == 2
    fact = summary.metrics["fact_correctness"]
    assert (fact.results, fact.passed, fact.failed) == (2, 1, 1)
    assert fact.mean == pytest.approx(0.75)  # 1.0 and 0.5, averaged within the metric only
    assert fact.pass_rate == 0.5
    validity = summary.metrics["citation_validity"]
    assert validity.mean == 1.0 and validity.applicable == 2  # the uncited answer is not applicable
    assert summary.metrics["latency_ms"].pass_rate is None
    keys = {(r.case_id, r.key) for r in summary.blocking_failures}
    assert ("fact_vance_three_point", "fact_correctness") in keys
    assert all(r.case_id != "method_poa_vs_team_defense" for r in summary.blocking_failures)
    assert summary.failures_by_severity["minor"] >= 1  # the method case's answer_kind
    as_dict = summary.as_dict()
    assert set(as_dict) == {"cases", "metrics", "blocking_failures", "failures_by_severity"}
    assert as_dict["metrics"]["fact_correctness"]["pass_rate"] == 0.5


# ------------------------------------------------------------------- output handling


def test_as_output_accepts_a_pydantic_style_model():
    class FakeResponse:
        def model_dump(self):
            return {"answer": "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S04].", "answer_kind": "grounded_answer"}

    results = _by_key(S.score_case(BENCHMARK.case("fact_vance_three_point"), FakeResponse(), MANIFEST))
    assert results["fact_correctness"].passed is True
    with pytest.raises(TypeError):
        S.as_output(42)


def test_retrieved_sources_use_rank_then_fall_back_to_selected_candidates():
    output = {"sources": [
        {"source_id": "S05", "retrieval": {"rank": 2}},
        {"source_id": "S04", "retrieval": {"rank": 1}},
        {"source_id": "S05", "retrieval": {"rank": 3}},
    ]}
    assert S.retrieved_source_ids(output) == ["S04", "S05"]
    assert S.retrieved_source_ids(output, k=1) == ["S04"]
    fallback = {"diagnostics": {"candidates": [
        {"source_id": "S07", "selected": True},
        {"source_id": "S09", "selected": False},
    ]}}
    assert S.retrieved_source_ids(fallback) == ["S07"]


def test_cited_ids_combine_text_and_reported_lists():
    output = {"answer": "Reed is graded A [S3].", "cited_source_ids": ["S03"], "invalid_citations": ["s99"]}
    assert S.cited_source_ids(output) == ["S03", "S99"]


def test_latency_falls_back_to_stage_timings():
    case = BENCHMARK.case("fact_vance_three_point")
    timed = S.latency_ms(case, {"diagnostics": {"timings": [{"stage": "a", "ms": 10}, {"stage": "b", "ms": 5.5}]}}, MANIFEST)
    assert timed.score == 15.5 and timed.passed is None
    assert S.latency_ms(case, {}, MANIFEST).score is None


# ------------------------------------------------------------- claim attribution units


def _numbers(text: str, focus: frozenset[str] = frozenset()):
    return [(n.value, n.stat, n.owners, n.rejected) for v in S.analyse(text, focus, MANIFEST) for n in v.numbers]


def test_bare_shared_surname_stays_ambiguous_without_focus():
    rows = _numbers("Cruz shoots 89% from the line.")
    assert rows == [(89.0, "ft_pct", ("DANTE_CRUZ", "MATEO_CRUZ"), False)]
    focused = _numbers("Cruz shoots 89% from the line.", frozenset({"MATEO_CRUZ"}))
    assert focused[0][2] == ("MATEO_CRUZ",)


def test_number_attribution_handles_volume_ages_and_rejections():
    assert _numbers("Dante Cruz attempted 0.1 threes per game.")[0][1] == "three_pa_per_game"
    assert _numbers("Theo Vance makes 2.5 threes a game.")[0][1] is None  # no made-threes stat exists
    assert _numbers("Wes Calder (33) shoots 39.5% from three.") == [(39.5, "three_pt_pct", ("WES_CALDER",), False)]
    assert _numbers("Mateo Cruz is 23, and he averages 6.8 assists.")[0][:2] == (6.8, "assists_per_game")
    rejected = _numbers("Reed shoots 34.1% from three, not 45%.")
    assert rejected == [(34.1, "three_pt_pct", ("NIKO_REED",), False), (45.0, "three_pt_pct", ("NIKO_REED",), True)]
    assert _numbers("The 45% figure for Reed is wrong.")[0][3] is True


def test_pronoun_carries_subject_but_team_sentences_do_not():
    rows = _numbers("Theo Vance is a wing [S05]. He shoots 40.4% from three [S04]. The Comets shoot 36.0% from three.")
    assert rows[0][2] == ("THEO_VANCE",)
    assert rows[1][2] == ()  # a team-only sentence is about the team, not the carried player


def test_polarity_hits_respect_negation_attribution_and_dates():
    def hits(text: str):
        return [(h.polarity, h.negated, h.attributed, h.dated) for v in S.analyse(text, frozenset(), MANIFEST) for h in S.polarity_hits(v, MANIFEST) if h.grade is None]

    assert ("negative", True, False, False) in hits("Reed is not a bad defender.")
    assert ("positive", False, True, False) in hits("The agent memo calls Pike an elite shooter.")
    assert ("positive", False, False, True) in hits("Wes Calder was an elite wing stopper in 2023.")
    assert hits("Reed struggles to space the floor, but on defense he is fine.") == []  # spans two claims


def test_grade_polarity_and_case_focus():
    assert S._grade_polarity("A-") == "positive"
    assert S._grade_polarity("C+") is None
    assert S._grade_polarity("D") == "negative"
    focus = S.case_focus(BENCHMARK.case("ambiguity_cruz_resolved_by_history"), MANIFEST)
    assert focus == frozenset({"MATEO_CRUZ"})
    assert S.case_focus(BENCHMARK.case("ambiguity_cruz_defense"), MANIFEST) == frozenset()


def test_objective_sensitivity_requires_a_declared_revision():
    initial, revised = BENCHMARK.pairs()[0]
    result = S.score_pair(revised, {"answer": "a"}, initial, {"answer": "b"}, MANIFEST)
    assert result.passed is None and result.score is None
    errored = S.score_pair(initial, {"answer": "a"}, revised, {"error": "boom"}, MANIFEST)
    assert errored.passed is False


def test_not_applicable_results_are_excluded_from_means():
    case = BENCHMARK.case("method_poa_vs_team_defense")
    results = [S.fact_correctness(case, {"answer": ""}, MANIFEST), S.conflict_handling(case, {"answer": ""}, MANIFEST)]
    assert all(r.score is None and r.passed is None for r in results)
    summary = S.summarize(results)
    assert summary.metrics["fact_correctness"].mean is None
    assert summary.metrics["fact_correctness"].pass_rate is None
    assert results[0].as_dict()["key"] == "fact_correctness"


def test_evaluation_package_imports_without_langchain():
    """CI installs only `.[dev]`: the benchmark and scorers must never pull in LangChain."""
    code = (
        "import sys, app.assistant_rag.evaluation, app.assistant_rag.evaluation.fixtures_adversarial; "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('langchain_core', 'langchain_ollama', 'langsmith')]; "
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_markdown_tables_are_read_with_their_headers():
    text = "| Player | 3P% | 3PA |\n|---|---|---|\n| Theo Vance | 40.4% | 6.1 |\nAfter the table."
    assert S.linearise_tables(text) == "Player Theo Vance, 3P% 40.4%, 3PA 6.1.\nAfter the table."
