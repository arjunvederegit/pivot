"""Ollama provider plumbing: the chat-model factory, the readiness probe, error mapping.

The probe runs against `httpx.MockTransport` (no socket is opened); error mapping is fed
the exact exception types the stack raises. `ollama` and `langchain_ollama` are optional,
so the tests that need them skip without the extra.
"""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any

import httpx
import pytest

from app.assistant_rag import providers
from app.assistant_rag.errors import (
    AssistantValidationError,
    ModelUnavailableError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.assistant_rag.providers import (
    INSTALL_HINT,
    base_url_is_local,
    dependency_status,
    display_url,
    map_provider_error,
    model_available,
    probe_ollama,
)
from tests.unit.test_assistant_pipeline import make_settings

SETTINGS = make_settings(assistant_chat_model="qwen3.5:2b", assistant_embedding_model="nomic-embed-text")
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def test_base_url_locality() -> None:
    for url in ("http://localhost:11434", "http://127.0.0.1:11434/", "http://[::1]:11434", "http://LOCALHOST"):
        assert base_url_is_local(url), url
    for url in ("http://10.0.0.5:11434", "https://ollama.example.com", "not a url", "http://[::1"):
        assert not base_url_is_local(url), url


CREDENTIAL_URL = "http://ops:Hunter2@gpu-box.lan:11434/private-path?token=abc#frag"
CREDENTIALS = ("Hunter2", "ops:", "private-path", "token", "frag")


def test_display_url_keeps_only_scheme_host_and_port() -> None:
    assert display_url(CREDENTIAL_URL) == "http://gpu-box.lan:11434"
    assert display_url("http://localhost:11434/") == "http://localhost:11434"
    assert display_url("https://user@ollama.example.com") == "https://ollama.example.com"
    assert display_url("http://u:p@[::1]:11434") == "http://[::1]:11434"
    for broken in ("not a url", "http://[::1", "http://host:notaport", ""):
        assert display_url(broken) == "(an unparseable URL)", broken


def test_model_availability_treats_latest_as_optional() -> None:
    pulled = ["qwen3.5:2b", "nomic-embed-text:latest", ""]
    assert model_available("nomic-embed-text", pulled)
    assert model_available("nomic-embed-text:latest", pulled)
    assert model_available("qwen3.5:2b", pulled)
    assert not model_available("qwen3.5:4b", pulled)
    assert model_available("llama:latest", ["llama"])


# ------------------------------------------------------------------------------- probe


def _mock_ollama(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[str]:
    """Route probe_ollama's AsyncClient through a MockTransport; return requested paths."""
    seen: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return handler(request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(recording), **kw))
    return seen


def test_probe_reports_version_and_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.5"})
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:2b"}, {"model": "nomic-embed-text:latest"}]})

    seen = _mock_ollama(monkeypatch, handler)
    status = asyncio.run(probe_ollama(SETTINGS))
    assert seen == ["/api/version", "/api/tags"]
    assert status.reachable and status.version == "0.40.5" and status.error is None
    assert status.chat_model_available and status.embedding_model_available
    assert status.base_url_is_local and status.name == "ollama"


def test_probe_reports_missing_models(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.5"})
        return httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})

    _mock_ollama(monkeypatch, handler)
    status = asyncio.run(probe_ollama(SETTINGS))
    assert status.reachable and not status.chat_model_available and not status.embedding_model_available


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (httpx.ConnectError("refused"), "Start it with 'ollama serve'"),
        (httpx.ReadTimeout("slow"), "did not answer within 1.5 s"),
    ],
)
def test_probe_never_raises_on_transport_failures(monkeypatch: pytest.MonkeyPatch, exc: Exception, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    _mock_ollama(monkeypatch, handler)
    status = asyncio.run(probe_ollama(SETTINGS))
    assert not status.reachable and expected in (status.error or "")
    assert not status.chat_model_available and not status.embedding_model_available


def test_probe_reports_http_errors_and_unreadable_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_ollama(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    status = asyncio.run(probe_ollama(SETTINGS))
    assert not status.reachable and "answered 500 to /api/version" in (status.error or "")

    def bad_tags(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "1"})
        return httpx.Response(200, text="not json")

    _mock_ollama(monkeypatch, bad_tags)
    status = asyncio.run(probe_ollama(SETTINGS))
    assert status.reachable and "could not be read" in (status.error or "")


# ------------------------------------------------------------------------ error mapping


def test_transport_failures_map_to_provider_unavailable_with_the_fix() -> None:
    for exc in (httpx.ConnectError("refused"), ConnectionError("Failed to connect to Ollama"), ConnectionRefusedError()):
        mapped = map_provider_error(exc, settings=SETTINGS)
        assert isinstance(mapped, ProviderUnavailableError)
        assert mapped.message == "Ollama is not reachable at http://localhost:11434. Start it with 'ollama serve'."


def test_timeouts_map_to_provider_timeout_even_for_connect_timeouts() -> None:
    for exc in (httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow")):
        assert isinstance(map_provider_error(exc, settings=SETTINGS), ProviderTimeoutError)


def test_http_status_errors_are_provider_unavailable() -> None:
    request = httpx.Request("POST", "http://localhost:11434/api/embed")
    exc = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(502, request=request))
    mapped = map_provider_error(exc, settings=SETTINGS)
    assert isinstance(mapped, ProviderUnavailableError)
    assert mapped.message == "Ollama at http://localhost:11434 answered 502 to /api/embed."


def test_mapped_errors_never_show_url_credentials() -> None:
    settings = make_settings(ollama_base_url=CREDENTIAL_URL)
    request = httpx.Request("POST", CREDENTIAL_URL)
    for exc in (
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request)),
    ):
        mapped = map_provider_error(exc, settings=settings)
        assert mapped is not None and "http://gpu-box.lan:11434" in mapped.message
        assert not any(secret in mapped.message for secret in CREDENTIALS), mapped.message


