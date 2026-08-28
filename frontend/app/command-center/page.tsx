"use client";

/**
 * Command Center — Pivot's front door.
 *
 * The page it replaced was a tool launcher: a four-card grid of modules with a status
 * chip each. That is an accurate description of what the software contains and a poor
 * description of what it is for, and it made Pivot read as a collection of calculators
 * that happen to share a header.
 *
 * This page leads with the decision workflow instead — observe, diagnose, test, decide —
 * because that sequence is the product. Every step links to the module that performs it,
 * so nothing is lost: the launcher is still here, it is just ordered by the question a
 * user is asking rather than by the shape of the codebase.
 *
 * The visual language is unchanged. The arena-at-night palette, the condensed display
 * face, the half-court motif and the team-tinted hero were all developed through R1-R7
 * and are the part of this product that was already right.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { dataHealthSchema } from "@/lib/schemas";
import { formatDate } from "@/lib/format";
import { setFavoriteTeam, useFavoriteTeam } from "@/lib/favoriteTeam";
import { useHydrated } from "@/lib/hydrated";
import { teamIdentity, teamVars } from "@/lib/teamIdentity";
import type { DataHealth, Scenario, Team, TradeSummary } from "@/lib/types";
import { PRODUCT_NAME, PRODUCT_TAGLINE } from "@/components/brand";
import { BallGlyph, HalfCourt, ShotChartMotif, TransactionLane } from "@/components/court";
import { TeamCrest, TeamLogo } from "@/components/media";
import { useToast } from "@/components/toast";
import { Badge, ButtonLink, ErrorState, Panel, Skeleton, StatBlock } from "@/components/ui";

/* ----------------------------------------------------------- decision workflow */

interface Step {
  step: string;
  title: string;
  href: string;
  blurb: string;
  status: "ready" | "needs-import";
  art: React.ReactNode;
}

/**
 * The four questions, in the order a front office asks them. `href` points at the module
 * that answers each one — these are the shipped routes, not aspirational ones.
 */
const WORKFLOW: Step[] = [
  {
    step: "01",
    title: "Understand the roster",
    href: "/team-outlook",
    blurb:
      "What a team actually contains: rotation, roles, measured strengths, and the needs that follow from them.",
    status: "ready",
    art: <HalfCourt className="h-10 w-28 text-signal/45" />,
  },
  {
    step: "02",
    title: "Identify the edge",
    href: "/player-explorer",
    blurb:
      "Who is out there, what they do, and which of them answers a need this roster actually has.",
    status: "ready",
    art: <ShotChartMotif className="h-10 w-28 text-signal" />,
  },
  {
    step: "03",
    title: "Test the move",
    href: "/trade-evaluator",
    blurb:
      "Build a two- or three-team deal and get a live rules check with projected impact, fit and risk.",
    status: "ready",
    art: <TransactionLane className="h-10 w-28" active />,
  },
  {
    step: "04",
    title: "Make the decision",
    href: "/strategy-lab",
    blurb:
      "Put the options side by side under your own priorities and see which one survives them.",
    status: "ready",
    art: (
      <svg viewBox="0 0 112 40" className="h-10 w-28" aria-hidden fill="none">
        <rect x="2" y="14" width="26" height="24" rx="3" stroke="var(--hairline)" />
        <rect x="34" y="6" width="26" height="32" rx="3" stroke="var(--signal)" />
        <rect x="66" y="20" width="26" height="18" rx="3" stroke="var(--hairline)" />
        <circle cx="47" cy="3" r="2.5" fill="var(--signal)" />
      </svg>
    ),
  },
];

const SUPPORTING = [
  {
    title: "Contracts & Cap",
    href: "/salary-cap-center",
    blurb: "Payroll by season, expiring money and commitments — once contracts are imported.",
    status: "needs-import" as const,
  },
  {
    title: "Methodology",
    href: "/methodology",
    blurb: "Every number, its definition, its source data and what it cannot support.",
    status: "ready" as const,
  },
  {
    title: "Data Health",
    href: "/data-health",
    blurb: "Seven sources with coverage, freshness and the next step for each.",
    status: "ready" as const,
  },
];

