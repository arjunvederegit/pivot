"""The evaluation runners: local (stub, fake and live-shaped), retrieval sweep, self-test,
review worksheet, fake model and the hosted LangSmith adapter.

Most tests run on the base install: the local runner is driven by a stub answerer that
returns the benchmark's reference answers (or raises the errors a live run can hit), the
retrieval sweep runs lexical-only, and the LangSmith adapter is exercised with a fake
client and a fake `evaluate`. Tests that run the real LangChain chain (fake mode) skip
without the optional extra. Nothing contacts Ollama or LangSmith.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from app.assistant_rag.errors import AssistantInternalError, ProviderUnavailableError
from app.assistant_rag.evaluation import fake_model, langsmith_runner, review, runner
from app.assistant_rag.evaluation import scorers as S
from app.assistant_rag.evaluation.dataset import BENCHMARK_PATH, load_benchmark, parse_benchmark
from app.assistant_rag.evaluation.fixtures_adversarial import (
    REFERENCE_ANSWERS,
    V1_ANSWER,
    make_output,
    reference_retrieved,
)
from app.assistant_rag.manifest import load_manifest
from app.assistant_rag.models import AskRequest, DependenciesStatusOut, ProviderStatusOut
from app.config import get_settings

MANIFEST = load_manifest()
BENCHMARK = load_benchmark()
BY_QUESTION = {c.question: c for c in BENCHMARK.cases}
HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None


def reference_output(case_id: str) -> dict[str, Any]:
    case = BENCHMARK.case(case_id)
    ref = REFERENCE_ANSWERS[case_id]
    return make_output(ref.answer, manifest=MANIFEST, answer_kind=ref.answer_kind,
                       retrieved=reference_retrieved(case.expect.retrieval, ref.answer), **dict(ref.extra))


class StubAnswerer:
    """Answers each request with its case's reference answer, an override string, or by
    raising the given exception. Records every request."""

    chat_model = "stub-chat"
    embedding_model = "stub-embed"

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self.overrides = overrides or {}
        self.requests: list[tuple[AskRequest, str]] = []

    async def ask(self, request: AskRequest, request_id: str) -> Any:
        self.requests.append((request, request_id))
        case = BY_QUESTION[request.question]
        override = self.overrides.get(case.id)
        if isinstance(override, BaseException):
            raise override
        if isinstance(override, str):
            return make_output(override, manifest=MANIFEST)
        return reference_output(case.id)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ----------------------------------------------------------------------------- variants


def test_variants_parse_validate_and_dedupe() -> None:
    variants = runner.parse_variants("grounded:hybrid, basic:lexical,grounded:hybrid")
    assert [v.label for v in variants] == ["grounded:hybrid", "basic:lexical"]
    assert variants[1].name == "basic-lexical"
    assert runner.parse_variants([("basic", "dense"), runner.Variant("grounded", "hybrid")])[0].label == "basic:dense"
    for bad in ("grounded", "fancy:hybrid", "grounded:vector", ""):
        with pytest.raises(ValueError):
            runner.parse_variants(bad)


def test_build_request_carries_history_priorities_and_variant_options() -> None:
    case = BENCHMARK.case("revision_comets_revised")
    request = runner.build_request(case, runner.Variant("basic", "lexical"), k=4)
    assert request.question == case.question and request.team_id == "COMETS" and request.mode == "roster_fit"
    assert [m.content for m in request.history] == [m.content for m in case.history]
    assert request.previous_priorities == dict(case.previous_priorities or {})
    assert request.options is not None
    assert (request.options.prompt_variant, request.options.retrieval_variant, request.options.k) == ("basic", "lexical", 4)


def test_select_cases_keeps_benchmark_order_and_rejects_unknown_ids() -> None:
    picked = runner.select_cases(BENCHMARK, ["legality_pure_holloway", "fact_vance_three_point"])
    assert [c.id for c in picked] == ["fact_vance_three_point", "legality_pure_holloway"]
    assert len(runner.select_cases(BENCHMARK, None)) == len(BENCHMARK.cases)
    with pytest.raises(ValueError, match="unknown case id"):
        runner.select_cases(BENCHMARK, ["no_such_case"])


def test_errors_are_classified_as_environment_or_application() -> None:
    for code in ("provider_unavailable", "model_unavailable", "provider_timeout", "assistant_busy"):
        assert runner.classify_error(code) == runner.ENVIRONMENT_ERROR
    for code in ("assistant_internal_error", "validation_error", "corpus_unavailable", "unexpected_exception"):
        assert runner.classify_error(code) == runner.APPLICATION_ERROR


def test_exit_codes_separate_environment_blocking_and_fake_mode() -> None:
    code = runner.variant_exit_code
    assert code("live", environment_errors=1, application_errors=0, blocking_failures=0) == runner.EXIT_ENVIRONMENT
    assert code("live", environment_errors=0, application_errors=0, blocking_failures=3) == runner.EXIT_BLOCKING
    assert code("live", environment_errors=0, application_errors=0, blocking_failures=0) == runner.EXIT_OK
    # A fake model fails checks by design: only a crash fails a fake run.
    assert code("fake", environment_errors=0, application_errors=0, blocking_failures=9) == runner.EXIT_OK
    assert code("fake", environment_errors=0, application_errors=1, blocking_failures=9) == runner.EXIT_BLOCKING


def test_run_local_validates_mode_and_k(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode"):
        runner.run_local(BENCHMARK, variants="grounded:hybrid", mode="cloud", output_dir=tmp_path)
    with pytest.raises(ValueError, match="k must be"):
        runner.run_local(BENCHMARK, variants="grounded:hybrid", k=40, output_dir=tmp_path)


# --------------------------------------------------------------------------- local run

CASES = [
    "fact_vance_three_point",  # reference answer: passes
    "abstain_mateo_cruz_ft",  # the V1 answer: a blocking failure
    "polarity_reed_defense",  # the pipeline crashes: application error
    "conflict_vance_poa",  # Ollama down: environment error
    "teamfit_comets_perimeter",
    "revision_comets_initial",
    "revision_comets_revised",
]


def mixed_run(tmp_path: Path) -> tuple[runner.LocalRunResult, StubAnswerer]:
    stub = StubAnswerer(
        {
            "abstain_mateo_cruz_ft": V1_ANSWER,
            "polarity_reed_defense": AssistantInternalError("Ask Pivot hit an unexpected error."),
            "conflict_vance_poa": ProviderUnavailableError("Ollama is not reachable at http://localhost:11434."),
        }
    )
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid", mode="live", k=5, case_ids=CASES,
                              output_dir=tmp_path, answerer=stub)
    return result, stub


def test_run_local_scores_records_and_writes_every_file(tmp_path: Path) -> None:
    result, stub = mixed_run(tmp_path)
    assert len(stub.requests) == len(CASES)
    assert all(rid.startswith("eval-grounded-hybrid-") for _, rid in stub.requests)
    (report,) = result.reports
    directory = report.directory
    assert directory.parent == tmp_path and directory.name == f"{result.run_id}-grounded-hybrid"
    assert {p.name for p in directory.iterdir()} == {"results.jsonl", "summary.json", "report.md", "review_worksheet.md"}

    rows = {r["case_id"]: r for r in read_jsonl(directory / "results.jsonl")}
    assert list(rows) == [c.id for c in BENCHMARK.cases if c.id in CASES]
    assert rows["fact_vance_three_point"]["status"] == "ok"
    assert rows["fact_vance_three_point"]["request"]["options"] == {"prompt_variant": "grounded", "retrieval_variant": "hybrid", "k": 5}
    assert rows["fact_vance_three_point"]["response"]["answer"] == REFERENCE_ANSWERS["fact_vance_three_point"].answer
    assert rows["fact_vance_three_point"]["case"]["id"] == "fact_vance_three_point"
    # An environment failure is recorded but never scored.
    assert rows["conflict_vance_poa"]["status"] == "environment_error"
    assert rows["conflict_vance_poa"]["error"]["code"] == "provider_unavailable"
    assert rows["conflict_vance_poa"]["scores"] == []
    # An application failure fails every check for its case, explicitly.
    crash = rows["polarity_reed_defense"]
    assert crash["status"] == "application_error" and crash["response"] is None
    runtime = next(s for s in crash["scores"] if s["key"] == "runtime_error")
    assert runtime["passed"] is False and runtime["severity"] == "blocking"
    assert all(s["passed"] is False for s in crash["scores"] if s["key"] not in ("latency_ms",))
    # The pair result lands on the revised case.
    pair = [s for s in rows["revision_comets_revised"]["scores"] if s["key"] == "objective_sensitivity"]
    assert len(pair) == 1 and pair[0]["passed"] is True

    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["cases"] == {"total": 7, "scored": 6, "ok": 5, "application_errors": 1, "environment_errors": 1}
    assert summary["mode"] == "live" and summary["quality_signal"] is True
    fact = summary["metrics"]["fact_correctness"]
    assert {"mean", "pass_rate", "passed", "failed", "applicable"} <= set(fact)
    assert summary["metrics"]["abstention_correctness"]["pass_rate"] == 0.0
    assert summary["metrics"]["latency_ms"]["pass_rate"] is None  # informational, no verdict
    blocking = {(b["case_id"], b["key"]) for b in summary["blocking_failures"]}
    assert ("abstain_mateo_cruz_ft", "abstention_correctness") in blocking
    assert ("polarity_reed_defense", "runtime_error") in blocking
    assert not any(case == "conflict_vance_poa" for case, _ in blocking)
    assert summary["environment_errors"][0]["case_id"] == "conflict_vance_poa"
    assert summary["application_errors"][0]["code"] == "assistant_internal_error"
    assert summary["pairs"] == [{"initial": "revision_comets_initial", "revised": "revision_comets_revised",
                                 "status": "scored", "score": 1.0, "passed": True, "detail": pair[0]["detail"]}]
    p = summary["provenance"]
    assert p["benchmark_hash"] == BENCHMARK.content_hash and p["corpus_version"] == BENCHMARK.corpus_version
    assert p["chat_model"] == "stub-chat" and p["embedding_model"] == "stub-embed" and p["data_mode"] == "synthetic_demo"
    assert "git_commit" in p and "git_dirty" in p
    assert summary["exit_code"] == runner.EXIT_ENVIRONMENT
    assert result.exit_code == runner.EXIT_ENVIRONMENT

    report_md = (directory / "report.md").read_text(encoding="utf-8")
    for needle in ("## Metrics", "## Variant comparison", "## Failure inventory", "### abstain_mateo_cruz_ft",
                   "Niko Reed is a bad defender", "**abstention_correctness** (blocking)", "## Environment errors",
                   "no overall score"):
        assert needle in report_md
    worksheet = (directory / "review_worksheet.md").read_text(encoding="utf-8")
    assert "## case: teamfit_comets_perimeter" in worksheet and "## case: revision_comets_revised" in worksheet
    assert "## case: fact_vance_three_point" not in worksheet


def test_live_shaped_run_with_good_answers_exits_zero_and_compares_variants(tmp_path: Path) -> None:
    cases = ["fact_vance_three_point", "legality_pure_holloway", "teamfit_comets_perimeter"]
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid,basic:hybrid", case_ids=cases,
                              output_dir=tmp_path, answerer=StubAnswerer())
    assert result.exit_code == runner.EXIT_OK
    assert [r.variant.label for r in result.reports] == ["grounded:hybrid", "basic:hybrid"]
    first = (result.reports[0].directory / "report.md").read_text(encoding="utf-8")
    assert "`grounded:hybrid`" in first and "`basic:hybrid`" in first  # the comparison covers the whole run
    assert set(runner.latest_run_dirs(tmp_path)) == {r.directory for r in result.reports}


def test_fake_mode_reports_are_labelled_and_ignore_scorer_failures(tmp_path: Path) -> None:
    stub = StubAnswerer({"fact_vance_three_point": "Theo Vance is a wing [S05]."})
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid", mode="fake", case_ids=["fact_vance_three_point"],
                              output_dir=tmp_path, answerer=stub)
    summary = result.reports[0].summary
    assert summary["blocking_failures"] and result.exit_code == runner.EXIT_OK
    assert summary["quality_signal"] is False and "NOT a quality signal" in summary["note"]
    assert "NOT a quality signal" in (result.reports[0].directory / "report.md").read_text(encoding="utf-8")
    crashed = runner.run_local(BENCHMARK, variants="grounded:hybrid", mode="fake", case_ids=["fact_vance_three_point"],
                               output_dir=tmp_path, answerer=StubAnswerer({"fact_vance_three_point": RuntimeError("boom")}))
    assert crashed.exit_code == runner.EXIT_BLOCKING
    assert crashed.reports[0].records[0].error == {"code": "unexpected_exception", "message": "RuntimeError: boom",
                                                   "status": None, "cause": None}


def test_one_sided_pair_is_reported_as_skipped(tmp_path: Path) -> None:
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid", case_ids=["revision_comets_revised"],
                              output_dir=tmp_path, answerer=StubAnswerer())
    assert result.reports[0].summary["pairs"][0]["status"] == "skipped"


def test_preflight_failure_stops_before_any_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(settings: Any, mode: str) -> str:
        return "Ollama is not reachable at http://localhost:11434. Start it with 'ollama serve'."

    monkeypatch.setattr(runner, "preflight", down)
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid", case_ids=["fact_vance_three_point"], output_dir=tmp_path)
    assert result.exit_code == runner.EXIT_ENVIRONMENT and result.reports == []
    (record,) = tmp_path.glob("*-preflight.json")
    assert "ollama serve" in json.loads(record.read_text())["environment_error"]


def _status(**overrides: Any) -> ProviderStatusOut:
    fields: dict[str, Any] = {"name": "ollama", "base_url_is_local": True, "reachable": True, "version": "1",
                              "chat_model": "qwen3.5:2b", "chat_model_available": True,
                              "embedding_model": "nomic-embed-text", "embedding_model_available": True, "error": None}
    fields.update(overrides)
    return ProviderStatusOut(**fields)


def test_preflight_names_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.assistant_rag import providers

    installed = DependenciesStatusOut(installed=True, missing=[], install_hint=None)
    monkeypatch.setattr(providers, "dependency_status", lambda: installed)
    settings = get_settings()
    for status, expected in (
        (_status(), None),
        (_status(reachable=False, error=None), "ollama serve"),
        (_status(chat_model_available=False), "ollama pull qwen3.5:2b"),
        (_status(embedding_model_available=False), "ollama pull nomic-embed-text"),
    ):
        async def probe(s: Any, timeout: float = 1.5, _status: ProviderStatusOut = status) -> ProviderStatusOut:
            return _status

        monkeypatch.setattr(providers, "probe_ollama", probe)
        problem = asyncio.run(runner.preflight(settings, "live"))
        assert problem is None if expected is None else expected in (problem or "")
    missing = DependenciesStatusOut(installed=False, missing=["langchain_core"], install_hint="pip install")
    monkeypatch.setattr(providers, "dependency_status", lambda: missing)
    assert "langchain-core" in (asyncio.run(runner.preflight(settings, "fake")) or "")


def test_eval_settings_enable_dev_options_and_disable_tracing() -> None:
    base = get_settings().model_copy(update={"assistant_enabled": False, "langsmith_tracing": True})
    copy = runner.eval_settings(base)
    assert copy.assistant_enabled and copy.assistant_dev_options and not copy.langsmith_tracing
    assert base.assistant_enabled is False  # the original is untouched


# ------------------------------------------------------------------- retrieval sweep


def test_retrieval_sweep_lexical_and_hybrid_without_embeddings(tmp_path: Path) -> None:
    result = runner.run_retrieval_sweep(BENCHMARK, variants=["dense", "lexical", "hybrid"], ks=[3, 6],
                                        embeddings="none", output_dir=tmp_path)
    assert result.exit_code == runner.EXIT_OK and result.environment_error is None
    report = json.loads((result.directory / "retrieval.json").read_text(encoding="utf-8"))
    assert report["skipped"] and report["skipped"][0].startswith("dense")
    assert set(report["aggregates"]) == {"lexical", "hybrid"}
    expected_cases = sum(1 for c in BENCHMARK.cases if c.expect.retrieval is not None)
    for by_k in report["aggregates"].values():
        assert set(by_k) == {"3", "6"}
        for metrics in by_k.values():
            assert metrics["cases"] == expected_cases
            assert all(0.0 <= metrics[m] <= 1.0 for m in ("recall", "hit_rate", "full_recall_rate"))
    assert len(report["cases"]) == 2 * 2 * expected_cases
    holdout = report["holdout"]
    assert holdout["in_scope"] + holdout["out_of_scope"] == holdout["questions"] > 0
    assert set(holdout["aggregates"]["lexical"]["6"]) == {"required_recall", "hit_rate", "out_of_scope_flagged",
                                                          "in_scope_wrongly_flagged"}
    assert report["provenance"]["benchmark_hash"] == BENCHMARK.content_hash
    md = (result.directory / "retrieval.md").read_text(encoding="utf-8")
    assert "### recall@k" in md and "Held-out questions" in md and "| lexical |" in md


def test_retrieval_sweep_reports_an_unreachable_embedding_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(kind: str, settings: Any) -> Any:
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(runner, "_sweep_embeddings", refuse)
    result = runner.run_retrieval_sweep(BENCHMARK, variants=["dense"], ks=[6], embeddings="live", output_dir=tmp_path)
    assert result.exit_code == runner.EXIT_ENVIRONMENT
    assert "ollama serve" in (result.environment_error or "")
    assert "Environment error" in (result.directory / "retrieval.md").read_text(encoding="utf-8")


def test_retrieval_sweep_validates_arguments(tmp_path: Path) -> None:
    for kwargs in ({"variants": ["vector"]}, {"ks": [0]}, {"ks": []}, {"embeddings": "openai"}):
        with pytest.raises(ValueError):
            runner.run_retrieval_sweep(BENCHMARK, output_dir=tmp_path, **({"embeddings": "none"} | kwargs))


def test_holdout_loader_rejects_unknown_sources(tmp_path: Path) -> None:
    path = tmp_path / "holdout.json"
    path.write_text(json.dumps({"questions": [{"id": "x", "question": "q?", "required_sources": ["S99"]}]}))
    with pytest.raises(ValueError, match="S99"):
        runner.load_holdout(path, MANIFEST)
    assert len(runner.load_holdout(runner.HOLDOUT_PATH, MANIFEST)) > 0


# ----------------------------------------------------------------------------- selftest


def test_selftest_passes_and_shows_v1_rows_failing_as_expected() -> None:
    result = runner.run_selftest(BENCHMARK, MANIFEST)
    assert result.exit_code == runner.EXIT_OK and not result.mismatches and not result.reference_failures
    v1 = [r for r in result.rows if r.v1]
    assert {r.scorer for r in v1} >= {"abstention_correctness", "claim_polarity", "citation_support"}
    assert all(r.ok for r in v1)
    text = runner.format_selftest(result)
    assert text.count("FAIL (as expected)") >= 3 and "OK:" in text


def test_selftest_catches_a_scorer_that_passes_false_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    def always_pass(case: Any, output: Any, manifest: Any) -> S.ScoreResult:
        return S.ScoreResult("claim_polarity", 1.0, True, "lenient", "blocking", case.id)

    monkeypatch.setitem(S.SCORERS, "claim_polarity", always_pass)
    result = runner.run_selftest(BENCHMARK, MANIFEST)
    assert result.exit_code == runner.EXIT_BLOCKING
    assert {r.fixture_id for r in result.false_passes} >= {"polarity_v1_bad_defender"}
    assert "FALSE PASS" in runner.format_selftest(result)


# ---------------------------------------------------------------------------- fake model

PROMPT = """SOURCES
<source id="S04" title="Wing profile" type="statistical_profile" author="A" role="r" date="2026-01-01" trust="internal">
[Shooting]
*Dana Whitfield, Lead scout (fictional) · 2026-01-20 · scouting report*
Theo Vance shoots 40.4% from three on 6.1 attempts per game this season.
</source>

