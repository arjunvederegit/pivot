"use client";

/**
 * Small instruments shared by the decision board: the step heading, the 50-centred
 * component bar, the shared-axis wins band and the "who moves" block.
 *
 * Every component score on this page is 0–100 with 50 as neutral, so it is drawn as a
 * deviation from 50 — right of the centre line helps, left hurts — never as a bar from
 * zero, which would make a 45 look like most of a good score.
 */

import clsx from "clsx";
import { COMPONENT_EXPLAIN, COMPONENT_LABEL } from "@/lib/format";
import type { RosterPlayer } from "@/lib/types";
import { ImpactCell, ImpactInline } from "@/components/impact";
import { PlayerPhoto } from "@/components/media";
import {
  COMPONENT_KEYS,
  distributionOf,
  isAvailable,
  signed,
  winsTickStep,
  type BoardAlternative,
  type BoardPlayer,
  type ComponentKey,
} from "./model";

/** `lib/format`'s risk line predates R5: risk is availability exposure and nothing else. */
export const COMPONENT_NOTE: Record<string, string> = {
  ...COMPONENT_EXPLAIN,
  risk: "Availability exposure: the minutes-weighted change in games played between the players arriving and leaving. Higher means less exposure.",
};

export type PlayerLookup = (
  tradeId: string,
  player: BoardPlayer,
) => { nbaPlayerId: number | null; row: RosterPlayer | null };

/* ------------------------------------------------------------- headings */

/**
 * A step of the board: quiet caps label, serif heading one step below the page title, an
 * optional note on the right. The steps are a real sequence, so the label says where in it
 * the reader is.
 */
export function StepHeading({
  step,
  title,
  aside,
  id,
}: {
  step: string;
  title: string;
  aside?: React.ReactNode;
  id?: string;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-8 gap-y-2">
      <div className="min-w-0">
        <div className="eyebrow">{step}</div>
        <h2 id={id} className="title-lg mt-2 text-balance text-foreground">
          {title}
        </h2>
      </div>
      {aside && <div className="max-w-md text-[13px] leading-snug text-muted lg:text-right">{aside}</div>}
    </div>
  );
}

/** A small round marker before a label — the editorial replacement for square swatches. */
export function Dot({ className, tone = "signal" }: { className?: string; tone?: "signal" | "faint" }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "inline-block h-1.5 w-1.5 shrink-0 rounded-full",
        tone === "signal" ? "bg-signal" : "bg-faint",
        className,
      )}
    />
  );
}

/**
 * A rank in a small ring, for rows and columns. The serif numeral is kept for the large
 * challenger ranks; at row size its "1" reads as an "l", so small ranks are set in the sans.
 * Decorative: every use sits beside text (or an sr-only line) that names the rank.
 */
export function RankMark({ rank, className }: { rank: number | null; className?: string }) {
  const lead = rank === 1;
  return (
    <span
      aria-hidden
      className={clsx(
        "numeral inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11.5px] leading-none",
        lead ? "border-signal/50 bg-signal/10 text-signal" : "border-line text-muted",
        className,
      )}
    >
      {rank ?? "—"}
    </span>
  );
}

/* ------------------------------------------------------- component bars */

/** A 0–100 score drawn from the neutral 50 line. Null renders a dashed, empty track. */
export function DeviationBar({
  value,
  label,
  className,
  tall = false,
}: {
  value: number | null | undefined;
  label: string;
  className?: string;
  tall?: boolean;
}) {
  if (!isAvailable(value)) {
    return (
      <div
        className={clsx(
          "w-full rounded-full border border-dashed border-line",
          tall ? "h-2" : "h-1.5",
          className,
        )}
        role="img"
        aria-label={`${label}: not scored, data unavailable`}
      />
    );
  }
  const v = Math.max(0, Math.min(100, value));
  const left = Math.min(v, 50);
  const width = Math.abs(v - 50);
  return (
    <div
      className={clsx("relative w-full rounded-full bg-panel3", tall ? "h-2" : "h-1.5", className)}
      role="img"
      aria-label={`${label}: ${v.toFixed(0)} of 100, where 50 is neutral`}
    >
      <span aria-hidden className="absolute -inset-y-[3px] left-1/2 w-px bg-faint/80" />
      <span
        aria-hidden
        className="absolute inset-y-0 rounded-full"
        style={{
          left: `${left}%`,
          width: `${Math.max(width, 0.8)}%`,
          background: v >= 50 ? "var(--signal)" : "var(--illegal)",
        }}
      />
    </div>
  );
}

