"use client";

/**
 * Salary-Cap Center — the multi-season payroll picture for one franchise.
 *
 * DATA HONESTY: contract data is opt-in and manually imported, so the endpoint usually
 * answers available=false. That is the DEFAULT state of this module, and it is designed
 * as a cap sheet in its own right: the real roster laid out as a contract ledger with
 * every salary slot visibly withheld, the totals a front office reads first (payroll, cap
 * space, apron position) shown as withheld rather than estimated, and the exact import
 * steps — reconciled with what the backend says is configured. Never a placeholder
 * number. When contracts exist we render exactly what the backend provides — payroll by
 * season, per-contract rows, league cap parameters — and never derive cap space or apron
 * position on the client.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useHydrated } from "@/lib/hydrated";
import { dataHealthSchema } from "@/lib/schemas";
import { count, formatDate, money } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type {
  CapOutlookAvailable,
  CapOutlookResponse,
  DataHealth,
  RosterPlayer,
  RosterResponse,
  Team,
} from "@/lib/types";
import { SalaryTimeline } from "@/components/charts";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { CommandText } from "@/components/reference/format";
import { HATCH, Withheld } from "@/components/reference/withheld";
import {
  Badge,
  ButtonLink,
  EmptyState,
  ErrorState,
  Explainer,
  PageHeader,
  Panel,
  SectionLabel,
  Skeleton,
  SkeletonRows,
  SourceRail,
  Td,
  Th,
  UnavailableNotice,
} from "@/components/ui";

interface ImportStep {
  title: string;
  body: string;
  code: string;
  /** Steps the backend's `contract_provider_configured` already proves are done. */
  provedByConfiguredProvider: boolean;
}

const IMPORT_STEPS: ImportStep[] = [
  {
    title: "Save the contracts page",
    body: "Download the Basketball-Reference player-contracts page and save the HTML snapshot into the repository at",
    code: "data/imports/contracts/players.html",
    provedByConfiguredProvider: true,
  },
  {
    title: "Point the app at the snapshot",
    body: "Set the contract provider in your environment file (.env)",
    code: "CONTRACT_DATA_PROVIDER=bbref_snapshot",
    provedByConfiguredProvider: true,
  },
  {
    title: "Run the sync",
    body: "Parse and load the contracts alongside the rest of the data",
    code: "make sync-data",
    provedByConfiguredProvider: false,
  },
];

/** What changes when contracts land — stated against what the product does today. */
const UNLOCKS: { feature: string; today: string; after: string }[] = [
  {
    feature: "Salary matching in the rules check",
    today: "Not run — a deal can reach “Incomplete check”, never “Passes”",
    after: "A verified pass or fail for every salary-matching band",
  },
  {
    feature: "Payroll by season",
    today: "Withheld — no total is drawn",
    after: "Committed money per league year, options and expiring deals",
  },
  {
    feature: "Apron and tax position",
    today: "Unverified — known salaries clear no line",
    after: "Checked by the rules engine against the league year's thresholds",
  },
  {
    feature: "Contract value in evaluations",
    today: "Excluded; the other weights are re-scaled",
    after: "Salary paid against on-court value, scored in every deal",
  },
];

export default function SalaryCapCenterPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <Skeleton className="h-40" />
          <Skeleton className="h-28" />
          <Skeleton className="h-[420px]" />
        </div>
      }
    >
      <SalaryCapCenter />
    </Suspense>
  );
}

