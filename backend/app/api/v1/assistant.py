"""Ask Pivot endpoints — the experimental research assistant over a FICTIONAL corpus.

    GET  /api/v1/assistant/status     readiness: feature flag, packages, corpus, Ollama, tracing
    GET  /api/v1/assistant/context    the fictional teams, players, priorities and starters
    POST /api/v1/assistant/ask        one question -> a cited, checked, labelled answer

Everything behind these routes is `synthetic_demo`: an invented league, invented players
and invented scouting reports. Nothing here reads Pivot's NBA database, the analytics
engines, the CBA engine or the tools registry, and nothing writes anything.

Three guards, in this order:

1. **feature flag** — with `ASSISTANT_ENABLED` off, `/ask` and `/context` are a 404,
   `/status` says only `enabled: false`, and `app.main` leaves all three out of
   `/openapi.json`;
2. **loopback only** — `/ask` is unauthenticated and costs real compute, and `/status` and
   `/context` describe the local setup, so with the feature on (and unless
   `ASSISTANT_ALLOW_REMOTE` is set) all three answer only a request that is loopback in
   every way this server can see:
   - the socket peer, and every address in `X-Forwarded-For` for a proxy that sets it.
     The Next.js rewrite proxy does **not**: it adds no `X-Forwarded-For`, so a request
     it forwards arrives from 127.0.0.1 whoever sent it. A LAN visitor is kept out by
     binding the dev server to loopback (`make assistant-dev` runs
     `next dev --hostname 127.0.0.1`), not by this check;
   - the `Host` header, and every `X-Forwarded-Host` (which the Next proxy sets to the
     host the browser used). They must name localhost, `*.localhost`, 127.0.0.0/8 or
     ::1. This is what stops DNS rebinding: a page on `evil.example` that re-points its
     own name at 127.0.0.1 reaches the server from a loopback peer, but its requests
     still say `Host: evil.example`;
3. **request bounds** — the request model forbids unknown keys and bounds every field (a
   422, never a silent truncation); the service then enforces the in-flight limit, the
   timeout and cancellation on disconnect.

Cancellation needs one piece of plumbing here. `app.main`'s observability middleware is a
Starlette `BaseHTTPMiddleware`, and behind one `Request.is_disconnected()` never reports a
disconnect (it reads `receive` inside an already-cancelled scope, and the middleware's
`receive` wrapper always loses that race). So `AskDisconnectMiddleware`, registered
outermost, watches the server's own `receive` for `http.disconnect` once the body has been
read and records it on the scope, and `/ask` hands the service a check that reads it.

The service — and with it LangChain — is imported inside the handlers, so `from app.main
import app` works on the base install without the optional `.[assistant]` extra.
"""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.assistant_rag.errors import AssistantDisabledError, AssistantLocalOnlyError
from app.assistant_rag.models import AskRequest, AskResponse, ContextOut, StatusOut
from app.config import get_settings

router = APIRouter(prefix="/assistant", tags=["assistant"])

LOCAL_NAMES = frozenset({"localhost"})
#: Where `AskDisconnectMiddleware` records the client's disconnect (an `asyncio.Event`).
DISCONNECT_SCOPE_KEY = "pivot.assistant.disconnected"


def is_loopback(host: str | None) -> bool:
    """`127.0.0.1`, `::1`, `::ffff:127.0.0.1` (IPv4-mapped) and `localhost` are loopback;
    anything unparseable — including a missing peer — is not (fail closed)."""
    if not host:
        return False
    value = host.strip().strip("[]")
    if value.lower() in LOCAL_NAMES:
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped is not None and mapped.is_loopback))


def is_loopback_host(value: str | None) -> bool:
    """Whether a `Host` / `X-Forwarded-Host` value names this machine: `localhost` or any
    `*.localhost` (browsers resolve those to loopback themselves, so no DNS answer can
    re-point them), 127.0.0.0/8 or ::1 — with or without a port. Anything else, an empty
    or missing value, a malformed port, fails closed."""
    if not value:
        return False
    text = value.strip().lower()
    if text.startswith("["):  # [::1] or [::1]:3000
        name, bracket, rest = text[1:].partition("]")
        if not bracket or (rest and not (rest.startswith(":") and rest[1:].isdigit())):
            return False
    elif text.count(":") == 1:  # name:port or v4:port
        name, _, port = text.partition(":")
        if not port.isdigit():
            return False
    else:  # a bare name, a bare IPv4 address, or a bare IPv6 address
        name = text
    name = name.rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    return is_loopback(name)


def require_enabled() -> None:
    if not get_settings().assistant_enabled:
        raise AssistantDisabledError("Ask Pivot is not enabled on this server.")


