"use client";

/**
 * The scouting library — the Player Explorer's default view, one card per player.
 *
 * A card answers "who is this, and what kind of season is it" before any click: the
 * portrait (a silhouette until photos are imported), team, position and games played,
 * then three indicators — points, rebounds and assists on the explorer's current scale —
 * each set against the qualified league with the same percentile micro-bar the table
 * uses (./stats.ts). A player under the sample line keeps his numbers and reads
 * "n/a · sample" where the bar would be: never zero, never the median.
 *
 * When the list is ordered by a stat the card does not show, that stat is added as a
 * last line, so the order of the cards is always explained by something on them.
 *
 * The whole card opens the player's file through a stretched link on his name; the
 * comparison pick is its own checkbox, layered above that link, so neither control
 * swallows the other and each keeps its own accessible name.
 */

import clsx from "clsx";
import Link from "next/link";
import { COUNTING_COLUMNS, QUALIFY_MIN, formatStat } from "@/lib/playerStats";
import type { Mode, StatColumn } from "@/lib/playerStats";
import { teamVars } from "@/lib/teamIdentity";
import type { SeasonTotalsPlayer } from "@/lib/types";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { SlotMarker } from "@/components/players/compare";
import { columnLabel } from "@/components/players/directory";
import {
  percentileText,
  placeValue,
  sampleUnit,
  type Populations,
} from "@/components/players/stats";

const INDICATOR_KEYS = ["PTS", "REB", "AST"];

/** Points, rebounds and assists — the line every reader already knows how to read. */
export const LIBRARY_INDICATORS: StatColumn[] = COUNTING_COLUMNS.filter((column) =>
  INDICATOR_KEYS.includes(column.key),
);

/** As many columns as fit at 300px or wider: 1 on a phone, 2 on a tablet, 3 at 1280–1440, 4 wide. */
export const LIBRARY_GRID = "grid gap-3 sm:grid-cols-[repeat(auto-fill,minmax(18.75rem,1fr))]";

/* -------------------------------------------------------------------- card */

export function LibraryCard({
  player,
  mode,
  sortColumn,
  populations,
  slot,
  selectionFull,
  onToggle,
}: {
  player: SeasonTotalsPlayer;
  mode: Mode;
  /** The column the list is ordered by — lit on the card, or added as a last line. */
  sortColumn: StatColumn;
  populations: Populations;
  /** Comparison slot index when picked, else null. */
  slot: number | null;
  selectionFull: boolean;
  onToggle: () => void;
}) {
  const selected = slot !== null;
  const sortShown = INDICATOR_KEYS.includes(sortColumn.key);

  return (
    <li
      className={clsx(
        "panel team-wash hover-frame group flex min-w-0 flex-col p-4",
        selected &&
          "border-signal/55 bg-[color-mix(in_srgb,var(--signal)_7%,var(--arena))] hover:border-signal/70",
      )}
      // The team's colour, quietly, around the headshot and name — identity, not data. The team is
      // the player's team in this dataset/season (`team_abbr`), not the selected organisation.
      style={
        player.team_abbr
          ? ({ ...teamVars(player.team_abbr), "--wash-a": "26%", "--wash-w": "340px", "--wash-h": "150px" } as React.CSSProperties)
          : undefined
      }
    >
      <div className="mb-4 flex items-start gap-3.5">
        <PlayerPhoto nbaPlayerId={player.nba_player_id} name={player.name} size={56} square />
        <div className="flex min-w-0 flex-1 flex-col">
          <h3 className="mt-2 flex min-w-0 items-baseline gap-1.5 font-serif text-[1.36rem] leading-[1.08] text-foreground">
            {selected && <SlotMarker index={slot} size={10} className="shrink-0 -translate-y-px" />}
            {/* The stretched link: its ::after covers the card, so the whole card opens
                the file. Long names wrap (at a hyphen first) rather than being cut. */}
            <Link
              href={`/players/${player.player_id}`}
              className="min-w-0 [overflow-wrap:break-word] after:absolute after:inset-0 after:rounded-[12px] focus-visible:outline-none focus-visible:after:outline focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-signal"
            >
              {player.name}
            </Link>
          </h3>
          <p className="mt-1.5 text-[12.5px] leading-snug text-muted">
            {player.position ?? "No position listed"}
            <span aria-hidden className="px-1.5 text-faint">
              ·
            </span>
            <span className="numeral font-normal tracking-normal text-foreground/90">{player.gp}</span>{" "}
            <span className="text-faint">GP</span>
            {/* R8.2: these totals belong to the team they were earned with, not the current one. */}
            {player.season_team_abbr && player.season_team_abbr !== player.team_abbr && (
              <span
                className="pl-1.5 text-faint"
                title={`These totals were accumulated with ${player.season_team_abbr}`}
                data-testid="totals-team"
              >
                · totals with {player.season_team_abbr}
              </span>
            )}
          </p>
          {/* Team line, drawn above the name (as on the player file) but read after it. */}
          <div className="order-first flex min-w-0 items-center justify-between gap-2">
            {player.team_abbr ? (
              <span className="inline-flex min-w-0 items-center gap-1.5 text-[12.5px]">
                <TeamLogo abbreviation={player.team_abbr} size={18} decorative />
                <span className="font-medium tracking-[0.02em] text-foreground/90">
                  {player.team_abbr}
                </span>
              </span>
            ) : (
              <span className="text-[12.5px] text-faint">Free agent</span>
            )}
            <CompareToggle
              name={player.name}
              selected={selected}
              disabled={!selected && selectionFull}
              onToggle={onToggle}
            />
          </div>
        </div>
      </div>

      {/* `mt-auto`: cards in a row share a height, so their indicator lines align even
          when one name wraps to two lines. */}
      <dl className="mt-auto grid grid-cols-3 gap-x-3 border-t border-hairline pt-3.5">
        {LIBRARY_INDICATORS.map((column) => (
          <Indicator
            key={column.key}
            player={player}
            column={column}
            mode={mode}
            populations={populations}
            sorted={column.key === sortColumn.key}
          />
        ))}
      </dl>

      {!sortShown && (
        <SortedLine player={player} column={sortColumn} mode={mode} populations={populations} />
      )}
    </li>
  );
}

