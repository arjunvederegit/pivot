"use client";

/**
 * The season line — the franchise's scoreboard, read before anything else on Team Outlook.
 *
 * Six served readings (record, the three ratings with their NBA.com league ranks, and the
 * two roster ages that set the competitive window), then the one-line read of the
 * diagnosis: the server's own headline needs and strengths, each at the same
 * direction-corrected league position the strengths axis below draws. Nothing here is
 * computed beyond formatting and the two age averages the page already labels as such.
 */

import { count, ordinal, pct } from "@/lib/format";
import type { SeasonContext, Team, TeamProfile, Vocabulary } from "@/lib/types";
import { Skeleton, SourceRail } from "@/components/ui";
import { diagnosisSummary, type SummaryRow } from "@/components/teams/strengths-needs";

export interface TeamDetail {
  team: Team;
  season: string;
  /** R8.2: the roster season sits beside `season` (the statistics season). */
  season_context?: SeasonContext;
  standing: {
    wins: number;
    losses: number;
    win_pct: number;
    conference: string | null;
    playoff_rank: number | null;
    details: Record<string, unknown>;
    source_retrieved_at: string | null;
  } | null;
  stats: Record<string, Record<string, number>>;
  stats_retrieved_at: string | null;
}

/** A string or number from the standings `details` bag, or null — never coerced. */
function detailText(details: Record<string, unknown> | undefined, key: string): string | null {
  const v = details?.[key];
  if (typeof v === "string" && v.trim()) return v;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return null;
}

/** Advanced-stat league rank, as NBA.com serves it (1 = best; on defense a lower rating ranks higher). */
function RankText({ rank }: { rank: number | undefined }) {
  if (rank === undefined || rank === null || !Number.isFinite(rank)) {
    return <span className="text-[12px] text-faint">rank —</span>;
  }
  return (
    <span className="whitespace-nowrap text-[12px] text-muted">
      <span className="numeral text-foreground">{ordinal(rank)}</span> of 30
    </span>
  );
}

function Reading({
  label,
  value,
  rank,
  note,
}: {
  label: string;
  value: React.ReactNode;
  rank?: React.ReactNode;
  note: React.ReactNode;
}) {
  return (
    <div className="min-w-0 bg-panel px-4 py-4 sm:px-5">
      <dt className="eyebrow truncate" title={label}>
        {label}
      </dt>
      <dd className="mt-2.5">
        <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span className="stat-serif text-[2.35rem] leading-none text-foreground">{value}</span>
          {rank}
        </span>
        <span className="mt-2 block text-[12px] leading-snug text-faint">{note}</span>
      </dd>
    </div>
  );
}

