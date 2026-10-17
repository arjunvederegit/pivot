"use client";

/**
 * One 0–100 league-position track, shared by every "where does this team stand" row on
 * Team Outlook (strengths & needs, skill coverage, acquisition targets).
 *
 * The track always carries the league median as a tick at 50, so a marker is read
 * against the middle of the league rather than against an empty bar. Zones and a second
 * reference tick are optional and only drawn when the caller has a served value for
 * them — nothing here invents a threshold.
 *
 * Drawn as a fine chalk line rather than a filled meter: a position is a point, and a
 * bar from 0 would read as an amount.
 */

import clsx from "clsx";

export type TrackTone = "need" | "strength" | "neutral" | "accent";

const TONE: Record<TrackTone, string> = {
  need: "var(--illegal)",
  strength: "var(--legal)",
  neutral: "var(--chalk-dim)",
  accent: "var(--signal)",
};

const clamp = (v: number) => Math.max(0, Math.min(100, v));

export function PercentileTrack({
  value,
  tone = "accent",
  needBelow,
  strengthFrom,
  reference,
  className,
}: {
  /** 0–100, or null for "not measured" (no marker is drawn). */
  value: number | null;
  tone?: TrackTone;
  /** Shade 0..needBelow as the need zone. */
  needBelow?: number | null;
  /** Shade strengthFrom..100 as the strength zone. */
  strengthFrom?: number | null;
  /** A second tick — e.g. the roster's own level a target has to beat. */
  reference?: number | null;
  className?: string;
}) {
  return (
    <span aria-hidden className={clsx("relative block h-3.5", className)}>
      {/* the line */}
      <span className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-panel3" />
      {needBelow != null && (
        <span
          className="absolute top-1/2 h-[3px] -translate-y-1/2 rounded-l-full bg-illegal/30"
          style={{ left: 0, width: `${clamp(needBelow)}%` }}
        />
      )}
      {strengthFrom != null && (
        <span
          className="absolute top-1/2 h-[3px] -translate-y-1/2 rounded-r-full bg-legal/25"
          style={{ left: `${clamp(strengthFrom)}%`, right: 0 }}
        />
      )}
      {/* league median */}
      <span className="absolute inset-y-0.5 left-1/2 w-px bg-faint/70" />
      {reference != null && (
        <span
          className="absolute inset-y-0 w-[2px] rounded-full bg-seriesb"
          style={{ left: `calc(${clamp(reference)}% - 1px)` }}
        />
      )}
      {value != null && (
        <span
          className="absolute top-1/2 h-[11px] w-[11px] -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-[var(--arena)]"
          style={{ left: `${clamp(value)}%`, background: TONE[tone] }}
        />
      )}
    </span>
  );
}

/**
 * The scale under a column of tracks: 0, the median, 100.
 *
 * A container, so the median's word is dropped when the column is too narrow for
 * "0 · 50 · median · 100" to sit apart — the bare "50" still marks the middle.
 */
export function PercentileAxis({ className, note }: { className?: string; note?: string }) {
  return (
    <div
      className={clsx("@container relative h-4 text-[10.5px] tabular-nums text-faint", className)}
      aria-hidden
    >
      <span className="absolute left-0">0</span>
      <span className="absolute left-1/2 -translate-x-1/2 whitespace-nowrap">
        50{note ? <span className="hidden @min-[9rem]:inline"> · {note}</span> : null}
      </span>
      <span className="absolute right-0">100</span>
    </div>
  );
}
