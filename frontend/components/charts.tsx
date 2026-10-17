"use client";

/**
 * Chart layer.
 *
 * Every chart ships inside a ChartFrame, which enforces the four things the
 * product requires of a visualization: a title, the unit being measured, a
 * one-line reason it matters, and a text summary that carries the same
 * information to screen readers (charts are `aria-hidden`; the summary is not).
 *
 * One look across the product: axis text in the UI sans in quiet ink, solid hairline
 * gridlines that stay behind the data, mint for the primary series, the warm neutral
 * (`--series-b`) for a comparison series, and coral only for a value below neutral.
 * Bars are thin with a 4px rounded data end and a square baseline; tooltips are a
 * rounded elevated card. Text never wears a series colour — the mark beside it does.
 */

import clsx from "clsx";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
  type BarShapeProps,
} from "recharts";
import { COMPONENT_LABEL } from "@/lib/format";
import type { TornadoBar, Uncertainty } from "@/lib/types";

const FONT = "var(--font-geist), system-ui, sans-serif";
/** Tick labels: quiet ink, tabular figures. */
const AXIS = { fill: "var(--chalk-faint)", fontSize: 11, fontFamily: FONT };
/** Category labels (the left column of a horizontal bar chart) read as text, one step louder. */
const CATEGORY = { fill: "var(--chalk-dim)", fontSize: 12, fontFamily: FONT };
/** Solid hairline, never dashed: the grid is a reference, not data. */
const GRID = "var(--hairline)";
/** The neutral reference (50 on a component score, 0 on a change). */
const NEUTRAL_LINE = "var(--chalk-faint)";
const CURSOR = { fill: "var(--arena-high)", opacity: 0.45 };

const TOOLTIP = {
  contentStyle: {
    backgroundColor: "var(--arena-raised)",
    border: "1px solid var(--hairline)",
    borderRadius: 8,
    boxShadow: "var(--shadow-pop)",
    padding: "8px 11px",
    fontSize: 12,
    fontFamily: FONT,
    color: "var(--chalk)",
  },
  labelStyle: { color: "var(--chalk)", fontWeight: 600, marginBottom: 3 },
  itemStyle: { color: "var(--chalk-dim)", padding: 0, fontVariantNumeric: "tabular-nums" },
  wrapperStyle: { outline: "none" },
} as const;

/** Bar radii, Recharts order [top-left, top-right, bottom-right, bottom-left]. */
const END_RIGHT: [number, number, number, number] = [0, 4, 4, 0];
const END_TOP: [number, number, number, number] = [4, 4, 0, 0];

export function ChartFrame({
  title,
  unit,
  why,
  summary,
  children,
  className,
}: {
  title: string;
  /** What the axis measures, e.g. "score, 0–100 (50 = neutral)". */
  unit: string;
  /** One line on why a decision-maker should care. */
  why?: string;
  /** Text equivalent of the chart, announced to assistive tech. */
  summary: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <figure className={clsx("min-w-0", className)}>
      <figcaption className="mb-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
          <span className="title-md text-foreground">{title}</span>
          <span className="text-[11.5px] text-faint">{unit}</span>
        </div>
        {why && <p className="mt-1 text-[12px] leading-snug text-muted">{why}</p>}
      </figcaption>
      <div aria-hidden>{children}</div>
      <p className="sr-only">{summary}</p>
    </figure>
  );
}

/** A small legend key: a round swatch and a word in text ink. */
function Key({ color, children, hollow = false }: { color: string; children: React.ReactNode; hollow?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-2 w-2 shrink-0 rounded-full"
        style={hollow ? { border: `1.75px solid ${color}` } : { background: color }}
      />
      {children}
    </span>
  );
}

/* ------------------------------------------------------- component profile */

/** A rectangle path with independent corner radii [tl, tr, br, bl]. */
function roundedRect(x: number, y: number, w: number, h: number, radii: [number, number, number, number]) {
  const cap = Math.min(w / 2, h / 2);
  const [tl, tr, br, bl] = radii.map((r) => Math.max(0, Math.min(r, cap)));
  return [
    `M${x + tl},${y}`,
    `H${x + w - tr}`,
    `Q${x + w},${y} ${x + w},${y + tr}`,
    `V${y + h - br}`,
    `Q${x + w},${y + h} ${x + w - br},${y + h}`,
    `H${x + bl}`,
    `Q${x},${y + h} ${x},${y + h - bl}`,
    `V${y + tl}`,
    `Q${x},${y} ${x + tl},${y}`,
    "Z",
  ].join(" ");
}

