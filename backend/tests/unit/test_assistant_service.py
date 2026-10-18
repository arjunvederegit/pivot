"""The Ask Pivot request service: readiness, validation, the in-flight limit, the timeout,
cancellation on disconnect, error mapping and logging.

Most tests run the real service around `PlainChain` — the real pipeline stages run without
LangChain, with the model's reply injected — so the bounding logic is covered on the base
install. Tests marked `requires_langchain` run the real LangChain chain with fake models and
fake embeddings. Nothing contacts Ollama or LangSmith.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from typing import Any

import httpx
import pytest

from app.assistant_rag import providers, tracing
from app.assistant_rag.corpus import Corpus
from app.assistant_rag.errors import (
    AssistantBusyError,
    AssistantDependencyError,
    AssistantDisabledError,
    AssistantInternalError,
    AssistantValidationError,
    CorpusUnavailableError,
    ModelUnavailableError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.assistant_rag.models import AskResponse, DependenciesStatusOut, ProviderStatusOut
from app.assistant_rag.pipeline import AskStages
from app.assistant_rag.service import (
    BUSY_MESSAGE,
    DISABLED_ISSUE,
    AssistantService,
    RequestCancelledError,
    get_service,
    model_loop,
    reset_service,
)
from app.config import Settings
from tests.unit.test_assistant_pipeline import (
    AMBIGUOUS_QUESTION,
    GROUNDED_QUESTION,
    NoModel,
    ask,
    make_corpus,
    make_settings,
    run_plain,
)

HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None
requires_langchain = pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")

ANSWER = "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S05]."
CORPUS = make_corpus()


class PlainChain:
    """Stands in for the LangChain chain: the real stages, run without LangChain."""

    def __init__(self, corpus: Corpus, retriever: Any, settings: Settings, *, answer: str, delay: float, error: Any) -> None:
        self.stages = AskStages(corpus=corpus, retriever=retriever, model=NoModel(), settings=settings, chat_model_name="fake-chat")
        self.answer, self.delay, self.error = answer, delay, error
        self.calls: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        self.loops: list[asyncio.AbstractEventLoop] = []
        self.cancelled = False

    async def ainvoke(self, payload: dict[str, Any], config: dict[str, Any] | None = None) -> AskResponse:
        self.calls.append((payload, config))
        self.loops.append(asyncio.get_running_loop())
        if self.delay:
            try:
                await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                self.cancelled = True  # what closes the HTTP call to Ollama in the real chain
                raise
        if self.error is not None:
            raise self.error
        return await run_plain(
            self.stages, payload["request"], request_id=payload["request_id"], model_text=self.answer, trace=payload.get("trace")
        )


def make_service(
    settings: Settings | None = None,
    *,
    answer: str = ANSWER,
    delay: float = 0.0,
    error: Any = None,
    embeddings_factory: Any = None,
    corpus_loader: Any = None,
    poll: float = 0.01,
) -> tuple[AssistantService, list[PlainChain]]:
    chains: list[PlainChain] = []

    def chain_factory(*, corpus: Corpus, retriever: Any, chat_model: Any, settings: Settings) -> PlainChain:
        chain = PlainChain(corpus, retriever, settings, answer=answer, delay=delay, error=error)
        chains.append(chain)
        return chain

    svc = AssistantService(
        settings or make_settings(),
        corpus_loader=corpus_loader or (lambda: CORPUS),
        embeddings_factory=embeddings_factory or (lambda s: None),
        chat_model_factory=lambda s: type("FakeChat", (), {"model": "fake-chat"})(),
        chain_factory=chain_factory,
        disconnect_poll_seconds=poll,
    )
    return svc, chains


def healthy_probe(settings: Settings, **overrides: Any) -> ProviderStatusOut:
    fields: dict[str, Any] = {
        "name": "ollama",
        "base_url_is_local": True,
        "reachable": True,
        "version": "0.40.5",
        "chat_model": settings.assistant_chat_model,
        "chat_model_available": True,
        "embedding_model": settings.assistant_embedding_model,
        "embedding_model_available": True,
        "error": None,
    }
    fields.update(overrides)
    return ProviderStatusOut(**fields)


@pytest.fixture(autouse=True)
def dependencies_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service checks the optional extra before answering; CI does not install it."""
    monkeypatch.setattr(
        providers, "dependency_status", lambda: DependenciesStatusOut(installed=True, missing=[], install_hint=None)
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture()
def service_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """caplog for the service logger. Another test in the suite may have run a logging
    `fileConfig` (alembic's env.py does), which disables every logger that already exists."""
    service_logger = logging.getLogger("app.assistant_rag.service")
    monkeypatch.setattr(service_logger, "disabled", False)
    monkeypatch.setattr(service_logger, "propagate", True)
    caplog.set_level(logging.INFO, logger="app.assistant_rag.service")
    return caplog


# -------------------------------------------------------------------------- status


def test_status_when_disabled_probes_and_loads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_probe(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a disabled assistant must not probe Ollama")

    def no_corpus() -> Corpus:
        raise AssertionError("a disabled assistant must not load the corpus")

    monkeypatch.setattr(providers, "probe_ollama", no_probe)
    svc, _ = make_service(make_settings(assistant_enabled=False), corpus_loader=no_corpus)
    status = run(svc.status())
    assert (status.enabled, status.ready, status.issues) == (False, False, [DISABLED_ISSUE])
    assert status.provider is None and status.corpus is None and status.tracing is None
    assert "NEXT_PUBLIC_ASSISTANT_ENABLED=true" in DISABLED_ISSUE


def test_status_ready_with_a_healthy_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(assistant_dev_options=True)
    calls: list[float] = []

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        calls.append(timeout)
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    svc, _ = make_service(settings)
    status = run(svc.status())
    assert status.enabled and status.ready and status.issues == []
    assert status.dev_options is True and status.data_mode == "synthetic_demo"
    assert status.corpus is not None and status.corpus.loaded and status.corpus.chunks == len(CORPUS.chunks)
    assert status.index is not None and not status.index.built  # nothing is embedded by a status call
    assert status.tracing is not None and not status.tracing.enabled and not status.tracing.api_key_configured
    run(svc.status())
    assert len(calls) == 1  # the probe is cached for a few seconds


def test_status_names_the_fix_for_each_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(
        assistant_chat_model="qwen3.5:2b", ollama_base_url="http://10.1.1.1:11434", langsmith_tracing=True
    )

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s, base_url_is_local=False, chat_model_available=False, embedding_model_available=False)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    monkeypatch.setattr(
        providers,
        "dependency_status",
        lambda: DependenciesStatusOut(installed=False, missing=["langsmith"], install_hint=providers.INSTALL_HINT),
    )
    status = run(make_service(settings)[0].status())
    text = "\n".join(status.issues)
    assert not status.ready
    assert "ollama pull qwen3.5:2b" in text and "ollama pull fake-embed" in text
    assert providers.INSTALL_HINT in text
    assert "not a loopback address" in text
    assert "ASSISTANT_TRACING is true but LANGSMITH_API_KEY is empty" in text


def test_status_reports_an_unreachable_provider_and_a_broken_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s, reachable=False, chat_model_available=False, embedding_model_available=False,
                             error="Ollama is not reachable at http://localhost:11434. Start it with 'ollama serve'.")

    def broken() -> Corpus:
        raise CorpusUnavailableError("The fictional corpus could not be loaded: S05 is missing")

    monkeypatch.setattr(providers, "probe_ollama", probe)
    status = run(make_service(corpus_loader=broken)[0].status())
    assert not status.ready and status.corpus is not None and not status.corpus.loaded
    assert any("ollama serve" in i for i in status.issues) and any("S05 is missing" in i for i in status.issues)


