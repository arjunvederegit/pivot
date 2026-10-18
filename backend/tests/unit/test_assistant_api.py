"""The Ask Pivot HTTP surface, through the real app (`from app.main import app`).

The service behind the routes is the real `AssistantService` built with fakes (see
`test_assistant_service.make_service`) and installed by monkeypatching `get_service`; the
loopback guard is overridden with `app.dependency_overrides` except in the tests about it,
which address the app as `localhost` (the guard checks the `Host` header too). Settings are
pinned per test on the process settings object, so a developer's local `.env` cannot change
what these tests see. Nothing contacts Ollama or LangSmith.

The client-disconnect tests drive the real `app.main.app` through raw ASGI, with a
`receive` that behaves like uvicorn's: the body, then — when the client goes away —
`http.disconnect`. That is the only way to cover the path through `app.main`'s
`BaseHTTPMiddleware`, which hides the disconnect from `Request.is_disconnected()`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import Message

from app.api.v1.assistant import (
    DISCONNECT_SCOPE_KEY,
    AskDisconnectMiddleware,
    is_loopback,
    is_loopback_host,
    require_local_client,
)
from app.assistant_rag import providers
from app.assistant_rag import service as service_module
from app.assistant_rag.errors import AssistantLocalOnlyError
from app.assistant_rag.models import (
    MAX_HISTORY_MESSAGES,
    MAX_QUESTION_CHARS,
    DependenciesStatusOut,
    ProviderStatusOut,
)
from app.assistant_rag.pipeline import STAGES
from app.config import Settings, get_settings
from app.core.errors import DomainError, domain_error_handler
from tests.unit.test_assistant_pipeline import GROUNDED_QUESTION, LEGALITY_QUESTION
from tests.unit.test_assistant_service import ANSWER, _eventually, healthy_probe, make_service

HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None
BACKEND = Path(__file__).resolve().parents[2]
ASK = "/api/v1/assistant/ask"
STATUS = "/api/v1/assistant/status"
CONTEXT = "/api/v1/assistant/context"
LOCAL = "http://localhost:8000"  # the backend as the Next proxy addresses it


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = get_settings()
    pinned = {
        "assistant_enabled": False,
        "assistant_allow_remote": False,
        "assistant_dev_options": False,
        "assistant_max_in_flight": 1,
        "assistant_request_timeout_seconds": 30.0,
        "langsmith_tracing": False,
        "langsmith_api_key": "",
        "langsmith_project": "pivot-intelligence-v2",
        "langsmith_endpoint": "",
        "ollama_base_url": "http://localhost:11434",
    }
    for name, value in pinned.items():
        monkeypatch.setattr(s, name, value)
    monkeypatch.setattr(
        providers, "dependency_status", lambda: DependenciesStatusOut(installed=True, missing=[], install_hint=None)
    )
    return s


@pytest.fixture()
def enabled(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(settings, "assistant_enabled", True)
    return settings


@pytest.fixture()
def install(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """Install a fake-backed service as the one the routes use."""

    def _install(**kwargs: Any) -> Any:
        svc, chains = make_service(settings, **kwargs)
        monkeypatch.setattr(service_module, "get_service", lambda: svc)
        return svc

    return _install


@pytest.fixture()
def app():
    from app.main import _request_log, app

    _request_log.clear()
    yield app
    app.dependency_overrides.pop(require_local_client, None)


@pytest.fixture()
def client(app):
    app.dependency_overrides[require_local_client] = lambda: None
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def loopback_client(app):
    """No guard override; the socket peer is 127.0.0.1 and the Host is localhost:8000."""
    with TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def error_of(response: httpx.Response) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}, body
    return body["error"]


# ----------------------------------------------------------------------- app and status


def test_app_imports_and_existing_routes_are_unaffected(client: TestClient) -> None:
    assert client.get("/").json()["name"] == "Pivot API"
    assert client.get("/api/v1/health").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/intelligence/vocabulary" in paths


#: Run in a fresh interpreter: what the schema lists depends on the flag `app.main` read at import.
_SCHEMA_PROBE = (
    "import json; from fastapi.testclient import TestClient; from app.main import app; "
    "c = TestClient(app); "
    "print(json.dumps({'paths': sorted(c.get('/openapi.json').json()['paths']), "
    "'status': c.get('/api/v1/assistant/status').status_code}))"
)


@pytest.mark.parametrize("flag", ["false", "true"])
def test_the_schema_lists_the_assistant_routes_only_when_enabled(flag: str) -> None:
    # An exported variable outranks any .env file, so the flag is exactly this one.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LANGSMITH_", "LANGCHAIN_", "ASSISTANT_"))}
    env["ASSISTANT_ENABLED"] = flag
    done = subprocess.run(
        [sys.executable, "-c", _SCHEMA_PROBE], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout.strip().splitlines()[-1])
    paths = set(result["paths"])
    assert "/api/v1/intelligence/vocabulary" in paths
    assert {ASK, STATUS, CONTEXT} & paths == (set() if flag == "false" else {ASK, STATUS, CONTEXT})
    if flag == "false":
        assert result["status"] == 200  # unlisted, but /status still answers `enabled: false`


def test_status_when_disabled_is_minimal_and_probes_nothing(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_probe(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a disabled assistant must not probe Ollama")

    monkeypatch.setattr(providers, "probe_ollama", no_probe)
    service_module.reset_service()
    try:
        response = client.get(STATUS)  # the real, unpatched service singleton
    finally:
        service_module.reset_service()
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False and body["ready"] is False
    assert body["issues"] == [service_module.DISABLED_ISSUE]
    assert body["provider"] is None and body["corpus"] is None and body["data_mode"] == "synthetic_demo"


def test_status_when_enabled_reports_the_probe_and_never_the_key(
    client: TestClient, enabled: Settings, install: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "lsv2_pt_API-TEST-SECRET"
    monkeypatch.setattr(enabled, "langsmith_tracing", True)
    monkeypatch.setattr(enabled, "langsmith_api_key", secret)

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    install()
    response = client.get(STATUS)
    assert response.status_code == 200 and secret not in response.text
    body = response.json()
    assert body["enabled"] and body["ready"] and body["issues"] == []
    assert body["provider"]["reachable"] and body["provider"]["version"] == "0.40.5"
    assert body["tracing"] == {"enabled": True, "api_key_configured": True, "project": enabled.langsmith_project}


def test_context_is_404_when_disabled_and_the_catalog_when_enabled(
    client: TestClient, settings: Settings, install: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    install()
    response = client.get(CONTEXT)
    assert response.status_code == 404 and error_of(response)["code"] == "assistant_disabled"
    monkeypatch.setattr(settings, "assistant_enabled", True)
    body = client.get(CONTEXT).json()
    assert body["data_mode"] == "synthetic_demo" and any(t["id"] == "COMETS" for t in body["teams"])


# --------------------------------------------------------------------------------- ask


def test_ask_is_404_when_disabled_even_for_an_invalid_body(client: TestClient, settings: Settings) -> None:
    response = client.post(ASK, json={"question": "x", "unknown": 1}, headers={"x-request-id": "rid-404"})
    assert response.status_code == 404
    assert error_of(response) == {
        "code": "assistant_disabled",
        "message": "Ask Pivot is not enabled on this server.",
        "request_id": "rid-404",
    }


def test_full_ask_returns_a_labelled_synthetic_answer(client: TestClient, enabled: Settings, install: Any) -> None:
    install()
    response = client.post(ASK, json={"question": GROUNDED_QUESTION}, headers={"x-request-id": "rid-ok"})
    assert response.status_code == 200 and response.headers["x-request-id"] == "rid-ok"
    body = response.json()
    assert body["data_mode"] == "synthetic_demo" and body["label"] == "FICTIONAL DATA / SANDBOX"
    assert body["answer"] == ANSWER and body["answer_kind"] == "grounded_answer"
    assert body["request_id"] == "rid-ok" and body["cited_source_ids"] == ["S05"]
    assert all(s["data_mode"] == "synthetic_demo" for s in body["sources"])


def test_legality_question_is_an_unsupported_request(client: TestClient, enabled: Settings, install: Any) -> None:
    install()
    body = client.post(ASK, json={"question": LEGALITY_QUESTION, "mode": "roster_fit", "team_id": "COMETS"}).json()
    assert body["answer_kind"] == "unsupported_request" and body["cited_source_ids"] == ["S31"]
    assert body["diagnostics"]["model_called"] is False


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"question": "Who defends?", "priorites": {"spacing": "high"}}, "priorites"),
        ({"question": "x" * (MAX_QUESTION_CHARS + 1)}, "question"),
        ({"question": "Who?", "history": [{"role": "user", "content": "hi"}] * (MAX_HISTORY_MESSAGES + 1)}, "history"),
        ({"question": "   "}, "question"),
        ({"question": "Who?", "options": {"k": 50}}, "options.k"),
    ],
)
def test_request_bounds_are_422(client: TestClient, enabled: Settings, install: Any, payload: dict[str, Any], field: str) -> None:
    install()
    response = client.post(ASK, json=payload)
    assert response.status_code == 422
    error = error_of(response)
    assert error["code"] == "validation_error" and any(f["field"].startswith(field) for f in error["fields"])


def test_unknown_team_and_disallowed_dev_options_are_422(client: TestClient, enabled: Settings, install: Any) -> None:
    install()
    team = client.post(ASK, json={"question": "Who fits?", "team_id": "NOPE"})
    assert team.status_code == 422 and "Unknown team_id 'NOPE'" in error_of(team)["message"]
    options = client.post(ASK, json={"question": GROUNDED_QUESTION, "options": {"prompt_variant": "basic"}})
    assert options.status_code == 422 and "ASSISTANT_DEV_OPTIONS" in error_of(options)["message"]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (httpx.ConnectError("refused"), 503, "provider_unavailable"),
        (httpx.ReadTimeout("slow"), 504, "provider_timeout"),
        (RuntimeError("internal detail"), 500, "assistant_internal_error"),
    ],
)
def test_failures_use_the_error_envelope(
    client: TestClient, enabled: Settings, install: Any, error: Exception, status: int, code: str
) -> None:
    install(error=error)
    response = client.post(ASK, json={"question": GROUNDED_QUESTION}, headers={"x-request-id": "rid-err"})
    assert response.status_code == status
    body = error_of(response)
    assert body["code"] == code and body["request_id"] == "rid-err"
    assert "internal detail" not in body["message"]


def test_missing_model_is_503_model_unavailable(client: TestClient, enabled: Settings, install: Any) -> None:
    ollama = pytest.importorskip("ollama")
    install(error=ollama.ResponseError('model "qwen3.5:2b" not found, try pulling it first', 404))
    response = client.post(ASK, json={"question": GROUNDED_QUESTION})
    assert response.status_code == 503 and error_of(response)["code"] == "model_unavailable"
    assert "ollama pull qwen3.5:2b" in error_of(response)["message"]


def test_timeout_is_504(client: TestClient, enabled: Settings, install: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enabled, "assistant_request_timeout_seconds", 0.05)
    install(delay=5.0)
    response = client.post(ASK, json={"question": GROUNDED_QUESTION})
    assert response.status_code == 504 and error_of(response)["code"] == "provider_timeout"


def test_a_second_concurrent_ask_is_503_busy(app: Any, enabled: Settings, install: Any) -> None:
    install(delay=0.3)

    async def both() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 40000))
        async with httpx.AsyncClient(transport=transport, base_url=LOCAL) as http:
            first = asyncio.create_task(http.post(ASK, json={"question": GROUNDED_QUESTION}))
            await asyncio.sleep(0.05)  # let the first request take the slot
            second = await http.post(ASK, json={"question": GROUNDED_QUESTION})
            return [await first, second]

    first, second = asyncio.run(both())
    assert first.status_code == 200
    assert second.status_code == 503 and error_of(second)["code"] == "assistant_busy"


# ------------------------------------------------------------------------ loopback guard


def test_loopback_detection() -> None:
    for host in ("127.0.0.1", "::1", "localhost", "LOCALHOST", "::ffff:127.0.0.1", "[::1]", " 127.0.0.2 "):
        assert is_loopback(host), host
    for host in (None, "", "testclient", "192.168.1.20", "10.0.0.1", "::ffff:10.0.0.1", "2001:db8::1", "unknown"):
        assert not is_loopback(host), host


def test_host_detection() -> None:
    for host in (
        "localhost", "LOCALHOST:3000", "localhost.", "app.localhost:3000", "127.0.0.1", "127.0.0.1:8000",
        "127.8.9.10:3000", "[::1]", "[::1]:3000", "::1",
    ):
        assert is_loopback_host(host), host
    for other in (
        None, "", "testserver", "evil.example", "evil.example:3000", "localhost.evil.example", "127.0.0.1.nip.io",
        "localhost:", "localhost:abc", "192.168.4.99:3000", "0.0.0.0:3000", "[::1", "[::1]x", "[2001:db8::1]:3000",
    ):
        assert not is_loopback_host(other), other


def _request(
    peer: str | None,
    forwarded: list[str] | None = None,
    *,
    host: str | None = "localhost:8000",
    forwarded_host: list[str] | None = None,
) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in forwarded or []]
    headers += [(b"x-forwarded-host", value.encode()) for value in forwarded_host or []]
    if host is not None:
        headers.append((b"host", host.encode()))
    scope = {"type": "http", "method": "POST", "path": ASK, "headers": headers, "client": (peer, 1) if peer else None}
    return Request(scope)


def test_require_local_client_checks_every_hop_and_every_host(enabled: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    require_local_client(_request("127.0.0.1"))
    require_local_client(_request("::1", ["127.0.0.1, ::1"]))
    require_local_client(_request("127.0.0.1", host="127.0.0.1:8000", forwarded_host=["localhost:3000"]))
    for request in (
        _request("192.168.1.20"),
        _request("127.0.0.1", ["192.168.1.20"]),
        _request("127.0.0.1", ["127.0.0.1", "203.0.113.9, 127.0.0.1"]),
        _request("127.0.0.1", ["garbage"]),
        _request(None),
        _request("127.0.0.1", host="evil.example:8000"),  # DNS rebinding, straight to the backend
        _request("127.0.0.1", forwarded_host=["evil.example:3000"]),  # ... and through the Next proxy
        _request("127.0.0.1", forwarded_host=["localhost:3000, evil.example"]),
        _request("127.0.0.1", host=None),
    ):
        with pytest.raises(AssistantLocalOnlyError):
            require_local_client(request)
    monkeypatch.setattr(enabled, "assistant_allow_remote", True)
    require_local_client(_request("192.168.1.20", ["203.0.113.9"], host="pivot.lan", forwarded_host=["pivot.lan"]))


def test_with_the_feature_off_the_guard_has_nothing_to_guard(settings: Settings) -> None:
    # /ask and /context are refused by the flag first; /status says only `enabled: false`.
    require_local_client(_request("192.168.1.20", host="evil.example"))


def test_a_proxy_that_forwards_a_non_loopback_address_is_403(
    loopback_client: TestClient, enabled: Settings, install: Any
) -> None:
    install()
    refused = loopback_client.post(ASK, json={"question": GROUNDED_QUESTION}, headers={"x-forwarded-for": "192.168.1.20"})
    assert refused.status_code == 403 and error_of(refused)["code"] == "assistant_local_only"
    allowed = loopback_client.post(ASK, json={"question": GROUNDED_QUESTION}, headers={"x-forwarded-for": "127.0.0.1, ::1"})
    assert allowed.status_code == 200


def test_the_next_proxy_shape_is_judged_by_the_browsers_host(
    loopback_client: TestClient, enabled: Settings, install: Any
) -> None:
    """Next's rewrite proxy sends no X-Forwarded-For — every request it forwards comes from
    127.0.0.1 — and sets X-Forwarded-Host to the host the browser used. So the address
    cannot tell a LAN browser apart (binding `next dev` to 127.0.0.1 does that); the
    forwarded host is what refuses a rebinding page, or a browser on the LAN address."""
    install()

    def via_next(browser_host: str) -> httpx.Response:
        return loopback_client.post(ASK, json={"question": GROUNDED_QUESTION}, headers={"x-forwarded-host": browser_host})

    assert via_next("localhost:3000").status_code == 200
    assert via_next("127.0.0.1:3000").status_code == 200
    for browser_host in ("evil.example:3000", "192.168.4.99:3000"):
        refused = via_next(browser_host)
        assert refused.status_code == 403 and error_of(refused)["code"] == "assistant_local_only", browser_host


def test_a_rebinding_page_is_refused_on_every_assistant_route(
    app: Any, enabled: Settings, install: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    install()

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    origin = {"origin": "http://evil.example:8000"}
    with TestClient(app, base_url="http://evil.example:8000", client=("127.0.0.1", 5555)) as rebound:
        for response in (
            rebound.post(ASK, json={"question": GROUNDED_QUESTION}, headers=origin),
            rebound.get(STATUS, headers=origin),
            rebound.get(CONTEXT, headers=origin),
        ):
            assert response.status_code == 403 and error_of(response)["code"] == "assistant_local_only"
    with TestClient(app, base_url=LOCAL, client=("127.0.0.1", 5555)) as local:
        assert local.get(STATUS).status_code == 200 and local.get(CONTEXT).status_code == 200


def test_status_and_context_are_loopback_only_while_enabled(
    app: Any, settings: Settings, install: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    install()
    with TestClient(app) as remote:  # TestClient's peer is "testclient", not loopback
        disabled = remote.get(STATUS)
        assert disabled.status_code == 200 and disabled.json()["enabled"] is False  # reveals only that
        assert error_of(remote.get(CONTEXT))["code"] == "assistant_disabled"  # the flag answers first
        monkeypatch.setattr(settings, "assistant_enabled", True)
        for response in (remote.get(STATUS), remote.get(CONTEXT)):
            assert response.status_code == 403 and error_of(response)["code"] == "assistant_local_only"


def test_a_non_loopback_peer_is_403_unless_remote_is_allowed(
    app: Any, enabled: Settings, install: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    install()
    with TestClient(app) as remote:  # TestClient's peer is "testclient", not loopback
        assert remote.post(ASK, json={"question": GROUNDED_QUESTION}).status_code == 403
        monkeypatch.setattr(enabled, "assistant_allow_remote", True)
        assert remote.post(ASK, json={"question": GROUNDED_QUESTION}).status_code == 200


# ------------------------------------------------------------------ client disconnect


def _scope(path: str = ASK, method: str = "POST", body: bytes = b"") -> dict[str, Any]:
    """An HTTP scope shaped like uvicorn's (spec 2.3), from a loopback peer to localhost."""
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"localhost:8000"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 51000),
        "server": ("127.0.0.1", 8000),
    }


