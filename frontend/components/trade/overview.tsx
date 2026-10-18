"use client";

/**
 * The evaluated deal at a glance — four compact panels that sit above the detailed
 * consequence sheet:
 *
 *   Projected Impact    each side's projected wins change, with the simulation's range
 *   Team Comparison     measured team ratings, side by side, with the gap coloured
 *   Player Comparison   the lead player each side receives, season per-game lines
 *   AI Insights         plain-language reads generated from this evaluation's served numbers
 *
 * Nothing is modelled here. Every figure is a value the backend served (the evaluation's
 * outcome distribution, the team's measured season ratings, a player's per-game line); where
 * the backend did not serve one, the panel says so instead of drawing it. Green and red mean
 * favourable and unfavourable *for the team being read* — a lower defensive rating is
 * favourable, a negative wins change is not.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { api } from "@/lib/api";
import { COMPONENT_EXPLAIN, COMPONENT_LABEL, height, money } from "@/lib/format";
import { assessSide } from "@/lib/assessment";
import type { RosterPlayer, TeamEvaluation } from "@/lib/types";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { SegmentedControl } from "@/components/ui";
import { useTeamDetail, type SheetSide } from "@/components/trade/sheet";
import { hasOutcomeBand, signedWins } from "@/components/trade/verdict";

const CARD = "panel @container min-w-0";
const CARD_HEAD = "flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 pb-1 pt-4";

/** Favourable / unfavourable / neutral — the one place a sign becomes a colour. */
function tone(delta: number, epsilon = 0.05): "good" | "bad" | "flat" {
  return delta > epsilon ? "good" : delta < -epsilon ? "bad" : "flat";
}
const TONE_TEXT = { good: "text-legal", bad: "text-illegal", flat: "text-foreground" } as const;

/* ------------------------------------------------------------ projected impact */

export function ProjectedImpact({
  sides,
  isRefused,
}: {
  sides: SheetSide[];
  isRefused: (evaluation: TeamEvaluation) => boolean;
}) {
  return (
    <section aria-labelledby="projected-impact-title" className={CARD}>
      <header className={CARD_HEAD}>
        <div className="min-w-0">
          <h3 id="projected-impact-title" className="text-[1.15rem] font-semibold leading-tight text-foreground">
            Projected Impact
          </h3>
          <p className="mt-1 text-[13px] text-muted">
            Estimated change in wins over a full season if the trade is completed.
          </p>
        </div>
      </header>
      <div
        className={clsx(
          "grid gap-px px-5 pb-5 pt-4",
          sides.length === 3 ? "md:grid-cols-3" : "md:grid-cols-2",
        )}
      >
        {sides.map((side, i) => (
          <ImpactSide
            key={side.teamId}
            side={side}
            refused={side.evaluation ? isRefused(side.evaluation) : false}
            divider={i > 0}
          />
        ))}
      </div>
    </section>
  );
}

