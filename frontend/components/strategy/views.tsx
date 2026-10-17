"use client";

/**
 * The seven tabbed readings of one comparison. Each is the same stored response sliced
 * by a question; none of them computes a number the response did not carry.
 */

import Link from "next/link";
import {
  COMPONENT_LABEL,
  LEGALITY_EXPLAIN,
  LEGALITY_LABEL,
  LEGALITY_SHORT,
  money,
  pct,
} from "@/lib/format";
import { FirstPlaceShareBars, ParetoScatter } from "@/components/charts";
import { Badge, ButtonLink, Td, Th, UnavailableNotice } from "@/components/ui";
import {
  COMPONENT_KEYS,
  distributionOf,
  driverSide,
  isAvailable,
  signed,
  weightShares,
  type BoardComparison,
  type Ranked,
} from "./model";
import {
  ComponentReadout,
  DealSliceCard,
  MovesBlock,
  RankMark,
  WinsAxisLabels,
  WinsBand,
  WinsRange,
  noDistributionReason,
  type PlayerLookup,
} from "./parts";

function ViewIntro({ children }: { children: React.ReactNode }) {
  return <p className="mb-5 max-w-3xl text-[13.5px] leading-relaxed text-muted">{children}</p>;
}

/** A small figure inside a deal card: quiet label, serif number. */
function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow">{label}</dt>
      <dd className="stat-serif mt-1.5 text-[1.6rem] leading-none text-foreground">{children}</dd>
    </div>
  );
}

/** The backend note is written in Markdown; the page renders it as a sentence. */
function plain(note: string): string {
  return note.replaceAll("**", "").replaceAll("`", "");
}

/* --------------------------------------------------------------- summary */

