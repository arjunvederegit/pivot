"use client";

/**
 * Team context — the one franchise every module opens on.
 *
 * Two pieces. `YourTeamCard` is the readout for the chosen team, set over the arena hero
 * as an elevated card: its record, seed and point differential as NBA.com served them,
 * and the three rostered players with the highest league impact, placed among everyone
 * the model scored. `TeamBoard` is the picker, split by conference because that is how
 * the league is organised (and how a reader scans for a team); the chosen franchise is
 * framed in its own colour and carries a check, so the choice never rests on colour alone.
 *
 * Nothing is inferred. A team with no standing row shows "—"; a roster whose players are
 * unscored says how many, rather than ranking them last.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Link from "next/link";
import { api } from "@/lib/api";
import type { FavoriteTeam } from "@/lib/favoriteTeam";
import { count, formatDate, ordinal } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type { RosterResponse, Team } from "@/lib/types";
import { ImpactInline } from "@/components/impact";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { asUtc } from "@/components/command/time";
import { ErrorState, Skeleton, SourceRail } from "@/components/ui";
import { TeamGrid } from "@/components/team-card";

interface TeamDetailResponse {
  team: Team;
  season: string;
  standing: {
    wins: number;
    losses: number;
    win_pct: number | null;
    conference: string | null;
    playoff_rank: number | null;
    details?: Record<string, unknown> | null;
    source_retrieved_at?: string | null;
  } | null;
}

/** The team card beside the page header: a panel, nothing more. */
const HERO_CARD =
  "panel";

/* ------------------------------------------------------------ your team */

function Reading({
  label,
  value,
  sub,
  title,
  loading,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  title?: string;
  /** The standings row is still being read — a placeholder, not a missing value. */
  loading?: boolean;
}) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="text-[11.5px] text-muted">{label}</dt>
      <dd className="mt-1 flex h-[1.85rem] items-baseline gap-1.5">
        {loading ? (
          <Skeleton className="h-6 w-14 max-w-full self-center bg-panel3" />
        ) : (
          <>
            <span className="stat-serif text-[1.85rem] leading-none text-foreground">{value}</span>
            {sub && <span className="truncate text-[11.5px] text-faint">{sub}</span>}
          </>
        )}
      </dd>
    </div>
  );
}

