"use client";

/**
 * The roster by listed position — every player as a card with his league impact cell,
 * grouped Guards / Wings / Bigs and sorted by impact inside each group.
 *
 * When the card is wide enough the three groups stand side by side like a depth chart,
 * which shows a lopsided roster at a glance (seven guards, four bigs); narrower, they
 * stack. It is a listing sorted by the impact estimate, not the coaching staff's rotation,
 * and the header says how it was sorted. Players with no estimate are listed, never
 * ranked, and the data-quality line names them.
 */

import Link from "next/link";
import { count, height } from "@/lib/format";
import type { RosterPlayer, TeamProfile } from "@/lib/types";
import { ImpactCell } from "@/components/impact";
import { PlayerPhoto } from "@/components/media";
import { SourceRail } from "@/components/ui";
import { POSITION_GROUPS, positionGroup } from "@/components/teams/composition";

const GROUPS = [...POSITION_GROUPS, "No listed position"] as const;
type Group = (typeof GROUPS)[number];

function PlayerCard({ player, topEight }: { player: RosterPlayer; topEight: boolean }) {
  return (
    <Link
      href={`/players/${player.player_id}`}
      className="group flex min-w-0 items-center gap-3 rounded-lg border border-hairline bg-panel2/45 py-2.5 pl-2.5 pr-3 transition-colors hover:border-line hover:bg-panel2"
    >
      <PlayerPhoto nbaPlayerId={player.nba_player_id} name={player.name} size={38} />
      <span className="min-w-0 flex-1">
        <span className="block text-[14px] font-semibold leading-tight text-foreground group-hover:text-signal sm:truncate">
          {player.name}
        </span>
        <span className="mt-1 block truncate text-[12px] leading-tight text-muted">
          {player.position ?? "—"} · {player.age !== null ? `${player.age.toFixed(0)}y` : "age —"} ·{" "}
          {height(player.height_inches)}
        </span>
        {(topEight || player.archetype) && (
          <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11.5px] leading-tight text-faint">
            {topEight && (
              <span
                className="shrink-0 rounded-full border border-signal/35 px-1.5 text-[10.5px] font-medium leading-[15px] text-signal"
                title="One of the 8 highest-impact players — their average age sets the competitive window"
              >
                top 8
              </span>
            )}
            {player.archetype && (
              <span className="truncate" title={player.archetype}>
                {player.archetype}
              </span>
            )}
          </span>
        )}
      </span>
      <ImpactCell tei={player.tei} band={{ low: player.tei_low, high: player.tei_high }} name={player.name} />
    </Link>
  );
}

export function RosterDepth({
  players,
  topEightIds,
  profile,
  source,
  retrievedAt,
}: {
  players: RosterPlayer[];
  topEightIds: Set<string>;
  profile: TeamProfile | undefined;
  source: string;
  retrievedAt: string | null;
}) {
  const byGroup: Record<Group, RosterPlayer[]> = {
    Guards: [],
    Wings: [],
    Bigs: [],
    "No listed position": [],
  };
  for (const player of players) byGroup[positionGroup(player.position) ?? "No listed position"].push(player);
  for (const group of GROUPS) byGroup[group].sort((a, b) => (b.tei ?? -99) - (a.tei ?? -99));

  const missing = profile?.players_without_impact_estimate ?? [];
  const shown = GROUPS.filter((group) => byGroup[group].length > 0);

  return (
    // A flex column so the data-quality note and source rail sit on the card's bottom edge.
    <div className="@container flex flex-1 flex-col">
      <div className="grid grid-cols-1 divide-y divide-hairline @min-[64rem]:grid-cols-3 @min-[64rem]:divide-x @min-[64rem]:divide-y-0">
        {shown.map((group) => (
          <section key={group} aria-label={group} className="min-w-0 px-4 py-4 sm:px-5">
            <div className="flex items-baseline justify-between gap-3">
              <h4 className="font-serif text-[1.4rem] leading-none text-foreground">{group}</h4>
              <span className="text-[11.5px] text-faint">
                {byGroup[group].length} · by league impact
              </span>
            </div>
            <ul className="mt-3 grid gap-2 @min-[36rem]:grid-cols-2 @min-[64rem]:grid-cols-1">
              {byGroup[group].map((player) => (
                <li key={player.player_id} className="min-w-0">
                  <PlayerCard player={player} topEight={topEightIds.has(player.player_id) && player.tei !== null} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <div className="mt-auto border-t border-hairline px-4 pb-4 pt-3.5 sm:px-5">
        {profile && missing.length > 0 && (
          <p className="flex items-start gap-2 text-[12.5px] leading-relaxed text-muted">
            <span aria-hidden className="mt-px text-unavail">
              —
            </span>
            <span>
              <span className="font-medium text-foreground">Data quality:</span>{" "}
              {count(missing.length, "player")} of {profile.roster_size} have no impact estimate (
              {missing.map((p) => p.name).join(", ")}). They are listed, not ranked, and skill
              coverage is computed without them.
            </span>
          </p>
        )}
        {profile && missing.length === 0 && (
          <p className="flex items-start gap-2 text-[12.5px] leading-relaxed text-muted">
            <span aria-hidden className="mt-px text-legal">
              ✓
            </span>
            <span>
              <span className="font-medium text-foreground">Data quality:</span> every one of the{" "}
              {count(profile.roster_size, "listed player")} has an impact estimate.
            </span>
          </p>
        )}
        <SourceRail
          source={source}
          retrievedAt={retrievedAt}
          extra={<span>· impact estimated by Pivot, not a provider metric</span>}
        />
      </div>
    </div>
  );
}
