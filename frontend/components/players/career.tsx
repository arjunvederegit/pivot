"use client";

/**
 * Season by season — the career arc strip and the table under it.
 *
 * The strip is three small multiples (points, true shooting, net rating) drawn from the
 * same season rows the table prints, oldest season on the left. Nothing is smoothed,
 * projected or interpolated: a season with no value breaks the line, and a season under
 * the explorer's sample line (`QUALIFY_MIN` games) is drawn hollow so a four-game cameo
 * does not read like a real swing in form.
 */

import clsx from "clsx";
import { pct } from "@/lib/format";
import { QUALIFY_MIN } from "@/lib/playerStats";
import { Td, Th } from "@/components/ui";

export interface SeasonLine {
  season: string;
  source_retrieved_at: string | null;
  base?: Record<string, unknown>;
  advanced?: Record<string, unknown>;
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const signed = (v: number, digits = 1) => {
  const r = Number(v.toFixed(digits));
  return r > 0
    ? `+${r.toFixed(digits)}`
    : r < 0
      ? `−${Math.abs(r).toFixed(digits)}`
      : r.toFixed(digits);
};

/** The most recent retrieval across every season row — the rail reports the freshest. */
export function latestRetrieval(seasons: SeasonLine[]): string | null {
  let best: string | null = null;
  let bestTime = -Infinity;
  for (const s of seasons) {
    if (!s.source_retrieved_at) continue;
    const t = new Date(s.source_retrieved_at).getTime();
    if (Number.isFinite(t) && t > bestTime) {
      bestTime = t;
      best = s.source_retrieved_at;
    }
  }
  return best;
}

interface Metric {
  key: string;
  label: string;
  unit: string;
  get: (s: SeasonLine) => number | null;
  fmt: (v: number) => string;
  /** Change between two seasons, already in display units. */
  delta: (from: number, to: number) => string;
}

const METRICS: Metric[] = [
  {
    key: "PTS",
    label: "Points",
    unit: "per game",
    get: (s) => num(s.base?.PTS),
    fmt: (v) => v.toFixed(1),
    delta: (a, b) => signed(b - a),
  },
  {
    key: "TS",
    label: "True shooting",
    unit: "TS%",
    get: (s) => num(s.advanced?.TS_PCT),
    fmt: (v) => pct(v, 1),
    delta: (a, b) => `${signed((b - a) * 100)} pts`,
  },
  {
    key: "NET",
    label: "Net rating",
    unit: "per 100 poss.",
    get: (s) => num(s.advanced?.NET_RATING),
    fmt: (v) => signed(v),
    delta: (a, b) => signed(b - a),
  },
];

/* ------------------------------------------------------------------ strip */

export function CareerStrip({ seasons }: { seasons: SeasonLine[] }) {
  const smallSample = seasons.some((s) => (num(s.base?.GP) ?? 0) < QUALIFY_MIN);
  return (
    <div>
      <div className="grid gap-px overflow-hidden rounded-md border border-hairline bg-hairline sm:grid-cols-3">
        {METRICS.map((metric) => (
          <MetricCell key={metric.key} metric={metric} seasons={seasons} />
        ))}
      </div>
      <p className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10.5px] text-faint">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="h-[7px] w-[7px] rounded-full bg-signal" /> latest season
        </span>
        {smallSample && (
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="h-[7px] w-[7px] rounded-full border border-foreground/60"
            />
            under {QUALIFY_MIN} games — a small sample, never a career high
          </span>
        )}
        <span>oldest season left · nothing smoothed or projected</span>
      </p>
    </div>
  );
}

