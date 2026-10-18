"""The Ask Pivot pipeline as one LangChain runnable chain — the whole RAG loop in one place.

What a Runnable is. LangChain's unit of composition is the `Runnable`: anything with
`invoke` (and `ainvoke`, `batch`, `stream`). `a | b` builds a `RunnableSequence` in which `b`
receives `a`'s output, and `RunnableLambda(func, afunc=...)` turns a plain function (sync,
async, or both) into a runnable. Eight function calls in a row would compute the same answer;
composing them as runnables buys three things:

* every stage becomes a **named run** (`.with_config(run_name=...)`), so a trace — LangSmith,
  or `collect_runs()` in the tests — shows each stage's input, output, timing and error;
* one **config** (callbacks, metadata, tags, root run id) flows from `chain.ainvoke(...,
  config=...)` into every stage, and on into the chat-model call nested inside `model_call`,
  so the model call appears *inside* its stage in the run tree;
* the **async path** comes with it: `ainvoke` awaits the two stages that do I/O, so a
  request waiting on Ollama never blocks the server's event loop.

The state. The chain passes one dict from stage to stage. Each stage reads what earlier
stages produced and returns only its own keys; `_stage` merges them in and records the
stage's wall time. The final stage returns the `AskResponse` itself, which becomes the
chain's output.

    stage                key(s) it adds   what it does                           network?
    query_context        ctx              bound history, options, defaults       no
    entity_resolution    entities         names -> fictional players / teams     no
    policy               decision         answer / clarify / refuse (rules)      no
    retrieval            retrieval        hybrid search (+ forced citations)     embeds the question
    prompt_construction  prompt, shown    system + user messages, budget trim    no
    model_call           model            ChatOllama — or a deterministic reply  generates (answer route only)
    citation_check       grounding        citations, numbers, policy echoes      no
    final_response       (output)         assemble the typed AskResponse         no

Only two stages touch the network, and the language model is called only when policy routes
the question to "answer" *and* retrieval found evidence. A clarification ("which Cruz?"), an
unsupported request (trade legality, a numeric fit score, a real NBA name) and an abstention
(nothing relevant in the corpus) are written by code, cite only what they can, and say so in
`answer_kind` and `attribution_status`.

`sources` in the response are exactly the passages the model was shown (after the prompt's
token budget trimmed any), because a citation is only meaningful against what the model
saw. Everything retrieval scored, kept or not, is in `diagnostics.candidates`.

LangChain is imported lazily (in `build_chain` and the model call), so the stage logic —
`AskStages` and `make_query_context` — is plain Python and testable without the extra.

One chain, one event loop: `ChatOllama` and `OllamaEmbeddings` keep async HTTP connections
that belong to the event loop of their first call, so a chain built here must always be
awaited from the same loop. A server does that naturally; a script that calls
`asyncio.run()` once per question does not. `AssistantService` runs every chain on a loop
it owns — go through it, or keep one loop for the life of a directly-built chain.
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from app.assistant_rag import (
    DATA_MODE,
    PROVIDER_KEY,
    SANDBOX_LABEL,
    epistemics,
    followups,
    grid,
    grounding,
    guard,
    policy,
    prompts,
)
from app.assistant_rag.context import priority_changes
from app.assistant_rag.corpus import Corpus
from app.assistant_rag.entities import resolve_entities
from app.assistant_rag.errors import AssistantError, ProviderUnavailableError
from app.assistant_rag.models import (
    AmbiguityOut,
    AnswerKind,
    AskRequest,
    AskResponse,
    CandidateOut,
    ClaimOut,
    ClaimSummaryOut,
    ConflictOut,
    ContextUsedOut,
    DiagnosticsOut,
    EntitiesOut,
    EntityRefOut,
    GuardOut,
    GuardRemovalOut,
    RetrievalSignalOut,
    SourceOut,
    StageTimingOut,
    TraceOut,
    UnsupportedOut,
    WarningOut,
)
from app.assistant_rag.providers import map_provider_error, model_name
from app.assistant_rag.retrieval import HybridRetriever, RetrievalConfig
from app.assistant_rag.types import (
    HistoryTurn,
    ModelOutput,
    PolicyDecision,
    PromptPackage,
    QueryContext,
    RetrievalResult,
    RetrievedChunk,
)
from app.core.provenance import upstream_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.runnables import Runnable

    from app.config import Settings

State = dict[str, Any]

STAGES = (
    "query_context",
    "entity_resolution",
    "policy",
    "retrieval",
    "prompt_construction",
    "model_call",
    "citation_check",
    "answer_guard",
    "final_response",
)
ROOT_RUN_NAME = "ask_pivot"
MODEL_RUN_NAME = "chat_model"

#: The pipeline's own conversation budget — smaller than the API's 422 bounds. The newest
#: messages win; history may use at most half of the prompt's token budget, so the
#: conversation can never crowd the evidence out of the context window.
PIPELINE_HISTORY_MESSAGES = 10
PIPELINE_HISTORY_CHARS = 12_000
HISTORY_SHARE_OF_PROMPT = 0.5

DETERMINISTIC_CITATION_REASON = "cited by the deterministic reply"
_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|\Z)", re.IGNORECASE | re.DOTALL)


# ------------------------------------------------------------------------- request -> ctx


def history_char_budget(settings: Settings) -> int:
    """Characters of conversation the pipeline keeps: the smaller of the fixed cap and half
    the prompt budget implied by the model's context window."""
    tokens = prompts.context_token_budget(settings.assistant_num_ctx, settings.assistant_max_output_tokens)
    return min(PIPELINE_HISTORY_CHARS, int(tokens * prompts.CHARS_PER_TOKEN * HISTORY_SHARE_OF_PROMPT))


