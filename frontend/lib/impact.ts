"use client";

/**
 * League impact placement — where one player's Pivot Estimated Impact (TEI) sits among
 * every player the model scored.
 *
 * A raw TEI ("+1.8") is a weighted z-score index; it only means something next to the
 * other indices it was scored with. The backend serves that population once
 * (`GET /players/impact-distribution`): every estimate for the current season under the
 * active model, sorted, at the same two-decimal precision every endpoint serves TEI at.
 * Everything here is arithmetic over that list — no scale is invented and no value is
 * stretched.
 *
 * Conventions, identical to `backend/app/services/impact_reference.py`:
 * - percentile: mid-rank — the share of scored players with a lower index, counting
 *   players with an equal index as half. Strictly between 0 and 100.
 * - rank: competition — 1 + the number with a higher index; ties share a rank.
 *
 * Three things this module refuses to do:
 * - place a missing estimate (null → null, never 0 and never the median);
 * - call a percentile a rating — it is a position among measured players;
 * - drop the band: when a 10–90% band is known it is placed too, so the reader sees how
 *   far the position could move.
 */

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "@/lib/api";

/* -------------------------------------------------------------- the contract */

export const impactReferenceSchema = z.discriminatedUnion("available", [
  z.object({
    available: z.literal(false),
    reason: z.string(),
  }),
  z
    .object({
      available: z.literal(true),
      metric: z.string(),
      scale: z.string(),
      season: z.string(),
      model: z.object({
        version: z.string(),
        algorithm: z.string(),
        training_period: z.string().nullable(),
        trained_at: z.string().nullable(),
      }),
      population: z.object({
        size: z.number().int().positive(),
        definition: z.string(),
        min_window_minutes: z.number(),
        rostered_scored: z.number().int(),
        rostered_unscored: z.number().int(),
        unrostered_scored: z.number().int(),
        distinct_values: z.number().int(),
      }),
      values: z.array(z.number()),
      precision: z.number().int(),
      percentile_method: z.string(),
      rank_method: z.string(),
      limitations: z.array(z.string()),
    })
    .refine((r) => r.values.length === r.population.size, {
      message: "population size disagrees with the values served",
    })
    .refine((r) => r.values.every((v, i) => i === 0 || r.values[i - 1] <= v), {
      message: "reference values must be sorted ascending",
    }),
]);

export type ImpactReference = z.infer<typeof impactReferenceSchema>;
export type AvailableImpactReference = Extract<ImpactReference, { available: true }>;

/** One cached fetch per session: the population only changes when the model retrains. */
export function useImpactReference() {
  return useQuery({
    queryKey: ["impact-reference"],
    queryFn: () => api.get<ImpactReference>("/players/impact-distribution", impactReferenceSchema),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/* --------------------------------------------------------------- arithmetic */

/** First index whose value is >= `value` (ascending list). */
function lowerBound(values: readonly number[], value: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (values[mid] < value) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** First index whose value is > `value` (ascending list). */
function upperBound(values: readonly number[], value: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (values[mid] <= value) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Served precision: compare like with like, so a player is always a member of the list. */
function atPrecision(value: number, precision = 2): number {
  const f = 10 ** precision;
  return Math.round(value * f) / f;
}

/** Mid-rank percentile of `value` within ascending `values`, 0 < p < 100. */
export function percentileOf(value: number, values: readonly number[]): number {
  if (values.length === 0) throw new Error("empty reference population");
  const below = lowerBound(values, value);
  const equal = upperBound(values, value) - below;
  return (100 * (below + 0.5 * equal)) / values.length;
}

/** Competition rank (1 = highest index) and how many players share it. */
export function rankOf(value: number, values: readonly number[]): { rank: number; tied: number } {
  const above = values.length - upperBound(values, value);
  const tied = upperBound(values, value) - lowerBound(values, value);
  return { rank: above + 1, tied };
}

/**
 * A factual description of a rank — "Top 5% of scored players" — not a tier. Cut points
 * are plain fractions of the population, stated as such; nothing here says "star".
 */
export function positionLabel(rank: number, size: number): string {
  const share = rank / size;
  if (share <= 0.01) return "Top 1%";
  if (share <= 0.05) return "Top 5%";
  if (share <= 0.1) return "Top 10%";
  if (share <= 0.25) return "Top quarter";
  if (share <= 0.5) return "Upper half";
  if (share <= 0.75) return "Lower half";
  if (share <= 0.9) return "Bottom quarter";
  return "Bottom 10%";
}

export interface ImpactPlacement {
  /** The served index, at served precision. */
  value: number;
  percentile: number;
  rank: number;
  /** Players sharing this exact served value, including this one. */
  tied: number;
  size: number;
  position: string;
  /** The 10–90% band placed in the same population, when the band is known. */
  band: { low: number; high: number; percentileLow: number; percentileHigh: number } | null;
}

/**
 * Place a served TEI in the league population. Returns null — never a default — when
 * there is no estimate or no population to place it in.
 *
 * Only pass values from the *served window estimate* (rosters, player detail, trade
 * evaluation, candidates). Trade-precedent `tei` is a single-season construction and is
 * a different quantity.
 */
export function placeImpact(
  tei: number | null | undefined,
  reference: ImpactReference | undefined,
  band?: { low: number | null | undefined; high: number | null | undefined } | null,
): ImpactPlacement | null {
  if (tei === null || tei === undefined || Number.isNaN(tei)) return null;
  if (!reference || !reference.available || reference.values.length === 0) return null;
  const values = reference.values;
  const value = atPrecision(tei, reference.precision);
  const { rank, tied } = rankOf(value, values);
  const low = band?.low;
  const high = band?.high;
  return {
    value,
    percentile: percentileOf(value, values),
    rank,
    tied,
    size: values.length,
    position: positionLabel(rank, values.length),
    band:
      low !== null && low !== undefined && high !== null && high !== undefined
        ? {
            low,
            high,
            percentileLow: percentileOf(atPrecision(low, reference.precision), values),
            percentileHigh: percentileOf(atPrecision(high, reference.precision), values),
          }
        : null,
  };
}

/**
 * Display a percentile as a whole number without ever showing 0 or 100: the mid-rank
 * percentile is strictly inside (0, 100), and rounding must not claim otherwise.
 */
export function percentileDisplay(p: number): number {
  return Math.min(99, Math.max(1, Math.round(p)));
}

/**
 * The population as a histogram for the distribution strip: `bins` equal-width bins over
 * the served range, as counts. Used only to draw the shape of the league behind a marker.
 */
export function histogram(values: readonly number[], bins = 32) {
  if (values.length === 0) return { min: 0, max: 0, counts: [] as number[], peak: 0 };
  const min = values[0];
  const max = values[values.length - 1];
  const span = max - min || 1;
  const counts = new Array<number>(bins).fill(0);
  for (const v of values) {
    const i = Math.min(bins - 1, Math.floor(((v - min) / span) * bins));
    counts[i] += 1;
  }
  return { min, max, counts, peak: Math.max(...counts) };
}
