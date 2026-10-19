"use client";

/**
 * R9.3A — the provider's injury status, framed by how old the reading is.
 *
 * The feed carries no report date, so the only date is when Pivot retrieved it. A reading from the last day is
 * "current as of" that time; an older one is "last reported" and flagged; no row and a closed row both say so
 * without saying "healthy". The words come from the backend (`/players/{id}/injury`); this lays them out.
 */

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatDate, formatDay } from "@/lib/format";
import { Badge, Panel, SkeletonRows, SourceRail } from "@/components/ui";

interface InjuryPayload {
  available: boolean;
  reason?: string;
  status?: string;
  description?: string | null;
  return_date?: string | null;
  return_date_passed?: boolean;
  retrieved_at?: string | null;
  age_hours?: number | null;
  freshness?: "fresh" | "stale";
  framing?: string;
  caveats?: string[];
  note?: string;
  last_feed_check?: string | null;
}

export function InjuryCard({ playerId }: { playerId: string }) {
  const { data, error } = useQuery({
    queryKey: ["player-injury", playerId],
    queryFn: () => api.get<InjuryPayload>(`/players/${playerId}/injury`),
    staleTime: 120_000,
  });
  return (
    <Panel title="Injury status" subtitle="From the BALLDONTLIE injury feed — dated by when Pivot read it">
      {error ? (
        <p className="text-[13px] text-muted">The injury feed could not be read: {String(error)}</p>
      ) : !data ? (
        <SkeletonRows rows={2} height="h-6" />
      ) : data.available ? (
        <div className="space-y-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge status={data.freshness === "fresh" ? "warning" : "stale"}>{data.status}</Badge>
            <span className="text-[12.5px] text-muted">{data.framing}</span>
          </div>
          <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-y-1 text-[13px]">
            <dt className="text-faint">Expected return</dt>
            <dd className="text-foreground">{data.return_date ? formatDay(data.return_date) : "not reported"}</dd>
            <dt className="text-faint">Pivot read it</dt>
            <dd className="text-foreground">{formatDate(data.retrieved_at)}</dd>
          </dl>
          {data.description && <p className="text-[12.5px] leading-relaxed text-muted">{data.description}</p>}
          {(data.caveats ?? []).map((c) => (
            <p key={c} className="text-[12px] leading-relaxed text-conditional">
              {c}
            </p>
          ))}
          <p className="text-[11.5px] leading-relaxed text-faint">{data.note}</p>
          <SourceRail source="BALLDONTLIE injuries" retrievedAt={data.retrieved_at ?? null} />
        </div>
      ) : (
        <div className="space-y-1.5">
          <p className="text-[13px] leading-relaxed text-muted">{data.reason}</p>
          <p className="text-[11.5px] leading-relaxed text-faint">{data.note}</p>
          {data.last_feed_check && <SourceRail source="BALLDONTLIE injuries" retrievedAt={data.last_feed_check} />}
        </div>
      )}
    </Panel>
  );
}
