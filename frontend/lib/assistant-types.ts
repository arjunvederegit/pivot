/**
 * The Ask Pivot API contract, in TypeScript.
 *
 * A field-for-field mirror of `backend/app/assistant_rag/models.py` (snake_case kept, so a
 * response can be read against the Pydantic models line by line). The backend file is the
 * source of truth; when it changes, this file and `lib/assistant-schemas.ts` change with it.
 *
 * Two kinds of thing live here:
 *  - the closed vocabularies (`AnswerKind`, `AttributionStatus`, …) as string unions plus a
 *    runtime array where the UI needs to iterate them;
 *  - the request/response shapes. Nothing here validates — `assistant-schemas.ts` does that
 *    at the network boundary, and only for the fields the page renders.
 *
 * Everything this API serves is FICTIONAL (`data_mode: "synthetic_demo"`): invented teams,
 * players, scouts and reports in a sandbox league. None of it is NBA data.
 */

/* ------------------------------------------------------------------ vocabularies */

export type AssistantMode = "research" | "roster_fit";
export const ASSISTANT_MODES: readonly AssistantMode[] = ["research", "roster_fit"];

export type PriorityLevel = "must_preserve" | "high" | "medium" | "low";
/** Strongest first — the order chips and selects list them in. */
export const PRIORITY_LEVELS: readonly PriorityLevel[] = ["must_preserve", "high", "medium", "low"];

export type PriorityKey =
  | "point_of_attack_defense"
  | "wing_defense"
  | "team_defense"
  | "spacing"
  | "secondary_creation"
  | "primary_creation"
  | "rim_protection"
  | "rebounding"
  | "size"
  | "youth_upside"
  | "availability";

export const PRIORITY_KEYS: readonly PriorityKey[] = [
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
];

/** The GM's stated priorities. Partial: a priority the GM has not set is absent, not "low". */
export type Priorities = Partial<Record<PriorityKey, PriorityLevel>>;

export type ChatRole = "user" | "assistant";
export type PromptVariant = "grounded" | "basic";
export type RetrievalVariant = "hybrid" | "dense" | "lexical";

/**
 * What kind of reply this is. Only `grounded_answer` and `partial_abstention` came from the
 * language model; the other three are deterministic and were produced without calling it.
 */
export type AnswerKind =
  | "grounded_answer"
  | "partial_abstention"
  | "abstention"
  | "clarification"
  | "unsupported_request";
export const ANSWER_KINDS: readonly AnswerKind[] = [
  "grounded_answer",
  "partial_abstention",
  "abstention",
  "clarification",
  "unsupported_request",
];

/**
 * What the citation check found. **None of these means "fact-checked"**: a valid citation is
 * a pointer to a retrieved passage, not proof that the sentence citing it is true.
 */
export type AttributionStatus =
  | "citations_present_not_fact_verified"
  | "citations_include_invalid_ids"
  | "no_citations"
  | "not_applicable_no_model_answer";
export const ATTRIBUTION_STATUSES: readonly AttributionStatus[] = [
  "citations_present_not_fact_verified",
  "citations_include_invalid_ids",
  "no_citations",
  "not_applicable_no_model_answer",
];

export type SourceTrust = "internal" | "untrusted";

/**
 * What kind of evidence a whole source is — from its document type in the manifest, never
 * from the model. A stat sheet is observed; a scout's report is a dated opinion; an agent
 * pitch or a fan letter is a claim.
 */
export type EvidenceClass =
  | "observed_statistic"
  | "analyst_opinion"
  | "team_context"
  | "method_guidance"
  | "untrusted_claim";

/** What kind of statement one sentence of an answer is (assigned after generation). */
export type ClaimKind =
  | "observed_fact"
  | "analyst_opinion"
  | "inference"
  | "unavailable"
  | "untrusted_claim"
  | "question";

export type ClaimCheck = "matches_manifest" | "contradicts_manifest" | "unverified" | "not_numeric";

export type DocType =
  | "scouting_report"
  | "stat_profile"
  | "analytics_note"
  | "team_strategy_memo"
  | "coaching_note"
  | "method_guide"
  | "agent_pitch"
  | "fan_submission";

export type WarningCode =
  | "invalid_citation"
  | "uncited_answer"
  | "unverified_number"
  | "number_contradicts_manifest"
  | "number_for_unavailable_stat"
  | "unsupported_legality_claim"
  | "invented_fit_score"
  | "injection_marker_echoed"
  | "untrusted_source_cited"
  | "citation_not_supporting"
  | "claim_contradicts_assessment"
  | "history_truncated"
  | "low_evidence";

export type UnsupportedKind =
  | "cba_legality"
  | "contracts_and_cap"
  | "fit_score"
  | "real_nba_entity"
  | "unknown_entity"
  | "unknown_team"
  | "medical"
  | "playoff_statistics";

/* Request bounds. The API answers anything over these with a 422 rather than truncating. */
export const MAX_QUESTION_CHARS = 2000;
export const MAX_HISTORY_MESSAGES = 20;
export const MAX_HISTORY_MESSAGE_CHARS = 6000;
export const MAX_CONSTRAINTS = 8;
export const MAX_CONSTRAINT_CHARS = 240;

