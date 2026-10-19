/**
 * R9.3A — how Player Explorer words the records of the shared `PlayerIntelligenceService` (R9.2).
 *
 * The backend decides what may be served and on what terms (`serving_decision`); the browser decides only the
 * *words*, and it is built so the wrong words are unreachable: a record is "validated" only when its own model
 * fields say so (status `active` AND evaluation `holdout_confirmed`); every other inferred or projected record is
 * "experimental" and is always labelled. Nothing here computes a statistic.
 */

export type OutputKind = "measured" | "inferred" | "projected";

export interface IntelInterval {
  low: number;
  high: number;
  nominal_level: number;
  method: string;
  calibration: { folds: number; n: number; empirical_coverage: number; evaluation_ref: string } | null;
  kind: "credible" | "prediction" | string;
  interpretation: string;
}

export interface IntelSample {
  games: number | null;
  minutes: number | null;
  attempts: number | null;
  unit: string | null;
  makes: number | null;
  counts_quality: string | null;
  attempts_min: number | null;
  attempts_max: number | null;
  seasons_used: number | null;
}

export interface IntelModel {
  name: string;
  version: string;
  status: string;
  evaluation_status: string;
  evaluation_ref: string | null;
  methodology: string;
}

export interface IntelRecord {
  player_id: string;
  quantity: string;
  unit: string;
  output_kind: OutputKind;
  value: number | null;
  period: { season: string; season_type: string; as_of_season: string | null };
  target_period: { season: string; season_type: string } | null;
  interval: IntelInterval | null;
  sample: IntelSample | null;
  model: IntelModel | null;
  provenance: {
    sources: { provider: string; dataset: string; retrieved_at: string | null }[];
    data_snapshot_id: string | null;
    code_commit: string | null;
  };
  assumptions: string[];
  limitations: string[];
  reliability: number | null;
  conditioning: unknown;
}

export interface IntelUnavailable {
  quantity: string;
  kind: string;
  reason: string;
}

export interface IntelResponse {
  player_id: string;
  as_of: string | null;
  context: Record<string, unknown>;
  records: IntelRecord[];
  reliability_statements: Record<string, string>;
  unavailable: IntelUnavailable[];
  notices: string[];
}

export type EvidenceTier = "measured" | "validated" | "experimental";

/**
 * The only way a modelled value is allowed to read as anything but experimental: the record's own model fields
 * say it is an active, holdout-confirmed model. Nothing in R9.2 meets this (promotion is manual), and a record
 * with a missing model never qualifies.
 */
export function evidenceTier(record: IntelRecord): EvidenceTier {
  if (record.output_kind === "measured") return "measured";
  const m = record.model;
  if (m && m.status === "active" && m.evaluation_status === "holdout_confirmed") return "validated";
  return "experimental";
}

const EVALUATION_WORDS: Record<string, string> = {
  development_only: "development evidence only — not confirmed on held-out data",
  holdout_confirmed: "confirmed on a held-out test, still a candidate",
  benchmarked: "a benchmark method, not a claim of skill",
  failed: "failed its evaluation",
  not_evaluated: "not evaluated",
};

export function evaluationWords(status: string | undefined): string {
  return (status && EVALUATION_WORDS[status]) || (status ? status.replaceAll("_", " ") : "unknown");
}

export const QUANTITY_LABEL: Record<string, string> = {
  ts_pct: "True shooting",
  fg3_pct: "Three-point accuracy",
  ft_pct: "Free-throw accuracy",
  ast_pct: "Assist rate",
  ast_to_ratio: "Assist-to-turnover ratio",
  reb_pct: "Rebound rate",
  oreb_pct: "Offensive rebound rate",
  dreb_pct: "Defensive rebound rate",
  usg_pct: "Usage",
  minutes_per_game: "Minutes per game",
  games_played: "Games played",
};

export const QUANTITY_GROUPS: { label: string; note?: string; quantities: string[] }[] = [
  { label: "Shooting", quantities: ["ts_pct", "fg3_pct", "ft_pct"] },
  { label: "Playmaking", quantities: ["ast_pct", "ast_to_ratio"] },
  { label: "Rebounding", quantities: ["reb_pct", "oreb_pct", "dreb_pct"] },
  {
    label: "Role and availability",
    note: "Usage is a role a coach assigns, not a skill.",
    quantities: ["usg_pct", "minutes_per_game", "games_played"],
  },
];

export function formatQuantity(quantity: string, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (["ts_pct", "fg3_pct", "ft_pct", "ast_pct", "reb_pct", "oreb_pct", "dreb_pct", "usg_pct"].includes(quantity)) {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (quantity === "ast_to_ratio") return value.toFixed(2);
  if (quantity === "minutes_per_game") return value.toFixed(1);
  if (quantity === "games_played") return String(Math.round(value));
  return String(value);
}

export function formatInterval(quantity: string, i: IntelInterval | null): string | null {
  if (!i) return null;
  return `${formatQuantity(quantity, i.low)} – ${formatQuantity(quantity, i.high)} (${Math.round(i.nominal_level * 100)}% ${
    i.kind === "credible" ? "credible range" : "prediction range"
  })`;
}

/** "47 of 143 three-point attempts" — what the figure rests on, with a flag when it is a reconstruction. */
export function sampleSentence(record: IntelRecord): string | null {
  const s = record.sample;
  if (!s) return null;
  const parts: string[] = [];
  if (s.makes !== null && s.attempts !== null) {
    parts.push(`${Math.round(s.makes)} of ${Math.round(s.attempts)}${s.unit ? ` ${s.unit}` : ""}`);
  } else if (s.attempts !== null) {
    parts.push(`${Math.round(s.attempts).toLocaleString("en-US")}${s.unit ? ` ${s.unit}` : ""}`);
  }
  if (s.games !== null && record.output_kind === "measured") parts.push(`${s.games} games`);
  if (s.counts_quality && s.counts_quality !== "exact") parts.push(`counts ${s.counts_quality} (reconstructed)`);
  return parts.length ? parts.join(" · ") : null;
}

/** A measured figure from a thin season carries its own caution; the thresholds are descriptive, not statistical. */
export function thinSample(record: IntelRecord): boolean {
  const s = record.sample;
  if (!s) return false;
  if (s.games !== null && s.games < 20) return true;
  if (s.minutes !== null && s.minutes < 300) return true;
  return false;
}

/** The statement that must accompany any projection: what it is conditional on. */
export function conditioningSentence(record: IntelRecord): string | null {
  if (record.output_kind !== "projected") return null;
  return record.interval?.interpretation ?? "conditional on the player playing in the target season";
}
