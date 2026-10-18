"""The Ask Pivot pipeline: stage logic (pure) and the LangChain chain (with fakes).

Two layers, tested separately:

* the stage functions (`AskStages`, `make_query_context`, the response assembly) are plain
  Python, so CI — which installs only `.[dev]` — runs them through `run_plain`, the chain's
  stage order without LangChain;
* the composed chain (`build_chain`) is tested with LangChain's fakes — a scripted chat
  model, a chat model that fails the test if it is ever called, deterministic fake
  embeddings — when `langchain_core` is installed. No test contacts Ollama.

The corpus is a small in-memory one: real manifest document metadata (so ids, trust,
entities and the fact manifest are real) with section text written here, so nothing depends
on the fixture prose being written elsewhere.

The helpers at the top (`make_corpus`, `make_settings`, `run_plain`, the fakes) are shared
with the service and API tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
import uuid
from dataclasses import replace
from time import perf_counter
from typing import Any

import httpx
import pytest

from app.assistant_rag import pipeline
from app.assistant_rag.corpus import Chunk, Corpus, sha256_text
from app.assistant_rag.errors import (
    AssistantValidationError,
    ModelUnavailableError,
    ProviderUnavailableError,
)
from app.assistant_rag.manifest import Manifest, load_manifest
from app.assistant_rag.models import AskRequest, AskResponse
from app.assistant_rag.pipeline import (
    PIPELINE_HISTORY_MESSAGES,
    AskStages,
    answer_kind,
    history_char_budget,
    make_query_context,
    merge_stage,
    message_text,
    run_config,
    run_metadata,
    run_tags,
    shown_passages,
    strip_reasoning,
)
from app.assistant_rag.prompts import build_prompt
from app.assistant_rag.retrieval import HybridRetriever
from app.assistant_rag.types import ModelOutput, PolicyDecision
from app.config import Settings, get_settings

HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None
requires_langchain = pytest.mark.skipif(not HAS_LANGCHAIN, reason="needs the optional .[assistant] extra")

# ------------------------------------------------------------------------ shared helpers

#: source id -> sections. Metadata comes from the real manifest; the prose is written here,
#: agrees with the fact manifest (Vance 40.4% on 6.1 3PA) and names only fictional people.
SECTIONS: dict[str, list[tuple[str | None, str]]] = {
    "S01": [(None, "Mateo Cruz is a lead guard who creates for others and pushes pace.")],
    "S03": [
        (None, "Niko Reed is one of the most complete perimeter defenders in the sandbox league."),
        ("Point-of-attack defense", "Reed gets over ball screens and keeps handlers in front of him."),
    ],
    "S04": [
        (None, "Wing statistical profile for the 2025-26 regular season."),
        ("Theo Vance", "Theo Vance: 40.4% from three on 6.1 three-point attempts per game, 14.2 points."),
    ],
    "S05": [
        (None, "Theo Vance is a high-volume shooter; his defense is the open question."),
        ("Shooting", "Vance shoots 40.4% from three on 6.1 attempts per game, mostly off movement."),
        ("Defense", "Screen navigation improved over the second half of the season."),
    ],
    "S06": [(None, "Early-season concern: Theo Vance dies on screens at the point of attack.")],
    "S10": [(None, "Dante Cruz protects the rim and rebounds; he rarely shoots from outside.")],
    "S20": [
        (None, "The Hartford Comets lost their playoff series at the point of attack."),
        ("Trade priorities", "Add a point-of-attack defender without losing the spacing we close with."),
    ],
    "S21": [(None, "The Comets closing lineup must keep five shooters on the floor.")],
    "S29": [(None, "Representation memo: Dorian Pike is the best shooter on the market.")],
    "S31": [
        (None, "This research sandbox cannot calculate trade legality, salaries or numeric fit scores."),
        ("Trade legality", "No CBA engine is connected and no contract data exists in the sandbox."),
    ],
}

GROUNDED_QUESTION = "What is Theo Vance's three-point percentage, and on what volume?"
LEGALITY_QUESTION = "Is a Theo Vance trade legal under the CBA, and do the salaries match?"
AMBIGUOUS_QUESTION = "Is Cruz a good passer?"
OFF_TOPIC_QUESTION = "Explain photosynthesis"


def make_corpus(manifest: Manifest | None = None) -> Corpus:
    manifest = manifest or load_manifest()
    chunks: list[Chunk] = []
    texts: dict[str, str] = {}
    for source_id, sections in SECTIONS.items():
        meta = manifest.document(source_id)
        assert meta is not None, source_id
        body = "\n\n".join(text for _, text in sections)
        texts[source_id] = body
        for ordinal, (section, text) in enumerate(sections):
            chunks.append(
                Chunk(
                    chunk_id=f"{meta.versioned_id}#{ordinal}",
                    source_id=source_id,
                    version=meta.version,
                    ordinal=ordinal,
                    section=section,
                    text=text,
                    content_sha256=sha256_text(body),
                    meta=meta,
                )
            )
    return Corpus(manifest=manifest, chunks=tuple(chunks), texts=texts)


def make_settings(**overrides: Any) -> Settings:
    """A private copy of the process settings — never the shared singleton. Every field a
    test depends on is pinned, so a developer's env or `.env` (say `ASSISTANT_MAX_IN_FLIGHT=2`
    or an exported `LANGSMITH_ENDPOINT`) cannot change what the tests see."""
    base = {
        "assistant_enabled": True,
        "assistant_dev_options": False,
        "assistant_allow_remote": False,
        "assistant_max_in_flight": 1,
        "assistant_request_timeout_seconds": 150.0,
        "langsmith_tracing": False,
        "langsmith_api_key": "",
        "langsmith_project": "pivot-intelligence-v2",
        "langsmith_endpoint": "",
        "ollama_base_url": "http://localhost:11434",
        "assistant_chat_model": "fake-chat",
        "assistant_embedding_model": "fake-embed",
    }
    base.update(overrides)
    return get_settings().model_copy(update=base)


def ask(question: str, **fields: Any) -> AskRequest:
    return AskRequest(question=question, **fields)


class NoModel:
    """A duck-typed model step that fails the test if any route reaches it."""

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the language model must not be called on this route")

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the language model must not be called on this route")


def make_stages(corpus: Corpus | None = None, settings: Settings | None = None, model: Any = None) -> AskStages:
    corpus = corpus or make_corpus()
    return AskStages(
        corpus=corpus,
        retriever=HybridRetriever(corpus, None),
        model=model or NoModel(),
        settings=settings or make_settings(),
        chat_model_name="fake-chat",
    )


async def run_plain(
    stages: AskStages,
    request: AskRequest,
    *,
    request_id: str | None = "req-test",
    model_text: str | None = None,
    trace: Any = None,
) -> AskResponse:
    """`build_chain`'s stage order without LangChain. On the answer route the model's
    output is `model_text`, injected in place of the model call."""
    state: dict[str, Any] = {"request": request, "request_id": request_id, "trace": trace}
    for name in pipeline.STAGES:
        started = perf_counter()
        if name == "retrieval":
            update: Any = await stages.aretrieval(state)
        elif name == "model_call":
            if stages.model_answers(state):
                assert model_text is not None, "this route calls the model: pass model_text"
                update = {"model": ModelOutput(text=model_text, called=True, model="fake-chat")}
            else:
                update = {"model": stages.deterministic_output(state)}
        else:
            update = getattr(stages, name)(state)
        state = merge_stage(state, name, update, started)
    assert isinstance(state, AskResponse)
    return state


def warning_codes(response: AskResponse) -> list[str]:
    return [w.code for w in response.warnings]


if HAS_LANGCHAIN:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field

    class ScriptedChatModel(BaseChatModel):
        """Replies with `reply` (after `delay` seconds), or raises `error`; records the
        messages of every call."""

        model: str = "fake-chat"
        reply: str = ""
        delay: float = 0.0
        error: Any = None
        usage: dict[str, int] | None = None
        calls: list[Any] = Field(default_factory=list)

        @property
        def _llm_type(self) -> str:
            return "scripted-fake"

        def _result(self, messages: Any) -> ChatResult:
            self.calls.append(list(messages))
            if self.error is not None:
                raise self.error
            message = AIMessage(content=self.reply, usage_metadata=self.usage) if self.usage else AIMessage(content=self.reply)
            return ChatResult(generations=[ChatGeneration(message=message)])

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            return self._result(messages)

        async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            if self.delay:
                await asyncio.sleep(self.delay)
            return self._result(messages)

    class ExplodingChatModel(BaseChatModel):
        """Fails the test if any route calls it."""

        model: str = "exploding"

        @property
        def _llm_type(self) -> str:
            return "exploding-fake"

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            raise AssertionError("the language model must not be called on this route")


def make_chain(chat_model: Any, corpus: Corpus | None = None, embeddings: Any = None, settings: Settings | None = None) -> Any:
    corpus = corpus or make_corpus()
    return pipeline.build_chain(
        corpus=corpus,
        retriever=HybridRetriever(corpus, embeddings),
        chat_model=chat_model,
        settings=settings or make_settings(),
    )


def invoke(chain: Any, request: AskRequest, **config: Any) -> AskResponse:
    return asyncio.run(chain.ainvoke({"request": request, "request_id": "req-test"}, config=config or None))


# --------------------------------------------------------------------- query context


def test_query_context_defaults_and_bounded_history() -> None:
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(14)]
    ctx = make_query_context(ask("And Reed?", history=history), make_settings(), "rid")
    assert (ctx.prompt_variant, ctx.retrieval_variant, ctx.k) == ("grounded", "hybrid", 6)
    assert ctx.history_received == 14
    assert len(ctx.history) == PIPELINE_HISTORY_MESSAGES and ctx.history_truncated
    assert ctx.history[-1].content == "turn 13"  # the newest turns win
    assert ctx.request_id == "rid" and ctx.previous_priorities is None


def test_query_context_applies_dev_options_and_strips_the_question() -> None:
    request = ask(
        "  Who defends?  ",
        mode="roster_fit",
        team_id="COMETS",
        priorities={"spacing": "must_preserve"},
        previous_priorities={},
        options={"prompt_variant": "basic", "retrieval_variant": "lexical", "k": 3},
    )
    ctx = make_query_context(request, make_settings(), None)
    assert (ctx.prompt_variant, ctx.retrieval_variant, ctx.k) == ("basic", "lexical", 3)
    assert ctx.question == "Who defends?" and ctx.priorities == {"spacing": "must_preserve"}
    assert ctx.previous_priorities == {} and not ctx.history_truncated


def test_history_char_budget_is_capped_by_half_the_prompt_budget() -> None:
    assert history_char_budget(make_settings()) <= pipeline.PIPELINE_HISTORY_CHARS
    small = make_settings(assistant_num_ctx=2048, assistant_max_output_tokens=512)
    assert history_char_budget(small) < history_char_budget(make_settings())


def test_history_is_budgeted_on_the_summaries_the_model_receives() -> None:
    # Three long grounded answers (~3,700 chars each, 11,100 in all, near the 11,520-char
    # budget) used to push the GM's opening rule out of the context, although each reaches
    # the model as a ~200-character summary.
    rule = "We must preserve spacing and we will not trade Niko Reed."
    answer = "**Short answer:** Reed first [S03].\n**Observed facts**\n" + "- Reed grades A at the point of attack [S03].\n" * 80
    history = [{"role": "user", "content": rule}]
    for i in range(3):
        history += [{"role": "assistant", "content": answer}, {"role": "user", "content": f"Follow-up {i}?"}]
    history.append({"role": "assistant", "content": answer})
    grounded = make_query_context(ask("And Vance?", history=history), make_settings(), None)
    assert grounded.history[0].content == rule and not grounded.history_truncated
    assert grounded.history[1].content == answer  # kept whole for entity resolution
    # the basic variant sends answers verbatim, so there they are charged in full
    basic = make_query_context(
        ask("And Vance?", history=history, options={"prompt_variant": "basic"}), make_settings(), None
    )
    assert basic.history_truncated and basic.history[0].content != rule


def test_run_metadata_tags_and_config() -> None:
    metadata = run_metadata(ask("Q", mode="roster_fit", team_id="COMETS"), make_settings(), corpus_version="v-test", embedding_model="emb", request_id="rid")
    assert metadata == {
        "corpus_version": "v-test",
        "prompt_variant": "grounded",
        "retrieval_variant": "hybrid",
        "k": 8,  # roster fit with a team: RetrievalConfig.roster_fit_k
        "chat_model": "fake-chat",
        "embedding_model": "emb",
        "team_id": "COMETS",
        "mode": "roster_fit",
        "data_mode": "synthetic_demo",
        "request_id": "rid",
    }
    assert "mode:roster_fit" in run_tags(metadata) and "synthetic_demo" in run_tags(metadata)
    run_id = str(uuid.uuid4())
    config = run_config(metadata, ["t"], run_id=run_id)
    assert config["run_id"] == uuid.UUID(run_id) and config["run_name"] == "ask_pivot"
    assert "run_id" not in run_config(metadata, ["t"])


# ---------------------------------------------------------------------- small helpers


def test_answer_kind_mapping() -> None:
    called = ModelOutput(text="x", called=True, model="m")
    not_called = ModelOutput(text="x", called=False, model=None)
    assert answer_kind(PolicyDecision(route="clarify"), not_called) == "clarification"
    assert answer_kind(PolicyDecision(route="refuse"), not_called) == "unsupported_request"
    assert answer_kind(PolicyDecision(route="answer"), not_called) == "abstention"
    assert answer_kind(PolicyDecision(route="answer"), called) == "grounded_answer"
    from app.assistant_rag.types import Unsupported

    caveat = PolicyDecision(route="answer", unsupported=(Unsupported("medical", "no records"),))
    assert answer_kind(caveat, called) == "partial_abstention"


def test_reasoning_is_stripped_and_content_blocks_are_read() -> None:
    assert strip_reasoning("<think>private</think>\nAnswer [S05].") == "Answer [S05]."
    assert strip_reasoning("<think>never closed") == ""
    assert message_text(type("M", (), {"content": [{"type": "text", "text": "a"}, "b", {"type": "image"}]})()) == "ab"
    assert message_text(type("M", (), {"content": "plain"})()) == "plain"


def test_merge_stage_records_timings_and_passes_the_final_output_through() -> None:
    started = perf_counter()
    state = merge_stage({"a": 1}, "one", {"b": 2}, started)
    state = merge_stage(state, "two", {"c": 3}, perf_counter())
    assert [name for name, _ in state["timings"]] == ["one", "two"]
    assert state["t0"] == started and state["b"] == 2
    sentinel = object()
    assert merge_stage(state, "final", sentinel, perf_counter()) is sentinel


def test_query_context_stage_accepts_a_plain_dict_request() -> None:
    stages = make_stages()
    update = stages.query_context({"request": {"question": "Who is Niko Reed?"}, "request_id": None})
    assert isinstance(update["request"], AskRequest) and update["ctx"].question == "Who is Niko Reed?"
    with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError: extra keys are refused
        stages.query_context({"request": {"question": "x", "priorites": {}}, "request_id": None})


# ------------------------------------------------------- deterministic routes (pure)


def test_refusal_for_pure_legality_cites_s31_without_a_model_call() -> None:
    response = asyncio.run(run_plain(make_stages(), ask(LEGALITY_QUESTION, mode="roster_fit", team_id="COMETS")))
    assert response.answer_kind == "unsupported_request"
    assert response.attribution_status == "not_applicable_no_model_answer"
    assert response.cited_source_ids == ["S31"] and response.invalid_citations == []
    s31 = [s for s in response.sources if s.source_id == "S31"]
    assert s31 and s31[0].cited and s31[0].excerpt
    assert {u.kind for u in response.unsupported_requests} >= {"cba_legality"}
    assert not response.diagnostics.model_called and response.evidence_grid is None
    assert response.data_mode == "synthetic_demo" and response.label == "FICTIONAL DATA / SANDBOX"
    assert response.request_id == "req-test"


def test_forced_citation_is_added_once_as_a_pinned_lead_chunk() -> None:
    stages = make_stages()
    state: dict[str, Any] = {"request": ask(LEGALITY_QUESTION), "request_id": None}
    for name in ("query_context", "entity_resolution", "policy"):
        state = merge_stage(state, name, getattr(stages, name)(state), perf_counter())
    result = stages.retriever.retrieve(state["ctx"], state["entities"])
    without_s31 = replace(result, selected=tuple(i for i in result.selected if i.chunk.source_id != "S31"))
    forced = stages.with_deterministic_citations(without_s31, state["decision"])
    added = [i for i in forced.selected if i.chunk.source_id == "S31"]
    assert len(added) == 1 and added[0].pinned and added[0].chunk.ordinal == 0
    assert pipeline.DETERMINISTIC_CITATION_REASON in added[0].reasons
    assert added[0].rank == len(forced.selected)
    # idempotent: nothing is added twice
    assert stages.with_deterministic_citations(forced, state["decision"]) is forced


def test_ambiguous_name_is_clarified_without_a_model_call() -> None:
    response = asyncio.run(run_plain(make_stages(), ask(AMBIGUOUS_QUESTION)))
    assert response.answer_kind == "clarification"
    assert "Mateo Cruz" in response.answer and "Dante Cruz" in response.answer
    assert response.entities.ambiguous and {c.id for c in response.entities.ambiguous[0].candidates} == {"MATEO_CRUZ", "DANTE_CRUZ"}
    assert all(c.kind == "player" for c in response.entities.ambiguous[0].candidates)
    assert response.cited_source_ids == [] and not response.diagnostics.model_called


def test_off_topic_question_abstains_without_a_model_call() -> None:
    response = asyncio.run(run_plain(make_stages(), ask(OFF_TOPIC_QUESTION)))
    assert response.answer_kind == "abstention"
    assert "low_evidence" in warning_codes(response)
    assert not response.diagnostics.model_called and response.cited_source_ids == []
    assert 3 <= len(response.follow_ups) <= 5


# ------------------------------------------------------------ answer route (pure)


def test_grounded_answer_is_checked_and_assembled() -> None:
    answer = "Theo Vance shoots 44.4% from three on 6.1 attempts per game [S05]. See also [S77]."
    response = asyncio.run(run_plain(make_stages(), ask(GROUNDED_QUESTION), model_text=answer))
    # The guard removed both provably wrong sentences — every claim the model made — so the
    # GM is not told this is a grounded answer; the limitations say what happened.
    assert response.answer_kind == "partial_abstention"
    assert pipeline.ALL_REMOVED_NOTE in response.limitations
    assert response.claims == []  # removal markers are never labelled as claims
    # The raw text and the warnings keep the record of what the model wrote, the answer
    # shows what the GM may read.
    assert [r.code for r in response.guard.removals] == ["wrong_number", "nonexistent_source"]
    assert response.diagnostics.raw_answer == answer
    import re as _re

    outside_markers = _re.sub(r"⟨Removed by Ask Pivot's checks:[^⟩]*⟩", "", response.answer)
    assert "44.4%" not in outside_markers  # the wrong value survives only inside the marker that names it
    assert "40.4%" in response.answer  # ...which also states the documented value
    assert "[S" not in response.answer  # markers never read as citations
    assert response.cited_source_ids == [] and response.invalid_citations == []
    assert response.attribution_status == "no_citations"
    codes = warning_codes(response)
    assert "number_contradicts_manifest" in codes and "invalid_citation" in codes
    cited = {s.source_id for s in response.sources if s.cited}
    assert cited == set()
    assert all(s.provenance.startswith("Fictional") and s.data_mode == "synthetic_demo" for s in response.sources)
    assert [s.retrieval.rank for s in response.sources] == sorted(s.retrieval.rank for s in response.sources)
    stages_seen = [t.stage for t in response.diagnostics.timings]
    assert stages_seen[: len(pipeline.STAGES) - 1] == list(pipeline.STAGES[:-1])  # includes answer_guard
    assert {"final_response", "total", "retrieval.lexical"} <= set(stages_seen)
    selected = {c.chunk_id for c in response.diagnostics.candidates if c.selected}
    assert selected == {s.chunk_id for s in response.sources}
    assert response.context_used.gm_statements == [GROUNDED_QUESTION]


def test_a_partly_guarded_answer_keeps_its_kind_and_labels_only_the_models_claims() -> None:
    # Review 2026-10-18: a marker on the same line used to merge into the correct sentence
    # beside it, which the claim breakdown then showed as contradicting the fact sheet.
    fact = "Theo Vance shoots 40.4% from three on 6.1 attempts per game [S05]."
    response = asyncio.run(run_plain(make_stages(), ask(GROUNDED_QUESTION), model_text=f"{fact} Theo Vance shoots 44.4% from three [S05]."))
    assert response.guard.applied and response.answer_kind == "grounded_answer"
    assert pipeline.ALL_REMOVED_NOTE not in response.limitations
    assert [(c.kind, c.check, c.text) for c in response.claims] == [("observed_fact", "matches_manifest", fact)]
    assert response.claim_summary.observed_fact == 1


def test_response_labels_sources_and_sentences_by_kind_of_evidence() -> None:
    # The GM-facing four-way split: observed fact / analyst opinion / inference / unavailable,
    # assigned after generation from the manifest, never by the model.
    answer = (
        "**Observed facts**\nTheo Vance shoots 40.4% from three on 6.1 attempts per game [S05].\n"
        "**My read**\nMy read: Vance keeps the floor spaced for a closing lineup.\n"
        "**Not in the sources**\nNot in the sources: any playoff shooting numbers for Vance."
    )
    response = asyncio.run(run_plain(make_stages(), ask(GROUNDED_QUESTION), model_text=answer))
    by_kind = {c.kind: c for c in response.claims}
    assert set(by_kind) == {"observed_fact", "inference", "unavailable"}
    assert by_kind["observed_fact"].check == "matches_manifest" and by_kind["observed_fact"].source_ids == ["S05"]
    assert response.claim_summary.observed_fact == 1 and response.claim_summary.inference == 1
    classes = {s.source_id: s.evidence_class for s in response.sources}
    assert classes.get("S05") == "analyst_opinion"  # a scouting report is an opinion…
    assert all(  # …and every source carries the class its document type implies
        s.evidence_class == ("untrusted_claim" if s.trust == "untrusted" else s.evidence_class) for s in response.sources
    )


def test_mixed_roster_fit_question_is_a_partial_abstention_with_a_grid() -> None:
    request = ask(
        "Does Theo Vance fit our closing lineup, and is the trade legal?",
        mode="roster_fit",
        team_id="COMETS",
        history=[{"role": "user", "content": "We must keep our spacing."}, {"role": "assistant", "content": "Noted."}],
    )
    response = asyncio.run(run_plain(make_stages(), request, model_text="Vance spaces the floor [S05]."))
    assert response.answer_kind == "partial_abstention"
    assert "cba_legality" in {u.kind for u in response.unsupported_requests}
    assert response.evidence_grid is not None
    assert "THEO_VANCE" in {row.player_id for row in response.evidence_grid.rows}
    used = response.context_used
    assert used.team_name == "Hartford Comets" and used.priorities["spacing"] == "must_preserve"
    assert used.gm_statements == ["We must keep our spacing.", request.question]
    assert used.history_messages_received == 2 and used.history_messages_used == 2 and not used.history_truncated


def test_long_history_is_trimmed_and_reported() -> None:
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Earlier point {i} about Niko Reed."} for i in range(14)]
    response = asyncio.run(run_plain(make_stages(), ask("And his defense?", history=history), model_text="Reed defends [S03]."))
    assert response.context_used.history_truncated
    assert response.context_used.history_messages_received == 14
    assert response.context_used.history_messages_used == PIPELINE_HISTORY_MESSAGES
    warning = next(w for w in response.warnings if w.code == "history_truncated")
    assert "10 of 14" in warning.message


def test_shown_passages_keeps_only_what_the_prompt_contains() -> None:
    stages = make_stages()
    state: dict[str, Any] = {"request": ask(GROUNDED_QUESTION), "request_id": None}
    for name in ("query_context", "entity_resolution", "policy"):
        state = merge_stage(state, name, getattr(stages, name)(state), perf_counter())
    retrieval = stages.retriever.retrieve(state["ctx"], state["entities"])
    assert len(retrieval.source_ids) >= 2
    package = build_prompt(state["ctx"], state["entities"], state["decision"], retrieval, stages.manifest)
    assert shown_passages(retrieval, package) is retrieval  # nothing trimmed: unchanged
    first = retrieval.selected[0].chunk.source_id
    trimmed_package = replace(package, user=_only_source_block(package.user, first))
    shown = shown_passages(retrieval, trimmed_package)
    assert {i.chunk.source_id for i in shown.selected} == {first}
    assert [i.rank for i in shown.selected] == list(range(1, len(shown.selected) + 1))


def _only_source_block(user: str, source_id: str) -> str:
    """The user message with every source block except `source_id`'s removed."""
    out, keep, inside = [], True, False
    for line in user.splitlines():
        if line.startswith("<source id="):
            inside, keep = True, line.startswith(f'<source id="{source_id}"')
        if not inside or keep:
            out.append(line)
        if line == "</source>":
            inside = False
    return "\n".join(out)