def make_query_context(
    request: AskRequest,
    settings: Settings,
    request_id: str | None,
    *,
    retrieval_config: RetrievalConfig | None = None,
) -> QueryContext:
    """The bounded, defaulted request every later stage reads (pure).

    Dev options (prompt / retrieval variant, k) are applied as given; whether they are
    *allowed* is the service's decision, made before the pipeline runs.

    History is budgeted on what the model will be sent (`prompts.sent_length`): the
    grounded prompt replaces each earlier answer with a ~200-character summary, so charging
    a 3,000-character answer its full length would drop the GM's first message — usually
    the one stating the constraints — while most of the budget went unused. The kept turns
    stay whole: entity resolution reads the full answers."""
    config = retrieval_config or RetrievalConfig()
    turns = tuple(HistoryTurn(role=m.role, content=m.content) for m in request.history)
    options = request.options
    prompt_variant = (options.prompt_variant if options else None) or "grounded"
    history, truncated = prompts.bound_history(
        turns,
        PIPELINE_HISTORY_MESSAGES,
        history_char_budget(settings),
        length=lambda turn: prompts.sent_length(turn, prompt_variant),
    )
    roster_fit = request.mode == "roster_fit" and request.team_id is not None
    priorities = {str(k): str(v) for k, v in request.priorities.items()}
    default_k = config.roster_fit_k if roster_fit else config.k
    return QueryContext(
        question=request.question.strip(),
        mode=request.mode,
        team_id=request.team_id,
        priorities=priorities,
        previous_priorities=None
        if request.previous_priorities is None
        else {str(k): str(v) for k, v in request.previous_priorities.items()},
        constraints=tuple(request.constraints),
        history=history,
        history_received=len(turns),
        history_truncated=truncated,
        prompt_variant=prompt_variant,
        retrieval_variant=(options.retrieval_variant if options else None) or config.default_variant,
        k=(options.k if options else None) or default_k,
        request_id=request_id,
    )


def run_metadata(
    request: AskRequest,
    settings: Settings,
    *,
    corpus_version: str,
    embedding_model: str | None,
    request_id: str | None = None,
    retrieval_config: RetrievalConfig | None = None,
) -> dict[str, Any]:
    """Metadata attached to the root run (and inherited by every child run): what a trace
    must carry to be filtered and compared later."""
    ctx = make_query_context(request, settings, request_id, retrieval_config=retrieval_config)
    return {
        "corpus_version": corpus_version,
        "prompt_variant": ctx.prompt_variant,
        "retrieval_variant": ctx.retrieval_variant,
        "k": ctx.k,
        "chat_model": settings.assistant_chat_model,
        "embedding_model": embedding_model,
        "team_id": ctx.team_id,
        "mode": ctx.mode,
        "data_mode": DATA_MODE,
        "request_id": request_id,
    }


