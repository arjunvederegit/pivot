"use client";

/**
 * The player file's header — a scouting report's top sheet.
 *
 * It reads in the order a front office asks: who he is (portrait, name, team, position,
 * role), the body and career facts, what to do next, then the headline line — where his
 * on-court impact sits in the league and what his latest season looks like. Every figure
 * is served: impact is the model's estimate placed among the players it scored (or
 * "unscored", never zero), and the season line is the latest row of the player's own
 * stats endpoint, with its games played beside it.
 */

import clsx from "clsx";
import Link from "next/link";
import { height } from "@/lib/format";
import { QUALIFY_MIN } from "@/lib/playerStats";
import { teamIdentity } from "@/lib/teamIdentity";
import type { ArchetypeAssignment, Provenance, SeasonContext, Team } from "@/lib/types";
import { tradeEvaluatorLink } from "@/lib/investigation";
import { SeasonContextLine } from "@/components/season-context";
import { PercentileFigure, useImpactPlacement } from "@/components/impact";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { Badge, ButtonLink, Skeleton, SourceRail } from "@/components/ui";
import type { SeasonLine } from "@/components/players/career";
import type { PlayerImpact } from "@/components/players/profile";

export interface PlayerRecord {
  id: string;
  nba_player_id: number;
  full_name: string;
  is_active: boolean;
  position: string | null;
  birth_date: string | null;
  height_inches: number | null;
  weight_lbs: number | null;
  years_experience: number | null;
  current_team: Team | null;
  provenance: Provenance | null;
}

const POSITION_WORDS: Record<string, string> = { G: "Guard", F: "Forward", C: "Center" };

