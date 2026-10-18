"""Internal stage-to-stage types of the pipeline (not the API contract — see `models.py`).

Each pipeline stage is a function from one of these to the next, which is what makes each
stage a separately-named, separately-traced LangChain runnable and a separately-testable
plain function. Frozen dataclasses; no LangChain, no pydantic.

    AskRequest ──query_context──▶ QueryContext
               ──entity_resolution──▶ EntityResolution
               ──policy──▶ PolicyDecision
               ──retrieval──▶ RetrievalResult
               ──prompt_construction──▶ PromptPackage
               ──model_call──▶ ModelOutput
               ──citation_check──▶ GroundingReport
               ──final_response──▶ AskResponse
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.assistant_rag.corpus import Chunk


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class QueryContext:
    """The bounded, sanitised request. `history` is what the context budget kept."""

    question: str
    mode: str  # "research" | "roster_fit"
    team_id: str | None
    priorities: dict[str, str]
    previous_priorities: dict[str, str] | None
    constraints: tuple[str, ...]
    history: tuple[HistoryTurn, ...]
    history_received: int
    history_truncated: bool
    prompt_variant: str  # "grounded" | "basic"
    retrieval_variant: str  # "hybrid" | "dense" | "lexical"
    k: int
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EntityRef:
    id: str
    name: str
    kind: str  # "player" | "team" | "club"
    how: str  # "named" | "carried_from_history" | "selected_team"


@dataclass(frozen=True, slots=True)
class Ambiguity:
    mention: str
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class EntityResolution:
    resolved: tuple[EntityRef, ...]
    ambiguous: tuple[Ambiguity, ...]
    #: Real NBA player or team names the question uses. Their presence routes the request
    #: to a deterministic refusal: the sandbox never describes real players from memory.
    unrecognized: tuple[str, ...]
    #: Capitalised names that look like a player or team but exist nowhere — not in the
    #: fictional manifest, not in the real-NBA list ("Corwin Ashby", "the Portland Ospreys").
    #: Without this, a stat question about an invented name retrieves on-topic passages and
    #: invites the model to make the answer up.
    unknown_names: tuple[str, ...] = ()

    @property
    def player_ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.resolved if e.kind == "player")

    @property
    def team_ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self.resolved if e.kind == "team")


@dataclass(frozen=True, slots=True)
class Unsupported:
    kind: str  # models.UnsupportedKind
    message: str


@dataclass(frozen=True)
class PolicyDecision:
    """What the deterministic rules decided before any retrieval or model call.

    `route` is one of:
      "answer"        retrieve and call the model
      "clarify"       ask which entity was meant (ambiguous name, nothing in history)
      "refuse"        unsupported request only (pure legality / fit score / real NBA)
    `unsupported` is populated for both "refuse" and mixed questions ("answer" + caveat).
    """

    route: str
    unsupported: tuple[Unsupported, ...] = ()
    clarification: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: Chunk
    rank: int  # 1-based final rank
    fused_score: float
    dense_score: float | None
    dense_rank: int | None
    lexical_score: float | None
    lexical_rank: int | None
    matched_entities: tuple[str, ...]
    pinned: bool  # included by a deterministic rule (e.g. the selected team's memo)
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalResult:
    query_text: str
    variant: str
    k: int
    selected: tuple[RetrievedChunk, ...]
    candidates: tuple[RetrievedChunk, ...]  # everything scored, selected or not, best first
    top_dense_score: float | None
    min_evidence_threshold: float
    low_evidence: bool
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def source_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for item in self.selected:
            if item.chunk.source_id not in seen:
                seen.append(item.chunk.source_id)
        return tuple(seen)


@dataclass(frozen=True)
class PromptPackage:
    """The messages sent to the model, plus the bookkeeping that explains them."""

    system: str
    user: str
    history: tuple[HistoryTurn, ...]
    variant: str
    approx_tokens: int
    known_gaps: tuple[str, ...]
    known_conflict_ids: tuple[str, ...]


@dataclass(frozen=True)
class ModelOutput:
    text: str
    called: bool
    model: str | None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    ms: float | None = None


@dataclass(frozen=True, slots=True)
class GroundingWarning:
    code: str  # models.WarningCode
    message: str
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundingReport:
    cited_source_ids: tuple[str, ...]
    invalid_citations: tuple[str, ...]  # cited ids not in the retrieved set
    attribution_status: str  # models.AttributionStatus
    attribution_note: str
    warnings: tuple[GroundingWarning, ...]
    limitations: tuple[str, ...]
