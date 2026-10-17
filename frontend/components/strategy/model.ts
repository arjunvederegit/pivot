/**
 * Decision-board arithmetic and shapes.
 *
 * Pure functions only — the page owns state, these own the maths. The scoring rule is the
 * backend's, restated: Σ(w·c) ÷ Σ(w) over the NON-NULL components, so a component that
 * could not be scored drops out and the remaining weights renormalize. Nothing here
 * estimates a missing value.
 */

import type {
  ComparisonAlternative,
  ComparisonResponse,
  TradeDetail,
  Uncertainty,
} from "@/lib/types";

export const COMPONENT_KEYS = [
  "performance",
  "fit",
  "contract",
  "timeline",
  "assets",
  "risk",
] as const;
export type ComponentKey = (typeof COMPONENT_KEYS)[number];

export const MAX_SELECTED = 5;
export const MIN_SELECTED = 2;

/** Names the engine, not the upstream: this page never inspects the rows it scores. */
export const ENGINE_SOURCE = "Pivot evaluation engine over ingested rosters and impact estimates";

/* ------------------------------------------------------------- wire shapes */

/**
 * What `POST /comparisons` actually returns. `lib/types` predates three of these fields;
 * they are read here, optionally, so an older response still renders.
 */
export interface Domination {
  axes_compared: number;
  axes_total: number;
  axes: string[];
}

export type BoardPlayer = { name: string; tei: number | null; player_id?: string };

export type BoardAlternative = Omit<ComparisonAlternative, "uncertainty" | "incoming" | "outgoing"> & {
  /** A suppressed evaluation carries `{}` — there is no distribution for a deal that cannot happen. */
  uncertainty: Partial<Uncertainty> | null;
  incoming: BoardPlayer[];
  outgoing: BoardPlayer[];
  domination?: Domination | null;
};

export type BoardComparison = Omit<ComparisonResponse, "alternatives" | "sensitivity"> & {
  scenario_id?: string | null;
  alternatives: BoardAlternative[];
  sensitivity: {
    first_place_share: Record<string, number>;
    rank_volatility: Record<string, number>;
    /** Absent when nothing on the board could be ranked. */
    median_rank?: Record<string, number>;
    /** How many weight vectors the saved analysis sampled. */
    n_samples?: number;
  };
  excluded_from_ranking?: string[];
};

/** One comparison as it was requested and returned — the dropped deals are the difference. */
export interface Board {
  comparison: BoardComparison;
  /** Trade ids sent to the backend, in selection order. */
  sent: string[];
  /** The scenario the starting weights (and the scored side) came from; "" = league default. */
  scenarioId: string;
}

export type Ranked = { alt: BoardAlternative; score: number }[];

/* ---------------------------------------------------------------- numbers */

/**
 * Signed one-decimal number: +3.2 / −1.4 / +0.0.
 *
 * The sign is taken from the ROUNDED value (as `lib/format` `tei()` does): a raw −0.04
 * used to print "-0.0", a negative zero that reads as a loss.
 */
export function signed(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const rounded = Number(value.toFixed(1));
  return rounded >= 0 ? `+${Math.abs(rounded).toFixed(1)}` : `−${Math.abs(rounded).toFixed(1)}`;
}

/** A served outcome distribution, or null when the response has none to show. */
export function distributionOf(
  u: Partial<Uncertainty> | null | undefined,
): (Uncertainty & { prob_positive: number }) | null {
  if (!u) return null;
  if (
    typeof u.median !== "number" ||
    typeof u.p10 !== "number" ||
    typeof u.p90 !== "number" ||
    typeof u.prob_positive !== "number"
  ) {
    return null;
  }
  return {
    n_draws: u.n_draws ?? 0,
    median: u.median,
    p10: u.p10,
    p90: u.p90,
    prob_positive: u.prob_positive,
    unavailable: u.unavailable,
    top_uncertainty_drivers: u.top_uncertainty_drivers ?? [],
  };
}

/** Tick spacing for a ±axis wins scale: every win up to ±6, then 2s, then 5s. */
export function winsTickStep(axis: number): number {
  return axis <= 6 ? 1 : axis <= 12 ? 2 : 5;
}

/**
 * Whole wins either side of zero that every band on the board fits inside, rounded up to a
 * multiple of the tick step — so the ticks always include 0 (the "no change" line) and land
 * on both ends. A raw ±7 with 2-win ticks used to run −7, −5 … +7 and lose the zero line.
 */