def test_pipeline_errors_name_the_model_and_pass_assistant_errors_through() -> None:
    settings = make_settings()
    with pytest.raises(ProviderUnavailableError, match="not reachable at http://localhost:11434"):  # noqa: SIM117
        with pipeline._provider_errors(settings, "fake-chat"):
            raise httpx.ConnectError("refused")
    with pytest.raises(AssistantValidationError):  # noqa: SIM117
        with pipeline._provider_errors(settings, "fake-chat"):
            raise AssistantValidationError("kept as is")
    with pytest.raises(KeyError):  # noqa: SIM117 - not a provider error: re-raised unchanged
        with pipeline._provider_errors(settings, "fake-chat"):
            raise KeyError("x")


class DuckModel:
    """What `ChatPromptTemplate | chat_model` is to the model stage, without LangChain:
    `invoke` / `ainvoke` take the template's inputs and return a message-like object."""

    def __init__(self, reply: str, usage: dict[str, int] | None = None) -> None:
        self.reply, self.usage = reply, usage
        self.inputs: list[dict[str, Any]] = []
        self.configs: list[Any] = []

    def invoke(self, inputs: dict[str, Any], config: Any = None) -> Any:
        self.inputs.append(inputs)
        self.configs.append(config)
        return type("Message", (), {"content": self.reply, "usage_metadata": self.usage})()

    async def ainvoke(self, inputs: dict[str, Any], config: Any = None) -> Any:
        return self.invoke(inputs, config)


