"use client";

/**
 * Step two and three of the decision board: the leader, its challengers, the priorities
 * that rank them, every deal's projected-wins band on one axis, and the component matrix
 * that shows where each deal wins.
 *
 * The hierarchy is the point. The leader is answered first — who, by how much, why, and
 * how robust — and every challenger is read against it. Nothing here re-runs a model: the
 * live ranking re-weights the stored component scores, and the saved sensitivity analysis
 * is always labelled as separate from the sliders.
 */

import clsx from "clsx";
import Link from "next/link";
import {
  COMPONENT_LABEL,
  LEGALITY_EXPLAIN,
  LEGALITY_LABEL,
  LEGALITY_SHORT,
  VERDICT_LABEL,
  VERDICT_STATUS,
  fanVerdict,
  pct,
} from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import { IconArrowRight } from "@/components/icons";
import { Badge, Button, ButtonLink, Panel, SourceRail, UnavailableNotice } from "@/components/ui";
import {
  COMPONENT_KEYS,
  ENGINE_SOURCE,
  bestByComponent,
  distributionOf,
  edgesOver,
  isAvailable,
  missingComponents,
  signed,
  unrankedReason,
  weightShares,
  type BoardAlternative,
  type Domination,
  type LeaderExplanation,
  type Ranked,
} from "./model";
import {
  COMPONENT_NOTE,
  ComponentProfile,
  DeviationBar,
  Dot,
  MovesBlock,
  RankMark,
  WinsAxisLabels,
  WinsBand,
  noDistributionReason,
  type PlayerLookup,
} from "./parts";

/* ------------------------------------------------------------ helpers */

function axesPhrase(domination: Domination | null | undefined): string {
  if (!domination) return "every component both deals could be scored on";
  const names = domination.axes.map((a) => COMPONENT_LABEL[a] ?? a).join(", ");
  return `the ${domination.axes_compared} of ${domination.axes_total} components both deals could be scored on (${names})`;
}

function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** One instrument in the leader's reading row: quiet label, serif figure, the context under it. */
function Reading({
  label,
  value,
  children,
  className,
}: {
  label: string;
  value: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("min-w-0 bg-panel px-4 py-4 sm:px-5", className)}>
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-2">
        <div className="stat-serif text-[2rem] leading-none text-foreground">{value}</div>
        {children}
      </dd>
    </div>
  );
}

/* ------------------------------------------------------------- leader */