def test_a_failed_model_listing_shows_the_real_error_not_ollama_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/version answered but /api/tags did not: which models are pulled is unknown."""
    error = "Ollama at http://localhost:11434 did not answer within 1.5 s."

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s, chat_model_available=False, embedding_model_available=False, error=error)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    status = run(make_service()[0].status())
    assert not status.ready
    assert status.issues == [error]
    assert not any("ollama pull" in issue for issue in status.issues)


def test_status_never_shows_credentials_from_the_ollama_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL with userinfo (an auth proxy) is used to connect, but only scheme://host:port is shown."""
    url = "http://ops:Hunter2@gpu-box.lan:11434/private-path?token=abc"
    real_client = httpx.AsyncClient

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(refused), **kw))
    status = run(make_service(make_settings(ollama_base_url=url))[0].status())  # the real probe, no socket
    dumped = status.model_dump_json()
    for secret in ("Hunter2", "ops:", "private-path", "token"):
        assert secret not in dumped, secret
    text = "\n".join(status.issues)
    assert "Ollama is not reachable at http://gpu-box.lan:11434." in text
    assert "OLLAMA_BASE_URL (http://gpu-box.lan:11434) is not a loopback address" in text
    assert status.provider is not None and "Hunter2" not in (status.provider.error or "")


