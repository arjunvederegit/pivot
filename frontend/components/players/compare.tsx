"use client";

/**
 * The comparison bench — the explorer's centre of gravity.
 *
 * Four slots, each with its own marker shape, sit at the top of the page — folded to a
 * single line while nobody is picked. Once two or more are filled the bench opens into
 * small multiples: one row per stat, one marker per
 * player on that stat's league-percentile axis. Identity is carried by SHAPE (and the
 * printed value beside it), and the row leader is named in TEXT ("best") as well as lit
 * in volt — so nothing here depends on telling colours apart.
 *
 * The axes are league percentiles over the qualified population (see ./stats.ts), so a
 * 33.5-point scorer and a 47.6 % shooter can be read on the same 0–100 line. Values under
 * the sample line are printed but never plotted.
 */

import clsx from "clsx";
import Link from "next/link";
import { useMemo } from "react";
import { QUALIFY_MIN, formatStat } from "@/lib/playerStats";
import { teamIdentity } from "@/lib/teamIdentity";
import type { SeasonTotalsPlayer } from "@/lib/types";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { Button, Explainer } from "@/components/ui";
import {
  COMPARE_COLUMNS,
  axisPercentile,
  leaguePopulations,
  percentileText,
  placeValue,
  rowLeaders,
  shortName,
  type CompareColumn,
  type PlacedValue,
} from "@/components/players/stats";

/* ---------------------------------------------------------------- markers */

const SLOT_SHAPES = ["circle", "square", "triangle", "diamond"] as const;
export const SLOT_NAMES = ["circle", "square", "triangle", "diamond"] as const;

/** The per-slot marker. Shape is the identity; fill only adds emphasis. */
export function SlotMarker({
  index,
  size = 11,
  variant = "solid",
  className,
}: {
  index: number;
  size?: number;
  /** solid = plotted value · best = row leader · outline = empty slot */
  variant?: "solid" | "best" | "outline";
  className?: string;
}) {
  const shape = SLOT_SHAPES[index % SLOT_SHAPES.length];
  const fill =
    variant === "outline" ? "none" : variant === "best" ? "var(--signal)" : "var(--chalk)";
  const stroke = variant === "outline" ? "var(--chalk-faint)" : "var(--arena)";
  const common = {
    fill,
    stroke,
    strokeWidth: variant === "outline" ? 1.2 : 1.4,
  };
  return (
    <svg
      aria-hidden
      width={size}
      height={size}
      viewBox="0 0 12 12"
      className={clsx("shrink-0 overflow-visible", className)}
    >
      {shape === "circle" && <circle cx="6" cy="6" r="5" {...common} />}
      {shape === "square" && <rect x="1.2" y="1.2" width="9.6" height="9.6" {...common} />}
      {shape === "triangle" && <path d="M6 0.6 11.6 11H0.4Z" {...common} strokeLinejoin="round" />}
      {shape === "diamond" && (
        <path d="M6 0.2 11.8 6 6 11.8 0.2 6Z" {...common} strokeLinejoin="round" />
      )}
    </svg>
  );
}

/* ------------------------------------------------------------------ bench */