def require_local_client(request: Request) -> None:
    """Refuse every assistant route to anything that is not loopback (see module doc).

    With the feature off there is nothing to guard — `/ask` and `/context` are already
    refused by the flag, which runs first, and `/status` says only `enabled: false` — so
    the check applies only while it is on."""
    settings = get_settings()
    if not settings.assistant_enabled or settings.assistant_allow_remote:
        return
    hops = [request.client.host if request.client else None]
    for header in request.headers.getlist("x-forwarded-for"):
        hops += [part.strip() for part in header.split(",")]
    hosts = [request.headers.get("host")]
    for header in request.headers.getlist("x-forwarded-host"):
        hosts += [part.strip() for part in header.split(",")]
    if not (all(is_loopback(hop) for hop in hops) and all(is_loopback_host(host) for host in hosts)):
        raise AssistantLocalOnlyError(
            "Ask Pivot answers loopback clients only (it runs a local model without authentication). "
            "Open the app on this machine as localhost or 127.0.0.1, or set ASSISTANT_ALLOW_REMOTE=true "
            "for a trusted network."
        )


class AskDisconnectMiddleware:
    """Record the client's disconnect from `POST /ask` on the scope (see module doc).

    Pure ASGI, and registered outermost so it sees the server's own `receive`. Every other
    request — and every message up to the end of the `/ask` body — passes through
    untouched. Once the body is complete, a watcher task reads the server's `receive`
    until `http.disconnect` and sets `scope[DISCONNECT_SCOPE_KEY]`. From then on the
    downstream `receive` must not read the server's directly (the watcher does), so it
    waits for that event and returns the disconnect — what a server's `receive` does
    after the body anyway."""

    def __init__(self, app: ASGIApp, *, path: str) -> None:
        self.app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != self.path:
            await self.app(scope, receive, send)
            return
        gone = asyncio.Event()
        scope[DISCONNECT_SCOPE_KEY] = gone
        body_done = False
        watcher: asyncio.Task[None] | None = None

        async def watch() -> None:
            try:
                while (await receive())["type"] != "http.disconnect":
                    pass
            except Exception:  # noqa: BLE001 - cannot tell; stop watching rather than guess
                return
            gone.set()

        async def receive_through() -> Message:
            nonlocal body_done, watcher
            if body_done:
                await gone.wait()
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.disconnect":
                body_done = True
                gone.set()
            elif message["type"] == "http.request" and not message.get("more_body", False):
                body_done = True
                watcher = asyncio.ensure_future(watch())
            return message

        try:
            await self.app(scope, receive_through, send)
        finally:
            if watcher is not None:
                watcher.cancel()


def _disconnect_check(request: Request) -> Callable[[], Awaitable[bool]]:
    """What the service polls to learn the client went away: the event the middleware
    records, or — without the middleware (a bare router in tests) — Starlette's own check,
    which works when no `BaseHTTPMiddleware` sits in between."""
    recorded = request.scope.get(DISCONNECT_SCOPE_KEY)
    if not isinstance(recorded, asyncio.Event):
        return request.is_disconnected
    gone = recorded

    async def is_disconnected() -> bool:
        return gone.is_set()

    return is_disconnected


@router.get("/status", response_model=StatusOut, dependencies=[Depends(require_local_client)])
async def get_assistant_status() -> StatusOut:
    """Whether Ask Pivot can answer right now, and the exact fix for anything that stops it.

    Always routed. With the feature disabled it reports only that, to any caller, without
    probing anything. With it enabled it answers loopback clients only and reports the
    optional packages, the fictional corpus, the local Ollama server and both models, the
    retrieval index and whether LangSmith tracing is on (`api_key_configured` is a boolean;
    the key itself is never returned, and the Ollama URL is shown without credentials).
    """
    from app.assistant_rag.service import get_service

    return await get_service().status()


@router.get(
    "/context",
    response_model=ContextOut,
    dependencies=[Depends(require_enabled), Depends(require_local_client)],
)
def get_assistant_context() -> ContextOut:
    """The fictional league the UI's controls are built from: selectable teams with their
    documented default priorities, players, priority dimensions and starter questions."""
    from app.assistant_rag.service import get_service

    return get_service().context()


@router.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(require_enabled), Depends(require_local_client)],
)
async def ask_assistant(payload: AskRequest, request: Request) -> AskResponse:
    """Answer one research question from the fictional scouting corpus.

    The reply carries every retrieved source with its excerpt and provenance, which ones
    the answer cites, the deterministic checks' warnings (invalid citations, numbers that
    contradict the fact manifest, legality verdicts, invented scores), what the sandbox
    cannot answer, and the context it used. Legality, salary and numeric fit-score requests
    are answered by rules, not the model, and say so. Errors use Pivot's envelope:
    `assistant_busy` and `provider_unavailable` / `model_unavailable` (503),
    `provider_timeout` (504), `assistant_local_only` (403), `validation_error` (422).
    """
    from app.assistant_rag.service import get_service

    return await get_service().ask(
        payload,
        request_id=getattr(request.state, "request_id", None),
        is_disconnected=_disconnect_check(request),
    )
