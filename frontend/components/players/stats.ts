/**
 * Percentile plumbing for the Player Explorer.
 *
 * Every percentile on the explorer — the micro-bar under each directory cell and every
 * dot on the comparison axes — is computed the same way, and the way is the one
 * `lib/playerStats` already defines and tests:
 *
 * - the population for a column is the players whose sample reaches `QUALIFY_MIN` of that
 *   column's own denominator (games for a per-game stat, attempts for a percentage);
 * - it is the whole imported league, never the filtered view — narrowing the list must
 *   not quietly redefine what "league percentile" means;
 * - a player under the line keeps his value but gets no percentile (null), which the UI
 *   renders as "n/a · sample" — never as zero and never as the median.
 */

import { ordinal } from "@/lib/format";
import { QUALIFY_MIN, numOrNull, percentileOf, qualifies, statValue } from "@/lib/playerStats";
import type { Mode, StatColumn } from "@/lib/playerStats";
import type { SeasonTotalsPlayer } from "@/lib/types";

/**
 * A percentile as a whole-number ordinal, held to 1st–99th.
 *
 * `percentileOf` is "share at or below", so 99.6 and 99.8 both round to 100 and three
 * different scorers all read "100th" — a tie the data does not contain. Holding the
 * printed figure inside 1–99 is the convention the impact readouts already use
 * (`lib/impact` `percentileDisplay`); the bars still draw the unrounded value.
 */
export function percentileText(percentile: number): string {
  return ordinal(Math.min(99, Math.max(1, Math.round(percentile))));
}

/**
 * Where a percentile sits on a comparison axis on which right is always better. For a
 * lower-is-better stat (turnovers) that is the share of qualified players with a HIGHER
 * value — 100 minus the share at or below — so a careful ball-handler plots right, like
 * a good shooter does, instead of every row but one reading left-to-right.
 */
export function axisPercentile(percentile: number, better: "high" | "low"): number {
  return better === "low" ? 100 - percentile : percentile;
}

/** Sorted qualified values per column key. */
export type Populations = Record<string, number[]>;

export function leaguePopulations(
  players: SeasonTotalsPlayer[],
  columns: StatColumn[],
  mode: Mode,
): Populations {
  const out: Populations = {};
  for (const column of columns) {
    const values: number[] = [];
    for (const player of players) {
      if (!qualifies(player, column)) continue;
      const value = statValue(player, column, mode);
      if (value !== null) values.push(value);
    }
    values.sort((a, b) => a - b);
    out[column.key] = values;
  }
  return out;
}

export interface PlacedValue {
  value: number | null;
  /** The player's sample reaches QUALIFY_MIN of this column's denominator. */
  qualified: boolean;
  /** 0–100 share of qualified players at or below; null when unqualified or absent. */
  percentile: number | null;
}

export function placeValue(
  player: SeasonTotalsPlayer,
  column: StatColumn,
  mode: Mode,
  populations: Populations,
): PlacedValue {
  const value = statValue(player, column, mode);
  const qualified = qualifies(player, column);
  const population = populations[column.key] ?? [];
  const percentile =
    qualified && value !== null && population.length > 0 ? percentileOf(population, value) : null;
  return { value, qualified, percentile };
}

/** "games" for a per-game stat, "attempts" (or the named count) for a rate. */
export function sampleUnit(column: StatColumn): string {
  if (column.kind === "counting") return "games";
  if (column.key === "AST_TOV") return "turnovers";
  return "attempts";
}

export function qualifierText(column: StatColumn): string {
  return `${QUALIFY_MIN}+ ${sampleUnit(column)}`;
}

/* ------------------------------------------------------------ comparison rows */

const games = (p: SeasonTotalsPlayer) => p.gp ?? 0;
const totalOf = (key: string) => (p: SeasonTotalsPlayer) => numOrNull(p.totals?.[key]) ?? 0;

export interface CompareColumn extends StatColumn {
  better: "high" | "low";
  group: "line" | "shooting";
}

/**
 * The comparison always reads the per-game line plus rates, so its scale never changes
 * with the directory's totals/per-game toggle. Each row carries the denominator its own
 * qualification is measured in — AST/TOV divides by turnovers, so that is what fifteen
 * of is counted in.
 */
export const COMPARE_COLUMNS: CompareColumn[] = [
  {
    key: "MIN",
    label: "MIN",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "PTS",
    label: "PTS",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "REB",
    label: "REB",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "AST",
    label: "AST",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "STL",
    label: "STL",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "BLK",
    label: "BLK",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "TOV",
    label: "TOV",
    kind: "counting",
    denominator: games,
    better: "low",
    group: "line",
  },
  {
    key: "EFF",
    label: "EFF",
    kind: "counting",
    denominator: games,
    better: "high",
    group: "line",
  },
  {
    key: "FG_PCT",
    label: "FG%",
    kind: "rate",
    denominator: totalOf("FGA"),
    better: "high",
    group: "shooting",
  },
  {
    key: "FG3_PCT",
    label: "3P%",
    kind: "rate",
    denominator: totalOf("FG3A"),
    better: "high",
    group: "shooting",
  },
  {
    key: "FT_PCT",
    label: "FT%",
    kind: "rate",
    denominator: totalOf("FTA"),
    better: "high",
    group: "shooting",
  },
  {
    key: "AST_TOV",
    label: "AST/TOV",
    kind: "rate",
    digits: 2,
    denominator: totalOf("TOV"),
    better: "high",
    group: "shooting",
  },
];

/**
 * Which players lead a comparison row. Only qualified values compete — a four-attempt
 * 75 % from three is not a measurement, so it cannot be "best" — and a row needs at least
 * two comparable values before anyone is said to lead it.
 */
export function rowLeaders(placed: PlacedValue[], better: "high" | "low"): Set<number> {
  const candidates = placed
    .map((p, index) => ({ index, value: p.qualified ? p.value : null }))
    .filter((c): c is { index: number; value: number } => c.value !== null);
  if (candidates.length < 2) return new Set();
  const target =
    better === "high"
      ? Math.max(...candidates.map((c) => c.value))
      : Math.min(...candidates.map((c) => c.value));
  return new Set(candidates.filter((c) => c.value === target).map((c) => c.index));
}

const NAME_SUFFIXES = new Set(["jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"]);

/** "Gilgeous-Alexander", "McCullar Jr." — enough to key a marker to a player. */
export function shortName(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length <= 1) return name;
  const last = parts[parts.length - 1];
  if (NAME_SUFFIXES.has(last.toLowerCase()) && parts.length >= 3) {
    return `${parts[parts.length - 2]} ${last}`;
  }
  return last;
}
