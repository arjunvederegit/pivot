"use client";

/**
 * The decision workflow, drawn as a possession.
 *
 * Observe, diagnose, test, decide run in the same order as a half-court possession —
 * survey from half court, read the defence at the arc, attack the lane, finish at the rim
 * — and on a 47-foot half court those four landmarks happen to sit almost exactly at the
 * centres of four equal columns (6 ft, 18 ft, 28 ft and 41.75 ft from the half-court
 * line). So each station is placed at its real distance, and the step it opens sits in
 * the column underneath it.
 *
 * Every status here is derived, never declared. A step is "ready" only when the data it
 * depends on is actually loaded: rosters for Team Outlook, the scored population for the
 * Player Explorer, contracts for the Trade Evaluator's salary rules, and two saved deals
 * for the Decision Board. The line into a station is lit only when that station is ready,
 * and each station's marker is filled, half-filled or open by the same rule, so the court
 * is a status diagram, not an illustration.
 */

import clsx from "clsx";
import Link from "next/link";
import type { ImpactReference } from "@/lib/impact";
import { count } from "@/lib/format";
import type { DataHealth, TradeSummary } from "@/lib/types";
import { CommandLine } from "@/components/command/command-line";
import { BallGlyph } from "@/components/court";
import { IconArrowRight, IconCap, IconHealth, IconMethod } from "@/components/icons";
import { Badge, Explainer } from "@/components/ui";

/* ------------------------------------------------------------------- status */

export type StepState = "ready" | "partial" | "waiting" | "blocked" | "checking";

export interface StepStatus {
  state: StepState;
  /** The badge word. Always paired with the badge glyph, never colour alone. */
  word: string;
  /** The whole state in one line — read by assistive tech and shown when there is no figure. */
  reading: string;
  /** The headline reading, as served ("530"), or "—" when what it counts is not there. */
  figure?: string;
  /** What the figure counts ("roster spots"). */
  unit?: string;
  /** The rest of the state, in one short line. */
  detail?: string;
}

const BADGE: Record<StepState, string> = {
  ready: "pass",
  partial: "conditionally_valid",
  waiting: "incomplete",
  blocked: "unavailable",
  checking: "not_evaluated",
};

const TONE: Record<StepState, string> = {
  ready: "var(--signal)",
  partial: "var(--conditional)",
  waiting: "var(--conditional)",
  blocked: "var(--unknown)",
  checking: "var(--hairline)",
};

const CHECKING: StepStatus = { state: "checking", word: "checking", reading: "Reading what is loaded…" };

export function observeStatus(health: DataHealth | undefined, failed: boolean): StepStatus {
  if (failed) return { state: "blocked", word: "unknown", reading: "Data-health report did not load" };
  if (!health) return CHECKING;
  const spots = health.tables?.rosters?.rows;
  // A missing table is "not reported", not "no rosters": never read an absent count as 0.
  if (spots === undefined) {
    return {
      state: "blocked",
      word: "unknown",
      reading: "Roster table not reported",
      figure: "—",
      unit: "roster spots",
      detail: "Roster table not reported",
    };
  }
  if (spots === 0) {
    return {
      state: "blocked",
      word: "no rosters",
      reading: "No rosters imported yet",
      figure: "0",
      unit: "roster spots",
      detail: "No rosters imported yet",
    };
  }
  const teams = health.tables?.teams?.rows;
  return {
    state: "ready",
    word: "ready",
    reading: `${count(spots, "roster spot")}${teams ? ` · ${count(teams, "team")}` : ""}`,
    figure: spots.toLocaleString(),
    unit: spots === 1 ? "roster spot" : "roster spots",
    detail: teams ? `across ${count(teams, "team")}` : undefined,
  };
}

export function diagnoseStatus(reference: ImpactReference | undefined, failed: boolean): StepStatus {
  if (failed) return { state: "blocked", word: "unknown", reading: "League impact reference did not load" };
  if (!reference) return CHECKING;
  if (!reference.available) return { state: "blocked", word: "unscored", reading: reference.reason };
  const { size, rostered_unscored } = reference.population;
  return {
    state: "ready",
    word: "ready",
    reading: `${count(size, "player")} scored · ${rostered_unscored} rostered unscored`,
    figure: size.toLocaleString(),
    unit: size === 1 ? "player scored" : "players scored",
    detail: `${rostered_unscored} rostered unscored`,
  };
}

