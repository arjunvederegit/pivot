"""Hosted evaluation: the same benchmark and scorers, run as LangSmith experiments.

The local runner (`runner.py`) and this one compute the same scores with the same code. The
difference is where the results live: here they are uploaded to LangSmith, where two
experiments can be compared side by side, example by example, with every run's full trace
one click away. Only runs when `LANGSMITH_API_KEY` is set; without it, `run_experiments`
returns an "unverified" result and never constructs a client.

The four LangSmith concepts, and what each one is here:

* **Dataset and examples.** A dataset is a named, versioned collection of *examples*; each
  example has `inputs` (what the target is called with) and reference `outputs` (what a
  good answer must satisfy). Here one example per benchmark case: `inputs` are the request
  fields (question, mode, team, priorities, previous priorities, constraints, history) and
  `outputs` are the case id plus its expectations — never gold values, which the scorers
  read from the manifest. The dataset name embeds the benchmark's content hash
  (`pivot-intelligence-v2-benchmark-<hash8>`), so editing `benchmark.json` creates a *new*
  dataset instead of silently evaluating against a stale copy; examples are uploaded only
  when the dataset is new (or, after an interrupted upload, only the missing ones). Each
  example's `split` is its category, so the UI can filter to e.g. `injection`.
* **Target.** The function under test: `inputs -> outputs`. Here it builds the `AskRequest`
  (adding the variant as dev options) and invokes the pipeline's LangChain chain directly,
  synchronously, in the evaluation thread. That keeps the chain's runs — one per stage,
  with the chat-model call nested inside `model_call` — *inside* the experiment's run for
  that example, so each row in the experiment links to the full trace. (Going through
  `AssistantService` would hop to its own event loop thread, where the experiment's
  tracing context does not follow.)
* **Evaluators.** Functions `(outputs, reference_outputs) -> {"key", "score", "comment"}`
  that LangSmith attaches to each run as *feedback*. Each wrapper here runs
  `scorers.score_case` exactly as the local runner does and reports one scorer's result:
  `score` is the scorer's score (None when the scorer does not apply to that case), and
  `comment` carries PASS/FAIL, severity and the scorer's explanation. `latency_ms` is not
  uploaded — LangSmith records every run's latency itself. A *summary evaluator* sees all
  runs at once; here it scores the priority-revision pair (objective sensitivity) and
  counts blocking failures.
* **Experiment.** One pass of the target over the dataset, with its evaluators, under one
  configuration. `run_experiments` creates one per variant, named
  `pivot-v2-<prompt>-<retrieval>-<suffix>`, with metadata (corpus version, benchmark hash,
  variants, models, `data_mode: synthetic_demo`) so experiments can be filtered later.

Comparing two experiments in the UI: open *Datasets & Experiments*, pick the
`pivot-intelligence-v2-benchmark-...` dataset, tick two experiments (say `pivot-v2-basic-
hybrid` and `pivot-v2-grounded-hybrid`) and choose *Compare*. Each row is one benchmark case
with both answers side by side; each feedback key is a column, with regressions and
improvements highlighted; clicking a cell opens that run's trace. The URL printed for each
experiment opens it in that view.

PRIVACY. Inference stays on this machine, but a hosted run uploads: every benchmark example,
every prompt the pipeline builds (the retrieved, fictional evidence included), every answer
and every score, to LangSmith (or `LANGSMITH_ENDPOINT`). The corpus is synthetic; the upload
is still real. That is why this only runs on request, with a key.

LangSmith is imported lazily and every network-facing object (`client`, `evaluate_fn`,
`answer_fn`) is injectable, so the wiring is tested with fakes and no network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any

from app.assistant_rag import DATA_MODE
from app.assistant_rag.errors import AssistantError, AssistantInternalError
from app.assistant_rag.evaluation import scorers
from app.assistant_rag.evaluation.dataset import Benchmark, BenchmarkCase
from app.assistant_rag.evaluation.runner import (
    ENVIRONMENT_ERROR,
    EXIT_ENVIRONMENT,
    EXIT_OK,
    Variant,
    classify_error,
    parse_variants,
)
from app.assistant_rag.manifest import Manifest
from app.assistant_rag.models import AskOptionsIn, AskRequest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

DATASET_PREFIX = "pivot-intelligence-v2-benchmark-"
EXPERIMENT_PREFIX = "pivot-v2"
UNVERIFIED_NO_KEY = "unverified: LANGSMITH_API_KEY not set"
#: Scorers not uploaded as feedback (LangSmith measures run latency itself).
NOT_UPLOADED = frozenset({"latency_ms"})
PRIVACY_NOTICE = (
    "Hosted evaluation uploads every benchmark example, every prompt (with the retrieved fictional "
    "evidence), every answer and every score to LangSmith. Inference stays local; the upload does not."
)

Target = Callable[[dict[str, Any]], dict[str, Any]]
AnswerFn = Callable[[AskRequest], Any]


class HostedEvaluationError(RuntimeError):
    """The hosted dataset does not match the benchmark it is named after."""


# ------------------------------------------------------------------------------ dataset


def dataset_name(benchmark: Benchmark) -> str:
    """The dataset for exactly this benchmark content: any edit changes the name."""
    return DATASET_PREFIX + benchmark.content_hash[:8]


def example_inputs(case: BenchmarkCase) -> dict[str, Any]:
    """What the target is called with: the request fields, without dev options."""
    return case.request_payload()


def example_outputs(case: BenchmarkCase, benchmark: Benchmark) -> dict[str, Any]:
    """The reference: which case this is and what it expects. No gold values."""
    return {
        "case_id": case.id,
        "category": case.category,
        "severity": case.severity,
        "pair_with": case.pair_with,
        "expect": dict(case.raw.get("expect") or {}),
        "rubric": case.rubric,
        "benchmark_hash": benchmark.content_hash,
    }


def build_examples(benchmark: Benchmark, cases: Sequence[BenchmarkCase] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "inputs": example_inputs(case),
            "outputs": example_outputs(case, benchmark),
            "metadata": {"case_id": case.id, "category": case.category, "severity": case.severity,
                         "corpus_version": benchmark.corpus_version, "data_mode": DATA_MODE},
            "split": case.category,
        }
        for case in (benchmark.cases if cases is None else cases)
    ]


@dataclass(frozen=True)
class DatasetSync:
    name: str
    dataset_id: str | None
    created: bool
    examples_created: int
    examples_total: int


def sync_dataset(client: Any, benchmark: Benchmark) -> DatasetSync:
    """Create the content-addressed dataset and its examples if it is new; otherwise reuse
    it, uploading only examples missing after an interrupted upload. An example whose case
    id is not in the benchmark means the dataset is not what its name claims: refuse."""
    name = dataset_name(benchmark)
    if not client.has_dataset(dataset_name=name):
        dataset = client.create_dataset(
            dataset_name=name,
            description=f"Ask Pivot V2 benchmark {benchmark.id} v{benchmark.version} over the fictional corpus "
            f"{benchmark.corpus_version} (synthetic_demo). Content hash {benchmark.content_hash}.",
            metadata={"benchmark_hash": benchmark.content_hash, "corpus_version": benchmark.corpus_version,
                      "data_mode": DATA_MODE},
        )
        client.create_examples(dataset_id=dataset.id, examples=build_examples(benchmark))
        return DatasetSync(name, str(dataset.id), True, len(benchmark.cases), len(benchmark.cases))
    dataset = client.read_dataset(dataset_name=name)
    existing = {str((getattr(ex, "outputs", None) or {}).get("case_id")) for ex in client.list_examples(dataset_id=dataset.id)}
    unexpected = sorted(existing - {c.id for c in benchmark.cases})
    if unexpected:
        raise HostedEvaluationError(
            f"dataset {name} holds examples for cases not in this benchmark ({unexpected[:5]}); "
            "delete it in LangSmith and rerun"
        )
    missing = [c for c in benchmark.cases if c.id not in existing]
    if missing:
        client.create_examples(dataset_id=dataset.id, examples=build_examples(benchmark, missing))
    return DatasetSync(name, str(dataset.id), False, len(missing), len(benchmark.cases))


# --------------------------------------------------------------------------- evaluators


def _case_for(reference_outputs: Mapping[str, Any] | None, benchmark: Benchmark) -> BenchmarkCase | None:
    try:
        return benchmark.case(str((reference_outputs or {}).get("case_id")))
    except KeyError:
        return None


def _is_environment_failure(outputs: Mapping[str, Any]) -> bool:
    return outputs.get("error_class") == ENVIRONMENT_ERROR


def evaluate_output(
    key: str, outputs: Mapping[str, Any] | None, reference_outputs: Mapping[str, Any] | None,
    benchmark: Benchmark, manifest: Manifest,
) -> dict[str, Any]:
    """One scorer's verdict on one run, as LangSmith feedback. Same rules as the local
    runner: an environment failure is not scored (score None) rather than counted wrong."""
    case = _case_for(reference_outputs, benchmark)
    if case is None:
        return {"key": key, "score": None, "comment": "unknown case id: this dataset does not match the benchmark"}
    output = dict(outputs) if outputs else {"error": "the target produced no output"}
    if _is_environment_failure(output):
        return {"key": key, "score": None, "comment": f"not scored: environment error ({output.get('error')})"}
    result = next((r for r in scorers.score_case(case, output, manifest) if r.key == key), None)
    if result is None:
        return {"key": key, "score": None, "comment": "not applicable to this case"}
    verdict = {True: "PASS", False: "FAIL", None: "n/a"}[result.passed]
    return {"key": key, "score": result.score, "comment": f"{verdict} [{result.severity}] {result.detail}"}


def make_evaluator(key: str, benchmark: Benchmark, manifest: Manifest) -> Callable[..., dict[str, Any]]:
    """A LangSmith evaluator for one scorer. LangSmith inspects the parameter *names*:
    `outputs` receives the target's return value, `reference_outputs` the example's outputs."""

    def evaluator(outputs: dict[str, Any], reference_outputs: dict[str, Any]) -> dict[str, Any]:
        return evaluate_output(key, outputs, reference_outputs, benchmark, manifest)

    evaluator.__name__ = key
    return evaluator


def make_evaluators(benchmark: Benchmark, manifest: Manifest) -> list[Callable[..., dict[str, Any]]]:
    return [make_evaluator(key, benchmark, manifest) for key in scorers.SCORERS if key not in NOT_UPLOADED]


def make_summary_evaluators(benchmark: Benchmark, manifest: Manifest) -> list[Callable[..., dict[str, Any]]]:
    """Experiment-level evaluators: they receive every run's outputs at once."""

    def objective_sensitivity(outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]) -> dict[str, Any]:
        by_case = {str(ref.get("case_id")): out for out, ref in zip(outputs, reference_outputs, strict=False)}
        results = []
        for initial, revised in benchmark.pairs():
            a, b = by_case.get(initial.id), by_case.get(revised.id)
            if a is None or b is None or _is_environment_failure(a) or _is_environment_failure(b):
                continue
            results.append(scorers.score_pair(initial, a, revised, b, manifest))
        if not results:
            return {"key": "objective_sensitivity", "score": None, "comment": "no complete pair in this experiment"}
        score = sum(r.score or 0.0 for r in results) / len(results)
        comment = " | ".join(f"{'PASS' if r.passed else 'FAIL'} {r.case_id}: {r.detail}" for r in results)
        return {"key": "objective_sensitivity", "score": score, "comment": comment}

    def blocking_failures(outputs: list[dict[str, Any]], reference_outputs: list[dict[str, Any]]) -> dict[str, Any]:
        failed: list[str] = []
        for out, ref in zip(outputs, reference_outputs, strict=False):
            case = _case_for(ref, benchmark)
            if case is None or _is_environment_failure(out or {}):
                continue
            output = dict(out) if out else {"error": "the target produced no output"}
            failed += [f"{case.id}:{r.key}" for r in scorers.score_case(case, output, manifest)
                       if r.passed is False and r.severity == "blocking"]
        return {"key": "blocking_failures", "score": len(failed), "comment": ", ".join(failed[:20]) or "none"}

    return [objective_sensitivity, blocking_failures]


