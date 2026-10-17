"use client";

/**
 * Saved deal report.
 *
 * Read in the order a front office reads a deal, in the same language as the Trade
 * Evaluator: every team BEFORE the deal, the PROPOSED MOVE, and AFTER — the verdict (or the
 * refusal), the projected wins change on one shared axis, the roster after, legality and
 * fit. Then whether the deal can happen at all, then one team's full analysis at a time
 * with the same tabs the evaluator uses, plus the rules audit trail.
 *
 * Two presentation rules specific to this page:
 * - Player indices are placed in the league, like everywhere else. `GET /trades/{id}`
 *   re-scores the saved assets with the ACTIVE model on every read (it persists nothing
 *   unless asked), so the incoming/outgoing `tei` are today's served values and belong to
 *   today's reference population. The raw index is printed beside each percentile.
 * - Projected wins are never a bare median: every wins figure carries its 10th–90th
 *   percentile band.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Link from "next/link";
import { use, useState } from "react";
import { api } from "@/lib/api";
import { comparablesResponseSchema, tradeDetailSchema } from "@/lib/schemas";
import { LEGALITY_EXPLAIN, LEGALITY_LABEL, count, formatDate } from "@/lib/format";
import { teamIdentity, teamVars } from "@/lib/teamIdentity";
import type { ComparablesResponse, RuleResult, TeamEvaluation, TradeDetail } from "@/lib/types";
import { KeyFrame } from "@/components/court";
import { TeamLogo } from "@/components/media";
import { PrecedentPanel } from "@/components/precedent";
import { TeamPerspective } from "@/components/trade/perspective";
import { SectionHead } from "@/components/trade/section-head";
import { ConsequenceSheet, type SheetSide } from "@/components/trade/sheet";
import { LEGALITY_VISUAL, RULE_STATUS_WORD, RuleItem } from "@/components/trade/verdict";
import {
  Badge,
  ButtonLink,
  ErrorState,
  PageHeader,
  Skeleton,
  SourceRail,
  Tabs,
  UnavailableNotice,
} from "@/components/ui";

const REPORT_LINK =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-panel2 px-3 py-1.5 text-[13px] text-foreground transition-colors hover:border-[#3a433f] hover:bg-panel3";

/** Every side that is not scored gets "No decision score" — never a verdict word. */
const notScored = (ev: TeamEvaluation) => ev.decision_status !== "scored";

