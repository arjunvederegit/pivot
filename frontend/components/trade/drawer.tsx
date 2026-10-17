"use client";

/**
 * The player file that slides in from a roster card: league impact with its band, fit
 * against every other team in the deal, availability, salary (or that it is not on file)
 * and the last season's per-game line. A modal dialog: focus moves in when it opens, Tab
 * stays inside it, Escape closes it, and focus returns to whatever opened it.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import type { RosterPlayer, Team } from "@/lib/types";
import { FitReadout } from "@/components/fit";
import { IconClose } from "@/components/icons";
import { ImpactReadout } from "@/components/impact";
import { PlayerPhoto } from "@/components/media";
import { Badge, ButtonLink, Skeleton, SkeletonRows, StatBlock } from "@/components/ui";
import { CONTRACT_TOOLTIP } from "@/components/trade/verdict";
import type { Destination } from "@/components/trade/workspace";

export function PlayerDrawer({
  player,
  rosterRow,
  fitTeams,
  onClose,
}: {
  player: { id: string; name: string };
  /** The builder's roster row, when the player is on a roster in the deal. */
  rosterRow?: RosterPlayer;
  /** The other teams in the deal — the ones this player could be traded to. */
  fitTeams: Destination[];
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // Focus moves into the dialog once, when it opens — not on every parent re-render — and
  // goes back to whatever opened it when it closes.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => opener?.focus?.();
  }, []);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      // aria-modal promises the page behind is out of reach: keep Tab inside the panel.
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusables = panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const inside = panelRef.current.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || !inside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !inside)) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const { data } = useQuery({
    queryKey: ["player", player.id],
    queryFn: () =>
      api.get<{
        player: {
          full_name: string;
          nba_player_id: number;
          position: string | null;
          birth_date: string | null;
          current_team: Team | null;
        };
        impact: {
          tei?: number;
          tei_offense?: number | null;
          tei_defense?: number | null;
          tei_range_10_90?: [number, number] | null;
          availability?: number | null;
          note: string;
        };
        archetype: { label: string } | null;
      }>(`/players/${player.id}`),
  });
  const { data: stats } = useQuery({
    queryKey: ["player-stats", player.id],
    queryFn: () =>
      api.get<{ seasons: { season: string; base?: Record<string, number> }[] }>(
        `/players/${player.id}/stats`,
      ),
  });
  const latest = stats?.seasons?.filter((s) => s.base).at(-1);
  const band = data?.impact.tei_range_10_90;

  return (
    <div
      className="fade-in fixed inset-0 z-[60] flex justify-end bg-ink/75"
      role="dialog"
      aria-modal="true"
      aria-label={`${player.name} details`}
      onClick={onClose}
    >
      <div
        ref={panelRef}
        className="scroll-thin h-full w-full max-w-md overflow-y-auto border-l border-line bg-arena p-5 shadow-[var(--shadow-pop)] sm:p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="eyebrow flex items-center gap-2">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />
              Player file
            </div>
            <h2 className="title-lg mt-2 text-balance text-foreground">{player.name}</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close player details"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-line text-muted transition-colors hover:bg-panel2 hover:text-foreground"
          >
            <IconClose size={16} />
          </button>
        </div>
        {!data ? (
          <div className="mt-5 space-y-3">
            <Skeleton className="h-20" />
            <SkeletonRows rows={3} height="h-14" />
          </div>
        ) : (
          <div className="mt-5 space-y-3">
            <div className="flex items-center gap-3.5">
              <PlayerPhoto
                nbaPlayerId={data.player.nba_player_id}
                name={data.player.full_name}
                size={64}
                square
              />
              <div className="min-w-0">
                <div className="text-[13px] text-muted">
                  {data.player.position ?? "—"}
                  {data.player.current_team ? ` · ${data.player.current_team.full_name}` : ""}
                </div>
                {data.archetype && (
                  <div className="mt-2">
                    {/* Role labels reach 31 characters ("unclassified (no listed
                        height)"), so the Badge must be allowed to wrap. */}
                    <Badge status="info" className="max-w-full whitespace-normal text-left">
                      {data.archetype.label}
                    </Badge>
                  </div>
                )}
              </div>
            </div>

            <div className="sheet p-4">
              <ImpactReadout
                tei={data.impact.tei ?? null}
                band={band ? { low: band[0], high: band[1] } : null}
                offense={data.impact.tei_offense ?? null}
                defense={data.impact.tei_defense ?? null}
              />
            </div>

            {fitTeams.length > 0 && (
              <div className="sheet space-y-4 p-4">
                {fitTeams.map((t) => (
                  <FitReadout
                    key={t.id}
                    playerId={player.id}
                    teamId={t.id}
                    teamAbbr={t.abbr}
                    teamName={t.name}
                  />
                ))}
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="sheet p-4">
                <StatBlock
                  size="sm"
                  label="Availability"
                  value={
                    data.impact.availability != null
                      ? `${(data.impact.availability * 100).toFixed(0)}%`
                      : "—"
                  }
                  note="share of games, recency-weighted"
                />
              </div>
              <div className="sheet p-4">
                <StatBlock
                  size="sm"
                  label="Salary"
                  value={rosterRow?.salary != null ? money(rosterRow.salary) : "—"}
                  note={
                    rosterRow?.salary != null
                      ? rosterRow.contract_years_remaining != null
                        ? `${rosterRow.contract_years_remaining} season(s) on file`
                        : "cap league year"
                      : "not on file — never estimated"
                  }
                  title={rosterRow?.salary != null ? undefined : CONTRACT_TOOLTIP}
                />
              </div>
            </div>
            {latest?.base && (
              <div className="sheet p-4">
                <div className="eyebrow">{latest.season} per game</div>
                <div className="mt-3 grid grid-cols-5 gap-1">
                  {(["PTS", "REB", "AST", "STL", "BLK"] as const).map((k) => (
                    <span key={k} className="text-center">
                      <span className="block text-[10.5px] font-medium tracking-[0.06em] text-faint">{k}</span>
                      <span className="stat-serif mt-1 block text-[1.45rem] leading-none text-foreground">
                        {latest.base?.[k]?.toFixed(1) ?? "—"}
                      </span>
                    </span>
                  ))}
                </div>
              </div>
            )}
            <ButtonLink href={`/players/${player.id}`} size="sm" className="w-full">
              Full player page <span aria-hidden>→</span>
            </ButtonLink>
            <p className="text-[11.5px] leading-snug text-faint">{data.impact.note}</p>
          </div>
        )}
      </div>
    </div>
  );
}
