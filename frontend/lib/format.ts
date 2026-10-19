export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "unavailable";
  return `$${(value / 1_000_000).toFixed(1)}M`;
}

/**
 * How a payroll figure is allowed to be rendered under partial contract coverage (R2c).
 *
 * One function so every surface says the same thing. Three states, and the middle one is
 * the whole release:
 *
 * - **verified** — every rostered player is priced. A plain number.
 * - **floor** — some are not. The number is the sum of the contracts on file, which can
 *   only be *below* the real payroll, so it is prefixed `≥` and carries its coverage.
 *   It is never presented as the payroll and never has a missing salary imputed into it.
 * - **unavailable** — nothing on file. A dash.
 *
 * `note` is not optional decoration: a floor rendered without it is a wrong number.
 */
export function payrollDisclosure(
  verified: number | null | undefined,
  known: number | null | undefined,
  coverage: { players_known: number; players_total: number } | null | undefined,
): { kind: "verified" | "floor" | "unavailable"; value: string; note: string } {
  if (verified !== null && verified !== undefined) {
    return { kind: "verified", value: money(verified), note: "all contracts on file" };
  }
  if (known !== null && known !== undefined && coverage && coverage.players_known > 0) {
    return {
      kind: "floor",
      value: `≥ ${money(known)}`,
      note: `${coverage.players_known} of ${coverage.players_total} contracts · ${
        coverage.players_total - coverage.players_known
      } unknown`,
    };
  }
  return { kind: "unavailable", value: "—", note: "not imported" };
}

/**
 * The backend stores every timestamp in UTC but some endpoints serialise it without an
 * offset, and `new Date()` reads an offset-less ISO date-time as LOCAL time — so one sync
 * rendered as two different times on the same screen. Offset-less date-times are marked
 * as UTC before parsing; anything with `Z` / `±hh:mm`, or a bare date, is left alone.
 */
