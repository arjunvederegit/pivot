"use client";

/**
 * Roster composition, two lenses on the same players:
 *
 * - **listed position** (the G / F / C designation on the official roster), placed at the
 *   spots those positions stand on a half court. It is a listing, not tracking data —
 *   nothing here knows where anyone actually plays — and the figure says so;
 * - **functional role** (the archetype family Pivot assigns from box-score data), as one
 *   stacked bar over the whole roster, with players who carry no archetype shown as their
 *   own segment rather than dropped.
 *
 * The two often disagree (a roster can list seven guards and hold two guard archetypes),
 * which is the point of showing them together.
 */

import { count } from "@/lib/format";
import type { RosterPlayer, TeamProfile } from "@/lib/types";
import { ErrorState, UnavailableNotice } from "@/components/ui";

export const POSITION_GROUPS = ["Guards", "Wings", "Bigs"] as const;
export type PositionGroup = (typeof POSITION_GROUPS)[number];

/**
 * Official position strings (G, G-F, F, F-C, C, …) grouped for scanning; the exact
 * designation stays visible on each roster row. A missing position is not a wing — it is
 * returned as null and counted on its own.
 */
export function positionGroup(position: string | null): PositionGroup | null {
  const p = (position ?? "").trim().toUpperCase();
  if (!p) return null;
  if (p.includes("C")) return "Bigs";
  if (p.includes("G") && !p.includes("F")) return "Guards";
  return "Wings";
}

/**
 * A regulation half court, 50 × 47 ft, drawn at 6 units per foot with the baseline at the
 * bottom: hoop 5.25 ft in, 16 ft lane to a 19 ft free-throw line, 22 ft corners and a
 * 23.75 ft arc. Fine chalk lines — the diagram is a floor, not a chart.
 */
const W = 300;
const H = 282;
const HOOP_Y = H - 31.5;

/** Where each listed position stands: top of the key, the wing, the block. */
const SPOT: Record<PositionGroup, { x: number; y: number }> = {
  Guards: { x: 150, y: 76 },
  Wings: { x: 40, y: 150 },
  Bigs: { x: 234, y: 226 },
};

function CourtLines() {
  const arcY = HOOP_Y - Math.sqrt(142.5 ** 2 - 132 ** 2);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="absolute inset-0 h-full w-full text-foreground"
      aria-hidden
      fill="none"
    >
      <g stroke="currentColor" strokeWidth="1" vectorEffect="non-scaling-stroke" strokeLinecap="round">
        {/* boundary: half-court line on top, sidelines, baseline */}
        <rect x="0.5" y="0.5" width={W - 1} height={H - 1} opacity="0.22" />
        {/* centre circle, the half of it on this side */}
        <path d={`M114 0.5a36 36 0 0 0 72 0`} opacity="0.28" />
        <path d={`M138 0.5a12 12 0 0 0 24 0`} opacity="0.2" />
        {/* the lane and the free-throw circle (dashed on the far side) */}
        <rect x="102" y={H - 114} width="96" height="114" opacity="0.42" />
        <path d={`M114 ${H - 114}a36 36 0 0 1 72 0`} opacity="0.42" />
        <path d={`M114 ${H - 114}a36 36 0 0 0 72 0`} opacity="0.3" strokeDasharray="3 4" />
        {/* lane blocks */}
        <path
          d={`M102 ${H - 42}h-5M198 ${H - 42}h5M102 ${H - 66}h-5M198 ${H - 66}h5M102 ${H - 84}h-5M198 ${H - 84}h5`}
          opacity="0.3"
        />
        {/* backboard, rim, restricted area */}
        <path d={`M132 ${H - 24}h36`} opacity="0.6" />
        <circle cx="150" cy={HOOP_Y} r="4.5" opacity="0.55" />
        <path d={`M126 ${HOOP_Y}a24 24 0 0 1 48 0`} opacity="0.3" />
        {/* three-point line: corners, then the arc */}
        <path d={`M18 ${H}V${arcY}M282 ${H}V${arcY}`} opacity="0.42" />
        <path d={`M18 ${arcY}A142.5 142.5 0 0 1 282 ${arcY}`} opacity="0.42" />
      </g>
    </svg>
  );
}

const FAMILY_ORDER = ["guard", "wing", "big", "unclassified"] as const;
const FAMILY_LABEL: Record<string, string> = {
  guard: "Guard",
  wing: "Wing",
  big: "Big",
  unclassified: "Unclassified",
};
/** The same family tones the home dashboard's roster read uses, so the two agree. */
const FAMILY_TONE: Record<string, string> = {
  guard: "var(--signal)",
  wing: "#a9c7d6",
  big: "#d6c39b",
  unclassified: "color-mix(in srgb, var(--series-b) 45%, var(--arena))",
};

