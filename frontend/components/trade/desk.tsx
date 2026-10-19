"use client";

/**
 * The deal desk under the builder: the live rules check on the left, and on the right the
 * controls that act on the deal (Run it) with the receipt of everything changing hands.
 *
 * Every rules result is the backend's (`POST /trades/validate`); the browser only lays it
 * out. A check that could not run says which rules and why — "incomplete" is never
 * rounded up to "legal".
 */

import Link from "next/link";
import { LEGALITY_EXPLAIN, LEGALITY_LABEL } from "@/lib/format";
import type { LegalityResponse, PickMove, PlayerMove, RosterPlayer, Team } from "@/lib/types";
import { KeyFrame } from "@/components/court";
import { PlayerPhoto } from "@/components/media";
import {
  Badge,
  Button,
  ButtonLink,
  Panel,
  Skeleton,
  SkeletonRows,
  SourceRail,
  UnavailableNotice,
} from "@/components/ui";
import { LegalityAssessment } from "@/components/trade/financials";
import { LEGALITY_VISUAL, RULE_STATUS_WORD, RuleItem } from "@/components/trade/verdict";

/* -------------------------------------------------------------- rules check */

export function RulesCheck({
  validation,
  validating,
  hasMoves,
  className,
}: {
  validation: LegalityResponse | null;
  validating: boolean;
  hasMoves: boolean;
  className?: string;
}) {
  const status = validation?.overall_status;
  const visual = status ? LEGALITY_VISUAL[status] : null;
  const blocked = validation?.rule_results.filter((r) => r.status === "unavailable") ?? [];
  const tally = validation
    ? (["pass", "fail", "warning", "unavailable"] as const)
        .map((s) => ({ s, n: validation.rule_results.filter((r) => r.status === s).length }))
        .filter((x) => x.n > 0)
    : [];

  return (
    <Panel
      className={className}
      title="Live rules check"
      subtitle={
        validation
          ? `Cap year ${validation.league_year} · every result below comes from the backend rules engine, not the browser.`
          : "The check runs automatically the moment a player crosses the lane."
      }
      accent={visual?.color ?? "var(--signal)"}
      actions={
        validating ? (
          <span className="flex items-center gap-1.5 text-[12px] font-medium text-signal">
            <span className="pulse-live h-1.5 w-1.5 rounded-full bg-signal" aria-hidden />
            Checking
          </span>
        ) : null
      }
    >
      {!validation ? (
        validating || hasMoves ? (
          <div className="space-y-3">
            <Skeleton className="h-28" />
            <SkeletonRows rows={3} height="h-12" />
          </div>
        ) : (
          <div className="court-grid rounded-xl border border-dashed border-line px-6 py-10 text-center">
            <p className="font-serif text-[1.5rem] leading-tight text-foreground">No check to run yet</p>
            <p className="mx-auto mt-1.5 max-w-md text-[13.5px] leading-relaxed text-muted">
              Move at least one player between the teams and Pivot validates the deal against the
              2023 CBA rules it can verify.
            </p>
          </div>
        )
      ) : (
        <div className="space-y-5">
          {/* --------------------------------------------- headline verdict */}
          <KeyFrame accent={visual?.color} className="rounded-xl border border-hairline bg-court/50 px-5 pb-5 pt-6">
            <div className="flex items-start gap-3">
              <span
                aria-hidden
                className="stat-serif mt-0.5 text-[2.2rem] leading-none"
                style={{ color: visual?.color }}
              >
                {visual?.glyph}
              </span>
              <div className="min-w-0">
                {/* The dash stays on the line of the word before it: a wrapped title that
                    opened its second line with "— data missing" read as a list item. */}
                <h3 className={`title-lg text-balance ${visual?.text ?? "text-foreground"}`}>
                  {LEGALITY_LABEL[status ?? "not_evaluated"].replace(/ —/g, "\u00a0—")}
                </h3>
                <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-muted">
                  {LEGALITY_EXPLAIN[status ?? "not_evaluated"]}
                </p>
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
            {status !== "verified_legal" && (
              <p className="mt-3 text-[12.5px] leading-relaxed text-faint">
                Pivot never reports a deal as legal while a required check is missing — an
                incomplete check stays incomplete.
              </p>
            )}
          </KeyFrame>

          {/* ------------------------------ R9.3A: what was checked, failed, unverified */}
          {validation.financial_analysis && <LegalityAssessment analysis={validation.financial_analysis} />}

          {/* ------------------------------------- what could not be checked */}
          {!validation.contract_provider_configured && (
            <UnavailableNotice
              reason={
                <>
                  Contract data isn&apos;t imported, so{" "}
                  {blocked.length > 0 ? (
                    <>
                      {blocked.length} check{blocked.length === 1 ? "" : "s"} could not run:{" "}
                      <span className="text-[12.5px] font-medium text-foreground [overflow-wrap:anywhere]">
                        {[...new Set(blocked.map((r) => r.rule_code))].join(", ")}
                      </span>
                      .
                    </>
                  ) : (
                    "salary-matching checks could not run."
                  )}{" "}
                  Everything that depends on salary — matching bands, aprons and payroll — stays
                  unavailable rather than estimated.
                </>
              }
              steps={
                <ButtonLink href="/salary-cap-center" size="sm" variant="secondary">
                  Open the Salary-Cap Center
                </ButtonLink>
              }
            />
          )}

          {/* -------------------------------------------------- rule details */}
          <div>
            <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="title-sm text-foreground">Rule by rule</h3>
              <span className="text-[12px] text-faint">Open a rule for its calculation</span>
            </div>
            <ul className="scroll-thin relative max-h-[24rem] space-y-1.5 overflow-y-auto pr-1">
              {validation.rule_results.map((rule, i) => (
                <RuleItem
                  key={`${rule.rule_code}-${i}`}
                  rule={rule}
                  teamAbbr={rule.team_id ? validation.teams[rule.team_id]?.abbreviation : undefined}
                />
              ))}
            </ul>
          </div>

          <SourceRail
            source={`Rules engine · 2023 CBA · cap parameters from ${validation.cap_parameters_source}`}
          />
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ run it */

export function RunIt({
  tradeName,
  setTradeName,
  canAct,
  evaluating,
  saving,
  onEvaluate,
  onSave,
  onCopy,
  onMemo,
  memoPending,
  evaluated,
}: {
  tradeName: string;
  setTradeName: (value: string) => void;
  canAct: boolean;
  evaluating: boolean;
  saving: boolean;
  onEvaluate: () => void;
  onSave: () => void;
  onCopy: () => void;
  onMemo: () => void;
  memoPending: boolean;
  /** An evaluation of the current deal is on the page. */
  evaluated: boolean;
}) {
  return (
    <Panel title="Run it" subtitle="Evaluate now, then keep the deal for comparison." accent="var(--signal)">
      <div className="space-y-3">
        <div>
          <label htmlFor="deal-name" className="eyebrow">
            Deal name
          </label>
          <input
            id="deal-name"
            value={tradeName}
            onChange={(e) => setTradeName(e.target.value)}
            placeholder="Name this deal…"
            className="mt-2 w-full rounded-lg border border-line bg-panel2 px-3 py-2 text-[14px] text-foreground transition-colors placeholder:text-faint hover:border-[#4a4a50] focus:border-signal/60"
          />
        </div>
        <Button
          variant="primary"
          className="w-full"
          disabled={!canAct || evaluating}
          onClick={onEvaluate}
        >
          {evaluating ? "Evaluating…" : "Evaluate this deal"}
        </Button>
        {evaluated && (
          <a
            href="#deal-evaluation"
            className="flex items-center justify-center gap-1.5 text-[13px] font-medium text-signal hover:text-foreground"
          >
            Read the evaluation <span aria-hidden>↓</span>
          </a>
        )}
        <div className="flex gap-2">
          <Button className="flex-1" disabled={!canAct || saving} onClick={onSave}>
            {saving ? "Saving…" : "Save deal"}
          </Button>
          <Button className="flex-1" disabled={!canAct} onClick={onCopy}>
            Copy share link
          </Button>
        </div>
        <Button className="w-full" disabled={!canAct || memoPending} onClick={onMemo}>
          {memoPending ? "Building memo…" : "Open decision memo"}
        </Button>
        <div className="space-y-2 border-t border-hairline pt-3 text-[12px] leading-snug text-faint">
          {!canAct && <p className="text-muted">Move at least one player between teams to enable these actions.</p>}
          <p>
            The memo is one page: the recommendation, what changes on the floor, fit, cost, the
            rules, comparable completed trades, and everything the analysis could not establish.
          </p>
          <p>
            Saved deals line up in{" "}
            <Link href="/strategy-lab" className="text-signal underline-offset-2 hover:underline">
              Strategy Lab
            </Link>{" "}
            so you can rank them under your own priorities.
          </p>
        </div>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ ledger */

/** The transaction receipt: every asset that changes hands, in plain language. */
export function DealLedger({
  teams,
  playerMoves,
  picks,
  playerIndex,
}: {
  teams: Team[];
  playerMoves: PlayerMove[];
  picks: PickMove[];
  playerIndex: Record<string, RosterPlayer>;
}) {
  const abbr = (id: string) => teams.find((t) => t.id === id)?.abbreviation ?? "—";
  if (playerMoves.length === 0 && picks.length === 0) {
    return (
      <Panel title="Deal ledger" subtitle="Nothing is moving yet.">
        <p className="text-[13px] leading-relaxed text-muted">
          Send a player across the lane and every asset in the deal is listed here, with the team it
          leaves and the team it joins.
        </p>
      </Panel>
    );
  }
  return (
    <Panel
      title="Deal ledger"
      subtitle={`${playerMoves.length} player${playerMoves.length === 1 ? "" : "s"}${
        picks.length > 0 ? ` · ${picks.length} pick${picks.length === 1 ? "" : "s"}` : ""
      } changing hands`}
    >
      <ul className="-my-1 divide-y divide-hairline">
        {playerMoves.map((move) => {
          const player = playerIndex[move.player_id];
          return (
            <li key={move.player_id} className="flex items-center gap-2.5 py-2">
              <PlayerPhoto
                nbaPlayerId={player?.nba_player_id}
                name={player?.name ?? "Player"}
                size={26}
              />
              <span className="min-w-0 flex-1 truncate text-[13.5px] text-foreground">
                {player?.name ?? "Player"}
              </span>
              <span className="numeral flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12px] font-medium text-muted">
                {abbr(move.from_team_id)}
                <span aria-hidden className="text-signal">
                  →
                </span>
                {abbr(move.to_team_id)}
              </span>
            </li>
          );
        })}
        {picks.map((pick, i) => (
          <li key={`pick-${i}`} className="flex items-center gap-2.5 py-2">
            <span
              aria-hidden
              className="numeral flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border border-dashed border-line text-[10px] text-muted"
            >
              R{pick.round_number}
            </span>
            <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-[13.5px] text-foreground">
              <span className="whitespace-nowrap">
                {pick.draft_year} round {pick.round_number}
              </span>
              <Badge status="unavailable">hypothetical</Badge>
            </span>
            <span className="numeral flex shrink-0 items-center gap-1.5 whitespace-nowrap text-[12px] font-medium text-muted">
              {abbr(pick.from_team_id)}
              <span aria-hidden className="text-signal">
                →
              </span>
              {abbr(pick.to_team_id)}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
