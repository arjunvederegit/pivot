import { z } from "zod";

/**
 * Runtime validation at the API boundary — the zod decision (R1-9).
 *
 * `lib/api.ts` returned `res.json() as Promise<T>`: a bare cast with no runtime check,
 * so a backend shape change rendered `undefined` on screen instead of failing. The
 * project already depended on `zod` without importing it once. The plan's instruction
 * was to *decide*: use it or delete it. It is used, deliberately narrowly.
 *
 * Scope: the responses that carry decision numbers, and only the fields a wrong shape
 * would silently corrupt. R1 changed both of these contracts and R3/R5 change them
 * again, which is exactly when an unchecked cast bites. R7 adds the comparable-trade
 * coverage block for the same reason: it changed shape in this release, and the panel
 * arithmetics on it, so a stale backend renders `undefined of undefined trades` or
 * throws inside the render rather than failing at the boundary.
 *
 * Deliberately **not** validated: everything else. A schema per endpoint would be a
 * second copy of the contract to keep in sync, and drift there would reject valid
 * responses — a worse failure than the one being prevented.
 *
 * `.passthrough()` throughout: unknown keys are additive and must never fail a client.
 */

const decisionStatus = z.enum(["scored", "suppressed_illegal", "insufficient_data"]);

const evaluatedPlayer = z
  .object({
    player_id: z.string(),
    name: z.string(),
    // Nullable on purpose: a player with no impact estimate reports null, never 0.
    tei: z.number().nullable(),
  })
  .passthrough();

const teamEvaluation = z
  .object({
    team_id: z.string(),
    decision_status: decisionStatus,
    composite_utility: z.number().nullable(),
    confidence: z.string(),
    components: z.record(z.string(), z.number().nullable()),
    excluded_components: z.array(z.string()),
    incoming: z.array(evaluatedPlayer),
    outgoing: z.array(evaluatedPlayer),
    // A suppressed evaluation carries `uncertainty: {}` — there is no distribution
    // for a deal that cannot happen — so the key is optional here and required by the
    // refinement below only when the evaluation is scored.
    uncertainty: z
      .object({ prob_positive: z.number().nullable().optional() })
      .passthrough(),
  })
  .passthrough()
  .refine(
    (e) => e.decision_status !== "scored" || e.composite_utility !== null,
    { message: "a scored evaluation must carry a composite utility" },
  )
  .refine(
    (e) => e.decision_status === "scored" || e.composite_utility === null,
    { message: "a suppressed evaluation must not carry a composite utility" },
  )
  .refine(
    (e) => e.decision_status !== "scored" || "prob_positive" in e.uncertainty,
    { message: "a scored evaluation must report prob_positive, even as null" },
  );

/**
 * What the Trade Evaluator page reads from an evaluation without a guard (W2.5), on top of
 * `teamEvaluation`. Kept apart from it because `tradeDetailSchema` shares that schema and a saved
 * trade's evaluation is not the page's concern here.
 *
 * - `legality`, `weights` and `sensitivity_tornado` are on every branch the producer returns; a
 *   refused evaluation carries its weights and an empty tornado.
 * - `uncertainty` is `{}` only when the deal is refused. Every other evaluation, scored or not,
 *   carries `n_draws` and `top_uncertainty_drivers` (also when there is no distribution:
 *   `n_draws: 0`, no drivers, `prob_positive: null`). The band numbers are required only when
 *   `prob_positive` is a number, because that is exactly when the page prints them.
 */
const tornadoBar = z
  .object({
    component: z.string(),
    utility_low: z.number(),
    utility_high: z.number(),
    base: z.number(),
  })
  .passthrough();

const simulatedOutcome = z
  .object({
    n_draws: z.number(),
    top_uncertainty_drivers: z.array(
      z.object({ side: z.string(), spread_wins: z.number() }).passthrough(),
    ),
  })
  .passthrough();

const outcomeBand = z
  .object({ median: z.number(), p10: z.number(), p90: z.number() })
  .passthrough();

const evaluatorTeamEvaluation = teamEvaluation
  .and(
    z
      .object({
        legality: z.object({}).passthrough(),
        weights: z.record(z.string(), z.number()),
        sensitivity_tornado: z.array(tornadoBar),
      })
      .passthrough(),
  )
  .superRefine((e, ctx) => {
    if (e.decision_status === "suppressed_illegal") return;
    const required =
      typeof e.uncertainty.prob_positive === "number"
        ? [simulatedOutcome, outcomeBand]
        : [simulatedOutcome];
    for (const schema of required) {
      const result = schema.safeParse(e.uncertainty);
      if (result.success) continue;
      for (const issue of result.error.issues) {
        ctx.addIssue({
          code: "custom",
          message: issue.message,
          path: ["uncertainty", ...issue.path],
        });
      }
    }
  });

export const evaluateResponseSchema = z
  .object({
    legality: z
      .object({
        overall_status: z.string(),
        // The page maps this to team abbreviations with no guard.
        teams: z.record(z.string(), z.object({ abbreviation: z.string() }).passthrough()),
      })
      .passthrough(),
    evaluations: z.record(z.string(), evaluatorTeamEvaluation),
  })
  .passthrough();

export const tradeDetailSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    legality: z.object({ overall_status: z.string() }).passthrough(),
    evaluations: z.record(z.string(), teamEvaluation),
  })
  .passthrough();

const sourceCard = z
  .object({
    key: z.string(),
    title: z.string(),
    status: z.enum(["fresh", "stale", "derived", "incomplete", "unavailable", "failed"]),
  })
  .passthrough();

