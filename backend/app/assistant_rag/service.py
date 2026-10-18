"""Request bounding for Ask Pivot: one place that decides whether, and how long, a question
may run.

A local language model is a scarce, slow resource: one 2B model on a laptop generates one
answer at a time, a cold load takes seconds and a long answer tens of seconds. The pipeline
(`pipeline.py`) does not know any of that; this service wraps it with the limits a server
needs:

* **lazy, retryable startup** — the corpus, the embedding index, the chat model and the
  chain are built on the first question (embedding the corpus is one batched call to
  Ollama), once, under a lock. A failed build is *not* cached: the next request retries,
  so starting Ollama after the backend just works. Only a *provider* failure (connection,
  HTTP, Ollama error) is reported as one; anything else that breaks the build is a bug,
  logged with its traceback, returned as `assistant_internal_error`, and makes `status()`
  say not ready — retrying cannot fix it;
* **an in-flight limit** — an `asyncio.Semaphore(ASSISTANT_MAX_IN_FLIGHT)` acquired
  without waiting. A second question while one is generating is a 503 `assistant_busy`
  straight away, not a request silently queued behind a minute of generation;
* **a wall-clock timeout** — `asyncio.wait_for(ASSISTANT_REQUEST_TIMEOUT_SECONDS)`; past it
  the work is cancelled and the caller gets a 504 `provider_timeout`;
* **cancellation on disconnect** — when the browser aborts (the GM pressed Stop), a watcher
  notices within half a second and cancels the work, which closes the HTTP call to Ollama
  and frees the slot. The route supplies the disconnect check; in the real app it reads
  what `AskDisconnectMiddleware` (api/v1/assistant.py) recorded, because Starlette's
  `Request.is_disconnected()` never reports a disconnect behind `app.main`'s
  `BaseHTTPMiddleware`;
* **typed failures** — provider exceptions become `provider_unavailable`,
  `model_unavailable` or `provider_timeout` with the command that fixes them; anything
  unexpected is logged with the request id and returned as a generic
  `assistant_internal_error`, never a stack trace.
* **one event loop for the model** — the chain runs on a process-wide loop the service owns
  (`model_loop()`), never on the caller's. ChatOllama's and OllamaEmbeddings' async HTTP
  clients keep pooled connections that belong to the loop of their first request; a server
  has one loop, but a script or evaluation that calls `asyncio.run()` per question creates a
  new loop each time, and the cached clients would then fail with "Event loop is closed".
  The caller awaits the work through `run_coroutine_threadsafe`, so timeouts and
  cancellation still reach the model call.

`status()` is the readiness report behind `GET /assistant/status`; it probes Ollama (cached
for a few seconds) but never builds the index or calls a model. One structured log line is
written per question — request id, route, answer kind, timings — and never the question,
the prompt or the answer.

Nothing here imports LangChain at module import time; the pipeline is imported when the
first question builds it.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.assistant_rag import SANDBOX_LABEL, providers, tracing
from app.assistant_rag.corpus import Corpus, CorpusError, load_corpus
from app.assistant_rag.errors import (
    AssistantBusyError,
    AssistantDependencyError,
    AssistantDisabledError,
    AssistantError,
    AssistantInternalError,
    AssistantValidationError,
    CorpusUnavailableError,
    ProviderTimeoutError,
    RequestCancelledError,
)
from app.assistant_rag.manifest import ManifestError
from app.assistant_rag.models import (
    AskRequest,
    AskResponse,
    ContextOut,
    CorpusStatusOut,
    IndexStatusOut,
    ProviderStatusOut,
    StatusOut,
    TracingStatusOut,
)
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

logger = get_logger(__name__)

DISABLED_ISSUE = (
    "Ask Pivot is disabled. Set ASSISTANT_ENABLED=true (backend) and "
    "NEXT_PUBLIC_ASSISTANT_ENABLED=true (frontend) for local development."
)
BUSY_MESSAGE = "Another answer is in progress; try again when it finishes."
PROBE_CACHE_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 1.5
DISCONNECT_POLL_SECONDS = 0.5
TRACE_URL_TIMEOUT_SECONDS = 2.0
_ROUTE_FOR_KIND = {"clarification": "clarify", "unsupported_request": "refuse"}


_loop_lock = threading.Lock()
_model_loop: asyncio.AbstractEventLoop | None = None


def model_loop() -> asyncio.AbstractEventLoop:
    """The process-wide event loop every chain run executes on (a daemon thread, started on
    first use). See "one event loop for the model" in the module doc."""
    global _model_loop
    with _loop_lock:
        if _model_loop is None or _model_loop.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="ask-pivot-model-loop", daemon=True).start()
            _model_loop = loop
        return _model_loop


@dataclass(frozen=True)
class Components:
    """Everything one question needs, built once."""

    corpus: Corpus
    retriever: Any  # HybridRetriever
    chain: Any  # the LangChain runnable
    chat_model_name: str | None
    embedding_model: str | None


#: The directories of an absolute POSIX path (`/home/me/pivot/.../`), so that only the file
#: name is left: corpus errors reach `/status` and the error envelope, and a path would
#: show the user name and the checkout layout to whoever reads them.
_DIRECTORIES = re.compile(r"""(?<![\w.])(?:/[^\s/'"]+)+/(?=[^\s/'"])""")
_MAX_CORPUS_DETAIL_CHARS = 240


def _corpus_message(detail: str) -> str:
    detail = _DIRECTORIES.sub("", detail)
    if len(detail) > _MAX_CORPUS_DETAIL_CHARS:
        detail = detail[: _MAX_CORPUS_DETAIL_CHARS - 1] + "…"
    return f"The fictional corpus could not be loaded: {detail}"


def _load_corpus_or_raise() -> Corpus:
    """The corpus, or `CorpusUnavailableError` with a message safe to show any client.

    Besides the corpus's own checks (`CorpusError`, `ManifestError`) and a missing or
    unreadable file (`OSError`), a hand-edited manifest or document can fail as plain
    Python errors: a JSON syntax error (`json.JSONDecodeError`), a missing field
    (`KeyError`), a wrong shape (`TypeError`), a bad value (`ValueError`) or bad UTF-8
    (`UnicodeDecodeError`). All of them are the same problem to the caller — the corpus is
    unavailable — so all of them map to it, instead of escaping as a bare 500."""
    try:
        return load_corpus()
    except (CorpusError, ManifestError, OSError) as exc:
        raise CorpusUnavailableError(_corpus_message(str(exc))) from exc
    except (KeyError, TypeError, ValueError) as exc:  # ValueError covers both decode errors
        raise CorpusUnavailableError(
            _corpus_message(f"the manifest or a document is malformed ({type(exc).__name__}: {exc})")
        ) from exc


class AssistantService:
    """The Ask Pivot request service. Factories are injectable so tests can run the real
    bounding logic against fake models, embeddings or chains."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        corpus_loader: Callable[[], Corpus] = _load_corpus_or_raise,
        embeddings_factory: Callable[[Settings], Any] | None = None,
        chat_model_factory: Callable[[Settings], Any] | None = None,
        chain_factory: Callable[..., Any] | None = None,
        disconnect_poll_seconds: float = DISCONNECT_POLL_SECONDS,
    ) -> None:
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
        self.settings = settings
        self._corpus_loader = corpus_loader
        self._embeddings_factory = embeddings_factory
        self._chat_model_factory = chat_model_factory
        self._chain_factory = chain_factory
        self._poll = disconnect_poll_seconds
        self._slots = asyncio.Semaphore(max(1, settings.assistant_max_in_flight))
        self._build_lock = threading.Lock()
        self._components: Components | None = None
        self._last_build_error: str | None = None
        #: The last build failed with a bug, not a provider problem (see `_build`).
        self._last_build_was_internal = False
        self._probe_cache: tuple[float, ProviderStatusOut] | None = None

    # ------------------------------------------------------------------- building

    def _build(self, request_id: str | None = None) -> Components:
        """Build (once) the retriever index, chat model and chain. Blocking: embedding the
        corpus is a network call to Ollama, so `ask` runs this in a worker thread.

        A provider failure (connection, HTTP, Ollama error) becomes its actionable code and
        the next question retries. Anything else is a bug in the build itself — a chain
        factory, the retriever, the index code — and saying "check that Ollama is running"
        would send the user to debug the wrong thing while the cause was lost. It is logged
        with its traceback and becomes `assistant_internal_error`, and `status()` reports
        not ready until a build succeeds."""
        if self._components is not None:
            return self._components
        with self._build_lock:
            if self._components is not None:
                return self._components
            try:
                components = self._build_components()
            except AssistantError as exc:
                self._last_build_error = exc.message
                self._last_build_was_internal = False
                raise
            except Exception as exc:
                mapped = providers.map_provider_error(
                    exc, settings=self.settings, model=self.settings.assistant_embedding_model
                )
                if mapped is None:
                    logger.exception(
                        "assistant index build failed unexpectedly (%s)", type(exc).__name__,
                        extra={"request_id": request_id},
                    )
                    mapped = AssistantInternalError(
                        f"Ask Pivot could not build its retrieval index because of an unexpected error "
                        f"({type(exc).__name__}). The server log has the traceback; restart the backend once "
                        "it is fixed."
                    )
                self._last_build_error = mapped.message
                self._last_build_was_internal = isinstance(mapped, AssistantInternalError)
                raise mapped from exc
            self._components = components
            self._last_build_error = None
            self._last_build_was_internal = False
            return components

    def _build_components(self) -> Components:
        from app.assistant_rag import pipeline
        from app.assistant_rag.retrieval import HybridRetriever

        corpus = self._corpus_loader()
        if self._embeddings_factory is not None:
            embeddings = self._embeddings_factory(self.settings)
        else:
            from app.assistant_rag.embeddings import build_embeddings

            embeddings = build_embeddings(self.settings)
        retriever = HybridRetriever(corpus, embeddings)
        retriever.build()
        chat_factory = self._chat_model_factory or providers.build_chat_model
        chat_model = chat_factory(self.settings)
        chain_factory = self._chain_factory or pipeline.build_chain
        chain = chain_factory(corpus=corpus, retriever=retriever, chat_model=chat_model, settings=self.settings)
        return Components(
            corpus=corpus,
            retriever=retriever,
            chain=chain,
            chat_model_name=providers.model_name(chat_model) or self.settings.assistant_chat_model,
            embedding_model=retriever.embedding_model,
        )

    # --------------------------------------------------------------------- status

    async def _probe(self) -> ProviderStatusOut:
        now = time.monotonic()
        if self._probe_cache is not None and now - self._probe_cache[0] < PROBE_CACHE_SECONDS:
            return self._probe_cache[1]
        result = await providers.probe_ollama(self.settings, timeout=PROBE_TIMEOUT_SECONDS)
        self._probe_cache = (time.monotonic(), result)
        return result

    def _corpus_status(self) -> CorpusStatusOut:
        try:
            corpus = self._corpus_loader()
        except AssistantError as exc:
            return CorpusStatusOut(
                loaded=False, corpus_version=None, documents=0, chunks=0, players=0, teams=0, error=exc.message
            )
        except Exception as exc:  # noqa: BLE001 - a readiness report never raises
            # The default loader maps every corpus failure; an injected one may not.
            logger.exception("assistant corpus check failed unexpectedly (%s)", type(exc).__name__)
            return CorpusStatusOut(
                loaded=False, corpus_version=None, documents=0, chunks=0, players=0, teams=0,
                error=f"The fictional corpus could not be loaded ({type(exc).__name__}); the server log has the details.",
            )
        manifest = corpus.manifest
        return CorpusStatusOut(
            loaded=True,
            corpus_version=manifest.corpus_version,
            documents=len(manifest.documents),
            chunks=len(corpus.chunks),
            players=len(manifest.players),
            teams=len(manifest.teams),
            error=None,
        )

    def _index_status(self) -> IndexStatusOut:
        components = self._components
        if components is None:
            return IndexStatusOut(built=False, chunks=0, embedding_model=None, error=self._last_build_error)
        return IndexStatusOut(
            built=bool(components.retriever.built),
            chunks=int(components.retriever.chunk_count),
            embedding_model=components.embedding_model,
            error=None,
        )

    async def status(self) -> StatusOut:
        """Readiness, with the exact command for every problem found. When the feature is
        disabled nothing is probed or loaded."""
        s = self.settings
        if not s.assistant_enabled:
            return StatusOut(
                enabled=False, ready=False, data_mode="synthetic_demo", label=SANDBOX_LABEL, issues=[DISABLED_ISSUE]
            )
        dependencies = providers.dependency_status()
        corpus = self._corpus_status()
        provider = await self._probe()
        index = self._index_status()
        tracing_status = TracingStatusOut(
            enabled=tracing.tracing_active(s),
            api_key_configured=bool(s.langsmith_api_key),
            project=tracing.project_name(s),
        )
        # Only scheme://host:port is ever shown: the URL may carry credentials.
        ollama_url = providers.display_url(s.ollama_base_url)

        issues: list[str] = []
        if not dependencies.installed:
            issues.append(
                f"Missing Python packages ({', '.join(dependencies.missing)}). Install them with: "
                f"{dependencies.install_hint}"
            )
        if not corpus.loaded:
            issues.append(corpus.error or "The fictional corpus could not be loaded.")
        if not provider.reachable:
            issues.append(provider.error or f"Ollama is not reachable at {ollama_url}. Start it with 'ollama serve'.")
        elif provider.error:
            # Up, but the model listing failed (a timeout, an error status, an unreadable
            # body): which models are pulled is unknown, so "ollama pull" would be wrong advice.
            issues.append(provider.error)
        else:
            if not provider.chat_model_available:
                issues.append(f"The chat model '{s.assistant_chat_model}' is not pulled. Run: ollama pull {s.assistant_chat_model}")
            if not provider.embedding_model_available:
                issues.append(
                    f"The embedding model '{s.assistant_embedding_model}' is not pulled. "
                    f"Run: ollama pull {s.assistant_embedding_model}"
                )
        if not provider.base_url_is_local:
            issues.append(
                f"OLLAMA_BASE_URL ({ollama_url}) is not a loopback address. Ask Pivot is designed for a "
                "local model; questions and evidence will be sent to that host."
            )
        if index.error:
            issues.append(f"The last attempt to build the retrieval index failed: {index.error}")
        if s.langsmith_tracing and not s.langsmith_api_key:
            issues.append("ASSISTANT_TRACING is true but LANGSMITH_API_KEY is empty, so tracing stays off.")

        # A build that failed with a bug fails the same way every time, so it is not ready.
        # A build that failed on the provider is not held against readiness: the probe
        # already says whether Ollama is up now, and the UI only asks — and so only lets the
        # next question retry the build — while `ready` is true.
        ready = (
            dependencies.installed
            and corpus.loaded
            and provider.reachable
            and provider.chat_model_available
            and provider.embedding_model_available
            and not (index.error and self._last_build_was_internal)
        )
        return StatusOut(
            enabled=True,
            ready=ready,
            data_mode="synthetic_demo",
            label=SANDBOX_LABEL,
            issues=issues,
            dev_options=s.assistant_dev_options,
            corpus=corpus,
            provider=provider,
            index=index,
            tracing=tracing_status,
            dependencies=dependencies,
        )

    def context(self) -> ContextOut:
        """The team / player / priority catalog the UI renders (pure manifest data)."""
        if not self.settings.assistant_enabled:
            raise AssistantDisabledError("Ask Pivot is not enabled on this server.")
        from app.assistant_rag.context import build_context_catalog

        return build_context_catalog(self._corpus_loader().manifest)

    # ------------------------------------------------------------------------ ask

    def _validate(self, request: AskRequest) -> None:
        """Reject, before taking a slot, what can never succeed: a disabled feature, an unknown
        team, dev options without the dev flag, missing packages."""
        s = self.settings
        if not s.assistant_enabled:
            raise AssistantDisabledError("Ask Pivot is not enabled on this server.")
        corpus = self._corpus_loader()
        if request.team_id is not None and corpus.manifest.team(request.team_id) is None:
            known = ", ".join(t.id for t in corpus.manifest.teams)
            raise AssistantValidationError(
                f"Unknown team_id '{request.team_id}'. The fictional league's teams are: {known}."
            )
        if request.options is not None and request.options.model_dump(exclude_none=True) and not s.assistant_dev_options:
            raise AssistantValidationError(
                "The 'options' block (prompt_variant, retrieval_variant, k) is for local experiments only. "
                "Set ASSISTANT_DEV_OPTIONS=true on the backend to use it."
            )
        dependencies = providers.dependency_status()
        if not dependencies.installed:
            raise AssistantDependencyError(
                f"Ask Pivot needs packages that are not installed ({', '.join(dependencies.missing)}). "
                f"Install them with: {dependencies.install_hint}"
            )

    async def ask(
        self,
        request: AskRequest,
        *,
        request_id: str | None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AskResponse:
        """Answer one question within the in-flight limit, the timeout and the client's
        patience. Raises only `AssistantError` subclasses (or `CancelledError` when the
        server itself cancels the request)."""
        self._validate(request)
        if self._slots.locked():
            raise AssistantBusyError(BUSY_MESSAGE)
        await self._slots.acquire()  # cannot wait: the slot was free and nothing has awaited since
        started = time.perf_counter()
        cancelled_by_client = asyncio.Event()
        # The answer runs on the model loop; `work` is this loop's handle on it. Cancelling
        # `work` (timeout, disconnect) cancels the task over there, and with it the HTTP call.
        on_model_loop = asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._answer(request, request_id), model_loop()))
        work = asyncio.ensure_future(asyncio.wait_for(on_model_loop, self.settings.assistant_request_timeout_seconds))
        watcher = (
            asyncio.ensure_future(self._watch_disconnect(is_disconnected, work, cancelled_by_client))
            if is_disconnected is not None
            else None
        )
        try:
            response = await work
        except asyncio.CancelledError:
            if cancelled_by_client.is_set():
                self._log(request_id, None, started, outcome="client_disconnected")
                raise RequestCancelledError("The client disconnected; the answer was cancelled.") from None
            work.cancel()
            raise
        except AssistantError as exc:
            self._log(request_id, None, started, outcome=exc.code)
            raise
        except TimeoutError as exc:
            self._log(request_id, None, started, outcome="provider_timeout")
            raise ProviderTimeoutError(
                f"No answer within {self.settings.assistant_request_timeout_seconds:g} s, so the request was "
                "cancelled. A cold model load is slow; try again, or raise ASSISTANT_REQUEST_TIMEOUT_SECONDS."
            ) from exc
        except Exception as exc:
            mapped = providers.map_provider_error(exc, settings=self.settings)
            if mapped is not None:
                self._log(request_id, None, started, outcome=mapped.code)
                raise mapped from exc
            logger.exception(
                "assistant ask failed unexpectedly (%s)", type(exc).__name__, extra={"request_id": request_id}
            )
            raise AssistantInternalError(
                "Ask Pivot hit an unexpected error. The request id identifies it in the server log."
            ) from None
        finally:
            if watcher is not None:
                watcher.cancel()
            self._slots.release()
        self._log(request_id, response, started, outcome="ok")
        return response

    async def _answer(self, request: AskRequest, request_id: str | None) -> AskResponse:
        from app.assistant_rag import pipeline

        components = await asyncio.to_thread(self._build, request_id)
        metadata = pipeline.run_metadata(
            request,
            self.settings,
            corpus_version=components.corpus.manifest.corpus_version,
            embedding_model=components.embedding_model,
            request_id=request_id,
            retrieval_config=getattr(components.retriever, "config", None),
        )
        tags = pipeline.run_tags(metadata)
        with tracing.tracing_scope(self.settings, metadata=metadata, tags=tags) as handle:
            response: AskResponse = await components.chain.ainvoke(
                {"request": request, "request_id": request_id, "trace": handle.to_out()},
                config=pipeline.run_config(metadata, tags, run_id=handle.run_id),
            )
        if handle.enabled:
            url = await self._trace_url(handle)
            if url:
                response = response.model_copy(update={"trace": handle.to_out(url)})
        return response

    async def _trace_url(self, handle: tracing.TraceHandle) -> str | None:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(tracing.trace_url, self.settings, handle), TRACE_URL_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - a missing link is not an error
            return None

    async def _watch_disconnect(
        self, is_disconnected: Callable[[], Awaitable[bool]], work: asyncio.Future[Any], flag: asyncio.Event
    ) -> None:
        while not work.done():
            await asyncio.sleep(self._poll)
            try:
                gone = await is_disconnected()
            except Exception:  # noqa: BLE001 - cannot tell; stop watching rather than guess
                return
            if gone:
                flag.set()
                work.cancel()
                return

    def _log(self, request_id: str | None, response: AskResponse | None, started: float, *, outcome: str) -> None:
        """One line per question. Ids, routes, counts and timings only — never the question,
        the prompt or the answer."""
        total_ms = (time.perf_counter() - started) * 1000.0
        if response is None:
            logger.info(
                "assistant ask outcome=%s total_ms=%.0f", outcome, total_ms, extra={"request_id": request_id}
            )
            return
        stages = ",".join(f"{t.stage}={t.ms:.0f}" for t in response.diagnostics.timings if "." not in t.stage)
        logger.info(
            "assistant ask outcome=%s route=%s answer_kind=%s model_called=%s sources=%d warnings=%d "
            "total_ms=%.0f stages_ms=[%s]",
            outcome,
            _ROUTE_FOR_KIND.get(response.answer_kind, "answer"),
            response.answer_kind,
            response.diagnostics.model_called,
            len(response.sources),
            len(response.warnings),
            total_ms,
            stages,
            extra={"request_id": request_id, "endpoint": "/assistant/ask", "run_id": response.trace.run_id},
        )


# ---------------------------------------------------------------------------- singleton

_service: AssistantService | None = None
_service_lock = threading.Lock()


def get_service() -> AssistantService:
    """The process-wide service (created on first use, with the process settings)."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = AssistantService()
    return _service


def reset_service() -> None:
    """Forget the singleton (tests; or after changing settings in a running process)."""
    global _service
    with _service_lock:
        _service = None
