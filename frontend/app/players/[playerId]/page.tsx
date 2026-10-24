"use client";

/**
 * Player detail — the scouting file.
 *
 * The header is the report's top sheet: who he is, the body and career facts, the next
 * step, and the headline line (league impact position and the latest season). Under it the
 * file opens in tabs, each one backed by data Pivot actually holds:
 *
 *  - Overview — the assessment: on-court impact placed in the league with its band, fit
 *    against the user's own team, and his role with the players nearest him;
 *  - Performance — the career arc strip and the season-by-season table;
 *  - Advanced — the measured skill profile, with every declared dimension Pivot cannot
 *    see listed rather than dropped;
 *  - Contract — the imported contract, or the honest notice that there is none.
 *
 * There is no shot-profile tab: Pivot has no shot-location data, so it draws none.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { money } from "@/lib/format";
import { playerIntelligenceSchema } from "@/lib/schemas";
import { teamVars } from "@/lib/teamIdentity";
import type { ArchetypeAssignment, ComparablePlayer, PlayerIntelligence } from "@/lib/types";
import {
  Badge,
  ButtonLink,
  ErrorState,
  PageHeader,
  Panel,
  Skeleton,
  SkeletonRows,
  SourceRail,
  Tabs,
  Td,
  Th,
  UnavailableNotice,
} from "@/components/ui";
import {
  CareerStrip,
  SeasonTable,
  latestRetrieval,
  type SeasonLine,
} from "@/components/players/career";
import { PlayerFileHeader, type PlayerRecord } from "@/components/players/file-header";
import type { SeasonContext } from "@/lib/types";
import {
  FitCard,
  ImpactPanel,
  RolePanel,
  SkillsPanel,
  UnmeasuredPanel,
  type PlayerImpact,
} from "@/components/players/profile";

interface PlayerDetail {
  /** R8.2: which season the team, the impact estimate and the statistics each belong to. */
  season_context?: SeasonContext;
  player: PlayerRecord;
  impact: PlayerImpact;
  archetype: ArchetypeAssignment | null;
  comparables: ComparablePlayer[];
}

interface PlayerStats {
  seasons: SeasonLine[];
  source: string;
}

interface PlayerContract {
  available: boolean;
  reason?: string;
  contract_type?: string;
  source_name?: string;
  source_date?: string;
  no_trade_clause?: boolean | null;
  years?: {
    season: string;
    salary: number;
    player_option: boolean | null;
    team_option: boolean | null;
  }[];
  note?: string;
}

type FileTab = "overview" | "performance" | "advanced" | "contract";

const FILE_TABS: { id: FileTab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "performance", label: "Performance" },
  { id: "advanced", label: "Advanced" },
  { id: "contract", label: "Contract" },
];