export function LeaderPanel({
  alt,
  score,
  runnerUp,
  explanation,
  axis,
  sideAbbr,
  firstPlaceShare,
  nSamples,
  lookup,
  retrievedAt,
}: {
  alt: BoardAlternative;
  score: number;
  runnerUp: { alt: BoardAlternative; score: number } | null;
  explanation: LeaderExplanation | null;
  axis: number;
  sideAbbr: string | null;
  firstPlaceShare: number | undefined;
  nSamples: number | undefined;
  lookup: PlayerLookup;
  retrievedAt: string | null;
}) {
  const missing = missingComponents(alt);
  // C13: the backend's confidence is the only one — the board never synthesizes its own.
  const verdict = fanVerdict(score, alt.confidence);
  const d = distributionOf(alt.uncertainty);
  const identity = teamIdentity(sideAbbr);
  const unmodeled = alt.has_unmodeled_players ? (alt.unmodeled_players ?? []) : [];

  return (
    <Panel accent="var(--signal)" padded={false} className="@container overflow-hidden">
      {/* ---- the verdict: who leads, by how much */}
      <div
        className="border-b border-hairline px-4 pb-5 pt-5 sm:px-6 sm:pt-6"
        style={
          sideAbbr
            ? { background: `linear-gradient(110deg, ${identity.primary}2e, transparent 58%)` }
            : undefined
        }
      >
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
          <div className="min-w-0 flex-[1_1_20rem]">
            <div className="eyebrow flex flex-wrap items-center gap-x-2 gap-y-1">
              <Dot />
              <span className="text-signal">Leads under your priorities</span>
              {sideAbbr && <span className="text-faint">· scored for {sideAbbr}</span>}
            </div>
            <span className="sr-only">Ranked first under your current weights.</span>
            {/* A step below the section h2 (title-lg) in rank, though set at the same size: it
                is the answer the whole page exists to give. */}
            <h3 className="mt-3 text-balance break-words font-serif text-[clamp(2rem,1.45rem+1.7vw,2.9rem)] leading-[1.02] tracking-[-0.012em] text-foreground">
              {alt.name}
            </h3>
            <p className="mt-2.5 max-w-xl text-[14px] leading-snug text-muted">
              {runnerUp ? (
                <>
                  Ahead of <span className="text-foreground">{runnerUp.alt.name}</span> by{" "}
                  <span className="numeral text-foreground">{(score - runnerUp.score).toFixed(1)}</span>{" "}
                  decision-score points.
                </>
              ) : (
                "The only deal on the board that can be ranked under these weights."
              )}
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-1.5">
              <Badge status={VERDICT_STATUS[verdict]}>{VERDICT_LABEL[verdict]}</Badge>
              <Badge status={alt.legality_status}>{LEGALITY_LABEL[alt.legality_status]}</Badge>
              {alt.dominated_by ? (
                <Badge status="unavailable">dominated</Badge>
              ) : (
                <Badge status="pass">on the frontier</Badge>
              )}
              <span className="pl-1 text-[12px] text-faint">confidence {alt.confidence}</span>
            </div>
          </div>
          {/* Left-aligned under the name on a phone, right-aligned beside it once there is room. */}
          <div className="min-w-0 sm:text-right">
            <div className="eyebrow">Decision score</div>
            <div className="mt-1 flex items-baseline gap-1 sm:justify-end">
              <span className="stat-serif text-[clamp(3.4rem,2.6rem+2.4vw,4.6rem)] leading-[0.9] text-signal">
                {score.toFixed(1)}
              </span>
              <span className="text-[13px] text-faint">/100</span>
            </div>
            <div className="mt-2 text-[12px] text-muted">0–100 · 50 is neutral · live</div>
          </div>
        </div>
      </div>

      {/* ---- how good, and how sure */}
      <dl className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline @min-[46rem]:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <Reading
          label="Projected wins"
          className="col-span-2 @min-[46rem]:col-span-1"
          value={
            <span className="flex items-baseline gap-1.5">
              {d ? signed(d.median) : "—"}
              {d && <span className="font-sans text-[12px] text-muted">wins · median</span>}
            </span>
          }
        >
          <WinsBand alt={alt} axis={axis} lead className="mt-3" />
          {d && <WinsAxisLabels axis={axis} className="mt-1" />}
          <p className="mt-1.5 text-[12px] leading-snug text-muted">
            {d ? (
              <>
                <span className="numeral font-normal text-foreground">
                  {signed(d.p10)} to {signed(d.p90)}
                </span>{" "}
                · 10th–90th percentile · full 82-game season
              </>
            ) : (
              noDistributionReason(alt)
            )}
          </p>
        </Reading>
        <Reading label="Chance it helps" value={d ? pct(d.prob_positive) : "—"}>
          <p className="mt-2 text-[12px] leading-snug text-muted">
            {d && d.n_draws > 0
              ? `of ${d.n_draws.toLocaleString()} simulated seasons`
              : "no outcome distribution"}
          </p>
        </Reading>
        <Reading label="Ranks first" value={firstPlaceShare !== undefined ? pct(firstPlaceShare) : "—"}>
          <p className="mt-2 text-[12px] leading-snug text-muted">
            {nSamples ? `of ${nSamples.toLocaleString()} sampled weightings` : "of the sampled weightings"}
            <span className="block text-faint">saved analysis, not your sliders</span>
          </p>
        </Reading>
      </dl>

      {/* ---- why */}
      <div className="grid gap-x-8 gap-y-6 border-b border-hairline px-4 py-5 sm:px-6 @min-[46rem]:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <h4 className="eyebrow mb-3">Why it leads</h4>
          {explanation ? (
            <ul className="space-y-3 text-[13.5px] leading-relaxed">
              <WhyItem>
                <span className="font-semibold text-foreground">{alt.name}</span>{" "}
                {explanation.strengths.length > 0 ? (
                  <>
                    leads because it scores best on{" "}
                    <span className="font-medium text-foreground">
                      {joinWords(explanation.strengths.slice(0, 2).map((s) => COMPONENT_LABEL[s.key]))}
                    </span>
                    .
                  </>
                ) : (
                  <>
                    leads on the weighted blend — it is not the outright best on any single dimension,
                    but its overall balance comes out on top.
                  </>
                )}
              </WhyItem>
              <WhyItem>
                {explanation.flips.length > 0 ? (
                  <>
                    It loses the top spot when{" "}
                    {explanation.flips
                      .map((flip) => `${COMPONENT_LABEL[flip.key]} is doubled (${flip.newLeader} takes over)`)
                      .join("; or when ")}
                    .
                  </>
                ) : (
                  <>
                    Doubling any single priority from its current setting does not change the leader —
                    the top spot is stable under these weights.
                  </>
                )}
              </WhyItem>
              {explanation.dominance.map((entry, index) => (
                <WhyItem key={`${entry.name}-${index}`} muted>
                  {entry.name} is Pareto-dominated by {entry.by}: no better on any of{" "}
                  {axesPhrase(entry.domination)}, and worse on at least one.
                </WhyItem>
              ))}
            </ul>
          ) : (
            <p className="text-[13.5px] leading-relaxed text-muted">
              It is the only deal that could be ranked under these weights, so there is no runner-up to
              explain the lead against — it is first by default, not by margin.
            </p>
          )}
          {missing.length > 0 && (
            <p className="mt-4 flex gap-2.5 rounded-lg border border-dashed border-unavail/50 bg-unavail/6 px-3.5 py-2.5 text-[12.5px] leading-snug text-muted">
              <span aria-hidden className="text-unavail">
                —
              </span>
              <span className="min-w-0">
                <span className="font-semibold text-foreground">Caution. </span>
                {missing.map((key) => COMPONENT_LABEL[key]).join(", ")}{" "}
                {missing.length === 1 ? "is" : "are"} unavailable for the leading deal, so this read
                cannot be a full evaluation.
              </span>
            </p>
          )}
          {(alt.membership_warnings ?? []).length > 0 && (
            <p data-testid="membership-warning" className="mt-2.5 text-[12px] leading-snug text-conditional">
              <span className="font-semibold">Unverified roster membership. </span>
              {(alt.membership_warnings ?? []).map((w) => w.name).join(", ")}{" "}
              {(alt.membership_warnings ?? []).length === 1 ? "is" : "are"} on no current roster in
              Pivot&apos;s snapshot, so this deal is scored as if the move were possible — not confirmed.
            </p>
          )}
          {unmodeled.length > 0 && (
            <p className="mt-2.5 text-[12px] leading-snug text-unavail">
              {unmodeled.length === 1 ? "1 player has" : `${unmodeled.length} players have`} no impact
              estimate and {unmodeled.length === 1 ? "was" : "were"} left out of the projection rather
              than given a league-average stand-in: {unmodeled.join(", ")}.
            </p>
          )}
        </div>

        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h4 className="eyebrow">Component profile</h4>
            <span className="text-[11.5px] text-faint">0–100 · 50 neutral · right of centre helps</span>
          </div>
          <ComponentProfile
            components={alt.components}
            className="@min-[34rem]:grid-cols-3 @min-[46rem]:grid-cols-2"
          />
          {missing.length > 0 && (
            <p className="mt-3.5 text-[12px] leading-snug text-unavail">
              Not scored — data unavailable: {missing.map((key) => COMPONENT_LABEL[key]).join(", ")}. The
              remaining weights were renormalized.
            </p>
          )}
        </div>
      </div>

      {/* ---- who moves */}
      <div className="border-b border-hairline px-4 py-5 sm:px-6">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h4 className="eyebrow">Who moves · placed in the league</h4>
          <span className="text-[11.5px] text-faint">league impact percentile</span>
        </div>
        <MovesBlock alt={alt} lookup={lookup} />
        <p className="mt-3 text-[12px] leading-snug text-faint">
          Percentiles place each player among everyone the model scored (Pivot Estimated Impact, a
          unitless index); they are not added up. The projected wins change weighs the same players by
          the minutes they would play.
        </p>
      </div>

      {/* ---- rules and provenance */}
      <div className="px-4 pb-4 pt-4 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="min-w-0 flex-[1_1_16rem] text-[12.5px] leading-snug text-muted">
            {LEGALITY_EXPLAIN[alt.legality_status]}
          </p>
          <ButtonLink href={`/trades/${alt.trade_id}`} size="sm" variant="secondary">
            Open the full report
          </ButtonLink>
        </div>
        <SourceRail source={ENGINE_SOURCE} retrievedAt={retrievedAt} />
      </div>
    </Panel>
  );
}

