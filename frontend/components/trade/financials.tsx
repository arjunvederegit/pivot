"use client";

/**
 * R9.3A — the Trade Evaluator's money and its account of what was checked.
 *
 * Two panels, both laid out from the backend's `financial_analysis` (the browser decides nothing):
 *
 *  - `LegalityAssessment` — the verdict in the three words the engine can support (prohibited under a
 *    named rule / indeterminate / passes the rules Pivot checks), then what passed, what could not be
 *    verified and why, and the CBA restrictions Pivot does not check at all. "Passes" is never "legal".
 *  - `SalaryAndContracts` — per traded player (cap hit, base salary, cash, length, source), per team
 *    (salary out and in, the difference only when every salary is known, the subtotal with its label) and
 *    the contract commitments the deal creates, season by season.
 *
 * An unknown salary renders as unknown. A difference that needs one renders "not stated".
 */

import Link from "next/link";
import { playerFileLink } from "@/lib/investigation";
import clsx from "clsx";
import {
  VERDICT_WORD,
  basisLabel,
  contractLength,
  contractLengthShort,
  dollars,
  signedMillions,
  type AssessmentEntry,
  type TradeFinancialAnalysis,
  type TradeTeamFinancials,
} from "@/lib/financials";
import { formatDate, money } from "@/lib/format";
import { Badge, Explainer, Panel, SourceRail, Td, Th } from "@/components/ui";

const VERDICT_COLOR: Record<string, { text: string; badge: string; glyph: string }> = {
  prohibited: { text: "text-illegal", badge: "fail", glyph: "✕" },
  indeterminate: { text: "text-conditional", badge: "incomplete", glyph: "~" },
  passes_checked_rules: { text: "text-foreground", badge: "info", glyph: "✓" },
};

/* ------------------------------------------------------------- legality assessment */

function EntryList({ entries, empty }: { entries: AssessmentEntry[]; empty: string }) {
  if (entries.length === 0) return <p className="text-[12px] text-faint">{empty}</p>;
  return (
    <ul className="space-y-2">
      {entries.map((e, i) => (
        <li key={`${e.rule_code}-${e.team_id}-${i}`} className="text-[12.5px] leading-relaxed">
          <span className="font-medium text-foreground">{e.title}</span>
          <span className="text-faint"> · {e.team}</span>
          {e.confidence !== "high" && (
            <span className="ml-1.5 rounded-[3px] border border-conditional/40 px-1 py-px text-[10.5px] text-conditional">
              {e.confidence} confidence
            </span>
          )}
          <span className="block text-muted [overflow-wrap:anywhere]">{e.message}</span>
        </li>
      ))}
    </ul>
  );
}

