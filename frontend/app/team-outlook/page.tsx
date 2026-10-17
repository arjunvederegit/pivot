"use client";

/**
 * Team Outlook index — the league map.
 *
 * Thirty franchises laid out the way the league is actually organised: two conferences,
 * three divisions each, five teams a division. On wide screens the two conferences sit
 * either side of a midcourt line drawn in chalk, so the board reads as a map of the league
 * rather than a list of logos; narrower, the conferences stack and the line runs across.
 *
 * A filter dims what does not match instead of reflowing the board, so a team never moves
 * on the map while you type. Nothing here fetches per-team stats: there is no bulk
 * endpoint, and thirty requests to decorate a directory would be a poor trade.
 *
 * Rendered from the shell-warmed `["teams"]` cache, so everything derived from it waits
 * for `useHydrated()` — the first client render must match the server HTML exactly.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { count } from "@/lib/format";
import { useHydrated } from "@/lib/hydrated";
import { teamIdentity } from "@/lib/teamIdentity";
import { useFavoriteTeam } from "@/lib/favoriteTeam";
import type { Team } from "@/lib/types";
import { IconSearch } from "@/components/icons";
import { TeamLogo } from "@/components/media";
import { Button, ButtonLink, EmptyState, ErrorState, PageHeader, Skeleton, SourceRail } from "@/components/ui";

type ConferenceKey = "East" | "West";

const CONFERENCES: { key: ConferenceKey; label: string }[] = [
  { key: "East", label: "Eastern Conference" },
  { key: "West", label: "Western Conference" },
];

interface Division {
  name: string;
  teams: Team[];
}

function matches(team: Team, q: string): boolean {
  if (!q) return true;
  return (
    team.full_name.toLowerCase().includes(q) ||
    team.city.toLowerCase().includes(q) ||
    team.abbreviation.toLowerCase().startsWith(q) ||
    (team.division ?? "").toLowerCase().includes(q)
  );
}

function divisionsOf(teams: Team[]): Division[] {
  const map = new Map<string, Team[]>();
  for (const team of teams) {
    const name = team.division ?? "No division";
    map.set(name, [...(map.get(name) ?? []), team]);
  }
  return [...map.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, list]) => ({
      name,
      teams: [...list].sort((a, b) => a.full_name.localeCompare(b.full_name)),
    }));
}

export default function TeamOutlookIndex() {
  const {
    data: teams,
    error,
    isLoading,
  } = useQuery({ queryKey: ["teams"], queryFn: () => api.get<Team[]>("/teams"), staleTime: 300_000 });

  // The shell warms `["teams"]` before this page hydrates; reading it on the hydration
  // render would put more markup on the client than the server sent.
  const hydrated = useHydrated();
  const board = hydrated ? teams : undefined;

  const favorite = useFavoriteTeam();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();

  const layout = useMemo(() => {
    const list = board ?? [];
    const conferences = CONFERENCES.map((c) => ({
      ...c,
      divisions: divisionsOf(list.filter((t) => t.conference === c.key)),
    }));
    const other = list.filter((t) => t.conference !== "East" && t.conference !== "West");
    return { conferences, other };
  }, [board]);

  const matchCount = (board ?? []).filter((t) => matches(t, q)).length;
  const retrievedAt = board?.[0]?.provenance?.source_retrieved_at ?? null;

  return (
    <div>
      <PageHeader
        eyebrow="The league map"
        title="Team Outlook"
        lede="Every franchise, by conference and division. Open a team for where it stands, what it is short of and who could answer it, its roster placed in the league, and its payroll picture — then start building."
        actions={
          <label className="flex w-[min(calc(100vw-2rem),24rem)] min-w-0 flex-col gap-2 sm:w-auto">
            <span className="eyebrow">Find a team</span>
            <span className="relative block">
              <IconSearch
                size={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint"
              />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Celtics, BOS, Atlantic…"
                aria-label="Filter teams by name, abbreviation or division"
                className="w-full min-w-0 rounded-lg border border-line bg-panel2 py-2 pl-9 pr-3 text-[14px] text-foreground placeholder:text-faint focus:border-signal/60 sm:w-64"
              />
            </span>
          </label>
        }
        meta={
          board && (
            <span className="text-[12.5px] text-muted" aria-live="polite">
              {matchCount === board.length
                ? count(board.length, "franchise", "franchises")
                : `${matchCount} of ${count(board.length, "franchise", "franchises")} match`}
            </span>
          )
        }
      />

      {error && <ErrorState message={`Could not load teams: ${String(error)}`} />}

      {!error && (!board || isLoading) && <BoardSkeleton />}

      {board && board.length === 0 && (
        <EmptyState
          title="No franchises imported yet"
          hint="The league map fills from the teams table. Data Health shows the sync step that loads it."
          action={<ButtonLink href="/data-health">Open Data Health</ButtonLink>}
        />
      )}

      {board && board.length > 0 && matchCount === 0 && (
        <EmptyState
          title="No franchise matches that search"
          hint="Try a city, a nickname, a three-letter abbreviation, or a division name."
          action={<Button onClick={() => setQuery("")}>Clear the filter</Button>}
        />
      )}

      {board && board.length > 0 && matchCount > 0 && (
        <section aria-label="The league by conference and division" className="panel overflow-hidden">
          <div className="grid xl:grid-cols-[minmax(0,1fr)_3rem_minmax(0,1fr)]">
            {layout.conferences.map((conference, i) => (
              <ConferenceSide
                key={conference.key}
                label={conference.label}
                divisions={conference.divisions}
                q={q}
                favoriteId={favorite?.id ?? null}
                divider={i === 0 ? <Midcourt /> : null}
              />
            ))}
          </div>
          {layout.other.length > 0 && (
            <div className="border-t border-hairline p-5">
              <DivisionColumn
                division={{ name: "Unclassified", teams: layout.other }}
                q={q}
                favoriteId={favorite?.id ?? null}
              />
            </div>
          )}
          <div className="border-t border-hairline px-5 py-3">
            <SourceRail
              className="mt-0! border-t-0! pt-0!"
              source={board[0]?.provenance?.upstream ?? "unknown source"}
              retrievedAt={retrievedAt}
            />
          </div>
        </section>
      )}
    </div>
  );
}

/**
 * The midcourt line between the conferences, in chalk: vertical with the centre circle on
 * wide screens, horizontal once the two halves stack.
 */