# ------------------------------------------------------------------ a broken corpus

MALFORMED_MANIFESTS = {
    "json_syntax": (b'{"corpus_version": "x",', "JSONDecodeError"),
    "missing_field": (b"{}", "KeyError"),
    "wrong_shape": (b"[]", "TypeError"),
    "wrong_item_shape": (b'{"teams": [1]}', "TypeError"),
    "bad_utf8": (b'{"corpus_version": "\xff"}', "UnicodeDecodeError"),
}


@pytest.fixture()
def corpus_from(monkeypatch: pytest.MonkeyPatch):
    """Make the real default loader (`_load_corpus_or_raise`) read a manifest at `path`.
    A load that raises caches nothing, so the process's real manifest stays cached."""
    from app.assistant_rag import corpus as corpus_module
    from app.assistant_rag import manifest as manifest_module
    from app.assistant_rag import service as service_module

    def _point_at(path: Any) -> Any:
        monkeypatch.setattr(
            service_module, "load_corpus", lambda: corpus_module.build_corpus(manifest_module.load_manifest(str(path)))
        )
        return service_module._load_corpus_or_raise

    return _point_at


@pytest.mark.parametrize("case", sorted(MALFORMED_MANIFESTS))
def test_a_malformed_manifest_is_corpus_unavailable_everywhere(
    case: str, tmp_path: Any, corpus_from: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, error_type = MALFORMED_MANIFESTS[case]
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    loader = corpus_from(path)
    with pytest.raises(CorpusUnavailableError, match=f"malformed \\({error_type}"):
        loader()

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    svc, _ = make_service(corpus_loader=loader)
    status = run(svc.status())  # reports, never raises
    assert not status.ready and status.corpus is not None and not status.corpus.loaded
    assert error_type in (status.corpus.error or "") and str(tmp_path) not in status.model_dump_json()
    with pytest.raises(CorpusUnavailableError):
        svc.context()
    with pytest.raises(CorpusUnavailableError):
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="r"))


def test_a_missing_corpus_file_is_reported_by_file_name_only(tmp_path: Any, corpus_from: Any) -> None:
    loader = corpus_from(tmp_path / "private" / "checkout" / "manifest.json")
    with pytest.raises(CorpusUnavailableError) as info:
        loader()
    assert "No such file or directory" in info.value.message and "'manifest.json'" in info.value.message
    assert str(tmp_path) not in info.value.message and "/private/checkout" not in info.value.message


def test_status_survives_a_loader_that_raises_something_unexpected(
    monkeypatch: pytest.MonkeyPatch, service_log: pytest.LogCaptureFixture
) -> None:
    def broken() -> Corpus:
        raise TypeError("a bug in an injected loader")

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    status = run(make_service(corpus_loader=broken)[0].status())
    assert not status.ready and status.corpus is not None and not status.corpus.loaded
    assert "TypeError" in (status.corpus.error or "") and "a bug" not in (status.corpus.error or "")
    assert any(r.levelno == logging.ERROR and r.exc_info for r in service_log.records)


def test_status_never_returns_the_langsmith_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "lsv2_pt_THIS-IS-A-TEST-SECRET"
    settings = make_settings(langsmith_tracing=True, langsmith_api_key=secret, langsmith_project="proj")

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    status = run(make_service(settings)[0].status())
    assert status.tracing is not None
    assert status.tracing.model_dump() == {"enabled": True, "api_key_configured": True, "project": "proj"}
    assert secret not in status.model_dump_json()


def test_context_requires_the_feature_and_returns_the_catalog() -> None:
    with pytest.raises(AssistantDisabledError):
        make_service(make_settings(assistant_enabled=False))[0].context()
    catalog = make_service()[0].context()
    assert catalog.data_mode == "synthetic_demo" and {t.id for t in catalog.teams} >= {"COMETS"}


# ---------------------------------------------------------------------- validation