/**
 * A component score drawn from the neutral 50 line, not from zero: the end away from 50 is
 * the data end (rounded), the end on 50 is the baseline (square). Exactly 50 draws a hairline
 * tick so "no change" is still visible.
 */
function DeviationBarShape(props: BarShapeProps) {
  const row = (props.payload ?? {}) as { delta?: number; tone?: string };
  const delta = Number(row.delta ?? 0);
  let { x, width } = props;
  const { y, height } = props;
  if (width < 0) {
    x += width;
    width = -width;
  }
  if (width < 1.5) {
    const mid = delta >= 0 ? x : x + width;
    return <rect x={mid - 1} y={y} width={2} height={height} rx={1} fill="var(--chalk-faint)" />;
  }
  const radii: [number, number, number, number] = delta >= 0 ? END_RIGHT : [4, 0, 0, 4];
  return <path d={roundedRect(x, y, width, height, radii)} fill={row.tone} fillOpacity={0.92} />;
}

export function ComponentBars({
  components,
  excluded,
  accent = "var(--signal)",
}: {
  components: Record<string, number | null>;
  excluded?: string[];
  accent?: string;
}) {
  const data = Object.entries(components)
    .filter(([, value]) => value !== null)
    .map(([key, value]) => {
      const v = value as number;
      return {
        name: COMPONENT_LABEL[key] ?? key,
        value: v,
        delta: v - 50,
        // Coral only below neutral.
        tone: v >= 50 ? accent : "var(--illegal)",
        // Drawn from the neutral line: [50, v] or [v, 50].
        span: [Math.min(50, v), Math.max(50, v)] as [number, number],
      };
    });

  const summary = data
    .map((d) => `${d.name} ${d.value.toFixed(0)} of 100`)
    .join("; ");

  return (
    <ChartFrame
      title="Component scores"
      unit="score 0–100 · 50 = neutral"
      why="Where the deal actually helps or hurts, before any weighting is applied. Bars run from the neutral 50 line: right helps, left hurts."
      summary={`${summary}.${excluded?.length ? ` Not scored for missing data: ${excluded.join(", ")}.` : ""}`}
    >
      <ResponsiveContainer width="100%" height={data.length * 32 + 30}>
        <BarChart accessibilityLayer={false} data={data} layout="vertical" margin={{ left: 4, right: 14, top: 2, bottom: 2 }}>
          <CartesianGrid stroke={GRID} horizontal={false} />
          <XAxis
            type="number"
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            tick={AXIS}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={128}
            tick={CATEGORY}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            {...TOOLTIP}
            cursor={CURSOR}
            formatter={(_value, _name, item) => [
              `${Number((item?.payload as { value?: number } | undefined)?.value ?? 0).toFixed(1)} / 100`,
              "score",
            ]}
          />
          <ReferenceLine x={50} stroke={NEUTRAL_LINE} strokeWidth={1.25} />
          <Bar dataKey="span" barSize={12} shape={DeviationBarShape} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      {excluded && excluded.length > 0 && (
        <p className="mt-1.5 text-[12px] text-unavail">
          Not scored — data unavailable: {excluded.map((c) => COMPONENT_LABEL[c] ?? c).join(", ")}.
          Remaining weights were rescaled.
        </p>
      )}
    </ChartFrame>
  );
}

/* ----------------------------------------------------------- before/after */

