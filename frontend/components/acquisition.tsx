"use client";

/**
 * Acquisition targets — the panel that starts from a need instead of from a trade.
 *
 * It renders three things the API insists on, and would be dishonest without:
 *
 * - **the two rules, in words**. Candidates are filtered by the need and ranked by
 *   projected wins, and neither is a hidden weighting. The panel prints both, verbatim.
 * - **the cost, beside the target and never folded into its rank**. A cheap target and an
 *   expensive one sit in the same order here.
 * - **the search's own accounting** — how many players were considered, how many were set
 *   aside and why, and how many trades were actually evaluated against the budget.
 *
 * Each target's impact estimate is placed in the league with the shared impact components,
 * and the filter is drawn rather than only stated: the target's percentile in the skill
 * that answers the need, against the roster's own level in it. The wins figure is the
 * model's point estimate for adding the player alone — this search serves no band, and
 * the panel says so next to the rules rather than in a footnote.
 */

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { LEGALITY_SHORT, NEED_LABEL, SKILL_LABEL, money, ordinal } from "@/lib/format";
import { evaluatorLink } from "@/lib/shareState";
import type { AcquisitionResponse, AcquisitionTarget } from "@/lib/types";
import { FitDeviation } from "@/components/fit";
import { ImpactInline } from "@/components/impact";
import { TeamLogo } from "@/components/media";
import { PercentileTrack } from "@/components/teams/percentile-track";
import { SectionHead } from "@/components/teams/section-head";
import { Badge, SegmentedControl, Skeleton, UnavailableNotice } from "@/components/ui";

const REJECTION_LABEL: Record<string, string> = {
  no_balancing_package: "no package balanced the value",
  verified_illegal: "the trade failed a CBA rule",
  focal_utility: "this team did not clear neutral",
  counterparty_utility: "the other team did not clear neutral",
  projected_win_loss: "one side lost more than two projected wins",
  context_error: "the trade context could not be built",
};

/** Signed, with the sign taken from the rounded value — never "−0.0" (as `tei()` does). */
const signed = (x: number, digits = 1) => {
  const shown = Math.abs(x).toFixed(digits);
  const r = Number(x.toFixed(digits));
  return r > 0 ? `+${shown}` : r < 0 ? `−${shown}` : shown;
};

