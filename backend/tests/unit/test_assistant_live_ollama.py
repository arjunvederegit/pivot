"""Opt-in: the real service against a real local Ollama. Skipped unless ASSISTANT_LIVE_TESTS=1.

    cd backend && ASSISTANT_LIVE_TESTS=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
        tests/unit/test_assistant_live_ollama.py

Needs `ollama serve` with the configured chat and embedding models pulled
(`ollama pull qwen3.5:2b`, `ollama pull nomic-embed-text`). The first question embeds the
whole corpus and loads the model, so it can take a minute on a laptop. Nothing leaves the
machine: tracing is forced off. Assertions are about the pipeline's guarantees (valid
citations, a deterministic legality abstention), not about the model's prose.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ASSISTANT_LIVE_TESTS") != "1", reason="live Ollama test: set ASSISTANT_LIVE_TESTS=1"
)


@pytest.fixture(scope="module")
def live_service():
    pytest.importorskip("langchain_ollama")
    from app.assistant_rag.service import AssistantService
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={"assistant_enabled": True, "langsmith_tracing": False, "assistant_dev_options": False}
    )
    service = AssistantService(settings)
    status = asyncio.run(service.status())
    if not status.ready:
        pytest.skip("Ollama is not ready: " + "; ".join(status.issues))
    return service


def test_live_fact_question_cites_only_retrieved_sources(live_service) -> None:
    from app.assistant_rag.models import AskRequest

    response = asyncio.run(
        live_service.ask(
            AskRequest(question="What is Theo Vance's three-point percentage, and on what volume?"),
            request_id="live-fact",
        )
    )
    assert response.answer_kind in ("grounded_answer", "partial_abstention")
    assert response.diagnostics.model_called
    assert response.cited_source_ids, "the answer should cite at least one retrieved source"
    assert response.invalid_citations == []
    assert {"S04", "S05"} & {s.source_id for s in response.sources}


def test_live_legality_question_is_abstained_without_the_model(live_service) -> None:
    from app.assistant_rag.models import AskRequest

    response = asyncio.run(
        live_service.ask(
            AskRequest(
                question="Is a Theo Vance trade legal under the CBA, and do the salaries match?",
                mode="roster_fit",
                team_id="COMETS",
            ),
            request_id="live-legality",
        )
    )
    assert response.answer_kind == "unsupported_request"
    assert not response.diagnostics.model_called
    assert response.cited_source_ids == ["S31"] and response.invalid_citations == []
    assert "cba_legality" in {u.kind for u in response.unsupported_requests}