function MetricCell({ metric, seasons }: { metric: Metric; seasons: SeasonLine[] }) {
  const points = seasons.map((s) => ({
    season: s.season,
    value: metric.get(s),
    gp: num(s.base?.GP),
    small: (num(s.base?.GP) ?? 0) < QUALIFY_MIN,
  }));
  const present = points.filter((p): p is typeof p & { value: number } => p.value !== null);
  const latest = present.at(-1) ?? null;
  const previous = present.length >= 2 ? present[present.length - 2] : null;
  // A career high is only claimed over seasons with a real sample: a four-game cameo is
  // drawn hollow on the line, so it must not then be quoted as the player's best season.
  const qualified = present.filter((p) => !p.small);
  const high =
    qualified.length > 1
      ? qualified.reduce((best, p) => (p.value > best.value ? p : best), qualified[0])
      : null;
  const sampleNote = (p: { gp: number | null; small: boolean } | null) =>
    p?.small ? ` (${p.gp ?? "?"} GP)` : "";

  return (
    <div className="min-w-0 bg-panel px-3.5 pb-3 pt-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="eyebrow truncate">{metric.label}</span>
        <span className="shrink-0 text-[10px] text-faint">{metric.unit}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="numeral text-[1.6rem] leading-none text-foreground">
          {latest ? metric.fmt(latest.value) : "—"}
        </span>
        <span className="text-[10.5px] text-muted">
          {latest && previous ? (
            <>
              <span className="text-foreground">{metric.delta(previous.value, latest.value)}</span>{" "}
              vs {previous.season}
              {previous.small && <span className="text-unavail">{sampleNote(previous)}</span>}
            </>
          ) : latest ? (
            "one season on file"
          ) : (
            "not recorded"
          )}
        </span>
      </div>
      {latest?.small && (
        <p className="mt-1 text-[10px] text-unavail">
          {latest.season} is {latest.gp ?? "?"} GP — under {QUALIFY_MIN}, a small sample
        </p>
      )}

      <Sparkline points={points} label={metric.label} fmt={metric.fmt} />

      <div className="mt-1 flex items-baseline justify-between gap-2 whitespace-nowrap text-[10px] text-faint">
        <span>{points[0]?.season}</span>
        {points.length > 1 && <span>{points.at(-1)?.season}</span>}
      </div>
      {high && (
        <p className="mt-2 border-t border-hairline pt-2 text-[10.5px] text-muted">
          career high <span className="text-foreground">{metric.fmt(high.value)}</span>
          <span className="text-faint"> · {high.season}</span>
        </p>
      )}
    </div>
  );
}