def run_tags(metadata: dict[str, Any]) -> list[str]:
    return [
        "ask-pivot",
        DATA_MODE,
        f"mode:{metadata.get('mode')}",
        f"prompt:{metadata.get('prompt_variant')}",
        f"retrieval:{metadata.get('retrieval_variant')}",
    ]


def run_config(metadata: dict[str, Any], tags: list[str], run_id: str | None = None) -> dict[str, Any]:
    """The `RunnableConfig` for one `chain.ainvoke`. `run_id` fixes the root run's id, so a
    trace can be named before it is uploaded."""
    config: dict[str, Any] = {"run_name": ROOT_RUN_NAME, "metadata": metadata, "tags": tags}
    if run_id:
        config["run_id"] = uuid.UUID(run_id)
    return config


# ----------------------------------------------------------------------------- helpers


@contextmanager
def _provider_errors(settings: Settings, model: str | None) -> Iterator[None]:
    """Re-raise a provider exception as the assistant error that names its fix. The stage
    knows which model it was calling, so the error can name it."""
    try:
        yield
    except AssistantError:
        raise
    except Exception as exc:
        mapped = map_provider_error(exc, settings=settings, model=model)
        if mapped is None:
            raise
        raise mapped from exc


def history_messages(turns: tuple[HistoryTurn, ...]) -> list[Any]:
    """Earlier turns as LangChain chat messages: user -> `HumanMessage`, assistant -> `AIMessage`."""
    if not turns:
        return []
    from langchain_core.messages import AIMessage, HumanMessage

    return [HumanMessage(content=t.content) if t.role == "user" else AIMessage(content=t.content) for t in turns]


def message_text(message: Any) -> str:
    """A chat message's text: `content` is a string, or a list of content blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def strip_reasoning(text: str) -> str:
    """Drop `<think>...</think>` blocks. `reasoning=False` should mean there are none, but a
    model's private reasoning must never be shown as its answer."""
    return _THINK_BLOCK.sub("", text).strip()


#: The limitation added when the guard removed every claim in the model's answer.
ALL_REMOVED_NOTE = (
    "Ask Pivot's checks removed every statement in the model's answer, so nothing here comes from "
    "the sources; the model's original text is kept in diagnostics.raw_answer."
)


def answer_kind(decision: PolicyDecision, model: ModelOutput, guarded: guard.GuardResult | None = None) -> AnswerKind:
    """What kind of reply the GM reads. A model answer the guard emptied — every claim
    replaced by a removal marker — is not a grounded answer: it is reported as a partial
    abstention, with `ALL_REMOVED_NOTE` among the limitations."""
    if decision.route == policy.ROUTE_CLARIFY:
        return "clarification"
    if decision.route == policy.ROUTE_REFUSE:
        return "unsupported_request"
    if not model.called:
        return "abstention"
    if decision.unsupported or (guarded is not None and guarded.removed_everything):
        return "partial_abstention"
    return "grounded_answer"


def _accepts_config(func: Callable[..., Any]) -> bool:
    """LangChain's own convention: a function that declares `config` is handed the run config."""
    try:
        return "config" in inspect.signature(func).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without signatures
        return False


def call_stage(func: Callable[..., Any], state: State, config: Any = None) -> Any:
    return func(state, config=config) if _accepts_config(func) else func(state)


def merge_stage(state: State, name: str, update: Any, started: float) -> Any:
    """Fold a stage's update into the state and record its wall time. A non-dict update is
    the chain's final output and is returned as it is."""
    if not isinstance(update, dict):
        return update
    elapsed = round((perf_counter() - started) * 1000.0, 3)
    return {**state, **update, "timings": (*state.get("timings", ()), (name, elapsed)), "t0": state.get("t0", started)}


# ------------------------------------------------------------------------------ stages


