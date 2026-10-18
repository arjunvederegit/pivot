"use client";

/**
 * Team-conditional fit — how well one player answers one roster's measured needs.
 *
 * Read from `GET /intelligence/fit`. Three things about it are easy to get wrong, and
 * every component here shows them rather than hiding them:
 *
 * 1. **50 is neutral.** The scale is 0–100 where 50 is what an addition that changes
 *    nothing receives. It is drawn as a deviation from 50, never as a bar from zero.
 * 2. **It measures direction, not size.** Minutes are normalised inside the model, so an
 *    8-minute player who answers a need scores like a 32-minute one. It is not a talent
 *    rating and is never shown as one.
 * 3. **It can be withheld.** When the team has no need strong enough, or the player has
 *    no measured skills, the API returns no score and says why in `detail.unavailable`.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { api } from "@/lib/api";
import { playerTeamFitSchema } from "@/lib/schemas";
import type { PlayerTeamFit } from "@/lib/types";

export function useTeamFit(playerId: string | undefined, teamId: string | undefined) {
  return useQuery({
    queryKey: ["fit", playerId, teamId],
    queryFn: () =>
      api.get<PlayerTeamFit>(
        `/intelligence/fit?player_id=${playerId}&team_id=${teamId}`,
        playerTeamFitSchema,
      ),
    staleTime: 300_000,
    enabled: Boolean(playerId && teamId),
  });
}

export function fitWithheldReason(fit: PlayerTeamFit | undefined): string | null {
  if (!fit || fit.available) return null;
  if (fit.already_on_roster) return "already on this roster";
  const reason = fit.detail?.unavailable;
  return typeof reason === "string" && reason ? reason : "Pivot returned no fit score";
}

/** A horizontal track centred at 50, filled toward the score. */
export function FitDeviation({ score, className }: { score: number; className?: string }) {
  const delta = score - 50; // −50..+50
  const width = Math.min(50, Math.abs(delta));
  return (
    <span
      aria-hidden
      className={clsx("relative block h-1.5 w-full rounded-sm bg-panel3", className)}
    >
      <span className="absolute inset-y-[-2px] left-1/2 w-px bg-faint" />
      <span
        className="absolute inset-y-0 rounded-sm"
        style={{
          left: delta >= 0 ? "50%" : `${50 - width}%`,
          width: `${width}%`,
          background: delta >= 0 ? "var(--signal)" : "var(--illegal)",
          opacity: 0.85,
        }}
      />
    </span>
  );
}

/** Compact: "FIT·BOS 62 ▸" for a row. Withheld fits say so in a word. */
export function FitChip({
  playerId,
  teamId,
  teamAbbr,
  className,
}: {
  playerId: string;
  teamId: string;
  teamAbbr: string;
  className?: string;
}) {
  const { data, isPending, isError } = useTeamFit(playerId, teamId);
  const withheld = fitWithheldReason(data);
  const score = data?.available ? data.score : null;

  return (
    <span
      className={clsx(
        "inline-flex min-w-0 items-center gap-1.5 whitespace-nowrap rounded-sm border border-line px-1.5 py-[3px] font-mono text-[10.5px]",
        className,
      )}
      title={
        score !== null
          ? `Fit with ${teamAbbr}: ${score.toFixed(0)} on a 0–100 scale where 50 changes nothing. Direction of change against this roster's needs — not a talent rating, and not the size of the improvement.`
          : (withheld ?? "Loading fit")
      }
    >
      <span className="uppercase tracking-[0.06em] text-faint">fit·{teamAbbr}</span>
      {isPending ? (
        <span className="text-faint">··</span>
      ) : isError ? (
        <span className="text-unavail">n/a</span>
      ) : score !== null ? (
        <>
          <span className="font-semibold text-foreground">{score.toFixed(0)}</span>
          <span
            className={score > 52 ? "text-legal" : score < 48 ? "text-illegal" : "text-faint"}
            aria-hidden
          >
            {score > 52 ? "▲" : score < 48 ? "▼" : "■"}
          </span>
          <span className="sr-only">{score > 52 ? "helps" : score < 48 ? "hurts" : "neutral"}</span>
        </>
      ) : (
        <span className="text-unavail">withheld</span>
      )}
    </span>
  );
}

/** The drawer/page version: score, the deviation track, and the honest caveats. */
export function FitReadout({
  playerId,
  teamId,
  teamAbbr,
  teamName,
}: {
  playerId: string;
  teamId: string;
  teamAbbr: string;
  teamName?: string;
}) {
  const { data, isPending, isError } = useTeamFit(playerId, teamId);
  const withheld = fitWithheldReason(data);
  const score = data?.available ? data.score : null;

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <span className="eyebrow truncate">Fit with {teamName ?? teamAbbr}</span>
        <span className="font-mono text-[10px] text-faint">50 = no change</span>
      </div>
      {isPending ? (
        <div className="skeleton mt-2 h-8" aria-hidden />
      ) : isError ? (
        <p className="mt-1.5 text-[12px] text-unavail">The fit request failed — no score shown.</p>
      ) : score !== null ? (
        <>
          <div className="mt-1.5 flex items-baseline gap-2">
            <span className="numeral text-[1.5rem] leading-none text-foreground">
              {score.toFixed(0)}
            </span>
            <span className="font-mono text-[11px] text-muted">
              {score > 52 ? "answers needs" : score < 48 ? "works against needs" : "roughly neutral"}
            </span>
          </div>
          <FitDeviation score={score} className="mt-2" />
          <p className="mt-1.5 text-[11px] leading-snug text-faint">
            Direction against this roster&apos;s measured needs — not a talent rating, and not
            how much the team improves.
          </p>
        </>
      ) : (
        <p className="mt-1.5 text-[12px] leading-snug text-muted">
          <span className="font-semibold text-unavail">Withheld.</span> {withheld}
        </p>
      )}
    </div>
  );
}