export function asUtc(iso: string): string {
  if (!/T\d{2}:\d{2}/.test(iso)) return iso;
  return /(Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`;
}

/**
 * A calendar date with no time of day (an expected return date, a deadline). A bare `YYYY-MM-DD` is a *day*, not an
 * instant: parsing it as UTC midnight and printing it in local time showed the previous evening ("Oct 19, 5:00 PM"
 * for Oct 20) and invented a time of day nobody reported.
 */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return formatDate(iso);
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(asUtc(iso));
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Signed one-decimal index value.
 *
 * The sign comes from the **rounded** value. Taking it from the raw one rendered 27 real
 * players as "−0.0" — Draymond Green at −0.0173 rounds to zero but kept a minus sign. A
 * negative-zero guard would not have helped: in JS `-0 >= 0` is true, so a literal `-0`
 * already rendered "+0.0"; every real case was a small negative.
 */
export function tei(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const rounded = Number(value.toFixed(1));
  return rounded >= 0 ? `+${rounded.toFixed(1)}` : rounded.toFixed(1);
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function height(inches: number | null | undefined): string {
  if (!inches) return "—";
  return `${Math.floor(inches / 12)}'${inches % 12}"`;
}

/** Fan-facing trade-rules language; the four-state honesty standard underneath. */
export const LEGALITY_LABEL: Record<string, string> = {
  verified_legal: "Passes rules check",
  verified_illegal: "Fails rules check",
  conditionally_valid: "Incomplete check — data missing",
  not_evaluated: "Not checked — data missing",
};

export const LEGALITY_EXPLAIN: Record<string, string> = {
  verified_legal:
    "Every rule Pivot checks passed with the data on file. Pivot does not check every CBA restriction, so this is not a statement that the deal is legal.",
  verified_illegal: "At least one verified NBA trade rule fails for this deal.",
  conditionally_valid:
    "No checked rule failed, but some checks could not be verified from the data on file — see what was and was not checked below.",
  not_evaluated: "Not enough data was available to run meaningful rule checks.",
};

/** Chip-sized legality wording; the long form stays in the verdict frame. */
export const LEGALITY_SHORT: Record<string, string> = {
  // R9.3A: "Legal" overstated a pass — Pivot checks a subset of the CBA, so a chip says what happened.
  verified_legal: "Passes checks",
  verified_illegal: "Fails a check",
  conditionally_valid: "Incomplete",
  not_evaluated: "Unchecked",
};

/**
 * Monotone in the score. The old labels were not: 46 → "High-risk upside" and 52 →
 * "Mixed outcome" gave the *worse* score the more optimistic word (QA-12).
 *
 * Only the wording changes. The thresholds are unchanged and the bucket keys are
 * renamed to match what each band actually says, so the mismatch cannot come back by
 * someone reading `upside` and writing an optimistic string for it.
 *
 *   ≥ 58  clear win        58 is ~1.6 composite points above neutral on both sides
 *   48–58 roughly neutral  straddles the neutral 50
 *   40–48 net negative
 *   < 40  clear loss
 */
export const VERDICT_LABEL: Record<string, string> = {
  clear_win: "Clear win",
  neutral: "Roughly neutral",
  net_negative: "Net negative",
  clear_loss: "Clear loss",
  unknown: "Cannot fully evaluate",
};

/** One definition, shared by every surface that renders a verdict chip. */
export const VERDICT_STATUS: Record<string, string> = {
  clear_win: "pass",
  neutral: "info",
  net_negative: "warning",
  clear_loss: "fail",
  unknown: "unavailable",
};

/** Best → worst. Exported so callers do not re-derive an ordering of their own. */
export const VERDICT_ORDER = ["clear_win", "neutral", "net_negative", "clear_loss"] as const;

/**
 * Fan verdict from the composite score and the backend's confidence.
 *
 * `confidence` is the backend's own field. The Strategy Lab used to synthesize its own
 * from whether any component was missing, so the same deal could read "Cannot fully
 * evaluate" on one page and "Strong fit" on another (C13).
 */
export function fanVerdict(
  utility: number | null | undefined,
  confidence?: string,
): keyof typeof VERDICT_LABEL {
  if (utility === null || utility === undefined) return "unknown";
  if (confidence === "low" || confidence === "not_applicable") return "unknown";
  if (utility >= 58) return "clear_win";
  if (utility >= 48) return "neutral";
  if (utility >= 40) return "net_negative";
  return "clear_loss";
}

export const NEED_LABEL: Record<string, string> = {
  three_point_volume: "3PT volume",
  defense_overall: "Overall defense",
  offense_overall: "Overall offense",
  defensive_rebounding: "Defensive rebounding",
  playmaking: "Playmaking",
  ball_security: "Ball security",
  rim_protection: "Rim protection",
  point_of_attack_defense: "Point-of-attack defense",
  shooting_efficiency: "Shooting efficiency",
  lineup_size: "Lineup size",
  secondary_creation: "Secondary creation",
};

/**
 * Player skill dimensions, as returned in a fit explanation's `skill_delta`.
 *
 * These were rendered by string-munging the key — `key.replaceAll("_", " ")` plus a CSS
 * `capitalize` — which produced "Turnover Avoidance" and "Team Defense" with no editorial
 * control and no way to attach the caveat a proxy deserves. R4 split four skills out of
 * two, so the vocabulary is now large enough to name properly.
 */
export const SKILL_LABEL: Record<string, string> = {
  // Matched to the server's vocabulary (/intelligence/vocabulary) so a skill has one name
  // everywhere it appears.
  shooting_volume: "Shooting volume",
  shooting_accuracy: "Shooting accuracy",
  creation: "Shot creation",
  turnover_avoidance: "Ball security",
  team_defense: "Team defense",
  rim_protection: "Rim protection",
  rebounding: "Rebounding",
  size: "Positional size",
  scoring: "Scoring",
};

export const COMPONENT_LABEL: Record<string, string> = {
  performance: "On-court impact",
  fit: "Roster fit",
  contract: "Contract value",
  timeline: "Competitive window",
  assets: "Flexibility & future value",
  // Higher is better on every component, including this one (less availability exposure),
  // so it is named for what a high score means.
  risk: "Downside protection",
};

export const COMPONENT_EXPLAIN: Record<string, string> = {
  performance: "Projected change in team performance after reallocating the rotation's minutes.",
  fit: "Whether incoming players address this roster's measured needs without redundancy.",
  contract: "Salary paid vs estimated on-court value (needs contract data).",
  timeline: "How player ages align with the team's competitive window and strategy.",
  assets: "Draft capital, payroll flexibility and roster optionality gained or lost.",
  risk: "Availability exposure: games-played availability of the minutes arriving vs leaving — higher means the deal sheds games-missed risk.",
};

/**
 * English ordinal for a rank or percentile: 1st, 2nd, 3rd, 4th … 93rd.
 * Teens are all "th" (11th, 12th, 13th), which the naive rule gets wrong.
 */
export function ordinal(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Math.round(value);
  const lastTwo = Math.abs(n) % 100;
  const lastOne = Math.abs(n) % 10;
  const suffix =
    lastTwo >= 11 && lastTwo <= 13
      ? "th"
      : lastOne === 1
        ? "st"
        : lastOne === 2
          ? "nd"
          : lastOne === 3
            ? "rd"
            : "th";
  return `${n}${suffix}`;
}

/**
 * "1 deal" / "2 deals" — a count with a noun that agrees with it.
 *
 * A count rendered from `array.length` reaches 1 far more often than it looks like it
 * will, and "1 saved deals" is the kind of thing that reads as a product nobody
 * proofread. Irregular plurals pass an explicit `plural`.
 */
export function count(n: number, singular: string, plural?: string): string {
  return `${n.toLocaleString()} ${n === 1 ? singular : (plural ?? `${singular}s`)}`;
}
