"""Opt-in LangSmith tracing: Settings decide, the shell environment does not.

Settings read Ask Pivot's own switch (`ASSISTANT_TRACING`) and project
(`ASSISTANT_LANGSMITH_PROJECT`), never the SDK's `LANGSMITH_TRACING` / `LANGSMITH_PROJECT`.
The tests that prove it build `from_environment()` from a monkeypatched environment —
the way the server builds them — rather than a pinned copy.

No test here constructs a real `langsmith.Client` or reaches the network: `Client` is
replaced by a fake that fails the test if constructed (or records its arguments), and
LangChain's `LangChainTracer` is replaced by a recorder, so "was a tracer attached?" is a
plain assertion. LangSmith caches environment lookups, so every test that sets a tracing
variable clears that cache before and after.

The first group runs on the base install: a stand-in `langsmith` module (a recording
`Client` and `tracing_context`) is put in `sys.modules`, which covers the scope's logic
whether or not the real SDK is installed. The second group uses the real SDK and LangChain
to prove the end-to-end behaviour — that the context really outranks the environment.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.assistant_rag import tracing
from app.assistant_rag.tracing import (
    DEFAULT_PROJECT,
    DISABLED,
    TraceHandle,
    project_name,
    trace_url,
    tracing_active,
    tracing_scope,
)
from app.config import Settings
from tests.unit.test_assistant_pipeline import make_settings

#: What a developer exports for *another* LangSmith project — the usual SDK setup.
OTHER_PROJECT_SHELL = {
    "LANGSMITH_TRACING": "true",
    "LANGCHAIN_TRACING_V2": "true",
    "LANGSMITH_API_KEY": "lsv2_pt_key_for_another_project",
    "LANGCHAIN_API_KEY": "lsv2_pt_key_for_another_project",
    "LANGSMITH_PROJECT": "acme-customer-bot",
}


def _without_tracing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(("LANGSMITH_", "LANGCHAIN_", "ASSISTANT_TRACING", "ASSISTANT_LANGSMITH_")):
            monkeypatch.delenv(name)


def from_environment(env_file: Any = None) -> Settings:
    """Settings built the way the server builds them — from the process environment (and,
    when given, a dotenv file) — never a pinned copy."""
    return Settings(_env_file=env_file)  # type: ignore[call-arg]  # pydantic-settings' init option


@pytest.fixture()
def shell(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A process environment with no tracing variables at all, so `from_environment()`
    sees exactly what the test exports — as a real shell would hand it to the backend."""
    _without_tracing_variables(monkeypatch)
    return monkeypatch


def test_tracing_needs_both_the_switch_and_a_key() -> None:
    assert not tracing_active(make_settings())
    assert not tracing_active(make_settings(langsmith_tracing=True))
    assert not tracing_active(make_settings(langsmith_api_key="lsv2-test"))
    assert tracing_active(make_settings(langsmith_tracing=True, langsmith_api_key="lsv2-test"))


def test_the_sdks_shell_variables_never_switch_ask_pivot_tracing_on(shell: pytest.MonkeyPatch) -> None:
    for name, value in OTHER_PROJECT_SHELL.items():
        shell.setenv(name, value)
    settings = from_environment()  # built from the environment, as the server does
    assert settings.langsmith_api_key  # the key is read; on its own it switches nothing on
    assert settings.langsmith_tracing is False and not tracing_active(settings)
    assert settings.langsmith_project == DEFAULT_PROJECT == project_name(settings)  # LANGSMITH_PROJECT ignored


def test_ask_pivots_own_switch_and_a_key_turn_tracing_on(shell: pytest.MonkeyPatch) -> None:
    shell.setenv("ASSISTANT_TRACING", "true")
    assert not tracing_active(from_environment())  # still needs a key
    shell.setenv("LANGSMITH_API_KEY", "lsv2_pt_mine")
    shell.setenv("LANGSMITH_PROJECT", "acme-customer-bot")
    settings = from_environment()
    assert tracing_active(settings) and project_name(settings) == DEFAULT_PROJECT  # the shell's project is ignored
    shell.setenv("ASSISTANT_LANGSMITH_PROJECT", "pivot-experiments")
    assert project_name(from_environment()) == "pivot-experiments"