class AskStages:
    """The eight stages as plain methods over the state dict.

    `model` is the runnable that turns `{"system", "history", "user"}` into an AI message —
    in production `ChatPromptTemplate | ChatOllama` (see `build_chain`)."""

    def __init__(
        self,
        *,
        corpus: Corpus,
        retriever: HybridRetriever,
        model: Any,
        settings: Settings,
        chat_model_name: str | None = None,
    ) -> None:
        self.corpus = corpus
        self.manifest = corpus.manifest
        self.retriever = retriever
        self.model = model
        self.settings = settings
        self.chat_model_name = chat_model_name or settings.assistant_chat_model
        self.token_budget = prompts.context_token_budget(
            settings.assistant_num_ctx, settings.assistant_max_output_tokens
        )

    # 1 ------------------------------------------------------------------ query_context
    def query_context(self, state: State) -> State:
        """Validate and bound the request: history budget, variants, k, defaults."""
        request = state["request"]
        if not isinstance(request, AskRequest):
            request = AskRequest.model_validate(request)
        ctx = make_query_context(
            request, self.settings, state.get("request_id"), retrieval_config=self.retriever.config
        )
        return {"request": request, "ctx": ctx}

    # 2 -------------------------------------------------------------- entity_resolution
    def entity_resolution(self, state: State) -> State:
        """Which fictional players and teams the question is about (`entities.py`)."""
        ctx = state["ctx"]
        return {"entities": resolve_entities(ctx.question, ctx.history, ctx.team_id, self.manifest)}

    # 3 ------------------------------------------------------------------------- policy
    def policy(self, state: State) -> State:
        """Answer, clarify or refuse — decided by rules before any search (`policy.py`)."""
        return {"decision": policy.decide(state["ctx"], state["entities"], self.manifest)}

    # 4 ---------------------------------------------------------------------- retrieval
    def retrieval(self, state: State) -> State:
        with _provider_errors(self.settings, self.retriever.embedding_model):
            result = self.retriever.retrieve(state["ctx"], state["entities"])
        return {"retrieval": self.with_deterministic_citations(result, state["decision"])}

    async def aretrieval(self, state: State) -> State:
        """Hybrid search (`retrieval.py`). Runs on every route: a refusal still returns the
        sandbox-limits document it cites, and a clarification still shows what was found."""
        with _provider_errors(self.settings, self.retriever.embedding_model):
            result = await self.retriever.aretrieve(state["ctx"], state["entities"])
        return {"retrieval": self.with_deterministic_citations(result, state["decision"])}

    def with_deterministic_citations(self, result: RetrievalResult, decision: PolicyDecision) -> RetrievalResult:
        """Add the sources a deterministic reply cites (S31 for a refusal) when retrieval did
        not find them — a citation only counts against a returned source. Each is added as
        its document's lead chunk, pinned, after everything retrieval selected."""
        missing = [sid for sid in policy.deterministic_citations(decision) if sid not in result.source_ids]
        if not missing:
            return result
        selected, candidates = list(result.selected), list(result.candidates)
        known = {item.chunk.chunk_id for item in candidates}
        for source_id in missing:
            chunks = self.corpus.chunks_for(source_id)
            if not chunks:
                continue  # not in this corpus: the citation check will flag it as invalid
            lead = min(chunks, key=lambda c: c.ordinal)
            item = RetrievedChunk(
                chunk=lead,
                rank=len(selected) + 1,
                fused_score=0.0,
                dense_score=None,
                dense_rank=None,
                lexical_score=None,
                lexical_rank=None,
                matched_entities=(),
                pinned=True,
                reasons=(DETERMINISTIC_CITATION_REASON,),
            )
            selected.append(item)
            if lead.chunk_id not in known:
                candidates.append(replace(item, rank=len(candidates) + 1))
        return replace(result, selected=tuple(selected), candidates=tuple(candidates))

    # 5 ------------------------------------------------------------ prompt_construction
    def model_answers(self, state: State) -> bool:
        """The language model is called only on the answer route, with evidence to answer from."""
        return state["decision"].route == policy.ROUTE_ANSWER and not state["retrieval"].low_evidence

    def prompt_construction(self, state: State) -> State:
        """Build the system and user messages within the token budget (`prompts.py`).

        `shown` is the retrieval restricted to the passages that survived the budget: a
        passage counts as shown when its source block and its text are in the user message."""
        retrieval = state["retrieval"]
        if not self.model_answers(state):
            return {"prompt": None, "shown": retrieval}
        package = prompts.build_prompt(
            state["ctx"],
            state["entities"],
            state["decision"],
            retrieval,
            self.manifest,
            token_budget=self.token_budget,
        )
        return {"prompt": package, "shown": shown_passages(retrieval, package)}

    # 6 ---------------------------------------------------------------------- model_call
    def deterministic_output(self, state: State) -> ModelOutput:
        ctx, entities, decision = state["ctx"], state["entities"], state["decision"]
        if decision.route == policy.ROUTE_ANSWER:  # answer route, but nothing relevant found
            text = policy.abstention_answer(ctx, entities, self.manifest)
        else:
            text = policy.deterministic_answer(decision, ctx, entities, self.manifest)
        return ModelOutput(text=text, called=False, model=None)

    def _model_input(self, package: PromptPackage) -> dict[str, Any]:
        return {"system": package.system, "history": history_messages(package.history), "user": package.user}

    def _model_output(self, message: Any, started: float) -> ModelOutput:
        text = strip_reasoning(message_text(message))
        if not text:
            raise ProviderUnavailableError(
                f"The local model '{self.chat_model_name}' returned an empty answer. Try again; "
                f"if it repeats, check the model with: ollama run {self.chat_model_name}"
            )
        usage = getattr(message, "usage_metadata", None) or {}
        return ModelOutput(
            text=text,
            called=True,
            model=self.chat_model_name,
            prompt_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            ms=round((perf_counter() - started) * 1000.0, 3),
        )

    def model_call(self, state: State, config: Any = None) -> State:
        if not self.model_answers(state):
            return {"model": self.deterministic_output(state)}
        started = perf_counter()
        with _provider_errors(self.settings, self.chat_model_name):
            message = self.model.invoke(self._model_input(state["prompt"]), config=config)
        return {"model": self._model_output(message, started)}

    async def amodel_call(self, state: State, config: Any = None) -> State:
        """Generate the answer with the chat model — or, on every other route, write the
        deterministic reply. `config` is passed on so the model run nests inside this stage."""
        if not self.model_answers(state):
            return {"model": self.deterministic_output(state)}
        started = perf_counter()
        with _provider_errors(self.settings, self.chat_model_name):
            message = await self.model.ainvoke(self._model_input(state["prompt"]), config=config)
        return {"model": self._model_output(message, started)}

    # 7 ------------------------------------------------------------------ citation_check
    def citation_check(self, state: State) -> State:
        """Check citations against the passages shown, numbers against the fact manifest,
        and the answer for policy echoes (`grounding.py`)."""
        report = grounding.check_answer(
            state["model"].text,
            state["shown"],
            state["entities"],
            state["decision"],
            self.manifest,
            model_called=state["model"].called,
        )
        return {"grounding": report}

    # 8 -------------------------------------------------------------------- answer_guard
    def answer_guard(self, state: State) -> State:
        """Remove sentences the checks can prove unsupported (legality verdicts, fit scores,
        canaries, nonexistent sources, numbers contradicting the fact sheet) — `guard.py`.

        The citation check is re-run on the guarded text, so the citations, attribution
        status and claim labels the GM sees describe the answer they actually read. The
        warnings stay those of the model's raw text: they are the record of what was caught."""
        model: ModelOutput = state["model"]
        if not model.called:
            return {"guarded": guard.GuardResult(model.text, ()), "final_grounding": state["grounding"]}
        guarded = guard.guard_answer(model.text, state["entities"], self.manifest)
        if not guarded.applied:
            return {"guarded": guarded, "final_grounding": state["grounding"]}
        final = grounding.check_answer(
            guarded.text, state["shown"], state["entities"], state["decision"], self.manifest, model_called=True
        )
        return {"guarded": guarded, "final_grounding": final}

    # 9 ------------------------------------------------------------------ final_response
    def final_response(self, state: State) -> AskResponse:
        """Assemble the typed response: answer, sources, checks, context, grid, diagnostics."""
        started = perf_counter()
        ctx: QueryContext = state["ctx"]
        decision: PolicyDecision = state["decision"]
        retrieval: RetrievalResult = state["retrieval"]
        shown: RetrievalResult = state["shown"]
        prompt: PromptPackage | None = state["prompt"]
        model: ModelOutput = state["model"]
        raw_report = state["grounding"]
        guarded: guard.GuardResult = state.get("guarded") or guard.GuardResult(model.text, ())
        report = state.get("final_grounding") or raw_report
        manifest = self.manifest
        answer_text = guarded.text

        kind = answer_kind(decision, model, guarded)
        limitations = list(report.limitations)
        if guarded.removed_everything:
            # before the closing "all sources are fictional" line
            at = len(limitations) - 1 if limitations and limitations[-1] == grounding.FICTIONAL_NOTE else len(limitations)
            limitations.insert(at, ALL_REMOVED_NOTE)
        cited = set(report.cited_source_ids)
        sources = [self.source_out(item, cited) for item in sorted(shown.selected, key=lambda c: c.rank)]
        source_ids = {s.source_id for s in sources}
        history_used = prompt.history if prompt is not None else ctx.history
        # Summarizing an earlier answer (prompts.summarize_assistant_turn) is not truncation;
        # dropping turns, or cutting one to fit the budget, is.
        truncated = ctx.history_truncated or (
            prompt is not None
            and (len(prompt.history) < len(ctx.history) or any(t.content.endswith(prompts.TRUNCATION_MARK) for t in prompt.history))
        )

        warnings = [WarningOut(code=w.code, message=w.message, source_ids=list(w.source_ids)) for w in raw_report.warnings]
        warnings += self.pipeline_warnings(ctx, retrieval, kind, len(history_used), truncated)

        if kind == "abstention":
            follow_ups = policy.in_scope_alternatives(ctx, state["entities"], manifest, limit=followups.MAX_FOLLOW_UPS)
        else:
            follow_ups = followups.suggest_follow_ups(ctx, state["entities"], decision, shown, manifest)

        timings = [StageTimingOut(stage=name, ms=ms) for name, ms in state.get("timings", ())]
        timings += [StageTimingOut(stage=f"retrieval.{k}", ms=v) for k, v in retrieval.timings_ms.items()]
        timings.append(StageTimingOut(stage="final_response", ms=round((perf_counter() - started) * 1000.0, 3)))
        timings.append(StageTimingOut(stage="total", ms=round((perf_counter() - state.get("t0", started)) * 1000.0, 3)))

        shown_ids = {item.chunk.chunk_id for item in shown.selected}
        team = manifest.team(ctx.team_id) if ctx.team_id else None
        # Observed fact / analyst opinion / inference / unavailable, sentence by sentence.
        classified = epistemics.classify_answer(
            answer_text,
            entities=state["entities"],
            manifest=manifest,
            sources={item.chunk.source_id: item.chunk.meta for item in shown.selected},
        )
        trace = state.get("trace") or TraceOut(enabled=False, project=None, run_id=None, url=None)
        return AskResponse(
            answer=answer_text,
            answer_kind=kind,
            data_mode="synthetic_demo",
            label=SANDBOX_LABEL,
            attribution_status=report.attribution_status,
            attribution_note=report.attribution_note,
            sources=sources,
            cited_source_ids=list(report.cited_source_ids),
            invalid_citations=list(report.invalid_citations),
            limitations=limitations,
            warnings=warnings,
            claims=[
                ClaimOut(
                    text=c.text,
                    kind=c.kind,  # type: ignore[arg-type]
                    section=c.section,
                    source_ids=list(c.source_ids),
                    check=c.check,  # type: ignore[arg-type]
                    note=c.note,
                )
                for c in classified
            ],
            claim_summary=ClaimSummaryOut(**epistemics.summarize(classified)),
            guard=GuardOut(
                applied=guarded.applied,
                removals=[
                    GuardRemovalOut(code=r.code, reason=r.reason, sentence=r.sentence)  # type: ignore[arg-type]
                    for r in guarded.removals
                ],
            ),
            conflicts=[
                ConflictOut(id=c.id, summary=c.summary, guidance=c.guidance, source_ids=list(c.source_ids))
                for c in manifest.conflicts_for(source_ids)
            ],
            unsupported_requests=[UnsupportedOut(kind=u.kind, message=u.message) for u in decision.unsupported],  # type: ignore[arg-type]
            entities=self.entities_out(state["entities"]),
            context_used=ContextUsedOut(
                mode=ctx.mode,  # type: ignore[arg-type]
                team_id=ctx.team_id,
                team_name=team.name if team else None,
                priorities=policy.effective_priorities(ctx, manifest) if ctx.mode == "roster_fit" else dict(ctx.priorities),
                priority_changes=priority_changes(ctx.previous_priorities, dict(ctx.priorities), manifest),
                constraints=list(ctx.constraints),
                history_messages_received=ctx.history_received,
                history_messages_used=len(history_used),
                history_truncated=truncated,
                # Everything the GM said is a hypothesis to check, never a document fact.
                gm_statements=[t.content for t in history_used if t.role == "user"] + [ctx.question],
            ),
            evidence_grid=grid.build_evidence_grid(ctx, state["entities"], shown, manifest) if model.called else None,
            follow_ups=follow_ups,
            diagnostics=DiagnosticsOut(
                corpus_version=manifest.corpus_version,
                prompt_variant=ctx.prompt_variant,  # type: ignore[arg-type]
                retrieval_variant=retrieval.variant,  # type: ignore[arg-type]
                k=retrieval.k,
                chat_model=self.chat_model_name,
                embedding_model=self.retriever.embedding_model,
                query_text=retrieval.query_text,
                candidates=[
                    CandidateOut(
                        chunk_id=c.chunk.chunk_id,
                        source_id=c.chunk.source_id,
                        title=c.chunk.meta.title,
                        fused_score=c.fused_score,
                        dense_rank=c.dense_rank,
                        lexical_rank=c.lexical_rank,
                        selected=c.chunk.chunk_id in shown_ids,
                    )
                    for c in retrieval.candidates
                ],
                timings=timings,
                model_called=model.called,
                prompt_tokens=model.prompt_tokens,
                output_tokens=model.output_tokens,
                min_evidence_threshold=retrieval.min_evidence_threshold,
                top_dense_score=retrieval.top_dense_score,
                raw_answer=model.text if guarded.applied else None,
            ),
            trace=trace,
            request_id=ctx.request_id,
        )

    # ------------------------------------------------------------- response pieces
    def source_out(self, item: RetrievedChunk, cited: set[str]) -> SourceOut:
        chunk, meta = item.chunk, item.chunk.meta
        return SourceOut(
            source_id=chunk.source_id,
            version=chunk.version,
            chunk_id=chunk.chunk_id,
            content_sha256=chunk.content_sha256,
            title=meta.title,
            section=chunk.section,
            doc_type=meta.doc_type,  # type: ignore[arg-type]
            author=meta.author,
            author_role=meta.author_role,
            report_date=meta.report_date,
            season=meta.season,
            entity_ids=list(meta.entity_ids),
            team_ids=list(meta.team_ids),
            topics=list(meta.topics),
            trust=meta.trust,  # type: ignore[arg-type]
            evidence_class=epistemics.evidence_class(meta),  # type: ignore[arg-type]
            data_mode="synthetic_demo",
            provenance=upstream_for(PROVIDER_KEY),
            excerpt=chunk.text,
            cited=chunk.source_id in cited,
            retrieval=RetrievalSignalOut(
                rank=item.rank,
                fused_score=item.fused_score,
                dense_score=item.dense_score,
                dense_rank=item.dense_rank,
                lexical_score=item.lexical_score,
                lexical_rank=item.lexical_rank,
                matched_entities=list(item.matched_entities),
                pinned=item.pinned,
                reasons=list(item.reasons),
            ),
        )

    def _ref(self, entity_id: str, name: str, kind: str, how: str) -> EntityRefOut:
        return EntityRefOut(id=entity_id, name=name, kind=cast(Any, kind), how=cast(Any, how))

    def entities_out(self, entities: Any) -> EntitiesOut:
        manifest = self.manifest

        def candidate(entity_id: str) -> EntityRefOut:
            player = manifest.player(entity_id)
            if player is not None:
                return self._ref(entity_id, player.name, "player", "named")
            kind = "team" if manifest.team(entity_id) is not None else "club"
            return self._ref(entity_id, manifest.club_name(entity_id), kind, "named")

        return EntitiesOut(
            resolved=[self._ref(e.id, e.name, e.kind, e.how) for e in entities.resolved],
            ambiguous=[
                AmbiguityOut(mention=a.mention, candidates=[candidate(cid) for cid in a.candidate_ids])
                for a in entities.ambiguous
            ],
            unrecognized=[*entities.unrecognized, *entities.unknown_names],
        )

    def pipeline_warnings(
        self, ctx: QueryContext, retrieval: RetrievalResult, kind: str, history_used: int, truncated: bool
    ) -> list[WarningOut]:
        """Warnings about what the pipeline itself did (the citation check reports the rest)."""
        out: list[WarningOut] = []
        if truncated:
            detail = (
                f"{history_used} of {ctx.history_received} earlier messages were used"
                if history_used < ctx.history_received
                else "the oldest message kept was shortened"
            )
            out.append(
                WarningOut(
                    code="history_truncated",
                    message=f"The conversation was trimmed to fit the context budget: {detail}. "
                    "Anything said only in the dropped part was not taken into account.",
                )
            )
        if kind == "abstention":
            top = "none" if retrieval.top_dense_score is None else f"{retrieval.top_dense_score:.2f}"
            out.append(
                WarningOut(
                    code="low_evidence",
                    message="Retrieval found no convincing evidence and the question names no fictional "
                    f"player or team (top similarity {top}, threshold {retrieval.min_evidence_threshold:.2f}), "
                    "so no model answer was generated.",
                )
            )
        return out