def test_ask_refuses_when_disabled() -> None:
    with pytest.raises(AssistantDisabledError):
        run(make_service(make_settings(assistant_enabled=False))[0].ask(ask(GROUNDED_QUESTION), request_id="r"))


def test_unknown_team_is_a_validation_error() -> None:
    with pytest.raises(AssistantValidationError, match="Unknown team_id 'LAKERS'.*COMETS"):
        run(make_service()[0].ask(ask("Who fits?", team_id="LAKERS"), request_id="r"))


def test_dev_options_need_the_dev_flag() -> None:
    request = ask(GROUNDED_QUESTION, options={"prompt_variant": "basic"})
    with pytest.raises(AssistantValidationError, match="ASSISTANT_DEV_OPTIONS=true"):
        run(make_service()[0].ask(request, request_id="r"))
    response = run(make_service(make_settings(assistant_dev_options=True))[0].ask(request, request_id="r"))
    assert response.diagnostics.prompt_variant == "basic"
    # an options block that sets nothing changes nothing, so it is not refused
    assert run(make_service()[0].ask(ask(GROUNDED_QUESTION, options={}), request_id="r")).answer == ANSWER


def test_missing_dependencies_are_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        providers,
        "dependency_status",
        lambda: DependenciesStatusOut(installed=False, missing=["langchain_core"], install_hint=providers.INSTALL_HINT),
    )
    with pytest.raises(AssistantDependencyError, match=r"pip install -e '\.\[dev,assistant\]'"):
        run(make_service()[0].ask(ask(GROUNDED_QUESTION), request_id="r"))


def test_a_broken_corpus_is_corpus_unavailable() -> None:
    def broken() -> Corpus:
        raise CorpusUnavailableError("The fictional corpus could not be loaded: bad hash")

    with pytest.raises(CorpusUnavailableError):
        run(make_service(corpus_loader=broken)[0].ask(ask(GROUNDED_QUESTION), request_id="r"))


# ------------------------------------------------------------------------- answering


def test_ask_runs_the_chain_with_run_metadata() -> None:
    svc, chains = make_service()
    response = run(svc.ask(ask(GROUNDED_QUESTION, mode="roster_fit", team_id="COMETS"), request_id="req-1"))
    assert response.answer == ANSWER and response.request_id == "req-1"
    assert response.data_mode == "synthetic_demo" and response.trace.enabled is False
    payload, config = chains[0].calls[0]
    assert payload["request_id"] == "req-1" and payload["trace"].enabled is False
    assert config is not None and config["run_name"] == "ask_pivot" and "run_id" not in config
    metadata = config["metadata"]
    assert metadata["corpus_version"] == CORPUS.manifest.corpus_version
    assert (metadata["team_id"], metadata["mode"], metadata["data_mode"]) == ("COMETS", "roster_fit", "synthetic_demo")
    assert "ask-pivot" in config["tags"]
    # built once, reused
    run(svc.ask(ask(AMBIGUOUS_QUESTION), request_id="req-2"))
    assert len(chains) == 1 and len(chains[0].calls) == 2


def test_a_second_concurrent_ask_is_busy_and_the_slot_is_released() -> None:
    svc, _ = make_service(delay=0.2)

    async def both() -> list[Any]:
        return await asyncio.gather(
            svc.ask(ask(GROUNDED_QUESTION), request_id="a"),
            svc.ask(ask(GROUNDED_QUESTION), request_id="b"),
            return_exceptions=True,
        )

    first, second = run(both())
    assert isinstance(first, AskResponse)
    assert isinstance(second, AssistantBusyError) and second.message == BUSY_MESSAGE
    assert isinstance(run(svc.ask(ask(GROUNDED_QUESTION), request_id="c")), AskResponse)


def _eventually(predicate: Any, seconds: float = 2.0) -> bool:
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_timeout_is_a_provider_timeout_cancels_the_work_and_frees_the_slot() -> None:
    svc, chains = make_service(make_settings(assistant_request_timeout_seconds=0.05), delay=5.0)
    started = time.perf_counter()
    with pytest.raises(ProviderTimeoutError, match="No answer within 0.05 s"):
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="slow"))
    assert time.perf_counter() - started < 2.0
    assert not svc._slots.locked()
    assert _eventually(lambda: chains[0].cancelled), "the work on the model loop must be cancelled"