export function BeforeAfterBars({
  rows,
  unit = "score 0–100",
  title = "Before vs after",
  why,
}: {
  rows: { name: string; before: number; after: number }[];
  unit?: string;
  title?: string;
  why?: string;
}) {
  const data = rows.map((r) => ({ ...r, delta: r.after - r.before }));
  return (
    <ChartFrame
      title={title}
      unit={unit}
      why={why}
      summary={data
        .map(
          (d) =>
            `${d.name}: ${d.before.toFixed(1)} before, ${d.after.toFixed(1)} after (${
              d.delta >= 0 ? "+" : ""
            }${d.delta.toFixed(1)})`,
        )
        .join("; ")}
    >
      <ResponsiveContainer width="100%" height={data.length * 38 + 30}>
        <BarChart accessibilityLayer={false} data={data} layout="vertical" margin={{ left: 4, right: 14 }} barGap={2}>
          <CartesianGrid stroke={GRID} horizontal={false} />
          <XAxis type="number" tick={AXIS} tickLine={false} axisLine={false} />
          <YAxis
            type="category"
            dataKey="name"
            width={128}
            tick={CATEGORY}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip {...TOOLTIP} cursor={CURSOR} formatter={(value) => Number(value).toFixed(1)} />
          <Bar dataKey="before" name="Before" fill="var(--series-b)" fillOpacity={0.8} barSize={10} radius={END_RIGHT} />
          <Bar dataKey="after" name="After" fill="var(--signal)" barSize={10} radius={END_RIGHT} />
        </BarChart>
      </ResponsiveContainer>
      <p className="mt-1.5 flex items-center gap-4 text-[11.5px] text-muted">
        <Key color="var(--series-b)">Before</Key>
        <Key color="var(--signal)">After</Key>
      </p>
    </ChartFrame>
  );
}

/* -------------------------------------------------------------- sensitivity */