/** Six components, each with its value and 50-centred bar. */
export function ComponentProfile({
  components,
  className,
}: {
  components: Record<string, number | null>;
  className?: string;
}) {
  return (
    <ul className={clsx("grid gap-x-6 gap-y-3.5 sm:grid-cols-2", className)}>
      {COMPONENT_KEYS.map((key) => {
        const value = components[key];
        const available = isAvailable(value);
        return (
          <li key={key} className="min-w-0" title={COMPONENT_NOTE[key]}>
            <div className="flex items-baseline justify-between gap-2">
              {/* wraps rather than truncating: "Flexibility & future value" lost its last word at 1440 */}
              <span className="min-w-0 text-[12.5px] leading-snug text-muted">{COMPONENT_LABEL[key]}</span>
              <span
                className={clsx(
                  "numeral shrink-0 text-[13.5px] leading-none",
                  available ? "text-foreground" : "text-unavail",
                )}
              >
                {available ? value.toFixed(0) : "n/a"}
              </span>
            </div>
            <DeviationBar value={value} label={COMPONENT_LABEL[key]} className="mt-2" />
          </li>
        );
      })}
    </ul>
  );
}

/** A single labelled component read-out with an honest n/a state and its definition. */
export function ComponentReadout({
  componentKey,
  value,
}: {
  componentKey: ComponentKey;
  value: number | null | undefined;
}) {
  const available = isAvailable(value);
  return (
    <div className="min-w-0">
      <div className="flex items-end justify-between gap-3">
        <span className="min-w-0 text-[13px] font-medium leading-snug text-foreground">
          {COMPONENT_LABEL[componentKey]}
        </span>
        {available ? (
          <span className="flex shrink-0 items-baseline gap-1">
            <span className="stat-serif text-[1.85rem] leading-none text-foreground">{value.toFixed(0)}</span>
            <span className="text-[11px] text-faint">/100</span>
          </span>
        ) : (
          // An absent score is not a headline figure: small, quiet, and named for what it is.
          <span className="shrink-0 text-[12.5px] text-unavail">n/a · not scored</span>
        )}
      </div>
      <DeviationBar value={value} label={COMPONENT_LABEL[componentKey]} tall className="mt-2.5" />
      <div className="mt-1.5 flex justify-between text-[10.5px] text-faint" aria-hidden>
        <span>hurts</span>
        <span>50 neutral</span>
        <span>helps</span>
      </div>
      <p className="mt-2 text-[12px] leading-snug text-muted">{COMPONENT_NOTE[componentKey]}</p>
    </div>
  );
}

/* ------------------------------------------------------------ wins band */

/**
 * Tick values for a symmetric ±axis, built outward from 0 so the no-change line is always a
 * tick whatever the axis (sharedWinsAxis already rounds the axis to a multiple of the step).
 */
export function axisTicks(axis: number): number[] {
  const step = winsTickStep(axis);
  const ticks: number[] = [0];
  for (let v = step; v <= axis + 1e-9; v += step) ticks.push(-v, v);
  return ticks.sort((a, b) => a - b);
}

/**
 * One deal's projected-wins band on the board's SHARED axis, so bands can be compared down
 * a column. Band = 10th–90th percentile, tick = median, the brighter line = no change.
 */
export function WinsBand({
  alt,
  axis,
  lead = false,
  className,
}: {
  alt: BoardAlternative;
  axis: number;
  lead?: boolean;
  className?: string;
}) {
  const d = distributionOf(alt.uncertainty);
  const toPct = (x: number) => 50 + (Math.max(-axis, Math.min(axis, x)) / axis) * 50;
  const ticks = axisTicks(axis);
  if (!d) {
    return (
      <div className={clsx("h-6 rounded-md border border-dashed border-line", className)} aria-hidden />
    );
  }
  const lo = Math.min(d.p10, d.p90);
  const hi = Math.max(d.p10, d.p90);
  return (
    <div className={clsx("relative h-6 rounded-md bg-panel3/55", className)} aria-hidden>
      {ticks.map((t) => (
        <span
          key={t}
          className={clsx("absolute inset-y-0 w-px", t === 0 ? "bg-muted/70" : "bg-hairline")}
          style={{ left: `${toPct(t)}%` }}
        />
      ))}
      <span
        className="absolute inset-y-[6px] rounded-full"
        style={{
          left: `${toPct(lo)}%`,
          width: `${Math.max(0.8, toPct(hi) - toPct(lo))}%`,
          background: lead
            ? "color-mix(in srgb, var(--signal) 45%, transparent)"
            : "color-mix(in srgb, var(--series-b) 32%, transparent)",
        }}
      />
      <span
        className={clsx("absolute inset-y-[2px] w-[2px] rounded-full", lead ? "bg-signal" : "bg-seriesb")}
        style={{ left: `calc(${toPct(d.median)}% - 1px)` }}
      />
    </div>
  );
}

