"use client";

/**
 * Home — Pivot's intelligence dashboard.
 *
 * A cinematic hero (an illustrated arena, an editorial headline, one statement of purpose
 * and the way into the core tool), then the working dashboard underneath: the user's
 * team read through every model Pivot has, the decision workspace, their saved scenarios,
 * and the state of the league data and models those numbers come from.
 *
 * Nothing on the page is a placeholder. Every figure is read from the API through the
 * same hydration gate the rest of the product uses — the shell warms `["teams"]` and
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
import {
  DecisionWorkflow,
  NotBuilt,
  SupportingModules,
  decideStatus,
  diagnoseStatus,
  observeStatus,
  testStatus,
} from "@/components/command/workflow";
import { NeedAnswers, TeamIntelligence } from "@/components/home/team-intelligence";
import { useToast } from "@/components/toast";
import { ButtonLink } from "@/components/ui";

function SectionHead({
  id,
  eyebrow,
  title,
  aside,
}: {
  id: string;
  eyebrow: string;
  title: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h2 id={id} className="title-lg mt-2 text-foreground">
          {title}
        </h2>
      </div>
      {aside && <div className="max-w-sm text-[13px] leading-snug text-muted">{aside}</div>}
    </div>
  );
}

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

  const contractsConfigured = health?.providers?.contracts?.configured === true;
  const cards = health?.source_cards ?? [];
  const notLive = cards.filter((c) => c.status === "unavailable" || c.status === "failed").length;

  return (
    <div>
      {/* ------------------------------------------------------------------ hero */}
      <section aria-labelledby="home-title" className="border-b border-hairline">
        <div className="mx-auto grid w-full max-w-[1560px] items-end gap-8 px-4 pb-8 pt-8 sm:px-6 lg:grid-cols-[minmax(0,1fr)_380px] lg:px-8 lg:pb-9 lg:pt-9">
          <div className="min-w-0 max-w-3xl">
            <div className="eyebrow text-faint">Pivot{health ? ` · ${health.current_season} season` : ""}</div>
            <h1
              id="home-title"
              className="mt-2.5 font-serif text-[clamp(2.2rem,1.5rem+2.2vw,3.5rem)] font-medium leading-[1.04] tracking-[-0.018em] text-foreground"
            >
              Sharper decisions.
              <br />
              <span className="italic text-muted">Better basketball.</span>
            </h1>
            <p className="mt-3.5 max-w-xl text-[14.5px] leading-relaxed text-muted">
              Basketball intelligence for the decisions that shape winning — every number traced to
              the data it came from.
            </p>
            <div className="mt-5 flex flex-wrap items-center gap-2.5">
              <ButtonLink
                href={team ? `/trade-evaluator?team=${team.id}` : "/trade-evaluator"}
                variant="primary"
              >
                Open the Trade Evaluator
              </ButtonLink>
              <ButtonLink href={team ? `/team-outlook/${team.id}` : "/team-outlook"} variant="secondary">
                Start with your roster
              </ButtonLink>
            </div>
          </div>
          <div className="min-w-0">
            <YourTeamCard favorite={team} teams={teams} />
          </div>
        </div>
      </section>

      <div className="mx-auto w-full max-w-[1560px] space-y-10 px-4 py-8 sm:px-6 lg:px-8">
        {/* ------------------------------------------------------ team intelligence */}
        {team && (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
            <TeamIntelligence favorite={team} />
            <NeedAnswers favorite={team} />
          </div>
        )}

        {/* ---------------------------------------------------- decision workspace */}
        <section aria-labelledby="workflow-heading">
          <SectionHead
            id="workflow-heading"
            eyebrow="Decision workspace"
            title="The decision workflow"
            aside="Four questions, in the order a front office asks them — each status read from the data that step needs."
          />
          <DecisionWorkflow
            statuses={[
              observeStatus(health, Boolean(healthError)),
              diagnoseStatus(reference, referenceFailed),
              testStatus(health, Boolean(healthError)),
              decideStatus(trades, Boolean(tradesError)),
            ]}
            hrefs={[
              team ? `/team-outlook/${team.id}` : "/team-outlook",
              "/player-explorer",
              team ? `/trade-evaluator?team=${team.id}` : "/trade-evaluator",
              "/strategy-lab",
            ]}
          />
          <div className="mt-4">
            <SupportingModules
              items={[
                {
                  title: "Contracts & Cap",
                  href: "/salary-cap-center",
                  reading: !health
                    ? "Payroll by season, expiring money and commitments."
                    : contractsConfigured
                      ? typeof health.tables?.contracts?.rows === "number"
                        ? `Payroll by season and commitments · ${health.tables.contracts.rows} contracts on file.`
                        : "Payroll by season and commitments."
                      : "Payroll by season and commitments — unavailable until contracts are imported.",
                  badge: !health
                    ? null
                    : contractsConfigured
                      ? { status: "pass", word: "imported" }
                      : { status: "warning", word: "needs import" },
                },
                {
                  title: "Methodology",
                  href: "/methodology",
                  reading: "Every number, its definition, its source data and what it cannot support.",
                },
                {
                  title: "Data Health",
                  href: "/data-health",
                  reading: health
                    ? `${cards.length} sources${notLive ? ` · ${notLive} not loaded` : ""}${
                        typeof health.open_quality_issue_total === "number"
                          ? ` · ${health.open_quality_issue_total} open issues, each with its next step.`
                          : "."
                      }`
                    : "Every source with its coverage, freshness and next step.",
                },
              ]}
            />
          </div>
          <div className="mt-4">
            <NotBuilt />
          </div>
        </section>

        {/* -------------------------------------------------------- recent work */}
        <section aria-labelledby="work-heading">
          <SectionHead id="work-heading" eyebrow="Saved in this workspace" title="Recent scenarios" />
          <YourWork
            trades={trades}
            tradesError={tradesError}
            scenarios={scenarios}
            scenariosError={scenariosError}
          />
        </section>

        {/* ------------------------------------------------------ league & models */}
        <section aria-labelledby="league-heading">
          <SectionHead
            id="league-heading"
            eyebrow="Basketball insights"
            title="The league, as Pivot sees it"
            aside="What the models were fed and what they scored — read live from Data Health and the impact model."
          />
          <LeagueState
            health={health}
            healthError={healthError}
            reference={reference}
            referenceFailed={referenceFailed}
          />
        </section>

        {/* ------------------------------------------------------------ pick team */}
        <section id="pick-your-team" aria-labelledby="pick-heading" className="scroll-mt-24">
          <SectionHead
            id="pick-heading"
            eyebrow="Set your context"
            title={team ? "Change your team" : "Pick your team"}
            aside="Team Outlook and the Trade Evaluator open on it, and this dashboard reads its record and roster."
          />
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