/* ----------------------------------------------------------------------- request */

export interface ChatMessageIn {
  role: ChatRole;
  content: string;
}

/** Dev-only experiment knobs; the backend rejects them unless `ASSISTANT_DEV_OPTIONS=true`. */
export interface AskOptionsIn {
  prompt_variant?: PromptVariant | null;
  retrieval_variant?: RetrievalVariant | null;
  k?: number | null;
}

export interface AskRequest {
  question: string;
  mode: AssistantMode;
  /** A fictional team id from `/assistant/context` (e.g. "COMETS"); unknown ids are a 422. */
  team_id: string | null;
  priorities: Priorities;
  /** The priorities in force on the previous answered turn, so the backend can name changes. */
  previous_priorities: Priorities | null;
  constraints: string[];
  history: ChatMessageIn[];
  options?: AskOptionsIn | null;
}

/* ---------------------------------------------------------------------- response */

/** Why a passage was retrieved. Scores are raw — never blended into one "confidence". */
export interface RetrievalSignal {
  rank: number;
  fused_score: number;
  dense_score: number | null;
  dense_rank: number | null;
  lexical_score: number | null;
  lexical_rank: number | null;
  matched_entities: string[];
  pinned: boolean;
  reasons: string[];
}

export interface AssistantSource {
  source_id: string;
  version: string;
  chunk_id: string;
  content_sha256: string;
  title: string;
  section: string | null;
  doc_type: DocType | string;
  author: string;
  author_role: string;
  report_date: string;
  season: string;
  entity_ids: string[];
  team_ids: string[];
  topics: string[];
  trust: SourceTrust;
  /** Optional so an older server's response still renders. */
  evidence_class?: EvidenceClass | string;
  data_mode: "synthetic_demo";
  provenance: string;
  excerpt: string;
  cited: boolean;
  retrieval: RetrievalSignal;
}

export interface AssistantWarning {
  code: WarningCode | string;
  message: string;
  source_ids: string[];
}

export interface AssistantClaim {
  text: string;
  kind: ClaimKind | string;
  section: string | null;
  source_ids: string[];
  check: ClaimCheck | string;
  note: string | null;
}

export interface GuardRemoval {
  code: "legality_verdict" | "fit_score" | "injection_canary" | "nonexistent_source" | "wrong_number" | string;
  reason: string;
  sentence: string;
}

/** What the backend's answer guard removed before showing the answer (null from older servers). */
export interface GuardInfo {
  applied: boolean;
  removals: GuardRemoval[];
}

export interface ClaimSummary {
  observed_fact: number;
  analyst_opinion: number;
  inference: number;
  unavailable: number;
  untrusted_claim: number;
  question: number;
}

export interface AssistantConflict {
  id: string;
  summary: string;
  guidance: string;
  source_ids: string[];
}

export interface UnsupportedRequest {
  kind: UnsupportedKind | string;
  message: string;
}

export interface EntityRef {
  id: string;
  name: string;
  kind: "player" | "team" | "club";
  how: "named" | "carried_from_history" | "selected_team";
}

export interface Ambiguity {
  mention: string;
  candidates: EntityRef[];
}

export interface Entities {
  resolved: EntityRef[];
  ambiguous: Ambiguity[];
  unrecognized: string[];
}

export interface PriorityChange {
  key: PriorityKey;
  label: string;
  before: PriorityLevel | null;
  after: PriorityLevel | null;
}

/** Exactly what the pipeline used — a GM *hypothesis* (anything said) vs a *document fact*. */
export interface ContextUsed {
  mode: AssistantMode;
  team_id: string | null;
  team_name: string | null;
  priorities: Record<string, string>;
  priority_changes: PriorityChange[];
  constraints: string[];
  history_messages_received: number;
  history_messages_used: number;
  history_truncated: boolean;
  gm_statements: string[];
}

/** One documented observation. `grade` is the analyst's letter as written — never computed. */
export interface GridCell {
  dimension: PriorityKey;
  grade: string | null;
  summary: string;
  source_id: string | null;
  report_date: string | null;
  stat_line: string | null;
  conflict_id: string | null;
}

export interface GridRow {
  player_id: string;
  player_name: string;
  current_club: string;
  cells: GridCell[];
}

/** Documented observations per GM priority. It ranks nothing and scores nothing. */
export interface EvidenceGrid {
  label: string;
  dimensions: PriorityKey[];
  rows: GridRow[];
  note: string;
}

export interface StageTiming {
  stage: string;
  ms: number;
}

export interface RetrievalCandidate {
  chunk_id: string;
  source_id: string;
  title: string;
  fused_score: number;
  dense_rank: number | null;
  lexical_rank: number | null;
  selected: boolean;
}

export interface Diagnostics {
  corpus_version: string;
  prompt_variant: PromptVariant;
  retrieval_variant: RetrievalVariant;
  k: number;
  chat_model: string | null;
  embedding_model: string | null;
  query_text: string;
  candidates: RetrievalCandidate[];
  timings: StageTiming[];
  model_called: boolean;
  prompt_tokens: number | null;
  output_tokens: number | null;
  min_evidence_threshold: number;
  top_dense_score: number | null;
  /** The model's unguarded text, present only when the answer guard changed it. */
  raw_answer?: string | null;
}