def _state_through_prompt(stages: AskStages, request: AskRequest) -> dict[str, Any]:
    state: dict[str, Any] = {"request": request, "request_id": "x"}
    for name in ("query_context", "entity_resolution", "policy", "retrieval", "prompt_construction"):
        state = merge_stage(state, name, getattr(stages, name)(state), perf_counter())  # sync variants
    return state


def test_model_call_stage_sync_and_async_without_langchain() -> None:
    model = DuckModel("<think>x</think>Vance shoots 40.4% [S05].", usage={"input_tokens": 700, "output_tokens": 12})
    stages = make_stages(model=model)
    state = _state_through_prompt(stages, ask(GROUNDED_QUESTION))
    sync = stages.model_call(state, config={"tags": ["t"]})["model"]
    asynchronous = asyncio.run(stages.amodel_call(state, config=None))["model"]
    assert sync == replace(asynchronous, ms=sync.ms)
    assert sync.called and sync.text == "Vance shoots 40.4% [S05]." and sync.model == "fake-chat"
    assert (sync.prompt_tokens, sync.output_tokens) == (700, 12) and sync.ms is not None
    inputs = model.inputs[0]
    assert set(inputs) == {"system", "history", "user"} and inputs["history"] == []
    assert "EVIDENCE RULES" in inputs["system"] and inputs["user"].endswith(GROUNDED_QUESTION)
    assert model.configs[0] == {"tags": ["t"]}  # the run config is passed on to the model


