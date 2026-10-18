"""The local evaluation runner: the real pipeline over the benchmark, scored, written down.

An evaluation run has the same four parts LangSmith names (see `langsmith_runner.py`):

    dataset     `benchmark.json` — each case is an example: an `AskRequest` plus expectations
    target      the thing under test — here Ask Pivot's pipeline, called in-process
    evaluators  `scorers.py` — one deterministic check per failure mode
    experiment  one pass of the target over the dataset under one configuration (a *variant*:
                prompt variant x retrieval variant), with every score recorded

`run_local` runs one experiment per variant on this machine and writes, per variant, to
`.assistant_eval/<run id>-<prompt>-<retrieval>/`:

    results.jsonl          one line per case: the case, the exact request, the full response,
                           the latency, any error, and every score with its explanation
    summary.json           each metric's mean and pass rate (separately — there is no blended
                           score), blocking failures, environment errors, and provenance:
                           corpus version, benchmark hash, models, git commit
    report.md              a metrics table, the comparison across this run's variants, and a
                           failure inventory (answer excerpt + the failed scorer's detail)
    review_worksheet.md    the subjective cases, for a person to judge (`review.py`)

How a case is run. The case becomes an `AskRequest` (question, mode, team, priorities,
previous priorities, constraints, history) with the variant in `options`, and goes through
`AssistantService.ask` — the API's own request bounding (timeout, provider-error mapping, the
one event loop the Ollama clients need) minus HTTP. The service gets a private settings
copy with the feature and dev options switched on and tracing off; the process settings are
never touched. Cases run one at a time, as a single local model can only answer one at a time.

Two modes:

* `live`: the real chat model and embeddings on the local Ollama. This is the quality signal.
* `fake`: a deterministic extractive fake chat model and hash-based fake embeddings
  (`fake_model.py`). It exercises every line of the harness offline in seconds and is NOT a
  quality signal; its reports say so first. In fake mode only a crash fails the run.

Two kinds of failure, never mixed:

* an **application failure** is Ask Pivot's fault: a wrong answer (a failed scorer) or a
  crash inside the pipeline. It is scored, and a crash fails every check for its case.
* an **environment failure** means the answer was never produced: Ollama is down, a model
  is not pulled, the provider timed out. Nothing is scored for that case — there is no
  answer to judge — and it is listed separately. Note `provider_unavailable` also covers an
  empty reply from the model (the pipeline raises it); it is listed by message.

Exit codes: 0 ok · 1 blocking failures (live) or a crash (fake) · 2 environment error.

`run_retrieval_sweep` measures retrieval alone — recall@k and hit@k for dense, lexical and
hybrid retrieval at several k, with no language-model call — over the benchmark's retrieval
expectations and the held-out questions in `retrieval_holdout.json` (written blind to the
retriever, so it guards against tuning to the benchmark). `run_selftest` runs every scorer
against hand-written answers with a known verdict and needs neither Ollama nor LangChain.
"""

from __future__ import annotations

import asyncio
import json
import platform
import statistics
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol, get_args

from app.assistant_rag import DATA_MODE
from app.assistant_rag.errors import AssistantError
from app.assistant_rag.evaluation import review, scorers
from app.assistant_rag.evaluation.dataset import Benchmark, BenchmarkCase
from app.assistant_rag.evaluation.scorers import ScoreResult
from app.assistant_rag.manifest import Manifest
from app.assistant_rag.models import AskOptionsIn, AskRequest, PromptVariant, RetrievalVariant

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.assistant_rag.corpus import Corpus
    from app.assistant_rag.retrieval import HybridRetriever
    from app.assistant_rag.types import RetrievalResult
    from app.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[3]
REPO_DIR = BACKEND_DIR.parent
DEFAULT_OUTPUT_DIR = BACKEND_DIR / ".assistant_eval"
HOLDOUT_PATH = Path(__file__).resolve().parent / "retrieval_holdout.json"

EXIT_OK, EXIT_BLOCKING, EXIT_ENVIRONMENT = 0, 1, 2
MODES: tuple[str, ...] = ("live", "fake")
PROMPT_VARIANTS: tuple[str, ...] = get_args(PromptVariant)
RETRIEVAL_VARIANTS: tuple[str, ...] = get_args(RetrievalVariant)
DEFAULT_VARIANTS = "grounded:hybrid,basic:hybrid"
SWEEP_VARIANTS: tuple[str, ...] = ("dense", "lexical", "hybrid")
SWEEP_KS: tuple[int, ...] = (3, 5, 6, 8)
SWEEP_EMBEDDINGS: tuple[str, ...] = ("live", "fake", "none")

OK, APPLICATION_ERROR, ENVIRONMENT_ERROR = "ok", "application_error", "environment_error"
#: Error codes that mean "no answer was produced, through no fault of the pipeline's logic".
ENVIRONMENT_CODES = frozenset(
    {"provider_unavailable", "model_unavailable", "provider_timeout", "assistant_dependencies_missing", "assistant_busy"}
)

FAKE_NOTE = (
    "FAKE MODEL RUN — a deterministic extractive fake chat model and hash-based fake embeddings "
    "exercised the harness offline. These scores are NOT a quality signal: do not compare "
    "variants or judge Ask Pivot with them."
)
LIVE_NOTE = (
    "Live run on the local Ollama models. Deterministic scorers only: each metric stands on its "
    "own (there is no overall score), and recommendation quality is not scored here — see "
    "review_worksheet.md."
)


# ------------------------------------------------------------------------------ variants


@dataclass(frozen=True, order=True)
class Variant:
    """One experiment configuration: which prompt, which retrieval."""

    prompt_variant: str
    retrieval_variant: str

    def __post_init__(self) -> None:
        if self.prompt_variant not in PROMPT_VARIANTS:
            raise ValueError(f"unknown prompt variant {self.prompt_variant!r}; expected one of {PROMPT_VARIANTS}")
        if self.retrieval_variant not in RETRIEVAL_VARIANTS:
            raise ValueError(f"unknown retrieval variant {self.retrieval_variant!r}; expected one of {RETRIEVAL_VARIANTS}")

    @property
    def label(self) -> str:
        return f"{self.prompt_variant}:{self.retrieval_variant}"

    @property
    def name(self) -> str:
        """Directory-safe form: `grounded-hybrid`."""
        return f"{self.prompt_variant}-{self.retrieval_variant}"

    @classmethod
    def parse(cls, text: str) -> Variant:
        prompt, sep, retrieval = text.strip().partition(":")
        if not sep:
            raise ValueError(f"variant {text!r} must be prompt:retrieval, e.g. grounded:hybrid")
        return cls(prompt.strip(), retrieval.strip())


