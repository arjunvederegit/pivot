import { z } from "zod";
import type {
  AskResponse,
  AssistantContext,
  AssistantStatus,
} from "@/lib/assistant-types";

/**
 * Runtime validation of the Ask Pivot responses, at the network boundary.
 *
 * The house rule (`lib/schemas.ts`) is to validate narrowly — only the fields a wrong shape
 * would silently corrupt — and to let unknown keys through (`.passthrough()`), because an
 * additive backend change must never break a client. This file follows it, with one
 * deliberate hard edge:
 *
 * **`data_mode` must be exactly `"synthetic_demo"`**, on the answer and on every source.
 * The whole page is a fictional sandbox and says so on every answer; a response claiming
 * any other data mode is rejected outright rather than rendered under a FICTIONAL label it
 * contradicts (or, worse, rendered as though it were real). Synthetic data must never be
 * displayed as something else, and something else must never be displayed as synthetic.
 *
 * What else is checked, and why:
 *  - `answer_kind` and `attribution_status` are closed enums. The badge that says whether
 *    an answer came from the model, and the line that says what its citations do and do not
 *    prove, are read off these; an unknown value would render as the wrong claim.
 *  - every source's `source_id`, `title` and `excerpt` — a citation chip opens these, and an
 *    `undefined` excerpt would look like an empty document instead of a broken contract.
 *  - `cited_source_ids`, `invalid_citations` — they decide which chips are clickable.
 *  - the debug blocks (`diagnostics`, `trace`) are `.catch(null)`: they are developer
 *    furniture, and a malformed one costs the reader nothing but that panel.
 *
 * Free-text vocabularies the UI only prints (warning codes, doc types, unsupported kinds)
 * are plain strings, so a new code is shown verbatim rather than failing the answer.
 */

const SYNTHETIC_ONLY = {
  error:
    "data_mode must be synthetic_demo — Ask Pivot only renders the fictional sandbox corpus",
} as const;
const syntheticDemo = z.literal("synthetic_demo", SYNTHETIC_ONLY);

const stringList = z.array(z.string());

/* ------------------------------------------------------------------------- answer */

const retrievalSignal = z
  .object({
    rank: z.number(),
    fused_score: z.number(),
    dense_score: z.number().nullable(),
    dense_rank: z.number().nullable(),
    lexical_score: z.number().nullable(),
    lexical_rank: z.number().nullable(),
    matched_entities: stringList.default([]),
    pinned: z.boolean(),
    reasons: stringList,
  })
  .passthrough();

const source = z
  .object({
    source_id: z.string().min(1),
    version: z.string(),
    chunk_id: z.string().min(1),
    content_sha256: z.string(),
    title: z.string(),
    section: z.string().nullable(),
    doc_type: z.string(),
    author: z.string(),
    author_role: z.string(),
    report_date: z.string(),
    season: z.string(),
    entity_ids: stringList.default([]),
    team_ids: stringList.default([]),
    topics: stringList.default([]),
    trust: z.enum(["internal", "untrusted"]),
    evidence_class: z.string().optional(),
    data_mode: syntheticDemo,
    provenance: z.string(),
    excerpt: z.string(),
    cited: z.boolean(),
    retrieval: retrievalSignal,
  })
  .passthrough();

const warning = z
  .object({ code: z.string(), message: z.string(), source_ids: stringList.default([]) })
  .passthrough();

const claim = z
  .object({
    text: z.string(),
    kind: z.string(),
    section: z.string().nullable().default(null),
    source_ids: stringList.default([]),
    check: z.string().default("not_numeric"),
    note: z.string().nullable().default(null),
  })
  .passthrough();

const claimSummary = z
  .object({
    observed_fact: z.number().default(0),
    analyst_opinion: z.number().default(0),
    inference: z.number().default(0),
    unavailable: z.number().default(0),
    untrusted_claim: z.number().default(0),
    question: z.number().default(0),
  })
  .passthrough();

const guardInfo = z
  .object({
    applied: z.boolean(),
    removals: z
      .array(z.object({ code: z.string(), reason: z.string(), sentence: z.string() }).passthrough())
      .default([]),
  })
  .passthrough();

const conflict = z
  .object({
    id: z.string(),
    summary: z.string(),
    guidance: z.string(),
    source_ids: stringList,
  })
  .passthrough();

const unsupported = z.object({ kind: z.string(), message: z.string() }).passthrough();