def test_the_probe_never_shows_url_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(ollama_base_url=CREDENTIAL_URL)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    for handler in (refused, slow, lambda request: httpx.Response(500, text="boom")):
        _mock_ollama(monkeypatch, handler)
        status = asyncio.run(probe_ollama(settings))
        assert status.error and "http://gpu-box.lan:11434" in status.error
        assert not any(secret in status.model_dump_json() for secret in CREDENTIALS), status.error


def test_other_exceptions_are_not_provider_errors() -> None:
    for exc in (KeyError("x"), ValueError("y"), TypeError("z"), RuntimeError("w"), AssistantValidationError("v")):
        assert map_provider_error(exc, settings=SETTINGS) is None


def test_default_settings_are_used_when_none_are_given() -> None:
    mapped = map_provider_error(httpx.ConnectError("refused"))
    assert isinstance(mapped, ProviderUnavailableError) and "ollama serve" in mapped.message


def test_ollama_response_errors() -> None:
    ollama = pytest.importorskip("ollama")
    named = map_provider_error(ollama.ResponseError('model "qwen3.5:2b" not found, try pulling it first', 404), settings=SETTINGS)
    assert isinstance(named, ModelUnavailableError)
    assert named.message == "Ollama does not have the model 'qwen3.5:2b'. Pull it with: ollama pull qwen3.5:2b"
    hinted = map_provider_error(ollama.ResponseError("not found", 404), settings=SETTINGS, model="nomic-embed-text")
    assert isinstance(hinted, ModelUnavailableError) and "ollama pull nomic-embed-text" in hinted.message
    unknown = map_provider_error(ollama.ResponseError("404 page not found", 404), settings=SETTINGS)
    assert isinstance(unknown, ModelUnavailableError)
    assert "ollama pull qwen3.5:2b && ollama pull nomic-embed-text" in unknown.message
    other = map_provider_error(ollama.ResponseError("out of memory " + "x" * 400, 500), settings=SETTINGS)
    assert isinstance(other, ProviderUnavailableError) and other.message.startswith("Ollama returned an error: out of memory")
    assert len(other.message) < 400


# ------------------------------------------------------------------------- dependencies


def test_dependency_status_reports_missing_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_find_spec(name: str, *args: Any) -> Any:
        if name == "langsmith":
            return None
        if name == "langchain_ollama":
            raise ValueError("broken install")
        return object()  # present, whatever this environment actually has installed

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    status = dependency_status()
    assert not status.installed and status.missing == ["langchain_ollama", "langsmith"]
    assert status.install_hint == INSTALL_HINT == "cd backend && .venv/bin/pip install -e '.[dev,assistant]'"


def test_dependency_status_when_everything_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object())
    status = dependency_status()
    assert status.installed and status.missing == [] and status.install_hint is None


# --------------------------------------------------------------------------- chat model


@pytest.mark.skipif(importlib.util.find_spec("langchain_ollama") is None, reason="needs langchain-ollama")
def test_chat_model_is_configured_for_grounded_bounded_answers() -> None:
    settings = make_settings(
        assistant_chat_model="qwen3.5:2b",
        ollama_base_url="http://127.0.0.1:11434",
        assistant_num_ctx=8192,
        assistant_max_output_tokens=900,
        assistant_request_timeout_seconds=150.0,
    )
    model = providers.build_chat_model(settings)  # constructing it contacts nothing
    assert model.model == "qwen3.5:2b" and model.base_url == "http://127.0.0.1:11434"
    assert model.temperature == 0 and model.reasoning is False
    assert (model.num_ctx, model.num_predict, model.keep_alive) == (8192, 900, "30m")
    assert model.client_kwargs == {"timeout": 150.0}
    assert providers.model_name(model) == "qwen3.5:2b"
    assert providers.model_name(object()) is None