def parse_variants(spec: str | Iterable[Variant | tuple[str, str] | str]) -> list[Variant]:
    """`"grounded:hybrid,basic:hybrid"`, or a sequence of `Variant`s / `(prompt, retrieval)`
    tuples / strings. Duplicates are dropped, order kept."""
    items: Iterable[Any] = [s for s in spec.split(",") if s.strip()] if isinstance(spec, str) else spec
    out: list[Variant] = []
    for item in items:
        if isinstance(item, Variant):
            variant = item
        elif isinstance(item, str):
            variant = Variant.parse(item)
        else:
            variant = Variant(*item)
        if variant not in out:
            out.append(variant)
    if not out:
        raise ValueError("at least one variant is required")
    return out


def build_request(case: BenchmarkCase, variant: Variant, k: int | None = None) -> AskRequest:
    """The case as the exact request the pipeline receives, with the variant as dev options."""
    options = AskOptionsIn(prompt_variant=variant.prompt_variant, retrieval_variant=variant.retrieval_variant, k=k)  # type: ignore[arg-type]
    return AskRequest(**case.request_payload(), options=options)


def select_cases(benchmark: Benchmark, case_ids: Sequence[str] | None) -> list[BenchmarkCase]:
    """Every case, or exactly the ones named (benchmark order). Unknown ids are an error."""
    if not case_ids:
        return list(benchmark.cases)
    known = {c.id for c in benchmark.cases}
    unknown = [c for c in case_ids if c not in known]
    if unknown:
        raise ValueError(f"unknown case id(s) {unknown}; see benchmark.json for the {len(known)} case ids")
    wanted = set(case_ids)
    return [c for c in benchmark.cases if c.id in wanted]


# -------------------------------------------------------------------------- provenance


def _git(*args: str) -> str | None:
    """Read-only git (`rev-parse`, `status`); None when git or the repository is unavailable."""
    try:
        done = subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=REPO_DIR, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _package_version(name: str) -> str | None:
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return None


def provenance(benchmark: Benchmark, *, chat_model: str | None, embedding_model: str | None) -> dict[str, Any]:
    """Everything a score must carry to be traced back to what produced it."""
    status = _git("status", "--porcelain")
    return {
        "benchmark_id": benchmark.id,
        "benchmark_version": benchmark.version,
        "benchmark_hash": benchmark.content_hash,
        "corpus_version": benchmark.corpus_version,
        "chat_model": chat_model,
        "embedding_model": embedding_model,
        "data_mode": DATA_MODE,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "python": platform.python_version(),
        "packages": {p: _package_version(p) for p in ("langchain-core", "langchain-ollama", "langsmith")},
    }


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_dir(output_dir: Path, run_id: str, suffix: str) -> Path:
    path = output_dir / f"{run_id}-{suffix}"
    n = 2
    while path.exists():
        path = output_dir / f"{run_id}-{suffix}-{n}"
        n += 1
    path.mkdir(parents=True)
    return path


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


# ------------------------------------------------------------------------- answering


class Answerer(Protocol):
    """What the runner calls to answer one request (the service, or a test double)."""

    chat_model: str | None
    embedding_model: str | None

    async def ask(self, request: AskRequest, request_id: str) -> Any: ...


def eval_settings(settings: Settings) -> Settings:
    """A private copy: feature and dev options on (variants travel in `options`), tracing
    off (a local run never uploads; `hosted` is the LangSmith path)."""
    return settings.model_copy(update={"assistant_enabled": True, "assistant_dev_options": True, "langsmith_tracing": False})


class ServiceAnswerer:
    """Ask through `AssistantService`: the API's bounding without HTTP (module doc)."""

    def __init__(self, settings: Settings, *, mode: str) -> None:
        from app.assistant_rag.service import AssistantService

        self.settings = eval_settings(settings)
        if mode == "fake":
            from app.assistant_rag.evaluation import fake_model

            self.chat_model: str | None = fake_model.FAKE_CHAT_MODEL_NAME
            self.embedding_model: str | None = "DeterministicFakeEmbedding"
            self.service = AssistantService(
                self.settings,
                embeddings_factory=lambda s: fake_model.build_fake_embeddings(),
                chat_model_factory=lambda s: fake_model.build_fake_chat_model(),
            )
        else:
            self.chat_model = self.settings.assistant_chat_model
            self.embedding_model = self.settings.assistant_embedding_model
            self.service = AssistantService(self.settings)

    async def ask(self, request: AskRequest, request_id: str) -> Any:
        return await self.service.ask(request, request_id=request_id)


async def preflight(settings: Settings, mode: str) -> str | None:
    """Why the run cannot start, or None. Live: Ollama up and both models pulled (one cheap
    probe instead of N identical failures). Fake: the LangChain extra is installed."""
    from app.assistant_rag import providers

    deps = providers.dependency_status()
    if mode == "fake":
        if "langchain_core" in deps.missing:
            return f"fake mode runs the real LangChain chain and needs langchain-core. Install with: {providers.INSTALL_HINT}"
        return None
    if not deps.installed:
        return f"missing packages {deps.missing}. Install with: {deps.install_hint}"
    status = await providers.probe_ollama(settings)
    if not status.reachable:
        return status.error or f"Ollama is not reachable at {settings.ollama_base_url}. Start it with 'ollama serve'."
    for model, available in ((status.chat_model, status.chat_model_available), (status.embedding_model, status.embedding_model_available)):
        if not available:
            return f"Ollama does not have the model '{model}'. Pull it with: ollama pull {model}"
    return None


def classify_error(code: str) -> str:
    return ENVIRONMENT_ERROR if code in ENVIRONMENT_CODES else APPLICATION_ERROR


def _error_dict(exc: BaseException) -> dict[str, Any]:
    cause = exc.__cause__ or exc.__context__
    if isinstance(exc, AssistantError):
        return {
            "code": exc.code,
            "message": exc.message,
            "status": exc.status_code,
            "cause": f"{type(cause).__name__}: {cause}" if cause else None,
        }
    return {"code": "unexpected_exception", "message": f"{type(exc).__name__}: {exc}", "status": None, "cause": None}