export function TornadoChart({ bars }: { bars: TornadoBar[] }) {
  const data = bars.map((bar) => ({
    name: COMPONENT_LABEL[bar.component] ?? bar.component,
    range: [bar.utility_low, bar.utility_high] as [number, number],
    base: bar.base,
  }));
  if (data.length === 0) return null;
  const base = data[0].base;
  // An explicit, rounded domain: "auto" printed ticks like 54.15 / 55.1 and could clip the
  // widest bar's end. Whole-number ticks on a step that suits the spread.
  const lo = Math.min(base, ...data.map((d) => Math.min(d.range[0], d.range[1])));
  const hi = Math.max(base, ...data.map((d) => Math.max(d.range[0], d.range[1])));
  const pad = Math.max(0.5, (hi - lo) * 0.08);
  const rawSpan = hi - lo + 2 * pad;
  const step = rawSpan <= 6 ? 1 : rawSpan <= 12 ? 2 : rawSpan <= 30 ? 5 : 10;
  const domainLo = Math.max(0, Math.floor((lo - pad) / step) * step);
  const domainHi = Math.min(100, Math.ceil((hi + pad) / step) * step);
  const ticks: number[] = [];
  for (let t = domainLo; t <= domainHi + 1e-9; t += step) ticks.push(t);

  return (
    <ChartFrame
      title="Sensitivity to your priorities"
      unit="decision score 0–100"
      why="A wide bar means the verdict depends on how you weight that priority. The vertical line is the score at your current weights."
      summary={data
        .map((d) => `${d.name}: score moves between ${d.range[0].toFixed(1)} and ${d.range[1].toFixed(1)}`)
        .join("; ")}
    >
      <ResponsiveContainer width="100%" height={data.length * 30 + 30}>
        <BarChart accessibilityLayer={false} data={data} layout="vertical" margin={{ left: 4, right: 14 }}>
          <CartesianGrid stroke={GRID} horizontal={false} />
          <XAxis
            type="number"
            domain={[domainLo, domainHi]}
            ticks={ticks}
            allowDataOverflow={false}
            tick={AXIS}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            type="category"
            dataKey="name"
            width={128}
            tick={CATEGORY}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            {...TOOLTIP}
            cursor={CURSOR}
            formatter={(value) =>
              Array.isArray(value)
                ? [`${Number(value[0]).toFixed(1)} – ${Number(value[1]).toFixed(1)}`, "score range"]
                : [String(value), ""]
            }
          />
          {/* A range has two data ends, so both are rounded. */}
          <Bar dataKey="range" fill="var(--signal)" fillOpacity={0.55} radius={4} barSize={12} />
          <ReferenceLine x={base} stroke="var(--chalk-dim)" strokeWidth={1.5} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/* -------------------------------------------------------------- uncertainty */

export function UncertaintyStrip({ u, compact = false }: { u: Uncertainty; compact?: boolean }) {
  // A little air either side of the band, so its ends read as ends rather than the frame.
  const reachLo = Math.min(u.p10, -1);
  const reachHi = Math.max(u.p90, 1);
  const air = (reachHi - reachLo) * 0.05;
  const min = reachLo - air;
  const max = reachHi + air;
  const span = max - min || 1;
  const toPct = (x: number) => ((x - min) / span) * 100;
  // Sign from the rounded value, so a tiny negative never prints as "-0.0".
  const sign = (x: number) => {
    const r = Number(x.toFixed(1));
    return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(1)}`;
  };

  // No players move → every draw is exactly zero, so there is no distribution and no
  // "chance it helps". Say that rather than rendering a 0% that reads as a prediction.
  if (u.prob_positive === null || u.prob_positive === undefined) {
    const message =
      u.unavailable ?? "no players move, so there is no outcome distribution";
    if (compact) return <p className="text-[12px] text-muted">Projected wins: {message}.</p>;
    return (
      <ChartFrame
        title="Projected wins impact"
        unit="unavailable"
        why="An empty deal has no outcome distribution; a 0 % here would read as a prediction."
        summary={`Projected wins impact is unavailable — ${message}.`}
      >
        <p className="rounded-lg border border-dashed border-line px-3.5 py-4 text-[13px] text-muted">
          Not applicable — {message}.
        </p>
      </ChartFrame>
    );
  }
  const probPositive = u.prob_positive;

  const strip = (
    <div>
      <div className="relative h-7 rounded-lg bg-panel3/70">
        {/* zero line: the honest reference point */}
        <div
          className="absolute -inset-y-0.5 w-px bg-muted/70"
          style={{ left: `${toPct(0)}%` }}
          aria-hidden
        />
        <div
          className="absolute inset-y-[7px] rounded-full"
          style={{
            left: `${toPct(u.p10)}%`,
            width: `${Math.max(1, toPct(u.p90) - toPct(u.p10))}%`,
            background: "color-mix(in srgb, var(--signal) 42%, transparent)",
          }}
          aria-hidden
        />
        <div
          className="absolute inset-y-[3px] w-[2px] rounded-full bg-signal"
          style={{ left: `calc(${toPct(u.median)}% - 1px)` }}
          aria-hidden
        />
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-3 text-[11.5px] text-muted">
        <span className="data">{sign(u.p10)}</span>
        <span className="text-center">
          median <span className="numeral text-foreground">{sign(u.median)} wins</span> ·{" "}
          {(probPositive * 100).toFixed(0)}% chance it helps
        </span>
        <span className="data">{sign(u.p90)}</span>
      </div>
    </div>
  );

  if (compact) return strip;

  return (
    <ChartFrame
      title="Projected wins impact"
      unit={`wins vs today · ${u.n_draws.toLocaleString()} simulations`}
      why="The band is the honest answer; the midpoint alone would overstate what the model knows."
      summary={`Median ${sign(u.median)} wins, with a 10th-to-90th percentile range of ${sign(
        u.p10,
      )} to ${sign(u.p90)} wins, and a ${(probPositive * 100).toFixed(0)} percent chance the deal helps.`}
    >
      {strip}
    </ChartFrame>
  );
}

/* ------------------------------------------------------------ comparisons */

/** Deal names inside an SVG cannot wrap; cut long ones and let the tooltip carry the rest. */
function clip(name: string, max: number): string {
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

/**
 * A plain <text> above a scatter point. Recharts' own label wraps the text into the mark's
 * width, which stacks a deal name one word per line.
 */
function scatterLabel(fill: string, below: boolean) {
  return function ScatterPointLabel(props: {
    x?: number | string;
    y?: number | string;
    width?: number | string;
    height?: number | string;
    value?: unknown;
  }) {
    const x = Number(props.x ?? 0) + Number(props.width ?? 0) / 2;
    // Frontier names sit above their mark, dominated names below, so two deals that land
    // close together do not print over each other.
    const y = below ? Number(props.y ?? 0) + Number(props.height ?? 0) + 15 : Number(props.y ?? 0) - 8;
    return (
      <text x={x} y={y} textAnchor="middle" fill={fill} fontSize={11} fontFamily={FONT}>
        {String(props.value ?? "")}
      </text>
    );
  };
}

const FRONTIER_LABEL = scatterLabel("var(--chalk)", false);
const DOMINATED_LABEL = scatterLabel("var(--chalk-faint)", true);

/**
 * Two stored components against each other, both scored so that higher is better: on-court
 * impact across, downside protection (the risk component — availability exposure, where a
 * higher score means LESS exposure) up. Up and to the right is better on both.
 *
 * Filled marks are on the Pareto frontier, hollow ones are dominated. Domination is judged by
 * the backend on every component two deals share, not only the two drawn here — so the
 * caption says so instead of claiming "both axes".
 */
export function ParetoScatter({
  points,
}: {
  points: { name: string; x: number; y: number; dominated: boolean }[];
}) {
  const frontier = points.filter((p) => !p.dominated).map((p) => ({ ...p, label: clip(p.name, 18) }));
  const dominated = points.filter((p) => p.dominated).map((p) => ({ ...p, label: clip(p.name, 18) }));
  const axisLabel = { fill: "var(--chalk-dim)", fontSize: 11, fontFamily: FONT };

  return (
    <ChartFrame
      title="Trade-off frontier"
      unit="score 0–100 · higher is better on both axes"
      why="Filled = on the frontier, hollow = Pareto-dominated. Domination is judged on every component two deals share, not only the two plotted here."
      summary={
        points.length === 0
          ? "No deal could be plotted."
          : points
              .map(
                (p) =>
                  `${p.name}: on-court impact ${p.x.toFixed(0)}, downside protection ${p.y.toFixed(0)}${
                    p.dominated
                      ? " (Pareto-dominated on the components it shares with another deal)"
                      : " (on the frontier)"
                  }`,
              )
              .join("; ")
      }
    >
      <ResponsiveContainer width="100%" height={272}>
        <ScatterChart accessibilityLayer={false} margin={{ left: 0, right: 18, top: 22, bottom: 20 }}>
          <CartesianGrid stroke={GRID} />
          <XAxis
            type="number"
            dataKey="x"
            name="On-court impact"
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "var(--hairline)" }}
            label={{
              value: "On-court impact, higher is better",
              position: "insideBottom",
              offset: -14,
              ...axisLabel,
            }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name="Downside protection"
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            width={44}
            tick={AXIS}
            tickLine={false}
            axisLine={{ stroke: "var(--hairline)" }}
            label={{
              value: "Downside protection",
              angle: -90,
              position: "insideLeft",
              offset: 10,
              dy: 56,
              ...axisLabel,
            }}
          />
          <ZAxis range={[110, 110]} />
          {/* 50 is neutral on every component: the quadrant lines are the honest reference. */}
          <ReferenceLine x={50} stroke={NEUTRAL_LINE} strokeOpacity={0.7} />
          <ReferenceLine y={50} stroke={NEUTRAL_LINE} strokeOpacity={0.7} />
          <Tooltip
            {...TOOLTIP}
            cursor={{ stroke: "var(--hairline)" }}
            formatter={(value, name) => [Number(value).toFixed(0), String(name)]}
            labelFormatter={(_, payload) => String(payload?.[0]?.payload?.name ?? "")}
          />
          <Scatter
            name="Dominated"
            data={dominated}
            fill="var(--arena)"
            stroke="var(--unknown)"
            strokeWidth={1.75}
            isAnimationActive={false}
          >
            <LabelList dataKey="label" content={DOMINATED_LABEL} />
          </Scatter>
          <Scatter
            name="Frontier"
            data={frontier}
            fill="var(--signal)"
            stroke="var(--arena)"
            strokeWidth={2}
            isAnimationActive={false}
          >
            <LabelList dataKey="label" content={FRONTIER_LABEL} />
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-muted">
        <Key color="var(--signal)">On the frontier</Key>
        <Key color="var(--unknown)" hollow>
          Dominated
        </Key>
        <span className="text-faint">Downside protection: higher = less exposure</span>
      </p>
    </ChartFrame>
  );
}

/**
 * How often each deal comes out on top across the saved analysis's sampled weightings. The
 * sample size belongs to the response, so it is not written into the unit here — the page
 * prints the real `n_samples` beside the chart.
 */
export function FirstPlaceShareBars({
  share,
  names,
}: {
  share: Record<string, number>;
  names: Record<string, string>;
}) {
  const data = Object.entries(share)
    .map(([id, value]) => {
      const name = names[id] ?? id.slice(0, 8);
      return { name, label: clip(name, 20), value: value * 100 };
    })
    .sort((a, b) => b.value - a.value);
  const top = data[0]?.value ?? 0;

  return (
    <ChartFrame
      title="How often each deal ranks first"
      unit="% of sampled weightings"
      why="A deal that only leads under one exact set of priorities isn't a robust recommendation."
      summary={data.map((d) => `${d.name} ranks first in ${d.value.toFixed(0)}% of weightings`).join("; ")}
    >
      <ResponsiveContainer width="100%" height={data.length * 34 + 30}>
        <BarChart accessibilityLayer={false} data={data} layout="vertical" margin={{ left: 0, right: 40, top: 4, bottom: 0 }}>
          <CartesianGrid stroke={GRID} horizontal={false} />
          <XAxis
            type="number"
            domain={[0, 100]}
            ticks={[0, 25, 50, 75, 100]}
            tick={AXIS}
            unit="%"
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={140}
            tick={{ ...CATEGORY, fill: "var(--chalk)" }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            {...TOOLTIP}
            cursor={CURSOR}
            formatter={(value) => [`${Number(value).toFixed(1)}%`, "ranks first"]}
            labelFormatter={(_, payload) => String(payload?.[0]?.payload?.name ?? "")}
          />
          <Bar dataKey="value" radius={END_RIGHT} barSize={14} isAnimationActive={false}>
            {data.map((entry, index) => (
              <Cell
                // index, not name: two saved deals can share a name
                key={index}
                fill={entry.value === top && top > 0 ? "var(--signal)" : "var(--series-b)"}
                fillOpacity={entry.value === top && top > 0 ? 0.95 : 0.7}
              />
            ))}
            <LabelList
              dataKey="value"
              position="right"
              offset={8}
              formatter={(value) => `${Number(value).toFixed(0)}%`}
              fill="var(--chalk)"
              fontSize={11.5}
              fontFamily={FONT}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/* --------------------------------------------------------- salary timeline */

export function SalaryTimeline({
  seasons,
  capLine,
  taxLine,
}: {
  seasons: { season: string; total: number; players?: number }[];
  capLine?: number;
  taxLine?: number;
}) {
  const data = seasons.map((s) => ({ ...s, millions: s.total / 1_000_000 }));
  // The scale reaches the cap and tax lines even when committed money is low, so the reference
  // lines the caption describes are always on the chart — on round $-million ticks.
  const reach =
    Math.max(0, ...data.map((d) => d.millions), (capLine ?? 0) / 1_000_000, (taxLine ?? 0) / 1_000_000) * 1.05;
  const yStep = reach > 150 ? 50 : reach > 60 ? 25 : reach > 20 ? 10 : 5;
  const yMax = Math.max(yStep, Math.ceil(reach / yStep) * yStep);
  const yTicks: number[] = [];
  for (let t = 0; t <= yMax + 1e-9; t += yStep) yTicks.push(t);
  // Reference labels are text, so they wear text ink; the line beside them carries identity.
  const refLabel = { fill: "var(--chalk-dim)", fontSize: 11, fontFamily: FONT, position: "right" as const };
  return (
    <ChartFrame
      title="Committed payroll by season"
      unit="$ millions"
      why="Where the money is already spoken for — and when the books clear."
      summary={data
        .map((d) => `${d.season}: $${d.millions.toFixed(1)}M across ${d.players ?? "?"} players`)
        .join("; ")}
    >
      <ResponsiveContainer width="100%" height={224}>
        {/* right margin holds the cap/tax reference labels — without it they clip */}
        <BarChart accessibilityLayer={false} data={data} margin={{ left: 4, right: 34, top: 8 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="season" tick={AXIS} tickLine={false} axisLine={{ stroke: "var(--hairline)" }} />
          <YAxis
            tick={AXIS}
            tickLine={false}
            axisLine={false}
            domain={[0, yMax]}
            ticks={yTicks}
            tickFormatter={(value: number) => (value === 0 ? "0" : `$${value}M`)}
            width={52}
          />
          <Tooltip
            {...TOOLTIP}
            cursor={CURSOR}
            formatter={(value) => [`$${Number(value).toFixed(1)}M`, "committed"]}
          />
          <Bar dataKey="millions" fill="var(--signal)" fillOpacity={0.85} radius={END_TOP} maxBarSize={28} />
          {/* Dashed on purpose: the page's caption names them "dashed lines". */}
          {capLine && (
            <ReferenceLine
              y={capLine / 1_000_000}
              stroke="var(--chalk-dim)"
              strokeDasharray="4 4"
              label={{ value: "cap", ...refLabel }}
            />
          )}
          {taxLine && (
            <ReferenceLine
              y={taxLine / 1_000_000}
              stroke="var(--conditional)"
              strokeDasharray="4 4"
              label={{ value: "tax", ...refLabel }}
            />
          )}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
