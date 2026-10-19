"use client";

/**
 * R9.3A — the player carried from Player Explorer into the Trade Evaluator.
 *
 * The link carried canonical ids and the statistics season the user was reading (lib/investigation.ts);
 * this notice *verifies* them against the roster the evaluator itself loaded and says what it found. It
 * never applies context it could not verify, and it names the seasons it is mixing: the salary is for the
 * financial season, the evidence the user was reading is from the statistics season, and they can differ.
 */

import Link from "next/link";
import { money } from "@/lib/format";
import { playerFileLink, type InvestigationContext } from "@/lib/investigation";
import type { RosterPlayer, Team } from "@/lib/types";
import { Badge } from "@/components/ui";

export function StagedPlayerNotice({
  playerId,
  context,
  teams,
  teamIds,
  rosterLoaded,
  rosterRow,
  originTeamId,
  movedTo,
  onDismiss,
}: {
  playerId: string;
  context: InvestigationContext;
  teams: Team[];
  teamIds: string[];
  rosterLoaded: boolean;
  rosterRow: RosterPlayer | undefined;
  originTeamId: string | null;
  movedTo: string | null;
  onDismiss: () => void;
}) {
  const teamName = (id: string | null) => teams.find((t) => t.id === id)?.abbreviation ?? "?";
  const verified = Boolean(rosterRow && originTeamId);
  const financialSeason = rosterRow?.salary_season ?? null;
  const differs = Boolean(context.statsSeason && financialSeason && context.statsSeason !== financialSeason);

  return (
    <aside
      aria-label="Player carried from Player Explorer"
      className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 rounded-xl border border-signal/35 bg-signal/5 px-5 py-4"
    >
      <div className="min-w-0 text-[13px] leading-relaxed text-muted">
        <div className="flex flex-wrap items-center gap-2">
          <Badge status="info">Carried from Player Explorer</Badge>
          {rosterLoaded && !verified && <Badge status="warning">not verified</Badge>}
        </div>
        {!rosterLoaded ? (
          <p className="mt-2">Checking the player against the {teamName(teamIds[0])} roster…</p>
        ) : verified && rosterRow ? (
          <>
            <p className="mt-2">
              <Link href={playerFileLink(playerId)} className="font-medium text-foreground hover:text-signal">
                {rosterRow.name}
              </Link>{" "}
              is on the {teamName(originTeamId)} roster
              {rosterRow.salary !== null
                ? ` with a ${financialSeason ?? "current-year"} cap hit of ${money(rosterRow.salary)}`
                : " with no cap hit on file (unknown, not $0)"}
              .{" "}
              {movedTo
                ? `He is in the deal, going to ${teamName(movedTo)}.`
                : teamIds.length < 2
                  ? "Choose the team he would go to and he is placed in the deal."
                  : "Move him across the lane to place him in the deal."}
            </p>
            {differs && (
              <p className="mt-1 text-[12px] text-faint">
                Seasons differ: the salary is for {financialSeason}; the performance evidence you were reading is from{" "}
                {context.statsSeason}.
              </p>
            )}
          </>
        ) : (
          <p className="mt-2">
            Pivot could not find that player on the {teamName(teamIds[0])} roster it loaded, so the carried
            context was <strong className="font-medium text-foreground">not applied</strong> — the link may be from
            an earlier roster snapshot. Search the roster below.
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="rounded-md px-2 py-1 text-[12px] text-muted hover:text-foreground"
        aria-label="Dismiss the carried-player notice"
      >
        Dismiss
      </button>
    </aside>
  );
}