export default function TradeReportPage({ params }: { params: Promise<{ tradeId: string }> }) {
  const { tradeId } = use(params);
  const { data: trade, error } = useQuery({
    queryKey: ["trade", tradeId],
    queryFn: () => api.get<TradeDetail>(`/trades/${tradeId}`, tradeDetailSchema),
  });
  const [activeTeam, setActiveTeam] = useState<string | null>(null);

  if (error) return <ErrorState message={`Could not load this deal: ${String(error)}`} />;
  if (!trade) return <TradeSkeleton />;

  const teamId = activeTeam ?? trade.teams[0]?.team_id;
  const evaluation: TeamEvaluation | undefined = trade.evaluations[teamId];
  const activeTeamMeta = trade.teams.find((team) => team.team_id === teamId);
  const identity = teamIdentity(activeTeamMeta?.abbreviation);
  const visual = LEGALITY_VISUAL[trade.legality.overall_status] ?? LEGALITY_VISUAL.not_evaluated;
  const evaluatedAt =
    trade.teams.map((t) => trade.evaluations[t.team_id]).find(Boolean)?.evaluated_at ?? null;
  const abbreviations = Object.fromEntries(trade.teams.map((t) => [t.team_id, t.abbreviation]));
  const abbr = (id: string) => abbreviations[id] ?? "—";

  const sides: SheetSide[] = trade.teams.map((team) => {
    const ev = trade.evaluations[team.team_id];
    const evaluatedTei = (playerId: string | null, list: TeamEvaluation["incoming"] | undefined) => {
      const hit = playerId ? list?.find((p) => p.player_id === playerId) : undefined;
      return hit ? hit.tei : undefined;
    };
    const toAsset = (asset: TradeDetail["assets"][number], i: number, direction: "in" | "out") =>
      asset.asset_type === "player" && asset.player_id
        ? {
            key: `${asset.player_id}-${i}`,
            kind: "player" as const,
            playerId: asset.player_id,
            label: asset.player_name ?? "Player",
            tei: evaluatedTei(asset.player_id, direction === "in" ? ev?.incoming : ev?.outgoing),
            counterpart: abbr(direction === "in" ? asset.from_team_id : asset.to_team_id),
            href: `/players/${asset.player_id}`,
          }
        : {
            key: `pick-${asset.draft_year}-${asset.round_number}-${i}`,
            kind: "pick" as const,
            label: `${asset.draft_year} round ${asset.round_number}`,
            detail: asset.protections,
            counterpart: abbr(direction === "in" ? asset.from_team_id : asset.to_team_id),
            hypothetical: asset.is_hypothetical,
            round: asset.round_number,
          };
    return {
      teamId: team.team_id,
      abbreviation: team.abbreviation,
      name: team.name,
      evaluation: ev,
      legality: ev?.legality ?? trade.legality.teams[team.team_id],
      receives: trade.assets
        .filter((a) => a.to_team_id === team.team_id)
        .map((a, i) => toAsset(a, i, "in")),
      sends: trade.assets
        .filter((a) => a.from_team_id === team.team_id)
        .map((a, i) => toAsset(a, i, "out")),
    };
  });

  const teamRules = trade.legality.rule_results.filter(
    (rule) => !rule.team_id || rule.team_id === teamId,
  );

  function selectAndReveal(id: string) {
    setActiveTeam(id);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById("team-perspective")
      ?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  return (
    <div className="pb-6">
      <PageHeader
        eyebrow={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span>Saved deal</span>
            <span aria-hidden className="text-faint">
              ·
            </span>
            <span>created {formatDate(trade.created_at)}</span>
          </span>
        }
        title={trade.name}
        lede={trade.notes ?? undefined}
        accent={visual.color}
        actions={
          // PageHeader's action slot sizes to its content; capping the width here lets the
          // three actions wrap on a phone instead of pushing the page sideways.
          <div className="flex max-w-[21rem] flex-wrap gap-2 sm:max-w-none">
            <ButtonLink href={`/trade-evaluator?load=${trade.id}`} variant="primary" size="sm">
              Clone &amp; modify
            </ButtonLink>
            <a
              href={`/api/v1/trades/${trade.id}/report`}
              target="_blank"
              rel="noreferrer"
              className={REPORT_LINK}
            >
              Report (Markdown)
              <span aria-hidden className="text-[11px] text-faint">
                ↗
              </span>
            </a>
            <a
              href={`/api/v1/trades/${trade.id}/report?format=html`}
              target="_blank"
              rel="noreferrer"
              className={REPORT_LINK}
            >
              Report (print)
              <span aria-hidden className="text-[11px] text-faint">
                ↗
              </span>
            </a>
          </div>
        }
        meta={
          <>
            <span className="flex flex-wrap items-center gap-1.5">
              {trade.teams.map((team, index) => (
                <span key={team.team_id} className="flex items-center gap-1.5">
                  {index > 0 && (
                    <span aria-hidden className="text-[12px] text-faint">
                      ↔
                    </span>
                  )}
                  <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-panel2 py-0.5 pl-0.5 pr-2.5">
                    <TeamLogo abbreviation={team.abbreviation} size={18} decorative />
                    <span className="numeral text-[12px] leading-none">{team.abbreviation}</span>
                  </span>
                </span>
              ))}
            </span>
            <Badge status={trade.legality.overall_status}>
              {LEGALITY_LABEL[trade.legality.overall_status] ?? trade.legality.overall_status}
            </Badge>
            <span className="text-[12px] text-faint">
              {count(trade.assets.length, "asset")} · evaluated {formatDate(evaluatedAt)}
            </span>
          </>
        }
      />

      <div className="space-y-12">
        {/* ------------------------------------------- before → move → after */}
        <section aria-labelledby="report-sheet-title">
          <SectionHead
            id="report-sheet-title"
            eyebrow={`Re-scored ${formatDate(evaluatedAt)} with the active model`}
            title="What the deal does to each team"
            aside="Where each team stands before the deal, what it gives and gets, and where the model projects it after — every side on the same wins scale, each player placed in today's league with his raw index beside it."
          />
          <ConsequenceSheet
            sides={sides}
            isRefused={notScored}
            active={teamId}
            onSelect={selectAndReveal}
            showIndex
            linkPlayers
          />
        </section>

        {/* ------------------------------------------------------ rules check */}
        <section aria-labelledby="report-rules-title">
          <SectionHead
            id="report-rules-title"
            eyebrow={`Rules check · league year ${trade.legality.league_year}`}
            title="Can this deal happen?"
            aside="The rules are re-run when this report is read. Each team's rule-by-rule audit is in its full analysis below."
          />
          <RulesVerdict trade={trade} checkedAt={evaluatedAt} />
        </section>

        {/* ----------------------------------------------- team perspective */}
        <section
          id="team-perspective"
          aria-labelledby="report-perspective-title"
          className="panel overflow-hidden"
          style={teamVars(activeTeamMeta?.abbreviation)}
        >
          <div className="px-5 pt-5">
            <div className="eyebrow flex items-center gap-2">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: identity.bright }} />
              Team perspective · every number scored from this side
            </div>
            <h2
              id="report-perspective-title"
              className="mt-2 font-serif text-[2rem] leading-none text-foreground"
            >
              {activeTeamMeta?.name ?? identity.name}, in full
            </h2>
            {evaluation && (
              <p className="mt-2 text-[12.5px] text-muted">
                Evaluation run {formatDate(evaluation.evaluated_at)} for the saved assets with the
                active model · players placed in the league
              </p>
            )}
            <div className="mt-4">
              <Tabs
                ariaLabel="Team perspective"
                active={teamId}
                onChange={setActiveTeam}
                tabs={trade.teams.map((team) => ({ id: team.team_id, label: team.name }))}
              />
            </div>
          </div>
          <div className="p-5">
            {!evaluation ? (
              <UnavailableNotice reason="No evaluation exists for this team on this deal." />
            ) : (
              <TeamPerspective
                key={teamId}
                evaluation={evaluation}
                abbreviation={identity.abbreviation}
                refused={notScored(evaluation)}
                teamAbbreviations={abbreviations}
                refusalClosing={
                  evaluation.decision_status === "suppressed_illegal"
                    ? "Clone the deal, fix the rule failures above and evaluate again."
                    : ""
                }
                precedent={<ReportPrecedent tradeId={trade.id} focalTeamId={teamId} />}
                rules={
                  <RuleAudit
                    rules={teamRules}
                    abbreviations={abbreviations}
                    capSource={
                      notScored(evaluation) ? undefined : trade.legality.cap_parameters_source
                    }
                  />
                }
              />
            )}
            {evaluation && (
              <div className="mt-2">
                <SourceRail
                  source="Pivot evaluation engine · saved assets re-scored for this report"
                  retrievedAt={evaluation.evaluated_at}
                />
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/* --------------------------------------------------------- rules verdict */

function RulesVerdict({ trade, checkedAt }: { trade: TradeDetail; checkedAt: string | null }) {
  const status = trade.legality.overall_status;
  const visual = LEGALITY_VISUAL[status] ?? LEGALITY_VISUAL.not_evaluated;
  const tally = (["pass", "fail", "warning", "unavailable"] as const)
    .map((s) => ({ s, n: trade.legality.rule_results.filter((r) => r.status === s).length }))
    .filter((x) => x.n > 0);
  return (
    <div className="panel" style={{ "--edge": visual.color } as React.CSSProperties}>
      <KeyFrame accent={visual.color} className="grid gap-x-10 gap-y-5 px-5 pb-5 pt-7 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <div className="flex items-start gap-3">
            <span aria-hidden className="stat-serif mt-0.5 text-[2.2rem] leading-none" style={{ color: visual.color }}>
              {visual.glyph}
            </span>
            <div className="min-w-0">
              {/* The dash stays on the line of the word before it: a wrapped title that opened
                  its second line with "— data missing" read as a list item. */}
              <h3 className={clsx("title-lg text-balance", visual.text)}>
                {(LEGALITY_LABEL[status] ?? status).replace(/ —/g, " —")}
              </h3>
              <p className="mt-2 text-[14px] leading-relaxed text-muted">{LEGALITY_EXPLAIN[status]}</p>
            </div>
          </div>
          {tally.length > 0 && (
            <ul className="mt-4 flex flex-wrap gap-1.5" aria-label="Rule results">
              {tally.map(({ s, n }) => (
                <li key={s}>
                  <Badge status={s}>
                    {n} {RULE_STATUS_WORD[s]}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="min-w-0 self-end">
          {!trade.legality.contract_provider_configured && (
            <p className="rounded-lg border border-dashed border-unavail/50 bg-unavail/[0.06] px-4 py-3 text-[13px] leading-relaxed text-muted">
              Contract data isn&apos;t configured, so salary-matching and apron rules could not be
              verified. This deal is <strong className="text-foreground">not</strong> being called
              legal.{" "}
              <Link href="/data-health" className="text-signal underline-offset-2 hover:underline">
                What&apos;s missing
              </Link>
            </p>
          )}
          {/* Not `retrievedAt={trade.created_at}`: that printed the deal's save date as the
              cap source's "updated" date. The rules are re-run when the report is read, so
              the date that belongs here is when this check ran. */}
          <SourceRail
            source={trade.legality.cap_parameters_source}
            extra={
              checkedAt ? (
                <>
                  <span aria-hidden>·</span>
                  <span>rules run {formatDate(checkedAt)}</span>
                </>
              ) : undefined
            }
          />
        </div>
      </KeyFrame>
    </div>
  );
}

/* ------------------------------------------------------------- precedent */

function ReportPrecedent({ tradeId, focalTeamId }: { tradeId: string; focalTeamId: string }) {
  const query = useQuery({
    queryKey: ["trade-comparables", tradeId, focalTeamId],
    queryFn: () =>
      api.get<ComparablesResponse>(
        `/trades/${tradeId}/comparables?focal_team_id=${focalTeamId}&k=5`,
        comparablesResponseSchema,
      ),
    staleTime: 300_000,
  });
  return (
    <PrecedentPanel
      data={query.data}
      loading={query.isPending}
      error={query.error ? String(query.error) : undefined}
    />
  );
}

/* ------------------------------------------------------------ rule audit */

function RuleAudit({
  rules,
  abbreviations,
  capSource,
}: {
  rules: RuleResult[];
  abbreviations: Record<string, string>;
  capSource?: string;
}) {
  if (rules.length === 0) {
    return <UnavailableNotice reason="No rules were run for this team." />;
  }
  return (
    <div className="rounded-xl border border-hairline bg-panel2/40 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="title-sm text-foreground">Rule audit trail</h3>
        <span className="text-[12px] text-faint">
          {count(rules.length, "rule")} · open a rule for its calculation
        </span>
      </div>
      <ul className="mt-3 space-y-1.5">
        {rules.map((rule, i) => (
          <RuleItem
            key={`${rule.rule_code}-${i}`}
            rule={rule}
            teamAbbr={rule.team_id ? abbreviations[rule.team_id] : undefined}
          />
        ))}
      </ul>
      {capSource && (
        <SourceRail
          source={capSource}
          extra={
            <Link href="/methodology#rules" className="text-signal underline-offset-2 hover:underline">
              rule coverage
            </Link>
          }
        />
      )}
    </div>
  );
}

/* --------------------------------------------------------------- skeleton */

function TradeSkeleton() {
  return (
    <div className="space-y-6" role="status" aria-label="Loading deal">
      <div className="space-y-3 border-b border-hairline pb-6">
        <Skeleton className="h-3 w-48" />
        <Skeleton className="h-12 w-96 max-w-full" />
        <Skeleton className="h-6 w-64 max-w-full" />
      </div>
      <Skeleton className="h-10 w-80 max-w-full" />
      <Skeleton className="h-[30rem] rounded-xl" />
      <Skeleton className="h-48 rounded-xl" />
      <Skeleton className="h-96 rounded-xl" />
    </div>
  );
}