/** "C" → "Center", "F-G" → "Forward-guard". Anything unrecognised is shown as served. */
export function positionName(position: string | null): string | null {
  if (!position) return null;
  const parts = position.toUpperCase().split("-");
  if (!parts.every((p) => POSITION_WORDS[p])) return position;
  return parts.map((p, i) => (i === 0 ? POSITION_WORDS[p] : POSITION_WORDS[p].toLowerCase())).join("-");
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function PlayerFileHeader({
  player,
  impact,
  archetype,
  age,
  seasonContext,
  seasons,
  seasonsSource,
  seasonsError,
}: {
  player: PlayerRecord;
  impact: PlayerImpact;
  archetype: ArchetypeAssignment | null;
  age: number | null;
  /** R8.2: the roster / statistics seasons, so a new team never reads as having earned an old line. */
  seasonContext?: SeasonContext;
  /** The player's season rows, oldest first; undefined while loading. */
  seasons: SeasonLine[] | undefined;
  /** Where the season rows came from — the rail names it beside the bio's source. */
  seasonsSource: string | undefined;
  seasonsError: unknown;
}) {
  const team = player.current_team;
  const identity = teamIdentity(team?.abbreviation);
  // R9.3A: the canonical player id, the team he is on in the roster snapshot, and the statistics season behind
  // the evidence on this page — nothing else travels (lib/investigation.ts).
  const tradeLink = tradeEvaluatorLink({
    playerId: player.id,
    teamId: team?.id ?? null,
    statsSeason: seasonContext?.performance?.season ?? null,
  });
  const scored = impact.tei !== undefined && impact.tei !== null;
  const position = positionName(player.position);
  const role = archetype?.label
    ? archetype.label.charAt(0).toUpperCase() + archetype.label.slice(1)
    : null;

  return (
    <section
      aria-labelledby="player-name"
      className="panel overflow-hidden"
      style={{ "--edge": identity.bright } as React.CSSProperties}
    >
      <div className="relative isolate">
        {/* The portrait sits in its team's light; a few warm planks underfoot. Atmosphere
            only — nothing here is data. */}
        <div
          aria-hidden
          className="floorboards pointer-events-none absolute inset-0 -z-10"
          style={{ maskImage: "linear-gradient(to right, black, transparent 55%)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10"
          style={{
            background:
              "radial-gradient(560px 300px at 0% 0%, color-mix(in srgb, var(--team-primary) 34%, transparent), transparent 70%)",
          }}
        />

        <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-4 gap-y-4 p-5 md:grid-cols-[152px_minmax(0,1fr)] md:gap-x-8 md:gap-y-0 md:p-7">
          <PlayerPhoto
            nbaPlayerId={player.nba_player_id}
            name={player.full_name}
            size={152}
            square
            className="col-start-1 row-start-1 !h-[88px] !w-[88px] shadow-[0_18px_40px_-22px_rgb(0_0_0/0.9)] md:row-span-3 md:!h-[152px] md:!w-[152px] md:!rounded-xl"
          />

          {/* team and position — beside the portrait on a phone, above the name from md */}
          <div className="col-start-2 row-start-1 min-w-0 self-center md:self-start md:pt-1">
            {team ? (
              <Link
                href={`/team-outlook/${team.id}`}
                className="group inline-flex max-w-full items-center gap-2 text-[13.5px] text-foreground"
              >
                <TeamLogo abbreviation={team.abbreviation} size={22} decorative />
                <span className="truncate font-medium group-hover:underline group-hover:decoration-line group-hover:underline-offset-4">
                  {team.full_name}
                </span>
              </Link>
            ) : (
              <Badge status="unavailable">no current team on file</Badge>
            )}
            <p className="mt-1.5 text-[12.5px] text-muted">
              {position ?? "Position not on file"}
            </p>
            <SeasonContextLine context={seasonContext} className="mt-1" />
          </div>

          {/* name, role and status */}
          <div className="col-span-2 min-w-0 md:col-span-1 md:col-start-2 md:mt-3">
            <h1 id="player-name" className="title-xl text-foreground">
              {player.full_name}
            </h1>
            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
              {role && (
                <p className="text-[14px] text-muted">
                  <span className="eyebrow mr-2 text-faint">Role</span>
                  <span className="font-serif text-[1.3rem] italic leading-none text-foreground">
                    {role}
                  </span>
                </p>
              )}
              {!scored && <Badge status="unavailable">unscored · no impact estimate</Badge>}
              {!player.is_active && <Badge status="unavailable">not active</Badge>}
            </div>
          </div>

          {/* bio facts and the next step */}
          <div className="col-span-2 flex min-w-0 flex-wrap items-end justify-between gap-x-8 gap-y-4 md:col-span-1 md:col-start-2 md:mt-5 md:self-end">
            <dl className="flex flex-wrap gap-x-6 gap-y-3">
              <BioFact label="Age" value={age !== null ? String(age) : "—"} />
              <BioFact label="Height" value={height(player.height_inches)} />
              <BioFact label="Weight" value={player.weight_lbs ? `${player.weight_lbs} lb` : "—"} />
              <BioFact
                label="Experience"
                value={
                  player.years_experience !== null
                    ? `${player.years_experience} ${player.years_experience === 1 ? "yr" : "yrs"}`
                    : "—"
                }
              />
            </dl>
            {team && (
              <div className="flex flex-wrap gap-2">
                {tradeLink && (
                  <ButtonLink href={tradeLink} variant="primary" size="sm">
                    Evaluate {player.full_name} in a trade
                  </ButtonLink>
                )}
                <ButtonLink href={`/trade-evaluator?team=${team.id}`} size="sm">
                  Open {team.abbreviation} in Trade Evaluator
                </ButtonLink>
                <ButtonLink href={`/player-explorer?team=${team.id}`} size="sm">
                  Compare {team.abbreviation} players
                </ButtonLink>
              </div>
            )}
          </div>
        </div>
      </div>

      <ScoutingLine
        tei={impact.tei}
        band={impact.tei_range_10_90 ?? null}
        seasons={seasons}
        seasonsError={seasonsError}
      />

      <div className="px-5 pb-3 md:px-7">
        <SourceRail
          className="mt-0 border-t-0 pt-3"
          source={player.provenance?.upstream ?? "unknown source"}
          retrievedAt={player.provenance?.source_retrieved_at}
          extra={
            seasonsSource ? <span>· season line: {seasonsSource}</span> : undefined
          }
        />
      </div>
    </section>
  );
}

function BioFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="eyebrow text-faint">{label}</dt>
      <dd className="numeral mt-1.5 whitespace-nowrap text-[15px] font-medium leading-none text-foreground">
        {value}
      </dd>
    </div>
  );
}

/* ------------------------------------------------------------ headline line */

/**
 * The file's headline figures: league impact position, then the latest season's line.
 * The impact percentile is placed among the players the model scored — the same
 * placement the Overview readout draws in full — and an unscored player reads
 * "unscored", never a number.
 */
function ScoutingLine({
  tei,
  band,
  seasons,
  seasonsError,
}: {
  tei: number | null | undefined;
  band: [number, number] | null;
  seasons: SeasonLine[] | undefined;
  seasonsError: unknown;
}) {
  const latest = seasons?.at(-1);
  const gp = latest ? (num(latest.base?.GP) ?? num(latest.advanced?.GP)) : null;
  const small = gp !== null && gp < QUALIFY_MIN;
  const seasonNote = seasonsError
    ? "did not load"
    : seasons && !latest
      ? "no season on file"
      : "per game";

  return (
    <dl className="grid grid-cols-2 gap-px border-t border-hairline bg-hairline sm:grid-cols-4 xl:grid-cols-[minmax(0,1.5fr)_repeat(4,minmax(0,1fr))]">
      <ImpactCell tei={tei} band={band} />
      <LineCell
        label="Points"
        value={latest ? num(latest.base?.PTS) : null}
        loading={!seasons && !seasonsError}
        note={seasonNote}
      />
      <LineCell
        label="Rebounds"
        value={latest ? num(latest.base?.REB) : null}
        loading={!seasons && !seasonsError}
        note={seasonNote}
      />
      <LineCell
        label="Assists"
        value={latest ? num(latest.base?.AST) : null}
        loading={!seasons && !seasonsError}
        note={seasonNote}
      />
      <div className="min-w-0 bg-panel px-5 py-4 md:px-7">
        <dt className="eyebrow">Games</dt>
        <dd className="mt-2">
          {!seasons && !seasonsError ? (
            <Skeleton className="h-8 w-16" />
          ) : (
            <span className="stat-serif block text-[2.1rem] leading-none text-foreground">
              {gp ?? "—"}
            </span>
          )}
          <span
            className={clsx("mt-1.5 block text-[12px]", small ? "text-unavail" : "text-muted")}
          >
            {latest ? `${latest.season}${small ? ` · under ${QUALIFY_MIN}, a small sample` : " season"}` : seasonNote}
          </span>
        </dd>
      </div>
    </dl>
  );
}

function LineCell({
  label,
  value,
  loading,
  note,
}: {
  label: string;
  value: number | null;
  loading: boolean;
  note: string;
}) {
  return (
    <div className="min-w-0 bg-panel px-5 py-4 md:px-7">
      <dt className="eyebrow">{label}</dt>
      <dd className="mt-2">
        {loading ? (
          <Skeleton className="h-8 w-16" />
        ) : (
          <span className="stat-serif block text-[2.1rem] leading-none text-foreground">
            {value !== null ? value.toFixed(1) : "—"}
          </span>
        )}
        <span className="mt-1.5 block text-[12px] text-muted">{note}</span>
      </dd>
    </div>
  );
}

function ImpactCell({
  tei,
  band,
}: {
  tei: number | null | undefined;
  band: [number, number] | null;
}) {
  const { placement, loading, failed, unavailableReason } = useImpactPlacement(
    tei,
    band ? { low: band[0], high: band[1] } : null,
  );
  const unscored = tei === null || tei === undefined;

  return (
    <div className="col-span-2 min-w-0 bg-panel px-5 py-4 sm:col-span-4 md:px-7 xl:col-span-1">
      <dt className="eyebrow">On-court impact</dt>
      <dd className="mt-2">
        {unscored ? (
          <>
            <span className="stat-serif block text-[2.1rem] leading-none text-unavail">
              Unscored
            </span>
            <span className="mt-1.5 block text-[12px] text-muted">
              No impact estimate — never ranked at zero
            </span>
          </>
        ) : placement ? (
          <>
            <span className="flex items-baseline gap-2">
              <PercentileFigure
                percentile={placement.percentile}
                serif
                className="text-[2.1rem] text-foreground"
              />
              <span className="text-[12px] text-muted">league percentile</span>
            </span>
            <span className="mt-1.5 block text-[12px] text-muted">
              Ranked{" "}
              <span className="numeral text-foreground">
                {placement.tied > 1 ? "tied " : ""}#{placement.rank}
              </span>{" "}
              of {placement.size} scored
            </span>
          </>
        ) : loading ? (
          <Skeleton className="h-8 w-28" />
        ) : (
          <span className="block text-[12px] text-muted">
            {failed
              ? "The league reference did not load, so no percentile is shown."
              : (unavailableReason ?? "No league reference is available.")}
          </span>
        )}
      </dd>
    </div>
  );
}