const ROADMAP = [
  { title: "Player Intelligence", need: "Skill grades beyond the box score need tracking or matchup data" },
  { title: "Scenario Engine", need: "Signings, waivers and departures on the same before/after machinery as trades" },
  { title: "Pivot AI", need: "Planned to call the engines and quote them — never to invent an answer" },
];

const PRINCIPLES = [
  ["Real NBA data", "Rosters and stats from NBA.com, never invented"],
  ["Conditional fit", "There is no universal fit score — it depends on the roster"],
  ["Traceable numbers", "Every value carries its method, source and limitations"],
  ["Named uncertainty", "Ranges and probabilities, not false precision"],
  ["Honest gaps", "What Pivot cannot measure is listed, not quietly omitted"],
  ["Rules that refuse", "A check that could not run never upgrades a verdict"],
];

/* -------------------------------------------------------------------- page */

export default function CommandCenterPage() {
  const toast = useToast();
  const favorite = useFavoriteTeam();
  const hydrated = useHydrated();

  // Each of these reads `error` as well as `data`. Without it a failed request renders
  // the same skeleton as a pending one, forever — the loading state becomes the error
  // state and the user is never told anything went wrong.
  const { data: teamsData, error: teamsError } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
  });
  const { data: healthData } = useQuery({
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

  // Everything below reads through the hydration gate. The app shell hydrates before this
  // page and warms the shared query cache, so these can already hold data on the page's
  // very first client render while the server HTML still had skeletons — React then throws
  // "Hydration failed because the server rendered text didn't match the client". Holding
  // the values back for that one render makes the two trees identical; the real data
  // arrives in the commit immediately after.
  const teams = hydrated ? teamsData : undefined;
  const health = hydrated ? healthData : undefined;
  const trades = hydrated ? tradesData : undefined;
  const scenarios = hydrated ? scenariosData : undefined;

  const favoriteTeam = teams?.find((t) => t.id === favorite?.id) ?? null;
  const identity = teamIdentity(favorite?.abbreviation);
  const rosterSpots = health?.tables?.rosters?.rows ?? null;
  const photoCoverage = health?.asset_coverage?.player_photo_coverage ?? null;

  return (
    <div className="space-y-10">
      {/* ---------------------------------------------------------- hero */}
      <section
        className="panel relative overflow-hidden"
        style={{ ...teamVars(favorite?.abbreviation), "--edge": identity.bright } as React.CSSProperties}
      >
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.55]"
          style={{
            background: `radial-gradient(680px 320px at 88% 8%, ${identity.primary}33, transparent 70%)`,
          }}
        />
        <div className="relative grid gap-8 p-6 md:p-9 lg:grid-cols-[minmax(0,1fr)_340px] lg:items-center">
          <div className="min-w-0">
            <div className="eyebrow flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <span className="flex items-center gap-1.5 text-signal">
                <BallGlyph size={13} />
                {PRODUCT_NAME}
              </span>
              <span aria-hidden className="hidden text-faint sm:inline">
                /
              </span>
              <span className="hidden sm:inline">{PRODUCT_TAGLINE}</span>
              {health && (
                <>
                  <span aria-hidden className="text-faint">
                    /
                  </span>
                  <span>{health.current_season} season</span>
                </>
              )}
            </div>

            <h1 className="title-xl mt-3 text-foreground">
              Know what your roster is. Then change it.
            </h1>
            <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
              Pivot is a decision system for basketball front offices. Observe a roster,
              diagnose what it lacks, compare who fits it, test the move — and read why,
              with every number traced to how it was produced.
            </p>

            <div className="mt-6 flex flex-wrap items-center gap-3">
              <ButtonLink
                href={favorite ? `/team-outlook/${favorite.id}` : "/team-outlook"}
                variant="primary"
              >
                Start with your roster
              </ButtonLink>
              <ButtonLink
                href={favorite ? `/trade-evaluator?team=${favorite.id}` : "/trade-evaluator"}
                variant="secondary"
              >
                Open the Trade Evaluator
              </ButtonLink>
            </div>

            <dl className="mt-7 flex flex-wrap gap-x-8 gap-y-3 border-t border-hairline pt-4">
              <div>
                <dt className="eyebrow text-[0.5625rem]">Teams</dt>
                <dd className="numeral text-xl leading-none">{teams?.length ?? "—"}</dd>
              </div>
              <div>
                <dt className="eyebrow text-[0.5625rem]">Roster spots</dt>
                <dd className="numeral text-xl leading-none">{rosterSpots ?? "—"}</dd>
              </div>
              <div>
                <dt className="eyebrow text-[0.5625rem]">Saved deals</dt>
                <dd className="numeral text-xl leading-none">{trades?.length ?? "—"}</dd>
              </div>
              <div className="min-w-0">
                <dt className="eyebrow text-[0.5625rem]">Data synced</dt>
                <dd className="text-sm leading-tight text-muted">
                  {health ? formatDate(health.last_successful_sync) : "—"}
                </dd>
              </div>
            </dl>
          </div>

          {/* The floor: your franchise standing at the top of the key. */}
          <div className="relative mx-auto w-full max-w-[340px]">
            <div className="relative aspect-[50/47] w-full">
              <HalfCourt className="absolute inset-0 h-full w-full text-signal/30" />
              <ShotChartMotif className="absolute inset-0 h-full w-full text-signal/25" />
              <div className="absolute inset-x-0 top-[14%] flex flex-col items-center px-4 text-center">
                {favoriteTeam ? (
                  <>
                    <TeamCrest
                      abbreviation={favoriteTeam.abbreviation}
                      name={favoriteTeam.full_name}
                      size={72}
                    />
                    <div className="display mt-2 max-w-full truncate text-lg leading-tight text-foreground">
                      {favoriteTeam.nickname ?? favoriteTeam.full_name}
                    </div>
                    <div className="eyebrow mt-0.5 text-[0.5625rem]">Your team</div>
                    <div className="mt-3 flex flex-wrap justify-center gap-2">
                      <ButtonLink href={`/team-outlook/${favoriteTeam.id}`} size="sm">
                        Roster
                      </ButtonLink>
                      <ButtonLink
                        href={`/trade-evaluator?team=${favoriteTeam.id}`}
                        size="sm"
                        variant="signal"
                      >
                        Trade
                      </ButtonLink>
                    </div>
                  </>
                ) : (
                  <>
                    <span
                      aria-hidden
                      className="flex h-[72px] w-[72px] items-center justify-center rounded-full border border-dashed border-line text-signal/70"
                    >
                      <BallGlyph size={30} />
                    </span>
                    <div className="display mt-2 text-lg leading-tight text-foreground">
                      Pick your team
                    </div>
                    <p className="mt-1 text-[12px] leading-snug text-muted">
                      Choose a franchise below — Pivot defaults to it everywhere.
                    </p>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------- the decision workflow */}
      <section>
        <SectionHead
          eyebrow="How Pivot works"
          title="The decision workflow"
          aside="Four questions, in the order a front office asks them"
        />
        <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {WORKFLOW.map((step, index) => (
            <li key={step.title} className="relative">
              <Link
                href={step.href}
                className="panel group flex h-full flex-col p-4 transition-colors hover:border-signal/45"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="numeral text-[22px] leading-none text-signal/70">
                    {step.step}
                  </span>
                  {step.status === "ready" ? (
                    <Badge status="pass">ready</Badge>
                  ) : (
                    <Badge status="warning">needs import</Badge>
                  )}
                </div>
                <h3 className="title-md mt-2 text-foreground group-hover:text-signal">
                  {step.title}
                </h3>
                <p className="mt-1.5 flex-1 text-[13px] leading-relaxed text-muted">
                  {step.blurb}
                </p>
                <div className="mt-3 flex items-end justify-between">
                  <span className="text-muted/80">{step.art}</span>
                  <span className="eyebrow text-signal opacity-0 transition-opacity group-hover:opacity-100">
                    Open →
                  </span>
                </div>
              </Link>
              {index < WORKFLOW.length - 1 && (
                <span
                  aria-hidden
                  className="pointer-events-none absolute -right-2 top-1/2 z-10 hidden text-signal/40 xl:block"
                >
                  →
                </span>
              )}
            </li>
          ))}
        </ol>

        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {SUPPORTING.map((item) => (
            <Link
              key={item.title}
              href={item.href}
              className="group flex items-start justify-between gap-3 rounded-lg border border-hairline bg-panel px-4 py-3 transition-colors hover:border-signal/45"
            >
              <span className="min-w-0">
                <span className="block text-sm font-semibold text-foreground group-hover:text-signal">
                  {item.title}
                </span>
                <span className="mt-0.5 block text-[12px] leading-snug text-muted">
                  {item.blurb}
                </span>
              </span>
              {item.status === "needs-import" && <Badge status="warning">import</Badge>}
            </Link>
          ))}
        </div>

        <div className="mt-3 rounded-lg border border-dashed border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="eyebrow">Not built yet</span>
            <span className="text-[11px] text-faint">
              Named rather than hinted at — Pivot won&apos;t ship a model it can&apos;t validate,
              or a page that promises one.
            </span>
          </div>
          <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1.5">
            {ROADMAP.map((item) => (
              <li key={item.title} className="text-[13px]">
                <span className="text-muted">{item.title}</span>{" "}
                <span className="text-faint">— {item.need}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* -------------------------------------------------- team picker strip */}
      <section>
        <SectionHead
          eyebrow="Set your context"
          title="Pick your team"
          aside="Sets the default across every module"
        />
        {teamsError ? (
          <ErrorState message={`Could not load teams: ${String(teamsError)}`} />
        ) : !teams ? (
          // Same column ramp as the loaded grid below, so the tiles do not reflow when
          // the request resolves.
          <div className="grid grid-cols-5 gap-2 sm:grid-cols-6 md:grid-cols-10">
            {Array.from({ length: 30 }).map((_, i) => (
              <Skeleton key={i} className="h-[74px]" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-5 gap-2 sm:grid-cols-6 md:grid-cols-10">
            {teams.map((team) => {
              const chosen = favorite?.id === team.id;
              const teamColors = teamIdentity(team.abbreviation);
              return (
                <button
                  key={team.id}
                  type="button"
                  aria-pressed={chosen}
                  title={`Set ${team.full_name} as your team`}
                  onClick={() => {
                    setFavoriteTeam({ id: team.id, abbreviation: team.abbreviation });
                    toast("success", `${team.full_name} is now your team.`);
                  }}
                  className={`flex flex-col items-center gap-1.5 rounded-lg border px-1 py-2.5 transition-colors ${
                    chosen ? "bg-panel2" : "border-hairline bg-panel hover:bg-panel2"
                  }`}
                  style={chosen ? { borderColor: teamColors.bright } : undefined}
                >
                  <TeamLogo abbreviation={team.abbreviation} name={team.full_name} size={34} decorative />
                  <span
                    className="numeral text-[13px] leading-none"
                    style={{ color: chosen ? teamColors.bright : undefined }}
                  >
                    {team.abbreviation}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </section>

      {/* -------------------------------------------------- front-office snapshot */}
      <section>
        <SectionHead eyebrow="Your work" title="Front-office snapshot" />
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel title="Recent deals" actions={<Link className="eyebrow text-signal" href="/strategy-lab">Compare →</Link>}>
            {tradesError ? (
              <ErrorState message={`Could not load saved deals: ${String(tradesError)}`} />
            ) : !trades ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-9" />
                ))}
              </div>
            ) : trades.length === 0 ? (
              <SnapshotEmpty
                title="No deals saved yet"
                body="Build one in the Trade Evaluator and it will show up here for comparison."
                href="/trade-evaluator"
                cta="Open the Trade Evaluator"
              />
            ) : (
              <ul className="divide-y divide-hairline">
                {trades.slice(0, 5).map((trade) => (
                  <li key={trade.id}>
                    <Link
                      href={`/trades/${trade.id}`}
                      className="flex items-center gap-3 py-2 transition-colors hover:text-signal"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm">{trade.name}</span>
                      <span className="flex shrink-0 items-center gap-1">
                        {trade.teams.map((abbr) => (
                          <TeamLogo key={abbr} abbreviation={abbr} size={18} decorative />
                        ))}
                      </span>
                      <span className="hidden shrink-0 text-[11px] text-faint sm:inline">
                        {formatDate(trade.created_at)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel
            title="Saved strategies"
            actions={<Link className="eyebrow text-signal" href="/team-outlook">Teams →</Link>}
          >
            {scenariosError ? (
              <ErrorState message={`Could not load saved strategies: ${String(scenariosError)}`} />
            ) : !scenarios ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-9" />
                ))}
              </div>
            ) : scenarios.length === 0 ? (
              <SnapshotEmpty
                title="No strategies yet"
                body="Choose how a team should build on its roster page — it sets how every deal is scored."
                href="/team-outlook"
                cta="Choose a strategy"
              />
            ) : (
              <ul className="divide-y divide-hairline">
                {scenarios.slice(0, 5).map((scenario) => (
                  <li key={scenario.id} className="flex items-center gap-3 py-2 text-sm">
                    <TeamLogo abbreviation={scenario.focal_team.abbreviation} size={20} decorative />
                    <span className="min-w-0 flex-1 truncate">{scenario.name}</span>
                    <Badge status="info">{scenario.strategy.replace("_", " ")}</Badge>
                    <Link
                      href={`/trade-evaluator?scenario=${scenario.id}`}
                      className="eyebrow shrink-0 text-signal"
                    >
                      Build →
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        {health && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Panel padded={false} className="px-4 py-3">
              <StatBlock
                label="Live NBA data"
                value={health.last_successful_sync ? "Synced" : "None"}
                note={formatDate(health.last_successful_sync)}
                size="sm"
                accent="var(--legal)"
              />
            </Panel>
            <Panel padded={false} className="px-4 py-3">
              <StatBlock
                label="Contracts"
                value={health.providers?.contracts?.configured ? "Imported" : "Not imported"}
                note="Salary rules stay unavailable until imported"
                size="sm"
                accent={health.providers?.contracts?.configured ? "var(--legal)" : "var(--unknown)"}
              />
            </Panel>
            <Panel padded={false} className="px-4 py-3">
              <StatBlock
                label="Player photos"
                value={photoCoverage !== null ? `${Math.round(photoCoverage * 100)}%` : "—"}
                note="of rostered players matched"
                size="sm"
                accent="var(--signal)"
              />
            </Panel>
            <Panel padded={false} className="px-4 py-3">
              <StatBlock
                label="Active models"
                value={health.active_models?.length ?? 0}
                note="validation shown in Data Health"
                size="sm"
                accent="var(--signal)"
              />
            </Panel>
          </div>
        )}
      </section>

      {/* --------------------------------------------------- principles strip */}
      <section className="hardwood rounded-xl border border-hairline px-5 py-5">
        <SectionHead eyebrow="What Pivot holds to" title="Built for defensible decisions" />
        <ul className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {PRINCIPLES.map(([title, body]) => (
            <li key={title} className="flex gap-2.5">
              <span aria-hidden className="mt-1 text-signal">
                <BallGlyph size={13} />
              </span>
              <span className="min-w-0">
                <span className="block whitespace-nowrap text-sm font-semibold text-foreground">
                  {title}
                </span>
                <span className="block text-[12px] leading-snug text-muted">{body}</span>
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function SectionHead({
  eyebrow,
  title,
  aside,
}: {
  eyebrow: string;
  title: string;
  aside?: string;
}) {
  return (
    <div className="mb-3">
      <div className="h-px w-full bg-gradient-to-r from-signal/60 to-transparent" />
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pt-2.5">
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h2 className="title-lg mt-1 text-balance text-foreground">{title}</h2>
        </div>
        {aside && <p className="text-[11px] text-faint">{aside}</p>}
      </div>
    </div>
  );
}

function SnapshotEmpty({
  title,
  body,
  href,
  cta,
}: {
  title: string;
  body: string;
  href: string;
  cta: string;
}) {
  return (
    <div className="court-grid rounded-lg border border-dashed border-line px-4 py-6 text-center">
      <p className="title-md text-foreground">{title}</p>
      <p className="mx-auto mt-1 max-w-sm text-[13px] leading-relaxed text-muted">{body}</p>
      <ButtonLink href={href} size="sm" className="mt-3">
        {cta}
      </ButtonLink>
    </div>
  );
}