def _as_json_output(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return dict(response.model_dump(mode="json"))
    return dict(response)


@dataclass
class CaseRecord:
    """One case under one variant: request, outcome, scores."""

    case: BenchmarkCase
    variant: Variant
    request: dict[str, Any]
    status: str
    latency_ms: float
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    scores: list[ScoreResult] = field(default_factory=list)
    #: Scores of the model's raw text, when the answer guard changed it (see `raw_output`).
    raw_scores: list[ScoreResult] = field(default_factory=list)

    @property
    def scored(self) -> bool:
        return self.status != ENVIRONMENT_ERROR

    def output(self) -> dict[str, Any]:
        """What the scorers read: the response plus the runner's wall-clock latency, or the
        error code (which `score_case` turns into explicit "not scored" failures)."""
        if self.response is not None:
            return {**self.response, "latency_ms": self.latency_ms}
        return {"error": (self.error or {}).get("code") or "error", "latency_ms": self.latency_ms}

    def raw_output(self) -> dict[str, Any] | None:
        """The output as the model wrote it, before the answer guard removed anything — so a
        run can report the model's behaviour and the system's separately. None when the
        guard changed nothing (the two are then identical)."""
        if self.response is None:
            return None
        raw = (self.response.get("diagnostics") or {}).get("raw_answer")
        if not raw:
            return None
        # citations re-read from the raw text (the reported lists describe the guarded text)
        return {**self.response, "answer": raw, "cited_source_ids": [], "invalid_citations": [], "latency_ms": self.latency_ms}

    def guard_codes(self) -> list[str]:
        if self.response is None:
            return []
        return [r.get("code", "") for r in ((self.response.get("guard") or {}).get("removals") or [])]

    def failed(self) -> list[ScoreResult]:
        return [s for s in self.scores if s.passed is False]

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case.id,
            "variant": self.variant.label,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 1),
            "case": dict(self.case.raw),
            "request": self.request,
            "response": self.response,
            "error": self.error,
            "scores": [s.as_dict() for s in self.scores],
            "raw_scores": [s.as_dict() for s in self.raw_scores],
        }


async def answer_case(answerer: Answerer, case: BenchmarkCase, variant: Variant, *, k: int | None) -> CaseRecord:
    """Run one case. Errors are recorded and classified, never raised (one bad case must not
    end a 40-minute run); only cancellation propagates."""
    request = build_request(case, variant, k)
    payload = request.model_dump(mode="json")
    started = perf_counter()
    try:
        response = await answerer.ask(request, f"eval-{variant.name}-{case.id}")
    except AssistantError as exc:
        ms = (perf_counter() - started) * 1000.0
        return CaseRecord(case, variant, payload, classify_error(exc.code), ms, error=_error_dict(exc))
    except Exception as exc:  # noqa: BLE001 - a crash is an application failure to record
        ms = (perf_counter() - started) * 1000.0
        return CaseRecord(case, variant, payload, APPLICATION_ERROR, ms, error=_error_dict(exc))
    ms = (perf_counter() - started) * 1000.0
    return CaseRecord(case, variant, payload, OK, ms, response=_as_json_output(response))


# ---------------------------------------------------------------------------- scoring


def score_records(records: Sequence[CaseRecord], benchmark: Benchmark, manifest: Manifest) -> list[dict[str, Any]]:
    """Score every answered case, then each priority-revision pair (the pair result is added
    to the revised case's scores). Returns one row per pair touched by this run."""
    for record in records:
        record.scores = scorers.score_case(record.case, record.output(), manifest) if record.scored else []
        raw = record.raw_output() if record.scored else None
        record.raw_scores = scorers.score_case(record.case, raw, manifest) if raw is not None else list(record.scores)
    by_id = {r.case.id: r for r in records}
    pairs: list[dict[str, Any]] = []
    for initial, revised in benchmark.pairs():
        a, b = by_id.get(initial.id), by_id.get(revised.id)
        row: dict[str, Any] = {"initial": initial.id, "revised": revised.id}
        if a is None and b is None:
            continue
        if a is None or b is None:
            pairs.append({**row, "status": "skipped", "detail": "only one side of the pair was in this run"})
            continue
        if not a.scored or not b.scored:
            pairs.append({**row, "status": "skipped", "detail": "an environment error left one side unanswered"})
            continue
        result = scorers.score_pair(initial, a.output(), revised, b.output(), manifest)
        b.scores.append(result)
        pairs.append({**row, "status": "scored", "score": result.score, "passed": result.passed, "detail": result.detail})
    return pairs


def _latency_stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p90": None, "max": None, "total": 0.0}
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]
    return {
        "mean": round(statistics.fmean(ordered), 1),
        "p50": round(statistics.median(ordered), 1),
        "p90": round(p90, 1),
        "max": round(ordered[-1], 1),
        "total": round(sum(ordered), 1),
    }


def variant_exit_code(mode: str, *, environment_errors: int, application_errors: int, blocking_failures: int) -> int:
    if environment_errors:
        return EXIT_ENVIRONMENT
    if mode == "fake":  # fake answers fail checks by design: only a crash means the harness broke
        return EXIT_BLOCKING if application_errors else EXIT_OK
    return EXIT_BLOCKING if blocking_failures else EXIT_OK


def build_summary(
    records: Sequence[CaseRecord],
    pairs: Sequence[Mapping[str, Any]],
    *,
    variant: Variant,
    mode: str,
    run_id: str,
    provenance_info: Mapping[str, Any],
    k: int | None,
    case_filter: Sequence[str] | None,
    wall_seconds: float,
) -> dict[str, Any]:
    """`summary.json`: per-metric means and pass rates (each on its own), failures, errors."""
    summary = scorers.summarize(s for r in records for s in r.scores)
    pre_guard = scorers.summarize(s for r in records for s in r.raw_scores)
    env = [r for r in records if r.status == ENVIRONMENT_ERROR]
    app = [r for r in records if r.status == APPLICATION_ERROR]
    blocking = [{"case_id": s.case_id, "key": s.key, "detail": s.detail} for s in summary.blocking_failures]

    def errors(rows: Sequence[CaseRecord]) -> list[dict[str, Any]]:
        return [{"case_id": r.case.id, **(r.error or {})} for r in rows]

    return {
        "run_id": run_id,
        "variant": {"label": variant.label, "prompt_variant": variant.prompt_variant, "retrieval_variant": variant.retrieval_variant},
        "mode": mode,
        "quality_signal": mode == "live",
        "note": FAKE_NOTE if mode == "fake" else LIVE_NOTE,
        "options": {"k": k, "cases": list(case_filter) if case_filter else None},
        "provenance": dict(provenance_info),
        "cases": {
            "total": len(records),
            "scored": sum(1 for r in records if r.scored),
            "ok": sum(1 for r in records if r.status == OK),
            "application_errors": len(app),
            "environment_errors": len(env),
        },
        "metrics": {key: m.as_dict() for key, m in summary.metrics.items()},
        # The same metrics on the model's raw text, before the answer guard: the guard must
        # never make the model look better than it is, so both are always reported.
        "pre_guard_metrics": {key: m.as_dict() for key, m in pre_guard.metrics.items()},
        "pre_guard_blocking_failures": len(pre_guard.blocking_failures),
        "guard_interventions": dict(Counter(code for r in records for code in r.guard_codes())),
        "answers_changed_by_guard": sum(1 for r in records if r.guard_codes()),
        "failures_by_severity": summary.failures_by_severity,
        "blocking_failures": blocking,
        "pairs": list(pairs),
        "application_errors": errors(app),
        "environment_errors": errors(env),
        "latency_ms": _latency_stats([r.latency_ms for r in records if r.scored]),
        "wall_seconds": round(wall_seconds, 1),
        "exit_code": variant_exit_code(mode, environment_errors=len(env), application_errors=len(app), blocking_failures=len(blocking)),
    }


