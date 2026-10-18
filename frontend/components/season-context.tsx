/**
 * R8.2 — say which season a number belongs to.
 *
 * A roster is *now* (the league year); statistics are the latest completed season. They differ from
 * 1 July until the new season has a sample, and a screen that shows both without saying so lets a
 * traded player's old production read as his new team's. These are the two small pieces that say it.
 */

import { Badge } from "@/components/ui";
import type { SeasonBasis, SeasonContext } from "@/lib/types";

/** "2026-27 roster · 2025-26 regular-season statistics", with a provisional badge when it applies. */
export function SeasonContextLine({
  context,
  rosterSeason,
  statisticsSeason,
  className,
}: {
  context?: SeasonContext;
  rosterSeason?: string;
  statisticsSeason?: string;
  className?: string;
}) {
  const roster = context?.roster.season ?? rosterSeason;
  const stats = context?.performance.season ?? statisticsSeason;
  if (!roster || !stats) return null;
  const provisional = context?.roster.provisional ?? false;
  const fellBack = context?.roster.fell_back ?? false;
  return (
    <p
      data-testid="season-context"
      className={`flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12.5px] text-muted ${className ?? ""}`}
    >
      <span>
        <span className="text-foreground">{roster} roster</span>
      </span>
      {provisional && (
        <Badge status={fellBack ? "stale" : "info"}>
          {fellBack ? "previous roster — new one not ingested" : "provisional"}
        </Badge>
      )}
      <span aria-hidden className="text-faint">
        ·
      </span>
      <span>
        statistics: <span className="text-foreground">{stats} regular season</span>
        {roster !== stats && <span className="text-faint"> (latest completed)</span>}
      </span>
    </p>
  );
}

/** What an evaluation could not take from the roster's own season, in the server's words. */
export function SeasonBasisNotice({
  basis,
  warnings = [],
}: {
  basis?: SeasonBasis;
  /** Players arriving who are on no current roster (see `TeamEvaluation.membership_warnings`). */
  warnings?: { player_id: string; message: string }[];
}) {
  const limitations = basis?.limitations ?? [];
  if (limitations.length === 0 && warnings.length === 0) return null;
  return (
    <div
      role="note"
      data-testid="season-basis-notice"
      className="rounded-lg border border-conditional/45 bg-conditional/8 px-4 py-3 text-[13px] text-muted"
    >
      <p className="font-semibold text-foreground">
        Limited analysis{basis ? ` · ${basis.roster_season} roster` : ""}
      </p>
      <ul className="mt-1.5 list-disc space-y-1 pl-4">
        {limitations.map((l) => (
          <li key={l.reason}>{l.message}</li>
        ))}
        {warnings.map((w) => (
          <li key={w.player_id}>{w.message}</li>
        ))}
      </ul>
    </div>
  );
}