export function testStatus(health: DataHealth | undefined, failed: boolean): StepStatus {
  if (failed) return { state: "blocked", word: "unknown", reading: "Data-health report did not load" };
  if (!health) return CHECKING;
  if (health.providers?.contracts?.configured === true) {
    const rows = health.tables?.contracts?.rows;
    return {
      state: "ready",
      word: "ready",
      reading: "Every rule runs, salary matching included",
      figure: typeof rows === "number" ? rows.toLocaleString() : undefined,
      unit: typeof rows === "number" ? (rows === 1 ? "contract on file" : "contracts on file") : undefined,
      detail: "Every rule runs, salary matching included",
    };
  }
  return {
    state: "partial",
    word: "partial",
    reading: "Salary rules do not run · no contracts imported",
    figure: "—",
    unit: "contracts on file",
    detail: "Salary rules do not run until contracts are imported",
  };
}

export function decideStatus(trades: TradeSummary[] | undefined, failed: boolean): StepStatus {
  if (failed) return { state: "blocked", word: "unknown", reading: "Saved deals did not load" };
  if (!trades) return CHECKING;
  const n = trades.length;
  const unit = n === 1 ? "saved deal" : "saved deals";
  if (n >= 2) {
    return {
      state: "ready",
      word: "ready",
      reading: `${count(n, "saved deal")} to compare`,
      figure: String(n),
      unit,
      detail: "ready to compare side by side",
    };
  }
  return {
    state: "waiting",
    word: `${n} of 2 deals`,
    reading: n === 0 ? "No saved deals · the board compares two or more" : "1 saved deal · the board compares two or more",
    figure: String(n),
    unit,
    detail: "the board compares two or more",
  };
}

/* ------------------------------------------------------------------- steps */

interface StepDef {
  n: string;
  verb: string;
  title: string;
  blurb: string;
  module: string;
  /** Distance from the half-court line, as a share of the 47 ft half court. */
  at: number;
  landmark: string;
  /** The landmark at phone width, where four full labels would touch. */
  short: string;
  /** The same distance in feet, as printed on the strip. */
  feet: string;
}

const STEPS: StepDef[] = [
  {
    n: "1",
    verb: "Observe",
    title: "Understand the roster",
    blurb:
      "What a team actually contains: rotation, roles, measured strengths, and the needs that follow from them.",
    module: "Team Outlook",
    at: 60 / 470,
    feet: "6 ft",
    landmark: "Half court",
    short: "Half court",
  },
  {
    n: "2",
    verb: "Diagnose",
    title: "Identify the edge",
    blurb: "Who is out there, what they do, and which of them answers a need this roster actually has.",
    module: "Player Explorer",
    at: 180 / 470,
    feet: "18 ft",
    landmark: "The arc",
    short: "Arc",
  },
  {
    n: "3",
    verb: "Test",
    title: "Test the move",
    blurb:
      "Build a two- or three-team deal and get a live rules check with projected impact, fit and risk.",
    module: "Trade Evaluator",
    at: 280 / 470,
    feet: "28 ft",
    landmark: "The lane",
    short: "Lane",
  },
  {
    n: "4",
    verb: "Decide",
    title: "Make the decision",
    blurb: "Put the options side by side under your own priorities and see which one survives them.",
    module: "Decision Board",
    at: 417.5 / 470,
    feet: "41.75 ft",
    landmark: "The rim",
    short: "Rim",
  },
];

/* -------------------------------------------------------------- the court */

/**
 * The middle 25 ft of a 47 × 50 ft half court, half-court line on the left and the basket
 * on the right, in tenths of a foot. It is stretched to the strip (a low courtside view),
 * so the stations are drawn in HTML on top — a marker stays round whatever the strip's
 * proportions. Lengths along the possession are exact; only the depth is foreshortened.
 * Lines are chalk at low opacity, and the band fades out top and bottom, where the court
 * carries on past what the strip shows.
 */