def test_a_dotenv_file_uses_the_same_names(shell: pytest.MonkeyPatch, tmp_path: Any) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=lsv2_pt_x\nLANGSMITH_PROJECT=acme\n")
    settings = from_environment(env_file)
    assert not tracing_active(settings) and settings.langsmith_project == DEFAULT_PROJECT
    env_file.write_text("ASSISTANT_TRACING=true\nLANGSMITH_API_KEY=lsv2_pt_x\nASSISTANT_LANGSMITH_PROJECT=mine\n")
    settings = from_environment(env_file)
    assert tracing_active(settings) and settings.langsmith_project == "mine"


def test_trace_handle_to_out() -> None:
    handle = TraceHandle(enabled=True, project="proj", run_id="abc")
    assert handle.to_out().model_dump() == {"enabled": True, "project": "proj", "run_id": "abc", "url": None}
    assert handle.to_out("https://x").url == "https://x"
    assert DISABLED.to_out().enabled is False


def test_trace_url_is_none_for_a_disabled_handle() -> None:
    assert trace_url(make_settings(), DISABLED) is None


ACTIVE = {"langsmith_tracing": True, "langsmith_api_key": "lsv2-test-key", "langsmith_project": "proj"}


@pytest.fixture()
def fake_langsmith(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """A stand-in `langsmith` package recording every Client and tracing_context call."""
    record = SimpleNamespace(clients=[], contexts=[], fail_client=False)

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            if record.fail_client:
                raise RuntimeError("bad endpoint")
            record.clients.append(kwargs)

    @contextmanager
    def tracing_context(**kwargs: Any) -> Iterator[None]:
        record.contexts.append(kwargs)
        yield

    run_helpers = types.ModuleType("langsmith.run_helpers")
    run_helpers.tracing_context = tracing_context  # type: ignore[attr-defined]
    package = types.ModuleType("langsmith")
    package.Client = FakeClient  # type: ignore[attr-defined]
    package.run_helpers = run_helpers  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langsmith", package)
    monkeypatch.setitem(sys.modules, "langsmith.run_helpers", run_helpers)
    tracing.reset_tracing_state()
    yield record
    tracing.reset_tracing_state()


def test_inactive_scope_switches_the_langsmith_context_off(fake_langsmith: SimpleNamespace) -> None:
    with tracing_scope(make_settings(), metadata={"a": 1}, tags=["t"]) as handle:
        pass
    assert handle == DISABLED
    assert fake_langsmith.contexts == [{"enabled": False}] and fake_langsmith.clients == []


def test_active_scope_creates_one_client_and_names_the_root_run(fake_langsmith: SimpleNamespace) -> None:
    settings = make_settings(**ACTIVE)
    metadata, tags = {"corpus_version": "v-test"}, ["ask-pivot"]
    with tracing_scope(settings, metadata=metadata, tags=tags) as first:
        pass
    with tracing_scope(settings, metadata=metadata, tags=tags) as second:
        pass
    assert first.enabled and first.project == "proj" and uuid.UUID(str(first.run_id))
    assert first.run_id != second.run_id  # one root run id per question
    assert fake_langsmith.clients == [{"api_key": "lsv2-test-key", "api_url": None}]  # created once, lazily
    context = fake_langsmith.contexts[0]
    assert context["enabled"] is True and context["project_name"] == "proj"
    assert context["metadata"] == metadata and context["tags"] == tags
    assert isinstance(context["client"], sys.modules["langsmith"].Client)


def test_a_changed_endpoint_replaces_the_client(fake_langsmith: SimpleNamespace) -> None:
    first = tracing._client(make_settings(**ACTIVE))
    assert tracing._client(make_settings(**ACTIVE)) is first
    other = tracing._client(make_settings(**ACTIVE, langsmith_endpoint="https://eu.example"))
    assert other is not first and fake_langsmith.clients[-1]["api_url"] == "https://eu.example"


def test_a_failing_client_runs_untraced(fake_langsmith: SimpleNamespace) -> None:
    fake_langsmith.fail_client = True
    with tracing_scope(make_settings(**ACTIVE), metadata={}, tags=[]) as handle:
        pass
    assert handle == DISABLED and fake_langsmith.contexts == [{"enabled": False}]


def test_a_scope_built_from_the_sdks_shell_variables_is_untraced(
    shell: pytest.MonkeyPatch, fake_langsmith: SimpleNamespace
) -> None:
    for name, value in OTHER_PROJECT_SHELL.items():
        shell.setenv(name, value)
    with tracing_scope(from_environment(), metadata={}, tags=[]) as handle:
        pass
    assert handle == DISABLED
    assert fake_langsmith.contexts == [{"enabled": False}] and fake_langsmith.clients == []


def test_an_empty_project_never_hands_the_choice_to_the_sdk(fake_langsmith: SimpleNamespace) -> None:
    with tracing_scope(make_settings(**{**ACTIVE, "langsmith_project": ""}), metadata={}, tags=[]) as handle:
        pass
    assert handle.project == DEFAULT_PROJECT and fake_langsmith.contexts[0]["project_name"] == DEFAULT_PROJECT


def test_without_langsmith_installed_every_scope_is_untraced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "langsmith", None)  # makes `import langsmith` fail
    monkeypatch.setitem(sys.modules, "langsmith.run_helpers", None)
    tracing.reset_tracing_state()
    for settings in (make_settings(), make_settings(**ACTIVE)):
        with tracing_scope(settings, metadata={}, tags=[]) as handle:
            pass
        assert handle == DISABLED


