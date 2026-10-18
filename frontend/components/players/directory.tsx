"use client";

/**
 * The Player Explorer directory — one row per imported player.
 *
 * Every stat cell carries its own league-percentile micro-bar, so a row reads as a shape
 * (tall where the player is strong, short where he is not) instead of a run of
 * indistinguishable numbers. The bars are computed over each column's qualified
 * population (./stats.ts); a player under the sample line keeps his value and is marked
 * "n/a · sample" in place of a bar.
 *
 * From `lg` the row is one line on a shared grid with a sticky header whose labels are the
 * sort controls. Below `lg` the same DOM folds into a card: identity on top, the stats in a
 * labelled grid. `lg:contents` lets one tree serve both, so nothing is rendered twice.
 */

import clsx from "clsx";
import Link from "next/link";
import { ALL_COLUMNS, QUALIFY_MIN, formatStat } from "@/lib/playerStats";
import type { Mode, StatColumn } from "@/lib/playerStats";
import { teamIdentity } from "@/lib/teamIdentity";
import type { SeasonTotalsPlayer } from "@/lib/types";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { SlotMarker } from "@/components/players/compare";
import {
  percentileText,
  placeValue,
  qualifierText,
  type Populations,
} from "@/components/players/stats";

/** check · rank · player · GP · nine stats */
export const DIRECTORY_GRID =
  "lg:grid lg:grid-cols-[1rem_1.75rem_minmax(11rem,2.3fr)_2.25rem_repeat(9,minmax(0,1fr))] lg:items-center lg:gap-x-2.5 xl:gap-x-3.5";

export function columnLabel(column: StatColumn, mode: Mode): string {
  return `${column.label}${column.kind === "counting" && mode === "per_game" ? "/g" : ""}`;
}

/* ------------------------------------------------------------------ header */