export function LegalityAssessment({ analysis }: { analysis: TradeFinancialAnalysis }) {
  const a = analysis.legality_assessment;
  const style = VERDICT_COLOR[a.verdict] ?? VERDICT_COLOR.indeterminate;
  return (
    <section aria-labelledby="assessment-heading" className="rounded-xl border border-hairline bg-court/40 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="assessment-heading" className="eyebrow">
          What Pivot can and cannot say about legality
        </h3>
        <Badge status={style.badge}>{VERDICT_WORD[a.verdict]}</Badge>
      </div>
      <p className={clsx("mt-3 text-balance text-[15px] font-medium leading-snug", style.text)}>{a.headline}</p>

      <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Checks by outcome">
        <li>
          <Badge status="pass">{a.counts.checked_passed} passed</Badge>
        </li>
        <li>
          <Badge status={a.counts.failed ? "fail" : "info"}>{a.counts.failed} failed</Badge>
        </li>
        <li>
          <Badge status={a.counts.unverified ? "unavailable" : "info"}>{a.counts.unverified} not verified</Badge>
        </li>
        <li>
          <Badge status="unavailable">{a.unchecked_categories.length} categories not checked at all</Badge>
        </li>
      </ul>

      {a.indications.length > 0 && (
        <div className="mt-4 rounded-lg border border-conditional/40 bg-conditional/5 px-4 py-3">
          <p className="text-[12px] font-semibold uppercase tracking-wide text-conditional">
            Indication — not a verified failure
          </p>
          <ul className="mt-1.5 space-y-1.5 text-[12.5px] leading-relaxed text-muted">
            {a.indications.map((i, idx) => (
              <li key={idx}>
                <span className="font-medium text-foreground">
                  {i.title} · {i.team}.
                </span>{" "}
                {i.explanation}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[11.5px] text-faint">
            It rests on the known salaries only, which are a floor built from the current roster snapshot.
          </p>
        </div>
      )}

      <div className="mt-4 space-y-2.5">
        {a.failed.length > 0 && (
          <Explainer summary={`Failed (${a.failed.length})`}>
            <EntryList entries={a.failed} empty="Nothing failed." />
          </Explainer>
        )}
        <Explainer summary={`Checked and passed (${a.checked.length})`}>
          <EntryList entries={a.checked} empty="No rule has passed yet." />
        </Explainer>
        <Explainer summary={`Could not be verified (${a.unverified.length}) — and why`}>
          <EntryList entries={a.unverified} empty="Every implemented check ran." />
        </Explainer>
        <Explainer summary={`Restrictions Pivot does not check (${a.unchecked_categories.length})`}>
          <ul className="space-y-1.5">
            {a.unchecked_categories.map((c) => (
              <li key={c.category} className="text-[12.5px] leading-relaxed">
                <span className="font-medium text-foreground">{c.category}.</span> {c.note}
              </li>
            ))}
          </ul>
        </Explainer>
      </div>
      <p className="mt-4 border-t border-hairline pt-3 text-[12px] leading-relaxed text-faint">{a.statement}</p>
    </section>
  );
}

/* ---------------------------------------------------------------- salary comparison */

function SideLine({
  label,
  side,
}: {
  label: string;
  side: TradeTeamFinancials["outgoing"];
}) {
  return (
    <div className="min-w-0">
      <div className="eyebrow">{label}</div>
      {side.count === 0 ? (
        <div className="mt-1 text-[13px] text-faint">no players</div>
      ) : (
        <>
          <div className="stat-serif mt-1 text-[1.5rem] leading-none text-foreground">
            {side.complete ? money(side.cap_hit_total) : "unknown"}
          </div>
          <div className="mt-1.5 text-[12px] leading-snug text-muted [overflow-wrap:anywhere]">
            {side.players.join(", ")}
          </div>
          {!side.complete && (
            <div className="mt-1 text-[11.5px] leading-snug text-conditional">
              {side.known_cap_hit_total !== null ? `${money(side.known_cap_hit_total)} known · ` : ""}
              no cap hit on file for {side.unknown_players.join(", ")}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function SalaryAndContracts({
  analysis,
  teamNames,
}: {
  analysis: TradeFinancialAnalysis;
  teamNames: Record<string, string>;
}) {
  const teams = Object.values(analysis.teams);
  const basis = analysis.season_basis;
  const seasons = [
    ...new Set(teams.flatMap((t) => t.commitments_by_season.map((c) => c.season))),
  ].sort();
  const retrieved = analysis.players
    .map((p) => p.financials?.source.retrieved_at)
    .filter((d): d is string => Boolean(d))
    .sort()
    .pop();
  const sourceName = analysis.players.find((p) => p.financials?.source.name)?.financials?.source.name;

  return (
    <Panel
      title="Salary and contracts"
      subtitle={
        <>
          Cap hits for the {analysis.financial_season} season
          {basis.roster ? ` · roster ${basis.roster}${basis.roster_is_provisional ? " (provisional)" : ""}` : ""}
          {basis.statistics ? ` · performance evidence from ${basis.statistics}` : ""}
        </>
      }
    >
      <div className="space-y-6">
        {/* --------------------------------------------------- per team */}
        <div className={clsx("grid gap-4", teams.length >= 3 ? "xl:grid-cols-3" : "md:grid-cols-2")}>
          {teams.map((t) => (
            <div key={t.team_id} className="min-w-0 rounded-lg border border-hairline p-4">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="title-sm text-foreground">{t.abbreviation}</h3>
                <span className="text-[11.5px] text-faint">{teamNames[t.team_id] ?? t.name}</span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-4">
                <SideLine label="Sends out" side={t.outgoing} />
                <SideLine label="Takes in" side={t.incoming} />
              </div>
              <dl className="mt-4 space-y-1.5 border-t border-hairline pt-3 text-[12.5px]">
                <div className="flex items-baseline justify-between gap-3">
                  <dt className="text-muted">Net salary change</dt>
                  <dd className="numeral text-foreground" data-testid={`net-${t.abbreviation}`}>
                    {signedMillions(t.net_salary_change)}
                    {t.incoming_to_outgoing_ratio !== null && (
                      <span className="ml-1.5 text-[11.5px] text-faint">
                        ({Math.round(t.incoming_to_outgoing_ratio * 100)}% of outgoing)
                      </span>
                    )}
                  </dd>
                </div>
                <div className="flex items-baseline justify-between gap-3">
                  <dt className="text-muted">{t.subtotal_label} before → after</dt>
                  <dd className="numeral text-foreground">
                    {t.coverage_before?.complete ? "" : "≥ "}
                    {money(t.known_subtotal_before)} →{" "}
                    {t.known_subtotal_after === null ? "not stated" : `${t.coverage_after?.complete ? "" : "≥ "}${money(t.known_subtotal_after)}`}
                  </dd>
                </div>
                {t.coverage_before && !t.coverage_before.complete && (
                  <div className="text-[11.5px] leading-snug text-conditional">
                    {t.coverage_before.players_known} of {t.coverage_before.players_total} rostered players priced — a
                    floor, not a payroll. Apron position is withheld
                    {t.apron_status_at_least_after
                      ? `; the known salaries alone put ${t.abbreviation} above the ${t.apron_status_at_least_after.replaceAll("_", " ").replace("above ", "")} after the deal`
                      : ""}
                    .
                  </div>
                )}
              </dl>
              {t.net_salary_note && (
                <p className="mt-2 text-[11.5px] leading-snug text-muted">{t.net_salary_note}</p>
              )}
            </div>
          ))}
        </div>

        {/* ------------------------------------------------ player table */}
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse">
            <caption className="sr-only">Contracts of the players in this deal</caption>
            <thead>
              <tr className="border-b border-line">
                <Th>Player</Th>
                <Th>Route</Th>
                <Th numeric>Cap hit</Th>
                <Th numeric>Base salary</Th>
                <Th numeric>Total cash</Th>
                <Th>Contract</Th>
                <Th>Counts toward matching</Th>
              </tr>
            </thead>
            <tbody>
              {analysis.players.map((p) => {
                const f = p.financials;
                const routeFrom = teamNames[p.from_team_id] ?? "?";
                const routeTo = teamNames[p.to_team_id] ?? "?";
                const shown = f?.status === "priced";
                return (
                  <tr key={p.player_id} className="border-b border-hairline align-top">
                    <Td>
                      <Link
                        href={playerFileLink(p.player_id)}
                        className="font-medium text-foreground hover:text-signal"
                        title={`Open ${p.name} in the Player Explorer`}
                      >
                        {p.name}
                      </Link>
                    </Td>
                    <Td className="whitespace-nowrap text-[12px] text-muted">
                      {routeFrom} → {routeTo}
                    </Td>
                    <Td numeric>{shown ? dollars(f?.cap_hit) : <span className="text-unavail" title={f?.status_reason}>unknown</span>}</Td>
                    <Td numeric>{shown ? dollars(f?.base_salary) : "—"}</Td>
                    <Td numeric>{shown ? dollars(f?.total_cash) : "—"}</Td>
                    <Td>
                      <span className="text-[12px] text-muted" title={f?.contract_end.basis}>
                        {shown ? contractLength(f?.contract_end) : f?.status_reason ?? "unknown"}
                      </span>
                    </Td>
                    <Td>
                      <span className="text-[12px] text-muted" title={p.matching_class_basis ?? undefined}>
                        {p.matching_class === "standard"
                          ? p.contract_type_stated
                            ? "yes (type stated)"
                            : "yes — not a two-way deal (deduced)"
                          : p.matching_class === "two-way"
                            ? "no — two-way"
                            : "unverified"}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* ----------------------------------------------- commitments */}
        {seasons.length > 1 && (
          <div>
            <h3 className="title-sm text-foreground">Contract commitments by season</h3>
            <p className="mt-1 text-[12px] leading-relaxed text-muted">
              Cap hits the deal moves in each season, from the loaded contract years. A season shows “not stated” when
              a player&apos;s length or salary is unknown — an unknown is never counted as $0.
            </p>
            <div className="scroll-thin mt-2 overflow-x-auto">
              <table className="w-full min-w-[520px] border-collapse">
                <caption className="sr-only">Contract commitments the trade moves, by season and team</caption>
                <thead>
                  <tr className="border-b border-line">
                    <Th>Season</Th>
                    {teams.map((t) => (
                      <Th key={t.team_id} numeric>
                        {t.abbreviation} net
                      </Th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {seasons.map((season) => (
                    <tr key={season} className="border-b border-hairline">
                      <Td className="numeral text-[13px]">{season}</Td>
                      {teams.map((t) => {
                        const row = t.commitments_by_season.find((c) => c.season === season);
                        return (
                          <Td key={t.team_id} numeric>
                            <span
                              title={
                                row
                                  ? `out ${row.outgoing.complete ? money(row.outgoing.known_total) : `unknown (${row.outgoing.unknown_players.join(", ")})`} · in ${row.incoming.complete ? money(row.incoming.known_total) : `unknown (${row.incoming.unknown_players.join(", ")})`}`
                                  : undefined
                              }
                            >
                              {row ? signedMillions(row.net_known) : "—"}
                            </span>
                          </Td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <Explainer summary="Definitions and what is not on file">
          <p>{analysis.definitions}</p>
          <p className="mt-2">
            Cap, tax and apron thresholds shown elsewhere are the {analysis.thresholds.season} figures
            ({analysis.thresholds.source_name}); they are not applied to later seasons.
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {Object.entries(analysis.not_on_file).map(([k, text]) => (
              <li key={k}>{text}</li>
            ))}
          </ul>
        </Explainer>
        <SourceRail
          source={`${sourceName ?? "contract provider"} (${basisLabel(analysis.players.find((p) => p.financials?.salary_basis)?.financials?.salary_basis)})`}
          retrievedAt={retrieved ?? null}
        />
        {retrieved && <span className="sr-only">Retrieved {formatDate(retrieved)}</span>}
      </div>
    </Panel>
  );
}

export { contractLengthShort };
