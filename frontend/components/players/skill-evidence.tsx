"use client";

/**
 * R9.3A — Player Explorer's view of the shared Player Intelligence service (R9.2) and of BALLDONTLIE's
 * shot-zone distribution. Three layers, in the order a reader should trust them:
 *
 *  1. **Observed performance** — measured values for one named season, each with what it rests on.
 *  2. **Research preview** — inferred ability and next-season projections from candidate models. Closed by
 *     default, fetched only when opened, and every value carries the word *experimental*, its model version,
 *     its evaluation status, its interval and the condition it holds under.
 *  3. **Shot profile** — where the attempts came from (descriptive; not a skill estimate).
 *
 * Nothing here computes a statistic: values, intervals, reliability sentences and the reasons a quantity is
 * unavailable are all the backend's (`GET /players/{id}/intelligence`, `GET /players/{id}/shot-zones`).
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import {
  QUANTITY_GROUPS,
  QUANTITY_LABEL,
  conditioningSentence,
  evaluationWords,
  evidenceTier,
  formatInterval,
  formatQuantity,
  sampleSentence,
  thinSample,
  type IntelRecord,
  type IntelResponse,
} from "@/lib/skillEvidence";
import { formatDate } from "@/lib/format";
import { Badge, Explainer, Panel, SkeletonRows, SourceRail, Td, Th, UnavailableNotice } from "@/components/ui";

const ENDPOINT = (id: string, query: string) => `/players/${id}/intelligence?${query}`;

/** The preview route is off unless the server enables it; that is an answer, not an error. */
function isDisabled(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/* ----------------------------------------------------------------------- observed */

export function ObservedPerformancePanel({ playerId }: { playerId: string }) {
  const { data, error } = useQuery({
    queryKey: ["player-intel-measured", playerId],
    queryFn: () => api.get<IntelResponse>(ENDPOINT(playerId, "kinds=measured")),
    staleTime: 300_000,
    retry: false,
  });
  if (error && isDisabled(error)) return null;
  const measured = new Map((data?.records ?? []).filter((r) => r.output_kind === "measured").map((r) => [r.quantity, r]));
  const season = data?.records.find((r) => r.output_kind === "measured")?.period;
  const ctx = (data?.context ?? {}) as { season?: { games?: number; minutes?: number } };
  const thin = [...measured.values()].some(thinSample);

  return (
    <Panel
      title="Observed performance"
      subtitle={
        season
          ? `${season.season} ${season.season_type} season · measured from the season's box-score totals, not estimated`
          : "Measured from the season's box-score totals, not estimated"
      }
      actions={
        ctx.season?.games !== undefined ? (
          <span className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
            <span>
              <span className="numeral text-foreground">{ctx.season.games}</span> games ·{" "}
              <span className="numeral text-foreground">{Math.round(ctx.season.minutes ?? 0)}</span> min
            </span>
            {thin && <Badge status="warning">thin season — read rates with care</Badge>}
          </span>
        ) : undefined
      }
    >
      {error ? (
        <UnavailableNotice reason={`The measured record could not be read: ${String(error)}`} />
      ) : !data ? (
        <SkeletonRows rows={5} height="h-8" />
      ) : measured.size === 0 ? (
        <UnavailableNotice reason="No measured season is on file for this player (for example, a rookie without a prior NBA season)." />
      ) : (
        <div className="space-y-5">
          {QUANTITY_GROUPS.map((group) => {
            const rows = group.quantities.map((q) => measured.get(q)).filter((r): r is IntelRecord => Boolean(r));
            if (rows.length === 0) return null;
            return (
              <div key={group.label}>
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="eyebrow">{group.label}</h3>
                  {group.note && <span className="text-[11.5px] text-faint">{group.note}</span>}
                </div>
                <table className="mt-1 w-full">
                  <caption className="sr-only">{group.label}, measured</caption>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.quantity} className="border-b border-hairline last:border-0">
                        <Td className="w-[46%] text-[13px] text-muted">{QUANTITY_LABEL[r.quantity] ?? r.quantity}</Td>
                        <Td numeric className="numeral w-[16%] text-[15px] text-foreground">
                          {formatQuantity(r.quantity, r.value)}
                        </Td>
                        <Td className="text-[12px] text-faint">
                          {sampleSentence(r) ?? ""}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
          {data.unavailable
            .filter((u) => u.kind === "measured")
            .map((u) => (
              <p key={u.quantity} className="text-[12px] text-faint">
                {QUANTITY_LABEL[u.quantity] ?? u.quantity}: {u.reason}
              </p>
            ))}
          <p className="text-[11.5px] leading-relaxed text-faint">
            A figure is for the season named above. A new team does not change what the season measured, and a thin
            season is flagged rather than smoothed over. Defence, shot location impact and an overall rating are not
            measured here — see the list below.
          </p>
          <SourceRail
            source={
              data.records[0]?.provenance.sources[0]
                ? `${data.records[0].provenance.sources[0].provider} · ${data.records[0].provenance.sources[0].dataset} · snapshot ${data.records[0].provenance.data_snapshot_id ?? "n/a"}`
                : "NBA.com season statistics"
            }
            retrievedAt={data.records[0]?.provenance.sources[0]?.retrieved_at ?? null}
          />
        </div>
      )}
    </Panel>
  );
}

/* ----------------------------------------------------------------- research preview */

function ModelCard({ record }: { record: IntelRecord }) {
  const tier = evidenceTier(record);
  const m = record.model;
  return (
    <Explainer summary="How this was computed — model, evidence and limits" className="mt-2">
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[9rem_minmax(0,1fr)]">
        <dt className="text-faint">Status</dt>
        <dd>
          {tier === "validated" ? "validated model" : "experimental candidate"} — {evaluationWords(m?.evaluation_status)}
        </dd>
        <dt className="text-faint">Model</dt>
        <dd className="[overflow-wrap:anywhere]">
          {m?.name} · <code>{m?.version}</code>
        </dd>
        {m?.methodology && (
          <>
            <dt className="text-faint">Method</dt>
            <dd>{m.methodology}</dd>
          </>
        )}
        {m?.evaluation_ref && (
          <>
            <dt className="text-faint">Evidence</dt>
            <dd className="[overflow-wrap:anywhere]">
              <code>{m.evaluation_ref}</code>
            </dd>
          </>
        )}
        <dt className="text-faint">Data snapshot</dt>
        <dd className="[overflow-wrap:anywhere]">
          <code>{record.provenance.data_snapshot_id ?? "n/a"}</code>
          {record.provenance.code_commit ? ` · code ${record.provenance.code_commit}` : ""}
        </dd>
        {record.interval && (
          <>
            <dt className="text-faint">Interval</dt>
            <dd>
              {record.interval.method}. {record.interval.interpretation}
            </dd>
          </>
        )}
        {record.sample?.seasons_used ? (
          <>
            <dt className="text-faint">Seasons used</dt>
            <dd>{record.sample.seasons_used}</dd>
          </>
        ) : null}
      </dl>
      {[...record.assumptions, ...record.limitations].length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {[...record.assumptions, ...record.limitations].map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      )}
    </Explainer>
  );
}

function ExperimentalRow({ record, statement }: { record: IntelRecord; statement: string | undefined }) {
  const tier = evidenceTier(record);
  const interval = formatInterval(record.quantity, record.interval);
  const condition = conditioningSentence(record);
  return (
    <li className="border-b border-hairline py-3 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <span className="text-[13px] text-muted">{QUANTITY_LABEL[record.quantity] ?? record.quantity}</span>
          <Badge status={tier === "validated" ? "pass" : "warning"} className="ml-2">
            {tier === "validated" ? "validated" : "experimental"}
          </Badge>
        </div>
        <div className="text-right">
          <span className="numeral text-[17px] text-foreground">{formatQuantity(record.quantity, record.value)}</span>
          {interval && <span className="ml-2 text-[12px] text-muted">{interval}</span>}
        </div>
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-muted">
        {record.output_kind === "inferred"
          ? `Estimate of underlying ability at the end of ${record.period.season} — not an observed rate.`
          : `Projection for ${record.target_period?.season ?? "the next season"}. ${condition ?? ""}`}
      </p>
      {statement && <p className="mt-1 text-[11.5px] leading-relaxed text-faint">{statement}</p>}
      <p className="mt-1 text-[11.5px] text-faint">
        {record.model?.version} · {evaluationWords(record.model?.evaluation_status)}
      </p>
      <ModelCard record={record} />
    </li>
  );
}

export function ResearchPreviewPanel({ playerId }: { playerId: string }) {
  const [open, setOpen] = useState(false);
  const probe = useQuery({
    queryKey: ["player-intel-measured", playerId],
    queryFn: () => api.get<IntelResponse>(ENDPOINT(playerId, "kinds=measured")),
    staleTime: 300_000,
    retry: false,
  });
  const experimental = useQuery({
    queryKey: ["player-intel-experimental", playerId],
    queryFn: () =>
      api.get<IntelResponse>(ENDPOINT(playerId, "kinds=inferred,projected&include_experimental=true")),
    enabled: open && !isDisabled(probe.error),
    staleTime: 300_000,
    retry: false,
  });
  if (isDisabled(probe.error)) return null;

  const data = experimental.data;
  const inferred = (data?.records ?? []).filter((r) => r.output_kind === "inferred");
  const projected = (data?.records ?? []).filter((r) => r.output_kind === "projected");
  const unavailable = (data?.unavailable ?? []).filter((u) => u.kind !== "measured");

  return (
    <Panel
      title="Research preview — experimental estimates"
      subtitle="Modelled values from candidate models. They are not approved intelligence and never feed Pivot's trade verdicts."
      actions={
        <button
          type="button"
          className="rounded-md border border-line px-3 py-1.5 text-[12.5px] font-medium text-foreground hover:border-signal hover:text-signal"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Hide estimates" : "Show experimental estimates"}
        </button>
      }
    >
      {!open ? (
        <p className="text-[13px] leading-relaxed text-muted">
          Closed by default. Opening it reads the stored candidate estimates for this player (nothing is trained or
          computed on request) and shows each with its model version, how it was evaluated and what it is conditional on.
        </p>
      ) : experimental.error ? (
        <UnavailableNotice reason={`The research preview could not be read: ${String(experimental.error)}`} />
      ) : !data ? (
        <SkeletonRows rows={4} height="h-10" />
      ) : (
        <div className="space-y-6">
          {data.notices.map((n) => (
            <p key={n} className="rounded-md border border-conditional/40 bg-conditional/5 px-3 py-2 text-[12.5px] leading-relaxed text-conditional">
              {n}
            </p>
          ))}
          <div>
            <h3 className="eyebrow">Inferred shooting ability</h3>
            {inferred.length === 0 ? (
              <p className="mt-2 text-[12.5px] text-muted">
                No ability estimate is served for this player — see why below. Pivot does not substitute a value.
              </p>
            ) : (
              <ul className="mt-1">
                {inferred.map((r) => (
                  <ExperimentalRow key={`i-${r.quantity}`} record={r} statement={data.reliability_statements[`${r.quantity}:inferred`]} />
                ))}
              </ul>
            )}
          </div>
          <div>
            <h3 className="eyebrow">Next-season projections</h3>
            <p className="mt-1 text-[12px] leading-relaxed text-faint">
              Each projection is <em>conditional on the player playing a rotation role</em> in the target season. It is
              not a forecast of his total contribution, and it says nothing about whether he will play.
            </p>
            {projected.length === 0 ? (
              <p className="mt-2 text-[12.5px] text-muted">No projection is served for this player.</p>
            ) : (
              <ul className="mt-1">
                {projected.map((r) => (
                  <ExperimentalRow key={`p-${r.quantity}`} record={r} statement={data.reliability_statements[`${r.quantity}:projected`]} />
                ))}
              </ul>
            )}
          </div>
          {unavailable.length > 0 && (
            <Explainer summary={`Why ${unavailable.length} other estimates are not offered`}>
              <ul className="space-y-1.5">
                {unavailable.map((u) => (
                  <li key={`${u.quantity}-${u.kind}`}>
                    <span className="font-medium text-foreground">
                      {QUANTITY_LABEL[u.quantity] ?? u.quantity} ({u.kind}).
                    </span>{" "}
                    {u.reason}
                  </li>
                ))}
              </ul>
            </Explainer>
          )}
        </div>
      )}
    </Panel>
  );
}

/* --------------------------------------------------------------------- shot profile */

interface ShotZones {
  available: boolean;
  reason?: string;
  season?: string;
  season_type?: string;
  games_played?: number | null;
  retrieved_at?: string | null;
  unit?: string;
  zones?: {
    key: string;
    label: string;
    attempts_per_game: number;
    share_of_attempts: number | null;
    fg_pct: number | null;
    approx_season_attempts: number;
    thin_sample: boolean;
    league_fg_pct: number | null;
    vs_league: number | null;
  }[];
  notes?: string[];
  limitations?: string[];
}

export function ShotProfilePanel({ playerId }: { playerId: string }) {
  const { data, error } = useQuery({
    queryKey: ["player-shot-zones", playerId],
    queryFn: () => api.get<ShotZones>(`/players/${playerId}/shot-zones`),
    staleTime: 600_000,
  });
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
  return (
    <Panel
      title="Shot profile"
      subtitle={
        data?.available
          ? `${data.season} ${data.season_type} season · where the attempts came from, per game · BALLDONTLIE`
          : "Where a player's attempts came from — descriptive, from BALLDONTLIE shot-zone averages"
      }
    >
      {error ? (
        <UnavailableNotice reason={`The shot profile could not be read: ${String(error)}`} />
      ) : !data ? (
        <SkeletonRows rows={5} height="h-8" />
      ) : !data.available || !data.zones ? (
        <UnavailableNotice reason={data.reason ?? "No shot-zone data for this player."} />
      ) : (
        <div className="space-y-4">
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[440px]">
              <caption className="sr-only">Shot attempts by zone, with accuracy against the league</caption>
              <thead>
                <tr className="border-b border-line">
                  <Th>Zone</Th>
                  <Th>Share of attempts · per game</Th>
                  <Th numeric>FG%</Th>
                  <Th numeric>League</Th>
                  <Th numeric>vs league</Th>
                </tr>
              </thead>
              <tbody>
                {data.zones.map((z) => (
                  <tr key={z.key} className="border-b border-hairline last:border-0">
                    <Td className="whitespace-nowrap text-[13px] text-muted">{z.label}</Td>
                    <Td>
                      <span className="flex items-center gap-2">
                        <span aria-hidden className="h-1.5 rounded-full bg-signal/70" style={{ width: `${Math.max(2, (z.share_of_attempts ?? 0) * 100 * 1.6)}px` }} />
                        <span className="numeral text-[12.5px] text-foreground">{pct(z.share_of_attempts)}</span>
                        <span className="numeral text-[11.5px] text-faint">{z.attempts_per_game.toFixed(1)}/g</span>
                      </span>
                    </Td>
                    <Td numeric>
                      <span className={clsx(z.thin_sample && "text-faint")} title={z.thin_sample ? `about ${z.approx_season_attempts} attempts — too few to read the percentage` : undefined}>
                        {pct(z.fg_pct)}
                        {z.thin_sample && <span className="ml-1 text-[10px] text-conditional">thin</span>}
                      </span>
                    </Td>
                    <Td numeric>{pct(z.league_fg_pct)}</Td>
                    <Td numeric>
                      {z.vs_league === null ? "—" : `${z.vs_league > 0 ? "+" : z.vs_league < 0 ? "−" : ""}${Math.abs(z.vs_league * 100).toFixed(1)}`}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Explainer summary="What this is and is not">
            <ul className="list-disc space-y-1 pl-5">
              {[...(data.notes ?? []), ...(data.limitations ?? [])].map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </Explainer>
          <SourceRail
            source={`BALLDONTLIE shooting/by_zone · ${data.games_played ?? "?"} games`}
            retrievedAt={data.retrieved_at ?? null}
          />
          {data.retrieved_at && <span className="sr-only">Retrieved {formatDate(data.retrieved_at)}</span>}
        </div>
      )}
    </Panel>
  );
}