function TargetRow({
  target,
  teamId,
  teamAbbr,
  rank,
  rosterLevel,
}: {
  target: AcquisitionTarget;
  teamId: string;
  teamAbbr: string;
  rank: number;
  /** The roster's own percentile (0–1) in the skill the filter reads, when served. */
  rosterLevel: number | null;
}) {
  const [open, setOpen] = useState(false);
  const cost = target.acquisition_cost;
  const evaluation = target.trade_evaluation;
  // The evaluator's own share-state encoding, so the link opens the exact deal that was
  // evaluated rather than an empty builder with the two teams in it.
  const href = evaluation
    ? evaluatorLink({
        teamIds: evaluation.team_ids,
        moves: Object.fromEntries(
          evaluation.player_moves.map((m) => [m.player_id, m.to_team_id]),
        ),
        picks: [],
        name: `${target.name} to ${teamId === evaluation.team_ids[0] ? "us" : target.team.abbreviation}`,
      })
    : evaluatorLink({ teamIds: [teamId, target.team.id], moves: {}, picks: [] });
  const skillLabel = SKILL_LABEL[target.need_skill] ?? target.need_skill.replace(/_/g, " ");
  const detailId = `target-detail-${target.player_id}`;

  return (
    <li className="sheet min-w-0 overflow-hidden transition-colors hover:border-line">
      <div className="p-3.5 sm:p-4">
        {/* who, and the figure the list is ranked by */}
        <div className="flex items-start gap-3">
          <span aria-hidden className="numeral w-3 shrink-0 pt-2 text-[12px] text-faint">
            {rank}
          </span>
          <TeamLogo
            abbreviation={target.team.abbreviation}
            name={target.team.name}
            size={32}
            decorative
            className="mt-0.5"
          />
          <div className="min-w-0 flex-1">
            <p className="text-[15px] font-semibold leading-tight text-foreground sm:truncate">
              {target.name}
            </p>
            <p className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[12px] text-muted">
              <span className="whitespace-nowrap">
                {target.team.abbreviation}
                {target.age !== null ? ` · ${target.age.toFixed(0)}y` : ""}
                {target.minutes !== null ? ` · ${target.minutes.toFixed(1)} min` : ""}
              </span>
              {/* ImpactInline sets its ordinal suffix at half of 13px (6.5px); hold it at the
                  10px floor here so "95th" stays readable in this meta line. */}
              <span className="inline-flex items-baseline gap-1 whitespace-nowrap [&_.numeral>span]:text-[10px]">
                <span className="text-faint">impact</span>
                <ImpactInline tei={target.tei} />
              </span>
            </p>
          </div>
          <div
            className="shrink-0 text-right"
            title="The model's point estimate for adding this player alone, over a full 82-game season. This search does not serve a 10–90% band; the Trade Evaluator does."
          >
            <span className="stat-serif block text-[2rem] leading-none text-foreground">
              {target.projected_delta_wins === null ? "—" : signed(target.projected_delta_wins)}
            </span>
            <span className="mt-1 block max-w-[5rem] text-[11px] leading-tight text-faint sm:max-w-none sm:whitespace-nowrap">
              {target.projected_delta_wins === null ? "not projected" : "wins if added alone"}
            </span>
          </div>
        </div>

        {/* the filter, drawn: his skill against the roster's own level */}
        <div className="mt-4 min-w-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[12px]">
            <span className="text-muted">
              {skillLabel}{" "}
              <span className="numeral text-foreground">{ordinal(target.skill_percentile * 100)}</span>
              {rosterLevel !== null && (
                <span
                  className="text-faint"
                  title="Roster benchmark: the third-best value in this skill among ALL of the roster's players with minutes, as a league percentile. Skill coverage on this page uses only the top-10 rotation by minutes, and the team diagnosis uses a team statistic against the other 29 teams — three different measures, so they can differ."
                >
                  {" "}
                  vs {teamAbbr} roster benchmark{" "}
                  <span className="numeral text-seriesb">{ordinal(rosterLevel * 100)}</span>
                </span>
              )}
            </span>
            <span className="tabular-nums text-signal">
              +{(target.need_improvement * 100).toFixed(0)} percentile pts
            </span>
          </div>
          <PercentileTrack
            className="mt-2"
            value={target.skill_percentile * 100}
            reference={rosterLevel !== null ? rosterLevel * 100 : null}
          />
        </div>
      </div>

      {/* the cost, beside the target and never folded into its rank */}
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-t border-hairline bg-panel/50 px-3.5 py-2.5 text-[12px] text-muted sm:px-4">
        <span>
          Costs about{" "}
          <span className="numeral text-foreground">{cost.package_value_projected_wins.toFixed(1)}</span>{" "}
          wins of value back
        </span>
        <span aria-hidden className="text-faint">
          ·
        </span>
        <span>
          {cost.salary === null
            ? "salary unavailable"
            : `${money(cost.salary)} in, needs ≈ ${money(cost.minimum_outgoing_salary)} out`}
        </span>
        {cost.rank_on_own_team_by_minutes !== null && (
          <>
            <span aria-hidden className="text-faint">
              ·
            </span>
            <span>
              {target.team.abbreviation}&apos;s No. {cost.rank_on_own_team_by_minutes} by minutes
            </span>
          </>
        )}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="ml-auto font-medium text-signal underline-offset-2 hover:underline"
          aria-expanded={open}
          aria-controls={detailId}
        >
          {open ? "Hide detail" : "Why and how"}
        </button>
      </div>

      {open && (
        <div
          id={detailId}
          className="fade-in grid gap-5 border-t border-hairline px-4 py-4 text-[12.5px] leading-relaxed text-muted md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]"
        >
          <div className="min-w-0">
            <div className="eyebrow">Why</div>
            <ul className="mt-2 space-y-1.5">
              {target.why.map((line) => (
                <li key={line} className="flex gap-2">
                  <span aria-hidden className="mt-[0.55em] h-1 w-1 shrink-0 rounded-full bg-faint" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2.5 text-faint">{cost.reported_not_scored}.</p>
          </div>
          <div className="min-w-0 space-y-3">
            <div>
              <div className="eyebrow">Suggested package</div>
              <p className="mt-2 text-foreground">
                {target.suggested_package.length === 0
                  ? "none found"
                  : target.suggested_package.map((p) => p.name).join(", ")}
              </p>
              <p className="mt-0.5">{target.suggested_package_note}.</p>
            </div>
            {evaluation && (
              <div className="rounded-lg border border-hairline bg-panel px-3 py-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="eyebrow">Evaluated</span>
                  <Badge status={evaluation.legality_status}>
                    {LEGALITY_SHORT[evaluation.legality_status] ??
                      evaluation.legality_status.replace(/_/g, " ")}
                  </Badge>
                </div>
                <p className="mt-2">
                  Composite score: {teamAbbr} {evaluation.focal_utility.toFixed(0)}/100,{" "}
                  {target.team.abbreviation} {evaluation.counterparty_utility.toFixed(0)}/100 (50 =
                  neutral) — {evaluation.legality_status.replace(/_/g, " ")}.
                </p>
                {(evaluation.projected_delta_wins.focal !== null ||
                  evaluation.projected_delta_wins.counterparty !== null) && (
                  <p className="mt-1 text-[12px]">
                    after the package: {teamAbbr}{" "}
                    <span className="numeral text-foreground">
                      {evaluation.projected_delta_wins.focal === null
                        ? "—"
                        : signed(evaluation.projected_delta_wins.focal)}
                    </span>{" "}
                    · {target.team.abbreviation}{" "}
                    <span className="numeral text-foreground">
                      {evaluation.projected_delta_wins.counterparty === null
                        ? "—"
                        : signed(evaluation.projected_delta_wins.counterparty)}
                    </span>{" "}
                    wins
                  </p>
                )}
              </div>
            )}
            {target.fit_score !== null && (
              <div>
                <div className="flex items-baseline justify-between gap-2 text-[12px]">
                  <span className="text-muted">
                    fit <span className="numeral text-foreground">{target.fit_score.toFixed(0)}</span>
                  </span>
                  <span className="text-faint">50 = no change</span>
                </div>
                <FitDeviation score={target.fit_score} className="mt-1.5" />
              </div>
            )}
            <Link
              href={href}
              className="inline-block text-[12.5px] font-medium text-signal underline underline-offset-2 hover:text-foreground"
            >
              Open this deal in the Trade Evaluator
            </Link>
          </div>
        </div>
      )}
    </li>
  );
}

export function AcquisitionTargetsPanel({
  teamId,
  teamAbbr,
  className,
}: {
  teamId: string;
  teamAbbr?: string;
  className?: string;
}) {
  const [needKey, setNeedKey] = useState<string | null>(null);
  const [sort, setSort] = useState<"impact" | "need">("impact");
  const query = useQuery({
    queryKey: ["acquisition-targets", teamId, needKey, sort],
    queryFn: () =>
      api.get<AcquisitionResponse>(
        `/teams/${teamId}/acquisition-targets?limit=6&sort=${sort}` +
          (needKey ? `&need_key=${needKey}` : ""),
      ),
    // Keep the last answer on screen while a new need or order loads, so the controls do
    // not vanish under a skeleton on every click.
    placeholderData: (previous) => previous,
  });
  const data = query.data;
  const abbr = teamAbbr ?? data?.team.abbreviation ?? "this team";
  const rosterLevel = data?.target_need?.roster_strength_percentile ?? null;
  const updating = query.isFetching && query.isPlaceholderData;

  return (
    <section aria-labelledby="acquisition-heading" className={clsx("min-w-0", className)}>
      <SectionHead
        id="acquisition-heading"
        eyebrow="Answers to the need"
        title="Who fixes this?"
        aside="Start from the need. Candidates are filtered by it and ranked by projected wins; cost sits beside each name and is never folded into the order."
      />
      <div className="panel p-4 sm:p-5">
        {query.isPending && (
          <div className="space-y-4" role="status" aria-label="Loading acquisition targets">
            <Skeleton className="h-9 w-2/3" />
            <Skeleton className="h-16" />
            <div className="grid gap-3 xl:grid-cols-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-40" />
              ))}
            </div>
          </div>
        )}
        {query.error && <UnavailableNotice reason={String(query.error)} />}
        {data && !data.available && (
          <UnavailableNotice
            reason={data.unavailable_reason ?? "No targets could be produced for this team."}
          />
        )}
        {data?.available && (
          <div className={clsx("space-y-5 transition-opacity", updating && "opacity-60")}>
            {/* ------------------------------------------------------ the controls */}
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
              <div className="min-w-0">
                <div className="eyebrow">Need</div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {data.diagnosis
                    .filter((d) => d.severity > 0)
                    .slice(0, 5)
                    .map((d) => {
                      const active = (data.target_need?.need_key ?? "") === d.need_key;
                      return (
                        <button
                          key={d.need_key}
                          type="button"
                          disabled={!d.addressable}
                          aria-pressed={active}
                          onClick={() => setNeedKey(d.need_key)}
                          title={d.addressable ? d.explanation : (d.not_addressable_reason ?? "")}
                          className={clsx(
                            "inline-flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-2xl border px-3 py-1.5 text-left text-[12.5px] transition-colors",
                            active
                              ? "border-signal/70 bg-signal/12 font-medium text-signal"
                              : d.addressable
                                ? "border-line text-muted hover:border-signal/50 hover:text-foreground"
                                : "cursor-not-allowed border-dashed border-line text-faint",
                          )}
                        >
                          <span className="whitespace-nowrap">{NEED_LABEL[d.need_key] ?? d.need_key}</span>
                          <span aria-hidden className="whitespace-nowrap text-[11px] tabular-nums opacity-75">
                            severity {d.severity.toFixed(2)}
                          </span>
                          {!d.addressable && (
                            <span className="whitespace-nowrap text-[11.5px]">· no skill addresses this</span>
                          )}
                        </button>
                      );
                    })}
                </div>
              </div>
              <div className="flex items-end gap-3 lg:flex-col lg:items-end lg:gap-2">
                <div>
                  <div className="eyebrow lg:text-right">Rank by</div>
                  <div className="mt-2">
                    <SegmentedControl
                      ariaLabel="Rank targets by"
                      value={sort}
                      onChange={(value) => setSort(value as "impact" | "need")}
                      options={[
                        { value: "impact", label: "Projected wins" },
                        { value: "need", label: "Need improvement" },
                      ]}
                    />
                  </div>
                </div>
                {updating && (
                  <span className="pb-2 text-[11.5px] text-muted lg:pb-0" role="status">
                    updating…
                  </span>
                )}
              </div>
            </div>

            {/* ------------------------------------------------- the rules, verbatim */}
            <dl className="grid gap-x-5 gap-y-2 rounded-lg border border-hairline bg-panel2/60 px-4 py-3 text-[12.5px] leading-relaxed sm:grid-cols-[4.5rem_minmax(0,1fr)]">
              <dt className="eyebrow pt-[3px] text-foreground">Filter</dt>
              <dd className="text-muted">{data.filter_rule}.</dd>
              <dt className="eyebrow pt-[3px] text-foreground">Rank</dt>
              <dd className="text-muted">{data.sort_rule}.</dd>
              <dt className="eyebrow pt-[3px] text-foreground">Wins</dt>
              <dd className="text-muted">
                Win figures are point projections over a full 82-game season; this search does
                not serve a 10–90% band. Open a deal in the Trade Evaluator to see its band.
              </dd>
            </dl>

            {/* ---------------------------------------------------------- targets */}
            {data.targets.length > 0 ? (
              <ol className="grid items-start gap-3 xl:grid-cols-2">
                {data.targets.map((t, i) => (
                  <TargetRow
                    key={t.player_id}
                    target={t}
                    teamId={teamId}
                    teamAbbr={abbr}
                    rank={i + 1}
                    rosterLevel={rosterLevel}
                  />
                ))}
              </ol>
            ) : (
              <p className="rounded-lg border border-dashed border-line px-4 py-3.5 text-[13px] text-muted">
                No player both improves this need and survives a trade both front offices would
                make. The rejection counts below say where the search stopped.
              </p>
            )}

            {/* ---------------------------------------------- the search's accounting */}
            {data.search && data.feasibility && (
              <div className="border-t border-hairline pt-4">
                <div className="eyebrow">How the search narrowed</div>
                <ol className="mt-2.5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-hairline bg-hairline sm:grid-cols-4">
                  {[
                    { n: data.search.players_considered, label: "players considered" },
                    ...(data.search.candidates !== undefined
                      ? [{ n: data.search.candidates, label: "improve the need" }]
                      : []),
                    {
                      n: data.feasibility.trades_evaluated,
                      label: `trades evaluated, of a ${data.feasibility.budget} budget`,
                    },
                    { n: data.targets.length, label: "shown" },
                  ].map((step) => (
                    <li key={step.label} className="min-w-0 bg-panel px-3.5 py-3">
                      <span className="stat-serif block text-[1.7rem] leading-none text-foreground">
                        {step.n}
                      </span>
                      <span className="mt-1.5 block text-[12px] leading-snug text-muted">{step.label}</span>
                    </li>
                  ))}
                </ol>
                <p className="mt-3 text-[12px] leading-relaxed text-faint">
                  {data.search.players_considered} players considered ·{" "}
                  {data.search.does_not_improve_the_need} do not improve this need ·{" "}
                  {data.search.no_skill_measured} have the skill unmeasured ·{" "}
                  {data.feasibility.trades_evaluated} trades evaluated against a budget of{" "}
                  {data.feasibility.budget}. Rejected:{" "}
                  {Object.entries(data.feasibility.rejected)
                    .filter(([, n]) => n > 0)
                    .map(([reason, n]) => `${n} because ${REJECTION_LABEL[reason] ?? reason}`)
                    .join("; ") || "none"}
                  .
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
