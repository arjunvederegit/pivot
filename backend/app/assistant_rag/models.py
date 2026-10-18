"""The typed contract of the Ask Pivot API.

House conventions (`app/api/schemas.py`): plain Pydantic models, snake_case fields, closed
vocabularies as module-level `Literal`s, `*In`/`*Request` for inputs and `*Out` for outputs.
Two deliberate departures, both for the inference route only:

* request models forbid unknown keys. A misspelt `priorites` silently ignored would make the
  assistant ignore the GM's stated goals while appearing to honour them;
* every bound (question length, history length, priorities count) is a hard 422, never a
  silent truncation of what the caller sent. The pipeline then applies its *own* smaller
  context budget, and reports in `context_used` exactly what it kept.

Nothing here imports LangChain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- vocabularies

Mode = Literal["research", "roster_fit"]
PriorityLevel = Literal["must_preserve", "high", "medium", "low"]
PriorityKey = Literal[
    "point_of_attack_defense",
    "wing_defense",
    "team_defense",
    "spacing",
    "secondary_creation",
    "primary_creation",
    "rim_protection",
    "rebounding",
    "size",
    "youth_upside",
    "availability",
]
Role = Literal["user", "assistant"]
PromptVariant = Literal["grounded", "basic"]
RetrievalVariant = Literal["hybrid", "dense", "lexical"]

#: What kind of reply this is. Only `grounded_answer` and `partial_abstention` came from the
#: language model; the other three are deterministic and produced without calling it.
AnswerKind = Literal[
    "grounded_answer",  # the model answered from retrieved sources
    "partial_abstention",  # the model answered part (or the guard removed every claim it made); a deterministic limitation covers the rest
    "abstention",  # nothing in the corpus can answer this; no model call
    "clarification",  # the question is ambiguous (e.g. "Cruz"); no model call
    "unsupported_request",  # legality / fit score / real-NBA request; no model call
]

#: What the citation check found. None of these values means "fact-checked": a valid citation
#: is a pointer to a retrieved passage, not proof that the sentence citing it is true.
AttributionStatus = Literal[
    "citations_present_not_fact_verified",
    "citations_include_invalid_ids",
    "no_citations",
    "not_applicable_no_model_answer",
]

SourceTrust = Literal["internal", "untrusted"]

#: What kind of evidence a whole source is — from its document type, never from the model.
#: Mirrors Pivot's evidence ladder (`app.domain.evidence`): a stat sheet is *observed*; a
#: scout's grade is a named person's *judgment*; a fan letter is a *claim*.
EvidenceClass = Literal[
    "observed_statistic",  # stat profiles: recorded numbers
    "analyst_opinion",  # scouting reports, analytics notes, coaching notes: a dated judgment
    "team_context",  # a front office's own memo: its stated needs and priorities
    "method_guidance",  # how-to-read guides and the sandbox's own limits
    "untrusted_claim",  # agent pitches, public submissions: claims, never facts
]

#: What kind of statement one sentence of an answer is. Assigned deterministically after
#: generation (`epistemics.py`), so the four-way distinction the GM sees does not depend on
#: a 2B model following formatting instructions.
ClaimKind = Literal[
    "observed_fact",  # a statistic (checked against the manifest) or a stat-sheet citation
    "analyst_opinion",  # a cited judgment from a scout, analyst, coach or front office
    "inference",  # the assistant's own conclusion — uncited reasoning, "My read: ..."
    "unavailable",  # what the sources do not contain
    "untrusted_claim",  # something only an untrusted third-party source says
    "question",  # a question back to the GM
]
ClaimCheck = Literal["matches_manifest", "contradicts_manifest", "unverified", "not_numeric"]
DocType = Literal[
    "scouting_report",
    "stat_profile",
    "analytics_note",
    "team_strategy_memo",
    "coaching_note",
    "method_guide",
    "agent_pitch",
    "fan_submission",
]
WarningCode = Literal[
    "invalid_citation",
    "uncited_answer",
    "unverified_number",
    "number_contradicts_manifest",
    "number_for_unavailable_stat",
    "unsupported_legality_claim",
    "invented_fit_score",
    "injection_marker_echoed",
    "untrusted_source_cited",
    "citation_not_supporting",
    "claim_contradicts_assessment",
    "history_truncated",
    "low_evidence",
]
UnsupportedKind = Literal[
    "cba_legality",
    "contracts_and_cap",
    "fit_score",
    "real_nba_entity",
    "unknown_entity",
    "unknown_team",
    "medical",
    "playoff_statistics",
]

# Bounds. The API rejects anything over these (422); the pipeline's own context budget is
# smaller and lives in `pipeline.py`.
MAX_QUESTION_CHARS = 2000
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_MESSAGE_CHARS = 6000
MAX_CONSTRAINTS = 8
MAX_CONSTRAINT_CHARS = 240


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------------------- request


class ChatMessageIn(_Strict):
    role: Role
    content: str = Field(min_length=1, max_length=MAX_HISTORY_MESSAGE_CHARS)


class AskOptionsIn(_Strict):
    """Dev-only experiment knobs. Rejected (422) unless `ASSISTANT_DEV_OPTIONS=true`."""

    prompt_variant: PromptVariant | None = None
    retrieval_variant: RetrievalVariant | None = None
    k: int | None = Field(default=None, ge=1, le=12)


class AskRequest(_Strict):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    mode: Mode = "research"
    #: A fictional team id from the manifest (e.g. "COMETS"). Unknown ids are a 422.
    team_id: str | None = Field(default=None, max_length=40)
    #: The GM's current priorities, e.g. {"point_of_attack_defense": "high"}.
    priorities: dict[PriorityKey, PriorityLevel] = Field(default_factory=dict)
    #: The priorities in force on the previous turn, so the pipeline can name what changed.
    previous_priorities: dict[PriorityKey, PriorityLevel] | None = None
    constraints: list[str] = Field(default_factory=list, max_length=MAX_CONSTRAINTS)
    history: list[ChatMessageIn] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    options: AskOptionsIn | None = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain non-whitespace text")
        return value

    @field_validator("constraints")
    @classmethod
    def _constraints_bounded(cls, value: list[str]) -> list[str]:
        cleaned = [c.strip() for c in value if c.strip()]
        for constraint in cleaned:
            if len(constraint) > MAX_CONSTRAINT_CHARS:
                raise ValueError(f"each constraint must be at most {MAX_CONSTRAINT_CHARS} characters")
        return cleaned


# ------------------------------------------------------------------------------ response


class RetrievalSignalOut(BaseModel):
    """Why this passage was retrieved. Every score is reported raw, never blended into a
    single 'confidence' — a reader can see which retriever found it."""

    rank: int
    fused_score: float
    dense_score: float | None
    dense_rank: int | None
    lexical_score: float | None
    lexical_rank: int | None
    matched_entities: list[str]
    pinned: bool
    reasons: list[str]


class SourceOut(BaseModel):
    source_id: str
    version: str
    chunk_id: str
    content_sha256: str
    title: str
    section: str | None
    doc_type: DocType
    author: str
    author_role: str
    report_date: str
    season: str
    entity_ids: list[str]
    team_ids: list[str]
    topics: list[str]
    trust: SourceTrust
    evidence_class: EvidenceClass
    data_mode: Literal["synthetic_demo"]
    provenance: str
    excerpt: str
    cited: bool
    retrieval: RetrievalSignalOut


class ClaimOut(BaseModel):
    """One sentence of the answer and what kind of statement it is."""

    text: str
    kind: ClaimKind
    section: str | None
    source_ids: list[str]
    check: ClaimCheck
    note: str | None


class GuardRemovalOut(BaseModel):
    code: Literal["legality_verdict", "fit_score", "injection_canary", "nonexistent_source", "wrong_number"]
    reason: str
    sentence: str


class GuardOut(BaseModel):
    """What the answer guard removed from the model's text before the GM saw it (each
    removal is replaced in `answer` by a visible marker). `diagnostics.raw_answer` holds the
    unguarded text."""

    applied: bool
    removals: list[GuardRemovalOut]


class ClaimSummaryOut(BaseModel):
    observed_fact: int = 0
    analyst_opinion: int = 0
    inference: int = 0
    unavailable: int = 0
    untrusted_claim: int = 0
    question: int = 0


class WarningOut(BaseModel):
    code: WarningCode
    message: str
    source_ids: list[str] = []


class ConflictOut(BaseModel):
    id: str
    summary: str
    guidance: str
    source_ids: list[str]


class UnsupportedOut(BaseModel):
    kind: UnsupportedKind
    message: str


class EntityRefOut(BaseModel):
    id: str
    name: str
    kind: Literal["player", "team", "club"]
    how: Literal["named", "carried_from_history", "selected_team"]


class AmbiguityOut(BaseModel):
    mention: str
    candidates: list[EntityRefOut]


class EntitiesOut(BaseModel):
    resolved: list[EntityRefOut]
    ambiguous: list[AmbiguityOut]
    unrecognized: list[str]


class PriorityChangeOut(BaseModel):
    key: PriorityKey
    label: str
    before: PriorityLevel | None
    after: PriorityLevel | None


class ContextUsedOut(BaseModel):
    """Exactly what the pipeline used — so a reader can tell a GM *hypothesis* (anything the
    user said) from a *document fact* (anything cited)."""

    mode: Mode
    team_id: str | None
    team_name: str | None
    priorities: dict[str, str]
    priority_changes: list[PriorityChangeOut]
    constraints: list[str]
    history_messages_received: int
    history_messages_used: int
    history_truncated: bool
    gm_statements: list[str]


class GridCellOut(BaseModel):
    """One documented observation. `grade` is the analyst's letter grade as written in the
    cited source — never a computed score."""

    dimension: PriorityKey
    grade: str | None
    summary: str
    source_id: str | None
    report_date: str | None
    stat_line: str | None
    conflict_id: str | None


class GridRowOut(BaseModel):
    player_id: str
    player_name: str
    current_club: str
    cells: list[GridCellOut]


class EvidenceGridOut(BaseModel):
    """A transparent, deterministic, NON-OFFICIAL comparison: documented observations per GM
    priority, straight from the manifest. It ranks nothing and scores nothing."""

    label: str
    dimensions: list[PriorityKey]
    rows: list[GridRowOut]
    note: str


class StageTimingOut(BaseModel):
    stage: str
    ms: float


class CandidateOut(BaseModel):
    """A retrieval candidate (kept or not) for the debug view."""

    chunk_id: str
    source_id: str
    title: str
    fused_score: float
    dense_rank: int | None
    lexical_rank: int | None
    selected: bool


class DiagnosticsOut(BaseModel):
    corpus_version: str
    prompt_variant: PromptVariant
    retrieval_variant: RetrievalVariant
    k: int
    chat_model: str | None
    embedding_model: str | None
    query_text: str
    candidates: list[CandidateOut]
    timings: list[StageTimingOut]
    model_called: bool
    prompt_tokens: int | None
    output_tokens: int | None
    min_evidence_threshold: float
    top_dense_score: float | None
    #: The model's unguarded text, present only when the guard changed it.
    raw_answer: str | None = None


class TraceOut(BaseModel):
    enabled: bool
    project: str | None
    run_id: str | None
    url: str | None


class AskResponse(BaseModel):
    answer: str
    answer_kind: AnswerKind
    data_mode: Literal["synthetic_demo"]
    label: str
    attribution_status: AttributionStatus
    attribution_note: str
    sources: list[SourceOut]
    cited_source_ids: list[str]
    invalid_citations: list[str]
    limitations: list[str]
    warnings: list[WarningOut]
    claims: list[ClaimOut]
    claim_summary: ClaimSummaryOut
    guard: GuardOut
    conflicts: list[ConflictOut]
    unsupported_requests: list[UnsupportedOut]
    entities: EntitiesOut
    context_used: ContextUsedOut
    evidence_grid: EvidenceGridOut | None
    follow_ups: list[str]
    diagnostics: DiagnosticsOut
    trace: TraceOut
    request_id: str | None


# -------------------------------------------------------------------------------- status


class CorpusStatusOut(BaseModel):
    loaded: bool
    corpus_version: str | None
    documents: int
    chunks: int
    players: int
    teams: int
    error: str | None


class ProviderStatusOut(BaseModel):
    name: Literal["ollama"]
    base_url_is_local: bool
    reachable: bool
    version: str | None
    chat_model: str
    chat_model_available: bool
    embedding_model: str
    embedding_model_available: bool
    error: str | None


class IndexStatusOut(BaseModel):
    built: bool
    chunks: int
    embedding_model: str | None
    error: str | None


class TracingStatusOut(BaseModel):
    enabled: bool
    #: True when an API key is configured. The key itself is never returned.
    api_key_configured: bool
    project: str | None


class DependenciesStatusOut(BaseModel):
    installed: bool
    missing: list[str]
    install_hint: str | None


class StatusOut(BaseModel):
    enabled: bool
    ready: bool
    data_mode: Literal["synthetic_demo"]
    label: str
    issues: list[str]
    dev_options: bool = False
    corpus: CorpusStatusOut | None = None
    provider: ProviderStatusOut | None = None
    index: IndexStatusOut | None = None
    tracing: TracingStatusOut | None = None
    dependencies: DependenciesStatusOut | None = None


# ------------------------------------------------------------------------------- context


class PriorityDimensionOut(BaseModel):
    key: PriorityKey
    label: str
    description: str


class TeamOut(BaseModel):
    id: str
    name: str
    abbreviation: str
    timeline: str
    identity: str
    default_priorities: dict[str, str]
    default_constraints: list[str]
    strategy_source_ids: list[str]


class PlayerOut(BaseModel):
    id: str
    name: str
    position: str
    age: int
    current_club: str
    archetype: str


class StarterOut(BaseModel):
    mode: Mode
    team_id: str | None
    prompt: str
    intent: str


class ContextOut(BaseModel):
    """Everything the UI needs to render the team / mode / priority controls."""

    data_mode: Literal["synthetic_demo"]
    label: str
    disclaimer: str
    corpus_version: str
    league: str
    season: str
    modes: list[Mode]
    priority_levels: list[PriorityLevel]
    priority_dimensions: list[PriorityDimensionOut]
    teams: list[TeamOut]
    players: list[PlayerOut]
    starters: list[StarterOut]