export function ComparisonBench({
  players,
  league,
  open,
  max,
  min,
  onOpen,
  onClose,
  onRemove,
  onClear,
  benchRef,
}: {
  /** The picked players, in slot order. */
  players: SeasonTotalsPlayer[];
  /** Every imported player — the percentile population. */
  league: SeasonTotalsPlayer[];
  open: boolean;
  max: number;
  min: number;
  onOpen: () => void;
  onClose: () => void;
  onRemove: (playerId: string) => void;
  onClear: () => void;
  benchRef: (node: HTMLElement | null) => void;
}) {
  const showing = open && players.length >= min;

  const populations = useMemo(
    () => leaguePopulations(league, COMPARE_COLUMNS, "per_game"),
    [league],
  );

  const rows = useMemo(
    () =>
      COMPARE_COLUMNS.map((column) => {
        const placed = players.map((p) => placeValue(p, column, "per_game", populations));
        return { column, placed, leaders: rowLeaders(placed, column.better) };
      }),
    [players, populations],
  );

  const leads = players.map((_, i) => rows.filter((r) => r.leaders.has(i)).length);

  return (
    <section
      ref={benchRef}
      aria-labelledby="compare-title"
      className="panel"
      style={{ "--edge": "var(--signal)" } as React.CSSProperties}
    >
      <header
        className={clsx(
          "flex flex-wrap items-center justify-between gap-x-5 gap-y-3 px-5 py-4",
          players.length > 0 && "border-b border-hairline",
        )}
      >
        <div className="min-w-0">
          {/* Focus target when the comparison opens (the button pressed to open it goes
              away), so tabIndex -1: reachable by script, not a tab stop. */}
          <h2 id="compare-title" tabIndex={-1} className="title-md text-foreground">
            {showing ? `Comparing ${players.length} players` : "Compare players"}
          </h2>
          <p className="mt-1 max-w-2xl text-[12.5px] leading-relaxed text-muted">
            {showing
              ? "Per-game line (season totals ÷ GP) and shooting rates, each placed on its own league-percentile axis."
              : `Tick Compare on up to ${max} players below. The comparison always uses the per-game line, so the scales stay honest.`}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2.5">
          {/* Empty bench: the four slot shapes stand in for the slots themselves, so the
              bench costs one line until someone is picked. */}
          {players.length === 0 && (
            <span aria-hidden className="flex items-center gap-2">
              {Array.from({ length: max }).map((_, index) => (
                <SlotMarker key={index} index={index} size={12} variant="outline" />
              ))}
            </span>
          )}
          <span className="text-[12px] text-muted" aria-live="polite">
            <span className="numeral text-foreground">{players.length}</span> of {max} picked
          </span>
          {players.length > 0 && (
            <Button size="sm" variant="ghost" onClick={onClear}>
              Clear
            </Button>
          )}
          {showing ? (
            <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close comparison">
              Close
            </Button>
          ) : players.length === 0 ? null : (
            <Button size="sm" variant="signal" disabled={players.length < min} onClick={onOpen}>
              Compare {players.length}
            </Button>
          )}
        </div>
      </header>

      {players.length > 0 && (
        <div className="p-4 sm:p-5">
          <ol className="grid grid-cols-2 gap-2 lg:grid-cols-4" aria-label="Comparison slots">
            {Array.from({ length: max }).map((_, index) => {
              const player = players[index];
              return player ? (
                <SlotCard
                  key={player.player_id}
                  index={index}
                  player={player}
                  leads={showing ? leads[index] : null}
                  rowCount={rows.length}
                  onRemove={() => onRemove(player.player_id)}
                />
              ) : (
                <EmptySlot key={`empty-${index}`} index={index} hint={index < min} />
              );
            })}
          </ol>

          {showing && <SmallMultiples players={players} rows={rows} populations={populations} />}
        </div>
      )}
    </section>
  );
}