function Midcourt() {
  return (
    <div aria-hidden className="relative flex items-center justify-center">
      {/* stacked: a horizontal line across the board */}
      <div className="relative h-12 w-full xl:hidden">
        <span className="absolute inset-x-0 top-1/2 h-px bg-line" />
        <span className="absolute left-1/2 top-1/2 h-10 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full border border-line bg-panel" />
        <span className="absolute left-1/2 top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border border-line" />
      </div>
      {/* side by side: a vertical line from baseline to baseline */}
      <div className="absolute inset-y-0 left-1/2 hidden w-px -translate-x-1/2 bg-line xl:block" />
      <span className="absolute left-1/2 top-1/2 hidden h-12 w-12 -translate-x-1/2 -translate-y-1/2 rounded-full border border-line bg-panel xl:block" />
      <span className="absolute left-1/2 top-1/2 hidden h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-line xl:block" />
    </div>
  );
}

function ConferenceSide({
  label,
  divisions,
  q,
  favoriteId,
  divider,
}: {
  label: string;
  divisions: Division[];
  q: string;
  favoriteId: string | null;
  divider: React.ReactNode;
}) {
  const total = divisions.reduce((sum, d) => sum + d.teams.length, 0);
  const shown = divisions.reduce((sum, d) => sum + d.teams.filter((t) => matches(t, q)).length, 0);
  return (
    <>
      <div className="min-w-0 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="font-serif text-[1.85rem] leading-none text-foreground">{label}</h2>
          <span className="text-[12px] text-faint">{q ? `${shown} of ${total}` : count(total, "team")}</span>
        </div>
        <div className="mt-5 grid gap-x-3 gap-y-5 md:grid-cols-3">
          {divisions.map((division) => (
            <DivisionColumn key={division.name} division={division} q={q} favoriteId={favoriteId} />
          ))}
        </div>
      </div>
      {divider}
    </>
  );
}