/* --------------------------------------------------------------- controls */

/**
 * The comparison pick. A real checkbox (so the bench, the tray and assistive tech all see
 * the same control the table uses), drawn as a small pill and raised above the card's
 * stretched link so a click here never opens the player file.
 */
function CompareToggle({
  name,
  selected,
  disabled,
  onToggle,
}: {
  name: string;
  selected: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      title={disabled ? "Four players are already picked" : undefined}
      className={clsx(
        "relative z-10 inline-flex shrink-0 select-none items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] transition-colors",
        selected
          ? "cursor-pointer border-signal/60 bg-signal/10 text-signal"
          : disabled
            ? "cursor-not-allowed border-hairline text-faint opacity-60"
            : "cursor-pointer border-line bg-panel2 text-muted hover:border-signal/50 hover:text-foreground",
      )}
    >
      <span className="relative flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        <input
          type="checkbox"
          checked={selected}
          disabled={disabled}
          onChange={onToggle}
          aria-label={`Select ${name} for comparison`}
          className="peer absolute inset-0 m-0 h-full w-full cursor-[inherit] appearance-none rounded-[4px] border border-line bg-panel transition-colors checked:border-signal checked:bg-signal focus-visible:outline-offset-[3px]"
        />
        <svg
          aria-hidden
          viewBox="0 0 10 10"
          className="pointer-events-none relative hidden h-2.5 w-2.5 text-ink peer-checked:block"
        >
          <path
            d="M2 5.3 4.1 7.4 8.1 3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span aria-hidden>Compare</span>
    </label>
  );
}

/* ------------------------------------------------------------- indicators */

function describe(
  column: StatColumn,
  display: string,
  value: number | null,
  qualified: boolean,
  percentile: number | null,
  population: number,
): string {
  if (value === null) return "no value recorded";
  if (!qualified) {
    return `${display} — fewer than ${QUALIFY_MIN} ${sampleUnit(column)}, too small a sample for a percentile`;
  }
  if (percentile !== null) {
    return `${display} — ${percentileText(percentile)} percentile among ${population} qualified players`;
  }
  return display;
}

