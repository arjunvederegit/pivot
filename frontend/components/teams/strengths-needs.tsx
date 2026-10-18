"use client";

/**
 * Strengths & needs on one axis.
 *
 * The old panel drew strengths as percentile bars and needs as *severity* bars under the
 * same-looking meter, so a 97th-percentile strength and a severity-1.0 need were two bars
 * of the same length meaning opposite things. Here every team-statistic category sits on
 * one 0–100 league-position axis where right is always better, with the league median at
 * 50.
 *
 * What this module does NOT do is classify. Which rows are strengths and which are needs
 * is decided on the server (`app/domain/needs.py`) and served as `strengths` /
 * `weaknesses`; the tag on each row is read from those lists. The only arithmetic here is
 * the direction correction:
 *
 *   The served `percentile` is the share of other teams with a LOWER value of the raw
 *   statistic. For defensive rating and turnover rate a lower value is better, so a raw
 *   10th percentile is a good defence. `severity` is the shortfall below the median in the
 *   need direction, so the direction is recoverable from the two served numbers:
 *   severity > 0 with percentile > 50, or severity 0 with percentile < 50, means lower is
 *   better. Position = 100 − percentile for those rows, the percentile itself otherwise.
 *
 * Roster-composition needs (lineup size, secondary creation) carry a placeholder
 * percentile of 0 — they are not league percentiles — so they are listed separately with
 * their explanation and never placed on the axis.
 */

import Link from "next/link";
import { ordinal } from "@/lib/format";
import type { ProfileNeedRow, TeamProfile, Vocabulary } from "@/lib/types";
import { Explainer } from "@/components/ui";
import { PercentileAxis, PercentileTrack, type TrackTone } from "@/components/teams/percentile-track";

/** Used only when the vocabulary has not loaded; the vocabulary is the source of truth. */
const COMPOSITION_FALLBACK = new Set(["lineup_size", "secondary_creation"]);

type Kind = "need" | "strength" | "unlisted" | "none";

interface AxisRow {
  need: ProfileNeedRow;
  position: number;
  raw: number;
  lowerIsBetter: boolean;
  kind: Kind;
}

export function leaguePosition(need: ProfileNeedRow): { position: number; lowerIsBetter: boolean } | null {
  if (need.percentile === null || need.percentile === undefined) return null;
  const p = need.percentile;
  const lowerIsBetter = need.severity > 0 ? p > 50 : p < 50;
  return { position: lowerIsBetter ? 100 - p : p, lowerIsBetter };
}

function compositionKeysOf(vocabulary: Vocabulary | undefined): Set<string> {
  return vocabulary
    ? new Set(vocabulary.needs.filter((n) => n.source === "roster_composition").map((n) => n.key))
    : COMPOSITION_FALLBACK;
}

export interface SummaryRow {
  key: string;
  label: string;
  /** Direction-corrected league position, or null for a roster-composition row. */
  position: number | null;
}

/**
 * The server's own headline lists, each row with the same direction-corrected league
 * position the axis draws — for the one-line read at the top of the page. Nothing is
 * reclassified here; a roster-composition row carries no position because its served
 * percentile is a placeholder, not a league percentile.
 */
export function diagnosisSummary(
  profile: TeamProfile,
  vocabulary: Vocabulary | undefined,
): { needs: SummaryRow[]; strengths: SummaryRow[] } {
  const composition = compositionKeysOf(vocabulary);
  const row = (need: ProfileNeedRow): SummaryRow => ({
    key: need.key,
    label: need.label,
    position: composition.has(need.key) ? null : (leaguePosition(need)?.position ?? null),
  });
  return { needs: profile.weaknesses.map(row), strengths: profile.strengths.map(row) };
}

const KIND_TEXT: Record<Kind, { glyph: string; word: string; className: string } | null> = {
  need: { glyph: "✕", word: "need", className: "text-illegal" },
  strength: { glyph: "✓", word: "strength", className: "text-legal" },
  unlisted: { glyph: "~", word: "not headlined", className: "text-muted" },
  none: null,
};

const KIND_TONE: Record<Kind, TrackTone> = {
  need: "need",
  strength: "strength",
  unlisted: "need",
  none: "neutral",
};