function DivisionColumn({
  division,
  q,
  favoriteId,
}: {
  division: Division;
  q: string;
  favoriteId: string | null;
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 border-b border-hairline pb-2">
        <h3 className="eyebrow text-foreground">{division.name}</h3>
        <span className="text-[11.5px] tabular-nums text-faint">{division.teams.length}</span>
      </div>
      <ul className="mt-2.5 grid grid-cols-2 gap-2 md:grid-cols-1">
        {division.teams.map((team) => (
          <li key={team.id} className="min-w-0">
            <TeamTile team={team} dimmed={!matches(team, q)} isFavorite={favoriteId === team.id} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One franchise on the map: crest, nickname in the serif the team page's title uses, and
 * a quiet line under it. Its colour is a thin line along the top edge, nothing more. In
 * the narrow columns of the side-by-side map the crest sits above the name, so a long
 * nickname ("Timberwolves") is never cut; elsewhere it sits beside it.
 */
function TeamTile({ team, dimmed, isFavorite }: { team: Team; dimmed: boolean; isFavorite: boolean }) {
  const identity = teamIdentity(team.abbreviation);
  return (
    <Link
      href={`/team-outlook/${team.id}`}
      // A filtered-out team stays on the map, faded, and leaves the tab order and the
      // accessibility tree so the matches are what a keyboard or screen reader reaches.
      inert={dimmed}
      aria-hidden={dimmed || undefined}
      tabIndex={dimmed ? -1 : undefined}
      className={clsx(
        "panel group flex h-full min-w-0 items-center gap-2.5 rounded-[10px] px-3 py-2.5 transition-[opacity,border-color,background-color] duration-150 md:gap-3 xl:flex-col xl:items-start xl:gap-2.5 xl:py-3",
        dimmed ? "opacity-25" : "hover-frame",
        isFavorite && !dimmed && "border-signal/45",
      )}
      style={{ "--edge": identity.bright } as React.CSSProperties}
      title={team.full_name}
    >
      <TeamLogo abbreviation={team.abbreviation} name={team.full_name} size={30} decorative />
      <span className="w-full min-w-0 flex-1">
        <span className="block truncate font-serif text-[1.15rem] leading-[1.1] text-foreground transition-colors group-hover:text-signal md:text-[1.3rem] xl:text-[1.22rem] 2xl:text-[1.3rem]">
          {team.nickname ?? team.full_name}
        </span>
        <span className="mt-1 block truncate text-[11.5px] leading-tight text-muted">
          <span className="font-medium text-foreground/80">{team.abbreviation}</span>
          <span aria-hidden className="text-faint">
            {" "}
            ·{" "}
          </span>
          {isFavorite ? <span className="font-medium text-signal">Your team</span> : team.city}
        </span>
      </span>
    </Link>
  );
}

function BoardSkeleton() {
  return (
    <div className="panel p-5 lg:p-6" role="status" aria-label="Loading the league">
      <div className="grid gap-8 xl:grid-cols-2 xl:gap-12">
        {[0, 1].map((side) => (
          <div key={side}>
            <Skeleton className="h-7 w-56" />
            <div className="mt-5 grid gap-3 md:grid-cols-3">
              {[0, 1, 2].map((d) => (
                <div key={d} className="space-y-2">
                  <Skeleton className="h-3 w-20" />
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-[54px] xl:h-[106px]" />
                  ))}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
