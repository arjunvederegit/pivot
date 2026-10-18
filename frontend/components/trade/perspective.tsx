"use client";

/**
 * One team's full read of a deal — the part of the Trade Evaluator and of the saved-deal
 * report that sits under the consequence sheet.
 *
 * Headline first: the verdict, the decision score and the projected wins with their band.
 * Then why: which components moved the score, and the simulation behind the wins figure.
 * Then the full analysis tabs (Impact, Fit, Cap, Timeline, Risk & uncertainty,
 * Precedent). The precedent search is passed in as an element and mounted only while its
 * tab is selected, so the comparable-trade retrieval runs on demand.
 *
 * A refused evaluation (the page decides which ones) gets the refusal and nothing else:
 * no verdict word, no score, no tabs.
 */

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { sectionOf } from "@/lib/evaluationDetail";
import { COMPONENT_EXPLAIN, COMPONENT_LABEL, count, money } from "@/lib/format";
import { assessSide } from "@/lib/assessment";
import { teamIdentity } from "@/lib/teamIdentity";
import type { PerformanceDetail, TeamEvaluation } from "@/lib/types";
import { UncertaintyStrip } from "@/components/charts";
import { CapTab, FitTab, ImpactTab, RiskTab, TimelineTab } from "@/components/evaluation-tabs";
import { direction, signed } from "@/components/reference/format";
import { ScrollCue } from "@/components/reference/scroll-cue";
import { Tabs } from "@/components/ui";
import {
  CONTRACT_TOOLTIP,
  RefusalBlock,
  UnmodeledNote,
  hasOutcomeBand,
  signedWins,
} from "@/components/trade/verdict";

const BASE_TABS = [
  { id: "impact", label: "Impact" },
  { id: "fit", label: "Fit" },
  { id: "cap", label: "Cap" },
  { id: "timeline", label: "Timeline" },
  { id: "risk", label: "Risk & uncertainty" },
  { id: "precedent", label: "Precedent" },
];