function KindTag({ kind }: { kind: Kind }) {
  const tag = KIND_TEXT[kind];
  if (!tag) return <span className="text-[11px] text-faint">—</span>;
  return (
    <span className={`whitespace-nowrap text-[11.5px] font-medium ${tag.className}`}>
      <span aria-hidden className="text-[10px]">
        {tag.glyph}{" "}
      </span>
      {tag.word}
    </span>
  );
}

/** label · track · position · tag — one line from `sm`, two below it. */
const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 sm:grid-cols-[minmax(0,10.5rem)_minmax(7rem,1fr)_2.75rem_6.25rem]";

export function StrengthsNeeds({
  profile,
  vocabulary,
}: {
  profile: TeamProfile;
  vocabulary: Vocabulary | undefined;
}) {
  const thresholds = vocabulary?.thresholds;
  const compositionKeys = compositionKeysOf(vocabulary);

  const weak = new Set(profile.weaknesses.map((n) => n.key));
  const strong = new Set(profile.strengths.map((n) => n.key));
  const kindOf = (need: ProfileNeedRow): Kind =>
    weak.has(need.key)
      ? "need"
      : strong.has(need.key)
        ? "strength"
        : thresholds && need.severity >= thresholds.need_severity
          ? "unlisted"
          : "none";

  const rows: AxisRow[] = [];
  const composition: ProfileNeedRow[] = [];
  // A team-statistic row the server sent without a percentile cannot be placed, and is
  // listed as such rather than silently dropped or drawn at 0.
  const unplaced: ProfileNeedRow[] = [];
  for (const need of profile.needs) {
    if (compositionKeys.has(need.key)) {
      composition.push(need);
      continue;
    }
    const placed = leaguePosition(need);
    if (!placed) {
      unplaced.push(need);
      continue;
    }
    rows.push({
      need,
      position: placed.position,
      raw: need.percentile ?? 0,
      lowerIsBetter: placed.lowerIsBetter,
      kind: kindOf(need),
    });
  }
  rows.sort((a, b) => a.position - b.position);

  // The edge of the need zone on this axis: severity is (50 − position) / 50, so the
  // server's headline threshold sits at 50 × (1 − threshold).
  const needEdge = thresholds ? 50 * (1 - thresholds.need_severity) : null;
  const strengthEdge = thresholds ? thresholds.strength_percentile : null;

  const unlistedStrongRows = rows.filter(
    (r) => r.lowerIsBetter && r.kind === "none" && strengthEdge !== null && r.position >= strengthEdge,
  );
  const unlistedNeedRows = rows.filter((r) => r.kind === "unlisted");

  return (
    // A flex column so that, when the panel is stretched to the height of the column beside
    // it, the rule and its explainer sit on the panel's bottom edge.
    <div className="flex flex-1 flex-col gap-4">
      {profile.weaknesses.length === 0 && (
        <p className="rounded-lg border border-hairline bg-panel2 px-3.5 py-2.5 text-[13px] leading-relaxed text-muted">
          No pressing needs. Every measured category sits at or above the league median for
          this team, so nothing here reads as a shortfall.
        </p>
      )}

      {/* legend */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2 w-2 rounded-full bg-foreground/70" />
          team position · right is better
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2.5 w-px bg-faint" />
          league median
        </span>
        {needEdge !== null && (
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-[3px] w-3.5 rounded-full bg-illegal/55" />
            need zone · severity ≥ {thresholds?.need_severity}
          </span>
        )}
        {strengthEdge !== null && (
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-[3px] w-3.5 rounded-full bg-legal/50" />
            strength zone · ≥ {ordinal(strengthEdge)}
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <p className="text-[13px] text-muted">No team-statistic categories were returned.</p>
      ) : (
        <div>
          <ul className="divide-y divide-hairline border-y border-hairline">
            {rows.map((row) => (
              <li key={row.need.key} title={row.need.explanation} className={`${ROW_GRID} py-2.5`}>
                <div className="min-w-0">
                  <div className="truncate text-[13.5px] text-foreground">{row.need.label}</div>
                  {row.lowerIsBetter ? (
                    <div className="mt-0.5 truncate text-[11px] text-faint">
                      raw {ordinal(row.raw)} · lower is better
                    </div>
                  ) : row.need.addressed_by === null && row.need.unaddressable_reason ? (
                    <div
                      className="mt-0.5 truncate text-[11px] text-faint"
                      title={row.need.unaddressable_reason}
                    >
                      no skill addresses this
                    </div>
                  ) : null}
                </div>
                <PercentileTrack
                  className="order-last col-span-2 sm:order-none sm:col-span-1"
                  value={row.position}
                  tone={KIND_TONE[row.kind]}
                  needBelow={needEdge}
                  strengthFrom={strengthEdge}
                />
                {/* below `sm` the figure and the tag share the right-hand cell */}
                <div className="flex items-baseline justify-end gap-3 sm:contents">
                  <span className="numeral text-right text-[15px] text-foreground">
                    {ordinal(row.position)}
                  </span>
                  <span className="text-right sm:text-left">
                    <KindTag kind={row.kind} />
                  </span>
                </div>
                <span className="sr-only">
                  {row.need.label}: league position {ordinal(row.position)} where higher is
                  better{KIND_TEXT[row.kind] ? `, listed as a ${KIND_TEXT[row.kind]?.word}` : ""}.
                </span>
              </li>
            ))}
          </ul>
          <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,10.5rem)_minmax(7rem,1fr)_2.75rem_6.25rem] sm:gap-x-4">
            <span className="hidden sm:block" />
            <PercentileAxis className="mt-1.5" note="median" />
          </div>
        </div>
      )}

      {unplaced.length > 0 && (
        <p className="text-[12px] leading-relaxed text-muted">
          <span className="font-medium text-unavail">Not placed — </span>
          {unplaced.map((n) => n.label).join(", ")}: no league percentile was served, so{" "}
          {unplaced.length === 1 ? "it is" : "they are"} not drawn on the axis.
        </p>
      )}

      {composition.length > 0 && (
        <div className="rounded-lg border border-hairline bg-panel2/50 px-3.5 py-3">
          <div className="eyebrow">Roster composition · not a league percentile</div>
          <ul className="mt-2.5 space-y-2.5">
            {composition.map((need) => {
              const kind = kindOf(need);
              return (
                <li key={need.key} className="flex items-start justify-between gap-3">
                  <span className="min-w-0">
                    <span className="block text-[13.5px] text-foreground">{need.label}</span>
                    <span className="mt-0.5 block text-[12px] leading-snug text-muted">
                      {need.explanation}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <KindTag kind={kind} />
                    <span className="mt-1 block text-[11px] tabular-nums text-faint">
                      severity {need.severity.toFixed(2)}
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {(unlistedStrongRows.length > 0 || unlistedNeedRows.length > 0) && (
        <div className="space-y-1.5 text-[12px] leading-relaxed text-muted">
          {unlistedStrongRows.length > 0 && (
            <p>
              <span className="text-foreground">
                {unlistedStrongRows.map((r) => r.need.label).join(" and ")}
              </span>{" "}
              {unlistedStrongRows.length === 1 ? "sits" : "sit"} in the strength zone but{" "}
              {unlistedStrongRows.length === 1 ? "is" : "are"} not listed as a strength: a lower
              raw number is better there, and the strength rule reads the raw percentile (
              {unlistedStrongRows.map((r) => ordinal(r.raw)).join(", ")}).
            </p>
          )}
          {unlistedNeedRows.length > 0 && thresholds && (
            <p>
              <span className="text-foreground">Not headlined</span>: severity clears{" "}
              {thresholds.need_severity}, but the server headlines at most{" "}
              {thresholds.headline_rows} needs, most severe first.
            </p>
          )}
        </div>
      )}

      {/* The rule itself, in words, next to what it produced. */}
      <div className="mt-auto space-y-2.5 border-t border-hairline pt-3">
        <p className="text-[11.5px] leading-relaxed text-muted">{profile.classification_note}</p>

        <Explainer summary="What each row measures">
          <ul className="space-y-1.5">
            {[...rows.map((r) => r.need), ...unplaced, ...composition].map((need) => (
              <li key={need.key}>
                <span className="text-foreground">{need.label}.</span> {need.explanation}
                {need.addressed_by === null && need.unaddressable_reason
                  ? ` — ${need.unaddressable_reason}.`
                  : ""}
              </li>
            ))}
          </ul>
          <p className="mt-2">
            Each position is the team&apos;s league percentile in that statistic, turned so right
            is always better: where a lower raw number is better (defensive rating, turnover
            rate) the position is 100 minus the raw percentile. The tags are the server&apos;s
            classification, not this page&apos;s.{" "}
            <Link href="/methodology#needs" className="text-signal underline underline-offset-2">
              How this is calculated
            </Link>
          </p>
        </Explainer>
      </div>
    </div>
  );
}