export function SummaryView({
  comparison,
  ranked,
  weights,
}: {
  comparison: BoardComparison;
  ranked: Ranked;
  weights: Record<string, number>;
}) {
  const shares = weightShares(weights);
  return (
    // A container, so the scroll hint shows exactly when the table is wider than the panel.
    // The rules check and Pareto status ride in the deal cell, so the columns that decide a
    // deal fit a 1440 panel and only the component detail scrolls on narrower ones.
    <div className="@container">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="max-w-3xl text-[13.5px] leading-relaxed text-muted">
          Every stored number for the deals on the board. Rank reflects your current sliders; the
          component columns are the saved backend scores, unchanged.
        </p>
        <p className="shrink-0 text-[11.5px] text-faint @min-[1060px]:hidden">Scroll sideways for every column</p>
      </div>
      <div className="scroll-thin relative overflow-x-auto">
        <table className="w-full min-w-[980px] border-collapse">
          <caption className="sr-only">
            Comparison matrix across all six decision components for the selected deals
          </caption>
          <thead>
            <tr className="border-b border-line">
              <Th>Rank</Th>
              <Th>Deal · rules check · Pareto</Th>
              <Th numeric>Score</Th>
              <Th numeric>Δ wins [p10, p90]</Th>
              {COMPONENT_KEYS.map((key) => (
                // Component names wrap so the matrix fits a desktop panel without scrolling.
                <th
                  key={key}
                  scope="col"
                  className="eyebrow max-w-[6.25rem] px-2 py-2.5 text-right align-bottom leading-snug"
                >
                  {COMPONENT_LABEL[key]}
                </th>
              ))}
              <Th numeric>Payroll after</Th>
            </tr>
          </thead>
          <tbody>
            {ranked.map(({ alt, score }, index) => (
              <tr key={alt.trade_id} className="border-b border-hairline transition-colors hover:bg-panel2/60">
                <Td>
                  <RankMark rank={index + 1} />
                  <span className="sr-only">Rank {index + 1}</span>
                </Td>
                <Td className="py-2.5">
                  <Link
                    href={`/trades/${alt.trade_id}`}
                    className="font-medium text-foreground underline decoration-line underline-offset-[3px] hover:text-signal"
                  >
                    {alt.name}
                  </Link>
                  <span className="mt-0.5 block max-w-[12.5rem] truncate text-[11.5px] text-faint">
                    In {alt.incoming.map((p) => p.name).join(", ") || "—"} · out{" "}
                    {alt.outgoing.map((p) => p.name).join(", ") || "—"}
                  </span>
                  <span className="mt-1.5 flex flex-wrap gap-1.5">
                    <span title={LEGALITY_LABEL[alt.legality_status]}>
                      <Badge status={alt.legality_status}>{LEGALITY_SHORT[alt.legality_status]}</Badge>
                    </span>
                    {alt.dominated_by ? (
                      <Badge status="unavailable">dominated</Badge>
                    ) : (
                      <Badge status="pass">frontier</Badge>
                    )}
                  </span>
                </Td>
                <Td numeric className={index === 0 ? "font-semibold text-signal" : "font-semibold text-foreground"}>
                  {score.toFixed(1)}
                </Td>
                <Td numeric>
                  <WinsRange alt={alt} />
                </Td>
                {COMPONENT_KEYS.map((key) => {
                  const value = alt.components[key];
                  return (
                    <Td key={key} numeric>
                      {isAvailable(value) ? value.toFixed(0) : <span className="text-unavail">n/a</span>}
                    </Td>
                  );
                })}
                <Td numeric>
                  {alt.payroll_after === null ? (
                    <span className="text-unavail">n/a</span>
                  ) : (
                    money(alt.payroll_after)
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-4 max-w-4xl text-[12px] leading-relaxed text-muted">
        Δ wins is always shown with its [p10, p90] range — never a bare point estimate. Components
        marked n/a are excluded from the score and the remaining weights renormalize.{" "}
        {plain(comparison.note)}
      </p>
      <p className="mt-2 text-[11.5px] leading-relaxed text-faint">
        Current weights:{" "}
        {COMPONENT_KEYS.map((key) => `${COMPONENT_LABEL[key]} ${pct(shares[key])}`).join(" · ")}
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------- impact */

export function ImpactView({
  ranked,
  axis,
  lookup,
}: {
  ranked: Ranked;
  axis: number;
  lookup: PlayerLookup;
}) {
  return (
    <div>
      <ViewIntro>
        On-court impact is the projected change in team performance after the rotation&apos;s minutes
        are reallocated. The wins band is the honest answer — the midpoint alone would overstate what
        the model knows. Every band below shares one ±{axis}-win scale.
      </ViewIntro>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {ranked.map(({ alt }, index) => {
          const d = distributionOf(alt.uncertainty);
          return (
            <DealSliceCard key={alt.trade_id} alt={alt} rank={index + 1}>
              <ComponentReadout componentKey="performance" value={alt.components.performance} />
              <div className="mt-5 border-t border-hairline pt-4">
                <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="eyebrow">Projected wins vs today</h4>
                  <span className="text-[11.5px] text-faint">
                    {d && d.n_draws > 0 ? `${d.n_draws.toLocaleString()} simulations` : ""}
                  </span>
                </div>
                <WinsBand alt={alt} axis={axis} lead={index === 0} />
                <WinsAxisLabels axis={axis} className="mt-1" />
                <p className="mt-2 text-[12px] text-muted">
                  {d ? (
                    <>
                      Median <span className="numeral text-foreground">{signed(d.median)} wins</span> ·{" "}
                      {signed(d.p10)} to {signed(d.p90)} · {pct(d.prob_positive)} chance it helps
                    </>
                  ) : (
                    noDistributionReason(alt)
                  )}
                </p>
              </div>
              <div className="mt-5 border-t border-hairline pt-4">
                <h4 className="eyebrow mb-2.5">Who moves · league impact percentile</h4>
                <MovesBlock alt={alt} lookup={lookup} />
              </div>
            </DealSliceCard>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- fit */

export function FitView({ ranked, lookup }: { ranked: Ranked; lookup: PlayerLookup }) {
  return (
    <div>
      <ViewIntro>
        Roster fit asks whether the incoming players address this roster&apos;s measured needs
        without duplicating what it already has. It measures direction, not size — 50 means the deal
        changes nothing against those needs — and it is not a talent rating. The comparison stores one
        fit score per deal; the need-by-need breakdown lives on each deal&apos;s own report.
      </ViewIntro>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {ranked.map(({ alt }, index) => (
          <DealSliceCard key={alt.trade_id} alt={alt} rank={index + 1}>
            <ComponentReadout componentKey="fit" value={alt.components.fit} />
            <div className="mt-5 border-t border-hairline pt-4">
              <MovesBlock alt={alt} lookup={lookup} compact />
            </div>
            <p className="mt-4 border-t border-hairline pt-3 text-[12px] leading-snug text-faint">
              Impact percentiles place each player among everyone the model scored and are shown for
              context only. Fit is scored from skill profiles and minutes, not from impact.
            </p>
          </DealSliceCard>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- financial */

export function FinancialView({
  ranked,
  contractsConfigured,
  contractsNote,
}: {
  ranked: Ranked;
  contractsConfigured: boolean;
  contractsNote: string | null;
}) {
  const anyFinancials = ranked.some(
    ({ alt }) => isAvailable(alt.components.contract) || isAvailable(alt.payroll_after),
  );

  return (
    <div>
      <ViewIntro>
        Contract value weighs salary paid against estimated on-court value, and payroll-after is the
        team&apos;s book once the deal lands. Both come from imported contract data — Pivot never
        estimates a salary.
      </ViewIntro>

      {!anyFinancials && (
        <div className="mb-4">
          <UnavailableNotice
            reason={
              contractsNote ??
              "No contract provider is configured, so contract value and payroll are not scored for these deals."
            }
            steps={
              <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                <p className="min-w-0 flex-[1_1_16rem] text-[12.5px] text-muted">
                  A deal can still pass every other rule — it just cannot be called salary-legal until
                  contracts exist.
                </p>
                <ButtonLink href="/salary-cap-center" size="sm">
                  Import steps
                </ButtonLink>
              </div>
            }
          />
        </div>
      )}
      {!anyFinancials && contractsConfigured && (
        <p className="mb-4 text-[12.5px] text-conditional">
          A contract provider is configured but these deals still returned no contract scores — check
          coverage in Data Health.
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {ranked.map(({ alt }, index) => (
          <DealSliceCard key={alt.trade_id} alt={alt} rank={index + 1}>
            <ComponentReadout componentKey="contract" value={alt.components.contract} />
            <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 border-t border-hairline pt-4">
              <div className="min-w-0">
                <dt className="eyebrow">Payroll after</dt>
                <dd className="numeral mt-1.5 text-[14px] font-medium">
                  {alt.payroll_after === null ? (
                    <span className="text-unavail">n/a</span>
                  ) : (
                    money(alt.payroll_after)
                  )}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="eyebrow">Apron status after</dt>
                <dd className="mt-1.5 text-[14px]">
                  {alt.apron_status_after ? (
                    alt.apron_status_after.replaceAll("_", " ")
                  ) : (
                    <span className="text-unavail">n/a</span>
                  )}
                </dd>
              </div>
              <div className="col-span-2 min-w-0">
                <dt className="eyebrow">Rules check</dt>
                <dd className="mt-1.5">
                  <Badge status={alt.legality_status}>{LEGALITY_LABEL[alt.legality_status]}</Badge>
                </dd>
              </div>
            </dl>
          </DealSliceCard>
        ))}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- timeline */

export function TimelineView({ ranked }: { ranked: Ranked }) {
  return (
    <div>
      <ViewIntro>
        Two questions about time: does the age profile of this deal match the window the team is
        building for, and what does it do to future optionality — picks, flexibility and roster
        slots.
      </ViewIntro>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {ranked.map(({ alt }, index) => {
          const bothMissing = !isAvailable(alt.components.timeline) && !isAvailable(alt.components.assets);
          return (
            <DealSliceCard key={alt.trade_id} alt={alt} rank={index + 1}>
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
                <ComponentReadout componentKey="timeline" value={alt.components.timeline} />
                <ComponentReadout componentKey="assets" value={alt.components.assets} />
              </div>
              {bothMissing && (
                <p className="mt-4 border-t border-hairline pt-3 text-[12px] text-unavail">
                  Neither window nor flexibility could be scored for this deal — both are excluded from
                  the decision score rather than filled in.
                </p>
              )}
            </DealSliceCard>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ risk */

export function RiskView({ ranked, axis }: { ranked: Ranked; axis: number }) {
  return (
    <div>
      <ViewIntro>
        Risk is <em>availability exposure</em> and nothing else — the minutes-weighted change in
        historical games played between the players arriving and the players leaving. Like every
        component it is scored so that higher is better: a higher risk score means less exposure. It
        used to blend in the share of simulations in which the deal helps, which is the on-court
        projection restated as a probability; the two were 0.86 correlated, so the composite counted
        the same thing twice. The simulation is still shown below, and is deliberately not scored.
      </ViewIntro>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {ranked.map(({ alt }, index) => {
          const d = distributionOf(alt.uncertainty);
          return (
            <DealSliceCard key={alt.trade_id} alt={alt} rank={index + 1}>
              <ComponentReadout componentKey="risk" value={alt.components.risk} />
              <dl className="mt-5 grid grid-cols-3 gap-x-4 gap-y-2 border-t border-hairline pt-4">
                <Figure label="Chance it helps">{d ? pct(d.prob_positive) : "—"}</Figure>
                <Figure label="Δ wins median">{d ? signed(d.median) : "—"}</Figure>
                <Figure label="Simulations">{d && d.n_draws > 0 ? d.n_draws.toLocaleString() : "—"}</Figure>
              </dl>
              <div className="mt-4">
                <WinsBand alt={alt} axis={axis} lead={index === 0} />
                <WinsAxisLabels axis={axis} className="mt-1" />
                <p className="mt-1.5 text-[12px] text-muted">
                  {d ? `${signed(d.p10)} to ${signed(d.p90)} wins · 10th–90th percentile` : noDistributionReason(alt)}
                </p>
              </div>
              {d && d.top_uncertainty_drivers.length > 0 && (
                <div className="mt-4 border-t border-hairline pt-4">
                  <h4 className="eyebrow mb-2">Where the spread comes from</h4>
                  <ul className="space-y-1.5">
                    {d.top_uncertainty_drivers.map((driver, driverIndex) => (
                      <li
                        key={`${driver.side}-${driverIndex}`}
                        className="flex items-baseline justify-between gap-3 text-[12.5px]"
                      >
                        <span className="min-w-0 truncate text-muted">{driverSide(driver.side)}</span>
                        <span className="numeral shrink-0 font-normal text-foreground">
                          ±{driver.spread_wins.toFixed(2)} wins
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p className="mt-4 border-t border-hairline pt-3 text-[12px] leading-snug text-muted">
                {LEGALITY_EXPLAIN[alt.legality_status]}
              </p>
            </DealSliceCard>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ sensitivity */

export function SensitivityView({
  comparison,
  names,
}: {
  comparison: BoardComparison;
  names: Record<string, string>;
}) {
  const share = comparison.sensitivity.first_place_share ?? {};
  const volatility = comparison.sensitivity.rank_volatility ?? {};
  const medianRank = comparison.sensitivity.median_rank ?? {};
  const nSamples = comparison.sensitivity.n_samples;
  const ids = Object.keys(share);
  const nameOf = (id: string) => names[id] ?? comparison.alternatives.find((a) => a.trade_id === id)?.name ?? id.slice(0, 8);
  const shareNames = Object.fromEntries(ids.map((id) => [id, nameOf(id)]));

  const plotted = comparison.alternatives.filter(
    (alt) => isAvailable(alt.components.performance) && isAvailable(alt.components.risk),
  );
  const omitted = comparison.alternatives.filter(
    (alt) => !isAvailable(alt.components.performance) || !isAvailable(alt.components.risk),
  );
  // A deal that fails a verified rule has no components at all — "could not be scored" would
  // misdescribe it, so the two reasons are reported separately.
  const omittedUnranked = omitted.filter((alt) => alt.decision_status !== "scored");
  const omittedUnscored = omitted.filter((alt) => alt.decision_status === "scored");
  const dominated = comparison.alternatives.filter((alt) => alt.dominated_by);

  return (
    <div>
      <ViewIntro>
        The saved backend analysis: the board is re-ranked under{" "}
        {nSamples ? <span className="numeral text-foreground">{nSamples.toLocaleString()}</span> : "a set of"}{" "}
        weight vectors sampled around the starting weights. A deal that only leads under one exact set
        of priorities is not a robust recommendation. This section is independent of the sliders.
      </ViewIntro>
      {ids.length === 0 ? (
        <UnavailableNotice reason="Nothing on the board could be ranked, so there is no rank-stability analysis to show." />
      ) : (
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2 lg:gap-10">
          <div className="min-w-0">
            <FirstPlaceShareBars share={share} names={shareNames} />
            <p className="mt-1.5 text-[11.5px] text-faint">
              {nSamples
                ? `n = ${nSamples.toLocaleString()} sampled weightings`
                : "sample size not reported by this comparison"}
            </p>

            <div className="scroll-thin relative mt-5 overflow-x-auto border-t border-hairline pt-3">
              <table className="w-full min-w-[420px] border-collapse">
                <caption className="sr-only">Rank stability under sampled strategy weights</caption>
                <thead>
                  <tr className="border-b border-line">
                    <Th>Deal</Th>
                    <Th numeric>Ranks #1</Th>
                    <Th numeric>Median rank</Th>
                    <Th numeric>Volatility</Th>
                  </tr>
                </thead>
                <tbody>
                  {ids.map((id) => (
                    <tr key={id} className="border-b border-hairline">
                      <Td className="py-2">
                        <span className="block max-w-[16rem] truncate">{nameOf(id)}</span>
                      </Td>
                      <Td numeric className="py-2">
                        {pct(share[id])}
                      </Td>
                      <Td numeric className="py-2">
                        {medianRank[id]?.toFixed(1) ?? "n/a"}
                      </Td>
                      <Td numeric className="py-2">
                        {volatility[id]?.toFixed(2) ?? "n/a"}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2.5 text-[12px] leading-snug text-muted">
              Rank volatility is the standard deviation of a deal&apos;s rank across the sampled
              weightings — lower means the answer barely moves when priorities change.
            </p>
          </div>

          <div className="min-w-0">
            <ParetoScatter
              points={plotted.map((alt) => ({
                name: alt.name,
                x: alt.components.performance as number,
                y: alt.components.risk as number,
                dominated: !!alt.dominated_by,
              }))}
            />
            {omittedUnscored.length > 0 && (
              <p className="mt-2 text-[12px] leading-snug text-unavail">
                {omittedUnscored.length} deal{omittedUnscored.length === 1 ? " is" : "s are"} not plotted:
                on-court impact or availability exposure could not be scored. They used to be drawn at 0,
                which reads as the worst deal on the board rather than as an unknown.
              </p>
            )}
            {omittedUnranked.length > 0 && (
              <p className="mt-2 text-[12px] leading-snug text-unavail">
                {omittedUnranked.length} deal{omittedUnranked.length === 1 ? " is" : "s are"} not plotted
                because {omittedUnranked.length === 1 ? "it is" : "they are"} not ranked (
                {omittedUnranked.map((alt) => alt.name).join(", ")}).
              </p>
            )}
            {dominated.length > 0 ? (
              <ul className="mt-3 space-y-2 border-t border-hairline pt-3 text-[12.5px] leading-snug text-muted">
                {dominated.map((alt) => (
                  <li key={alt.trade_id} className="flex gap-2.5">
                    <span aria-hidden className="mt-[5px] h-2 w-2 shrink-0 rounded-full border border-unavail" />
                    <span className="min-w-0">
                      <span className="text-foreground">{alt.name}</span> is dominated by{" "}
                      <span className="text-foreground">{nameOf(alt.dominated_by as string)}</span>{" "}
                      {alt.domination ? (
                        <>
                          on the {alt.domination.axes_compared} of {alt.domination.axes_total} components
                          both could be scored on (
                          {alt.domination.axes.map((a) => COMPONENT_LABEL[a] ?? a).join(", ")}).
                        </>
                      ) : (
                        <>on every component both could be scored on.</>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-3 border-t border-hairline pt-3 text-[12.5px] leading-snug text-muted">
                No deal is flagged as dominated. A flag needs another deal at least as good on every
                component both share, better on one, and at least three shared components to judge on.
              </p>
            )}
            <p className="mt-2.5 text-[12px] leading-snug text-faint">
              Both axes read the same way — higher is better. Downside protection is the risk
              component: availability exposure, scored so that higher means less. The two are
              different questions, so a deal can lead one and trail the other.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