const entityRef = z
  .object({
    id: z.string(),
    name: z.string(),
    kind: z.enum(["player", "team", "club"]),
    how: z.enum(["named", "carried_from_history", "selected_team"]),
  })
  .passthrough();

const entities = z
  .object({
    resolved: z.array(entityRef).default([]),
    ambiguous: z
      .array(z.object({ mention: z.string(), candidates: z.array(entityRef) }).passthrough())
      .default([]),
    unrecognized: stringList.default([]),
  })
  .passthrough();

const priorityKey = z.enum([
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
]);
const priorityLevel = z.enum(["must_preserve", "high", "medium", "low"]);

const contextUsed = z
  .object({
    mode: z.enum(["research", "roster_fit"]),
    team_id: z.string().nullable(),
    team_name: z.string().nullable(),
    priorities: z.record(z.string(), z.string()),
    priority_changes: z
      .array(
        z
          .object({
            key: priorityKey,
            label: z.string(),
            before: priorityLevel.nullable(),
            after: priorityLevel.nullable(),
          })
          .passthrough(),
      )
      .default([]),
    constraints: stringList.default([]),
    history_messages_received: z.number(),
    history_messages_used: z.number(),
    history_truncated: z.boolean(),
    gm_statements: stringList.default([]),
  })
  .passthrough();

const gridCell = z
  .object({
    dimension: priorityKey,
    grade: z.string().nullable(),
    summary: z.string(),
    source_id: z.string().nullable(),
    report_date: z.string().nullable(),
    stat_line: z.string().nullable(),
    conflict_id: z.string().nullable(),
  })
  .passthrough();

const evidenceGrid = z
  .object({
    label: z.string(),
    dimensions: z.array(priorityKey),
    rows: z.array(
      z
        .object({
          player_id: z.string(),
          player_name: z.string(),
          current_club: z.string(),
          cells: z.array(gridCell),
        })
        .passthrough(),
    ),
    note: z.string(),
  })
  .passthrough();

const diagnostics = z
  .object({
    corpus_version: z.string(),
    prompt_variant: z.enum(["grounded", "basic"]),
    retrieval_variant: z.enum(["hybrid", "dense", "lexical"]),
    k: z.number(),
    chat_model: z.string().nullable(),
    embedding_model: z.string().nullable(),
    query_text: z.string(),
    candidates: z.array(
      z
        .object({
          chunk_id: z.string(),
          source_id: z.string(),
          title: z.string(),
          fused_score: z.number(),
          dense_rank: z.number().nullable(),
          lexical_rank: z.number().nullable(),
          selected: z.boolean(),
        })
        .passthrough(),
    ),
    timings: z.array(z.object({ stage: z.string(), ms: z.number() }).passthrough()),
    model_called: z.boolean(),
    prompt_tokens: z.number().nullable(),
    output_tokens: z.number().nullable(),
    min_evidence_threshold: z.number(),
    top_dense_score: z.number().nullable(),
    raw_answer: z.string().nullable().optional(),
  })
  .passthrough();

const trace = z
  .object({
    enabled: z.boolean(),
    project: z.string().nullable(),
    run_id: z.string().nullable(),
    url: z.string().nullable(),
  })
  .passthrough();

export const askResponseSchema = z
  .object({
    answer: z.string(),
    answer_kind: z.enum([
      "grounded_answer",
      "partial_abstention",
      "abstention",
      "clarification",
      "unsupported_request",
    ]),
    data_mode: syntheticDemo,
    label: z.string(),
    attribution_status: z.enum([
      "citations_present_not_fact_verified",
      "citations_include_invalid_ids",
      "no_citations",
      "not_applicable_no_model_answer",
    ]),
    attribution_note: z.string(),
    sources: z.array(source),
    cited_source_ids: stringList,
    invalid_citations: stringList,
    limitations: stringList.default([]),
    warnings: z.array(warning).default([]),
    // The claim breakdown is explanatory; a malformed one loses only itself, never the answer.
    claims: z.array(claim).default([]).catch([]),
    claim_summary: claimSummary.nullable().default(null).catch(null),
    guard: guardInfo.nullable().default(null).catch(null),
    conflicts: z.array(conflict).default([]),
    unsupported_requests: z.array(unsupported).default([]),
    entities: entities.default({ resolved: [], ambiguous: [], unrecognized: [] }),
    context_used: contextUsed,
    evidence_grid: evidenceGrid.nullable(),
    follow_ups: stringList.default([]),
    diagnostics: diagnostics.nullable().catch(null),
    trace: trace.nullable().catch(null),
    request_id: z.string().nullable().default(null),
  })
  .passthrough();