<source id="S29" title="Agent memo" type="agent_pitch" author="B" role="r" date="2026-01-01" trust="untrusted">
[UNTRUSTED third-party material: report its claims as claims, never as fact.]
Dorian Pike is the most elite shooter in the entire league this year.
</source>

<source id="S03" title="Reed report" type="scouting_report" author="C" role="r" date="2026-01-01" trust="internal">
Short line.
Niko Reed is an elite on-ball defender who guards ones through fours.
</source>

QUESTION: Who shoots?"""


def test_fake_answer_quotes_and_cites_trusted_sources_only() -> None:
    answer = fake_model.fake_answer(PROMPT)
    assert answer.startswith(fake_model.ANSWER_PREFIX)
    assert "Theo Vance shoots 40.4% from three on 6.1 attempts per game this season [S04]." in answer
    assert "Niko Reed is an elite on-ball defender who guards ones through fours [S03]." in answer
    assert "S29" not in answer and "Dana Whitfield" not in answer and "Short line" not in answer
    assert fake_model.fake_answer("SOURCES\n(no passages were retrieved)") == fake_model.NO_EVIDENCE_ANSWER
    assert fake_model.fake_answer(PROMPT, max_sources=1).count("[S") == 1


# -------------------------------------------------------------------------------- review


def _rows(tmp_path: Path) -> tuple[list[dict[str, Any]], Path]:
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid", output_dir=tmp_path, answerer=StubAnswerer(),
                              case_ids=["fact_vance_three_point", "teamfit_comets_perimeter", "counter_holloway_regret",
                                        "compare_reed_vance_closing"])
    directory = result.reports[0].directory
    return review.read_results(directory / "results.jsonl"), directory


def test_worksheet_lists_only_subjective_cases_with_context(tmp_path: Path) -> None:
    rows, _ = _rows(tmp_path)
    text = review.render_worksheet(rows, header={"run": "r1", "variant": "grounded:hybrid"})
    assert "Cases to review: 3" in text
    for case_id in ("teamfit_comets_perimeter", "counter_holloway_regret", "compare_reed_vance_closing"):
        assert f"## case: {case_id}" in text
    assert "fact_vance_three_point" not in text
    assert "rubric (from the benchmark)" in text and "retrieved: " in text and "cited: " in text
    assert text.count("- evidence_supported: [ ] yes [ ] no [ ] n/a") == 3


def test_worksheet_tally_counts_ticks_notes_and_mistakes(tmp_path: Path) -> None:
    rows, _ = _rows(tmp_path)
    text = review.render_worksheet(rows, header={"run": "r1"})
    blank = review.parse_worksheet(text)
    assert blank.cases == 3 and not blank.complete
    assert blank.criteria["trade_offs"]["unreviewed"] == 3
    filled = text.replace("- evidence_supported: [ ] yes", "- evidence_supported: [x] yes", 2)
    filled = filled.replace("- trade_offs: [ ] yes [ ] no", "- trade_offs: [ ] yes [x] no", 1)
    filled = filled.replace("- asks_gm: [ ] yes [ ] no [ ] n/a", "- asks_gm: [x] yes [x] no [ ] n/a", 1)
    filled = filled.replace("- counterargument: [ ] yes [ ] no [ ] n/a", "- counterargument: [ ] yes [ ] no [X] n/a", 1)
    filled = filled.replace("Reviewer notes: <!-- write below this line -->", "Reviewer notes: <!-- x -->\nToo long, but honest.", 1)
    tally = review.parse_worksheet(filled)
    assert tally.criteria["evidence_supported"] == {"yes": 2, "no": 0, "n/a": 0, "unreviewed": 1, "invalid": 0}
    assert tally.criteria["trade_offs"]["no"] == 1
    assert tally.criteria["asks_gm"]["invalid"] == 1
    assert tally.criteria["counterargument"]["n/a"] == 1
    assert tally.notes == {"teamfit_comets_perimeter": "Too long, but honest."}
    assert tally.per_case["teamfit_comets_perimeter"]["evidence_supported"] == "yes"
    removed = filled.replace("- respects_priorities: [ ] yes [ ] no [ ] n/a", "", 1)
    assert review.parse_worksheet(removed).criteria["respects_priorities"]["unreviewed"] == 3
    assert "Reviewed cases: 3 (incomplete)" in review.format_tally(tally)


def test_write_worksheet_never_overwrites_a_reviewers_work(tmp_path: Path) -> None:
    _, directory = _rows(tmp_path)
    path = directory / review.WORKSHEET_NAME
    path.write_text("my notes", encoding="utf-8")
    assert review.write_worksheet(directory, header={}) == (path, False)
    assert path.read_text(encoding="utf-8") == "my notes"
    assert review.write_worksheet(directory, header={}, force=True) == (path, True)
    assert "## case: teamfit_comets_perimeter" in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------- LangSmith (hosted)


class FakeDataset:
    def __init__(self, name: str) -> None:
        self.id, self.name = f"ds-{name}", name


class FakeClient:
    """Records what the hosted runner asks of LangSmith; holds examples in memory."""

    def __init__(self, existing: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.datasets = {name: FakeDataset(name) for name in (existing or {})}
        self.examples: dict[str, list[dict[str, Any]]] = {f"ds-{n}": list(v) for n, v in (existing or {}).items()}
        self.created: list[str] = []

    def has_dataset(self, *, dataset_name: str) -> bool:
        return dataset_name in self.datasets

    def create_dataset(self, *, dataset_name: str, description: str, metadata: dict[str, Any]) -> FakeDataset:
        self.datasets[dataset_name] = FakeDataset(dataset_name)
        self.created.append(dataset_name)
        return self.datasets[dataset_name]

    def read_dataset(self, *, dataset_name: str) -> FakeDataset:
        return self.datasets[dataset_name]

    def list_examples(self, *, dataset_id: str) -> list[Any]:
        return [types.SimpleNamespace(outputs=e["outputs"]) for e in self.examples.get(dataset_id, [])]

    def create_examples(self, *, dataset_id: str, examples: list[dict[str, Any]]) -> None:
        self.examples.setdefault(dataset_id, []).extend(examples)


def hosted_settings(key: str = "test-key-not-real") -> Any:
    return get_settings().model_copy(update={"langsmith_api_key": key, "langsmith_tracing": False})


def test_hosted_is_unverified_without_a_key_and_never_builds_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = types.ModuleType("langsmith")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a LangSmith Client must not be constructed without an API key")

    stub.Client = forbidden  # type: ignore[attr-defined]
    stub.evaluate = forbidden  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", stub)
    result = langsmith_runner.run_experiments("grounded:hybrid", settings=hosted_settings(key=""))
    assert result.status == "unverified" and result.message == "unverified: LANGSMITH_API_KEY not set"
    assert result.exit_code == runner.EXIT_ENVIRONMENT and result.experiments == []


def test_dataset_name_follows_the_benchmark_content() -> None:
    name = langsmith_runner.dataset_name(BENCHMARK)
    assert name == "pivot-intelligence-v2-benchmark-" + BENCHMARK.content_hash[:8]
    raw = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    raw["cases"][0]["rubric"] += " Edited."
    assert langsmith_runner.dataset_name(parse_benchmark(raw, MANIFEST)) != name


def test_examples_carry_requests_and_expectations_but_no_gold_values() -> None:
    examples = langsmith_runner.build_examples(BENCHMARK)
    assert len(examples) == len(BENCHMARK.cases)
    first = examples[0]
    case = BENCHMARK.cases[0]
    assert first["inputs"] == case.request_payload()
    assert first["outputs"]["case_id"] == case.id and first["outputs"]["expect"] == case.raw["expect"]
    assert first["split"] == case.category and first["metadata"]["data_mode"] == "synthetic_demo"
    assert "40.4" not in json.dumps(first["outputs"])  # gold stays in the manifest


def test_sync_creates_once_repairs_missing_and_refuses_a_foreign_dataset() -> None:
    client = FakeClient()
    sync = langsmith_runner.sync_dataset(client, BENCHMARK)
    assert sync.created and sync.examples_created == len(BENCHMARK.cases) and client.created == [sync.name]
    again = langsmith_runner.sync_dataset(client, BENCHMARK)
    assert not again.created and again.examples_created == 0 and client.created == [sync.name]
    partial = FakeClient({sync.name: langsmith_runner.build_examples(BENCHMARK)[:5]})
    repaired = langsmith_runner.sync_dataset(partial, BENCHMARK)
    assert repaired.examples_created == len(BENCHMARK.cases) - 5
    foreign = FakeClient({sync.name: [{"outputs": {"case_id": "from_another_benchmark"}}]})
    with pytest.raises(langsmith_runner.HostedEvaluationError):
        langsmith_runner.sync_dataset(foreign, BENCHMARK)


def test_evaluators_wrap_scorers_as_langsmith_feedback() -> None:
    evaluators = langsmith_runner.make_evaluators(BENCHMARK, MANIFEST)
    keys = [e.__name__ for e in evaluators]
    assert "latency_ms" not in keys and set(keys) == set(S.SCORERS) - {"latency_ms"}
    assert list(inspect.signature(evaluators[0]).parameters) == ["outputs", "reference_outputs"]
    ref = {"case_id": "fact_vance_three_point"}
    fact = langsmith_runner.make_evaluator("fact_correctness", BENCHMARK, MANIFEST)
    good = fact(reference_output("fact_vance_three_point"), ref)
    assert good["key"] == "fact_correctness" and good["score"] == 1.0 and good["comment"].startswith("PASS [blocking]")
    bad = fact(make_output("Theo Vance shoots 10.4% from three on 6.1 attempts per game [S05].", manifest=MANIFEST), ref)
    assert bad["score"] == 0.5 and bad["comment"].startswith("FAIL")  # 6.1 is right, 10.4 is not
    assert langsmith_runner.make_evaluator("clarification", BENCHMARK, MANIFEST)({}, ref)["score"] is None
    env = fact({"error": "provider_unavailable", "error_class": "environment_error"}, ref)
    assert env["score"] is None and "environment" in env["comment"]
    assert fact({}, {"case_id": "nope"})["score"] is None


def test_summary_evaluators_score_the_pair_and_count_blocking_failures() -> None:
    objective, blocking = langsmith_runner.make_summary_evaluators(BENCHMARK, MANIFEST)
    ids = ["revision_comets_initial", "revision_comets_revised", "abstain_mateo_cruz_ft"]
    outputs = [reference_output(ids[0]), reference_output(ids[1]), make_output(V1_ANSWER, manifest=MANIFEST)]
    refs = [{"case_id": i} for i in ids]
    assert objective(outputs, refs)["score"] == 1.0
    counted = blocking(outputs, refs)
    assert counted["score"] >= 1 and "abstain_mateo_cruz_ft:abstention_correctness" in counted["comment"]
    assert objective(outputs[2:], refs[2:])["score"] is None


def test_run_experiments_wires_dataset_target_evaluators_and_metadata() -> None:
    client = FakeClient()
    calls: list[dict[str, Any]] = []

    class Results:
        def __init__(self, prefix: str) -> None:
            self.experiment_name = f"{prefix}-abc123"
            self.url = f"https://smith.example/{prefix}"

    def fake_evaluate(target: Any, **kwargs: Any) -> Results:
        examples = client.examples[client.datasets[kwargs["data"]].id]
        rows = []
        for example in examples[:3]:
            output = target(example["inputs"])
            rows.append([e(output, example["outputs"]) for e in kwargs["evaluators"]])
        calls.append({"target": target, "rows": rows, **kwargs})
        return Results(kwargs["experiment_prefix"])

    def answer(request: AskRequest) -> Any:
        case = BY_QUESTION[request.question]
        if case.id == "fact_mateo_cruz_assists":
            raise ProviderUnavailableError("Ollama is not reachable")
        return reference_output(case.id)

    result = langsmith_runner.run_experiments("grounded:hybrid,basic:lexical", settings=hosted_settings(), client=client,
                                              evaluate_fn=fake_evaluate, answer_fn=answer, k=5)
    assert result.status == "ok" and result.exit_code == runner.EXIT_OK and result.dataset is not None
    assert [e["experiment_name"] for e in result.experiments] == ["pivot-v2-grounded-hybrid-abc123", "pivot-v2-basic-lexical-abc123"]
    assert result.experiments[0]["url"] == "https://smith.example/pivot-v2-grounded-hybrid"
    first = calls[0]
    assert first["data"] == result.dataset.name and first["max_concurrency"] == 1 and first["client"] is client
    assert first["metadata"] == {
        "corpus_version": BENCHMARK.corpus_version, "benchmark_hash": BENCHMARK.content_hash,
        "benchmark_version": BENCHMARK.version, "prompt_variant": "grounded", "retrieval_variant": "hybrid", "k": 5,
        "chat_model": hosted_settings().assistant_chat_model, "embedding_model": hosted_settings().assistant_embedding_model,
        "data_mode": "synthetic_demo",
    }
    fact_row = {r["key"]: r for r in first["rows"][0]}
    assert fact_row["fact_correctness"]["score"] == 1.0
    env_row = {r["key"]: r for r in first["rows"][1]}  # the second example raised a provider error
    assert env_row["runtime_error"]["score"] is None
    output = first["target"]({**BENCHMARK.case("fact_mateo_cruz_assists").request_payload()})
    assert output["error"] == "provider_unavailable" and output["error_class"] == "environment_error"
    assert "latency_ms" in output


# --------------------------------------------------------------- LangChain-backed paths


@pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")
def test_fake_mode_runs_the_real_chain_end_to_end(tmp_path: Path) -> None:
    cases = ["fact_vance_three_point", "ambiguity_cruz_defense", "legality_pure_holloway", "teamfit_comets_perimeter"]
    result = runner.run_local(BENCHMARK, variants="grounded:hybrid", mode="fake", case_ids=cases, output_dir=tmp_path)
    assert result.exit_code == runner.EXIT_OK
    (report,) = result.reports
    assert [r.status for r in report.records] == ["ok"] * 4
    by_id = {r.case.id: r for r in report.records}
    assert by_id["ambiguity_cruz_defense"].response["answer_kind"] == "clarification"  # type: ignore[index]
    assert by_id["legality_pure_holloway"].response["answer_kind"] == "unsupported_request"  # type: ignore[index]
    assert by_id["fact_vance_three_point"].response["answer"].startswith(fake_model.ANSWER_PREFIX)  # type: ignore[index]
    summary = json.loads((report.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["provenance"]["chat_model"] == fake_model.FAKE_CHAT_MODEL_NAME
    assert summary["provenance"]["embedding_model"] == "DeterministicFakeEmbedding"
    assert summary["quality_signal"] is False and summary["cases"]["ok"] == 4
    assert {"results.jsonl", "summary.json", "report.md", "review_worksheet.md"} <= {p.name for p in report.directory.iterdir()}


@pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")
def test_fake_chat_model_is_a_langchain_chat_model() -> None:
    from langchain_core.messages import HumanMessage, SystemMessage

    model = fake_model.build_fake_chat_model()
    reply = model.invoke([SystemMessage(content="rules"), HumanMessage(content=PROMPT)])
    assert reply.content == fake_model.fake_answer(PROMPT)
    assert model.model == fake_model.FAKE_CHAT_MODEL_NAME


@pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")
def test_retrieval_sweep_with_fake_embeddings_includes_dense(tmp_path: Path) -> None:
    result = runner.run_retrieval_sweep(BENCHMARK, variants=["dense"], ks=[6], embeddings="fake", output_dir=tmp_path,
                                        holdout_path=None)
    assert result.exit_code == runner.EXIT_OK and result.embedding_model == "DeterministicFakeEmbedding"
    assert "6" in result.report["aggregates"]["dense"] and result.report["holdout"] is None
    assert "FAKE EMBEDDINGS" in (result.directory / "retrieval.md").read_text(encoding="utf-8")


def test_hosted_probes_ollama_before_building_the_real_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(settings: Any, mode: str) -> str:
        return "Ollama is not reachable at http://localhost:11434. Start it with 'ollama serve'."

    monkeypatch.setattr(runner, "preflight", down)
    result = langsmith_runner.run_experiments("grounded:hybrid", settings=hosted_settings(), client=FakeClient(),
                                              evaluate_fn=lambda *a, **k: pytest.fail("must not evaluate"))
    assert result.status == "environment_error" and "ollama serve" in result.message
    assert result.exit_code == runner.EXIT_ENVIRONMENT


def test_hosted_builds_its_client_from_settings_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []
    shared = FakeClient()

    def client_factory(**kwargs: Any) -> FakeClient:
        built.append(kwargs)
        return shared

    stub = types.ModuleType("langsmith")
    stub.Client = client_factory  # type: ignore[attr-defined]
    stub.evaluate = lambda target, **kwargs: types.SimpleNamespace(experiment_name="exp", url=None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", stub)
    settings = hosted_settings().model_copy(update={"langsmith_endpoint": "https://smith.example"})
    result = langsmith_runner.run_experiments("grounded:hybrid", settings=settings, answer_fn=lambda r: {})
    assert built == [{"api_key": "test-key-not-real", "api_url": "https://smith.example"}]
    assert result.experiments == [{"variant": "grounded:hybrid", "experiment_name": "exp", "url": None}]


def test_pipeline_answerer_maps_failures_to_assistant_errors() -> None:
    from app.assistant_rag.errors import AssistantError

    answerer = langsmith_runner.PipelineAnswerer(hosted_settings())
    request = runner.build_request(BENCHMARK.case("fact_vance_three_point"), runner.Variant("grounded", "hybrid"))

    class Chain:
        def __init__(self, outcome: Any) -> None:
            self.outcome, self.calls = outcome, []

        def invoke(self, payload: Any, config: Any = None) -> Any:
            self.calls.append((payload, config))
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

    answerer._chain = Chain("response")
    assert answerer(request) == "response"
    _, config = answerer._chain.calls[0]
    assert config["run_name"] == "ask_pivot" and config["metadata"]["prompt_variant"] == "grounded"
    for exc, code in ((ConnectionError("refused"), "provider_unavailable"), (ValueError("bad"), "assistant_internal_error"),
                      (ProviderUnavailableError("down"), "provider_unavailable")):
        answerer._chain = Chain(exc)
        with pytest.raises(AssistantError) as raised:
            answerer(request)
        assert raised.value.code == code


@pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")
def test_pipeline_answerer_runs_the_chain_synchronously_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant_rag import embeddings, providers

    monkeypatch.setattr(embeddings, "build_embeddings", lambda settings: fake_model.build_fake_embeddings())
    monkeypatch.setattr(providers, "build_chat_model", lambda settings: fake_model.build_fake_chat_model())
    answerer = langsmith_runner.PipelineAnswerer(hosted_settings())
    target = langsmith_runner.make_target(runner.Variant("grounded", "lexical"), answerer)
    output = target(BENCHMARK.case("fact_vance_three_point").request_payload())
    assert output["answer"].startswith(fake_model.ANSWER_PREFIX)
    assert output["diagnostics"]["retrieval_variant"] == "lexical" and output["latency_ms"] >= 0