export function Composition({
  roster,
  profile,
  profileError,
}: {
  roster: RosterPlayer[];
  profile: TeamProfile | undefined;
  /** The archetype mix comes from the roster profile; when that fails, say so here. */
  profileError?: unknown;
}) {
  const byGroup: Record<PositionGroup, number> = { Guards: 0, Wings: 0, Bigs: 0 };
  let unlisted = 0;
  for (const player of roster) {
    const group = positionGroup(player.position);
    if (group) byGroup[group] += 1;
    else unlisted += 1;
  }

  const rows = profile?.archetype_distribution ?? [];
  const families = new Map<string, number>();
  for (const row of rows) {
    const key = row.family && FAMILY_LABEL[row.family] ? row.family : "unclassified";
    families.set(key, (families.get(key) ?? 0) + row.count);
  }
  const labelled = rows.reduce((sum, row) => sum + row.count, 0);
  const total = profile?.roster_size ?? roster.length;
  const unlabelled = Math.max(total - labelled, 0);
  const segments = FAMILY_ORDER.filter((f) => (families.get(f) ?? 0) > 0).map((f) => ({
    key: f,
    label: FAMILY_LABEL[f],
    n: families.get(f) ?? 0,
  }));

  // A container: once the card is wide enough the court and the role breakdown sit side
  // by side; on a phone they stack, court first. The role column is a container of its
  // own, so its archetype list takes as many columns as that column can hold.
  return (
    <div className="@container">
      <div className="grid grid-cols-1 gap-6 @min-[38rem]:grid-cols-[minmax(0,18.5rem)_minmax(0,1fr)] @min-[38rem]:gap-8">
        {/* --------------------------------------------- listed position, on a court */}
        <figure className="min-w-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
            <span className="eyebrow">By listed position</span>
            <span className="text-[11.5px] text-faint">{count(roster.length, "player")}</span>
          </div>
          <div className="mx-auto mt-3 w-full max-w-[272px]">
            <div className="relative aspect-[300/282]">
              <CourtLines />
              {POSITION_GROUPS.map((group) => (
                <div
                  key={group}
                  className="absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center"
                  style={{ left: `${(SPOT[group].x / W) * 100}%`, top: `${(SPOT[group].y / H) * 100}%` }}
                >
                  <span
                    className={`stat-serif flex h-10 w-10 items-center justify-center rounded-full border text-[1.4rem] leading-none ${
                      byGroup[group] > 0
                        ? "border-signal/70 bg-panel text-foreground"
                        : "border-dashed border-unavail bg-panel text-unavail"
                    }`}
                  >
                    {byGroup[group]}
                  </span>
                  <span className="eyebrow mt-1.5 whitespace-nowrap rounded-full bg-panel px-1.5">
                    {group}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <figcaption className="mt-2.5 text-center text-[11.5px] text-faint">
            Listed position, not tracking data
            {unlisted > 0 ? ` · ${unlisted} with no listed position` : ""}
          </figcaption>
        </figure>

        {/* ------------------------------------------ functional role, one stacked bar */}
        <div className="@container min-w-0 border-t border-hairline pt-5 @min-[38rem]:border-l @min-[38rem]:border-t-0 @min-[38rem]:pl-8 @min-[38rem]:pt-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
            <span className="eyebrow">By functional role</span>
            <span className="text-[11.5px] text-faint">archetype family</span>
          </div>
          {profileError ? (
            <div className="mt-3">
              <ErrorState message={`Could not load the archetype distribution: ${String(profileError)}`} />
            </div>
          ) : !profile ? (
            <div className="skeleton mt-3 h-10" aria-hidden />
          ) : rows.length === 0 ? (
            <div className="mt-3">
              <UnavailableNotice reason="No player on this roster carries an archetype label for this season, so its functional composition is unknown." />
            </div>
          ) : (
            <>
              <div
                className="mt-3 flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full"
                role="img"
                aria-label={`${segments.map((s) => `${s.n} ${s.label.toLowerCase()}`).join(", ")}${
                  unlabelled ? `, ${unlabelled} with no archetype` : ""
                } — out of ${total} players`}
              >
                {segments.map((s) => (
                  <span
                    key={s.key}
                    className="h-full min-w-0"
                    style={{ width: `${(s.n / total) * 100}%`, background: FAMILY_TONE[s.key] }}
                  />
                ))}
                {unlabelled > 0 && (
                  <span
                    className="h-full min-w-0 rounded-r-full border border-dashed border-unavail/70"
                    style={{ width: `${(unlabelled / total) * 100}%` }}
                  />
                )}
              </div>
              <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[12px] text-muted">
                {segments.map((s) => (
                  <li key={s.key} className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: FAMILY_TONE[s.key] }} />
                    {s.label}s
                    <span className="numeral text-foreground">{s.n}</span>
                  </li>
                ))}
                {unlabelled > 0 && (
                  <li className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="h-2 w-2 rounded-full border border-dashed border-unavail" />
                    No archetype
                    <span className="numeral text-unavail">{unlabelled}</span>
                  </li>
                )}
              </ul>

              <ul className="mt-4 grid grid-cols-1 gap-x-8 gap-y-1.5 border-t border-hairline pt-3.5 @min-[22rem]:grid-cols-2 @min-[42rem]:grid-cols-3">
                {rows.map((row) => (
                  <li key={row.key} className="flex min-w-0 items-baseline justify-between gap-2 text-[12.5px]">
                    <span className="flex min-w-0 items-baseline gap-2">
                      <span
                        aria-hidden
                        className="h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full"
                        style={{ background: FAMILY_TONE[row.family && FAMILY_LABEL[row.family] ? row.family : "unclassified"] }}
                      />
                      <span className="min-w-0 truncate text-foreground" title={row.label}>
                        {row.label}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11.5px] text-faint">
                      {row.family ?? "—"} <span className="numeral text-[12.5px] text-foreground">{row.count}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        {/* The coverage note spans both columns when they sit side by side. */}
        {!profileError && profile && rows.length > 0 && (
          <p className="text-[11.5px] leading-relaxed text-faint @min-[38rem]:col-span-2 @min-[38rem]:border-t @min-[38rem]:border-hairline @min-[38rem]:pt-3">
            {count(labelled, "player")} of {total} labelled
            {unlabelled > 0
              ? `; ${count(unlabelled, "player")} ${unlabelled === 1 ? "carries" : "carry"} no archetype for this season and ${unlabelled === 1 ? "is" : "are"} shown as ${unlabelled === 1 ? "a segment of its own" : "their own segment"}.`
              : "."}
          </p>
        )}
      </div>
    </div>
  );
}