/** Column headings for the one-line layout; each is also the sort control. */
export function DirectoryHeader({
  sortKey,
  sortDir,
  onSort,
  mode,
  populations,
}: {
  sortKey: string;
  sortDir: "desc" | "asc";
  onSort: (key: string) => void;
  mode: Mode;
  populations: Populations;
}) {
  return (
    // Sticky so the column meaning survives a 573-row scroll; the nav is --nav-h tall.
    <div
      className={clsx(
        "sticky top-[var(--nav-h)] z-10 mb-1.5 hidden rounded-lg border border-hairline bg-panel2 px-3 py-2",
        DIRECTORY_GRID,
      )}
    >
      <span aria-hidden />
      <span className="eyebrow text-right text-[10px]">#</span>
      <span className="eyebrow text-[10px]">Player</span>
      <span className="eyebrow text-right text-[10px]" title="Games played">
        GP
      </span>
      {ALL_COLUMNS.map((column) => {
        const active = column.key === sortKey;
        const size = populations[column.key]?.length ?? 0;
        return (
          <button
            key={column.key}
            type="button"
            onClick={() => onSort(column.key)}
            aria-label={`Sort by ${column.label}${
              column.kind === "counting"
                ? mode === "per_game"
                  ? " per game"
                  : " season total"
                : ""
            }`}
            title={`${columnLabel(column, mode)} — bar is the league percentile among the ${size} players with ${qualifierText(column)}`}
            className={clsx(
              "group flex min-w-0 items-center justify-end gap-1 whitespace-nowrap text-[10.5px] font-medium tracking-[0.04em] transition-colors",
              active ? "text-signal" : "text-muted hover:text-foreground",
            )}
          >
            <span className={clsx(active && "border-b border-signal pb-px")}>
              {columnLabel(column, mode)}
            </span>
            <span
              aria-hidden
              className={clsx("w-2 text-center", !active && "opacity-0 group-hover:opacity-50")}
            >
              {active ? (sortDir === "desc" ? "↓" : "↑") : "↓"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* --------------------------------------------------------------------- row */

export function DirectoryRow({
  player,
  rank,
  mode,
  sortKey,
  populations,
  slot,
  selectionFull,
  onToggle,
}: {
  player: SeasonTotalsPlayer;
  rank: number;
  mode: Mode;
  sortKey: string;
  populations: Populations;
  /** Comparison slot index when picked, else null. */
  slot: number | null;
  selectionFull: boolean;
  onToggle: () => void;
}) {
  const selected = slot !== null;
  const identity = teamIdentity(player.team_abbr);

  return (
    <li
      className={clsx(
        "rounded-md border px-3 py-2.5 transition-colors lg:py-2",
        DIRECTORY_GRID,
        selected
          ? "border-signal/60 bg-signal/[0.07]"
          : "border-hairline bg-panel hover:border-line hover:bg-panel2/60",
      )}
    >
      {/* identity line — a flex row on a card, four grid cells from lg */}
      <div className="flex items-center gap-2.5 lg:contents">
        <input
          type="checkbox"
          checked={selected}
          disabled={!selected && selectionFull}
          onChange={onToggle}
          aria-label={`Select ${player.name} for comparison`}
          title={!selected && selectionFull ? "Four players are already picked" : undefined}
          className="h-4 w-4 shrink-0 cursor-pointer accent-[var(--signal)] disabled:cursor-not-allowed disabled:opacity-30"
        />
        <span className="numeral w-7 shrink-0 text-right text-[12px] leading-none text-faint lg:w-auto">
          {rank}
        </span>

        <span className="flex min-w-0 flex-1 items-center gap-2.5">
          <PlayerPhoto nbaPlayerId={player.nba_player_id} name={player.name} size={32} square />
          <span className="min-w-0">
            <span className="flex min-w-0 items-center gap-1.5">
              {selected && <SlotMarker index={slot} size={10} />}
              {/* A long name wraps to a second line rather than being cut: at 390 and
                  around 1024 the column is narrower than "Shai Gilgeous-Alexander". */}
              <Link
                href={`/players/${player.player_id}`}
                className="text-[14px] font-semibold leading-tight text-foreground [overflow-wrap:anywhere] hover:underline hover:decoration-line hover:underline-offset-2"
              >
                {player.name}
              </Link>
            </span>
            <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[10.5px] leading-none text-muted">
              {player.team_abbr ? (
                <span className="inline-flex shrink-0 items-center gap-1">
                  <TeamLogo abbreviation={player.team_abbr} size={12} decorative />
                  <span style={{ color: identity.bright }}>{player.team_abbr}</span>
                </span>
              ) : (
                <span className="shrink-0 text-faint">free agent</span>
              )}
              <span aria-hidden className="text-faint">
                ·
              </span>
              <span className="truncate">{player.position ?? "—"}</span>
            </span>
          </span>
        </span>

        <span className="shrink-0 text-right text-[12px] leading-none text-muted">
          {player.gp}
          <span className="text-faint lg:hidden"> GP</span>
        </span>
      </div>

      {/* stat line — a labelled grid on a card, nine grid cells from lg */}
      <div className="mt-2.5 grid grid-cols-3 gap-x-4 gap-y-2.5 border-t border-hairline pt-2.5 sm:grid-cols-5 lg:contents">
        {ALL_COLUMNS.map((column) => (
          <StatCell
            key={column.key}
            player={player}
            column={column}
            mode={mode}
            populations={populations}
            sorted={column.key === sortKey}
          />
        ))}
      </div>
    </li>
  );
}

function StatCell({
  player,
  column,
  mode,
  populations,
  sorted,
}: {
  player: SeasonTotalsPlayer;
  column: StatColumn;
  mode: Mode;
  populations: Populations;
  sorted: boolean;
}) {
  const { value, qualified, percentile } = placeValue(player, column, mode, populations);
  const label = columnLabel(column, mode);
  const display = formatStat(value, column, mode);
  const population = populations[column.key]?.length ?? 0;

  const description =
    value === null
      ? `${label}: no value recorded`
      : !qualified
        ? `${label} ${display} — fewer than ${QUALIFY_MIN} ${column.kind === "rate" ? "attempts" : "games"}, too small a sample for a percentile`
        : percentile !== null
          ? `${label} ${display} — ${percentileText(percentile)} percentile among ${population} qualified players`
          : `${label} ${display}`;

  return (
    <div
      className={clsx(
        "min-w-0",
        sorted && "lg:-mx-1.5 lg:rounded-md lg:bg-signal/[0.07] lg:px-1.5 lg:py-1",
      )}
      title={description}
    >
      <div aria-hidden className="flex items-baseline justify-between gap-1.5 lg:justify-end">
        <span
          className={clsx(
            "text-[10.5px] tracking-[0.04em] lg:hidden",
            sorted ? "text-signal" : "text-faint",
          )}
        >
          {label}
        </span>
        <span
          className={clsx(
            "data text-[13px] leading-none",
            sorted ? "font-semibold text-signal" : qualified ? "text-foreground" : "text-muted",
          )}
        >
          {display}
        </span>
      </div>

      <div aria-hidden className="mt-1.5 flex h-2.5 items-center justify-end gap-1.5">
        {value === null ? (
          <span className="text-[10px] leading-none text-faint">—</span>
        ) : !qualified ? (
          <span className="truncate text-[10px] leading-none text-unavail">
            n/a<span className="lg:hidden xl:inline"> · sample</span>
          </span>
        ) : percentile !== null ? (
          <>
            <span className="relative h-[3px] min-w-0 flex-1 overflow-hidden rounded-[1px] bg-panel3">
              <span
                className={clsx(
                  "absolute inset-y-0 left-0",
                  sorted ? "bg-signal" : "bg-foreground/45",
                )}
                style={{ width: `${Math.max(2, percentile)}%` }}
              />
            </span>
            {sorted && (
              <span className="shrink-0 text-[10px] leading-none text-signal">
                {percentileText(percentile)}
              </span>
            )}
          </>
        ) : null}
      </div>
      <span className="sr-only">{description}</span>
    </div>
  );
}