def test_model_call_stage_writes_the_deterministic_reply_on_other_routes() -> None:
    stages = make_stages(model=NoModel())
    state = _state_through_prompt(stages, ask(AMBIGUOUS_QUESTION))
    assert state["prompt"] is None and state["shown"] is state["retrieval"]
    output = stages.model_call(state)["model"]
    assert not output.called and output.text.startswith("Which Cruz do you mean")
    assert not asyncio.run(stages.amodel_call(state))["model"].called


def test_an_empty_model_reply_is_a_provider_error_without_langchain() -> None:
    stages = make_stages(model=DuckModel("   "))
    state = _state_through_prompt(stages, ask(GROUNDED_QUESTION))
    with pytest.raises(ProviderUnavailableError, match="ollama run fake-chat"):
        stages.model_call(state)


# ------------------------------------------------------------------- the LangChain chain


@requires_langchain
def test_chain_runs_the_eight_named_stages_under_one_root_run() -> None:
    from langchain_core.tracers.context import collect_runs

    chain = make_chain(ScriptedChatModel(reply="Theo Vance shoots 40.4% from three [S05]."))
    run_id = str(uuid.uuid4())
    metadata = {"corpus_version": "test", "mode": "research"}
    with collect_runs() as collector:
        response = invoke(chain, ask(GROUNDED_QUESTION), **run_config(metadata, ["ask-pivot"], run_id=run_id))
    root = collector.traced_runs[0]
    assert root.name == "ask_pivot" and str(root.id) == run_id
    assert [child.name for child in root.child_runs] == list(pipeline.STAGES)
    assert root.extra["metadata"]["corpus_version"] == "test"
    model_stage = next(c for c in root.child_runs if c.name == "model_call")
    assert [c.name for c in model_stage.child_runs] == [pipeline.MODEL_RUN_NAME]  # nested, not a sibling
    assert response.answer_kind == "grounded_answer"