# ------------------------------------------------------------------------------- target


class PipelineAnswerer:
    """The pipeline's LangChain chain, built once and invoked synchronously (module doc).

    Needs the `.[assistant]` extra and a running Ollama; built lazily on first call."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._chain: Any = None
        self._corpus_version: str | None = None
        self._embedding_model: str | None = None

    def _build(self) -> None:
        from app.assistant_rag import providers
        from app.assistant_rag.corpus import load_corpus
        from app.assistant_rag.embeddings import build_embeddings
        from app.assistant_rag.pipeline import build_chain
        from app.assistant_rag.retrieval import HybridRetriever

        corpus = load_corpus()
        retriever = HybridRetriever(corpus, build_embeddings(self.settings))
        retriever.build()
        chat_model = providers.build_chat_model(self.settings)
        self._chain = build_chain(corpus=corpus, retriever=retriever, chat_model=chat_model, settings=self.settings)
        self._corpus_version = corpus.manifest.corpus_version
        self._embedding_model = retriever.embedding_model

    def __call__(self, request: AskRequest) -> Any:
        from app.assistant_rag import pipeline, providers

        try:
            if self._chain is None:
                self._build()
            metadata = pipeline.run_metadata(request, self.settings, corpus_version=self._corpus_version or "",
                                             embedding_model=self._embedding_model)
            return self._chain.invoke({"request": request, "request_id": None, "trace": None},
                                      config=pipeline.run_config(metadata, pipeline.run_tags(metadata)))
        except AssistantError:
            raise
        except Exception as exc:
            mapped = providers.map_provider_error(exc, settings=self.settings)
            if mapped is not None:
                raise mapped from exc
            raise AssistantInternalError(f"{type(exc).__name__}: {exc}") from exc


def make_target(variant: Variant, answer_fn: AnswerFn, k: int | None = None) -> Target:
    """`inputs -> outputs` for one variant. Errors become an `error` output (classified as
    environment or application) so the evaluators can report them, never a lost row."""

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        options = AskOptionsIn(prompt_variant=variant.prompt_variant, retrieval_variant=variant.retrieval_variant, k=k)  # type: ignore[arg-type]
        started = perf_counter()
        try:
            response = answer_fn(AskRequest(**inputs, options=options))
            output = dict(response.model_dump(mode="json")) if hasattr(response, "model_dump") else dict(response)
        except AssistantError as exc:
            output = {"error": exc.code, "error_message": exc.message, "error_class": classify_error(exc.code)}
        output["latency_ms"] = round((perf_counter() - started) * 1000.0, 1)
        return output

    target.__name__ = f"ask_pivot_{variant.name.replace('-', '_')}"
    return target


# -------------------------------------------------------------------------- experiments


@dataclass
class HostedResult:
    status: str  # "ok" | "unverified" | "environment_error"
    message: str
    dataset: DatasetSync | None = None
    experiments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_OK if self.status == "ok" else EXIT_ENVIRONMENT


def experiment_metadata(benchmark: Benchmark, variant: Variant, settings: Settings, k: int | None) -> dict[str, Any]:
    return {
        "corpus_version": benchmark.corpus_version,
        "benchmark_hash": benchmark.content_hash,
        "benchmark_version": benchmark.version,
        "prompt_variant": variant.prompt_variant,
        "retrieval_variant": variant.retrieval_variant,
        "k": k,
        "chat_model": settings.assistant_chat_model,
        "embedding_model": settings.assistant_embedding_model,
        "data_mode": DATA_MODE,
    }


def _experiment_ref(variant: Variant, results: Any) -> dict[str, Any]:
    ref: dict[str, Any] = {"variant": variant.label, "experiment_name": getattr(results, "experiment_name", None), "url": None}
    try:
        ref["url"] = getattr(results, "url", None)
    except Exception:  # noqa: BLE001 - the link is a convenience; the experiment exists regardless
        ref["url"] = None
    return ref


def _langsmith_evaluate() -> Callable[..., Any]:
    from langsmith import evaluate

    return evaluate


def _ollama_problem(settings: Settings) -> str | None:
    import asyncio

    from app.assistant_rag.evaluation.runner import preflight

    return asyncio.run(preflight(settings, "live"))


def run_experiments(
    variants: str | Sequence[Variant | tuple[str, str] | str],
    *,
    benchmark: Benchmark | None = None,
    settings: Settings | None = None,
    manifest: Manifest | None = None,
    k: int | None = None,
    client: Any = None,
    evaluate_fn: Callable[..., Any] | None = None,
    answer_fn: AnswerFn | None = None,
    progress: Callable[[str], None] | None = None,
) -> HostedResult:
    """Sync the dataset and run one LangSmith experiment per variant (module doc).

    Without `settings.langsmith_api_key` nothing is constructed or contacted: the result is
    `unverified`. With the real `answer_fn`, Ollama is probed first."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not settings.langsmith_api_key:
        return HostedResult(status="unverified", message=UNVERIFIED_NO_KEY)
    chosen = parse_variants(variants)
    if benchmark is None or manifest is None:
        from app.assistant_rag.evaluation.dataset import load_benchmark
        from app.assistant_rag.manifest import load_manifest

        manifest = manifest or load_manifest()
        benchmark = benchmark or load_benchmark(manifest=manifest)
    say = progress or (lambda message: None)
    if answer_fn is None:
        problem = _ollama_problem(settings)
        if problem:
            return HostedResult(status="environment_error", message=problem)
        answer_fn = PipelineAnswerer(settings)
    if client is None:
        from langsmith import Client

        client = Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint or None)
    run_evaluation: Callable[..., Any] = evaluate_fn or _langsmith_evaluate()
    sync = sync_dataset(client, benchmark)
    say(f"dataset {sync.name}: {'created' if sync.created else 'reused'} ({sync.examples_created} examples uploaded)")
    evaluators = make_evaluators(benchmark, manifest)
    summary_evaluators = make_summary_evaluators(benchmark, manifest)
    experiments = []
    for variant in chosen:
        say(f"experiment {EXPERIMENT_PREFIX}-{variant.name}: running {len(benchmark.cases)} examples sequentially")
        results = run_evaluation(
            make_target(variant, answer_fn, k),
            data=sync.name,
            evaluators=evaluators,
            summary_evaluators=summary_evaluators,
            experiment_prefix=f"{EXPERIMENT_PREFIX}-{variant.name}",
            description=f"Ask Pivot V2, prompt={variant.prompt_variant}, retrieval={variant.retrieval_variant} (synthetic_demo)",
            metadata=experiment_metadata(benchmark, variant, settings, k),
            max_concurrency=1,
            client=client,
        )
        experiments.append(_experiment_ref(variant, results))
    return HostedResult(status="ok", message=f"{len(experiments)} experiment(s) on dataset {sync.name}",
                        dataset=sync, experiments=experiments)