def test_every_answer_runs_on_the_model_loop_whatever_the_callers_loop() -> None:
    svc, chains = make_service()
    callers: list[asyncio.AbstractEventLoop] = []

    async def one(request_id: str) -> AskResponse:
        callers.append(asyncio.get_running_loop())
        return await svc.ask(ask(GROUNDED_QUESTION), request_id=request_id)

    run(one("a"))
    run(one("b"))  # a second asyncio.run: a new caller loop, as in a script or an evaluation
    loops = chains[0].loops
    assert len(loops) == 2 and loops[0] is loops[1] is model_loop()
    assert callers[0] is not callers[1] and all(loop not in callers for loop in loops)


def test_provider_exceptions_escaping_the_chain_are_mapped() -> None:
    with pytest.raises(ProviderUnavailableError, match="ollama serve"):
        run(make_service(error=httpx.ConnectError("refused"))[0].ask(ask(GROUNDED_QUESTION), request_id="r"))
    with pytest.raises(ProviderTimeoutError):
        run(make_service(error=httpx.ReadTimeout("slow"))[0].ask(ask(GROUNDED_QUESTION), request_id="r"))


def test_missing_model_is_model_unavailable() -> None:
    ollama = pytest.importorskip("ollama")
    error = ollama.ResponseError('model "qwen3.5:2b" not found, try pulling it first', 404)
    with pytest.raises(ModelUnavailableError, match="ollama pull qwen3.5:2b"):
        run(make_service(error=error)[0].ask(ask(GROUNDED_QUESTION), request_id="r"))


def test_unexpected_errors_are_generic_and_logged_with_the_request_id(service_log: pytest.LogCaptureFixture) -> None:
    svc, _ = make_service(error=RuntimeError("internal detail that must not leak"))
    with pytest.raises(AssistantInternalError) as info:
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="req-err"))
    assert "internal detail" not in info.value.message and "request id" in info.value.message
    failures = [r for r in service_log.records if r.levelno == logging.ERROR]
    assert failures and getattr(failures[0], "request_id", None) == "req-err"
    assert not svc._slots.locked()


def test_a_failed_build_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def flaky_embeddings(settings: Settings) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("Failed to connect to Ollama")
        return None

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    svc, _ = make_service(embeddings_factory=flaky_embeddings)
    with pytest.raises(ProviderUnavailableError, match="ollama serve"):
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="first"))
    status = run(svc.status())
    assert status.index is not None and not status.index.built and "ollama serve" in (status.index.error or "")
    # A provider failure is not held against readiness: the UI only asks while `ready` is
    # true, and the next question is what retries the build once Ollama is up.
    assert status.ready
    assert run(svc.ask(ask(GROUNDED_QUESTION), request_id="second")).answer == ANSWER
    status = run(svc.status())
    assert status.index is not None and status.index.built and status.index.error is None
    assert status.index.chunks == len(CORPUS.chunks) and len(attempts) == 2


def test_an_http_error_during_the_build_is_provider_unavailable() -> None:
    request = httpx.Request("POST", "http://localhost:11434/api/embed")

    def failing(settings: Settings) -> Any:
        raise httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request))

    with pytest.raises(ProviderUnavailableError, match="answered 500 to /api/embed"):
        run(make_service(embeddings_factory=failing)[0].ask(ask(GROUNDED_QUESTION), request_id="r"))


@pytest.mark.parametrize("where", ["embeddings", "chain"])
def test_a_bug_in_the_build_is_an_internal_error_logged_with_its_traceback(
    where: str, monkeypatch: pytest.MonkeyPatch, service_log: pytest.LogCaptureFixture
) -> None:
    def bug(*args: Any, **kwargs: Any) -> Any:
        raise TypeError("build_chain() got an unexpected keyword argument 'retriever'")

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    svc, _ = make_service(embeddings_factory=bug if where == "embeddings" else None)
    if where == "chain":
        svc._chain_factory = bug
    with pytest.raises(AssistantInternalError) as info:
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="req-bug"))
    assert "unexpected error (TypeError)" in info.value.message
    assert "Ollama" not in info.value.message and "unexpected keyword" not in info.value.message
    failures = [r for r in service_log.records if r.levelno == logging.ERROR]
    assert failures and failures[0].exc_info is not None and getattr(failures[0], "request_id", None) == "req-bug"
    assert "unexpected keyword argument" in service_log.text  # the traceback reaches the log
    assert not svc._slots.locked()
    # retrying cannot fix a bug, so status says not ready and names the problem
    status = run(svc.status())
    assert not status.ready and status.index is not None and "TypeError" in (status.index.error or "")
    assert any("retrieval index failed" in issue for issue in status.issues)