def test_trace_url_is_best_effort_and_caches_the_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = uuid.uuid4()
    reads: list[str] = []

    class FakeClient:
        def read_project(self, *, project_name: str) -> Any:
            reads.append(project_name)
            return SimpleNamespace(id=project_id)

        def get_run_url(self, *, run: Any, project_id: Any) -> str:
            return f"https://smith.example/p/{project_id}/r/{run.id}"

    tracing.reset_tracing_state()
    monkeypatch.setattr(tracing, "_client", lambda settings: FakeClient())
    settings = make_settings(**ACTIVE)
    handle = TraceHandle(enabled=True, project="proj", run_id=str(uuid.uuid4()))
    assert trace_url(settings, handle) == f"https://smith.example/p/{project_id}/r/{handle.run_id}"
    assert trace_url(settings, handle) is not None
    assert reads == ["proj"]

    class FailingClient(FakeClient):
        def get_run_url(self, *, run: Any, project_id: Any) -> str:
            raise ConnectionError("offline")

    monkeypatch.setattr(tracing, "_client", lambda settings: FailingClient())
    assert trace_url(settings, handle) is None
    tracing.reset_tracing_state()


# ------------------------------------------------------------- with the real SDK


@pytest.fixture()
def recorder(monkeypatch: pytest.MonkeyPatch):
    """Replace LangChainTracer with a recorder and make constructing a real Client fail."""
    pytest.importorskip("langchain_core")
    import langsmith
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tracers import langchain as lc_tracers
    from langsmith import utils as ls_utils

    created: list[dict[str, Any]] = []

    class RecordingTracer(BaseCallbackHandler):
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)
            # The attributes LangChain reads off a real tracer while configuring a run.
            self.run_map: dict[str, Any] = {}
            self.order_map: dict[Any, Any] = {}
            self._external_run_ids: dict[str, int] = {}

        def copy_with_metadata_defaults(self, **_: Any) -> RecordingTracer:
            return self

    def no_client(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("tests must never construct a LangSmith Client")

    monkeypatch.setattr(lc_tracers, "LangChainTracer", RecordingTracer)
    monkeypatch.setattr(langsmith, "Client", no_client)
    monkeypatch.setattr(langsmith.client.Client, "__init__", no_client)
    ls_utils.get_env_var.cache_clear()
    tracing.reset_tracing_state()
    yield created
    ls_utils.get_env_var.cache_clear()
    tracing.reset_tracing_state()


@pytest.fixture()
def tracing_env(monkeypatch: pytest.MonkeyPatch, recorder: list[dict[str, Any]]):
    """A developer shell that exports the SDK's tracing variables for another project (with
    a fake key), and none of Ask Pivot's own."""
    from langsmith import utils as ls_utils

    _without_tracing_variables(monkeypatch)
    for name, value in OTHER_PROJECT_SHELL.items():
        monkeypatch.setenv(name, value)
    ls_utils.get_env_var.cache_clear()
    return recorder


def _probe_runnable() -> Any:
    """A one-stage runnable that reports the LangSmith context it ran under."""
    from langchain_core.runnables import RunnableLambda
    from langsmith.run_helpers import get_tracing_context

    def look(_: Any) -> dict[str, Any]:
        context = get_tracing_context()
        return {"enabled": context["enabled"], "project": context["project_name"], "client": context["client"]}

    return RunnableLambda(look, name="probe")


def test_shell_tracing_variables_alone_would_attach_a_tracer(tracing_env: list[dict[str, Any]]) -> None:
    """The control: without the scope, LangChain honours the environment. This is the
    behaviour `tracing_scope` exists to override."""
    _probe_runnable().invoke(1)
    assert tracing_env, "LangChain should attach a tracer when only the environment says so"


def test_inactive_scope_overrides_shell_tracing_variables(tracing_env: list[dict[str, Any]]) -> None:
    # Settings built from that same shell, as the server builds them: the SDK's variables
    # (key included) leave Ask Pivot's switch off, and the scope keeps LangChain off too.
    with tracing_scope(from_environment(), metadata={"a": 1}, tags=["t"]) as handle:
        seen = _probe_runnable().invoke(1)
        seen_async = asyncio.run(_probe_runnable().ainvoke(1))
    assert handle == DISABLED
    assert seen["enabled"] is False and seen_async["enabled"] is False
    assert tracing_env == []  # no tracer was created, so nothing could be sent


def test_switch_without_key_is_still_inactive(tracing_env: list[dict[str, Any]]) -> None:
    with tracing_scope(make_settings(langsmith_tracing=True), metadata={}, tags=[]) as handle:
        _probe_runnable().invoke(1)
    assert not handle.enabled and tracing_env == []


def test_active_scope_ignores_the_shells_langsmith_project(
    tracing_env: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Ask Pivot's own switch on, runs go to its project, not the exported one."""
    fake_client = SimpleNamespace(name="fake-client")
    monkeypatch.setattr(tracing, "_client", lambda settings: fake_client)
    settings = make_settings(langsmith_tracing=True, langsmith_api_key="lsv2-test-key")  # default project
    with tracing_scope(settings, metadata={}, tags=[]) as handle:
        seen = _probe_runnable().invoke(1, config={"run_id": uuid.UUID(handle.run_id)})
    assert handle.project == DEFAULT_PROJECT and seen["project"] == DEFAULT_PROJECT
    assert tracing_env and tracing_env[0]["project_name"] == DEFAULT_PROJECT


def test_active_scope_traces_with_one_lazy_client_and_a_known_root_run_id(
    recorder: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.tracers.context import collect_runs

    fake_client = SimpleNamespace(name="fake-client")
    requested: list[Any] = []

    def fake_client_for(settings: Any) -> Any:
        requested.append(settings)
        return fake_client

    monkeypatch.setattr(tracing, "_client", fake_client_for)
    settings = make_settings(langsmith_tracing=True, langsmith_api_key="lsv2-test-key", langsmith_project="proj")
    metadata = {"corpus_version": "v-test", "data_mode": "synthetic_demo"}
    with tracing_scope(settings, metadata=metadata, tags=["ask-pivot"]) as handle, collect_runs() as runs:
        seen = _probe_runnable().invoke(1, config={"run_id": uuid.UUID(handle.run_id)})
    assert handle.enabled and handle.project == "proj" and uuid.UUID(handle.run_id)
    assert seen == {"enabled": True, "project": "proj", "client": fake_client}
    assert len(requested) == 1
    assert recorder and recorder[0]["client"] is fake_client and recorder[0]["project_name"] == "proj"
    assert recorder[0]["metadata"] == metadata and recorder[0]["tags"] == ["ask-pivot"]
    assert str(runs.traced_runs[0].id) == handle.run_id


def test_a_failing_client_degrades_to_an_untraced_run(monkeypatch: pytest.MonkeyPatch, recorder: list[dict[str, Any]]) -> None:
    def broken(settings: Any) -> Any:
        raise RuntimeError("bad endpoint")

    monkeypatch.setattr(tracing, "_client", broken)
    settings = make_settings(langsmith_tracing=True, langsmith_api_key="lsv2-test-key")
    with tracing_scope(settings, metadata={}, tags=[]) as handle:
        seen = _probe_runnable().invoke(1)
    assert handle == DISABLED and seen["enabled"] is False and recorder == []