function ReadList({
  rows,
  glyph,
  tone,
  empty,
}: {
  rows: SummaryRow[];
  glyph: string;
  tone: string;
  empty: string;
}) {
  if (rows.length === 0) return <p className="mt-2 text-[13px] text-muted">{empty}</p>;
  return (
    <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5">
      {rows.map((row) => (
        <li key={row.key} className="flex items-baseline gap-1.5 text-[14px] text-foreground">
          <span aria-hidden className={`text-[10px] ${tone}`}>
            {glyph}
          </span>
          {row.label}
          {row.position !== null ? (
            <span className="text-[12px] text-muted">
              <span className="numeral">{ordinal(row.position)}</span>
              <span className="sr-only"> league position</span>
            </span>
          ) : (
            <span className="text-[12px] text-faint">roster composition</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function SeasonLine({
  detail,
  rosterCount,
  rosterAge,
  windowAge,
  windowLabelText,
  profile,
  profileError,
  vocabulary,
}: {
  detail: TeamDetail;
  rosterCount: number | null;
  rosterAge: number | null;
  windowAge: number | null;
  windowLabelText: string;
  profile: TeamProfile | undefined;
  profileError: unknown;
  vocabulary: Vocabulary | undefined;
}) {
  const advanced = detail.stats.advanced ?? {};
  const standing = detail.standing;
  const rating = (key: string, signed = false) => {
    const v = advanced[key];
    if (v === undefined || v === null || !Number.isFinite(v)) return "—";
    const shown = v.toFixed(1);
    if (!signed) return shown;
    const r = Number(shown);
    return r > 0 ? `+${shown}` : r < 0 ? `−${Math.abs(r).toFixed(1)}` : "0.0";
  };
  const details = standing?.details;
  const splits = [
    ["Home", detailText(details, "HOME")],
    ["Road", detailText(details, "ROAD")],
    ["Last 10", detailText(details, "L10")],
    ["Pts for / game", detailText(details, "PointsPG")],
    ["Pts against / game", detailText(details, "OppPointsPG")],
  ].filter((pair): pair is [string, string] => pair[1] !== null);

  const summary = profile?.needs_available ? diagnosisSummary(profile, vocabulary) : null;

  return (
    <section aria-labelledby="season-line-heading" className="panel overflow-hidden">
      <h2 id="season-line-heading" className="sr-only">
        Season line
      </h2>
      <dl className="grid grid-cols-2 gap-px bg-hairline sm:grid-cols-3 xl:grid-cols-6">
        {standing ? (
          <Reading
            label={`Record · ${detail.season}`}
            value={
              <>
                {standing.wins}–{standing.losses}
              </>
            }
            note={
              <>
                {pct(standing.win_pct, 1)} · #{standing.playoff_rank ?? "—"} in the{" "}
                {standing.conference ?? "conference"}
              </>
            }
          />
        ) : (
          <Reading
            label="Record"
            value={<span className="text-unavail">—</span>}
            note="no standings row ingested"
          />
        )}
        <Reading
          label="Net rating"
          value={rating("NET_RATING", true)}
          rank={<RankText rank={advanced.NET_RATING_RANK} />}
          note="scoring margin per 100 possessions"
        />
        <Reading
          label="Offense"
          value={rating("OFF_RATING")}
          rank={<RankText rank={advanced.OFF_RATING_RANK} />}
          note="points scored per 100 possessions"
        />
        <Reading
          label="Defense"
          value={rating("DEF_RATING")}
          rank={<RankText rank={advanced.DEF_RATING_RANK} />}
          note="points allowed per 100 · lower is better"
        />
        <Reading
          label="Window age"
          value={windowAge === null ? "—" : windowAge.toFixed(1)}
          note={`average age of the 8 highest-impact players — window ${windowLabelText.toLowerCase()}`}
        />
        <Reading
          label="Roster age"
          value={rosterAge === null ? "—" : rosterAge.toFixed(1)}
          note={
            rosterCount === null
              ? "loading the roster"
              : `average across ${count(rosterCount, "listed player")}`
          }
        />
      </dl>

      {/* the diagnosis in one line, from the server's own lists */}
      <div className="grid gap-px border-t border-hairline bg-hairline md:grid-cols-2">
        {profileError ? (
          <p className="bg-panel px-4 py-3.5 sm:px-5 text-[13px] text-muted md:col-span-2">
            The roster profile did not load, so the measured needs and strengths are not shown.
          </p>
        ) : !profile ? (
          <>
            <div className="bg-panel px-4 py-3.5 sm:px-5">
              <Skeleton className="h-10" />
            </div>
            <div className="bg-panel px-4 py-3.5 sm:px-5">
              <Skeleton className="h-10" />
            </div>
          </>
        ) : !summary ? (
          <p className="bg-panel px-4 py-3.5 sm:px-5 text-[13px] text-muted md:col-span-2">
            Needs and strengths are not measured for this roster — the reason is in the
            diagnosis below.
          </p>
        ) : (
          <>
            <div className="min-w-0 bg-panel px-4 py-3.5 sm:px-5">
              <h3 className="eyebrow">Measured needs</h3>
              <ReadList
                rows={summary.needs}
                glyph="✕"
                tone="text-illegal"
                empty="No pressing needs — nothing sits in the need zone."
              />
            </div>
            <div className="min-w-0 bg-panel px-4 py-3.5 sm:px-5">
              <h3 className="eyebrow">Strengths</h3>
              <ReadList
                rows={summary.strengths}
                glyph="✓"
                tone="text-legal"
                empty="None measured this season."
              />
            </div>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-hairline px-4 py-3 sm:px-5">
        {splits.length > 0 && (
          <dl className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[12px]">
            {splits.map(([label, value]) => (
              <div key={label} className="flex items-baseline gap-1.5">
                <dt className="text-faint">{label}</dt>
                <dd className="numeral font-medium text-foreground">{value}</dd>
              </div>
            ))}
          </dl>
        )}
        <SourceRail
          className="mt-0! border-t-0! pt-0! lg:ml-auto"
          source={detail.team.provenance?.upstream ?? "unknown source"}
          retrievedAt={detail.standing?.source_retrieved_at ?? detail.stats_retrieved_at}
          extra={<span>· ranks as served, 1 = best · positions as on the axis below</span>}
        />
      </div>
    </section>
  );
}