def shown_passages(retrieval: RetrievalResult, package: PromptPackage) -> RetrievalResult:
    """The retrieval result restricted to the passages that made it into the prompt, re-ranked
    1..n. Unchanged when nothing was trimmed."""
    included = set(prompts.included_source_ids(package))
    kept = [
        item
        for item in sorted(retrieval.selected, key=lambda c: c.rank)
        if item.chunk.source_id in included and prompts.neutralise_source_tags(item.chunk.text.strip()) in package.user
    ]
    if len(kept) == len(retrieval.selected):
        return retrieval
    return replace(retrieval, selected=tuple(replace(item, rank=i) for i, item in enumerate(kept, start=1)))


# --------------------------------------------------------------------------- the chain


def _stage(name: str, func: Callable[..., Any], afunc: Callable[..., Any] | None = None) -> Runnable:
    """One stage as a named runnable. Pure stages run inline on the event loop under
    `ainvoke` (no thread hop for a few milliseconds of work); I/O stages give an async
    variant. A stage that declares `config` receives the run config, as with RunnableLambda."""
    from langchain_core.runnables import RunnableLambda

    def run(state: State, config: Any) -> Any:
        started = perf_counter()
        return merge_stage(state, name, call_stage(func, state, config), started)

    async def arun(state: State, config: Any) -> Any:
        started = perf_counter()
        update = await call_stage(afunc, state, config) if afunc is not None else call_stage(func, state, config)
        return merge_stage(state, name, update, started)

    return RunnableLambda(run, afunc=arun, name=name).with_config(run_name=name)