async def _raw_ask(asgi_app: Any, body: bytes, *, disconnect_after: float | None) -> tuple[list[Message], float]:
    """One POST /ask delivered the way uvicorn delivers it: the body, then a `receive` that
    blocks until the client goes away — `disconnect_after` seconds in, or never — and,
    once it has gone, returns `http.disconnect` at once, without awaiting anything."""
    sent: list[Message] = []
    delivered = False
    started = time.perf_counter()

    async def receive() -> Message:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect_after is None:
            await asyncio.Event().wait()  # never set: this client stays
        else:
            remaining = started + disconnect_after - time.perf_counter()
            if remaining > 0:
                await asyncio.sleep(remaining)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await asgi_app(_scope(body=body), receive, send)
    return sent, time.perf_counter() - started


def _status_and_body(sent: list[Message]) -> tuple[int, dict[str, Any]]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return int(start["status"]), json.loads(body)


def test_stop_through_the_real_app_cancels_the_answer_and_frees_the_slot(
    app: Any, enabled: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GM presses Stop 0.3 s into a 5 s answer. Through `app.main.app` — whose
    observability middleware is a BaseHTTPMiddleware — the chain must be cancelled, the
    request must end as 499 `request_cancelled`, and the next question must not be busy."""
    svc, chains = make_service(enabled, delay=5.0)
    monkeypatch.setattr(service_module, "get_service", lambda: svc)
    body = json.dumps({"question": GROUNDED_QUESTION}).encode()

    async def stop_then_ask_again() -> tuple[tuple[list[Message], float], tuple[list[Message], float]]:
        stopped = await _raw_ask(app, body, disconnect_after=0.3)
        chains[0].delay = 0.0  # the retry answers at once
        return stopped, await _raw_ask(app, body, disconnect_after=None)

    (sent, elapsed), (sent_again, _) = asyncio.run(stop_then_ask_again())
    assert elapsed < 2.0, f"the abandoned answer ran for {elapsed:.1f} s"
    status, payload = _status_and_body(sent)
    assert status == 499 and payload["error"]["code"] == "request_cancelled"
    assert _eventually(lambda: chains[0].cancelled), "the work on the model loop must be cancelled"
    assert not svc._slots.locked()
    status_again, payload_again = _status_and_body(sent_again)
    assert status_again == 200, payload_again  # not 503 assistant_busy
    assert payload_again["answer"] == ANSWER


def test_without_the_middleware_the_route_falls_back_to_starlettes_check(
    enabled: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare router (no BaseHTTPMiddleware in between) still notices the disconnect."""
    from app.api.v1.assistant import router

    bare = FastAPI()
    bare.include_router(router, prefix="/api/v1")
    bare.add_exception_handler(DomainError, domain_error_handler)
    svc, chains = make_service(enabled, delay=5.0)
    monkeypatch.setattr(service_module, "get_service", lambda: svc)
    sent, elapsed = asyncio.run(_raw_ask(bare, json.dumps({"question": GROUNDED_QUESTION}).encode(), disconnect_after=0.3))
    assert elapsed < 2.0 and _status_and_body(sent)[0] == 499
    assert _eventually(lambda: chains[0].cancelled) and not svc._slots.locked()


def test_the_disconnect_middleware_passes_the_body_through_and_reports_the_disconnect() -> None:
    chunks: list[Message] = [
        {"type": "http.request", "body": b'{"question": ', "more_body": True},
        {"type": "http.request", "body": b'"Who?"}', "more_body": False},
    ]
    pending = list(chunks)
    seen: list[Message] = []
    recorded: list[Any] = []

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        await asyncio.sleep(0.05)  # the client leaves a moment after the body
        return {"type": "http.disconnect"}

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            seen.append(message)
            if not message.get("more_body", False):
                break
        recorded.append(scope[DISCONNECT_SCOPE_KEY])
        seen.append(await receive())  # after the body: blocks until the client goes away

    async def send(message: Message) -> None:
        raise AssertionError("nothing is sent")

    asyncio.run(AskDisconnectMiddleware(downstream, path=ASK)(_scope(), receive, send))
    assert seen == [*chunks, {"type": "http.disconnect"}]  # the body unchanged, then the disconnect
    assert recorded[0].is_set()


def test_the_disconnect_middleware_leaves_every_other_request_alone() -> None:
    calls: list[tuple[Any, bool]] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        pass

    async def downstream(scope: Any, receive_: Any, send_: Any) -> None:
        calls.append((receive_, DISCONNECT_SCOPE_KEY in scope))

    middleware = AskDisconnectMiddleware(downstream, path=ASK)
    for scope in (_scope(path=STATUS, method="GET"), _scope(method="GET"), {"type": "lifespan"}):
        asyncio.run(middleware(scope, receive, send))
    assert calls == [(receive, False)] * 3


# ---------------------------------------------------------------------- broken corpus


def test_a_malformed_manifest_is_an_enveloped_corpus_unavailable(
    app: Any, enabled: Settings, install: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.assistant_rag import corpus as corpus_module
    from app.assistant_rag import manifest as manifest_module

    bad = tmp_path / "manifest.json"
    bad.write_text('{"corpus_version": "x",', encoding="utf-8")  # a hand edit with a trailing comma
    monkeypatch.setattr(
        service_module, "load_corpus", lambda: corpus_module.build_corpus(manifest_module.load_manifest(str(bad)))
    )

    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    install(corpus_loader=service_module._load_corpus_or_raise)  # the real default loader
    with TestClient(app, base_url=LOCAL, client=("127.0.0.1", 5000), raise_server_exceptions=False) as local:
        status = local.get(STATUS)
        assert status.status_code == 200
        corpus = status.json()["corpus"]
        assert corpus["loaded"] is False and "JSONDecodeError" in corpus["error"] and status.json()["ready"] is False
        rid = {"x-request-id": "rid-corpus"}
        for response in (local.get(CONTEXT, headers=rid), local.post(ASK, json={"question": GROUNDED_QUESTION}, headers=rid)):
            assert response.status_code == 503 and response.headers["x-request-id"] == "rid-corpus"
            error = error_of(response)
            assert error["code"] == "corpus_unavailable" and error["request_id"] == "rid-corpus"
            assert str(tmp_path) not in response.text


# --------------------------------------------------------------------- the real chain


@pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")
def test_ask_through_the_real_chain_with_fakes(
    client: TestClient, enabled: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.assistant_rag.service import AssistantService
    from tests.unit.test_assistant_pipeline import ScriptedChatModel
    from tests.unit.test_assistant_service import CORPUS

    svc = AssistantService(
        enabled,
        corpus_loader=lambda: CORPUS,
        embeddings_factory=lambda s: None,
        chat_model_factory=lambda s: ScriptedChatModel(reply=ANSWER),
    )
    monkeypatch.setattr(service_module, "get_service", lambda: svc)
    body = client.post(ASK, json={"question": GROUNDED_QUESTION}).json()
    assert body["answer"] == ANSWER and body["data_mode"] == "synthetic_demo"
    stages = [t["stage"] for t in body["diagnostics"]["timings"]]
    assert stages[: len(STAGES) - 1] == list(STAGES[:-1]) and {"final_response", "total"} <= set(stages)