export function YourTeamCard({
  favorite,
  teams,
}: {
  favorite: FavoriteTeam | null;
  teams: Team[] | undefined;
}) {
  const id = favorite?.id ?? null;
  const team = teams?.find((t) => t.id === id) ?? null;
  const identity = teamIdentity(favorite?.abbreviation);

  const { data: detail, isPending: detailPending } = useQuery({
    queryKey: ["team-detail", id],
    queryFn: () => api.get<TeamDetailResponse>(`/teams/${id}`),
    enabled: id !== null,
    staleTime: 300_000,
  });
  const { data: roster, error: rosterError } = useQuery({
    queryKey: ["roster", id],
    queryFn: () => api.get<RosterResponse>(`/teams/${id}/roster`),
    enabled: id !== null,
  });

  if (!favorite) {
    return (
      <section aria-label="Your team" className={clsx(HERO_CARD, "p-5")}>
        <div className="eyebrow flex items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full border border-faint" />
          Your team
        </div>
        <p className="mt-3 font-serif text-[1.75rem] leading-none text-foreground">No team set</p>
        <p className="mt-2.5 text-[13px] leading-relaxed text-muted">
          Choose a franchise and Team Outlook and the Trade Evaluator open on it, and this page
          reads its record and roster.
        </p>
        <Link
          href="#pick-your-team"
          className="mt-4 inline-flex items-center gap-1.5 text-[13px] font-medium text-signal transition-colors hover:text-foreground"
        >
          Pick a team <span aria-hidden>↓</span>
        </Link>
      </section>
    );
  }

  const standing = detail?.standing ?? null;
  const diff = standing?.details?.DiffPointsPG;
  const scored = (roster?.roster ?? []).filter((p) => p.tei !== null && p.tei !== undefined);
  const unscored = (roster?.roster.length ?? 0) - scored.length;
  const top = [...scored].sort((a, b) => (b.tei ?? 0) - (a.tei ?? 0)).slice(0, 3);

  return (
    <section
      aria-label="Your team"
      className={clsx(HERO_CARD, "flex h-full flex-col overflow-hidden")}
      style={{ "--edge": identity.bright } as React.CSSProperties}
    >
      <header className="flex items-start justify-between gap-3 px-5 pt-5">
        <div className="flex min-w-0 items-center gap-3.5">
          <TeamLogo abbreviation={favorite.abbreviation} size={40} decorative />
          <div className="min-w-0">
            <div className="eyebrow flex items-center gap-1.5">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: identity.bright }} />
              Your team
            </div>
            <p className="mt-1 truncate font-serif text-[1.6rem] leading-none text-foreground">
              {team?.full_name ?? identity.name}
            </p>
          </div>
        </div>
        <Link
          href="#pick-your-team"
          className="-mr-1 shrink-0 rounded-md px-1 py-0.5 text-[12px] text-muted underline decoration-line underline-offset-[3px] transition-colors hover:text-foreground hover:decoration-muted"
        >
          change
        </Link>
      </header>

      <dl className="mx-5 mt-4 grid grid-cols-3 gap-3 border-b border-hairline pb-4">
        {/* R8.2: the record and point differential are the statistics season's, not the roster's. */}
        <Reading
          label={detail?.season ? `${detail.season} record` : "Record"}
          loading={detailPending}
          value={standing ? `${standing.wins}–${standing.losses}` : "—"}
        />
        <Reading
          label="Seed"
          loading={detailPending}
          value={standing?.playoff_rank ? ordinal(standing.playoff_rank) : "—"}
          sub={standing?.playoff_rank ? standing.conference : undefined}
        />
        <Reading
          label="Point diff."
          loading={detailPending}
          title="Average point differential per game"
          value={
            typeof diff === "number"
              ? `${diff > 0 ? "+" : diff < 0 ? "−" : ""}${Math.abs(diff).toFixed(1)}`
              : "—"
          }
          sub={typeof diff === "number" ? "/ game" : undefined}
        />
      </dl>

      <div className="flex flex-1 flex-col px-5 pb-4 pt-3.5">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-[12.5px] font-medium text-foreground">Highest league impact</h3>
          <span className="text-[11px] text-faint">percentile of scored players</span>
        </div>
        {rosterError ? (
          <div className="mt-2.5">
            <ErrorState message={`Could not load the roster: ${String(rosterError)}`} />
          </div>
        ) : !roster ? (
          <div className="mt-2.5 space-y-2" role="status" aria-label="Loading roster">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 bg-panel3/70" />
            ))}
          </div>
        ) : top.length === 0 ? (
          <p className="mt-2.5 text-[12.5px] text-muted">No player on this roster has an impact estimate.</p>
        ) : (
          <ol className="mt-1.5">
            {top.map((p) => (
              <li key={p.player_id} className="flex items-center gap-3 py-1.5">
                <PlayerPhoto nbaPlayerId={p.nba_player_id} name={p.name} size={28} />
                <span className="min-w-0 flex-1">
                  <Link
                    href={`/players/${p.player_id}`}
                    className="block truncate text-[13.5px] font-medium text-foreground transition-colors hover:text-signal"
                  >
                    {p.name}
                  </Link>
                </span>
                <span className="shrink-0 text-[11.5px] text-faint">{p.position ?? "—"}</span>
                <ImpactInline tei={p.tei} className="w-[4.5rem] shrink-0 justify-end" />
              </li>
            ))}
          </ol>
        )}
        {roster && (
          <p className="mt-2 text-[11.5px] text-muted">
            {count(roster.roster.length, "rostered player")} · {scored.length} scored
            {unscored > 0 && <span className="text-unavail"> · {unscored} unscored</span>}
          </p>
        )}
        {/* Two retrievals feed this card — the standings row (record, seed, differential)
            and the roster (the players) — and they are not always the same sync, so each
            gets its own date. Both are served without an offset and are UTC. */}
        <SourceRail
          className="mt-auto"
          source={roster?.source ?? "NBA.com via nba_api"}
          extra={
            <span className="grid basis-full grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
              <span>Standings</span>
              <span className="tabular-nums">
                {standing?.source_retrieved_at
                  ? formatDate(asUtc(standing.source_retrieved_at))
                  : detailPending
                    ? "reading…"
                    : "—"}
              </span>
              {roster && (
                <>
                  <span>Roster</span>
                  <span className="tabular-nums">
                    {roster.source_retrieved_at ? formatDate(asUtc(roster.source_retrieved_at)) : "—"}
                  </span>
                </>
              )}
            </span>
          }
        />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- the board */

export function TeamBoard({
  teams,
  error,
  chosenId,
  onPick,
}: {
  teams: Team[] | undefined;
  error: unknown;
  chosenId: string | null;
  onPick: (team: Team) => void;
}) {
  if (error) {
    return (
      <div className="panel p-5">
        <ErrorState message={`Could not load teams: ${String(error)}`} />
      </div>
    );
  }

  return (
    <div className="panel p-4 sm:p-5">
      <TeamGrid
        teams={teams}
        selectedIds={chosenId ? [chosenId] : []}
        onSelect={onPick}
        columns="grid-cols-5"
        ariaLabelFor={(team, { selected }) =>
          selected ? `${team.full_name} is your team` : `Set ${team.full_name} as your team`
        }
      />
      <p className="mt-5 border-t border-hairline pt-3 text-[12px] text-faint">
        Stored in this browser only · Team Outlook and the Trade Evaluator open on it
      </p>
    </div>
  );
}