export const dataHealthSchema = z
  .object({
    // The product's core claim. A shape change here is how "fresh" came to be rendered
    // over six-day-old data in the first place.
    source_cards: z.array(sourceCard),
    last_successful_sync: z.string().nullable(),
    open_quality_issue_total: z.number(),
    tables: z.record(
      z.string(),
      z.object({ rows: z.number(), stale: z.boolean().nullable() }).passthrough(),
    ),
  })
  .passthrough();

/**
 * Comparable-trade coverage. **Only the coverage block**, because it is the part the
 * panel does arithmetic on — it subtracts `trades_rankable` from `trades_ingested` and
 * formats both — while the neighbours themselves are rendered field by field and a
 * missing one shows as absent rather than as a wrong number.
 *
 * `available: false` responses carry a coverage block too, so nothing here is optional
 * on the unavailable path.
 */
export const comparablesResponseSchema = z
  .object({
    available: z.boolean(),
    coverage: z
      .object({
        trades_ingested: z.number(),
        sides_rankable: z.number(),
        trades_rankable: z.number(),
        sides_blocked_by_unmodelled_players: z.number(),
        // No .min(1): `ComparableTradeService.coverage()` derives this from the
        // corpus, so a database with no transactions imported returns [] — and that
        // same coverage block rides on the `available: false` responses, which is
        // exactly when the corpus is empty. Requiring one season made the empty-corpus
        // path throw instead of rendering its unavailable state.
        seasons_ingested: z.array(z.string()),
        calendar_backed: z.boolean(),
        note: z.string(),
      })
      .passthrough()
      // R7's own invariant. Sides come from trades, so a corpus reporting more rankable
      // trades than it ingested is a join that has gone wrong, not a bigger corpus.
      .refine((c) => c.trades_rankable <= c.trades_ingested, {
        message: "more trades are rankable than were ingested",
      })
      .refine((c) => c.sides_rankable >= c.trades_rankable, {
        message: "fewer rankable sides than rankable trades",
      }),
  })
  .passthrough();

/* ----------------------------------------------------------- intelligence (Pivot) */

/**
 * The `Measurement` envelope every intelligence value arrives in.
 *
 * Validated because it carries an invariant the type system cannot: `value` and `reason`
 * are mutually exclusive, and `available` must agree with which one is present. A backend
 * that returned `available: true` with a null value would render a blank where a number
 * belongs, and a client reading `value` without checking `available` would print "null"
 * as a percentile — the exact class of failure the zod boundary exists for.
 */
const measurement = z
  .object({
    value: z.number().nullable(),
    available: z.boolean(),
    evidence: z.enum(["observed", "derived", "inferred"]).nullable(),
    confidence: z.enum(["validated", "measured", "heuristic", "unavailable"]),
    reason: z.string(),
  })
  .passthrough()
  .refine((m) => m.available === (m.value !== null), {
    message: "available must agree with whether a value is present",
  })
  .refine((m) => m.available || m.reason.length > 0, {
    message: "an unavailable measurement must state why",
  });

export const playerIntelligenceSchema = z
  .object({
    player: z.object({ id: z.string(), full_name: z.string() }).passthrough(),
    season: z.string(),
    skills: z.array(measurement.and(z.object({ key: z.string(), label: z.string() }))),
    skills_measured: z.number(),
    skills_declared: z.number(),
    impact: measurement,
  })
  .passthrough()
  // The gap between declared and measured is the product's honesty claim made numeric.
  // A response where they are equal means the declared-but-unavailable dimensions were
  // dropped somewhere, which is precisely the silent omission Pivot refuses.
  .refine((p) => p.skills_declared >= p.skills_measured, {
    message: "more dimensions measured than declared",
  })
  .refine((p) => p.skills.length === p.skills_declared, {
    message: "the skills array does not carry every declared dimension",
  });

const needRow = z
  .object({
    key: z.string(),
    label: z.string(),
    severity: z.number(),
    percentile: z.number().nullable(),
    addressed_by: z.string().nullable(),
  })
  .passthrough();

export const teamProfileSchema = z
  .object({
    team: z.object({ id: z.string(), abbreviation: z.string() }).passthrough(),
    season: z.string(),
    roster_size: z.number(),
    skill_coverage: z.array(measurement.and(z.object({ key: z.string(), label: z.string() }))),
    needs: z.array(needRow),
    weaknesses: z.array(needRow),
    strengths: z.array(needRow),
    needs_available: z.boolean(),
  })
  .passthrough()
  // QA-9: a franchise once appeared under Strengths *and* Needs for the same row, with a
  // zero-length bar under a caption promising a longer bar meant a larger shortfall. The
  // classification is server-side now, and this is the assertion that it stayed disjoint.
  .refine(
    (p) => {
      const weak = new Set(p.weaknesses.map((r) => r.key));
      return p.strengths.every((r) => !weak.has(r.key));
    },
    { message: "a need appears as both a strength and a weakness" },
  );

export const playerTeamFitSchema = z
  .object({
    player: z.object({ id: z.string(), full_name: z.string() }).passthrough(),
    team: z.object({ id: z.string(), abbreviation: z.string() }).passthrough(),
    available: z.boolean(),
    score: z.number().nullable(),
    already_on_roster: z.boolean(),
  })
  .passthrough()
  // A withheld fit must be withheld, not zeroed. `score: 0` on an unavailable fit would
  // read as "the worst possible fit" rather than "no answer".
  .refine((f) => f.available === (f.score !== null), {
    message: "a fit score must be present exactly when it is available",
  });