def build_chain(*, corpus: Corpus, retriever: HybridRetriever, chat_model: Any, settings: Settings) -> Runnable:
    """The Ask Pivot RAG chain. Input `{"request": AskRequest, "request_id": str | None,
    "trace": TraceOut | None}`; output `AskResponse`. Invoke with `run_config(...)`."""
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    # The model step: a three-slot chat template piped into the chat model. The slots are
    # filled with values (never parsed as templates), so braces in a source are harmless.
    template = ChatPromptTemplate.from_messages(
        [("system", "{system}"), MessagesPlaceholder("history"), ("human", "{user}")]
    )
    stages = AskStages(
        corpus=corpus,
        retriever=retriever,
        model=(template | chat_model).with_config(run_name=MODEL_RUN_NAME),
        settings=settings,
        chat_model_name=model_name(chat_model),
    )
    chain = (
        _stage("query_context", stages.query_context)
        | _stage("entity_resolution", stages.entity_resolution)
        | _stage("policy", stages.policy)
        | _stage("retrieval", stages.retrieval, stages.aretrieval)  # embeds the question
        | _stage("prompt_construction", stages.prompt_construction)
        | _stage("model_call", stages.model_call, stages.amodel_call)  # the only generation
        | _stage("citation_check", stages.citation_check)
        | _stage("answer_guard", stages.answer_guard)  # removes what the checks can prove wrong
        | _stage("final_response", stages.final_response)
    )
    return chain.with_config(run_name=ROOT_RUN_NAME)