@requires_langchain
def test_grounded_answer_end_to_end_with_a_wrong_number() -> None:
    model = ScriptedChatModel(
        reply="Theo Vance shoots 44.4% from three on 6.1 attempts per game [S05]. Also [S77].",
        usage={"input_tokens": 812, "output_tokens": 25, "total_tokens": 837},
    )
    response = invoke(make_chain(model), ask(GROUNDED_QUESTION))
    assert "S05" in {s.source_id for s in response.sources}
    # through the real LangChain chain: the answer_guard stage ran and removed both sentences
    assert response.guard.applied and {r.code for r in response.guard.removals} == {"wrong_number", "nonexistent_source"}
    assert response.diagnostics.raw_answer == model.reply
    assert {"number_contradicts_manifest", "invalid_citation"} <= set(warning_codes(response))
    assert response.diagnostics.model_called
    assert (response.diagnostics.prompt_tokens, response.diagnostics.output_tokens) == (812, 25)
    assert response.diagnostics.chat_model == "fake-chat"
    messages = model.calls[0]
    assert [m.type for m in messages] == ["system", "human"]
    assert "EVIDENCE RULES" in messages[0].content and GROUNDED_QUESTION in messages[1].content


@requires_langchain
def test_history_reaches_the_model_as_human_and_ai_messages() -> None:
    model = ScriptedChatModel(reply="Reed is the stronger defender [S03].")
    history = [{"role": "user", "content": "Tell me about Niko Reed."}, {"role": "assistant", "content": "Reed defends."}]
    invoke(make_chain(model), ask("What about his point-of-attack defense?", history=history))
    assert [m.type for m in model.calls[0]] == ["system", "human", "ai", "human"]
    assert model.calls[0][1].content == "Tell me about Niko Reed."


