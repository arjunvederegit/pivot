"use client";

/**
 * Workspace — where a front-office user starts.
 *
 * Reads top to bottom as the questions a working day opens with: where am I working (the
 * team, its record, its roster), what can I decide (the implemented workflows), where was I
 * (saved deals and strategies), and — where this build has it — Ask Pivot. Underneath, the
 * state of the data and models the numbers come from, and the team picker.
 *
 * Nothing on the page is a placeholder. Every figure is read from the API through the same
 * hydration gate the rest of the product uses — the shell warms `["teams"]` and
 * `["data-health"]`, so the first client render must match the server HTML — and every
 * module without data says so rather than inventing it.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { setFavoriteTeam, useFavoriteTeam } from "@/lib/favoriteTeam";
import { useHydrated } from "@/lib/hydrated";
import { useImpactReference } from "@/lib/impact";
import { dataHealthSchema } from "@/lib/schemas";
import type { DataHealth, Scenario, Team, TradeSummary } from "@/lib/types";
import { LeagueState } from "@/components/command/league-state";
import { YourWork } from "@/components/command/snapshot";
import { TeamBoard, YourTeamCard } from "@/components/command/team-context";
import { NeedAnswers, TeamIntelligence } from "@/components/home/team-intelligence";
import { useToast } from "@/components/toast";
import { AskPivotPanel, DecisionsPanel } from "@/components/home/workspace-panels";
import { ASSISTANT_ENABLED } from "@/lib/features";

export default function HomePage() {
  const toast = useToast();
  const favorite = useFavoriteTeam();
  const hydrated = useHydrated();

  const { data: teamsData, error: teamsError } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
  });
  const { data: healthData, error: healthError } = useQuery({
    queryKey: ["data-health"],
    queryFn: () => api.get<DataHealth>("/data-health", dataHealthSchema),
  });
  const { data: tradesData, error: tradesError } = useQuery({
    queryKey: ["trades"],
    queryFn: () => api.get<TradeSummary[]>("/trades"),
  });
  const { data: scenariosData, error: scenariosError } = useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api.get<Scenario[]>("/scenarios"),
  });
  const { data: referenceData, isError: referenceFailed } = useImpactReference();

  const teams = hydrated ? teamsData : undefined;
  const health = hydrated ? healthData : undefined;
  const trades = hydrated ? tradesData : undefined;
  const scenarios = hydrated ? scenariosData : undefined;
  const reference = hydrated ? referenceData : undefined;
  const team = hydrated ? favorite : null;

  const contractsConfigured = health ? health.providers?.contracts?.configured === true : undefined;

  return (
    <div className="mx-auto w-full max-w-[1560px] px-4 py-6 sm:px-6 lg:px-8 lg:py-7">
      <header className="mb-6">
        <div className="eyebrow text-faint">
          Overview{health ? ` · ${health.current_season} season` : ""}
        </div>
        <h1 className="title-xl mt-2.5 text-foreground">Workspace</h1>
      </header>

      <div className="space-y-4">
        {/* ------------------------------------------- where am I · what can I decide */}
        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
          <YourTeamCard favorite={team} teams={teams} />
          <div className="min-w-0 space-y-4">
            <DecisionsPanel
              teamId={team?.id ?? null}
              savedDeals={trades?.length}
              contractsConfigured={contractsConfigured}
            />
            {ASSISTANT_ENABLED && <AskPivotPanel />}
          </div>
        </div>

        {/* ------------------------------------------------------- the team, read */}
        {team && (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
            <TeamIntelligence favorite={team} />
            <NeedAnswers favorite={team} />
          </div>
        )}

        {/* --------------------------------------------------------- where was I */}
        <section aria-labelledby="work-heading">
          <h2 id="work-heading" className="sr-only">
            Saved work
          </h2>
          <YourWork
            trades={trades}
            tradesError={tradesError}
            scenarios={scenarios}
            scenariosError={scenariosError}
          />
        </section>

        {/* ------------------------------------------------------ data and models */}
        <section aria-labelledby="league-heading">
          <h2 id="league-heading" className="sr-only">
            Data and models
          </h2>
          <LeagueState
            health={health}
            healthError={healthError}
            reference={reference}
            referenceFailed={referenceFailed}
          />
        </section>

        {/* ------------------------------------------------------------ pick team */}
        <section id="pick-your-team" aria-labelledby="pick-heading" className="scroll-mt-24">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
            <h2 id="pick-heading" className="text-[1.05rem] font-semibold text-foreground">
              {team ? "Change team" : "Select team"}
            </h2>
            <p className="text-[12.5px] text-muted">
              Team Outlook and the Trade Evaluator open on it, and this page reads its record and
              roster.
            </p>
          </div>
          <TeamBoard
            teams={teams}
            error={teamsError}
            chosenId={team?.id ?? null}
            onPick={(t) => {
              setFavoriteTeam({ id: t.id, abbreviation: t.abbreviation });
              toast("success", `${t.full_name} is now your team.`);
            }}
          />
        </section>
      </div>
    </div>
  );
}
