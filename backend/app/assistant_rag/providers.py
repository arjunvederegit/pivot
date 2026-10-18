"""The local model provider — Ollama — and everything that can go wrong talking to it.

Three jobs, each small:

* **`build_chat_model`** constructs LangChain's `ChatOllama` with the settings that matter
  for a small local model answering from retrieved evidence:
    - `temperature=0`: the same question over the same passages should produce the same
      answer. Sampling creativity is a liability when the task is "restate the sources";
    - `reasoning=False`: qwen3.5 is a *thinking* model. Left to its default it writes a long
      hidden chain of thought before answering — about 75 s on a laptop for a 2B model —
      and the thought is discarded anyway. This one flag is the difference between a
      usable demo and a timeout;
    - `num_ctx` / `num_predict`: the context window and the answer budget. Ollama's own
      default context (2048-4096) would silently truncate the prompt's evidence block;
      `prompts.CONTEXT_TOKEN_BUDGET` is computed against these same numbers;
    - `keep_alive="30m"`: keep the model loaded between questions, so the second question
      does not pay a multi-second cold load;
    - `client_kwargs={"timeout": ...}`: an HTTP timeout on the underlying client, so a hung
      server fails the request instead of holding the in-flight slot forever.
  Constructing it does not contact Ollama; the first call does.

* **`probe_ollama`** answers "is the provider ready?" for `GET /assistant/status` without
  generating anything: `GET /api/version` (is the server up?) and `GET /api/tags` (which
  models are pulled?). It never raises — a status endpoint that 500s when the thing it
  reports on is down would be useless.

* **`map_provider_error`** turns the exceptions the stack actually raises into the
  assistant's typed, actionable errors. The ollama client raises a builtin
  `ConnectionError` when nothing listens on the port, `ollama.ResponseError(status_code=404)`
  when a model was never pulled, and lets `httpx` timeouts through. Each becomes a code the
  UI can explain ("start Ollama", "run `ollama pull qwen3.5:2b`") instead of a bare 500.

`ollama`, `httpx` and `langchain_ollama` are imported lazily, so this module imports with
only the base install.
"""

from __future__ import annotations

