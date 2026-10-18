"use client";

/**
 * The roster on the league — every rostered player's impact estimate placed on ONE
 * distribution of every player Pivot scored this season.
 *
 * The bars are the served population (`GET /players/impact-distribution`), the axis is the
 * raw model index (TEI, unitless — not points per 100), and the dashed ticks are the
 * league's quartiles, so the counts under the figure can be checked against the picture.
 * Each player is a flag on a pole planted at his index. Players with no estimate are not
 * planted anywhere: they are listed beside the figure as unscored, never at zero and never
 * at the median.
 *
 * Label layout (see `layout`): flags are stacked in lanes as two staircases that meet in
 * the middle, placed so that no pole is ever drawn through another player's label.
 */

import clsx from "clsx";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  histogram,
  percentileDisplay,
  placeImpact,
  useImpactReference,
  type AvailableImpactReference,
  type ImpactPlacement,
} from "@/lib/impact";
import { count, ordinal, tei as fmtTei } from "@/lib/format";
import type { RosterPlayer } from "@/lib/types";
import { SourceRail, UnavailableNotice } from "@/components/ui";

const LANE_H = 21;
const HIST_H = 64;
const GAP = 6;
/** Geist at 11px with tabular figures advances ~0.6em per glyph at most. */
const CHAR_W = 6.5;

function useMeasuredWidth() {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.floor(entries[0]?.contentRect.width ?? 0);
      setWidth((prev) => (prev === next ? prev : next));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, width] as const;
}

const SUFFIX = /^(jr\.?|sr\.?|ii|iii|iv|v)$/i;

export function shortName(name: string): string {
  const parts = name.split(" ").filter(Boolean);
  if (parts.length <= 1) return name;
  const last = parts[parts.length - 1];
  if (SUFFIX.test(last) && parts.length >= 3) return `${parts[parts.length - 2]} ${last}`;
  return last;
}