function CourtLines() {
  const line = { vectorEffect: "non-scaling-stroke" as const };
  return (
    <svg
      viewBox="0 125 470 250"
      preserveAspectRatio="none"
      className="absolute inset-0 h-full w-full text-foreground [mask-image:linear-gradient(to_bottom,transparent,black_22%,black_78%,transparent)]"
      fill="none"
      aria-hidden
    >
      <g stroke="currentColor" strokeWidth="1">
        {/* half-court line and baseline (the band is the middle 25 ft, so no sidelines) */}
        <path d="M0 125V375M470 125V375" opacity="0.2" {...line} />
        {/* centre circle */}
        <path d="M0 190A60 60 0 0 1 0 310" opacity="0.2" {...line} />
        {/* three-point line: corners, then the arc */}
        <path d="M470 30H328A237.5 237.5 0 0 0 328 470H470" opacity="0.2" {...line} />
        {/* the lane and the free-throw circle (its outer half — the inner half is the lane) */}
        <rect x="280" y="170" width="190" height="160" fill="currentColor" fillOpacity="0.025" opacity="0.2" {...line} />
        <path d="M280 190A60 60 0 0 0 280 310" opacity="0.2" {...line} />
        {/* the backboard — the rim itself is the last station's marker */}
        <path d="M430 222V278" opacity="0.4" {...line} />
      </g>
    </svg>
  );
}

/** The path into a station: lit mint when the station is ready, a thin quiet line otherwise. */
function Segment({ from, to, state }: { from: number; to: number; state: StepState }) {
  const lit = state === "ready";
  return (
    <span
      aria-hidden
      className={clsx("absolute top-1/2 rounded-full", lit ? "-mt-[1px] h-[2px]" : "-mt-[0.5px] h-px")}
      style={{
        left: `${from * 100}%`,
        width: `${(to - from) * 100}%`,
        background: TONE[state],
        opacity: lit ? 1 : state === "checking" ? 0.9 : 0.55,
      }}
    />
  );
}

/** Filled = has what it needs; half = runs with something missing; open ring = waiting or absent. */
function stationFill(state: StepState): string {
  if (state === "ready") return TONE.ready;
  if (state === "partial") return `linear-gradient(90deg, ${TONE.partial} 50%, var(--arena) 50%)`;
  return "var(--arena)";
}