export default function PlayerPage({ params }: { params: Promise<{ playerId: string }> }) {
  const { playerId } = use(params);
  const { data, error } = useQuery({
    queryKey: ["player", playerId],
    queryFn: () => api.get<PlayerDetail>(`/players/${playerId}`),
  });
  const { data: stats, error: statsError } = useQuery({
    queryKey: ["player-stats", playerId],
    queryFn: () => api.get<PlayerStats>(`/players/${playerId}/stats`),
  });
  const { data: contract, error: contractError } = useQuery({
    queryKey: ["player-contract", playerId],
    queryFn: () => api.get<PlayerContract>(`/players/${playerId}/contract`),
  });
  const { data: intelligence, error: intelligenceError } = useQuery({
    queryKey: ["player-intelligence", playerId],
    queryFn: () =>
      api.get<PlayerIntelligence>(`/intelligence/players/${playerId}`, playerIntelligenceSchema),
  });
  const [now] = useState(() => Date.now());
  const [tab, setTab] = useState<FileTab>("overview");

  if (error) {
    // A 404 is an answer, not a failure: it gets a designed block, not an error banner.
    if (error instanceof ApiError && error.status === 404) {
      return <PlayerNotFound playerId={playerId} />;
    }
    return (
      <div className="space-y-5">
        <PageHeader
          eyebrow="Player file"
          title="Player file unavailable"
          lede="The player record did not load, so nothing on this file is shown."
        />
        <ErrorState
          message={`Could not load player: ${String(error)}`}
          action={
            <ButtonLink href="/player-explorer" size="sm">
              Browse the Player Explorer
            </ButtonLink>
          }
        />
      </div>
    );
  }
  if (!data) return <PlayerSkeleton />;

  const player = data.player;
  const team = player.current_team;
  const age = player.birth_date
    ? Math.floor((now - new Date(player.birth_date).getTime()) / (365.25 * 24 * 3600 * 1000))
    : null;
  const scored = data.impact.tei !== undefined && data.impact.tei !== null;
  const firstName = player.full_name.split(" ")[0] ?? player.full_name;
  const activeLabel = FILE_TABS.find((t) => t.id === tab)?.label ?? "Overview";

  return (
    <div style={teamVars(team?.abbreviation)}>
      <PlayerFileHeader
        player={player}
        impact={data.impact}
        archetype={data.archetype}
        age={age}
        seasonContext={data.season_context}
        seasons={stats?.seasons}
        seasonsSource={stats?.source}
        seasonsError={statsError}
      />

      {/* Arrow keys, Home and End move between tabs (the ARIA tabs pattern); the
          primitive handles clicks, this adds the keyboard half by delegation. */}
      <div className="mt-8" onKeyDown={(event) => moveTabFocus(event, tab, setTab)}>
        <Tabs
          ariaLabel="Player file sections"
          tabs={FILE_TABS}
          active={tab}
          onChange={(id) => setTab(id as FileTab)}
        />
      </div>

      {/* The Tabs primitive renders the tablist; this is its one panel, named after the
          selected tab. Keyed so the change of section reads as one quiet fade. */}
      <div
        key={tab}
        role="tabpanel"
        aria-label={activeLabel}
        tabIndex={0}
        className="fade-in mt-6 rounded-lg focus-visible:outline-offset-4"
      >
        {tab === "overview" && (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
            <ImpactPanel
              impact={data.impact}
              intelligence={intelligence}
              intelligenceError={intelligenceError}
            />
            <div className="min-w-0 space-y-4">
              <FitCard playerId={playerId} playerName={firstName} currentTeamId={team?.id ?? null} />
              <RolePanel
                archetype={data.archetype}
                intelligence={intelligence}
                comparables={data.comparables}
                scored={scored}
              />
            </div>
          </div>
        )}

        {tab === "performance" && (
          <Panel
            className="min-w-0"
            title="Season by season"
            subtitle="Per-game averages from the league dashboard, with advanced rates · oldest season first"
            actions={
              stats && stats.seasons.length > 0 ? (
                <span className="text-[12px] text-muted">
                  <span className="numeral text-foreground">{stats.seasons.length}</span> season
                  {stats.seasons.length === 1 ? "" : "s"} on file
                </span>
              ) : undefined
            }
          >
            {statsError ? (
              <ErrorState message={`Could not load season stats: ${String(statsError)}`} />
            ) : !stats ? (
              <SkeletonRows rows={3} height="h-9" />
            ) : stats.seasons.length === 0 ? (
              <UnavailableNotice reason="No season rows have been ingested for this player." />
            ) : (
              <div className="space-y-6">
                <CareerStrip seasons={stats.seasons} />
                <SeasonTable seasons={stats.seasons} />
                <SourceRail source={stats.source} retrievedAt={latestRetrieval(stats.seasons)} />
              </div>
            )}
          </Panel>
        )}

        {tab === "advanced" && (
          <div className="space-y-4">
            <SkillsPanel intelligence={intelligence} error={intelligenceError} />
            <UnmeasuredPanel intelligence={intelligence} />
          </div>
        )}

        {tab === "contract" && <ContractSection contract={contract} error={contractError} />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function moveTabFocus(
  event: React.KeyboardEvent<HTMLDivElement>,
  current: FileTab,
  setTab: (tab: FileTab) => void,
) {
  if (!(event.target instanceof HTMLElement) || event.target.getAttribute("role") !== "tab") return;
  const index = FILE_TABS.findIndex((t) => t.id === current);
  const last = FILE_TABS.length - 1;
  const next =
    event.key === "ArrowRight"
      ? (index + 1) % FILE_TABS.length
      : event.key === "ArrowLeft"
        ? (index - 1 + FILE_TABS.length) % FILE_TABS.length
        : event.key === "Home"
          ? 0
          : event.key === "End"
            ? last
            : null;
  if (next === null) return;
  event.preventDefault();
  setTab(FILE_TABS[next].id);
  event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus();
}

/**
 * The answer to an id that matches no player. It keeps the file header's shape — an
 * empty frame where the portrait sits, the requested id where the name would be — so the
 * reader sees which file is missing, then the two ways to find the right one.
 */
function PlayerNotFound({ playerId }: { playerId: string }) {
  return (
    <div>
      <PageHeader eyebrow="Player file" title="Player not found" />
      <section className="panel max-w-5xl" aria-labelledby="player-missing-title">
        <div className="grid lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="min-w-0 p-5 md:p-7">
            <div className="flex items-center gap-4 md:gap-6">
              <div
                aria-hidden
                className="flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-lg border border-dashed border-line bg-panel2 text-lg text-faint sm:h-[104px] sm:w-[104px]"
              >
                —
              </div>
              <div className="min-w-0">
                <h2 id="player-missing-title" className="eyebrow">
                  No player file at this id
                </h2>
                <p className="mt-2 text-[13px] text-muted [overflow-wrap:anywhere]">
                  <code>{playerId}</code>
                </p>
              </div>
            </div>
            <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-foreground md:mt-6">
              No player in Pivot&apos;s directory has this id. The link may come from an older
              import, or the address was cut short.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <ButtonLink href="/player-explorer" variant="primary" size="sm">
                Browse the Player Explorer
              </ButtonLink>
              <ButtonLink href="/player-explorer?focus=search" size="sm">
                Search players by name
              </ButtonLink>
            </div>
          </div>

          <div className="min-w-0 border-t border-hairline p-5 md:p-7 lg:border-l lg:border-t-0">
            <h3 className="eyebrow">Other ways in</h3>
            <ul className="mt-3 space-y-2.5 text-[13px] leading-relaxed text-muted">
              <li className="hidden lg:block">
                Press{" "}
                <kbd className="rounded border border-line px-1 text-[11px] text-foreground">/</kbd>{" "}
                to search players and teams from the sidebar.
              </li>
              <li className="lg:hidden">Open the menu to search players and teams by name.</li>
              <li>
                <Link href="/team-outlook" className="text-signal underline underline-offset-2">
                  Teams
                </Link>{" "}
                — open any team for its full roster.
              </li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}

/**
 * The Contract tab: the imported contract, or the notice that there is none — and in
 * either case where else in Pivot this one record is read, so the reader knows what an
 * import changes. Nothing here is estimated.
 */
function ContractSection({
  contract,
  error,
}: {
  contract: PlayerContract | undefined;
  error: unknown;
}) {
  const available = contract?.available === true;
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
      <ContractPanel contract={contract} error={error} />
      <Panel as="aside" title="Where this contract is read" className="min-w-0">
        <ul className="-mt-2.5 divide-y divide-hairline text-[13px] leading-relaxed text-muted">
          <li className="py-2.5">
            <Link href="/trade-evaluator" className="font-medium text-foreground hover:text-signal">
              Trade Evaluator
            </Link>{" "}
            — salary matching and apron rules for any deal he is in.
          </li>
          <li className="py-2.5">
            <Link href="/salary-cap-center" className="font-medium text-foreground hover:text-signal">
              Cap Center
            </Link>{" "}
            — his team&apos;s payroll by season, options and expiring money.
          </li>
        </ul>
        <p className="mt-1 border-t border-hairline pt-3 text-[12px] leading-relaxed text-faint">
          {!contract
            ? "Checking for an imported contract…"
            : available
              ? "Both read this same imported snapshot."
              : "Until contracts are imported, both report these checks as unavailable — Pivot never estimates a salary."}
        </p>
      </Panel>
    </div>
  );
}

function ContractPanel({
  contract,
  error,
}: {
  contract: PlayerContract | undefined;
  error: unknown;
}) {
  return (
    <Panel
      title="Contract"
      subtitle="Salary by season, options and trade restrictions — from an imported contract snapshot"
      className="min-w-0"
    >
      {error ? (
        <ErrorState message={`Could not load the contract: ${String(error)}`} />
      ) : !contract ? (
        <SkeletonRows rows={3} height="h-8" />
      ) : contract.available ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {contract.contract_type && <Badge status="info">{contract.contract_type}</Badge>}
            {contract.no_trade_clause && <Badge status="warning">no-trade clause</Badge>}
          </div>
          <table className="w-full">
            <thead>
              <tr className="border-b border-line">
                <Th>Season</Th>
                <Th numeric>Salary</Th>
                <Th>Option</Th>
              </tr>
            </thead>
            <tbody>
              {contract.years?.map((year) => (
                <tr key={year.season} className="border-b border-hairline">
                  <Td className="numeral whitespace-nowrap text-[14px]">{year.season}</Td>
                  <Td numeric>{money(year.salary)}</Td>
                  <Td className="text-xs text-muted">
                    {year.player_option ? "player" : year.team_option ? "team" : "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
          {contract.note && (
            <p className="text-[11.5px] leading-relaxed text-faint">{contract.note}</p>
          )}
          <SourceRail
            source={contract.source_name ?? "user-imported contract snapshot"}
            retrievedAt={contract.source_date ?? null}
          />
        </div>
      ) : (
        <UnavailableNotice
          reason={contract.reason ?? "Contract data has not been imported."}
          steps={
            <p className="text-[12px] leading-relaxed text-muted">
              Until a contract provider is configured, salary-matching rules report{" "}
              <em>unavailable</em> everywhere — see{" "}
              <Link href="/data-health" className="text-signal underline">
                Data Health
              </Link>{" "}
              for the exact next step.
            </p>
          }
        />
      )}
    </Panel>
  );
}

function PlayerSkeleton() {
  return (
    <div role="status" aria-label="Loading player">
      <div className="panel overflow-hidden">
        <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-4 gap-y-4 p-5 md:grid-cols-[152px_minmax(0,1fr)] md:gap-x-8 md:p-7">
          <Skeleton className="h-[88px] w-[88px] rounded-lg md:row-span-3 md:h-[152px] md:w-[152px] md:rounded-xl" />
          <div className="space-y-2 self-center md:self-start md:pt-1">
            <Skeleton className="h-5 w-44" />
            <Skeleton className="h-3.5 w-20" />
          </div>
          <Skeleton className="col-span-2 h-12 w-80 max-w-full md:col-span-1 md:col-start-2 md:mt-3" />
          <div className="col-span-2 flex flex-wrap gap-6 md:col-span-1 md:col-start-2 md:mt-5 md:self-end">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-16" />
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-px border-t border-hairline bg-hairline sm:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className={
                i === 0 ? "col-span-2 bg-panel px-5 py-4 sm:col-span-4 xl:col-span-1" : "bg-panel px-5 py-4"
              }
            >
              <Skeleton className="h-3 w-20" />
              <Skeleton className="mt-3 h-8 w-16" />
            </div>
          ))}
        </div>
      </div>
      <Skeleton className="mt-8 h-10 w-96 max-w-full" />
      <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <Skeleton className="h-96 rounded-xl" />
        <div className="space-y-4">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-52 rounded-xl" />
        </div>
      </div>
    </div>
  );
}