function SalaryCapCenter() {
  const searchParams = useSearchParams();
  const teamParam = searchParams.get("team");

  const { data: teamsData } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    staleTime: 300_000,
  });
  const { data: healthData } = useQuery({
    queryKey: ["data-health"],
    queryFn: () => api.get<DataHealth>("/data-health", dataHealthSchema),
    staleTime: 120_000,
  });

  // The app shell hydrates before this page and warms the shared `["teams"]` cache, so
  // the team picker rendered real buttons on the first client render while the server
  // HTML held skeletons. Reading through the hydration gate keeps the two trees identical
  // for that render; the picker fills in on the next commit.
  const hydrated = useHydrated();
  const teams = hydrated ? teamsData : undefined;
  const health = hydrated ? healthData : undefined;

  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Seed selection once teams load: honor ?team=<id>, else the first team. Safe to do in
  // render because `teams` above is hydration-gated, so this cannot fire during hydration.
  const [seeded, setSeeded] = useState(false);
  if (!seeded && teams && teams.length > 0) {
    setSeeded(true);
    if (teamParam && teams.some((team) => team.id === teamParam)) setSelectedId(teamParam);
    else setSelectedId(teams[0].id);
  }

  const selectedTeam = teams?.find((team) => team.id === selectedId) ?? null;
  const identity = teamIdentity(selectedTeam?.abbreviation);

  const {
    data: outlook,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["cap-outlook", selectedId],
    queryFn: () => api.get<CapOutlookResponse>(`/teams/${selectedId}/cap-outlook`),
    enabled: !!selectedId,
  });

  // Roster gives player_id -> nba_player_id so contract rows can show real photos, and it
  // is the ledger the default state draws: the real players whose salaries are not on file.
  const { data: roster } = useQuery({
    queryKey: ["roster", selectedId],
    queryFn: () => api.get<RosterResponse>(`/teams/${selectedId}/roster`),
    enabled: !!selectedId,
    staleTime: 300_000,
  });
  const nbaIdByPlayer = useMemo(() => {
    const map: Record<string, number> = {};
    for (const player of roster?.roster ?? []) map[player.player_id] = player.nba_player_id;
    return map;
  }, [roster]);

  const contractsConfigured = health?.providers?.contracts?.configured ?? false;
  const capLeagueYear =
    (outlook && outlook.available ? outlook.cap_league_year : null) ??
    health?.cap_league_year ??
    null;

  return (
    <div>
      <PageHeader
        eyebrow="Salary-Cap Center"
        title="Payroll & commitments"
        lede="The multi-season money picture for one franchise, built strictly from imported contract data. Nothing on this page is estimated — a missing salary stays missing."
        accent={identity.bright}
        meta={
          <>
            {capLeagueYear && <Badge status="info">league year {capLeagueYear}</Badge>}
            <Badge status={contractsConfigured ? "pass" : "unavailable"}>
              contract provider {contractsConfigured ? "configured" : "not configured"}
            </Badge>
          </>
        }
        actions={
          selectedTeam ? (
            <>
              <ButtonLink href={`/team-outlook/${selectedTeam.id}`} size="sm" variant="secondary">
                Team Outlook
              </ButtonLink>
              <ButtonLink
                href={`/trade-evaluator?team=${selectedTeam.id}`}
                size="sm"
                variant="secondary"
              >
                Trade Evaluator
              </ButtonLink>
            </>
          ) : undefined
        }
      />

      <div className="space-y-8">
        <FranchisePicker teams={teams} selectedId={selectedId} onSelect={setSelectedId} />

        {!selectedId || isLoading ? (
          <div className="space-y-6" role="status" aria-label="Loading the cap outlook">
            <div className="panel overflow-hidden">
              <div className="flex items-center gap-4 border-b border-hairline px-5 py-5">
                <Skeleton className="h-12 w-12 shrink-0" />
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-3 w-40 max-w-full" />
                  <Skeleton className="h-7 w-64 max-w-full" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-px bg-hairline lg:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="bg-panel px-5 py-4">
                    <Skeleton className="h-3 w-20" />
                    <Skeleton className="mt-3 h-8 w-16" />
                  </div>
                ))}
              </div>
              <div className="p-5">
                <SkeletonRows rows={6} height="h-9" />
              </div>
            </div>
          </div>
        ) : error ? (
          <ErrorState
            message={`Could not load the cap outlook: ${(error as Error).message}`}
            action={
              <ButtonLink href="/data-health" size="sm">
                Check Data Health
              </ButtonLink>
            }
          />
        ) : !outlook ? null : outlook.available ? (
          <CapOutlookView
            outlook={outlook}
            team={selectedTeam}
            roster={roster?.roster}
            nbaIdByPlayer={nbaIdByPlayer}
            accent={identity.bright}
            teamName={selectedTeam?.full_name ?? "This team"}
          />
        ) : (
          <ContractsNotImported
            reason={outlook.reason}
            providerConfigured={outlook.contract_provider_configured}
            team={selectedTeam}
            roster={roster?.roster}
            capLeagueYear={capLeagueYear}
          />
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------- franchise picker */

function FranchisePicker({
  teams,
  selectedId,
  onSelect,
}: {
  teams: Team[] | undefined;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section aria-labelledby="team-strip-heading">
      <SectionLabel
        aside={
          teams ? (
            <span className="text-[12px] text-faint">
              {count(teams.length, "team")}
              <span className="lg:hidden"> · scroll for more</span>
            </span>
          ) : undefined
        }
      >
        <span id="team-strip-heading">Choose a franchise</span>
      </SectionLabel>
      {!teams ? (
        <div className="flex gap-1.5 overflow-hidden lg:grid lg:grid-cols-15">
          {Array.from({ length: 15 }).map((_, index) => (
            <Skeleton key={index} className="h-[60px] w-[62px] shrink-0 lg:w-auto" />
          ))}
        </div>
      ) : (
        <div className="relative">
          <div
            role="group"
            aria-label="Choose a franchise"
            // The padding is room for the focus ring: a scrolling strip clips anything
            // drawn outside it, and the ring sits 2px outside each button.
            className="scroll-thin -mx-1.5 flex snap-x scroll-px-1.5 gap-1.5 overflow-x-auto px-1.5 pb-2 pt-1 lg:mx-0 lg:grid lg:grid-cols-15 lg:overflow-visible lg:px-0 lg:pb-0 lg:pt-0"
          >
            {teams.map((team) => {
              const active = team.id === selectedId;
              const colors = teamIdentity(team.abbreviation);
              return (
                <button
                  key={team.id}
                  type="button"
                  onClick={() => onSelect(team.id)}
                  // Chrome leaves a partly visible item where it is on focus, so tabbing
                  // along the phone strip landed on buttons out of view.
                  onFocus={(event) =>
                    event.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest" })
                  }
                  aria-pressed={active}
                  aria-label={team.full_name}
                  title={team.full_name}
                  className={clsx(
                    "flex w-[62px] shrink-0 snap-start flex-col items-center gap-1.5 rounded-lg border px-1 pb-1.5 pt-2 transition-colors lg:w-auto",
                    active
                      ? "bg-panel2"
                      : "border-hairline bg-panel hover:border-line hover:bg-panel2",
                  )}
                  style={
                    active
                      ? { borderColor: `color-mix(in srgb, ${colors.bright} 70%, var(--hairline))` }
                      : undefined
                  }
                >
                  <TeamLogo abbreviation={team.abbreviation} name={team.full_name} size={24} decorative />
                  <span
                    className={clsx(
                      "numeral text-[11.5px] leading-none tracking-[0.02em]",
                      active ? "text-foreground" : "text-muted",
                    )}
                  >
                    {team.abbreviation}
                  </span>
                </button>
              );
            })}
          </div>
          <span
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-court via-court/70 to-transparent lg:hidden"
          />
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------- the team header */

function LedgerHeader({
  team,
  leagueYear,
  status,
}: {
  team: Team | null;
  leagueYear: string | null;
  status: React.ReactNode;
}) {
  const identity = teamIdentity(team?.abbreviation);
  return (
    <header
      className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-hairline px-5 py-5"
      style={{ background: `linear-gradient(105deg, ${identity.primary}30, transparent 62%)` }}
    >
      <div className="flex min-w-0 items-center gap-4">
        <TeamLogo abbreviation={team?.abbreviation} name={team?.full_name} size={48} />
        <div className="min-w-0">
          <div className="eyebrow">
            Contract ledger{leagueYear ? ` · ${leagueYear}` : ""}
          </div>
          <h2 className="mt-1.5 text-balance font-serif text-[clamp(1.65rem,1.25rem+1vw,2.15rem)] leading-[1.05] text-foreground">
            {team?.full_name ?? "—"}
          </h2>
          <p className="mt-1 text-[12.5px] text-muted">
            {[team?.conference, team?.division].filter(Boolean).join(" · ") || "Conference not on file"}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">{status}</div>
    </header>
  );
}

/**
 * One slot per rostered player. A filled slot is a salary on file for the cap league
 * year; a hatched slot is a salary that is not. Slots are equal width on purpose — they
 * count contracts, they do not size money.
 */
function CoverageSlots({ players }: { players: RosterPlayer[] }) {
  const known = players.filter((p) => p.salary !== null).length;
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="eyebrow">Rostered contracts on file</span>
        <span className="text-[12px] text-muted">
          <span className="numeral text-[13px] text-foreground">{known}</span> of {players.length} ·{" "}
          {players.length - known} not on file
        </span>
      </div>
      {/* Surnames at every width. Initials on phones and tablets ("JT", "JB") made the
          slots a puzzle; three columns on a phone leave room for the longest surname. */}
      <ul className="mt-3 grid grid-cols-3 gap-1.5 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-[repeat(auto-fit,minmax(5.5rem,1fr))]">
        {players.map((player) => {
          const onFile = player.salary !== null;
          return (
            <li
              key={player.player_id}
              title={`${player.name}: ${onFile ? money(player.salary) : "no salary on file — not estimated"}`}
              className={clsx(
                "flex min-w-0 flex-col items-center justify-center gap-1 rounded-md border px-1.5 py-1.5",
                onFile ? "border-signal/40 bg-signal/10" : "border-dashed border-unavail/45",
              )}
              style={onFile ? undefined : HATCH}
            >
              <span aria-hidden className="max-w-full truncate text-[12px] leading-tight text-muted">
                {player.name.split(" ").slice(1).join(" ") || player.name}
              </span>
              <span aria-hidden className="numeral text-[11px] font-medium leading-none text-faint">
                {onFile ? money(player.salary) : "—"}
              </span>
              <span className="sr-only">
                {player.name}: {onFile ? money(player.salary) : "no salary on file"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* --------------------------------------------------------- default state */

/**
 * The intentional default: contracts are opt-in, so this screen is a cap sheet with its
 * money withheld rather than an error. The totals a front office reads first come first
 * — and say "withheld" — then the ledger of real players beside the steps that fill it.
 */
function ContractsNotImported({
  reason,
  providerConfigured,
  team,
  roster,
  capLeagueYear,
}: {
  reason: string;
  providerConfigured: boolean;
  team: Team | null;
  roster: RosterPlayer[] | undefined;
  capLeagueYear: string | null;
}) {
  const identity = teamIdentity(team?.abbreviation);
  const teamName = team?.full_name ?? "this team";
  const onFile = roster ? roster.filter((p) => p.salary !== null).length : null;

  const totals: { label: string; value?: string; note: string; withheld: boolean }[] = [
    {
      label: "Payroll",
      note: capLeagueYear ? `${capLeagueYear} · not imported` : "not imported",
      withheld: true,
    },
    { label: "Cap space", note: "withheld — never estimated", withheld: true },
    { label: "Apron position", note: "withheld — unverified", withheld: true },
    {
      label: "Contracts on file",
      value: roster && onFile !== null ? `${onFile}/${roster.length}` : undefined,
      note: "rostered players",
      withheld: false,
    },
  ];

  return (
    <div className="space-y-8">
      <Panel accent={identity.bright} padded={false} className="overflow-hidden">
        <LedgerHeader
          team={team}
          leagueYear={capLeagueYear}
          status={
            providerConfigured ? (
              <>
                <Badge status="info">provider configured</Badge>
                <Badge status="unavailable">no contracts matched yet</Badge>
              </>
            ) : (
              <Badge status="unavailable">contracts not imported</Badge>
            )
          }
        />

        {/* The four numbers a cap sheet opens with — three of them withheld, said plainly. */}
        <dl className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline lg:grid-cols-4">
          {totals.map((cell) => (
            <div key={cell.label} className="min-w-0 bg-panel px-5 py-4" style={cell.withheld ? HATCH : undefined}>
              <dt className="eyebrow">{cell.label}</dt>
              <dd
                className={clsx(
                  "stat-serif mt-2 text-[2.1rem] leading-none",
                  cell.value ? "text-foreground" : "text-unavail",
                )}
              >
                {cell.value ?? "—"}
              </dd>
              <dd className="mt-2 text-[12px] leading-snug text-muted">{cell.note}</dd>
            </div>
          ))}
        </dl>

        <div className="border-b border-hairline px-5 py-4">
          {/* The backend sentence carries unbroken tokens (a repo path, an env assignment)
              that are wider than a phone column; let them break rather than spill out of
              the notice frame. */}
          <UnavailableNotice
            reason={
              <span className="[overflow-wrap:anywhere]">
                <CommandText text={reason} />
              </span>
            }
          />
        </div>

        {/* Steps first in reading order (they are what a person does next); on wide
            screens the ledger takes the wider left column beside them. */}
        <div className="grid lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <div className="min-w-0 border-b border-hairline px-5 py-6 lg:col-start-2 lg:row-start-1 lg:border-b-0 lg:border-l">
            <ImportPath providerConfigured={providerConfigured} teamName={teamName} />
          </div>
          <div className="min-w-0 lg:col-start-1 lg:row-start-1">
            {roster && roster.length > 0 ? (
              <CapSheet roster={roster} capLeagueYear={capLeagueYear} teamName={teamName} />
            ) : (
              <div className="p-5">
                <SkeletonRows rows={8} height="h-9" />
              </div>
            )}
          </div>
        </div>
      </Panel>

      <Unlocks />
    </div>
  );
}

/**
 * The roster as a cap sheet: who is on the books, with the money column empty because
 * no contract is imported. A salary that is on file (a partial snapshot) is shown as
 * served; a missing one is a hatched "not on file" slot — never a zero, never summed.
 */
function CapSheet({
  roster,
  capLeagueYear,
  teamName,
}: {
  roster: RosterPlayer[];
  capLeagueYear: string | null;
  teamName: string;
}) {
  const onFile = roster.filter((p) => p.salary !== null).length;
  const year = capLeagueYear ?? "Cap year";
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pb-3 pt-6">
        <h3 className="title-md text-foreground">Cap sheet</h3>
        <span className="text-[12.5px] text-muted">
          <span className="numeral text-foreground">{onFile}</span> of {count(roster.length, "salary", "salaries")}{" "}
          on file
        </span>
      </div>
      <table className="w-full border-collapse">
        <caption className="sr-only">
          {teamName} rostered players with their {year} salary as imported. Not on file means no
          contract is imported for that player — it is not a zero.
        </caption>
        <thead>
          <tr className="border-y border-hairline bg-court/40">
            <Th className="w-12 pl-5">No.</Th>
            <Th>Player</Th>
            <Th className="hidden sm:table-cell">Pos</Th>
            <Th numeric className="hidden md:table-cell">
              Age
            </Th>
            <Th numeric className="hidden xl:table-cell">
              Exp
            </Th>
            <Th numeric className="pr-5">
              {year} salary
            </Th>
          </tr>
        </thead>
        <tbody>
          {roster.map((player) => (
            <tr key={player.player_id} className="border-b border-hairline transition-colors hover:bg-panel2/60">
              <Td className="numeral pl-5 text-[12.5px] font-medium text-faint">
                {player.jersey_number ?? "—"}
              </Td>
              <Td className="py-2">
                <Link
                  href={`/players/${player.player_id}`}
                  className="font-medium text-foreground transition-colors hover:text-signal"
                >
                  {player.name}
                </Link>
                <span className="block text-[12px] text-faint sm:hidden">{player.position ?? "—"}</span>
              </Td>
              <Td className="hidden text-muted sm:table-cell">{player.position ?? "—"}</Td>
              <Td numeric className="hidden text-muted md:table-cell">
                {player.age != null ? Math.floor(player.age) : "—"}
              </Td>
              <Td numeric className="hidden text-muted xl:table-cell">
                {player.years_experience ?? "—"}
              </Td>
              <Td numeric className="pr-5">
                {player.salary !== null ? (
                  <span className="numeral text-foreground">{money(player.salary)}</span>
                ) : (
                  <Withheld title={`${player.name}: no salary on file — not estimated`}>not on file</Withheld>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 py-4">
        <span className="text-[13px] font-semibold text-foreground">
          Committed {capLeagueYear ?? "this league year"}
        </span>
        <Withheld>withheld — partial salaries are never summed</Withheld>
      </div>
    </div>
  );
}

function ImportPath({ providerConfigured, teamName }: { providerConfigured: boolean; teamName: string }) {
  const remaining = IMPORT_STEPS.filter((step) => !(providerConfigured && step.provedByConfiguredProvider));
  return (
    <div className="min-w-0">
      <div className="eyebrow">
        Contracts · {providerConfigured ? "configured, not synced" : "not imported"}
      </div>
      {/* Serif, one step under the ledger's h2 team name. */}
      <h3 className="mt-2 font-serif text-[1.9rem] leading-[1.05] text-foreground">
        {providerConfigured ? "One sync away." : "One import away."}
      </h3>
      <p className="mt-3 text-[14px] leading-relaxed text-muted">
        Pivot will not guess a salary. Until contracts are loaded, the payroll view for {teamName}{" "}
        stays empty on purpose — no placeholder totals, no modelled cap space, no invented apron
        position.
      </p>

      <h4 className="title-sm mt-6 text-foreground">
        {remaining.length === 1 ? "One step left: run the sync" : "Import contracts in three steps"}
      </h4>
      <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
        {providerConfigured
          ? "The backend reports a contract source as configured and readable, so the first two steps are already done."
          : "Run these once; every cap feature turns on together."}
      </p>
      <ol className="mt-3.5 space-y-2.5">
        {IMPORT_STEPS.map((step, index) => {
          const done = providerConfigured && step.provedByConfiguredProvider;
          const next = !done && remaining[0] === step;
          return <ImportStepRow key={step.title} step={step} index={index} done={done} next={next} />;
        })}
      </ol>
      <p className="mt-4 text-[12px] leading-relaxed text-faint">
        The parser reads only the local snapshot you saved — Pivot does not scrape
        Basketball-Reference at runtime. Provenance for every imported row shows up in the
        contracts table and in Data Health.
      </p>
    </div>
  );
}

function ImportStepRow({
  step,
  index,
  done,
  next,
}: {
  step: ImportStep;
  index: number;
  done: boolean;
  next: boolean;
}) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard
      ?.writeText(step.code)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => undefined);
  }
  return (
    <li
      className={clsx(
        "flex gap-3 rounded-xl border p-3.5",
        next ? "border-signal/40 bg-signal/[0.05]" : "border-hairline bg-panel2/40",
        done && "opacity-75",
      )}
    >
      <span
        aria-hidden
        className={clsx(
          "numeral flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[13px] leading-none",
          done
            ? "border-legal/50 bg-legal/10 text-legal"
            : next
              ? "border-volt bg-volt text-ink"
              : "border-line text-muted",
        )}
      >
        {done ? "✓" : index + 1}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1">
          <p className="title-sm text-foreground">{step.title}</p>
          {done && <Badge status="pass">done · provider configured</Badge>}
          {next && <Badge status="running">next</Badge>}
        </div>
        <p className="mt-0.5 text-[13px] leading-relaxed text-muted">
          {step.body}
          {/* A finished step keeps its path or setting in the sentence (the body ends on
              "…into the repository at"), just without the copy affordance. */}
          {done && (
            <>
              {" "}
              <code className="text-[12px] text-muted [overflow-wrap:anywhere]">{step.code}</code>
            </>
          )}
        </p>
        {!done && (
          <div className="mt-2.5 flex items-stretch overflow-hidden rounded-lg border border-hairline bg-court">
            <code className="min-w-0 flex-1 px-3 py-2 text-[12px] leading-relaxed text-foreground [overflow-wrap:anywhere]">
              <Breakable text={step.code} />
            </code>
            <button
              type="button"
              onClick={copy}
              aria-label={`Copy ${step.code}`}
              // The row clips its children (rounded frame), so the focus ring is drawn
              // inside the button rather than outside it, where it would be cut off.
              className="shrink-0 border-l border-hairline px-3 text-[12px] font-medium text-muted transition-colors hover:bg-panel2 hover:text-foreground focus-visible:-outline-offset-2"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <span className="sr-only" aria-live="polite">
              {copied ? `${step.code} copied` : ""}
            </span>
          </div>
        )}
      </div>
    </li>
  );
}

/**
 * A path or setting that may wrap in a narrow column: it breaks after a "/", "=" or "_"
 * (where a reader expects a break) rather than mid-word. Copy still copies `step.code`.
 */
function Breakable({ text }: { text: string }) {
  const parts = text.split(/(?<=[/=_])/);
  return (
    <>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {i < parts.length - 1 && <wbr />}
        </span>
      ))}
    </>
  );
}

/** Today, and after the sync — what each cap feature does now and what the import changes. */
function Unlocks() {
  return (
    <section aria-labelledby="unlocks-heading" className="panel overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 border-b border-hairline px-5 py-5">
        <div className="min-w-0">
          <div className="eyebrow">Today, and after the sync</div>
          <h2 id="unlocks-heading" className="mt-2 font-serif text-[1.85rem] leading-[1.05] text-foreground">
            What the import switches on
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ButtonLink href="/trade-evaluator" size="sm" variant="secondary">
            Build a deal anyway
          </ButtonLink>
          <ButtonLink href="/data-health" size="sm" variant="ghost">
            Data Health
          </ButtonLink>
        </div>
      </div>
      <div
        aria-hidden
        className="hidden grid-cols-[minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1fr)] gap-6 border-b border-hairline bg-court/40 px-5 py-2.5 md:grid"
      >
        <span className="eyebrow">Capability</span>
        <span className="eyebrow">Today</span>
        <span className="eyebrow">After the import</span>
      </div>
      <ul className="divide-y divide-hairline">
        {UNLOCKS.map((unlock) => (
          <li
            key={unlock.feature}
            className="grid gap-x-6 gap-y-2.5 px-5 py-4 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1fr)]"
          >
            <p className="title-sm text-foreground">{unlock.feature}</p>
            <p className="flex gap-2.5 text-[13px] leading-snug text-muted">
              <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-unavail" />
              <span>
                <span className="eyebrow mb-1 block md:sr-only">Today</span>
                {unlock.today}
              </span>
            </p>
            <p className="flex gap-2.5 text-[13px] leading-snug text-foreground">
              <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-signal" />
              <span>
                <span className="eyebrow mb-1 block md:sr-only">After the import</span>
                {unlock.after}
              </span>
            </p>
          </li>
        ))}
      </ul>
      <p className="border-t border-hairline px-5 py-3.5 text-[12px] leading-relaxed text-faint">
        Every other module keeps working on real NBA data in the meantime.
      </p>
    </section>
  );
}

/* ------------------------------------------------------- available state */

function CapOutlookView({
  outlook,
  team,
  roster,
  nbaIdByPlayer,
  accent,
  teamName,
}: {
  outlook: CapOutlookAvailable;
  team: Team | null;
  roster: RosterPlayer[] | undefined;
  nbaIdByPlayer: Record<string, number>;
  accent: string;
  teamName: string;
}) {
  const seasons = outlook.seasons.map((season) => season.season);
  const missing = outlook.roster_size - outlook.players_with_contracts;
  const params = outlook.cap_parameters;
  const sourceName = outlook.players.find((player) => player.source_name)?.source_name ?? null;
  const sourceDate = outlook.players.find((player) => player.source_date)?.source_date ?? null;
  const firstSeason = outlook.seasons[0];

  const capParamBlocks = params
    ? ([
        ["Salary cap", params.salary_cap, "var(--signal)"],
        ["Luxury tax", params.luxury_tax, "var(--conditional)"],
        ["First apron", params.first_apron, "var(--illegal)"],
        ["Second apron", params.second_apron, "var(--illegal)"],
      ] as const)
    : [];

  const figures: { label: string; value: React.ReactNode; note: string; tone?: string }[] = [
    {
      label: `Committed ${firstSeason?.season ?? ""}`,
      value: firstSeason
        ? outlook.complete
          ? money(firstSeason.total)
          : `≥ ${money(firstSeason.total)}`
        : "—",
      note: outlook.complete ? "sum of imported salaries" : "floor — contracts missing",
    },
    {
      label: "Contracts imported",
      value: `${outlook.players_with_contracts}/${outlook.roster_size}`,
      note: "rostered players covered",
      tone: outlook.complete ? "var(--legal)" : "var(--conditional)",
    },
    {
      label: "Seasons committed",
      value: outlook.seasons.length,
      note: "league years with money on the books",
    },
    { label: "Cap league year", value: outlook.cap_league_year, note: "thresholds below" },
  ];

  return (
    <div className="space-y-6">
      <Panel accent={accent} padded={false} className="overflow-hidden">
        <LedgerHeader
          team={team}
          leagueYear={outlook.cap_league_year}
          status={
            outlook.complete ? (
              <Badge status="pass">all {outlook.roster_size} rostered players have contract data</Badge>
            ) : (
              <Badge status="incomplete">
                {outlook.players_with_contracts} of {outlook.roster_size} players covered
              </Badge>
            )
          }
        />
        <dl className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline lg:grid-cols-4">
          {figures.map((cell) => (
            <div key={cell.label} className="min-w-0 bg-panel px-5 py-4">
              <dt className="eyebrow truncate">{cell.label}</dt>
              <dd
                className="stat-serif mt-2 text-[2.1rem] leading-none text-foreground"
                style={cell.tone ? { color: cell.tone } : undefined}
              >
                {cell.value}
              </dd>
              <dd className="mt-2 text-[12px] leading-snug text-muted">{cell.note}</dd>
            </div>
          ))}
        </dl>
        {roster && roster.length > 0 && (
          <div className="border-b border-hairline px-5 py-5 last:border-b-0">
            <CoverageSlots players={roster} />
          </div>
        )}
        {!outlook.complete && (
          <p className="px-5 py-3.5 text-[12.5px] leading-relaxed text-muted">
            {count(missing, "contract")} missing — season totals are partial and the cap position
            is withheld rather than estimated.
          </p>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:items-start">
        <Panel accent={accent}>
          {!outlook.complete && (
            <p className="mb-3 flex items-start gap-2 text-[12px] leading-snug text-conditional">
              <span aria-hidden className="mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full bg-conditional" />
              Sum of imported contract salaries per season — partial roster coverage, so every bar
              is a floor.
            </p>
          )}
          {outlook.seasons.length === 0 ? (
            <EmptyState
              title="No contract years found"
              hint="The import produced no season rows for this roster."
            />
          ) : (
            <>
              <SalaryTimeline
                seasons={outlook.seasons}
                capLine={params?.salary_cap}
                taxLine={params?.luxury_tax}
              />
              {params && (
                <p className="mt-2 text-[12px] leading-relaxed text-muted">
                  Dashed lines are the league cap and tax for {outlook.cap_league_year}. They apply
                  to that league year only — later seasons are shown for contract context, not as a
                  cap projection.
                </p>
              )}
            </>
          )}
          <SourceRail
            source={sourceName ? `${sourceName} (imported snapshot)` : "imported contract snapshot"}
            retrievedAt={sourceDate}
          />
        </Panel>

        <Panel title="Cap parameter reference" subtitle={`League year ${outlook.cap_league_year}`}>
          {params ? (
            <ul className="divide-y divide-hairline">
              {capParamBlocks.map(([label, value, color]) => (
                <li key={label} className="flex items-baseline justify-between gap-3 py-2.5 first:pt-0">
                  <span className="flex items-center gap-2.5 text-[13px] text-muted">
                    <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
                    {label}
                  </span>
                  <span className="numeral text-[15px] text-foreground">{money(value)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <UnavailableNotice
              reason={`No league cap parameters are loaded for ${outlook.cap_league_year}, so no cap, tax or apron lines are drawn.`}
            />
          )}
          <p className="mt-3 border-t border-hairline pt-3 text-[12px] leading-relaxed text-muted">
            {outlook.note}
          </p>
          <p className="mt-2 text-[12px] leading-relaxed text-unavail">
            Cap space, apron position and tax bills are not derived here — Pivot reports only what
            the import contains.
          </p>
        </Panel>
      </div>

      <Panel
        title="Contracts"
        subtitle={`${count(outlook.players.length, "imported contract")} · PO = player option, TO = team option`}
        actions={!outlook.complete ? <Badge status="incomplete">partial coverage</Badge> : undefined}
      >
        {outlook.players.length === 0 ? (
          <EmptyState
            title="No contract rows matched this roster"
            hint={`The import ran, but none of its rows resolved to a ${teamName} player. Check the snapshot date in Data Health.`}
            action={
              <ButtonLink href="/data-health" size="sm">
                Open Data Health
              </ButtonLink>
            }
          />
        ) : (
          <>
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse">
                <caption className="sr-only">
                  Imported contracts for {teamName} by season, with option and expiring markers
                </caption>
                <thead>
                  <tr className="border-b border-line">
                    <Th>Player</Th>
                    {seasons.map((season) => (
                      <Th key={season} numeric>
                        {season}
                      </Th>
                    ))}
                    <Th>Status</Th>
                    <Th>Source</Th>
                  </tr>
                </thead>
                <tbody>
                  {outlook.players.map((player) => {
                    const bySeason = new Map(player.seasons.map((s) => [s.season, s]));
                    return (
                      <tr key={player.player_id} className="border-b border-hairline hover:bg-panel2/60">
                        <Td>
                          <span className="flex items-center gap-2.5">
                            <PlayerPhoto
                              nbaPlayerId={nbaIdByPlayer[player.player_id] ?? null}
                              name={player.name}
                              size={28}
                            />
                            <span className="whitespace-nowrap font-medium text-foreground">
                              {player.name}
                            </span>
                          </span>
                        </Td>
                        {seasons.map((season) => {
                          const year = bySeason.get(season);
                          return (
                            <Td key={season} numeric>
                              {year ? (
                                <span className="whitespace-nowrap">
                                  {money(year.salary)}
                                  {year.player_option && (
                                    <span
                                      className="ml-1 text-[10.5px] font-semibold text-signal"
                                      title="Player option"
                                    >
                                      PO
                                    </span>
                                  )}
                                  {year.team_option && (
                                    <span
                                      className="ml-1 text-[10.5px] font-semibold text-conditional"
                                      title="Team option"
                                    >
                                      TO
                                    </span>
                                  )}
                                </span>
                              ) : (
                                <span className="text-unavail">—</span>
                              )}
                            </Td>
                          );
                        })}
                        <Td>
                          <span className="flex flex-wrap gap-1">
                            {player.expiring && <Badge status="warning">expiring</Badge>}
                            {player.no_trade_clause && <Badge status="info">no-trade</Badge>}
                            {!player.expiring && !player.no_trade_clause && (
                              <span className="text-[12px] text-faint">—</span>
                            )}
                          </span>
                        </Td>
                        <Td>
                          <span className="whitespace-nowrap text-[12px] text-faint">
                            {player.source_name ?? "unknown"}
                            {player.source_date ? ` · ${formatDate(player.source_date)}` : ""}
                          </span>
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Explainer summary="Reading this table" className="mt-3">
              A dash means no salary is recorded for that player in that season — it is not a zero.
              Option years are shown at their stated value; Pivot does not model whether an option
              will be exercised.
            </Explainer>
          </>
        )}
        <SourceRail
          source={sourceName ? `${sourceName} (imported snapshot)` : "imported contract snapshot"}
          retrievedAt={sourceDate}
        />
      </Panel>
    </div>
  );
}