function Sparkline({
  points,
  label,
  fmt,
}: {
  points: { season: string; value: number | null; small: boolean }[];
  label: string;
  fmt: (v: number) => string;
}) {
  const values = points.map((p) => p.value).filter((v): v is number => v !== null);
  if (values.length === 0) {
    return (
      <div className="mt-3 flex h-16 items-center justify-center rounded-md border border-dashed border-hairline text-[10.5px] text-faint">
        no values recorded
      </div>
    );
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.18 || Math.max(Math.abs(max) * 0.1, 1);
  const lo = min - pad;
  const hi = max + pad;
  const y = (v: number) => 100 - ((v - lo) / (hi - lo)) * 100;
  const n = points.length;
  const x = (i: number) => (n === 1 ? 50 : (i / (n - 1)) * 100);
  const lastIndex = points.reduce((acc, p, i) => (p.value !== null ? i : acc), -1);

  // Break the line where a season has no value rather than bridging the gap.
  const segments: string[] = [];
  let current: string[] = [];
  points.forEach((p, i) => {
    if (p.value === null) {
      if (current.length > 1) segments.push(current.join(" "));
      current = [];
    } else current.push(`${x(i)},${y(p.value)}`);
  });
  if (current.length > 1) segments.push(current.join(" "));

  const crossesZero = lo < 0 && hi > 0;
  const description = points
    .map(
      (p) =>
        `${p.season} ${p.value === null ? "no value" : fmt(p.value)}${p.small && p.value !== null ? " (small sample)" : ""}`,
    )
    .join(", ");

  return (
    <div
      className="mt-3 px-1.5 py-1.5"
      role="img"
      aria-label={`${label} by season: ${description}`}
    >
      <div className="relative h-14">
        <svg
          aria-hidden
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full overflow-visible"
        >
          {crossesZero && (
            <line
              x1="0"
              x2="100"
              y1={y(0)}
              y2={y(0)}
              stroke="var(--chalk-faint)"
              strokeDasharray="3 3"
              opacity="0.6"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {segments.map((pts, i) => (
            <polyline
              key={i}
              points={pts}
              fill="none"
              stroke="var(--chalk-dim)"
              strokeWidth="1.5"
              strokeLinejoin="round"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {crossesZero && (
          <span
            aria-hidden
            className="absolute -left-3.5 -translate-y-1/2 text-[10px] leading-none text-faint"
            style={{ top: `${y(0)}%` }}
          >
            0
          </span>
        )}
        {points.map((p, i) =>
          p.value === null ? null : (
            <span
              key={p.season}
              aria-hidden
              title={`${p.season}: ${fmt(p.value)}${p.small ? ` — under ${QUALIFY_MIN} games` : ""}`}
              className={clsx(
                "absolute -translate-x-1/2 -translate-y-1/2",
                i === lastIndex
                  ? "h-[9px] w-[9px] rounded-full bg-signal ring-2 ring-[var(--arena)]"
                  : p.small
                    ? "h-[7px] w-[7px] rounded-full border border-foreground/70 bg-panel"
                    : "h-[6px] w-[6px] rounded-full bg-foreground/80",
              )}
              style={{ left: `${x(i)}%`, top: `${y(p.value)}%` }}
            />
          ),
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ table */

export function SeasonTable({ seasons }: { seasons: SeasonLine[] }) {
  const latest = seasons.at(-1)?.season;
  return (
    <div>
      <p className="mb-1.5 text-[10px] text-faint md:hidden">
        <span aria-hidden>→ </span>scroll sideways for every column
      </p>
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full min-w-[680px]">
          <caption className="sr-only">
            Per-game averages and advanced rates for each season on file, oldest first
          </caption>
          <thead>
            <tr className="border-b border-line">
              {/* The season column stays pinned while a phone scrolls the numbers. */}
              <Th className="sticky left-0 z-[1] bg-panel">Season</Th>
              <Th>Team</Th>
              <Th numeric>GP</Th>
              <Th numeric>MIN</Th>
              <Th numeric className="text-foreground">
                PTS
              </Th>
              <Th numeric>REB</Th>
              <Th numeric>AST</Th>
              <Th numeric>TS%</Th>
              <Th numeric>USG%</Th>
              <Th numeric>Net rtg</Th>
            </tr>
          </thead>
          <tbody>
            {seasons.map((season) => {
              const isLatest = season.season === latest;
              const gp = num(season.base?.GP) ?? num(season.advanced?.GP);
              const small = gp !== null && gp < QUALIFY_MIN;
              const net = num(season.advanced?.NET_RATING);
              const team = season.base?.TEAM_ABBREVIATION ?? season.advanced?.TEAM_ABBREVIATION;
              return (
                <tr
                  key={season.season}
                  className={clsx(
                    "border-b border-hairline",
                    isLatest && "bg-[color-mix(in_srgb,var(--signal)_6%,var(--arena))]",
                  )}
                >
                  <Td
                    className={clsx(
                      "sticky left-0 z-[1] whitespace-nowrap",
                      isLatest
                        ? "bg-[color-mix(in_srgb,var(--signal)_6%,var(--arena))]"
                        : "bg-panel",
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <span
                        aria-hidden
                        className={clsx(
                          "h-3.5 w-[3px] rounded-[1px]",
                          isLatest ? "bg-signal" : "bg-transparent",
                        )}
                      />
                      <span className="numeral text-[14px] text-foreground">{season.season}</span>
                      {isLatest && (
                        <span className="text-[10px] uppercase tracking-[0.08em] text-signal">
                          latest
                        </span>
                      )}
                    </span>
                  </Td>
                  <Td className="text-[12px] text-muted">
                    {typeof team === "string" ? team : "—"}
                  </Td>
                  <Td numeric className={small ? "text-unavail" : "text-muted"}>
                    {gp ?? "—"}
                    {small && (
                      <span className="sr-only"> games, under {QUALIFY_MIN} — small sample</span>
                    )}
                  </Td>
                  <Td numeric className="text-muted">
                    {num(season.base?.MIN)?.toFixed(1) ?? "—"}
                  </Td>
                  <Td numeric className="font-semibold text-foreground">
                    {num(season.base?.PTS)?.toFixed(1) ?? "—"}
                  </Td>
                  <Td numeric className="text-muted">
                    {num(season.base?.REB)?.toFixed(1) ?? "—"}
                  </Td>
                  <Td numeric className="text-muted">
                    {num(season.base?.AST)?.toFixed(1) ?? "—"}
                  </Td>
                  <Td numeric className="text-foreground">
                    {pct(num(season.advanced?.TS_PCT), 1)}
                  </Td>
                  <Td numeric className="text-muted">
                    {pct(num(season.advanced?.USG_PCT), 1)}
                  </Td>
                  <Td numeric className="text-foreground">
                    {net !== null ? signed(net) : "—"}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