# ---------------------------------------------------------------------------- reports

_METRIC_ORDER = (*scorers.SCORERS, "objective_sensitivity")


def _ordered_metrics(keys: Iterable[str]) -> list[str]:
    keys = set(keys)
    return [k for k in _METRIC_ORDER if k in keys] + sorted(keys - set(_METRIC_ORDER))


def _fmt_mean(key: str, mean: float | None) -> str:
    if mean is None:
        return "—"
    return f"{mean:.0f} ms" if key == "latency_ms" else f"{mean:.2f}"


def _fmt_rate(metric: Mapping[str, Any]) -> str:
    rate = metric.get("pass_rate")
    if rate is None:
        return "—"
    return f"{rate:.0%} ({metric['passed']}/{metric['passed'] + metric['failed']})"


def _one_line(text: str, limit: int = 320) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def metrics_table(summary: Mapping[str, Any]) -> list[str]:
    metrics = summary["metrics"]
    lines = ["| metric | mean | pass rate | scored | failed |", "|---|---|---|---|---|"]
    for key in _ordered_metrics(metrics):
        m = metrics[key]
        lines.append(f"| {key} | {_fmt_mean(key, m['mean'])} | {_fmt_rate(m)} | {m['applicable']} | {m['failed']} |")
    return lines


def comparison_table(summaries: Sequence[Mapping[str, Any]]) -> list[str]:
    """Rows = metrics, columns = variants; each cell `mean · pass rate`."""
    labels = [s["variant"]["label"] for s in summaries]
    lines = ["| | " + " | ".join(f"`{label}`" for label in labels) + " |", "|---|" + "---|" * len(labels)]
    rows: list[tuple[str, Callable[[Mapping[str, Any]], str]]] = [
        ("cases scored / total", lambda s: f"{s['cases']['scored']}/{s['cases']['total']}"),
        ("application errors", lambda s: str(s["cases"]["application_errors"])),
        ("environment errors", lambda s: str(s["cases"]["environment_errors"])),
        ("blocking failures", lambda s: str(len(s["blocking_failures"]))),
        ("major failures", lambda s: str(s["failures_by_severity"].get("major", 0))),
        ("mean latency", lambda s: _fmt_mean("latency_ms", s["latency_ms"]["mean"])),
    ]
    for name, cell in rows:
        lines.append(f"| {name} | " + " | ".join(cell(s) for s in summaries) + " |")
    keys = _ordered_metrics({k for s in summaries for k in s["metrics"]} - {"latency_ms"})
    for key in keys:
        cells = []
        for s in summaries:
            m = s["metrics"].get(key)
            cells.append("—" if m is None else f"{_fmt_mean(key, m['mean'])} · {_fmt_rate(m)}")
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    return lines


def failure_inventory(records: Sequence[CaseRecord]) -> list[str]:
    lines: list[str] = []
    failed = [r for r in records if r.failed()]
    if not failed:
        return ["No scorer failed."]
    for record in failed:
        case = record.case
        lines.append(f"### {case.id} — {case.category}, {case.severity}")
        lines.append("")
        lines.append(f"Q: {_one_line(case.question, 200)}")
        if record.response is not None:
            lines.append(f"> {_one_line(str(record.response.get('answer') or '(empty)'))}")
            lines.append(f"> — answer_kind `{record.response.get('answer_kind')}`, cited {record.response.get('cited_source_ids') or []}")
        else:
            error = record.error or {}
            lines.append(f"> no answer: `{error.get('code')}` — {_one_line(str(error.get('message')), 200)}")
        lines.append("")
        for s in record.failed():
            lines.append(f"- **{s.key}** ({s.severity}): {_one_line(s.detail, 400)}")
        lines.append("")
    return lines