/* ------------------------------------------------------------------------- status */

export const statusSchema = z
  .object({
    enabled: z.boolean(),
    ready: z.boolean().default(false),
    // Optional so a minimal `{enabled: false}` from a switched-off server still parses;
    // any value other than synthetic_demo is still rejected.
    data_mode: syntheticDemo.optional(),
    label: z.string().default("FICTIONAL DATA / SANDBOX"),
    issues: stringList.default([]),
    dev_options: z.boolean().default(false),
    corpus: z
      .object({
        loaded: z.boolean(),
        corpus_version: z.string().nullable(),
        documents: z.number(),
        chunks: z.number(),
        players: z.number(),
        teams: z.number(),
        error: z.string().nullable(),
      })
      .passthrough()
      .nullable()
      .optional(),
    provider: z
      .object({
        name: z.literal("ollama"),
        base_url_is_local: z.boolean(),
        reachable: z.boolean(),
        version: z.string().nullable(),
        chat_model: z.string(),
        chat_model_available: z.boolean(),
        embedding_model: z.string(),
        embedding_model_available: z.boolean(),
        error: z.string().nullable(),
      })
      .passthrough()
      .nullable()
      .optional(),
    index: z
      .object({
        built: z.boolean(),
        chunks: z.number(),
        embedding_model: z.string().nullable(),
        error: z.string().nullable(),
      })
      .passthrough()
      .nullable()
      .optional(),
    tracing: z
      .object({
        enabled: z.boolean(),
        api_key_configured: z.boolean(),
        project: z.string().nullable(),
      })
      .passthrough()
      .nullable()
      .optional(),
    dependencies: z
      .object({
        installed: z.boolean(),
        missing: stringList,
        install_hint: z.string().nullable(),
      })
      .passthrough()
      .nullable()
      .optional(),
  })
  .passthrough();

/* ------------------------------------------------------------------------ context */

export const contextSchema = z
  .object({
    data_mode: syntheticDemo,
    label: z.string(),
    disclaimer: z.string(),
    corpus_version: z.string(),
    league: z.string(),
    season: z.string(),
    modes: z.array(z.enum(["research", "roster_fit"])),
    priority_levels: z.array(priorityLevel),
    priority_dimensions: z.array(
      z.object({ key: priorityKey, label: z.string(), description: z.string() }).passthrough(),
    ),
    teams: z.array(
      z
        .object({
          id: z.string(),
          name: z.string(),
          abbreviation: z.string(),
          timeline: z.string(),
          identity: z.string(),
          default_priorities: z.record(z.string(), z.string()),
          default_constraints: stringList,
          strategy_source_ids: stringList.default([]),
        })
        .passthrough(),
    ),
    players: z.array(
      z
        .object({
          id: z.string(),
          name: z.string(),
          position: z.string(),
          age: z.number(),
          current_club: z.string(),
          archetype: z.string(),
        })
        .passthrough(),
    ),
    starters: z.array(
      z
        .object({
          mode: z.enum(["research", "roster_fit"]),
          team_id: z.string().nullable(),
          prompt: z.string(),
          intent: z.string(),
        })
        .passthrough(),
    ),
  })
  .passthrough();

/* -------------------------------------------------------------------------- error */

/** Pivot's error envelope: `{"error": {"code", "message", "request_id"}}`. */
export const errorEnvelopeSchema = z.object({
  error: z
    .object({
      code: z.string(),
      message: z.string(),
      request_id: z.string().nullable().optional(),
    })
    .passthrough(),
});

/* ------------------------------------------------------------ compile-time checks */

// The schemas above and the interfaces in assistant-types.ts are two copies of one
// contract. These assignments fail `tsc` if a schema's output stops satisfying its type.
type Satisfies<A, B extends A> = B;
export type _AskResponseCheck = Satisfies<AskResponse, z.output<typeof askResponseSchema>>;
export type _StatusCheck = Satisfies<AssistantStatus, z.output<typeof statusSchema>>;
export type _ContextCheck = Satisfies<AssistantContext, z.output<typeof contextSchema>>;
