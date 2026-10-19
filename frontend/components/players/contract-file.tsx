"use client";

/**
 * R9.3A — the player file's Contract tab, read from the same assembled financial record Cap Center and the Trade
 * Evaluator read (`GET /players/{id}/contract` -> `financials`). It shows the cap hit for the financial season beside
 * base salary and cash, the contract years on file with their source and retrieval date, and — equally — what is
 * not on file. Nothing is estimated; a missing contract is stated as missing, never as $0.
 */

import Link from "next/link";
import { dollars, contractLength, type PlayerFinancialsRecord } from "@/lib/financials";
import { formatDate } from "@/lib/format";
import { tradeEvaluatorLink } from "@/lib/investigation";
import {
  Badge,
  Explainer,
  Panel,
  SkeletonRows,
  SourceRail,
  Td,
  Th,
  UnavailableNotice,
  ErrorState,
} from "@/components/ui";

export interface PlayerContractPayload {
  available: boolean;
  reason?: string;
  financial_season?: string;
  roster_season?: string;
  current_team?: { id: string; abbreviation: string; name: string } | null;
  financials?: PlayerFinancialsRecord;
  not_on_file?: Record<string, string>;
  note?: string;
}

export function ContractFile({
  contract,
  error,
  playerId,
  statsSeason,
}: {
  contract: PlayerContractPayload | undefined;
  error: unknown;
  playerId: string;
  statsSeason: string | null;
}) {
  const f = contract?.financials;
  const team = contract?.current_team ?? null;
  const tradeLink = tradeEvaluatorLink({ playerId, teamId: team?.id ?? null, statsSeason });
  const differs = Boolean(statsSeason && contract?.financial_season && statsSeason !== contract.financial_season);

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
      <Panel
        title="Contract"
        subtitle={
          contract?.financial_season
            ? `${contract.financial_season} cap hit and the contract years on file — from BALLDONTLIE contract rows`
            : "Cap hit and the contract years on file"
        }
        className="min-w-0"
      >
        {error ? (
          <ErrorState message={`Could not load the contract: ${String(error)}`} />
        ) : !contract ? (
          <SkeletonRows rows={4} height="h-8" />
        ) : f && f.status === "priced" ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-hairline bg-hairline sm:grid-cols-4">
              {[
                ["Cap hit", dollars(f.cap_hit), `${f.financial_season} · counts against the cap`],
                ["Base salary", dollars(f.base_salary), "a different quantity"],
                ["Total cash", dollars(f.total_cash), "a different quantity"],
                ["Contract", contractLength(f.contract_end), f.contract_end.status === "through_horizon" ? "may run longer" : ""],
              ].map(([label, value, note]) => (
                <div key={label} className="min-w-0 bg-panel px-4 py-3">
                  <dt className="eyebrow">{label}</dt>
                  <dd className="numeral mt-1.5 text-[15px] leading-snug text-foreground [overflow-wrap:anywhere]">{value}</dd>
                  {note && <dd className="mt-1 text-[11.5px] text-faint">{note}</dd>}
                </div>
              ))}
            </dl>
            <table className="w-full">
              <caption className="sr-only">Contract years on file</caption>
              <thead>
                <tr className="border-b border-line">
                  <Th>Season</Th>
                  <Th numeric>Cap hit</Th>
                  <Th numeric>Base salary</Th>
                  <Th numeric>Total cash</Th>
                </tr>
              </thead>
              <tbody>
                {f.seasons.map((y) => (
                  <tr key={y.season} className="border-b border-hairline">
                    <Td className="numeral whitespace-nowrap text-[14px]">
                      {y.season}
                      {y.season === f.financial_season && <span className="ml-2 text-[11px] text-signal">financial season</span>}
                    </Td>
                    <Td numeric>{dollars(y.cap_hit)}</Td>
                    <Td numeric>{dollars(y.base_salary)}</Td>
                    <Td numeric>{dollars(y.total_cash)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[12px] leading-relaxed text-muted">{f.contract_end.basis}.</p>
            {f.not_two_way && (
              <p className="text-[12px] leading-relaxed text-muted">
                <Badge status="derived" className="mr-1.5">deduced</Badge>
                {f.not_two_way_basis}. The source does not state a contract type.
              </p>
            )}
            {differs && (
              <p className="text-[12px] leading-relaxed text-faint">
                Seasons differ: this is the {contract.financial_season} contract; the performance evidence in the other
                tabs is from {statsSeason}.
              </p>
            )}
            <Explainer summary="What is not on file">
              <ul className="list-disc space-y-1 pl-5">
                {Object.values(contract.not_on_file ?? {}).map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </Explainer>
            <SourceRail
              source={`${f.source.name ?? "contract provider"} (cap-hit basis)`}
              retrievedAt={f.source.retrieved_at}
            />
            {f.source.retrieved_at && <span className="sr-only">Retrieved {formatDate(f.source.retrieved_at)}</span>}
          </div>
        ) : (
          <UnavailableNotice
            reason={
              f?.status_reason
                ? `${f.status_reason.charAt(0).toUpperCase()}${f.status_reason.slice(1)}. He is not a $0 contract — his salary is unknown.`
                : (contract.reason ?? "No contract is on file for this player.")
            }
            steps={
              <p className="text-[12px] leading-relaxed text-muted">
                See{" "}
                <Link href="/data-health" className="text-signal underline">
                  Data Health
                </Link>{" "}
                for contract coverage.
              </p>
            }
          />
        )}
      </Panel>

      <Panel as="aside" title="Where this contract is read" className="min-w-0">
        <ul className="-mt-2.5 divide-y divide-hairline text-[13px] leading-relaxed text-muted">
          <li className="py-2.5">
            {tradeLink ? (
              <Link href={tradeLink} className="font-medium text-foreground hover:text-signal">
                Trade Evaluator
              </Link>
            ) : (
              <span className="font-medium text-foreground">Trade Evaluator</span>
            )}{" "}
            — evaluate him in a deal; his cap hit, contract years and the salary checks come from this same record.
          </li>
          <li className="py-2.5">
            <Link
              href={team ? `/salary-cap-center?team=${team.id}` : "/salary-cap-center"}
              className="font-medium text-foreground hover:text-signal"
            >
              Cap Center
            </Link>{" "}
            — {team ? `${team.abbreviation}'s` : "his team's"} contracts by season, with the players Pivot cannot price.
          </li>
        </ul>
        <p className="mt-1 border-t border-hairline pt-3 text-[12px] leading-relaxed text-faint">
          {team
            ? `Team: ${team.name} on the ${contract?.roster_season ?? "current"} roster snapshot.`
            : "No current team on the roster snapshot."}
        </p>
      </Panel>
    </div>
  );
}