/** Tick labels for a shared wins axis, aligned with `WinsBand`. */
export function WinsAxisLabels({ axis, className }: { axis: number; className?: string }) {
  const ticks = axisTicks(axis);
  const toPct = (x: number) => 50 + (x / axis) * 50;
  return (
    <div className={clsx("numeral relative h-4 text-[10.5px] font-normal text-faint", className)} aria-hidden>
      {ticks.map((t, i) => (
        <span
          key={t}
          className={clsx(
            "absolute whitespace-nowrap",
            i === 0 ? "" : i === ticks.length - 1 ? "-translate-x-full" : "-translate-x-1/2",
            t === 0 && "text-muted",
          )}
          style={{ left: `${toPct(t)}%` }}
        >
          {t === 0 ? "0" : signed(t).replace(".0", "")}
        </span>
      ))}
    </div>
  );
}

/** "+1.8 [−0.4, +3.9]" — never a bare point estimate. */
export function WinsRange({ alt }: { alt: BoardAlternative }) {
  const d = distributionOf(alt.uncertainty);
  if (!d) return <span className="text-unavail">n/a</span>;
  return (
    <span className="whitespace-nowrap">
      <span className="data text-foreground">{signed(d.median)}</span>{" "}
      <span className="data text-[11.5px] text-muted">
        [{signed(d.p10)}, {signed(d.p90)}]
      </span>
    </span>
  );
}

/** Why there is no band: the served reason when there is one. */
export function noDistributionReason(alt: BoardAlternative): string {
  if (alt.decision_status === "suppressed_illegal") return "not projected — the deal cannot happen";
  return alt.uncertainty?.unavailable ?? "no outcome distribution was returned";
}

/* ----------------------------------------------------------- who moves */

/**
 * Headshot size for every player row on the board. `PlayerPhoto` draws its no-photo initials
 * at 0.34× its size, so 30px is the smallest box whose initials stay at the 10px text floor.
 */
export const AVATAR_SIZE = 30;

/**
 * Who moves, each placed in the league — the same reading the Trade Evaluator gives.
 * `compact` swaps the full impact cell for the inline percentile.
 */
export function MovesBlock({
  alt,
  lookup,
  compact = false,
  className,
}: {
  alt: BoardAlternative;
  lookup: PlayerLookup;
  compact?: boolean;
  className?: string;
}) {
  const column = (label: string, players: BoardPlayer[]) => (
    <div className="min-w-0">
      <div className="flex items-baseline gap-2">
        <span className="eyebrow text-foreground">{label}</span>
        <span className="numeral text-[11.5px] font-normal text-faint">{players.length}</span>
      </div>
      <ul className="mt-2.5 space-y-1.5">
        {players.length === 0 && (
          <li className="rounded-lg border border-dashed border-hairline px-3 py-2.5 text-[12px] text-faint">
            No players
          </li>
        )}
        {players.map((player) => {
          const { nbaPlayerId, row } = lookup(alt.trade_id, player);
          return (
            <li
              key={`${player.player_id ?? ""}-${player.name}`}
              className={clsx(
                "flex items-center gap-3 rounded-lg border border-hairline bg-panel2/70",
                compact ? "px-2.5 py-1.5" : "px-3 py-2.5",
              )}
            >
              <PlayerPhoto nbaPlayerId={nbaPlayerId} name={player.name} size={AVATAR_SIZE} />
              <span className="min-w-0 flex-1">
                {/* two lines before cutting: at 390px the impact cell leaves ~110px for the name */}
                <span className="line-clamp-2 block break-words text-[13.5px] font-medium leading-tight text-foreground">
                  {player.name}
                </span>
                {!compact && (
                  <span className="mt-0.5 block truncate text-[11.5px] text-faint">
                    {row?.position ?? "—"}
                    {row?.archetype ? ` · ${row.archetype}` : ""}
                  </span>
                )}
              </span>
              {compact ? (
                <ImpactInline tei={player.tei} className="shrink-0" />
              ) : (
                <ImpactCell
                  tei={player.tei}
                  band={row ? { low: row.tei_low, high: row.tei_high } : null}
                  name={player.name}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );

  // Side by side only when the block itself has room for two rows of photo, name and impact
  // cell — a viewport breakpoint squeezed names to one syllable a line inside two-up cards.
  return (
    <div className={clsx("@container", className)}>
      <div className="grid gap-x-5 gap-y-4 @min-[40rem]:grid-cols-2">
        {column("Receives", alt.incoming)}
        {column("Sends", alt.outgoing)}
      </div>
    </div>
  );
}

/** A deal's card inside one of the tabbed views. */
export function DealSliceCard({
  alt,
  rank,
  children,
  aside,
}: {
  alt: BoardAlternative;
  rank: number;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <article className="sheet min-w-0 p-4 sm:p-5">
      <header className="flex items-center gap-3">
        <RankMark rank={rank} />
        <span className="sr-only">Rank {rank}.</span>
        <h3 className="title-md min-w-0 flex-1 break-words text-foreground">{alt.name}</h3>
        {aside}
      </header>
      <div className="mt-4">{children}</div>
    </article>
  );
}
