"""The frontend's contract fixture: real backend payloads, written for the UI's own tests.

`frontend/tests/fixtures/assistant-contract.json` holds what the three assistant routes
return, built here by the real pipeline and service with this suite's fakes (lexical-only
retrieval, an injected model reply, a stubbed Ollama probe — nothing reaches a network):

* `ask` — one AskResponse (with the AskRequest that produced it) per answer kind, plus a guarded answer (guard removals and the
  raw answer in diagnostics), a roster-fit answer with an evidence grid and a priority
  change, a roster-fit answer on the team's default priorities, an `unknown_entity`
  refusal and a traced answer;
* `status` — an enabled, ready StatusOut and a switched-off one;
* `context` — the ContextOut catalogue.

`frontend/tests/unit/assistant-contract.test.ts` parses every payload through the zod
schemas in `lib/assistant-schemas.ts` and fails if a field is dropped, defaulted, caught or
not declared. Together the two tests catch a backend rename (or a new field) that the
handwritten UI fixtures would hide.

This test fails when the committed fixture no longer matches what the backend produces.
Regenerate it (then run the frontend's vitest) with:

    cd backend && UPDATE_CONTRACT_FIXTURE=1 .venv/bin/pytest -q tests/unit/test_assistant_contract_fixture.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.assistant_rag import providers
from app.assistant_rag.models import DependenciesStatusOut, ProviderStatusOut, TraceOut
from app.config import Settings
from tests.unit.test_assistant_pipeline import (
    AMBIGUOUS_QUESTION,
    GROUNDED_QUESTION,
    LEGALITY_QUESTION,
    OFF_TOPIC_QUESTION,
    ask,
    make_settings,
    make_stages,
    run_plain,
)
from tests.unit.test_assistant_service import healthy_probe, make_service

FIXTURE = Path(__file__).resolve().parents[3] / "frontend" / "tests" / "fixtures" / "assistant-contract.json"
REGENERATE = "cd backend && UPDATE_CONTRACT_FIXTURE=1 .venv/bin/pytest -q tests/unit/test_assistant_contract_fixture.py"

#: Stage timings are wall-clock; every row is pinned to this so the fixture is stable. The
#: stage *names* (top-level stages, `retrieval.*` sub-stages, `total`) are kept as sent.
PINNED_MS = 1.0

#: name -> (request, injected model reply or None for a deterministic route, trace)
ASKS: dict[str, tuple[Any, str | None, TraceOut | None]] = {
    "grounded_answer": (
        ask(GROUNDED_QUESTION),
        "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S05].",
        None,
    ),
    "guarded_answer": (
        ask(GROUNDED_QUESTION),
        "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S05].\n\nA Theo Vance trade is legal under the CBA.",
        None,
    ),
    "roster_fit_grid": (
        ask(
            "Which players fit the Comets' point-of-attack defense needs?",
            mode="roster_fit",
            team_id="COMETS",
            priorities={"point_of_attack_defense": "high", "spacing": "must_preserve"},
            previous_priorities={"spacing": "must_preserve"},
            constraints=["Keep the closing five's spacing intact"],
            history=[
                {"role": "user", "content": "Where are we thin?"},
                {"role": "assistant", "content": "Point-of-attack defense [S20]."},
            ],
        ),
        "**Short answer:** Niko Reed is the strongest point-of-attack option [S03].\n\n"
        "- My read: Reed fits your spacing rule only if Vance closes [S05].\n"
        "- What would you trade for him?",
        None,
    ),
    # No priorities from the GM: roster fit falls back to the team's documented defaults.
    "roster_fit_team_defaults": (
        ask("Which players fit the Comets?", mode="roster_fit", team_id="COMETS", priorities={}),
        "Niko Reed is the strongest point-of-attack option [S03].",
        None,
    ),
    "partial_abstention": (
        ask("What is Theo Vance's three-point percentage, and what is his salary?"),
        "Theo Vance shoots 40.4% from three [S05]. His salary is not in the sandbox.",
        None,
    ),
    "clarification": (ask(AMBIGUOUS_QUESTION), None, None),
    "abstention": (ask(OFF_TOPIC_QUESTION), None, None),
    "unsupported_request": (ask(LEGALITY_QUESTION), None, None),
    "unknown_entity": (ask("What do the scouts say about Zebulon Quackenbush's defense?"), None, None),
    "traced_answer": (
        ask(GROUNDED_QUESTION),
        "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S05].",
        TraceOut(
            enabled=True,
            project="pivot-assistant",
            run_id="00000000-0000-4000-8000-000000000001",
            url="https://smith.langchain.com/o/example/projects/p/r/00000000-0000-4000-8000-000000000001",
        ),
    ),
}


def _pin_timings(payload: dict[str, Any]) -> dict[str, Any]:
    diagnostics = payload.get("diagnostics") or {}
    for row in diagnostics.get("timings", []):
        row["ms"] = PINNED_MS
    return payload


def build_fixture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async def probe(s: Settings, timeout: float = 1.5) -> ProviderStatusOut:
        return healthy_probe(s)

    monkeypatch.setattr(providers, "probe_ollama", probe)
    # The optional extra is not installed in CI; the status must not depend on it.
    monkeypatch.setattr(
        providers, "dependency_status", lambda: DependenciesStatusOut(installed=True, missing=[], install_hint=None)
    )

    stages = make_stages()
    asks: dict[str, Any] = {}
    for name, (request, model_text, trace) in ASKS.items():
        response = asyncio.run(run_plain(stages, request, request_id=f"req-{name}", model_text=model_text, trace=trace))
        asks[name] = {
            "request": request.model_dump(mode="json"),
            "response": _pin_timings(response.model_dump(mode="json")),
        }

    service, _ = make_service()
    disabled, _ = make_service(make_settings(assistant_enabled=False))
    return {
        "_comment": f"Generated by backend/tests/unit/test_assistant_contract_fixture.py — do not edit. Regenerate: {REGENERATE}",
        "ask": asks,
        "status": {
            "enabled": asyncio.run(service.status()).model_dump(mode="json"),
            "disabled": asyncio.run(disabled.status()).model_dump(mode="json"),
        },
        "context": service.context().model_dump(mode="json"),
    }


def _serialise(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"


def test_every_answer_kind_is_in_the_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = {case["response"]["answer_kind"] for case in build_fixture(monkeypatch)["ask"].values()}
    assert kinds == {"grounded_answer", "partial_abstention", "abstention", "clarification", "unsupported_request"}


def test_frontend_contract_fixture_is_current(monkeypatch: pytest.MonkeyPatch) -> None:
    if not FIXTURE.parent.parent.is_dir():
        pytest.skip("no frontend/ checkout next to backend/")
    expected = _serialise(build_fixture(monkeypatch))
    if os.environ.get("UPDATE_CONTRACT_FIXTURE") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(expected, encoding="utf-8")
        return
    current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else None
    assert current == expected, (
        f"{FIXTURE} is stale: the backend's assistant responses changed. Regenerate it with\n"
        f"    {REGENERATE}\n"
        "then run `npx vitest run tests/unit/assistant-contract.test.ts` in frontend/ and fix "
        "lib/assistant-schemas.ts (and lib/assistant-types.ts) for any field it reports."
    )