export function sharedWinsAxis(alternatives: BoardAlternative[]): number {
  const reach = Math.max(
    1,
    ...alternatives.flatMap((alt) => {
      const d = distributionOf(alt.uncertainty);
      return d ? [Math.abs(d.p10), Math.abs(d.p90), Math.abs(d.median)] : [];
    }),
  );
  const step = winsTickStep(Math.ceil(reach));
  return Math.ceil(reach / step) * step;
}

/* ---------------------------------------------------------------- scoring */

export function isAvailable(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && !Number.isNaN(value);
}

/**
 * Client-side decision score under a weight vector: Σ(w·c) ÷ Σ(w) over NON-NULL
 * components only — the same renormalization rule the backend applies when a component
 * is unavailable.
 */
export function decisionScore(
  weights: Record<string, number>,
  components: Record<string, number | null>,
): number | null {
  let numerator = 0;
  let denominator = 0;
  for (const key of COMPONENT_KEYS) {
    const component = components[key];
    const weight = weights[key] ?? 0;
    if (isAvailable(component) && weight > 0) {
      numerator += weight * component;
      denominator += weight;
    }
  }
  return denominator > 0 ? numerator / denominator : null;
}

/** Only deals the backend scored ever compete — an illegal deal is never a leader. */
export function rankable(alternatives: BoardAlternative[]): BoardAlternative[] {
  return alternatives.filter((alt) => alt.decision_status === "scored");
}

export function leaderUnder(
  weights: Record<string, number>,
  alternatives: BoardAlternative[],
): BoardAlternative | null {
  let best: BoardAlternative | null = null;
  let bestScore = -Infinity;
  for (const alt of rankable(alternatives)) {
    const score = decisionScore(weights, alt.components);
    if (score !== null && score > bestScore) {
      best = alt;
      bestScore = score;
    }
  }
  return best;
}

/** Backend weights (normalized to ~1) mapped onto 0–100 slider positions. */
export function toSliderWeights(weights: Record<string, number>): Record<string, number> {
  const total = COMPONENT_KEYS.reduce((sum, key) => sum + (weights[key] ?? 0), 0);
  const out: Record<string, number> = {};
  for (const key of COMPONENT_KEYS) {
    out[key] =
      total > 0
        ? Math.round(((weights[key] ?? 0) / total) * 100)
        : Math.round(100 / COMPONENT_KEYS.length);
  }
  return out;
}

export function weightShares(weights: Record<string, number>): Record<string, number> {
  const total = COMPONENT_KEYS.reduce((sum, key) => sum + (weights[key] ?? 0), 0);
  return Object.fromEntries(
    COMPONENT_KEYS.map((key) => [key, total > 0 ? (weights[key] ?? 0) / total : 0]),
  );
}

export function missingComponents(alt: BoardAlternative): ComponentKey[] {
  return COMPONENT_KEYS.filter((key) => !isAvailable(alt.components[key]));
}

/**
 * Live ranking of the stored component scores. A deal that fails a verified rule, that no
 * component could score, or whose every scored component is weighted to zero is listed
 * but never ranked — putting it on the board invites choosing the one that cannot happen.
 */