export interface TraceInfo {
  enabled: boolean;
  project: string | null;
  run_id: string | null;
  url: string | null;
}

export interface AskResponse {
  answer: string;
  answer_kind: AnswerKind;
  data_mode: "synthetic_demo";
  label: string;
  attribution_status: AttributionStatus;
  attribution_note: string;
  sources: AssistantSource[];
  cited_source_ids: string[];
  invalid_citations: string[];
  limitations: string[];
  warnings: AssistantWarning[];
  /** Sentence-by-sentence kinds of statement; empty from an older server. */
  claims: AssistantClaim[];
  claim_summary: ClaimSummary | null;
  guard: GuardInfo | null;
  conflicts: AssistantConflict[];
  unsupported_requests: UnsupportedRequest[];
  entities: Entities;
  context_used: ContextUsed;
  evidence_grid: EvidenceGrid | null;
  follow_ups: string[];
  /**
   * Debug-only blocks. `null` when the server's block did not match the expected shape:
   * a malformed diagnostics block must not cost the reader the answer itself.
   */
  diagnostics: Diagnostics | null;
  trace: TraceInfo | null;
  request_id: string | null;
}

/* ------------------------------------------------------------------------ status */

export interface CorpusStatus {
  loaded: boolean;
  corpus_version: string | null;
  documents: number;
  chunks: number;
  players: number;
  teams: number;
  error: string | null;
}

export interface ProviderStatus {
  name: "ollama";
  base_url_is_local: boolean;
  reachable: boolean;
  version: string | null;
  chat_model: string;
  chat_model_available: boolean;
  embedding_model: string;
  embedding_model_available: boolean;
  error: string | null;
}

export interface IndexStatus {
  built: boolean;
  chunks: number;
  embedding_model: string | null;
  error: string | null;
}

export interface TracingStatus {
  enabled: boolean;
  /** Only whether a key is configured. The key itself is never returned. */
  api_key_configured: boolean;
  project: string | null;
}

export interface DependenciesStatus {
  installed: boolean;
  missing: string[];
  install_hint: string | null;
}

export interface AssistantStatus {
  enabled: boolean;
  ready: boolean;
  data_mode?: "synthetic_demo";
  label: string;
  issues: string[];
  dev_options: boolean;
  corpus?: CorpusStatus | null;
  provider?: ProviderStatus | null;
  index?: IndexStatus | null;
  tracing?: TracingStatus | null;
  dependencies?: DependenciesStatus | null;
}

/* ----------------------------------------------------------------------- context */

export interface PriorityDimension {
  key: PriorityKey;
  label: string;
  description: string;
}

export interface AssistantTeam {
  id: string;
  name: string;
  abbreviation: string;
  timeline: string;
  identity: string;
  default_priorities: Record<string, string>;
  default_constraints: string[];
  strategy_source_ids: string[];
}

export interface AssistantPlayer {
  id: string;
  name: string;
  position: string;
  age: number;
  current_club: string;
  archetype: string;
}

export interface Starter {
  mode: AssistantMode;
  team_id: string | null;
  prompt: string;
  intent: string;
}

/** Everything the UI needs to render the team / mode / priority controls. */
export interface AssistantContext {
  data_mode: "synthetic_demo";
  label: string;
  disclaimer: string;
  corpus_version: string;
  league: string;
  season: string;
  modes: AssistantMode[];
  priority_levels: PriorityLevel[];
  priority_dimensions: PriorityDimension[];
  teams: AssistantTeam[];
  players: AssistantPlayer[];
  starters: Starter[];
}

/* ------------------------------------------------------------------- type guards */

export function isPriorityKey(value: string): value is PriorityKey {
  return (PRIORITY_KEYS as readonly string[]).includes(value);
}

export function isPriorityLevel(value: string): value is PriorityLevel {
  return (PRIORITY_LEVELS as readonly string[]).includes(value);
}

/**
 * Keep only the entries the API accepts. A team's `default_priorities` arrive typed as
 * `dict[str, str]`; sending an unknown key back would be a 422, so it is dropped here
 * instead — the UI never shows a priority it could not send.
 */
export function sanitizePriorities(raw: Record<string, string> | null | undefined): Priorities {
  const out: Priorities = {};
  for (const [key, level] of Object.entries(raw ?? {})) {
    if (isPriorityKey(key) && isPriorityLevel(level)) out[key] = level;
  }
  return out;
}

/**
 * A team's own starting position, in the shape the API accepts: its default priorities
 * (unknown keys dropped) and its default constraints (bounded to what the API allows).
 */
export function teamDefaults(
  team: Pick<AssistantTeam, "default_priorities" | "default_constraints">,
): { priorities: Priorities; constraints: string[] } {
  return {
    priorities: sanitizePriorities(team.default_priorities),
    constraints: team.default_constraints
      .slice(0, MAX_CONSTRAINTS)
      .map((c) => c.slice(0, MAX_CONSTRAINT_CHARS)),
  };
}