function WhyItem({ muted = false, children }: { muted?: boolean; children: React.ReactNode }) {
  return (
    <li className={clsx("flex gap-3", muted ? "text-muted" : "text-foreground")}>
      <Dot className="mt-[0.6em]" tone={muted ? "faint" : "signal"} />
      <span className="min-w-0">{children}</span>
    </li>
  );
}

/* --------------------------------------------------------- challengers */

/**
 * The gap to the leader, drawn: the leader sits at the right end, the challenger's dot sits
 * back from it by its deficit on a scale every row shares, so "close" and "far" read down the
 * column without a calculation.
 */
function GapToLeader({ gap, scale }: { gap: number; scale: number }) {
  const back = Math.min(100, (gap / scale) * 100);
  return (
    <div className="relative h-3 w-full" aria-hidden>
      <span className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-panel3" />
      <span
        className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-seriesb/40"
        style={{ left: `${100 - back}%`, right: 0 }}
      />
      <span className="absolute right-0 top-0 h-3 w-[2px] rounded-full bg-signal" />
      <span
        className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-panel bg-seriesb"
        style={{ left: `${100 - back}%` }}
      />
    </div>
  );
}

export function ChallengerList({
  challengers,
  leader,
  leaderScore,
  names,
}: {
  challengers: Ranked;
  leader: BoardAlternative;
  leaderScore: number;
  names: Record<string, string>;
}) {
  if (challengers.length === 0) return null;
  const widest = Math.max(...challengers.map(({ score }) => leaderScore - score));
  // A shared scale for every row: at least 10 points, rounded up to the next 5.
  const scale = Math.max(10, Math.ceil(widest / 5) * 5);
  return (
    <Panel
      title="Challengers"
      subtitle={
        <>
          Ranked under your priorities and read against the leader. The gap is in decision-score
          points, drawn on a shared 0–{scale}-point scale with the leader at the right end; the edges
          compare stored component scores.
        </>
      }
      padded={false}
    >
      <ol className="divide-y divide-hairline">
        {challengers.map(({ alt, score }, index) => {
          const rank = index + 2;
          const verdict = fanVerdict(score, alt.confidence);
          const edges = edgesOver(alt, leader);
          return (
            <li
              key={alt.trade_id}
              className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 gap-y-3 px-4 py-4 sm:grid-cols-[2.25rem_minmax(0,1fr)_12.5rem] sm:gap-x-4 sm:px-5"
            >
              <div>
                <span aria-hidden className="stat-serif block text-[2rem] leading-[0.95] text-faint">
                  {rank}
                </span>
                <span className="sr-only">Rank {rank}.</span>
              </div>
              <div className="min-w-0">
                <h3 className="title-md break-words text-foreground">{alt.name}</h3>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <Badge status={VERDICT_STATUS[verdict]}>{VERDICT_LABEL[verdict]}</Badge>
                  <Badge status={alt.legality_status}>{LEGALITY_SHORT[alt.legality_status]}</Badge>
                  {alt.dominated_by && (
                    <Badge status="unavailable">
                      dominated by {names[alt.dominated_by] ?? "another deal"}
                      {alt.domination ? ` · ${alt.domination.axes_compared}/${alt.domination.axes_total}` : ""}
                    </Badge>
                  )}
                </div>
                <p className="mt-2.5 text-[13px] leading-snug text-muted">
                  {edges.length > 0 ? (
                    <>
                      Beats the leader on{" "}
                      {edges.slice(0, 3).map((edge, i) => (
                        <span key={edge.key}>
                          {i > 0 && (i === Math.min(edges.length, 3) - 1 ? " and " : ", ")}
                          <span className="font-medium text-foreground">{COMPONENT_LABEL[edge.key]}</span>{" "}
                          <span className="numeral text-signal">+{edge.margin.toFixed(0)}</span>
                        </span>
                      ))}
                      .
                    </>
                  ) : (
                    "Not ahead of the leader on any component both were scored on."
                  )}
                </p>
                <p
                  className="mt-1.5 truncate text-[12px] text-faint"
                  title={`in ${alt.incoming.map((p) => p.name).join(", ") || "—"} · out ${alt.outgoing.map((p) => p.name).join(", ") || "—"}`}
                >
                  In {alt.incoming.map((p) => p.name).join(", ") || "—"} · out{" "}
                  {alt.outgoing.map((p) => p.name).join(", ") || "—"}
                </p>
                {(alt.membership_warnings ?? []).length > 0 && (
                  <p data-testid="membership-warning-alt" className="mt-1.5 text-[12px] leading-snug text-conditional">
                    Unverified roster membership: {(alt.membership_warnings ?? []).map((w) => w.name).join(", ")}{" "}— on no
                    current roster in Pivot&apos;s snapshot; scored as if the move were possible.
                  </p>
                )}
                {alt.has_unmodeled_players && (alt.unmodeled_players ?? []).length > 0 && (
                  <p className="mt-1.5 text-[12px] leading-snug text-unavail">
                    No impact estimate for {(alt.unmodeled_players ?? []).join(", ")} — left out of the
                    projection, not given a stand-in.
                  </p>
                )}
              </div>
              <div className="col-span-2 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-x-4 gap-y-2.5 border-t border-hairline pt-3 sm:col-span-1 sm:block sm:border-t-0 sm:pt-0">
                <div className="min-w-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="stat-serif text-[2rem] leading-none text-foreground">{score.toFixed(1)}</span>
                    {/* Neutral ink: the coral is reserved for "fails rules check", and trailing the
                        leader is not a rules failure. */}
                    <span className="text-[12px] text-muted">
                      <span className="numeral text-foreground">{signed(score - leaderScore)}</span> vs leader
                    </span>
                  </div>
                  <GapToLeader gap={leaderScore - score} scale={scale} />
                </div>
                <div className="flex flex-col items-end gap-1.5 sm:mt-2.5 sm:flex-row sm:items-baseline sm:justify-between">
                  <span className="whitespace-nowrap text-[12px] text-muted">
                    Δ wins <WinsText alt={alt} />
                  </span>
                  <Link
                    href={`/trades/${alt.trade_id}`}
                    className="inline-flex items-center gap-1 whitespace-nowrap text-[12.5px] font-medium text-signal hover:text-foreground"
                  >
                    Report
                    <IconArrowRight size={14} />
                  </Link>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

function WinsText({ alt }: { alt: BoardAlternative }) {
  const d = distributionOf(alt.uncertainty);
  if (!d) return <span className="text-unavail">n/a</span>;
  return (
    <span className="whitespace-nowrap">
      <span className="numeral text-foreground">{signed(d.median)}</span>{" "}
      <span className="data text-muted">
        [{signed(d.p10)}, {signed(d.p90)}]
      </span>
    </span>
  );
}

/* ----------------------------------------------------- off the ranking */

export function NotRankedList({ unrankable }: { unrankable: BoardAlternative[] }) {
  if (unrankable.length === 0) return null;
  return (
    <div className="rounded-xl border border-dashed border-line bg-panel/50 px-4 py-4 sm:px-5">
      <div className="eyebrow flex items-center gap-2 text-unavail">
        <span aria-hidden>—</span>
        Not ranked ({unrankable.length})
      </div>
      <p className="mt-1.5 max-w-3xl text-[12.5px] leading-snug text-muted">
        These deals are on the board but never compete: a deal that fails a verified rule cannot be
        executed, one with no scorable component has nothing to compare, and one whose every scored
        component you have weighted to 0 has no score under these priorities.
      </p>
      <ul className="mt-3 space-y-2">
        {unrankable.map((alt) => {
          const rules = (alt.suppression?.failing_rules ?? [])
            .map((r) => r.rule_code)
            .filter((c, i, a) => a.indexOf(c) === i);
          return (
            <li key={alt.trade_id} className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13.5px]">
              <Link
                href={`/trades/${alt.trade_id}`}
                className="min-w-0 max-w-full truncate font-medium text-foreground underline decoration-line underline-offset-[3px] hover:text-signal"
              >
                {alt.name}
              </Link>
              <Badge status={alt.legality_status}>{LEGALITY_LABEL[alt.legality_status]}</Badge>
              <span className="text-[12px] text-unavail">
                {unrankedReason(alt)}
                {rules.length > 0 ? ` · ${rules.join(" · ")}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Deals sent to the backend that came back with no evaluation for the scored side. */
export function DroppedNotice({
  dropped,
  sideAbbr,
}: {
  dropped: { id: string; name: string; involvesSide: boolean | null }[];
  sideAbbr: string | null;
}) {
  if (dropped.length === 0) return null;
  const notInvolved = dropped.filter((d) => d.involvesSide === false);
  return (
    <UnavailableNotice
      reason={
        <>
          {dropped.length === 1 ? "1 deal you selected was" : `${dropped.length} deals you selected were`}{" "}
          left off the board — the backend returned no evaluation for{" "}
          {dropped.length === 1 ? "it" : "them"}
          {sideAbbr ? ` from ${sideAbbr}'s side` : ""}: {dropped.map((d) => d.name).join(", ")}.
          {sideAbbr && notInvolved.length === dropped.length && (
            <> {sideAbbr} is not part of {dropped.length === 1 ? "that deal" : "those deals"}.</>
          )}{" "}
          Nothing was estimated in {dropped.length === 1 ? "its" : "their"} place.
        </>
      }
    />
  );
}

/* ---------------------------------------------------------- priorities */

/** The range input's own track and thumb, so the control matches the rest of the board. */
const RANGE_CLASS = clsx(
  "h-5 w-full cursor-pointer appearance-none bg-transparent",
  "[&::-webkit-slider-runnable-track]:h-1.5 [&::-webkit-slider-runnable-track]:rounded-full [&::-webkit-slider-runnable-track]:[background:var(--fill)]",
  "[&::-webkit-slider-thumb]:-mt-[5px] [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-[var(--arena)] [&::-webkit-slider-thumb]:bg-signal [&::-webkit-slider-thumb]:shadow-[0_0_0_1px_var(--signal-2)]",
  "[&::-moz-range-track]:h-1.5 [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-panel3",
  "[&::-moz-range-progress]:h-1.5 [&::-moz-range-progress]:rounded-full [&::-moz-range-progress]:bg-signal",
  "[&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-[var(--arena)] [&::-moz-range-thumb]:bg-signal",
);

export function PriorityRail({
  weights,
  savedWeights,
  onChange,
  onReset,
  startingLabel,
  savedLeader,
  liveLeader,
}: {
  weights: Record<string, number>;
  savedWeights: Record<string, number>;
  onChange: (key: string, value: number) => void;
  onReset: () => void;
  startingLabel: string;
  /** Compared by trade id: two saved deals can share a name. */
  savedLeader: { id: string; name: string } | null;
  liveLeader: { id: string; name: string } | null;
}) {
  const shares = weightShares(weights);
  const savedShares = weightShares(savedWeights);
  const changed = COMPONENT_KEYS.some((key) => (weights[key] ?? 0) !== (savedWeights[key] ?? 0));
  return (
    <Panel
      title="Adjust priorities"
      subtitle="Exploratory re-weighting of stored component scores. The board re-ranks live; no model is re-run."
      actions={
        <Button size="sm" variant="ghost" onClick={onReset} disabled={!changed}>
          Reset
        </Button>
      }
    >
      <p className="mb-4 text-[12px] leading-snug text-faint">
        Starting point · <span className="text-muted">{startingLabel}</span>
      </p>
      <div className="space-y-4">
        {COMPONENT_KEYS.map((key) => {
          const value = weights[key] ?? 0;
          const share = shares[key];
          const moved = Math.round(share * 100) !== Math.round(savedShares[key] * 100);
          return (
            <div key={key}>
              <div className="flex items-baseline justify-between gap-2">
                <label
                  htmlFor={`weight-${key}`}
                  className="min-w-0 text-[13px] leading-snug text-foreground"
                  title={COMPONENT_NOTE[key]}
                >
                  {COMPONENT_LABEL[key]}
                </label>
                <span className="flex shrink-0 items-baseline gap-2">
                  {moved && <span className="text-[11px] text-faint">was {pct(savedShares[key])}</span>}
                  <span className="numeral w-10 text-right text-[15px] leading-none text-signal">{pct(share)}</span>
                </span>
              </div>
              <input
                id={`weight-${key}`}
                type="range"
                min={0}
                max={100}
                step={1}
                value={value}
                aria-label={`Priority weight for ${COMPONENT_LABEL[key]}`}
                aria-valuetext={`${pct(share)} of the decision score`}
                onChange={(event) => onChange(key, Number(event.target.value))}
                className={clsx("mt-1.5 accent-[var(--signal)]", RANGE_CLASS)}
                style={
                  {
                    "--fill": `linear-gradient(90deg, var(--signal) 0 ${value}%, var(--arena-high) ${value}% 100%)`,
                  } as React.CSSProperties
                }
              />
            </div>
          );
        })}
      </div>
      {savedLeader && liveLeader && savedLeader.id !== liveLeader.id && (
        <p
          className="mt-5 flex gap-2.5 rounded-lg border border-signal/30 bg-signal/8 px-3.5 py-2.5 text-[12.5px] leading-snug text-muted"
          aria-live="polite"
        >
          <Dot className="mt-[0.45em]" />
          <span className="min-w-0">
            Under the starting weights <span className="text-foreground">{savedLeader.name}</span> leads.
            Your priorities put <span className="font-semibold text-foreground">{liveLeader.name}</span>{" "}
            first.
          </span>
        </p>
      )}
      <p className="mt-5 border-t border-hairline pt-4 text-[12px] leading-relaxed text-muted">
        Decision score = Σ(weight × component) ÷ Σ(weights), over available components only. A
        component marked n/a drops out and the remaining weights renormalize — the same rule the
        backend applies. The saved rank-stability analysis in{" "}
        <span className="text-foreground">Sensitivity</span> is independent of these sliders.
      </p>
    </Panel>
  );
}

/* ---------------------------------------------------- outcome ledger */

/**
 * Every deal's projected-wins band on ONE shared axis, in board order, so the outcomes can
 * be compared by eye: whether a band clears zero, how wide it is, where the medians sit.
 * Deals that cannot be projected keep their row, with the reason, instead of vanishing.
 */
export function OutcomeLedger({
  ranked,
  unrankable,
  axis,
  sideFor,
}: {
  ranked: Ranked;
  unrankable: BoardAlternative[];
  axis: number;
  sideFor: (alt: BoardAlternative) => string | null;
}) {
  const rows = [
    ...ranked.map(({ alt }, index) => ({ alt, rank: index + 1 })),
    ...unrankable.map((alt) => ({ alt, rank: null as number | null })),
  ];
  const draws = Array.from(
    new Set(rows.map(({ alt }) => distributionOf(alt.uncertainty)?.n_draws).filter((n): n is number => !!n)),
  );
  const grid = "grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-2 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_7.5rem]";

  return (
    <Panel
      title="Projected wins, one axis"
      subtitle="The change in wins over a full 82-game season for every deal on the board, on the same scale."
      padded={false}
    >
      <div className={clsx(grid, "px-4 pt-4 sm:px-5")} aria-hidden>
        <span className="hidden sm:block" />
        <div>
          <WinsAxisLabels axis={axis} />
          <div className="mt-1 flex justify-between text-[11px] text-faint">
            <span>Fewer wins</span>
            <span>No change</span>
            <span>More wins</span>
          </div>
        </div>
        <span className="hidden self-end text-right text-[11px] text-faint sm:block">median · 10th–90th</span>
      </div>
      <ul className="mt-3 divide-y divide-hairline border-t border-hairline">
        {rows.map(({ alt, rank }) => {
          const d = distributionOf(alt.uncertainty);
          const lead = rank === 1;
          const side = sideFor(alt);
          return (
            <li
              key={alt.trade_id}
              className={clsx(grid, "items-center px-4 py-3.5 sm:px-5", lead && "bg-signal/[0.04]")}
            >
              <div className="flex min-w-0 items-center gap-3">
                <RankMark rank={rank} />
                <span className="min-w-0">
                  <span className="line-clamp-2 block break-words text-[13.5px] font-semibold leading-tight text-foreground">
                    {alt.name}
                  </span>
                  <span className="mt-0.5 block text-[11.5px] text-faint">
                    {rank !== null ? `Rank ${rank}` : "Not ranked"}
                    {side ? ` · for ${side}` : ""}
                  </span>
                </span>
              </div>
              <div className="min-w-0">
                <WinsBand alt={alt} axis={axis} lead={lead} />
                {!d && <p className="mt-1.5 text-[12px] text-unavail">{noDistributionReason(alt)}</p>}
              </div>
              <div
                className={clsx(
                  "items-baseline justify-between gap-3 sm:block sm:text-right",
                  d ? "flex" : "hidden sm:block",
                )}
              >
                <span
                  className={clsx(
                    "numeral block text-[16px] leading-none",
                    !d ? "text-unavail" : lead ? "text-signal" : "text-foreground",
                  )}
                >
                  {d ? signed(d.median) : "—"}
                </span>
                {d && (
                  <span className="mt-1 block text-[11.5px] text-muted sm:mt-1.5">
                    <span className="numeral font-normal">
                      {signed(d.p10)} to {signed(d.p90)}
                    </span>
                    <span className="block text-faint">{pct(d.prob_positive)} chance it helps</span>
                  </span>
                )}
                {d && (
                  <span className="sr-only">
                    {alt.name}: median {signed(d.median)} wins, 10th to 90th percentile {signed(d.p10)} to{" "}
                    {signed(d.p90)}, {pct(d.prob_positive)} chance it helps.
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-hairline px-4 py-3 text-[11.5px] leading-snug text-faint sm:px-5">
        Band = 10th–90th percentile of the simulation · tick = median · brighter line = no change · same
        ±{axis}-win scale for every deal
        {draws.length === 1 ? ` · ${draws[0].toLocaleString()} draws each` : ""}. Each deal is projected
        for the team it is scored for.
      </p>
    </Panel>
  );
}

/* ---------------------------------------------------- trade-off matrix */

/**
 * The trade-offs as a matrix: one row per component, one column per ranked deal, beside
 * the share of the score you currently give that component. The best stored score in each
 * row is marked, so where each deal wins — and whether that is where your weight sits —
 * reads at a glance. A phone gets the same matrix as one small block per component.
 */
export function TradeoffMatrix({
  ranked,
  weights,
}: {
  ranked: Ranked;
  weights: Record<string, number>;
}) {
  const alts = ranked.map((r) => r.alt);
  const best = bestByComponent(alts);
  const shares = weightShares(weights);
  const scoredCount = (key: string) => alts.filter((a) => isAvailable(a.components[key])).length;
  const isBest = (alt: BoardAlternative, key: string) => {
    const value = alt.components[key];
    return isAvailable(value) && scoredCount(key) > 1 && value === best[key];
  };

  const valueCell = (alt: BoardAlternative, key: string) => {
    const value = alt.components[key];
    const available = isAvailable(value);
    const top = isBest(alt, key);
    return (
      <>
        <div className="flex items-center justify-between gap-2">
          <span
            className={clsx(
              "numeral text-[14px] leading-none",
              !available ? "text-unavail" : top ? "text-signal" : "text-foreground",
            )}
          >
            {available ? value.toFixed(0) : "n/a"}
          </span>
          {top && (
            <>
              <span
                aria-hidden
                className="rounded-full border border-signal/40 bg-signal/10 px-1.5 py-px text-[10px] leading-tight text-signal"
              >
                best
              </span>
              <span className="sr-only">best on the board</span>
            </>
          )}
        </div>
        <DeviationBar value={value} label={`${alt.name}, ${COMPONENT_LABEL[key]}`} className="mt-2" />
      </>
    );
  };

  return (
    <Panel
      title="Where each deal wins"
      subtitle="Stored component scores for the ranked deals, 0–100 with 50 neutral, beside the share of the score you give each. The best on the board in each row is marked best."
      padded={false}
    >
      {/* phone: one block per component */}
      <ul className="divide-y divide-hairline sm:hidden">
        {COMPONENT_KEYS.map((key) => (
          <li key={key} className="px-4 py-3.5">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[13.5px] font-medium text-foreground">{COMPONENT_LABEL[key]}</span>
              <span className="text-[12px] text-muted">
                weight <span className="numeral text-foreground">{pct(shares[key])}</span>
              </span>
            </div>
            <ul className="mt-2.5 space-y-2.5">
              {alts.map((alt, index) => (
                <li key={alt.trade_id} className="grid grid-cols-[1.25rem_minmax(0,6.5rem)_minmax(0,1fr)] items-center gap-x-2.5">
                  <RankMark rank={index + 1} className="h-5 w-5 text-[10.5px]" />
                  <span className="truncate text-[12.5px] text-muted">{alt.name}</span>
                  <div className="min-w-0">{valueCell(alt, key)}</div>
                </li>
              ))}
            </ul>
          </li>
        ))}
        <li className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 px-4 py-3.5">
          <span className="text-[12.5px] font-medium text-foreground">Decision score · live</span>
          {ranked.map(({ alt, score }, index) => (
            <span key={alt.trade_id} className="flex items-center gap-1.5">
              <RankMark rank={index + 1} className="h-5 w-5 text-[10.5px]" />
              <span className={clsx("stat-serif text-[1.35rem] leading-none", index === 0 ? "text-signal" : "text-foreground")}>
                {score.toFixed(1)}
              </span>
            </span>
          ))}
        </li>
      </ul>

      {/* wider: the matrix */}
      <div className="scroll-thin relative hidden overflow-x-auto sm:block">
        <table className="w-full table-fixed border-collapse" style={{ minWidth: `${16 + alts.length * 8.5}rem` }}>
          <caption className="sr-only">
            Component scores for each ranked deal, with your current weight on each component and the
            best score in each row marked
          </caption>
          <colgroup>
            <col className="w-[11.5rem]" />
            <col className="w-[4.5rem]" />
            {alts.map((alt) => (
              <col key={alt.trade_id} />
            ))}
          </colgroup>
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className="eyebrow px-4 py-3 text-left align-bottom sm:pl-5">
                Component
              </th>
              <th scope="col" className="eyebrow px-2 py-3 text-right align-bottom">
                Weight
              </th>
              {ranked.map(({ alt }, index) => (
                <th key={alt.trade_id} scope="col" className="px-3 py-3 text-left align-bottom font-normal">
                  <span className="flex items-center gap-2">
                    <RankMark rank={index + 1} />
                    <span className="sr-only">Rank {index + 1}:</span>
                    <span className="line-clamp-2 min-w-0 break-words text-[12.5px] font-semibold leading-tight text-foreground">
                      {alt.name}
                    </span>
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {COMPONENT_KEYS.map((key) => (
              <tr key={key} className="border-b border-hairline">
                <th scope="row" className="px-4 py-3 text-left align-top font-normal sm:pl-5" title={COMPONENT_NOTE[key]}>
                  <span className="block text-[13px] text-foreground">{COMPONENT_LABEL[key]}</span>
                </th>
                <td className="px-2 py-3 text-right align-top">
                  <span className="numeral text-[12.5px] font-normal text-muted">{pct(shares[key])}</span>
                </td>
                {alts.map((alt) => (
                  <td key={alt.trade_id} className="px-3 py-3 align-top">
                    {valueCell(alt, key)}
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <th scope="row" className="px-4 py-3.5 text-left align-middle font-normal sm:pl-5">
                <span className="text-[13px] font-medium text-foreground">Decision score</span>
              </th>
              <td className="px-2 py-3.5 text-right align-middle text-[11.5px] text-faint">live</td>
              {ranked.map(({ alt, score }, index) => (
                <td key={alt.trade_id} className="px-3 py-3.5 align-middle">
                  <span
                    className={clsx(
                      "stat-serif text-[1.6rem] leading-none",
                      index === 0 ? "text-signal" : "text-foreground",
                    )}
                  >
                    {score.toFixed(1)}
                  </span>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