function initials(name: string): string {
  return name
    .split(" ")
    .filter((p) => p && !SUFFIX.test(p))
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

interface Planted {
  player: RosterPlayer;
  placement: ImpactPlacement;
}

interface Flag extends Planted {
  x: number;
  w: number;
  lane: number;
  text: string;
  /** Which way the flag flies from its pole. */
  side: "left" | "right";
}

/**
 * Two staircases that meet in the middle. The lower half of the roster flies its flags
 * to the left of the pole and is placed left-to-right; the upper half flies them to the
 * right and is placed right-to-left. Within each half a lane remembers the innermost
 * thing occupying it — a flag, or a pole passing up through it — and a flag only goes
 * where it cannot cover another player's pole. The halves cannot collide: every
 * left-flying pole sits at or left of every right-flying one.
 */
function layout(
  planted: Planted[],
  width: number,
  min: number,
  span: number,
  mode: "name" | "initials",
) {
  const pct = (p: ImpactPlacement) => String(percentileDisplay(p.percentile));
  // Two players sharing a surname get a first initial, so a flag never names two people.
  const surnames = new Map<string, number>();
  for (const item of planted) {
    const short = shortName(item.player.name);
    surnames.set(short, (surnames.get(short) ?? 0) + 1);
  }
  const label = (name: string) => {
    if (mode === "initials") return initials(name);
    const short = shortName(name);
    return (surnames.get(short) ?? 0) > 1 ? `${name.trim()[0]}. ${short}` : short;
  };
  const items = planted
    .map((item) => {
      const text = label(item.player.name);
      // + padding, the pole border and the gap; uppercase initials run wider than the average.
      const w = Math.ceil((text.length + 1 + pct(item.placement).length) * CHAR_W + 14);
      const f = (Math.min(min + span, Math.max(min, item.placement.value)) - min) / span;
      return { ...item, text, w, f };
    })
    .sort((a, b) => a.f - b.f || a.player.name.localeCompare(b.player.name));
  const half = Math.floor(items.length / 2);
  const leftItems = items.slice(0, half);
  const rightItems = items.slice(half);

  // Fit the plot inside the frame so the outermost flags on both sides still fit.
  let plotX = 0;
  let plotW = width;
  for (let i = 0; i < 4; i++) {
    plotX = Math.max(0, ...leftItems.map((it) => it.w - it.f * plotW));
    plotW = width - plotX;
    for (const it of rightItems) {
      if (it.f > 0) plotW = Math.min(plotW, (width - plotX - it.w) / it.f);
    }
  }
  plotW = Math.max(width * 0.5, Math.floor(plotW));

  const flags: Flag[] = [];

  // Upper half: right-flying, placed right to left.
  const rightLimits: number[] = [];
  for (const it of [...rightItems].reverse()) {
    const x = plotX + it.f * plotW;
    let lane = rightLimits.length;
    for (let k = 0; k < rightLimits.length; k++) {
      const fits = x + it.w + GAP <= rightLimits[k];
      const polesClear = rightLimits.slice(0, k).every((l) => x <= l);
      if (fits && polesClear) {
        lane = k;
        break;
      }
    }
    if (lane === rightLimits.length) rightLimits.push(Number.POSITIVE_INFINITY);
    for (let j = 0; j <= lane; j++) rightLimits[j] = Math.min(rightLimits[j], x);
    flags.push({ player: it.player, placement: it.placement, x, w: it.w, lane, text: it.text, side: "right" });
  }

  // Lower half: left-flying, placed left to right.
  const leftLimits: number[] = [];
  for (const it of leftItems) {
    const x = plotX + it.f * plotW;
    let lane = leftLimits.length;
    for (let k = 0; k < leftLimits.length; k++) {
      const fits = x - it.w - GAP >= leftLimits[k];
      const polesClear = leftLimits.slice(0, k).every((l) => x >= l);
      if (fits && polesClear) {
        lane = k;
        break;
      }
    }
    if (lane === leftLimits.length) leftLimits.push(Number.NEGATIVE_INFINITY);
    for (let j = 0; j <= lane; j++) leftLimits[j] = Math.max(leftLimits[j], x);
    flags.push({ player: it.player, placement: it.placement, x, w: it.w, lane, text: it.text, side: "left" });
  }

  // Highest index first, for reading order.
  flags.sort((a, b) => b.placement.value - a.placement.value || a.player.name.localeCompare(b.player.name));
  return {
    flags,
    lanes: Math.max(1, rightLimits.length, leftLimits.length),
    plotX,
    plotW,
  };
}

export function RosterOnLeague({ roster, teamAbbr }: { roster: RosterPlayer[]; teamAbbr: string }) {
  const { data: reference, isError } = useImpactReference();
  const [measure, width] = useMeasuredWidth();
  const [active, setActive] = useState<string | null>(null);

  const available = reference?.available ? reference : undefined;
  const planted = useMemo<Planted[]>(() => {
    if (!available) return [];
    return roster
      .map((player) => ({
        player,
        placement: placeImpact(player.tei, available, { low: player.tei_low, high: player.tei_high }),
      }))
      .filter((x): x is Planted => x.placement !== null);
  }, [roster, available]);
  const unscored = roster.filter((p) => p.tei === null);

  if (isError) {
    return (
      <UnavailableNotice reason="The league impact reference did not load, so the roster cannot be placed in the league. Each player's raw index is still in the roster list below." />
    );
  }
  if (reference && !reference.available) {
    return <UnavailableNotice reason={reference.reason} />;
  }

  // Until the league reference arrives nobody is placed yet, so the counts are unknown —
  // shown as "—", not as four zeros.
  //
  // Bucketed on the percentile as it is PRINTED on each flag (`percentileDisplay`), not on
  // the unrounded one: a player whose flag reads 75 is counted in the top quarter, so the
  // counts can be checked against the flags by eye.
  const inBand = (lo: number, hi: number) =>
    available
      ? planted.filter((p) => {
          const shown = percentileDisplay(p.placement.percentile);
          return shown >= lo && shown < hi;
        }).length
      : null;
  // Short labels so the five tiles never truncate in a narrow column; the full name is the
  // tile's accessible name and tooltip. ("Q1" is avoided on purpose: in statistics it is
  // the LOWER quartile, the opposite of what a "top" tile means.)
  const quarters = [
    { label: "Top", full: "Top quarter of the league", note: "≥ 75th", n: inBand(75, Infinity) },
    { label: "Upper mid", full: "Second quarter of the league", note: "50th–74th", n: inBand(50, 75) },
    { label: "Lower mid", full: "Third quarter of the league", note: "25th–49th", n: inBand(25, 50) },
    { label: "Bottom", full: "Bottom quarter of the league", note: "< 25th", n: inBand(-Infinity, 25) },
  ];

  return (
    // A flex column so that, stretched beside the composition card, the provenance line
    // sits on the card's bottom edge.
    <div className="flex min-w-0 flex-1 flex-col">
      <div ref={measure} className="relative w-full">
        {available && width > 0 ? (
          <Figure
            reference={available}
            planted={planted}
            width={width}
            active={active}
            setActive={setActive}
            teamAbbr={teamAbbr}
          />
        ) : (
          <div className="skeleton h-[150px]" aria-hidden />
        )}
      </div>

      {/* Where the roster sits, by quarter of the league — counted on the printed flags. */}
      <dl className="mt-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-hairline bg-hairline sm:grid-cols-5">
        {quarters.map((q) => (
          <div key={q.label} className="min-w-0 bg-panel px-3.5 py-3" title={`${q.full} (${q.note})`}>
            <dt className="eyebrow truncate">
              <span aria-hidden>{q.label}</span>
              <span className="sr-only">{q.full}</span>
            </dt>
            <dd className="mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
              <span
                className={`stat-serif text-[1.85rem] leading-none ${q.n === null ? "text-faint" : "text-foreground"}`}
              >
                {q.n ?? "—"}
              </span>
              <span className="whitespace-nowrap text-[11px] text-faint">{q.note}</span>
            </dd>
          </div>
        ))}
        <div className="col-span-2 min-w-0 bg-panel px-3.5 py-3 sm:col-span-1">
          <dt className="eyebrow text-unavail">Unscored</dt>
          <dd className="mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
            <span className="stat-serif text-[1.85rem] leading-none text-unavail">{unscored.length}</span>
            <span className="text-[11px] text-faint">not placed</span>
          </dd>
        </div>
      </dl>

      {unscored.length > 0 && (
        <p className="mt-3 flex items-start gap-2 rounded-lg border border-dashed border-unavail/45 px-3.5 py-2.5 text-[12.5px] leading-relaxed text-muted">
          <span aria-hidden className="mt-px text-unavail">
            —
          </span>
          <span>
            <span className="font-medium text-foreground">Unscored: </span>
            {unscored.map((p) => p.name).join(", ")} —{" "}
            {unscored.length === 1 ? "below" : "each below"}{" "}the model&apos;s{" "}
            {available ? `${available.population.min_window_minutes}-minute` : "minutes"} window.
            Not planted on the strip: an unmeasured player is not a zero and not an average one.
          </span>
        </p>
      )}
      {available && (
        <div className="mt-auto">
          <SourceRail
            source={`Pivot impact model ${available.model.version}`}
            retrievedAt={available.model.trained_at}
            extra={<span>· date is the model&apos;s training run · not a provider metric</span>}
          />
        </div>
      )}
    </div>
  );
}

function Figure({
  reference,
  planted,
  width,
  active,
  setActive,
  teamAbbr,
}: {
  reference: AvailableImpactReference;
  planted: Planted[];
  width: number;
  active: string | null;
  setActive: (id: string | null) => void;
  teamAbbr: string;
}) {
  const values = reference.values;
  const min = values[0];
  const max = values[values.length - 1];
  const span = max - min || 1;

  const { flags, lanes, plotX, plotW } = useMemo(() => {
    const named = layout(planted, width, min, span, "name");
    return named.lanes > 7 && width < 640 ? layout(planted, width, min, span, "initials") : named;
  }, [planted, width, min, span]);

  const bins = width >= 700 ? 56 : width >= 480 ? 40 : 28;
  const hist = useMemo(() => histogram(values, bins), [values, bins]);
  const x = (v: number) => plotX + ((Math.min(max, Math.max(min, v)) - min) / span) * plotW;
  const at = (q: number) => values[Math.min(values.length - 1, Math.max(0, Math.round(q * (values.length - 1))))];
  const th = width >= 480 ? "th" : "";
  const quartiles = [
    { label: `25${th}`, value: at(0.25) },
    { label: `50${th}`, value: at(0.5) },
    { label: `75${th}`, value: at(0.75) },
  ];

  const lanesH = lanes * LANE_H;
  const totalH = lanesH + HIST_H;
  const base = totalH - 0.5;
  const barW = plotW / hist.counts.length;
  const activeFlag = flags.find((f) => f.player.player_id === active) ?? null;

  return (
    <div>
      <div className="relative" style={{ height: totalH }}>
        <svg
          width={width}
          height={totalH}
          viewBox={`0 0 ${width} ${totalH}`}
          className="absolute inset-0 block overflow-visible"
          aria-hidden
        >
          {/* the league */}
          {hist.counts.map((c, i) => {
            const h = hist.peak ? Math.max(c ? 1.5 : 0, (c / hist.peak) * (HIST_H - 8)) : 0;
            return (
              <rect
                key={i}
                x={plotX + i * barW + barW * 0.1}
                width={Math.max(0.5, barW * 0.8)}
                y={totalH - h}
                height={h}
                fill="var(--chalk-faint)"
                opacity={0.3}
              />
            );
          })}
          {quartiles.map((q) => (
            <line
              key={q.label}
              x1={x(q.value)}
              x2={x(q.value)}
              y1={lanesH + 2}
              y2={totalH}
              stroke="var(--chalk-faint)"
              strokeDasharray="2 3"
              opacity={0.8}
            />
          ))}
          <line x1={plotX} x2={plotX + plotW} y1={base} y2={base} stroke="var(--hairline)" />

          {/* the band of the player under the pointer or focus */}
          {activeFlag?.placement.band && (
            <g>
              <rect
                x={x(activeFlag.placement.band.low)}
                width={Math.max(2, x(activeFlag.placement.band.high) - x(activeFlag.placement.band.low))}
                y={lanesH + 4}
                height={HIST_H - 4}
                fill="var(--signal)"
                opacity={0.12}
              />
              <path
                d={`M${x(activeFlag.placement.band.low)} ${totalH - 16}v8M${x(activeFlag.placement.band.low)} ${totalH - 12}H${x(activeFlag.placement.band.high)}M${x(activeFlag.placement.band.high)} ${totalH - 16}v8`}
                stroke="var(--signal)"
                strokeWidth={2}
                fill="none"
              />
            </g>
          )}

          {/* poles */}
          {flags.map((f) => {
            const top = (lanes - 1 - f.lane) * LANE_H + 2;
            const isActive = f.player.player_id === active;
            return (
              <g key={f.player.player_id}>
                <line
                  x1={f.x + 0.75}
                  x2={f.x + 0.75}
                  y1={top}
                  y2={totalH}
                  stroke="var(--signal)"
                  strokeWidth={isActive ? 1.75 : 1}
                  opacity={active && !isActive ? 0.3 : 0.8}
                />
                <circle cx={f.x + 0.75} cy={totalH - 1.5} r={2.25} fill="var(--signal)" />
              </g>
            );
          })}
        </svg>

        {/* flags — real links, in index order for assistive tech */}
        <ul aria-label={`${teamAbbr} players placed in the league, highest index first`}>
          {flags.map((f) => {
            const top = (lanes - 1 - f.lane) * LANE_H + 2;
            const isActive = f.player.player_id === active;
            const p = percentileDisplay(f.placement.percentile);
            const nth = ordinal(p);
            return (
              <li key={f.player.player_id} className="contents">
                <Link
                  href={`/players/${f.player.player_id}`}
                  onMouseEnter={() => setActive(f.player.player_id)}
                  onMouseLeave={() => setActive(null)}
                  onFocus={() => setActive(f.player.player_id)}
                  onBlur={() => setActive(null)}
                  aria-label={`${f.player.name}: ${nth} league impact percentile, ranked ${f.placement.tied > 1 ? "tied " : ""}${f.placement.rank} of ${f.placement.size}, index ${fmtTei(f.placement.value)}`}
                  title={`${f.player.name} · ${nth} pctl · ${f.placement.tied > 1 ? "T-" : ""}#${f.placement.rank} of ${f.placement.size} · index ${fmtTei(f.placement.value)}`}
                  className={clsx(
                    "absolute flex items-center gap-1 whitespace-nowrap border-signal px-1.5 text-[11px] font-medium leading-none tabular-nums transition-colors",
                    f.side === "right"
                      ? "rounded-r-[4px] border-l-[1.5px]"
                      : "flex-row-reverse rounded-l-[4px] border-r-[1.5px]",
                    isActive ? "bg-signal text-ink" : "bg-panel3/80 text-foreground hover:bg-panel3",
                    active && !isActive && "opacity-60",
                  )}
                  style={{
                    left: f.side === "right" ? f.x : f.x - f.w + 1.5,
                    top,
                    height: LANE_H - 5,
                    width: f.w,
                  }}
                >
                  <span className="truncate">{f.text}</span>
                  <span className={isActive ? "text-ink/70" : "font-normal text-muted"}>{p}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </div>

      {/* axis */}
      <div className="relative mt-1.5 h-9 text-[10.5px] tabular-nums text-faint" aria-hidden>
        {quartiles.map((q) => (
          <span
            key={q.label}
            className="absolute top-0 -translate-x-1/2 whitespace-nowrap"
            style={{ left: x(q.value) }}
          >
            {q.label}
          </span>
        ))}
        <span className="absolute top-4 whitespace-nowrap" style={{ left: plotX }}>
          idx {fmtTei(min)}
        </span>
        <span
          className="absolute top-4 -translate-x-full whitespace-nowrap"
          style={{ left: plotX + plotW }}
        >
          {fmtTei(max)}
        </span>
      </div>
      <p className="mt-1 text-[11.5px] leading-relaxed text-faint">
        Bars: all {count(reference.population.size, "player")} Pivot scored for {reference.season}.
        Axis: the model index (TEI) — unitless, not points per 100 possessions. Flag number =
        league percentile. Hover or focus a flag for its 10–90% band.
      </p>
    </div>
  );
}