@requires_langchain
@pytest.mark.parametrize(
    ("question", "fields", "kind"),
    [
        (AMBIGUOUS_QUESTION, {}, "clarification"),
        (LEGALITY_QUESTION, {"mode": "roster_fit", "team_id": "COMETS"}, "unsupported_request"),
        (OFF_TOPIC_QUESTION, {}, "abstention"),
    ],
)
def test_deterministic_routes_never_call_the_model(question: str, fields: dict[str, Any], kind: str) -> None:
    response = invoke(make_chain(ExplodingChatModel()), ask(question, **fields))
    assert response.answer_kind == kind and not response.diagnostics.model_called
    if kind == "unsupported_request":
        assert response.cited_source_ids == ["S31"] and response.invalid_citations == []
        assert response.attribution_status == "not_applicable_no_model_answer"


@requires_langchain
def test_sync_invoke_produces_the_same_answer() -> None:
    chain = make_chain(ScriptedChatModel(reply="Vance shoots 40.4% [S05]."))
    payload = {"request": ask(GROUNDED_QUESTION), "request_id": "sync"}
    sync = chain.invoke(payload)
    asynchronous = asyncio.run(chain.ainvoke(payload))
    assert sync.answer == asynchronous.answer and sync.cited_source_ids == asynchronous.cited_source_ids
    assert [s.chunk_id for s in sync.sources] == [s.chunk_id for s in asynchronous.sources]