function ImpactSide({
  side,
  refused,
  divider,
}: {
  side: SheetSide;
  refused: boolean;
  divider: boolean;
}) {
  const { data: detail } = useTeamDetail(side.teamId);
  const standing = detail?.standing;
  const u = side.evaluation?.uncertainty;
  const band = Boolean(side.evaluation && !refused && hasOutcomeBand(u));
  const median = band && u ? u.median : null;
  const t = median === null ? "flat" : tone(median);

  return (
    <div
      className={clsx(
        "flex min-w-0 items-center gap-4 py-2",
        divider && "md:border-l md:border-hairline md:pl-6",
        divider && "border-t border-hairline pt-5 md:border-t-0 md:pt-2",
      )}
    >
      <TeamLogo abbreviation={side.abbreviation} name={side.name} size={72} className="shrink-0" />
      <div className="min-w-0">
        <p className="truncate text-[14px] font-semibold text-foreground">{side.name}</p>
        <p
          className={clsx(
            "stat-serif mt-0.5 text-[2.6rem] leading-none",
            median === null ? "text-faint" : TONE_TEXT[t],
          )}
          aria-label={median === null ? "Not projected" : `${signedWins(median)} projected wins`}
        >
          {median === null ? "—" : signedWins(median)}
        </p>
        <p className="mt-1.5 text-[12.5px] text-muted">Projected Wins Added</p>
        <p className="mt-1 text-[12.5px] text-muted">
          {band && u ? (
            <>
              <span className="numeral text-foreground">
                {signedWins(u.p10)} to {signedWins(u.p90)}
              </span>{" "}
              range · {((u.prob_positive ?? 0) * 100).toFixed(0)}% chance it helps
            </>
          ) : refused ? (
            "Not projected — the deal is refused."
          ) : (
            (u?.unavailable ?? "No outcome distribution.")
          )}
        </p>
        {standing && (
          <p className="mt-0.5 text-[12px] text-faint">
            {/* R8.2: the record belongs to the statistics season, not the roster being shown. */}
            {detail?.season ?? "Latest"} record <span className="numeral text-muted">{standing.wins}–{standing.losses}</span>
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ team comparison */

interface Metric {
  key: string;
  group: "advanced" | "base";
  label: string;
  /** Lower is the favourable direction (defensive rating). */
  lowerBetter?: boolean;
  format: (v: number) => string;
}

const f1 = (v: number) => v.toFixed(1);
const pctText = (v: number) => `${(v * 100).toFixed(1)}`;

const METRICS: Metric[] = [
  { key: "OFF_RATING", group: "advanced", label: "Offensive Rating", format: f1 },
  { key: "DEF_RATING", group: "advanced", label: "Defensive Rating", lowerBetter: true, format: f1 },
  { key: "NET_RATING", group: "advanced", label: "Net Rating", format: f1 },
  { key: "REB_PCT", group: "advanced", label: "Rebound %", format: pctText },
  { key: "AST_PCT", group: "advanced", label: "Assist %", format: pctText },
  { key: "FG3_PCT", group: "base", label: "3PT %", format: pctText },
];

export function TeamComparison({ sides, activeId }: { sides: SheetSide[]; activeId: string }) {
  const a = sides.find((s) => s.teamId === activeId) ?? sides[0];
  const others = sides.filter((s) => s.teamId !== a.teamId);
  const [otherId, setOtherId] = useState(others[0]?.teamId);
  const b = others.find((s) => s.teamId === otherId) ?? others[0];
  const { data: da, isPending: pa } = useTeamDetail(a.teamId);
  const { data: db, isPending: pb } = useTeamDetail(b?.teamId ?? a.teamId);

  const value = (d: typeof da, m: Metric) => {
    const v = d?.stats?.[m.group]?.[m.key];
    return typeof v === "number" ? v : null;
  };
  const anyData = METRICS.some((m) => value(da, m) !== null && value(db, m) !== null);

  return (
    <section aria-labelledby="team-comparison-title" className={CARD}>
      <header className={CARD_HEAD}>
        <div className="min-w-0">
          <h3 id="team-comparison-title" className="text-[1.15rem] font-semibold leading-tight text-foreground">
            Team Comparison
          </h3>
          <p className="mt-1 text-[13px] text-muted">
            Measured {da?.season ?? "season"} ratings, per 100 possessions.
          </p>
        </div>
        {others.length > 1 && (
          <SegmentedControl
            ariaLabel="Compare against"
            value={b?.teamId ?? ""}
            onChange={setOtherId}
            options={others.map((s) => ({ value: s.teamId, label: s.abbreviation }))}
          />
        )}
      </header>
      <div className="px-5 pb-5 pt-3">
        {!b ? null : pa || pb ? (
          <div className="space-y-3" aria-hidden>
            {METRICS.map((m) => (
              <div key={m.key} className="skeleton h-5" />
            ))}
          </div>
        ) : !anyData ? (
          <p className="rounded-md border border-dashed border-line px-4 py-6 text-center text-[13px] text-muted">
            Team ratings are not available for these teams, so no comparison is drawn.
          </p>
        ) : (
          <>
            <div className="grid grid-cols-[minmax(0,8.5rem)_3.25rem_minmax(0,1fr)_3.25rem] items-center gap-x-3 gap-y-2.5 text-[13px]">
              <span />
              <span className="numeral text-right text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
                {a.abbreviation}
              </span>
              <span />
              <span className="numeral text-[11px] font-medium uppercase tracking-[0.08em] text-muted">
                {b.abbreviation}
              </span>
              {METRICS.map((m) => (
                <MetricRow key={m.key} metric={m} a={value(da, m)} b={value(db, m)} />
              ))}
            </div>
            <p className="mt-3.5 border-t border-hairline pt-2.5 text-[11.5px] leading-snug text-faint">
              Bar length places each team against the other, not from zero. Green = better for{" "}
              {a.abbreviation}, red = worse. Defensive rating is better when lower.
            </p>
          </>
        )}
      </div>
    </section>
  );
}

function MetricRow({ metric, a, b }: { metric: Metric; a: number | null; b: number | null }) {
  if (a === null || b === null) {
    return (
      <>
        <span className="text-muted">{metric.label}</span>
        <span className="numeral text-right text-foreground">{a === null ? "—" : metric.format(a)}</span>
        <span className="text-[12px] text-faint">not available</span>
        <span className="numeral text-foreground">{b === null ? "—" : metric.format(b)}</span>
      </>
    );
  }
  // Position both values on a track padded either side of the pair, so the gap is legible.
  const gap = Math.abs(a - b);
  const pad = Math.max(gap * 1.5, Math.max(Math.abs(a), Math.abs(b), 1) * 0.04);
  const lo = Math.min(a, b) - pad;
  const hi = Math.max(a, b) + pad;
  const at = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const better = metric.lowerBetter ? a < b : a > b;
  const even = gap < 1e-9;
  const base = at(Math.min(a, b));
  const top = at(Math.max(a, b));
  return (
    <>
      <span className="text-muted">{metric.label}</span>
      <span className="numeral text-right font-medium text-foreground">{metric.format(a)}</span>
      <span
        role="img"
        aria-label={`${metric.label}: ${metric.format(a)} against ${metric.format(b)}${even ? ", level" : better ? ", favourable" : ", unfavourable"}`}
        className="relative block h-2 overflow-hidden rounded-[2px] bg-panel3"
      >
        <span className="absolute inset-y-0 left-0 bg-foreground" style={{ width: `${base}%` }} />
        {!even && (
          <span
            className={clsx("absolute inset-y-0", better ? "bg-legal" : "bg-illegal")}
            style={{ left: `${base}%`, width: `${Math.max(1.5, top - base)}%` }}
          />
        )}
      </span>
      <span className="numeral font-medium text-foreground">{metric.format(b)}</span>
    </>
  );
}

/* ----------------------------------------------------------- player comparison */

interface PlayerLine {
  GP?: number;
  MIN?: number;
  PTS?: number;
  REB?: number;
  AST?: number;
  FG_PCT?: number;
  FG3_PCT?: number;
}

function useSeasonStats(teamId: string | undefined) {
  return useQuery({
    queryKey: ["team-season-stats", teamId],
    queryFn: () => api.get<{ season: string; stats: Record<string, PlayerLine> }>(`/teams/${teamId}/season-stats`),
    enabled: Boolean(teamId),
    staleTime: 300_000,
  });
}

/** The lead player a side receives: its highest-impact incoming player. */
function leadIncoming(side: SheetSide) {
  const players = side.receives.filter((r) => r.kind === "player" && r.playerId);
  return [...players].sort((x, y) => (y.tei ?? -99) - (x.tei ?? -99))[0] ?? null;
}

const LINE_FIELDS: { key: keyof PlayerLine; label: string; pct?: boolean }[] = [
  { key: "PTS", label: "PPG" },
  { key: "REB", label: "RPG" },
  { key: "AST", label: "APG" },
  { key: "FG_PCT", label: "FG%", pct: true },
  { key: "FG3_PCT", label: "3PT%", pct: true },
];

export function PlayerComparison({
  sides,
  activeId,
  playerIndex,
  originOf,
}: {
  sides: SheetSide[];
  activeId: string;
  playerIndex: Record<string, RosterPlayer>;
  /** Player id → the team id he currently plays for. */
  originOf: Record<string, string>;
}) {
  const a = sides.find((s) => s.teamId === activeId) ?? sides[0];
  const others = sides.filter((s) => s.teamId !== a.teamId);
  const [otherId, setOtherId] = useState(others[0]?.teamId);
  const b = others.find((s) => s.teamId === otherId) ?? others[0];

  const leadA = leadIncoming(a);
  const leadB = b ? leadIncoming(b) : null;
  const rowA = leadA?.playerId ? playerIndex[leadA.playerId] : undefined;
  const rowB = leadB?.playerId ? playerIndex[leadB.playerId] : undefined;
  const statsA = useSeasonStats(leadA?.playerId ? originOf[leadA.playerId] : undefined);
  const statsB = useSeasonStats(leadB?.playerId ? originOf[leadB.playerId] : undefined);
  const lineA = leadA?.playerId ? statsA.data?.stats[leadA.playerId] : undefined;
  const lineB = leadB?.playerId ? statsB.data?.stats[leadB.playerId] : undefined;

  return (
    <section aria-labelledby="player-comparison-title" className={CARD}>
      <header className={CARD_HEAD}>
        <div className="min-w-0">
          <h3 id="player-comparison-title" className="text-[1.15rem] font-semibold leading-tight text-foreground">
            Player Comparison
          </h3>
          <p className="mt-1 text-[13px] text-muted">
            The highest-impact player each side receives · {statsA.data?.season ?? "season"} per game.
          </p>
        </div>
        {others.length > 1 && (
          <SegmentedControl
            ariaLabel="Compare against"
            value={b?.teamId ?? ""}
            onChange={setOtherId}
            options={others.map((s) => ({ value: s.teamId, label: s.abbreviation }))}
          />
        )}
      </header>
      <div className="px-5 pb-5 pt-3">
        {!leadA || !leadB || !rowA || !rowB ? (
          <p className="rounded-md border border-dashed border-line px-4 py-6 text-center text-[13px] text-muted">
            Each side needs to receive a player before two can be compared.
          </p>
        ) : (
          <div className="grid items-start gap-4 @2xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] @2xl:gap-5">
            <PlayerCard row={rowA} line={lineA} other={lineB} loading={statsA.isPending} toTeam={a.abbreviation} />
            <span
              aria-hidden
              className="flex items-center gap-3 text-[11px] font-semibold tracking-[0.1em] text-faint @2xl:self-center"
            >
              <span className="h-px flex-1 bg-hairline @2xl:hidden" />
              VS
              <span className="h-px flex-1 bg-hairline @2xl:hidden" />
            </span>
            <PlayerCard row={rowB} line={lineB} other={lineA} loading={statsB.isPending} toTeam={b.abbreviation} />
          </div>
        )}
      </div>
    </section>
  );
}

function PlayerCard({
  row,
  line,
  other,
  loading,
  toTeam,
}: {
  row: RosterPlayer;
  line: PlayerLine | undefined;
  other: PlayerLine | undefined;
  loading: boolean;
  toTeam: string;
}) {
  return (
    <div className="flex min-w-0 gap-4">
      <PlayerPhoto nbaPlayerId={row.nba_player_id} name={row.name} size={84} square className="shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-semibold text-foreground">{row.name}</p>
        <p className="mt-0.5 truncate text-[12.5px] text-muted">
          {row.position ?? "—"}
          {row.height_inches ? ` · ${height(row.height_inches)}` : ""}
          {row.age ? ` · ${row.age.toFixed(0)}` : ""} · to {toTeam}
        </p>
        {loading ? (
          <div className="skeleton mt-3 h-12" aria-hidden />
        ) : !line ? (
          <p className="mt-3 text-[12.5px] text-faint">No season line on file.</p>
        ) : (
          <dl className="mt-3 grid grid-cols-5 gap-1">
            {LINE_FIELDS.map((f) => {
              const v = line[f.key];
              const o = other?.[f.key];
              const lead = typeof v === "number" && typeof o === "number" && v > o;
              return (
                <div
                  key={f.key}
                  className="min-w-0 rounded-[4px] border border-hairline bg-panel2 px-1 py-1.5 text-center"
                >
                  <dd
                    className={clsx(
                      "numeral text-[13.5px] leading-none",
                      lead ? "font-semibold text-foreground" : "font-normal text-muted",
                    )}
                  >
                    {typeof v !== "number" ? "—" : f.pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(1)}
                  </dd>
                  <dt className="mt-1 text-[10px] uppercase tracking-[0.06em] text-faint">{f.label}</dt>
                </div>
              );
            })}
          </dl>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- insights */

/**
 * Plain-language reads, built only from what the evaluation served: the verdict, the
 * projected wins and its range, the strongest and weakest score drivers (with the model's
 * own description of each), cap movement where contracts exist, and what is missing.
 */
export function buildInsights(side: SheetSide): string[] {
  const ev = side.evaluation;
  if (!ev) return [];
  const out: string[] = [];
  const u = ev.uncertainty;
  const refused = ev.decision_status === "suppressed_illegal";

  if (refused) {
    const codes = (ev.suppression?.failing_rules ?? []).map((r) => r.rule_code);
    out.push(
      `${side.abbreviation}'s side fails a verified trade rule${codes.length ? ` (${[...new Set(codes)].join(", ")})` : ""}, so the deal is not scored.`,
    );
    return out;
  }
  if (ev.composite_utility === null || ev.composite_utility === undefined) {
    out.push("There was not enough data to score this deal for this team.");
    return out;
  }

  const assessment = assessSide(ev, side.name);
  out.push(
    hasOutcomeBand(u)
      ? `${assessment.headline}: ${signedWins(u.median)} projected wins (range ${signedWins(u.p10)} to ${signedWins(u.p90)}, ${((u.prob_positive ?? 0) * 100).toFixed(0)}% model-reported chance it helps). The model's category is "${assessment.category}".`
      : `${assessment.headline}; the simulation produced no outcome distribution, so no wins are projected.`,
  );
  if (assessment.limitations.length > 0)
    out.push(`Not established by this score: ${assessment.limitations.map((l) => l.replace(/\.$/, "")).join("; ")}.`);
  if (assessment.tone === "favorable" || assessment.tone === "neutral")
    out.push("A favorable basketball estimate does not show the deal is legal, financially sound or feasible.");
  // The headline already carries the main driver; add the strongest driver pulling the other way.
  const drivers = [...(ev.drivers ?? [])].sort((x, y) => y.contribution - x.contribution);
  const counter =
    assessment.tone === "unfavorable"
      ? drivers.find((d) => d.contribution > 0.5)
      : [...drivers].reverse().find((d) => d.contribution < -0.5);
  if (counter)
    out.push(
      `${COMPONENT_LABEL[counter.component] ?? counter.component} pulls the other way (${counter.contribution > 0 ? "+" : ""}${counter.contribution.toFixed(1)}): ${lower(COMPONENT_EXPLAIN[counter.component])}`,
    );

  const lg = ev.legality;
  if (lg.payroll_after !== null && lg.payroll_before !== null) {
    const delta = lg.payroll_after - lg.payroll_before;
    out.push(
      `Payroll ${delta >= 0 ? "rises" : "falls"} by ${money(Math.abs(delta))}${lg.apron_status_after ? `, leaving ${side.abbreviation} ${lg.apron_status_after}` : ""}.`,
    );
  }

  return out;
}

function lower(text: string | undefined): string {
  if (!text) return "";
  return text.charAt(0).toLowerCase() + text.slice(1);
}

export function Insights({
  sides,
  activeId,
  onSelect,
}: {
  sides: SheetSide[];
  activeId: string;
  onSelect: (teamId: string) => void;
}) {
  const side = sides.find((s) => s.teamId === activeId) ?? sides[0];
  const items = buildInsights(side);
  return (
    <section aria-labelledby="insights-title" className={CARD}>
      <header className={CARD_HEAD}>
        <div className="min-w-0">
          <h3 id="insights-title" className="flex items-center gap-2.5 text-[1.15rem] font-semibold leading-tight text-foreground">
            AI Insights
            <span className="rounded-full border border-line px-2 py-px text-[10px] font-medium uppercase tracking-[0.08em] text-muted">
              From this evaluation
            </span>
          </h3>
          <p className="mt-1 text-[13px] text-muted">Read for {side.name}.</p>
        </div>
        {sides.length > 1 && (
          <SegmentedControl
            ariaLabel="Read the deal for"
            value={side.teamId}
            onChange={onSelect}
            options={sides.map((s) => ({ value: s.teamId, label: s.abbreviation }))}
          />
        )}
      </header>
      <div className="px-5 pb-5 pt-3">
        {items.length === 0 ? (
          <p className="text-[13px] text-muted">No evaluation exists for this team on this deal.</p>
        ) : (
          <ol className="space-y-3">
            {items.map((text, i) => (
              <li key={i} className="grid grid-cols-[1.5rem_minmax(0,1fr)] items-start gap-3 text-[14.5px] leading-[1.5] text-foreground">
                <span className="numeral grid h-6 w-6 place-items-center rounded-full border border-line text-[12px] text-muted">
                  {i + 1}
                </span>
                <span className="pt-0.5">{text}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
