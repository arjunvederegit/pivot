"use client";

/**
 * Precedent — the completed trades a proposal resembles, and what a trade does to the
 * shape of a rotation.
 *
 * Three rules govern everything rendered here.
 *
 * **A similarity number is meaningless without its distribution.** 0.74 reads as a grade
 * until you know the middle of the corpus sits at 0.67. Every score is shown against the
 * per-dimension breakdown that produced it, and the panel says how large the searched
 * corpus is.
 *
 * **Resemblance is not consequence.** Nothing in the retrieval reads what happened after
 * these trades, and the panel says so in its own text rather than in a tooltip.
 *
 * **A precedent's `tei` is a single-season quantity.** It is the index for the season the
 * historical trade was made in, so it is shown as a raw index — never placed in today's
 * league percentile, which describes a different population.
 */

import clsx from "clsx";
import { tei } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type {
  ComparableLeg,
  ComparablePick,
  ComparableSide,
  ComparablesResponse,
  RoleShareRow,
  RosterShapeDetail,
} from "@/lib/types";
import { Skeleton, UnavailableNotice } from "@/components/ui";

const DIMENSION_LABEL: Record<string, string> = {
  player_value: "On-court value",
  draft_capital: "Draft capital",
  structure: "Deal structure",
  age_profile: "Age profile",
  team_context: "Team situation",
  timing: "Timing",
};

const INDEX_TITLE =
  "Single-season model index for the season this trade was made in — a raw index, not today's league percentile.";

function picks(list: ComparablePick[]): string {
  if (list.length === 0) return "—";
  return list
    .map((p) => `${p.draft_year} R${p.round_number}${p.conveyance === "unconditional" ? "" : `*`}`)
    .join(", ");
}