export function TeamPerspective({
  evaluation,
  abbreviation,
  refused,
  teamAbbreviations,
  refusalClosing,
  precedent,
  rules,
}: {
  evaluation: TeamEvaluation;
  abbreviation: string;
  /** Decided by the page: refused evaluations get the refusal and nothing else. */
  refused: boolean;
  teamAbbreviations: Record<string, string>;
  refusalClosing: string;
  /** Rendered only while its tab is selected, so the search runs on demand. */
  precedent: React.ReactNode;
  /** An extra "Rules" tab (and, under a refusal, the rules themselves). */
  rules?: React.ReactNode;
}) {
  const [tab, setTab] = useState("impact");

  if (refused) {
    return (
      <div className="space-y-4">
        <RefusalBlock
          evaluation={evaluation}
          abbreviation={abbreviation}
          teamAbbreviations={teamAbbreviations}
          closing={refusalClosing}
        />
        {rules}
      </div>
    );
  }

  const perf = sectionOf<PerformanceDetail>(evaluation.detail, "performance");
  const tabs = rules ? [...BASE_TABS, { id: "rules", label: "Rules" }] : BASE_TABS;

  return (
    <div className="space-y-6">
      <Headline evaluation={evaluation} abbreviation={abbreviation} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Drivers evaluation={evaluation} />
        <div className="min-w-0 space-y-4">
          <div className="rounded-xl border border-hairline bg-panel2/40 p-4">
            <UncertaintyStrip u={evaluation.uncertainty} />
            {perf.delta_net_rating !== undefined && (
              <p className="mt-3 border-t border-hairline pt-2.5 text-[12px] leading-relaxed text-muted">
                Modeled net-rating change{" "}
                <span className="numeral text-foreground">
                  {perf.delta_net_rating >= 0 ? "+" : ""}
                  {perf.delta_net_rating.toFixed(2)}
                </span>{" "}
                points per 100 possessions: the change in the minutes-weighted team index times
                the fitted team-level conversion (≈15), after reallocating rotation minutes.
              </p>
            )}
          </div>
          <Facts evaluation={evaluation} />
        </div>
      </div>

      <div>
        <ScrollCue target='[role="tablist"]' label={null}>
          <Tabs ariaLabel="Full analysis" active={tab} onChange={setTab} tabs={tabs} />
        </ScrollCue>
        <div
          id="analysis-panel"
          role="tabpanel"
          aria-label={tabs.find((t) => t.id === tab)?.label}
          className="pt-5"
        >
          {tab === "impact" && <ImpactTab teamEval={evaluation} perf={perf} />}
          {tab === "fit" && <FitTab teamEval={evaluation} />}
          {tab === "cap" && <CapTab teamEval={evaluation} />}
          {tab === "timeline" && <TimelineTab teamEval={evaluation} />}
          {tab === "risk" && <RiskTab teamEval={evaluation} />}
          {tab === "precedent" && precedent}
          {tab === "rules" && rules}
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- headline */

function Headline({ evaluation, abbreviation }: { evaluation: TeamEvaluation; abbreviation: string }) {
  const identity = teamIdentity(abbreviation);
  const assessment = assessSide(evaluation, identity.nickname === "Team" ? abbreviation : identity.name);
  const u = evaluation.uncertainty;
  const band = hasOutcomeBand(u);

  return (
    <div className="overflow-hidden rounded-xl border border-hairline">
      <div className="grid gap-px bg-hairline md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="min-w-0 bg-panel px-5 py-5">
          <div className="eyebrow flex items-center gap-2">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: identity.bright }} />
            Assessment for {abbreviation}
          </div>
          <h3 className="title-lg mt-2 text-balance text-foreground">{assessment.headline}</h3>
          {assessment.reason && <p className="mt-2 text-[14px] leading-snug text-foreground/90">{assessment.reason}</p>}
          <p className="mt-2 text-[12.5px] text-muted">
            Model category: <span className="text-foreground">{assessment.category}</span> · confidence{" "}
            {evaluation.confidence} ·{" "}
            <Link href="/methodology#utility" className="text-signal underline-offset-2 hover:underline">
              how this is calculated
            </Link>
          </p>
        </div>
        <div className="min-w-0 bg-panel px-5 py-5">
          <div className="eyebrow">Decision score</div>
          <p className="stat-serif mt-2 text-[2.6rem] leading-none text-foreground">
            {evaluation.composite_utility != null ? evaluation.composite_utility.toFixed(0) : "—"}
          </p>
          <p className="mt-2 text-[12.5px] text-muted">0–100 · 50 is neutral · covers evaluated factors only</p>
        </div>
        <div className="min-w-0 bg-panel px-5 py-5">
          <div className="eyebrow">Projected wins</div>
          <p className="stat-serif mt-2 text-[2.6rem] leading-none text-foreground">
            {band ? signedWins(u.median) : "—"}
          </p>
          <p className="mt-2 text-[12.5px] leading-snug text-muted">
            {band ? (
              <>
                median · range{" "}
                <span className="numeral text-foreground">
                  {signedWins(u.p10)} to {signedWins(u.p90)}
                </span>{" "}
                (10th–90th pct) · {((u.prob_positive ?? 0) * 100).toFixed(0)}% model-reported chance it helps
              </>
            ) : (
              (u.unavailable ?? "no outcome distribution")
            )}
          </p>
        </div>
      </div>
      {assessment.limitations.length > 0 && (
        <div className="space-y-1.5 border-t border-hairline bg-panel px-5 py-3.5">
          <p className="text-[11px] font-medium uppercase tracking-[0.07em] text-conditional">Not established by this score</p>
          <ul className="space-y-1">
            {assessment.limitations.map((l) => (
              <li key={l} className="flex gap-2 text-[13px] leading-snug text-muted">
                <span aria-hidden className="text-unavail">
                  —
                </span>
                <span>{l}</span>
              </li>
            ))}
          </ul>
          <p className="pt-0.5 text-[12px] leading-snug text-faint">
            Remaining weights are rescaled when a component is missing, so scores with different
            missing components are not directly comparable. A favorable basketball estimate does not
            show the deal is legal, financially sound or feasible.
          </p>
          <UnmodeledNote teamEval={evaluation} />
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- drivers */

function Drivers({ evaluation }: { evaluation: TeamEvaluation }) {
  const drivers = evaluation.drivers ?? [];
  const driverScale = Math.max(1, ...drivers.map((d) => Math.abs(d.contribution)));
  return (
    <div className="min-w-0 rounded-xl border border-hairline bg-panel2/40 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="title-sm text-foreground">What moved the score</h4>
        <span className="text-[11.5px] text-faint">points of decision score vs 50</span>
      </div>
      {drivers.length === 0 ? (
        <p className="mt-3 text-[13px] text-muted">No component moved the score away from neutral.</p>
      ) : (
        <ul className="mt-4 space-y-3">
          {drivers.map((driver) => {
            const width = Math.min(50, (Math.abs(driver.contribution) / driverScale) * 50);
            const positive = driver.contribution >= 0;
            const d = direction(driver.contribution, 1);
            return (
              <li
                key={driver.component}
                className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_3.25rem] items-center gap-3"
                title={COMPONENT_EXPLAIN[driver.component]}
              >
                <span className="min-w-0">
                  <span className="block truncate text-[13px] text-foreground">
                    {COMPONENT_LABEL[driver.component] ?? driver.component}
                  </span>
                  <span className="block text-[11px] text-faint">
                    {driver.score.toFixed(0)}/100 · {(driver.weight * 100).toFixed(0)}% weight
                  </span>
                </span>
                <span aria-hidden className="relative block h-1.5 rounded-full bg-panel3">
                  <span className="absolute -inset-y-1 left-1/2 w-px bg-faint" />
                  <span
                    className="absolute inset-y-0 rounded-full"
                    style={{
                      left: positive ? "50%" : `${50 - width}%`,
                      width: `${Math.max(1, width)}%`,
                      background: positive ? "var(--signal)" : "var(--illegal)",
                      opacity: 0.85,
                    }}
                  />
                </span>
                <span
                  className={clsx(
                    "numeral text-right text-[13px]",
                    d > 0 ? "text-legal" : d < 0 ? "text-illegal" : "text-faint",
                  )}
                >
                  <span aria-hidden>{d > 0 ? "▲ " : d < 0 ? "▼ " : "■ "}</span>
                  {signed(driver.contribution, 1)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="mt-4 border-t border-hairline pt-3 text-[11.5px] leading-relaxed text-faint">
        Strategy weights:{" "}
        {Object.entries(evaluation.weights)
          .map(([key, value]) => `${COMPONENT_LABEL[key] ?? key} ${(value * 100).toFixed(0)}%`)
          .join(" · ")}
        . Unscored components drop out and the rest re-scale, so the weights above can differ from
        the ones applied.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ facts */

function Facts({ evaluation }: { evaluation: TeamEvaluation }) {
  const legality = evaluation.legality;
  const salaryKnown = legality.incoming_salary != null || legality.outgoing_salary != null;
  const payrollDelta =
    legality.payroll_after !== null && legality.payroll_before !== null
      ? legality.payroll_after - legality.payroll_before
      : null;
  const cell = "min-w-0 bg-panel px-4 py-3";
  return (
    <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-hairline bg-hairline sm:grid-cols-3">
      <div className={cell}>
        <dt className="eyebrow">Confidence</dt>
        <dd
          className={clsx(
            "numeral mt-1.5 text-[1.1rem] capitalize",
            evaluation.confidence === "low" ? "text-conditional" : "text-foreground",
          )}
        >
          {evaluation.confidence}
        </dd>
        <dd className="mt-0.5 text-[11.5px] text-faint">
          {evaluation.excluded_components.length > 0
            ? `${count(evaluation.excluded_components.length, "component")} unscored`
            : "all components scored"}
        </dd>
      </div>
      <div className={cell} title={salaryKnown ? undefined : CONTRACT_TOOLTIP}>
        <dt className="eyebrow">Salary in · out</dt>
        <dd className="numeral mt-1.5 text-[1.1rem] text-foreground">
          {salaryKnown ? `${money(legality.incoming_salary)} · ${money(legality.outgoing_salary)}` : "—"}
        </dd>
        <dd className="mt-0.5 text-[11.5px] text-faint">
          {salaryKnown ? "committed salary" : "contracts not imported"}
        </dd>
      </div>
      <div className={clsx(cell, "col-span-2 sm:col-span-1")} title={payrollDelta !== null ? undefined : CONTRACT_TOOLTIP}>
        <dt className="eyebrow">Payroll change</dt>
        <dd className="numeral mt-1.5 text-[1.1rem] text-foreground">
          {payrollDelta !== null ? money(payrollDelta) : "—"}
        </dd>
        <dd className="mt-0.5 text-[11.5px] text-faint">
          {payrollDelta !== null
            ? (legality.apron_status_after ?? "apron unavailable")
            : "never estimated"}
        </dd>
      </div>
    </dl>
  );
}