function PossessionCourt({ statuses }: { statuses: StepStatus[] }) {
  const summary = STEPS.map((s, i) => `${s.n} ${s.title}: ${statuses[i].word}`).join("; ");
  return (
    <div
      role="img"
      aria-label={`The workflow drawn as a possession, from half court to the rim. ${summary}.`}
      className="relative h-[118px] lg:h-[150px]"
    >
      {/* landmark labels, above the floor */}
      <div aria-hidden className="absolute inset-x-0 top-0 h-5">
        {STEPS.map((step, i) => {
          const first = i === 0;
          const last = i === STEPS.length - 1;
          return (
            <span
              key={step.n}
              className="absolute top-0"
              style={{ left: `${step.at * 100}%` }}
            >
              <span
                className={clsx(
                  "absolute top-0 whitespace-nowrap text-[11.5px] leading-5 text-muted",
                  last
                    ? "right-0 translate-x-2"
                    : first
                      ? "left-0 -translate-x-3 sm:-translate-x-1/2"
                      : "left-0 -translate-x-1/2",
                )}
              >
                <span className="numeral mr-1.5 hidden text-foreground sm:inline">{step.n}</span>
                <span className="sm:hidden">{step.short}</span>
                <span className="hidden sm:inline">{step.landmark}</span>
                <span className="hidden text-faint md:inline"> · {step.feet}</span>
              </span>
            </span>
          );
        })}
      </div>

      {/* the floor, with the possession on it */}
      <div className="absolute inset-x-0 bottom-0 top-7">
        <CourtLines />
        {/* the possession starts at the half-court line */}
        <span className="absolute left-0 top-1/2 flex -translate-x-1/2 -translate-y-1/2 rounded-full bg-panel p-0.5 text-muted">
          <BallGlyph size={13} />
        </span>
        {STEPS.map((step, i) => (
          <Segment
            key={`seg-${step.n}`}
            from={i === 0 ? 0.022 : STEPS[i - 1].at}
            to={step.at}
            state={statuses[i].state}
          />
        ))}
        {STEPS.map((step, i) => {
          const st = statuses[i];
          return (
            <span key={step.n} className="absolute inset-y-0" style={{ left: `${step.at * 100}%` }}>
              {/* leader into the card below — only where the cards sit under it */}
              <span
                aria-hidden
                className="absolute left-0 top-1/2 hidden h-[calc(50%+16px)] w-px bg-gradient-to-b from-line to-line/0 lg:block"
              />
              <span
                aria-hidden
                className="absolute left-0 top-1/2 h-[13px] w-[13px] -translate-x-1/2 -translate-y-1/2 rounded-full border-[1.5px]"
                style={{
                  borderColor: TONE[st.state],
                  background: stationFill(st.state),
                  boxShadow: "0 0 0 4px var(--arena)",
                }}
              />
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- the section */

function StepReading({ status }: { status: StepStatus }) {
  if (status.state === "checking") {
    return (
      <span className="block" role="status" aria-label="Reading what is loaded">
        <span aria-hidden className="skeleton block h-7 w-20" />
        <span aria-hidden className="skeleton mt-2 block h-3 w-32 max-w-full" />
      </span>
    );
  }
  if (status.figure === undefined) {
    return (
      <span className="block text-[12.5px] leading-snug text-foreground">
        <CommandLine text={status.reading} />
      </span>
    );
  }
  const missing = status.figure === "—";
  return (
    <span className="block">
      <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className={clsx("stat-serif text-[2.15rem] leading-none", missing ? "text-faint" : "text-foreground")}>
          {status.figure}
        </span>
        {status.unit && <span className="text-[12.5px] text-muted">{status.unit}</span>}
      </span>
      {status.detail && (
        <span className="mt-1.5 block text-[12px] leading-snug text-faint">{status.detail}</span>
      )}
    </span>
  );
}

export function DecisionWorkflow({
  statuses,
  hrefs,
}: {
  /** Derived statuses, in step order. */
  statuses: [StepStatus, StepStatus, StepStatus, StepStatus];
  /** Where each step opens — favourite-team aware. */
  hrefs: [string, string, string, string];
}) {
  const ready = statuses.filter((s) => s.state === "ready").length;
  const settled = statuses.every((s) => s.state !== "checking");

  return (
    <div className="panel">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4">
        <span className="eyebrow flex items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 self-center rounded-full bg-signal" />
          Possession · half court to rim
        </span>
        <span className="text-[12.5px] text-muted">
          {settled ? (
            <>
              <span className="stat-serif mr-1 text-[1.3rem] leading-none text-foreground">{ready}</span>
              of 4 steps have the data they need
            </>
          ) : (
            "Reading what is loaded…"
          )}
        </span>
      </div>

      <div className="mt-3 px-5">
        <PossessionCourt statuses={statuses} />
      </div>

      <ol className="mt-4 grid gap-3 px-5 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((step, i) => {
          const st = statuses[i];
          return (
            <li key={step.n} className="min-w-0">
              <Link
                href={hrefs[i]}
                className="group relative flex h-full flex-col rounded-lg border border-hairline bg-panel2/45 p-4 transition-[background-color,border-color] duration-200 hover:border-line hover:bg-panel2 focus-visible:outline-offset-[-3px]!"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-[12px] text-muted">
                    <span className="numeral text-foreground">{step.n}</span>
                    <span aria-hidden className="mx-1.5 text-faint">
                      ·
                    </span>
                    {step.verb}
                  </span>
                  <Badge status={BADGE[st.state]}>{st.word}</Badge>
                </span>
                <h3 className="title-md mt-3 text-foreground transition-colors group-hover:text-signal">
                  {step.title}
                </h3>
                <span className="mt-3 block border-t border-hairline pt-3">
                  <StepReading status={st} />
                </span>
                <span className="mt-3 block flex-1 text-[12.5px] leading-relaxed text-muted">{step.blurb}</span>
                <span className="mt-4 flex items-center gap-1.5 text-[13px] font-medium text-signal">
                  {step.module}
                  <IconArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
                </span>
              </Link>
            </li>
          );
        })}
      </ol>

      <div className="mt-4 border-t border-hairline px-5 py-3">
        <Explainer summary="Why it is drawn as a possession">
          <p>
            The four steps run in the order of a half-court possession: survey the floor from half
            court, read the defence at the arc, attack the lane, finish at the rim. Each station
            sits at its real distance on a 47-foot half court — 6, 18, 28 and 41.75 feet from the
            half-court line — which is also where the four columns fall.
          </p>
          <p className="mt-1.5">
            A filled station on a lit mint line means that step has what it depends on. A
            half-filled amber station means it runs with something missing; an open amber ring
            means it is waiting on you — the Decision Board needs two saved deals (the badge says
            which); an open grey ring means what it depends on did not load or is not there.
            Nothing here is a score — it is what is loaded right now.
          </p>
        </Explainer>
      </div>
    </div>
  );
}

/* --------------------------------------------------------- supporting rows */

export interface SupportItem {
  title: string;
  href: string;
  reading: string;
  badge?: { status: string; word: string } | null;
}

/** Each supporting module keeps the icon it has in the navigation. */
const SUPPORT_ICON: Record<string, (p: { size?: number; className?: string }) => React.ReactNode> = {
  "/salary-cap-center": IconCap,
  "/methodology": IconMethod,
  "/data-health": IconHealth,
};

export function SupportingModules({ items }: { items: SupportItem[] }) {
  return (
    <ul className="grid gap-3 md:grid-cols-3">
      {items.map((item) => {
        const Icon = SUPPORT_ICON[item.href];
        return (
          <li key={item.title} className="min-w-0">
            <Link
              href={item.href}
              className="panel hover-frame group flex h-full items-start gap-3.5 p-4"
            >
              {Icon && (
                // Three columns are too narrow for an icon beside the text at tablet width.
                <span
                  aria-hidden
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-hairline bg-panel2 text-muted transition-colors group-hover:text-signal md:hidden lg:flex"
                >
                  <Icon size={17} />
                </span>
              )}
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <span className="title-sm text-foreground transition-colors group-hover:text-signal">
                    {item.title}
                  </span>
                  {item.badge && <Badge status={item.badge.status}>{item.badge.word}</Badge>}
                </span>
                <span className="mt-1 block text-[12.5px] leading-snug text-muted">{item.reading}</span>
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------ not built yet */

const ROADMAP = [
  { title: "Player Intelligence", need: "Skill grades beyond the box score need tracking or matchup data" },
  { title: "Scenario Engine", need: "Signings, waivers and departures on the same before/after machinery as trades" },
  { title: "Pivot AI", need: "Planned to call the engines and quote them — never to invent an answer" },
];

export function NotBuilt() {
  return (
    <section
      aria-labelledby="not-built-heading"
      className="rounded-xl border border-dashed border-line px-5 py-4"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 id="not-built-heading" className="title-sm text-foreground">
          Not built yet
        </h3>
        <p className="text-[12.5px] text-muted">
          Named rather than hinted at — Pivot won&apos;t ship a model it can&apos;t validate, or a page
          that promises one.
        </p>
      </div>
      <ul className="mt-3.5 grid gap-x-8 gap-y-3 md:grid-cols-3">
        {ROADMAP.map((item) => (
          <li key={item.title} className="flex min-w-0 gap-2.5">
            <span aria-hidden className="mt-px text-[13px] leading-5 text-unavail">
              —
            </span>
            <span className="min-w-0">
              <span className="block text-[13px] font-medium leading-5 text-foreground">{item.title}</span>
              <span className="mt-0.5 block text-[12px] leading-snug text-muted">{item.need}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
