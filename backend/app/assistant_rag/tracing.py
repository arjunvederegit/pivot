"""Opt-in LangSmith tracing for Ask Pivot — off unless Settings say otherwise.

What a trace is. Every LangChain runnable that executes produces a *run*: a record of its
name, inputs, outputs, timing and error. Runs nest — the pipeline's root run contains one
child per stage (`query_context`, `entity_resolution`, ..., `final_response`), and the
`model_call` stage contains the prompt template and chat-model runs — so one question
produces a *run tree*. LangSmith stores and displays that tree, which is how you answer
"why did it cite S12?": open the `retrieval` run and read what it returned, then the
`prompt_construction` run and read exactly what the model was shown.

What metadata does. Key/value pairs attached to the root run and inherited by every child
(corpus version, prompt and retrieval variants, model names, team, mode, request id). They
are how traces are filtered and grouped later — "every `basic`-prompt run against corpus
2026.10-v2.0" — and how a trace is matched to a server log line (`request_id`). Tags do the
same job as a flat list of labels.

How this module decides. LangChain decides whether to trace from LangSmith's context
variables first and the environment (`LANGSMITH_TRACING`, `LANGCHAIN_TRACING_V2`) second.
Pivot's rule is that *Settings* decide, and Settings read Pivot's own switch
(`ASSISTANT_TRACING`), never the SDK's — so the SDK variables a developer exports for other
projects can neither turn Ask Pivot's tracing on nor choose where its runs go:

* inactive (the default): the run is wrapped in `tracing_context(enabled=False)`. The
  context variable outranks the environment, so a `LANGSMITH_TRACING=true` exported in a
  developer's shell — with or without a `LANGSMITH_API_KEY` — cannot switch tracing on;
* active (`ASSISTANT_TRACING=true` *and* a `LANGSMITH_API_KEY`): one `langsmith.Client` is
  created lazily — never at import, never in tests — and the run is wrapped in
  `tracing_context(enabled=True, client=..., project_name=..., metadata=..., tags=...)`.
  The project is always named (`ASSISTANT_LANGSMITH_PROJECT`, default
  `pivot-intelligence-v2`), because a missing one would let the SDK fall back to the
  shell's `LANGSMITH_PROJECT`. The root run id is chosen *here*, before the run starts, and
  handed to LangChain as the invoke config's `run_id`, so the response can name its trace
  without waiting for the upload (runs are sent in the background by the client).

PRIVACY. Inference is local, but tracing is not. With tracing on, every traced run — the
GM's question and conversation, the retrieved (fictional) evidence, the full prompt and the
model's answer — is sent to LangSmith's servers (or `LANGSMITH_ENDPOINT`). The corpus is
synthetic, but what a user types is theirs. That is why tracing is off by default and why
`GET /assistant/status` reports whether it is on.
"""

from __future__ import annotations

import threading
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.assistant_rag.models import TraceOut
from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

logger = get_logger(__name__)

_lock = threading.Lock()
#: (api_key, endpoint) -> the one Client for those settings. Kept in memory only.
_client_cache: dict[tuple[str, str], Any] = {}
#: project name -> project id, so building a run URL costs one cheap lookup per process.
_project_ids: dict[str, Any] = {}


@dataclass(frozen=True)
class TraceHandle:
    """What the response says about its trace. `run_id` is the root run's id."""

    enabled: bool
    project: str | None
    run_id: str | None
    url: str | None = None

    def to_out(self, url: str | None = None) -> TraceOut:
        return TraceOut(enabled=self.enabled, project=self.project, run_id=self.run_id, url=url or self.url)


DISABLED = TraceHandle(enabled=False, project=None, run_id=None)


DEFAULT_PROJECT = "pivot-intelligence-v2"


def tracing_active(settings: Settings) -> bool:
    """Tracing runs only when Pivot's own switch (`ASSISTANT_TRACING`) is on *and* there is
    a key to send with."""
    return bool(settings.langsmith_tracing and settings.langsmith_api_key)


def project_name(settings: Settings) -> str:
    """The LangSmith project Ask Pivot traces into. Never empty: an empty name would hand
    the choice back to the SDK, which reads the shell's `LANGSMITH_PROJECT`."""
    return settings.langsmith_project or DEFAULT_PROJECT


def _client(settings: Settings) -> Any:
    """The process's LangSmith client for these settings, created on first use."""
    key = (settings.langsmith_api_key, settings.langsmith_endpoint or "")
    with _lock:
        client = _client_cache.get(key)
        if client is None:
            from langsmith import Client

            client = Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint or None)
            _client_cache.clear()  # settings changed: drop the old client, keep one
            _client_cache[key] = client
        return client


@contextmanager
def _disabled() -> Iterator[TraceHandle]:
    try:
        from langsmith.run_helpers import tracing_context
    except ImportError:  # nothing installed that could trace
        yield DISABLED
        return
    with tracing_context(enabled=False):
        yield DISABLED


@contextmanager
def tracing_scope(settings: Settings, *, metadata: dict[str, Any], tags: list[str]) -> Iterator[TraceHandle]:
    """Run the enclosed block traced or untraced, exactly as Settings say (module doc).

    A tracing failure never fails an answer: if the client cannot be created, the block
    runs untraced and a warning is logged (without the key)."""
    if not tracing_active(settings):
        with _disabled() as handle:
            yield handle
        return
    try:
        from langsmith.run_helpers import tracing_context

        client = _client(settings)
    except Exception as exc:  # noqa: BLE001 - tracing is optional; answering is not
        logger.warning("LangSmith tracing unavailable, running untraced: %s", type(exc).__name__)
        with _disabled() as handle:
            yield handle
        return
    project = project_name(settings)
    handle = TraceHandle(enabled=True, project=project, run_id=str(uuid.uuid4()))
    with tracing_context(enabled=True, client=client, project_name=project, metadata=metadata, tags=tags):
        yield handle


def trace_url(settings: Settings, handle: TraceHandle) -> str | None:
    """The LangSmith UI link for a traced run, or None.

    Building the link needs the project's id and the workspace id, which only the server
    knows: one lookup each, cached per process. It is best effort — the project may not
    exist until the first run has been uploaded — so any failure returns None. Call it off
    the event loop (the client is synchronous)."""
    if not handle.enabled or handle.run_id is None:
        return None
    try:
        client = _client(settings)
        run = SimpleNamespace(id=uuid.UUID(handle.run_id))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # get_run_url is deprecated
            project_id = _project_ids.get(handle.project or "")
            if project_id is None and handle.project:
                project_id = client.read_project(project_name=handle.project).id
                _project_ids[handle.project] = project_id
            return str(client.get_run_url(run=run, project_id=project_id))  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - a missing link is not an error
        return None


def reset_tracing_state() -> None:
    """Forget the cached client and project ids (tests)."""
    with _lock:
        _client_cache.clear()
        _project_ids.clear()