def render_report(summary: Mapping[str, Any], records: Sequence[CaseRecord], comparison: Sequence[Mapping[str, Any]]) -> str:
    p = summary["provenance"]
    commit = (p.get("git_commit") or "unknown")[:12] + (" (uncommitted changes)" if p.get("git_dirty") else "")
    cases = summary["cases"]
    lines = [
        f"# Ask Pivot evaluation — `{summary['variant']['label']}` ({summary['mode']})",
        "",
        f"**{summary['note']}**",
        "",
        f"- run `{summary['run_id']}` · benchmark `{p['benchmark_id']}` v{p['benchmark_version']} "
        f"(hash `{p['benchmark_hash'][:12]}`) · corpus `{p['corpus_version']}` · data `{p['data_mode']}`",
        f"- chat model `{p.get('chat_model')}` · embedding model `{p.get('embedding_model')}` · git `{commit}`",
        f"- k = {summary['options']['k'] or 'default'} · cases: {', '.join(summary['options']['cases'] or []) or 'all'}"
        f" · wall time {summary['wall_seconds']:.1f} s · exit code {summary['exit_code']}",
        "",
        "## Outcome",
        "",
        f"{cases['scored']}/{cases['total']} cases scored ({cases['ok']} answered, {cases['application_errors']} "
        f"application errors, {cases['environment_errors']} environment errors). Blocking failures: "
        f"{len(summary['blocking_failures'])}; failures by severity: "
        + ", ".join(f"{k} {v}" for k, v in summary["failures_by_severity"].items())
        + ".",
        "",
        "## Metrics",
        "",
        "Each metric is computed on its own: `mean` over the cases where it applies, `pass rate` over "
        "its pass/fail verdicts. There is deliberately no overall score — averaging citation validity "
        "with fact correctness is how V1 hid a fabrication.",
        "",
        *metrics_table(summary),
        "",
    ]
    lines += [
        "## Before and after the answer guard",
        "",
        f"The guard changed {summary.get('answers_changed_by_guard', 0)} of {cases['ok']} answers "
        f"(removals by reason: {', '.join(f'{k} {v}' for k, v in (summary.get('guard_interventions') or {}).items()) or 'none'}). "
        f"Blocking failures on the model's raw text: {summary.get('pre_guard_blocking_failures', 0)}; "
        f"after the guard: {len(summary['blocking_failures'])}. The table above scores what the GM sees; "
        "this one scores what the model wrote.",
        "",
        *metrics_table({**summary, "metrics": summary.get("pre_guard_metrics") or {}}),
        "",
    ]
    if summary["pairs"]:
        lines += ["## Priority-revision pairs (objective_sensitivity)", ""]
        for pair in summary["pairs"]:
            verdict = "PASS" if pair.get("passed") else ("FAIL" if pair.get("status") == "scored" else "skipped")
            lines.append(f"- {pair['initial']} -> {pair['revised']}: **{verdict}** — {_one_line(pair['detail'], 400)}")
        lines.append("")
    lines += ["## Variant comparison (this run)", "", *comparison_table(comparison), ""]
    lines += ["## Failure inventory", "", *failure_inventory(records)]
    if summary["environment_errors"]:
        lines += ["## Environment errors (not scored)", ""]
        lines += [f"- {e['case_id']}: `{e.get('code')}` — {_one_line(str(e.get('message')), 300)}" for e in summary["environment_errors"]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------------- local runs


@dataclass
class VariantReport:
    variant: Variant
    directory: Path
    records: list[CaseRecord]
    summary: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return int(self.summary["exit_code"])


@dataclass
class LocalRunResult:
    run_id: str
    mode: str
    output_dir: Path
    reports: list[VariantReport]
    preflight_error: str | None = None

    @property
    def exit_code(self) -> int:
        if self.preflight_error:
            return EXIT_ENVIRONMENT
        return max((r.exit_code for r in self.reports), default=EXIT_OK)


def _write_variant(report: VariantReport, comparison: Sequence[Mapping[str, Any]], *, mode: str) -> None:
    directory = report.directory
    with (directory / review.RESULTS_NAME).open("w", encoding="utf-8") as handle:
        for record in report.records:
            handle.write(_dump(record.to_json()) + "\n")
    (directory / "results.partial.jsonl").unlink(missing_ok=True)
    _write_json(directory / "summary.json", report.summary)
    (directory / "report.md").write_text(render_report(report.summary, report.records, comparison), encoding="utf-8")
    p = report.summary["provenance"]
    header = {"run": report.summary["run_id"], "variant": report.variant.label, "mode": mode,
              "benchmark": p["benchmark_hash"][:12], "corpus": p["corpus_version"]}
    review.write_worksheet(directory, header=header, force=True)


def run_local(
    benchmark: Benchmark,
    *,
    variants: str | Sequence[Variant | tuple[str, str] | str],
    mode: str = "live",
    k: int | None = None,
    case_ids: Sequence[str] | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    settings: Settings | None = None,
    manifest: Manifest | None = None,
    answerer: Answerer | None = None,
    progress: Callable[[str], None] | None = None,
) -> LocalRunResult:
    """Run every selected case under every variant, sequentially; score; write reports.

    `answerer` replaces the service (tests); with it, no preflight probe runs."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if k is not None and not 1 <= k <= 12:
        raise ValueError("k must be between 1 and 12")
    chosen = parse_variants(variants)
    cases = select_cases(benchmark, case_ids)
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if manifest is None:
        from app.assistant_rag.manifest import load_manifest

        manifest = load_manifest()
    run_id = new_run_id()
    say = progress or (lambda message: None)
    return asyncio.run(_run_local(benchmark, chosen, cases, mode=mode, k=k, case_ids=case_ids, output_dir=output_dir,
                                  settings=settings, manifest=manifest, answerer=answerer, run_id=run_id, say=say))


async def _run_local(
    benchmark: Benchmark,
    variants: list[Variant],
    cases: list[BenchmarkCase],
    *,
    mode: str,
    k: int | None,
    case_ids: Sequence[str] | None,
    output_dir: Path,
    settings: Settings,
    manifest: Manifest,
    answerer: Answerer | None,
    run_id: str,
    say: Callable[[str], None],
) -> LocalRunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = LocalRunResult(run_id=run_id, mode=mode, output_dir=output_dir, reports=[])
    if answerer is None:
        problem = await preflight(settings, mode)
        if problem:
            result.preflight_error = problem
            _write_json(output_dir / f"{run_id}-preflight.json", {"run_id": run_id, "mode": mode, "environment_error": problem,
                                                                   "exit_code": EXIT_ENVIRONMENT})
            return result
        answerer = ServiceAnswerer(settings, mode=mode)
    total = len(variants) * len(cases)
    done = 0
    for variant in variants:
        directory = _run_dir(output_dir, run_id, variant.name)
        records: list[CaseRecord] = []
        started = perf_counter()
        with (directory / "results.partial.jsonl").open("w", encoding="utf-8") as partial:
            for case in cases:
                record = await answer_case(answerer, case, variant, k=k)
                records.append(record)
                partial.write(_dump(record.to_json()) + "\n")
                partial.flush()
                done += 1
                say(f"[{done}/{total}] {variant.label} {case.id}: {record.status} ({record.latency_ms / 1000:.1f} s)")
        pairs = score_records(records, benchmark, manifest)
        ok = next((r.response for r in records if r.response is not None), None)
        diagnostics = (ok or {}).get("diagnostics") or {}
        info = provenance(
            benchmark,
            chat_model=diagnostics.get("chat_model") or answerer.chat_model,
            embedding_model=diagnostics.get("embedding_model") or answerer.embedding_model,
        )
        summary = build_summary(records, pairs, variant=variant, mode=mode, run_id=run_id, provenance_info=info, k=k,
                                case_filter=case_ids, wall_seconds=perf_counter() - started)
        report = VariantReport(variant, directory, records, summary)
        result.reports.append(report)
        _write_variant(report, [r.summary for r in result.reports], mode=mode)
    # Rewrite each report so its comparison table covers every variant of the run.
    for report in result.reports:
        (report.directory / "report.md").write_text(
            render_report(report.summary, report.records, [r.summary for r in result.reports]), encoding="utf-8"
        )
    return result


# ------------------------------------------------------------------------ latest run


def latest_run_dirs(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    """The variant directories of the most recent local run (newest run id)."""
    if not output_dir.is_dir():
        return []
    runs = sorted(p for p in output_dir.iterdir() if p.is_dir() and (p / review.RESULTS_NAME).exists())
    if not runs:
        return []
    newest = runs[-1].name.split("-", 1)[0]
    return [p for p in runs if p.name.split("-", 1)[0] == newest]


# --------------------------------------------------------------------- retrieval sweep


@dataclass(frozen=True)
class HoldoutQuestion:
    id: str
    question: str
    mode: str
    team_id: str | None
    style: str
    required: tuple[str, ...]
    acceptable: tuple[str, ...]
    out_of_scope: bool


def load_holdout(path: Path, manifest: Manifest) -> tuple[HoldoutQuestion, ...]:
    """The blind retrieval holdout. Source ids are checked against the manifest so a typo
    cannot quietly turn a check off."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for item in raw.get("questions") or []:
        required, acceptable = tuple(item.get("required_sources") or ()), tuple(item.get("acceptable_sources") or ())
        unknown = [s for s in (*required, *acceptable) if s not in manifest.source_ids]
        if unknown:
            raise ValueError(f"holdout {item.get('id')}: unknown source id(s) {unknown}")
        out.append(HoldoutQuestion(str(item["id"]), str(item["question"]), str(item.get("mode") or "research"),
                                   item.get("team_id"), str(item.get("style") or ""), required, acceptable,
                                   bool(item.get("out_of_scope"))))
    return tuple(out)


@dataclass
class RetrievalSweepResult:
    run_id: str
    directory: Path
    embeddings: str
    embedding_model: str | None
    report: dict[str, Any]
    environment_error: str | None = None

    @property
    def exit_code(self) -> int:
        return EXIT_ENVIRONMENT if self.environment_error else EXIT_OK


def _sweep_embeddings(kind: str, settings: Settings) -> Any:
    if kind == "none":
        return None
    if kind == "fake":
        from app.assistant_rag.evaluation.fake_model import build_fake_embeddings

        return build_fake_embeddings()
    from app.assistant_rag.embeddings import build_embeddings

    return build_embeddings(settings)


def _retrieve(retriever: HybridRetriever, request: AskRequest, settings: Settings, manifest: Manifest) -> RetrievalResult:
    """Retrieval exactly as the pipeline's first four stages do it (query context, entity
    resolution, expansion, pinning, diversification), with nothing after it."""
    from app.assistant_rag.entities import resolve_entities
    from app.assistant_rag.pipeline import make_query_context

    ctx = make_query_context(request, settings, None, retrieval_config=retriever.config)
    entities = resolve_entities(ctx.question, ctx.history, ctx.team_id, manifest)
    return retriever.retrieve(ctx, entities)


def _with_k(case: BenchmarkCase, k: int) -> BenchmarkCase:
    expect = case.expect.retrieval
    assert expect is not None
    return replace(case, expect=replace(case.expect, retrieval=replace(expect, k=k)))


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def run_retrieval_sweep(
    benchmark: Benchmark,
    *,
    variants: Sequence[str] = SWEEP_VARIANTS,
    ks: Sequence[int] = SWEEP_KS,
    embeddings: str = "live",
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    settings: Settings | None = None,
    corpus: Corpus | None = None,
    holdout_path: Path | None = HOLDOUT_PATH,
    progress: Callable[[str], None] | None = None,
) -> RetrievalSweepResult:
    """recall@k / hit@k per retrieval variant and k — retrieval only, no language model.

    `embeddings`: "live" (the configured Ollama model), "fake" (hash vectors: dense numbers
    are meaningless) or "none" (dense is skipped; lexical and hybrid run as keyword + entity)."""
    from app.assistant_rag import providers
    from app.assistant_rag.retrieval import HybridRetriever

    if embeddings not in SWEEP_EMBEDDINGS:
        raise ValueError(f"embeddings must be one of {SWEEP_EMBEDDINGS}")
    bad = [v for v in variants if v not in RETRIEVAL_VARIANTS]
    if bad or not variants:
        raise ValueError(f"unknown retrieval variant(s) {bad}; expected some of {RETRIEVAL_VARIANTS}")
    if not ks or any(not isinstance(k, int) or not 1 <= k <= 12 for k in ks):
        raise ValueError("every k must be an integer between 1 and 12")
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if corpus is None:
        from app.assistant_rag.corpus import load_corpus

        corpus = load_corpus()
    manifest = corpus.manifest
    say = progress or (lambda message: None)
    run_id = new_run_id()
    output_dir.mkdir(parents=True, exist_ok=True)
    directory = _run_dir(output_dir, run_id, "retrieval")
    started = perf_counter()

    report: dict[str, Any] = {
        "run_id": run_id,
        "embeddings": embeddings,
        "quality_signal": embeddings == "live",
        "variants": list(variants),
        "ks": list(ks),
        "skipped": [],
        "aggregates": {},
        "cases": [],
        "holdout": None,
        "environment_error": None,
    }
    embedder = None
    try:
        embedder = _sweep_embeddings(embeddings, settings)
        retriever = HybridRetriever(corpus, embedder)
        retriever.build()
    except Exception as exc:  # noqa: BLE001 - reported as an environment error
        mapped = providers.map_provider_error(exc, settings=settings, model=settings.assistant_embedding_model)
        message = mapped.message if mapped else f"the retrieval index could not be built: {type(exc).__name__}: {exc}"
        return _finish_sweep(report, directory, benchmark, corpus, None, embeddings, started, error=message)
    embedding_model = retriever.embedding_model
    report["config"] = asdict(retriever.config)
    cases = [c for c in benchmark.cases if c.expect.retrieval is not None]
    holdout = load_holdout(holdout_path, manifest) if holdout_path is not None and holdout_path.exists() else ()
    holdout_rows: list[dict[str, Any]] = []
    try:
        for variant in variants:
            if variant == "dense" and embedder is None:
                report["skipped"].append("dense: no embedding model (run with live or fake embeddings)")
                continue
            report["aggregates"][variant] = {}
            for k in ks:
                rows: list[dict[str, Any]] = []
                for case in cases:
                    request = AskRequest(**case.request_payload(), options=AskOptionsIn(retrieval_variant=variant, k=k))  # type: ignore[arg-type]
                    result = _retrieve(retriever, request, settings, manifest)
                    output = {"sources": [{"source_id": i.chunk.source_id, "retrieval": {"rank": i.rank}} for i in result.selected]}
                    scored = _with_k(case, k)
                    recall = scorers.retrieval_recall_at_k(scored, output, manifest)
                    hit = scorers.retrieval_hit(scored, output, manifest)
                    rows.append({"case_id": case.id, "variant": variant, "k": k, "recall": recall.score, "hit": hit.passed,
                                 "retrieved": scorers.retrieved_source_ids(output), "missing": list(recall.data.get("missing", []))})
                report["cases"] += rows
                recalls = [float(r["recall"]) for r in rows if r["recall"] is not None]
                report["aggregates"][variant][str(k)] = {
                    "cases": len(rows),
                    "recall": _mean(recalls),
                    "hit_rate": _mean([1.0 if r["hit"] else 0.0 for r in rows]),
                    "full_recall_rate": _mean([1.0 if r == 1.0 else 0.0 for r in recalls]),
                }
                for question in holdout:
                    request = AskRequest(question=question.question, mode=question.mode, team_id=question.team_id,  # type: ignore[arg-type]
                                         options=AskOptionsIn(retrieval_variant=variant, k=k))  # type: ignore[arg-type]
                    result = _retrieve(retriever, request, settings, manifest)
                    got = set(result.source_ids)
                    holdout_rows.append({
                        "id": question.id, "variant": variant, "k": k, "style": question.style,
                        "out_of_scope": question.out_of_scope, "low_evidence": result.low_evidence,
                        "required_recall": (len(got & set(question.required)) / len(question.required)) if question.required else None,
                        "hit": bool(got & (set(question.required) | set(question.acceptable))),
                        "retrieved": list(result.source_ids),
                    })
                say(f"retrieval {variant} k={k}: recall {report['aggregates'][variant][str(k)]['recall']}")
    except Exception as exc:  # noqa: BLE001 - e.g. Ollama stopped mid-sweep
        mapped = providers.map_provider_error(exc, settings=settings, model=settings.assistant_embedding_model)
        message = mapped.message if mapped else f"retrieval failed: {type(exc).__name__}: {exc}"
        return _finish_sweep(report, directory, benchmark, corpus, embedding_model, embeddings, started, error=message)
    if holdout:
        report["holdout"] = _holdout_aggregates(holdout, holdout_rows)
    return _finish_sweep(report, directory, benchmark, corpus, embedding_model, embeddings, started)


def _holdout_aggregates(holdout: Sequence[HoldoutQuestion], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    aggregates: dict[str, dict[str, Any]] = {}
    for row in rows:
        aggregates.setdefault(row["variant"], {}).setdefault(str(row["k"]), []).append(row)
    out: dict[str, Any] = {"questions": len(holdout), "in_scope": sum(not q.out_of_scope for q in holdout),
                           "out_of_scope": sum(q.out_of_scope for q in holdout), "aggregates": {}, "rows": list(rows)}
    for variant, by_k in aggregates.items():
        out["aggregates"][variant] = {}
        for k, items in by_k.items():
            inside = [r for r in items if not r["out_of_scope"]]
            outside = [r for r in items if r["out_of_scope"]]
            out["aggregates"][variant][k] = {
                "required_recall": _mean([float(r["required_recall"]) for r in inside if r["required_recall"] is not None]),
                "hit_rate": _mean([1.0 if r["hit"] else 0.0 for r in inside]),
                "out_of_scope_flagged": _mean([1.0 if r["low_evidence"] else 0.0 for r in outside]),
                "in_scope_wrongly_flagged": _mean([1.0 if r["low_evidence"] else 0.0 for r in inside]),
            }
    return out


def _finish_sweep(
    report: dict[str, Any],
    directory: Path,
    benchmark: Benchmark,
    corpus: Corpus,
    embedding_model: str | None,
    embeddings: str,
    started: float,
    *,
    error: str | None = None,
) -> RetrievalSweepResult:
    report["environment_error"] = error
    report["embedding_model"] = embedding_model
    report["chunks"] = len(corpus.chunks)
    report["provenance"] = provenance(benchmark, chat_model=None, embedding_model=embedding_model)
    report["wall_seconds"] = round(perf_counter() - started, 1)
    report["exit_code"] = EXIT_ENVIRONMENT if error else EXIT_OK
    _write_json(directory / "retrieval.json", report)
    (directory / "retrieval.md").write_text(render_retrieval_report(report), encoding="utf-8")
    return RetrievalSweepResult(report["run_id"], directory, embeddings, embedding_model, report, error)


def _k_table(aggregates: Mapping[str, Mapping[str, Mapping[str, Any]]], ks: Sequence[int], metric: str) -> list[str]:
    lines = ["| variant | " + " | ".join(f"k={k}" for k in ks) + " |", "|---|" + "---|" * len(ks)]
    for variant, by_k in aggregates.items():
        cells = []
        for k in ks:
            value = (by_k.get(str(k)) or {}).get(metric)
            cells.append("—" if value is None else f"{value:.2f}")
        lines.append(f"| {variant} | " + " | ".join(cells) + " |")
    return lines


def render_retrieval_report(report: Mapping[str, Any]) -> str:
    p = report["provenance"]
    ks = report["ks"]
    n_cases = len({r["case_id"] for r in report["cases"]})
    lines = ["# Retrieval sweep — recall@k and hit@k (no language-model calls)", ""]
    if report["embeddings"] == "fake":
        lines += ["**FAKE EMBEDDINGS — dense rankings come from hash vectors and mean nothing. Lexical numbers are real.**", ""]
    elif report["embeddings"] == "none":
        lines += ["**No embedding model — dense was skipped; hybrid ran as keyword + entity search.**", ""]
    lines += [
        f"- run `{report['run_id']}` · embeddings `{report['embeddings']}` (model `{report.get('embedding_model') or 'none'}`) · "
        f"corpus `{p['corpus_version']}` ({report.get('chunks')} chunks) · benchmark hash `{p['benchmark_hash'][:12]}`",
        f"- git `{(p.get('git_commit') or 'unknown')[:12]}` · wall time {report['wall_seconds']:.1f} s",
        "- retrieval config: " + ", ".join(f"{k}={v}" for k, v in (report.get("config") or {}).items()),
        "",
    ]
    if report["environment_error"]:
        lines += [f"**Environment error:** {report['environment_error']}", ""]
    if report["skipped"]:
        lines += [f"- skipped {note}" for note in report["skipped"]] + [""]
    lines += [
        "What is measured: each request is retrieved exactly as the pipeline's first four stages do it "
        "(query context, entity resolution, query expansion, pinning, per-source diversification), "
        "with k passages. Not included: the sandbox-limits source a refusal adds afterwards, and the "
        "prompt budget's trimming — so these numbers can differ slightly from a local run's "
        "`retrieval_recall_at_k`. recall@k = share of required source groups (an `any_of` group "
        "counts once) among the top-k passages' sources; hit@k = at least one required group.",
        "",
        f"## Benchmark retrieval expectations ({n_cases} cases)",
        "",
        "### recall@k",
        "",
        *_k_table(report["aggregates"], ks, "recall"),
        "",
        "### hit@k",
        "",
        *_k_table(report["aggregates"], ks, "hit_rate"),
        "",
        "### full recall (every required source in the top k)",
        "",
        *_k_table(report["aggregates"], ks, "full_recall_rate"),
        "",
    ]
    holdout = report.get("holdout")
    if holdout:
        lines += [
            f"## Held-out questions ({holdout['in_scope']} in scope, {holdout['out_of_scope']} out of scope)",
            "",
            "Written blind to the retriever and the benchmark, so a gap between this and the table above "
            "is a sign of tuning to the benchmark.",
            "",
            "### required-source recall@k",
            "",
            *_k_table(holdout["aggregates"], ks, "required_recall"),
            "",
            "### hit@k (a required or acceptable source)",
            "",
            *_k_table(holdout["aggregates"], ks, "hit_rate"),
            "",
            "### out-of-scope questions flagged low-evidence (higher is better; the flag does not depend on k)",
            "",
            *_k_table(holdout["aggregates"], ks, "out_of_scope_flagged"),
            "",
            "### in-scope questions wrongly flagged low-evidence (lower is better; an answerable question would be refused)",
            "",
            *_k_table(holdout["aggregates"], ks, "in_scope_wrongly_flagged"),
            "",
        ]
    default_k = 6 if 6 in ks else ks[-1]
    misses = [r for r in report["cases"] if r["k"] == default_k and r["missing"]]
    lines += [f"## Misses at k={default_k}", ""]
    if not misses:
        lines.append("None: every required source group was retrieved.")
    for row in misses:
        lines.append(f"- {row['variant']} · {row['case_id']}: missing {row['missing']} (retrieved {row['retrieved']})")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- self-test


@dataclass(frozen=True)
class SelfTestRow:
    fixture_id: str
    scorer: str
    case_id: str
    expected: str  # "pass" | "fail" | "not_applicable"
    got: str
    v1: bool
    why: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.expected == self.got

    @property
    def false_pass(self) -> bool:
        """The dangerous kind of mistake: a known-false answer passed."""
        return self.expected == "fail" and self.got == "pass"


@dataclass
class SelfTestResult:
    rows: list[SelfTestRow]
    pair_rows: list[SelfTestRow]
    reference_failures: list[tuple[str, str, str]]
    reference_cases: int

    @property
    def mismatches(self) -> list[SelfTestRow]:
        return [r for r in (*self.rows, *self.pair_rows) if not r.ok]

    @property
    def false_passes(self) -> list[SelfTestRow]:
        return [r for r in (*self.rows, *self.pair_rows) if r.false_pass]

    @property
    def exit_code(self) -> int:
        return EXIT_BLOCKING if self.mismatches or self.reference_failures else EXIT_OK


def _verdict(passed: bool | None) -> str:
    return "pass" if passed is True else "fail" if passed is False else "not_applicable"


def run_selftest(benchmark: Benchmark | None = None, manifest: Manifest | None = None) -> SelfTestResult:
    """Every scorer against `fixtures_adversarial`: answers whose verdict a human fixed in
    advance (including V1's 3/3 fabrication), plus the positive control — one good answer per
    benchmark case must pass every check its case calls for. Pure Python, no model."""
    from app.assistant_rag.evaluation import fixtures_adversarial as fx
    from app.assistant_rag.evaluation.dataset import load_benchmark
    from app.assistant_rag.manifest import load_manifest

    manifest = manifest or load_manifest()
    benchmark = benchmark or load_benchmark(manifest=manifest)
    rows = []
    for f in fx.ADVERSARIAL_FIXTURES:
        result = scorers.SCORERS[f.scorer](benchmark.case(f.case_id), f.output(manifest), manifest)
        rows.append(SelfTestRow(f.id, f.scorer, f.case_id, f.expect, _verdict(result.passed),
                                f.answer == fx.V1_ANSWER or "v1" in f.id, f.why, result.detail))
    pair_rows = []
    for p in fx.PAIR_FIXTURES:
        result = scorers.score_pair(benchmark.case(p.case_a), fx.make_output(p.answer_a, manifest=manifest),
                                    benchmark.case(p.case_b), fx.make_output(p.answer_b, manifest=manifest), manifest)
        pair_rows.append(SelfTestRow(p.id, "objective_sensitivity", p.case_b, p.expect, _verdict(result.passed), False, p.why, result.detail))
    failures: list[tuple[str, str, str]] = []
    for case in benchmark.cases:
        ref = fx.REFERENCE_ANSWERS.get(case.id)
        if ref is None:
            failures.append((case.id, "-", "no reference answer for this case"))
            continue
        output = fx.make_output(ref.answer, manifest=manifest, answer_kind=ref.answer_kind,
                                retrieved=fx.reference_retrieved(case.expect.retrieval, ref.answer), **dict(ref.extra))
        failures += [(case.id, r.key, r.detail) for r in scorers.score_case(case, output, manifest) if r.passed is False]
    return SelfTestResult(rows, pair_rows, failures, len(benchmark.cases))


def format_selftest(result: SelfTestResult) -> str:
    from app.assistant_rag.evaluation.fixtures_adversarial import V1_ANSWER

    def mark(row: SelfTestRow) -> str:
        if row.ok:
            return f"{row.got.upper()} (as expected)"
        return f"{row.got.upper()} (EXPECTED {row.expected.upper()}){'  <-- FALSE PASS' if row.false_pass else ''}"

    lines = ["Ask Pivot scorer self-test: every evaluator against answers with a known verdict", ""]
    lines += ["V1 regression — the answer V1's substring evaluator scored 3/3:", f"  {V1_ANSWER!r}"]
    for row in (r for r in result.rows if r.v1):
        lines.append(f"  {row.scorer:<24}{row.case_id:<26}{mark(row)}")
    lines.append("  (citation_validity passes on purpose: the ids are real and retrieved; validity is not correctness)")
    lines += ["", f"{'scorer':<26}{'fixtures':>9}{'true->PASS':>12}{'false->FAIL':>13}{'n/a':>5}{'mismatches':>12}"]
    by_scorer: dict[str, list[SelfTestRow]] = {}
    for row in (*result.rows, *result.pair_rows):
        by_scorer.setdefault(row.scorer, []).append(row)
    for scorer, rows in by_scorer.items():
        trues = [r for r in rows if r.expected == "pass"]
        falses = [r for r in rows if r.expected == "fail"]
        nas = [r for r in rows if r.expected == "not_applicable"]
        lines.append(
            f"{scorer:<26}{len(rows):>9}{sum(r.ok for r in trues):>8}/{len(trues):<3}{sum(r.ok for r in falses):>9}/{len(falses):<3}"
            f"{sum(r.ok for r in nas):>5}{len([r for r in rows if not r.ok]):>12}"
        )
    for row in result.mismatches:
        lines.append(f"  MISMATCH {row.fixture_id} ({row.scorer} on {row.case_id}): {mark(row)} — {row.why} -> {row.detail}")
    passed_refs = result.reference_cases - len({c for c, _, _ in result.reference_failures})
    lines += ["", f"Positive control: {passed_refs}/{result.reference_cases} reference answers pass every applicable scorer"]
    for case_id, key, detail in result.reference_failures[:10]:
        lines.append(f"  FAIL {case_id} {key}: {detail}")
    total = len(result.rows) + len(result.pair_rows)
    if result.exit_code == EXIT_OK:
        lines += ["", f"OK: {total}/{total} fixtures as expected — no false answer passes, no true answer fails."]
    else:
        lines += ["", f"FAILED: {len(result.mismatches)} mismatch(es), {len(result.false_passes)} false pass(es), "
                      f"{len(result.reference_failures)} positive-control failure(s)."]
    return "\n".join(lines)