function SlotCard({
  index,
  player,
  leads,
  rowCount,
  onRemove,
}: {
  index: number;
  player: SeasonTotalsPlayer;
  leads: number | null;
  rowCount: number;
  onRemove: () => void;
}) {
  const identity = teamIdentity(player.team_abbr);
  return (
    <li className="relative min-w-0 overflow-hidden rounded-lg border border-line bg-panel2 py-2.5 pl-3 pr-1.5">
      <span
        aria-hidden
        className="absolute inset-x-0 top-0 h-[2px]"
        style={{ background: identity.bright, opacity: 0.85 }}
      />
      <div className="flex items-start gap-2">
        <SlotMarker index={index} size={12} className="mt-1" />
        <PlayerPhoto
          nbaPlayerId={player.nba_player_id}
          name={player.name}
          size={30}
          square
          className="hidden sm:block"
        />
        <div className="min-w-0 flex-1">
          {/* Wraps rather than truncates: two slots share a 390px row, and "Anthony Edw…"
              is not a name a comparison should make the reader guess at. */}
          <Link
            href={`/players/${player.player_id}`}
            className="block text-[13px] font-semibold leading-tight text-foreground [overflow-wrap:anywhere] hover:underline hover:underline-offset-2"
          >
            {player.name}
          </Link>
          <span className="mt-1 flex min-w-0 items-center gap-1 text-[11px] leading-none text-muted">
            {player.team_abbr && <TeamLogo abbreviation={player.team_abbr} size={12} decorative />}
            <span className="truncate">
              <span
                style={{
                  color: player.team_abbr ? identity.bright : undefined,
                }}
              >
                {player.team_abbr ?? "FA"}
              </span>
              {" · "}
              {player.position ?? "—"}
              <span className="hidden sm:inline"> · {player.gp} GP</span>
            </span>
          </span>
          <span className="mt-1.5 block text-[11px] leading-snug text-faint">
            {/* Stacked on a phone: inline, the separator was left dangling at the end of
                the first line ("64 GP ·"). */}
            <span className="block sm:hidden">{player.gp} GP</span>
            {leads !== null && (
              <span className="block whitespace-nowrap">
                leads <span className="text-foreground">{leads}</span>
                <span className="hidden sm:inline"> of {rowCount} rows</span>
                <span className="sm:hidden">/{rowCount}</span>
              </span>
            )}
          </span>
        </div>
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${player.name} from comparison`}
          className="shrink-0 rounded-md px-1.5 py-1 text-[12px] leading-none text-faint transition-colors hover:bg-panel3 hover:text-foreground"
        >
          <span aria-hidden>✕</span>
        </button>
      </div>
    </li>
  );
}

function EmptySlot({ index, hint }: { index: number; hint: boolean }) {
  return (
    <li className="flex min-h-[3.25rem] items-center gap-2.5 rounded-lg border border-dashed border-line px-3 py-2">
      <SlotMarker index={index} size={12} variant="outline" />
      <span className="min-w-0">
        <span className="eyebrow block text-faint">
          Slot {index + 1}
          <span className="sr-only"> ({SLOT_NAMES[index]} marker), empty</span>
        </span>
        <span className="mt-0.5 block truncate text-[12px] text-muted">
          {hint ? "tick Compare below" : "optional"}
        </span>
      </span>
    </li>
  );
}

/* --------------------------------------------------------- small multiples */

interface CompareRowModel {
  column: CompareColumn;
  placed: PlacedValue[];
  leaders: Set<number>;
}

const TICKS = [0, 25, 50, 75, 100];

function SmallMultiples({
  players,
  rows,
  populations,
}: {
  players: SeasonTotalsPlayer[];
  rows: CompareRowModel[];
  populations: Record<string, number[]>;
}) {
  const n = players.length;
  const groups: { id: CompareColumn["group"]; title: string; note: string }[] = [
    { id: "line", title: "Per-game line", note: "15+ games to be placed" },
    {
      id: "shooting",
      title: "Shooting & ball care",
      note: "15+ attempts (AST/TOV: turnovers)",
    },
  ];

  return (
    <div
      className="mt-4 space-y-5 border-t border-hairline pt-4"
      style={{ "--n": n } as React.CSSProperties}
    >
      {/* Below lg the values sit under each axis in a grid, so the shape key is stated
          once up front; from lg it heads the value columns of every group instead. */}
      <ul
        className="flex flex-wrap gap-x-4 gap-y-1.5 lg:hidden"
        aria-label="Marker key"
      >
        {players.map((player, i) => (
          <li key={player.player_id} className="flex min-w-0 items-center gap-1.5">
            <SlotMarker index={i} size={10} />
            <span className="text-[12px] text-foreground">{player.name}</span>
          </li>
        ))}
      </ul>

      {groups.map((group) => (
        <div key={group.id}>
          <div className={rowGrid}>
            <div className="flex items-baseline justify-between gap-2 lg:block">
              <h3 className="eyebrow text-foreground">{group.title}</h3>
              <span className="text-[10px] leading-tight text-faint lg:mt-1 lg:block">
                {group.note}
              </span>
            </div>
            <AxisScale />
            <div
              aria-hidden
              className="hidden gap-x-2 lg:grid lg:grid-cols-[repeat(var(--n),minmax(0,1fr))]"
            >
              {players.map((player, i) => (
                <span
                  key={player.player_id}
                  className="flex min-w-0 items-center gap-1.5"
                  title={player.name}
                >
                  <SlotMarker index={i} size={9} />
                  {/* Wraps at the hyphen ("Gilgeous-/Alexander") rather than cutting. */}
                  <span className="min-w-0 text-[11.5px] font-semibold leading-tight text-foreground [overflow-wrap:anywhere]">
                    {shortName(player.name)}
                  </span>
                </span>
              ))}
            </div>
          </div>
          <ul className="mt-1.5 divide-y divide-hairline">
            {rows
              .filter((r) => r.column.group === group.id)
              .map((row) => (
                <CompareRow
                  key={row.column.key}
                  row={row}
                  players={players}
                  populationSize={populations[row.column.key]?.length ?? 0}
                />
              ))}
          </ul>
        </div>
      ))}

      <Explainer summary="How the comparison is placed">
        <p>
          Each marker is the player&apos;s league percentile on that stat — the share of qualified
          players at or below his value. Qualified means {QUALIFY_MIN}+ of whatever the stat divides
          by: games for a per-game number, attempts for a shooting percentage, turnovers for
          AST/TOV. The population is the whole imported league; the directory filters never change
          it.
        </p>
        <p className="mt-1.5">
          Right is better on every axis. Turnovers are the one stat where less is better, so that
          axis is reversed: its marker and printed percentile are the share of qualified players
          who average <em>more</em> turnovers.
        </p>
        <p className="mt-1.5">
          A value under that line is printed with{" "}
          <span className="text-foreground">n/a · sample</span> and is not plotted or eligible to
          lead a row. <span className="text-foreground">Best</span> marks the leader among qualified
          values (lowest for turnovers), named in text and lit in volt. Ties share it. Printed
          percentiles are held to 1st–99th.
        </p>
      </Explainer>
    </div>
  );
}

/** Label | axis | values. The values column is sized from --n (players compared). */
const rowGrid =
  "grid gap-x-4 gap-y-1.5 lg:grid-cols-[6rem_minmax(0,1fr)_calc(var(--n)*6.75rem)] lg:items-center";

function AxisScale() {
  return (
    <div className="px-1.5">
      <div className="relative h-4 text-[10px] text-faint">
        {TICKS.map((t) => (
          <span
            key={t}
            className={clsx(
              "absolute top-0",
              t === 0 ? "" : t === 100 ? "-translate-x-full" : "-translate-x-1/2",
            )}
            style={{ left: `${t}%` }}
          >
            {t === 50 ? "50th" : t}
          </span>
        ))}
      </div>
    </div>
  );
}

function CompareRow({
  row,
  players,
  populationSize,
}: {
  row: CompareRowModel;
  players: SeasonTotalsPlayer[];
  populationSize: number;
}) {
  const { column, placed, leaders } = row;
  const n = players.length;
  const reversed = column.better === "low";
  // Position on this row's axis: right is better on every row (see axisPercentile).
  const positions = placed.map((pv) =>
    pv.percentile === null ? null : axisPercentile(pv.percentile, column.better),
  );
  return (
    <li className={clsx(rowGrid, "py-2")}>
      <div className="flex items-baseline justify-between gap-2 lg:block">
        <span className="text-[13px] font-semibold text-foreground">
          {column.label}
          {column.kind === "counting" && <span className="font-normal text-faint">/g</span>}
        </span>
        <span className="block text-right text-[10px] leading-tight text-faint lg:mt-0.5 lg:text-left">
          {reversed ? (
            <>
              lower is better
              <span className="lg:hidden"> · </span>
              <span className="lg:block">axis reversed</span>
            </>
          ) : (
            `${populationSize} qualified`
          )}
        </span>
      </div>

      {/* The axis. Lanes keep markers from covering each other; the 50th is the league
          median of the qualified population. */}
      <div className="px-1.5">
        <div
          className="relative h-11"
          aria-hidden
          title={
            reversed
              ? "Reversed axis: further right means fewer turnovers than the qualified league"
              : undefined
          }
        >
          {TICKS.map((t) => (
            <span
              key={t}
              className={clsx("absolute inset-y-0 w-px", t === 50 ? "bg-line" : "bg-hairline")}
              style={{ left: `${t}%` }}
            />
          ))}
          <span className="absolute inset-x-0 top-1/2 h-px bg-hairline" />
          {placed.map((pv, i) =>
            positions[i] === null ? null : (
              <span
                key={players[i].player_id}
                className="absolute -translate-x-1/2 -translate-y-1/2"
                style={{
                  left: `${positions[i]}%`,
                  top: `${((i + 0.5) / n) * 100}%`,
                }}
              >
                <SlotMarker
                  index={i}
                  size={leaders.has(i) ? 13 : 11}
                  variant={leaders.has(i) ? "best" : "solid"}
                />
              </span>
            ),
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-[repeat(var(--n),minmax(0,1fr))] sm:gap-x-2">
        {placed.map((pv, i) => {
          const best = leaders.has(i);
          const position = positions[i];
          return (
            <div key={players[i].player_id} className="min-w-0">
              <span className="flex items-center gap-1.5">
                <SlotMarker index={i} size={9} variant={best ? "best" : "solid"} />
                <span
                  className={clsx(
                    "data truncate text-[13px] leading-none",
                    best ? "font-semibold text-foreground" : "text-muted",
                    !pv.qualified && "text-faint",
                  )}
                >
                  <span className="sr-only">{players[i].name}: </span>
                  {formatStat(pv.value, column, "per_game")}
                </span>
              </span>
              <span className="mt-1 block truncate pl-[15px] text-[10px] leading-none">
                {pv.value === null ? (
                  <span className="text-faint">no value</span>
                ) : !pv.qualified || position === null ? (
                  <span className="text-unavail">n/a · sample</span>
                ) : (
                  <>
                    {best && (
                      <span className="font-semibold uppercase tracking-[0.06em] text-signal">
                        best ·{" "}
                      </span>
                    )}
                    <span className="text-faint">
                      {percentileText(position)}
                      {reversed && <span className="sr-only"> percentile for fewest turnovers</span>}
                    </span>
                  </>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </li>
  );
}