/** The micro-bar or, below the sample line, the reason there is none. */
function PercentileBar({
  value,
  qualified,
  percentile,
  sorted,
}: {
  value: number | null;
  qualified: boolean;
  percentile: number | null;
  sorted: boolean;
}) {
  if (value === null) {
    return <span className="text-[11px] leading-none text-faint">no value</span>;
  }
  if (!qualified) {
    return <span className="truncate text-[11px] leading-none text-unavail">n/a · sample</span>;
  }
  if (percentile === null) return null;
  return (
    <>
      <span className="relative h-[3px] min-w-0 flex-1 overflow-hidden rounded-full bg-panel3">
        <span
          className={clsx(
            "absolute inset-y-0 left-0 rounded-full",
            sorted ? "bg-signal" : "bg-foreground/45",
          )}
          style={{ width: `${Math.max(2, percentile)}%` }}
        />
      </span>
      <span
        className={clsx(
          "numeral shrink-0 text-[11px] font-medium leading-none tracking-normal",
          sorted ? "text-signal" : "text-muted",
        )}
      >
        {percentileText(percentile)}
      </span>
    </>
  );
}

function Indicator({
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
  const display = formatStat(value, column, mode);
  const population = populations[column.key]?.length ?? 0;

  return (
    <div className="min-w-0">
      <dt
        className={clsx(
          "text-[11px] font-medium leading-none tracking-[0.04em]",
          sorted ? "text-signal" : "text-faint",
        )}
      >
        {columnLabel(column, mode)}
        {sorted && <span className="sr-only"> (sorted)</span>}
      </dt>
      <dd className="mt-1.5 min-w-0">
        <span
          aria-hidden
          className={clsx(
            "numeral block text-[1.22rem] leading-none",
            value === null || !qualified ? "text-muted" : "text-foreground",
          )}
        >
          {display}
        </span>
        <span aria-hidden className="mt-2 flex h-3 items-center gap-1.5">
          <PercentileBar
            value={value}
            qualified={qualified}
            percentile={percentile}
            sorted={sorted}
          />
        </span>
        <span className="sr-only">
          {describe(column, display, value, qualified, percentile, population)}
        </span>
      </dd>
    </div>
  );
}

/** The ordering stat, when it is not one of the three the card already shows. */
function SortedLine({
  player,
  column,
  mode,
  populations,
}: {
  player: SeasonTotalsPlayer;
  column: StatColumn;
  mode: Mode;
  populations: Populations;
}) {
  const { value, qualified, percentile } = placeValue(player, column, mode, populations);
  const display = formatStat(value, column, mode);
  const population = populations[column.key]?.length ?? 0;
  const label = columnLabel(column, mode);
  return (
    <div className="mt-3 flex min-w-0 items-center gap-2.5 border-t border-hairline pt-2.5">
      <span className="shrink-0 text-[11px] leading-none text-faint">
        Sorted by <span className="font-medium tracking-[0.04em] text-signal">{label}</span>
      </span>
      <span aria-hidden className="numeral shrink-0 text-[13px] leading-none text-foreground">
        {display}
      </span>
      <span aria-hidden className="flex h-3 min-w-0 flex-1 items-center gap-1.5">
        <PercentileBar value={value} qualified={qualified} percentile={percentile} sorted />
      </span>
      <span className="sr-only">
        {describe(column, display, value, qualified, percentile, population)}
      </span>
    </div>
  );
}

/* ---------------------------------------------------------------- loading */

export function LibrarySkeleton({ cards = 9 }: { cards?: number }) {
  return (
    <div className={LIBRARY_GRID} aria-hidden>
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="panel p-4">
          <div className="flex items-start gap-3.5">
            <div className="skeleton h-14 w-14 rounded-lg" />
            <div className="flex-1 space-y-2 pt-1">
              <div className="skeleton h-5 w-3/4" />
              <div className="skeleton h-3 w-1/2" />
            </div>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3 border-t border-hairline pt-3.5">
            {Array.from({ length: 3 }).map((__, j) => (
              <div key={j} className="space-y-2">
                <div className="skeleton h-2.5 w-8" />
                <div className="skeleton h-5 w-10" />
                <div className="skeleton h-1" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