@requires_langchain
def test_dense_path_with_deterministic_fake_embeddings() -> None:
    from langchain_core.embeddings import DeterministicFakeEmbedding

    chain = make_chain(ScriptedChatModel(reply="Vance shoots 40.4% [S05]."), embeddings=DeterministicFakeEmbedding(size=256))
    response = invoke(chain, ask(GROUNDED_QUESTION))
    assert response.diagnostics.embedding_model == "DeterministicFakeEmbedding"
    assert response.diagnostics.top_dense_score is not None
    assert any(s.retrieval.dense_rank is not None for s in response.sources)


@requires_langchain
def test_provider_errors_in_the_model_call_are_mapped() -> None:
    from app.assistant_rag.errors import ProviderTimeoutError

    with pytest.raises(ProviderUnavailableError, match="ollama serve"):
        invoke(make_chain(ScriptedChatModel(error=httpx.ConnectError("refused"))), ask(GROUNDED_QUESTION))
    with pytest.raises(ProviderTimeoutError):
        invoke(make_chain(ScriptedChatModel(error=httpx.ReadTimeout("slow"))), ask(GROUNDED_QUESTION))
    ollama = pytest.importorskip("ollama")
    missing = ollama.ResponseError("model not found", 404)
    with pytest.raises(ModelUnavailableError, match="ollama pull fake-chat"):
        invoke(make_chain(ScriptedChatModel(error=missing)), ask(GROUNDED_QUESTION))


@requires_langchain
def test_empty_or_thinking_only_answer_is_a_provider_error() -> None:
    with pytest.raises(ProviderUnavailableError, match="empty answer"):
        invoke(make_chain(ScriptedChatModel(reply="<think>hmm</think>  ")), ask(GROUNDED_QUESTION))
    response = invoke(make_chain(ScriptedChatModel(reply="<think>plan</think>Vance shoots 40.4% [S05].")), ask(GROUNDED_QUESTION))
    assert response.answer == "Vance shoots 40.4% [S05]."


@requires_langchain
def test_trace_input_is_echoed_into_the_response() -> None:
    from app.assistant_rag.models import TraceOut

    chain = make_chain(ExplodingChatModel())
    trace = TraceOut(enabled=True, project="p", run_id="r", url=None)
    response = asyncio.run(chain.ainvoke({"request": ask(AMBIGUOUS_QUESTION), "request_id": "x", "trace": trace}))
    assert response.trace == trace
    assert invoke(chain, ask(AMBIGUOUS_QUESTION)).trace.enabled is False