/** "2020-02-06" → "Feb 6, 2020", read as a calendar date (no timezone shift). */
function tradeDate(iso: string | null, season: string): string {
  if (!iso) return season;
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

function Legs({
  label,
  glyph,
  legs,
  pickList,
}: {
  label: string;
  glyph: string;
  legs: ComparableLeg[];
  pickList: ComparablePick[];
}) {
  return (
    <div className="min-w-0">
      <div className="text-[12.5px] font-medium text-foreground">
        <span aria-hidden className={glyph === "←" ? "text-signal" : "text-muted"}>
          {glyph}{" "}
        </span>
        {label}
      </div>
      <ul className="mt-2 space-y-1.5">
        {legs.length === 0 && <li className="text-[12.5px] text-faint">No players</li>}
        {legs.map((leg, i) => (
          <li
            key={`${leg.player_id ?? leg.name}-${i}`}
            className="flex items-baseline justify-between gap-2 text-[13px]"
          >
            <span className="min-w-0 truncate text-foreground">{leg.name}</span>
            {leg.tei === null ? (
              <span className="shrink-0 text-[11.5px] text-unavail">unscored</span>
            ) : (
              <span className="numeral shrink-0 text-[11.5px] font-medium text-muted" title={INDEX_TITLE}>
                idx {tei(leg.tei)}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-[11.5px] text-faint">Picks {picks(pickList)}</p>
    </div>
  );
}

function ComparableCard({ side, rank }: { side: ComparableSide; rank: number }) {
  const dimensions = Object.entries(side.dimension_similarity).sort(
    (a, b) => b[1].weight - a[1].weight,
  );
  const identity = teamIdentity(side.team_abbreviation);
  const similarity = Math.round(side.similarity * 100);
  return (
    <li className="rounded-xl border border-hairline bg-panel2/40">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-hairline px-4 py-3.5">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <span className="numeral text-[12px] text-faint">#{rank}</span>
            <h4 className="flex items-center gap-1.5 font-serif text-[1.45rem] leading-none text-foreground">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: identity.bright }} />
              {side.team_abbreviation}
            </h4>
            <span className="text-[12.5px] text-muted">
              {tradeDate(side.transaction_date, side.season)}
            </span>
          </div>
          {side.counterparties.length > 0 && (
            <p className="mt-1.5 text-[12px] text-faint">
              with {side.counterparties.join(" · ")} · {side.season}
            </p>
          )}
        </div>
        <div className="w-[7.5rem] shrink-0 text-right">
          <span className="stat-serif text-[1.9rem] leading-none text-foreground">{similarity}</span>
          <span className="text-[12px] text-muted">% similar</span>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-panel3" aria-hidden>
            <div className="h-full rounded-full bg-signal" style={{ width: `${similarity}%` }} />
          </div>
        </div>
      </div>

      <div className="space-y-3.5 px-4 py-3.5">
        <div className="grid gap-3 sm:grid-cols-2">
          <Legs label="Received" glyph="←" legs={side.incoming} pickList={side.picks_in} />
          <Legs label="Sent" glyph="→" legs={side.outgoing} pickList={side.picks_out} />
        </div>

        <ul className="grid gap-x-6 gap-y-2 border-t border-hairline pt-3 sm:grid-cols-2">
          {dimensions.map(([key, value]) => {
            const pct = Math.round(value.similarity * 100);
            return (
              <li key={key} className="grid grid-cols-[6.5rem_minmax(0,1fr)_2.25rem] items-center gap-2">
                <span className="truncate text-[12px] text-muted">{DIMENSION_LABEL[key] ?? key}</span>
                <span
                  className="block h-1.5 overflow-hidden rounded-full bg-panel3"
                  role="img"
                  aria-label={`${DIMENSION_LABEL[key] ?? key} similarity ${pct} percent`}
                >
                  <span className="block h-full rounded-full bg-signal/80" style={{ width: `${pct}%` }} />
                </span>
                <span className="numeral text-right text-[11.5px] font-medium text-muted">{pct}%</span>
              </li>
            );
          })}
        </ul>

        <div className="space-y-1 text-[11.5px] leading-snug">
          {side.why.length > 0 && <p className="text-faint">{side.why[0]}</p>}
          {side.dimensions_unavailable.length > 0 && (
            <p className="text-unavail">
              Not compared:{" "}
              {side.dimensions_unavailable.map((d) => DIMENSION_LABEL[d] ?? d).join(", ")} — one of
              the two sides does not state it.
            </p>
          )}
          {(side.reported_not_scored.cash_involved ||
            side.reported_not_scored.trade_exception_received) && (
            <p className="text-faint">
              Reported, never scored:{" "}
              {[
                side.reported_not_scored.cash_involved ? "cash changed hands" : null,
                side.reported_not_scored.trade_exception_received
                  ? "this team received a trade exception"
                  : null,
              ]
                .filter(Boolean)
                .join("; ")}
              .
            </p>
          )}
          <p className="text-faint">
            <span className="font-medium text-muted">Record · </span>
            {side.source_text}
          </p>
        </div>
      </div>
    </li>
  );
}

function AsideNote({
  title,
  children,
  tone = "default",
}: {
  title: string;
  children: React.ReactNode;
  tone?: "default" | "signal";
}) {
  return (
    <div
      className={clsx(
        "rounded-xl border p-4",
        tone === "signal" ? "border-signal/30 bg-signal/[0.05]" : "border-hairline bg-panel2/40",
      )}
    >
      <h5 className="title-sm flex items-center gap-2 text-foreground">
        {tone === "signal" && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />}
        {title}
      </h5>
      <div className="mt-1.5 text-[12.5px] leading-snug text-muted">{children}</div>
    </div>
  );
}

export function PrecedentPanel({
  data,
  loading,
  error,
}: {
  data?: ComparablesResponse;
  loading?: boolean;
  error?: string;
}) {
  if (loading) {
    return (
      <div className="space-y-3" role="status" aria-live="polite">
        <p className="text-[13px] text-muted">Searching completed trades…</p>
        <Skeleton className="h-36" />
        <Skeleton className="h-36" />
      </div>
    );
  }
  if (error) {
    return <UnavailableNotice reason={error} />;
  }
  if (!data) {
    return (
      <p className="text-[13px] text-muted">
        Evaluate a trade to search the completed-trade record for precedents.
      </p>
    );
  }
  if (!data.available) {
    return (
      <UnavailableNotice
        reason={
          <>
            {data.unavailable_reason}
            {data.unmodelled_players && data.unmodelled_players.length > 0 && (
              <> Players without modelled production: {data.unmodelled_players.join(", ")}.</>
            )}
          </>
        }
      />
    );
  }
  const coverage = data.coverage;
  const firstSeason = coverage.seasons_ingested[0];
  const lastSeason = coverage.seasons_ingested[coverage.seasons_ingested.length - 1];
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h4 className="font-serif text-[1.55rem] leading-tight text-foreground">
            Completed trades this one resembles
          </h4>
          <span className="text-[11.5px] text-faint">idx = single-season index</span>
        </div>
        <p className="mt-1.5 max-w-3xl text-[12.5px] leading-relaxed text-muted">
          {/* `trades_rankable`, not `trades_ingested`: 565 trades are ingested and 535 can
              be ranked, and naming the larger number here would claim coverage the
              retrieval does not have. The gap is stated below rather than absorbed. */}
          Ranked over {coverage.sides_rankable.toLocaleString()} team-sides of{" "}
          {coverage.trades_rankable.toLocaleString()}
          {" "}completed trades. A side is one
          team&apos;s view of one trade, because sending a star for picks and receiving one are
          different decisions — at most one side of any trade appears here.
        </p>
        <ol className="mt-3 space-y-3">
          {data.comparables.map((side, i) => (
            <ComparableCard key={side.key} side={side} rank={i + 1} />
          ))}
        </ol>
        {data.comparables.length === 0 && (
          <p className="mt-3 text-[13px] text-muted">
            No completed trade in the corpus is close enough to show.
          </p>
        )}
      </div>
      <aside className="min-w-0 space-y-3">
        <AsideNote title="Resemblance is not consequence" tone="signal">
          Nothing here reads what happened after these trades. A historical deal that worked is
          not an argument that this one will.
        </AsideNote>
        {data.not_scored && data.not_scored.length > 0 && (
          <AsideNote title="Not part of the similarity">
            <ul className="space-y-1.5">
              {data.not_scored.map((entry) => (
                <li key={entry.field}>
                  <span className="text-foreground">{entry.field}</span> — {entry.reason}.
                </li>
              ))}
            </ul>
          </AsideNote>
        )}
        <AsideNote title="Coverage">
          <dl className="my-2 grid grid-cols-2 gap-x-3 gap-y-2">
            <div>
              <dt className="eyebrow">Trades rankable</dt>
              <dd className="numeral mt-1 text-[15px] text-foreground">
                {coverage.trades_rankable.toLocaleString()}
                <span className="text-[11.5px] font-normal text-faint">
                  {" "}
                  / {coverage.trades_ingested.toLocaleString()}
                </span>
              </dd>
            </div>
            <div>
              <dt className="eyebrow">Seasons</dt>
              <dd className="numeral mt-1 text-[13px] text-foreground">
                {firstSeason} – {lastSeason}
              </dd>
            </div>
          </dl>
          <p>{coverage.note}</p>
          <p className="mt-1.5">
            {coverage.sides_blocked_by_unmodelled_players} sides withheld because a player in them
            has no modelled production
            {coverage.trades_ingested > coverage.trades_rankable && (
              <>
                ; {(coverage.trades_ingested - coverage.trades_rankable).toLocaleString()} of{" "}
                {coverage.trades_ingested.toLocaleString()} ingested trades cannot be ranked at all
              </>
            )}
            .
          </p>
          {/* The rule that decided every feature season above. Silent when it is the
              ingested calendar, because that is the case with nothing to disclose. */}
          {coverage.calendar_backed === false && (
            <p className="mt-1.5 text-conditional">
              No season calendar has been ingested, so each trade&apos;s feature season was decided
              from its calendar month. That mis-describes draft-night, preseason and
              November-2020 trades. Run <code>make sync-season-calendar</code>.
            </p>
          )}
        </AsideNote>
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------- rotation shape */

function roleNote(row: RoleShareRow): string {
  if (row.congested) return "above the league 90th percentile";
  if (row.lost) return "no longer a rotation role";
  return "";
}

function RoleGlyph({ row }: { row: RoleShareRow }) {
  if (row.congested) {
    return (
      <span aria-hidden className="text-conditional">
        !{" "}
      </span>
    );
  }
  if (row.lost) {
    return (
      <span aria-hidden className="text-illegal">
        ✕{" "}
      </span>
    );
  }
  return null;
}

export function RosterShapePanel({ shape }: { shape: RosterShapeDetail }) {
  if (shape.unavailable) {
    return <UnavailableNotice reason={shape.unavailable} />;
  }
  const rows = (shape.roles ?? [])
    .filter((r) => Math.abs(r.delta) >= 2 || r.congested || r.lost)
    .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  return (
    <section className="min-w-0 rounded-xl border border-hairline bg-panel2/40 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h4 className="title-sm text-foreground">Rotation consequences</h4>
        <span className="text-[11.5px] text-faint">minutes of 240</span>
      </div>
      <p className="mt-1 text-[12px] leading-snug text-muted">
        Minutes by player role, of 240. Roster composition — not lineup data.
      </p>
      {rows.length === 0 ? (
        <p className="mt-3 text-[13px] text-muted">
          No role&apos;s minutes move by more than two of 240. The rotation keeps its shape.
        </p>
      ) : (
        // Below `sm` the note moves under the role name instead of a fifth column: in a
        // scrolled-off column the glyph and the words were out of view, which left the
        // amber/red figure saying it by colour alone.
        <div className="scroll-thin mt-2.5 overflow-x-auto">
          <table className="w-full text-[12.5px] sm:min-w-[420px]">
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="eyebrow py-2 pr-2 text-left font-medium">
                  Role
                </th>
                <th scope="col" className="eyebrow px-2 py-2 text-right font-medium">
                  Before
                </th>
                <th scope="col" className="eyebrow px-2 py-2 text-right font-medium">
                  After
                </th>
                <th scope="col" className="eyebrow px-2 py-2 text-right font-medium">
                  League med.
                </th>
                <th
                  scope="col"
                  className="eyebrow hidden py-2 pl-2 text-left font-medium sm:table-cell"
                >
                  Note
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.role} className="border-b border-hairline last:border-b-0">
                  <td className="py-2 pr-2 text-foreground">
                    {row.role}
                    {roleNote(row) && (
                      <span className="mt-0.5 block text-[11px] leading-snug text-muted sm:hidden">
                        <RoleGlyph row={row} />
                        {roleNote(row)}
                      </span>
                    )}
                  </td>
                  <td className="numeral px-2 py-2 text-right font-normal text-muted">
                    {row.minutes_before.toFixed(0)}
                  </td>
                  <td
                    className={clsx(
                      "numeral px-2 py-2 text-right font-normal",
                      row.congested ? "text-conditional" : row.lost ? "text-illegal" : "text-foreground",
                    )}
                  >
                    {row.minutes_after.toFixed(0)}
                  </td>
                  <td className="numeral px-2 py-2 text-right font-normal text-faint">
                    {row.league_median === null ? "—" : row.league_median.toFixed(0)}
                  </td>
                  <td className="hidden py-2 pl-2 text-muted sm:table-cell">
                    <RoleGlyph row={row} />
                    {roleNote(row)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {shape.lineup_fit && !shape.lineup_fit.available && (
        <p className="mt-3 border-t border-hairline pt-2.5 text-[12px] leading-snug text-muted">
          <span className="font-semibold text-foreground">Lineup-aware fit is unavailable. </span>
          {shape.lineup_fit.reason}.
        </p>
      )}
    </section>
  );
}