def test_client_disconnect_cancels_the_work() -> None:
    svc, chains = make_service(delay=5.0)
    polls: list[int] = []

    async def is_disconnected() -> bool:
        polls.append(1)
        return len(polls) >= 2

    started = time.perf_counter()
    with pytest.raises(RequestCancelledError):
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="gone", is_disconnected=is_disconnected))
    assert time.perf_counter() - started < 2.0 and not svc._slots.locked()
    assert _eventually(lambda: chains[0].cancelled), "the work on the model loop must be cancelled"


def test_a_disconnect_check_that_fails_stops_watching_but_not_answering() -> None:
    svc, _ = make_service(delay=0.05)

    async def broken() -> bool:
        raise RuntimeError("receive channel closed")

    assert run(svc.ask(ask(GROUNDED_QUESTION), request_id="r", is_disconnected=broken)).answer == ANSWER


def test_one_log_line_per_ask_without_the_question_or_answer(service_log: pytest.LogCaptureFixture) -> None:
    svc, _ = make_service()
    run(svc.ask(ask(GROUNDED_QUESTION), request_id="req-log"))
    lines = [r for r in service_log.records if r.name == "app.assistant_rag.service" and "assistant ask" in r.getMessage()]
    assert len(lines) == 1
    message = lines[0].getMessage()
    assert "outcome=ok" in message and "route=answer" in message and "answer_kind=grounded_answer" in message
    assert "model_call=" in message and getattr(lines[0], "request_id", None) == "req-log"
    assert GROUNDED_QUESTION not in service_log.text and ANSWER not in service_log.text


def test_get_service_is_a_resettable_singleton() -> None:
    reset_service()
    try:
        assert get_service() is get_service()
        first = get_service()
        reset_service()
        assert get_service() is not first
    finally:
        reset_service()


# ----------------------------------------------------------------- with LangChain


@requires_langchain
def test_tracing_handle_names_the_root_run_and_its_url(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(langsmith_tracing=True, langsmith_api_key="lsv2-test-key", langsmith_project="proj")
    monkeypatch.setattr(tracing, "_client", lambda s: object())
    monkeypatch.setattr(tracing, "trace_url", lambda s, handle: f"https://smith.example/r/{handle.run_id}")
    svc, chains = make_service(settings)
    response = run(svc.ask(ask(AMBIGUOUS_QUESTION), request_id="traced"))
    _, config = chains[0].calls[0]
    assert config is not None and str(config["run_id"]) == response.trace.run_id
    assert response.trace.enabled and response.trace.project == "proj"
    assert response.trace.url == f"https://smith.example/r/{response.trace.run_id}"


@requires_langchain
def test_real_chain_with_fake_model_and_embeddings_through_the_service() -> None:
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from tests.unit.test_assistant_pipeline import ScriptedChatModel

    svc = AssistantService(
        make_settings(),
        corpus_loader=lambda: CORPUS,
        embeddings_factory=lambda s: DeterministicFakeEmbedding(size=256),
        chat_model_factory=lambda s: ScriptedChatModel(reply=ANSWER),
    )
    response = run(svc.ask(ask(GROUNDED_QUESTION), request_id="lc"))
    assert response.answer == ANSWER and response.cited_source_ids == ["S05"]
    assert response.diagnostics.embedding_model == "DeterministicFakeEmbedding"
    assert [t.stage for t in response.diagnostics.timings][:3] == ["query_context", "entity_resolution", "policy"]


@requires_langchain
def test_slow_fake_model_times_out_through_the_real_chain() -> None:
    from tests.unit.test_assistant_pipeline import ScriptedChatModel

    svc = AssistantService(
        make_settings(assistant_request_timeout_seconds=0.2),
        corpus_loader=lambda: CORPUS,
        embeddings_factory=lambda s: None,
        chat_model_factory=lambda s: ScriptedChatModel(reply=ANSWER, delay=5.0),
    )
    started = time.perf_counter()
    with pytest.raises(ProviderTimeoutError):
        run(svc.ask(ask(GROUNDED_QUESTION), request_id="slow"))
    assert time.perf_counter() - started < 3.0