import importlib.util
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.assistant_rag.errors import (
    AssistantDependencyError,
    AssistantError,
    ModelUnavailableError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.assistant_rag.models import DependenciesStatusOut, ProviderStatusOut

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

#: The optional extra Ask Pivot needs, by importable module name.
REQUIRED_MODULES = ("langchain_core", "langchain_ollama", "langsmith")
INSTALL_HINT = "cd backend && .venv/bin/pip install -e '.[dev,assistant]'"
KEEP_ALIVE = "30m"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
#: Ollama's 404 text: `model "qwen3.5:2b" not found, try pulling it first`.
_MODEL_NOT_FOUND = re.compile(r"""model\s+["']?([^"'\s,]+)["']?\s+not\s+found""", re.IGNORECASE)
_MAX_DETAIL_CHARS = 300


# ----------------------------------------------------------------------------- the model


def build_chat_model(settings: Settings) -> Any:
    """`ChatOllama` configured for grounded, deterministic, bounded answers (see module doc)."""
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise AssistantDependencyError(
            f"Ask Pivot needs the optional assistant dependencies: {INSTALL_HINT}"
        ) from exc
    return ChatOllama(
        model=settings.assistant_chat_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        reasoning=False,
        num_ctx=settings.assistant_num_ctx,
        num_predict=settings.assistant_max_output_tokens,
        keep_alive=KEEP_ALIVE,
        client_kwargs={"timeout": settings.assistant_request_timeout_seconds},
    )


def model_name(chat_model: Any) -> str | None:
    """The model a LangChain chat model wraps (`ChatOllama.model`), if it says."""
    name = getattr(chat_model, "model", None) or getattr(chat_model, "model_name", None)
    return str(name) if name else None


# ------------------------------------------------------------------------------- readiness


def base_url_is_local(base_url: str) -> bool:
    """True when the URL's host is loopback. The assistant is designed for a local model;
    status reports (but does not block) anything else."""
    try:
        host = urlsplit(base_url).hostname
    except ValueError:
        return False
    return (host or "").lower() in LOCAL_HOSTS


def display_url(url: str) -> str:
    """`scheme://host:port` of a URL, for messages, issues and logs — never the rest.

    `OLLAMA_BASE_URL` may carry credentials for an auth proxy (`http://user:pass@host`;
    httpx turns URL userinfo into Basic auth), and every message built here reaches
    `/status`, the UI or a log line. So the userinfo is dropped, and the path, query and
    fragment with it (a token can hide there too). The real URL is still what the client
    connects to; only what is *shown* is reduced."""
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port
    except ValueError:
        return "(an unparseable URL)"
    if not host:
        return "(an unparseable URL)"
    if ":" in host:  # IPv6: urlsplit drops the brackets
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return f"{parts.scheme}://{netloc}" if parts.scheme else netloc


def _strip_latest(name: str) -> str:
    return name[: -len(":latest")] if name.endswith(":latest") else name


def model_available(wanted: str, pulled: list[str]) -> bool:
    """`nomic-embed-text` matches `nomic-embed-text:latest` and vice versa: Ollama treats an
    untagged name as `:latest`, so the suffix is optional on either side."""
    target = _strip_latest(wanted.strip())
    return any(_strip_latest(name.strip()) == target for name in pulled if name)


def _endpoint(path: str) -> str:
    """The Ollama endpoint a request path names (`/api/tags`), without any path the base
    URL put in front of it — `display_url` hides that part too."""
    index = path.rfind("/api/")
    return path[index:] if index >= 0 else "the request"


def _unreachable_message(base_url: str) -> str:
    return f"Ollama is not reachable at {display_url(base_url)}. Start it with 'ollama serve'."


async def probe_ollama(settings: Settings, timeout: float = 1.5) -> ProviderStatusOut:
    """Ask Ollama whether it is up and which models it has. Never raises."""
    base = settings.ollama_base_url.rstrip("/")
    version: str | None = None
    pulled: list[str] = []
    reachable = False
    error: str | None = None
    try:
        import httpx

        async with httpx.AsyncClient(base_url=base, timeout=timeout) as client:
            response = await client.get("/api/version")
            response.raise_for_status()
            reachable = True
            version = str(response.json().get("version") or "") or None
            response = await client.get("/api/tags")
            response.raise_for_status()
            for row in response.json().get("models") or []:
                name = row.get("name") or row.get("model")
                if name:
                    pulled.append(str(name))
    except ImportError:  # pragma: no cover - httpx is a base dependency
        error = "httpx is not installed, so Ollama cannot be probed."
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, it never raises
        error = _probe_error(exc, base, timeout, reachable)
    return ProviderStatusOut(
        name="ollama",
        base_url_is_local=base_url_is_local(base),
        reachable=reachable,
        version=version,
        chat_model=settings.assistant_chat_model,
        chat_model_available=model_available(settings.assistant_chat_model, pulled),
        embedding_model=settings.assistant_embedding_model,
        embedding_model_available=model_available(settings.assistant_embedding_model, pulled),
        error=error,
    )


def _probe_error(exc: Exception, base: str, timeout: float, reachable: bool) -> str:
    import httpx

    shown = display_url(base)
    if isinstance(exc, httpx.TimeoutException):
        return f"Ollama at {shown} did not answer within {timeout:g} s."
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Ollama at {shown} answered {exc.response.status_code} to {_endpoint(exc.request.url.path)}."
    if isinstance(exc, httpx.TransportError) or not reachable:
        return _unreachable_message(base)
    return f"Ollama at {shown} returned a response that could not be read ({type(exc).__name__})."


def dependency_status() -> DependenciesStatusOut:
    """Which parts of the optional `.[assistant]` extra are importable — checked with
    `find_spec`, which locates a module without importing (or executing) it."""
    missing = []
    for module in REQUIRED_MODULES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return DependenciesStatusOut(
        installed=not missing, missing=missing, install_hint=INSTALL_HINT if missing else None
    )


# ---------------------------------------------------------------------------- error mapping


def _detail(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_DETAIL_CHARS else text[: _MAX_DETAIL_CHARS - 1] + "…"


def map_provider_error(
    exc: BaseException, *, settings: Settings | None = None, model: str | None = None
) -> AssistantError | None:
    """The assistant error for a provider exception, or None if `exc` is not one.

    `model` names the model the failing call used, when the caller knows (the pipeline
    does); otherwise the model is read from Ollama's own 404 text. Order matters:
    `httpx.ConnectTimeout` is both a timeout and a transport error, and is reported as a
    timeout.
    """
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    base = settings.ollama_base_url.rstrip("/")
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a base dependency
        httpx = None  # type: ignore[assignment]

    if httpx is not None and isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(
            f"Ollama at {display_url(base)} did not respond in time. A cold model load can take a while; "
            "try again, and check `ollama ps` if it keeps happening."
        )
    if (httpx is not None and isinstance(exc, httpx.TransportError)) or isinstance(exc, ConnectionError):
        return ProviderUnavailableError(_unreachable_message(base))
    if httpx is not None and isinstance(exc, httpx.HTTPStatusError):  # an HTTP error from the server itself
        return ProviderUnavailableError(
            f"Ollama at {display_url(base)} answered {exc.response.status_code} to {_endpoint(exc.request.url.path)}."
        )

    try:
        from ollama import ResponseError
    except ImportError:  # pragma: no cover - depends on the optional extra
        return None
    if isinstance(exc, ResponseError):
        detail = _detail(getattr(exc, "error", "") or str(exc))
        if getattr(exc, "status_code", None) == 404:
            match = _MODEL_NOT_FOUND.search(detail)
            name = match.group(1) if match else model
            if name:
                return ModelUnavailableError(
                    f"Ollama does not have the model '{name}'. Pull it with: ollama pull {name}"
                )
            return ModelUnavailableError(
                f"Ollama could not find a required model ({detail}). Pull both with: "
                f"ollama pull {settings.assistant_chat_model} && "
                f"ollama pull {settings.assistant_embedding_model}"
            )
        return ProviderUnavailableError(f"Ollama returned an error: {detail}")
    return None