export function rankBoard(
  comparison: BoardComparison | null,
  weights: Record<string, number>,
): { ranked: Ranked; unrankable: BoardAlternative[] } {
  if (!comparison) return { ranked: [], unrankable: [] };
  const scored: Ranked = [];
  const excluded: BoardAlternative[] = [];
  for (const alt of comparison.alternatives) {
    const score = alt.decision_status === "scored" ? decisionScore(weights, alt.components) : null;
    if (score === null) excluded.push(alt);
    else scored.push({ alt, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return { ranked: scored, unrankable: excluded };
}

/** Why a listed deal is not on the ranking — three different reasons, three sentences. */
export function unrankedReason(alt: BoardAlternative): string {
  if (alt.decision_status === "suppressed_illegal") return "no score — fails a verified rule";
  if (alt.decision_status === "insufficient_data") return "no score — nothing could be scored";
  return "no score under these weights — every component it has is weighted 0";
}

/** Best stored value per component across the given deals (ties all count as best). */
export function bestByComponent(alternatives: BoardAlternative[]): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const key of COMPONENT_KEYS) {
    const values = alternatives
      .map((alt) => alt.components[key])
      .filter((v): v is number => isAvailable(v));
    out[key] = values.length > 0 ? Math.max(...values) : null;
  }
  return out;
}

/** Components on which `alt` beats `other`, largest margin first (both must be scored). */
export function edgesOver(
  alt: BoardAlternative,
  other: BoardAlternative,
): { key: ComponentKey; margin: number }[] {
  const edges: { key: ComponentKey; margin: number }[] = [];
  for (const key of COMPONENT_KEYS) {
    const own = alt.components[key];
    const theirs = other.components[key];
    if (isAvailable(own) && isAvailable(theirs) && own > theirs) {
      edges.push({ key, margin: own - theirs });
    }
  }
  return edges.sort((a, b) => b.margin - a.margin);
}

export interface LeaderExplanation {
  lead: BoardAlternative;
  /** Components where the leader is strictly best among the other RANKABLE deals. */
  strengths: { key: ComponentKey; margin: number }[];
  /** Single priorities that, doubled from their current setting, hand the lead to another deal. */
  flips: { key: ComponentKey; newLeader: string }[];
  /** Dominated deals on the board, with the axes the judgement rested on. */
  dominance: { name: string; by: string; domination: Domination | null }[];
}

/** Plain-English reasons for the current leader, derived only from response data. */
export function explainLeader(
  comparison: BoardComparison,
  lead: BoardAlternative,
  weights: Record<string, number>,
): LeaderExplanation {
  const competitors = rankable(comparison.alternatives).filter((a) => a.trade_id !== lead.trade_id);

  const strengths: { key: ComponentKey; margin: number }[] = [];
  for (const key of COMPONENT_KEYS) {
    const own = lead.components[key];
    if (!isAvailable(own)) continue;
    const others = competitors.map((a) => a.components[key]).filter((v): v is number => isAvailable(v));
    if (others.length === 0) continue;
    const margin = own - Math.max(...others);
    if (margin > 0) strengths.push({ key, margin });
  }
  strengths.sort((a, b) => b.margin - a.margin);

  const flips: { key: ComponentKey; newLeader: string }[] = [];
  for (const key of COMPONENT_KEYS) {
    const current = weights[key] ?? 0;
    if (current <= 0) continue;
    const doubled = { ...weights, [key]: current * 2 };
    const newLeader = leaderUnder(doubled, comparison.alternatives);
    if (newLeader && newLeader.trade_id !== lead.trade_id) {
      flips.push({ key, newLeader: newLeader.name });
    }
  }

  const nameById = Object.fromEntries(comparison.alternatives.map((a) => [a.trade_id, a.name]));
  const dominance = comparison.alternatives
    .filter((a) => a.dominated_by)
    .map((a) => ({
      name: a.name,
      by: nameById[a.dominated_by as string] ?? "another deal",
      domination: a.domination ?? null,
    }));

  return { lead, strengths, flips, dominance };
}

/* ------------------------------------------------------------- deal shape */

export interface DealAsset {
  key: string;
  label: string;
  playerName: string | null;
  playerId: string | null;
}

/** Assets grouped by receiving team — "who gets what", for the selection cards. */
export function receivedByTeam(detail: TradeDetail): { abbreviation: string; items: DealAsset[] }[] {
  return detail.teams
    .map((team) => ({
      abbreviation: team.abbreviation,
      items: detail.assets
        .filter((asset) => asset.to_team_id === team.team_id)
        .map((asset, index): DealAsset => {
          const isPlayer = asset.asset_type === "player";
          return {
            key: `${team.team_id}-${index}`,
            label: isPlayer
              ? (asset.player_name ?? "Unknown player")
              : `${asset.draft_year ?? "Future"} R${asset.round_number ?? "?"} pick${
                  asset.protections ? ` · ${asset.protections}` : ""
                }`,
            playerName: isPlayer ? (asset.player_name ?? "Unknown player") : null,
            playerId: isPlayer ? asset.player_id : null,
          };
        }),
    }))
    .filter((group) => group.items.length > 0);
}

/** Where a spread driver sits. The simulation labels moved players by rotation. */
export function driverSide(side: string): string {
  if (side === "after" || side === "incoming") return "An arriving player";
  if (side === "before" || side === "outgoing") return "A departing player";
  return side;
}
