"""Evaluation for Ask Pivot: a version-controlled benchmark and deterministic evaluators.

How the pieces fit — the same shape LangSmith uses, so the hosted runner is a thin adapter:

  dataset      `benchmark.json` -> `Benchmark` of `BenchmarkCase`s. A case is an *example*:
               an `AskRequest` (inputs) plus machine-checkable expectations (the reference)
               and a human-review rubric. Gold values are never stored here; they come from
               `fixtures/manifest.json` when a case is scored.
  scorers      *evaluators*: `scorer(case, output, manifest) -> ScoreResult`, one per failure
               mode (fact correctness, abstention, polarity, citation support, injection, ...).
               `score_case` runs the ones a case calls for; `summarize` reports each metric's
               mean and pass rate separately — there is deliberately no blended overall score.
  fixtures_adversarial
               the evaluators' own test set: hand-written answers with a known verdict,
               including the V1 answer that the old substring evaluator scored 3/3.
  runner / langsmith_runner
               run the pipeline (live Ollama or a fake model) over the benchmark and score it,
               locally or as a LangSmith experiment (built separately; they import this API).

Why deterministic evaluators rather than an LLM judge: they are reproducible, free, run in CI
without a model, and can be tested against known answers. They judge only what is checkable —
numbers against the manifest, directions against analyst grades, citations against what the
cited source carries. Whether an answer *explained* a trade-off well is left to the rubric and
human review, and that limit is stated rather than papered over.

Pure Python: importing this package never imports LangChain.
"""

from app.assistant_rag.evaluation.dataset import (
    BENCHMARK_PATH,
    CATEGORIES,
    Benchmark,
    BenchmarkCase,
    BenchmarkError,
    load_benchmark,
    parse_benchmark,
)
from app.assistant_rag.evaluation.scorers import (
    SCORERS,
    ScoreResult,
    Summary,
    applicable_scorers,
    score_case,
    score_pair,
    summarize,
)

__all__ = [
    "BENCHMARK_PATH",
    "CATEGORIES",
    "SCORERS",
    "Benchmark",
    "BenchmarkCase",
    "BenchmarkError",
    "ScoreResult",
    "Summary",
    "applicable_scorers",
    "load_benchmark",
    "parse_benchmark",
    "score_case",
    "score_pair",
    "summarize",
]
