"use client";

/**
 * Player page instruments: impact, fit with the user's team, role and comparables, and
 * the measured / unmeasured skill profile.
 *
 * Impact and role used to be told twice — once from the player record and again inside
 * "Player intelligence". Each is now one block that takes the number from the player
 * record and the method, evidence, confidence and limitations from the intelligence
 * payload, so nothing was dropped in the merge.
 */

import Link from "next/link";
import { pct } from "@/lib/format";
import { useFavoriteTeam } from "@/lib/favoriteTeam";
import { useHydrated } from "@/lib/hydrated";
import { teamIdentity } from "@/lib/teamIdentity";
import type {
  ArchetypeAssignment,
  ComparablePlayer,
  Measurement,
  PlayerIntelligence,
  SkillEntry,
} from "@/lib/types";
import { FitReadout } from "@/components/fit";
import { ImpactInline, ImpactReadout } from "@/components/impact";
import { percentileText } from "@/components/players/stats";
import {
  Badge,
  ButtonLink,
  ErrorState,
  Explainer,
  Panel,
  Skeleton,
  SkeletonRows,
  SourceRail,
  StatBlock,
  UnavailableNotice,
} from "@/components/ui";

export interface PlayerImpact {
  tei?: number;
  tei_offense?: number | null;
  tei_defense?: number | null;
  tei_range_10_90?: [number, number] | null;
  availability?: number | null;
  minutes_estimate?: number | null;
  model?: string | null;
  note: string;
}

/* ------------------------------------------------------------------ impact */

export function ImpactPanel({
  impact,
  intelligence,
  intelligenceError,
}: {
  impact: PlayerImpact;
  intelligence: PlayerIntelligence | undefined;
  /** The intelligence request failed: say so instead of "loading…" for ever. */
  intelligenceError?: unknown;
}) {
  const scored = impact.tei !== undefined && impact.tei !== null;
  const band = impact.tei_range_10_90 ?? null;
  const measurement = intelligence?.impact;

  return (
    <Panel
      className="min-w-0"
      title="On-court impact"
      subtitle="Pivot Estimated Impact (TEI) — a unitless index, placed among every player the model scored"
      accent="var(--signal)"
      actions={
        <Link href="/methodology#tei" className="eyebrow whitespace-nowrap text-signal">
          How it&apos;s built <span aria-hidden>→</span>
        </Link>
      }
    >
      {scored ? (
        <ImpactReadout
          tei={impact.tei}
          band={band ? { low: band[0], high: band[1] } : null}
          offense={impact.tei_offense ?? null}
          defense={impact.tei_defense ?? null}
        />
      ) : (
        // Not ImpactReadout's unscored state: that one asserts the player is "below the
        // model's 200-minute window", which is false for a player dropped for being
        // inactive and unrostered (Trey Lyles: 69 games last season). The reason shown
        // here is the one the API served for this player.
        <div className="rounded-md border border-dashed border-unavail/55 bg-unavail/[0.06] px-3.5 py-3">
          <div className="eyebrow">On-court impact</div>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            <span className="font-semibold text-foreground">Unscored.</span>{" "}
            Pivot has no impact estimate for this player, so it does not rank him — never at
            zero and never at the median.
          </p>
          <p className="mt-1.5 text-[12px] leading-relaxed text-muted">
            <span className="text-faint">Served reason: </span>
            {impact.note}
          </p>
        </div>
      )}

      {scored ? (
        <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-4 border-t border-hairline pt-4 sm:grid-cols-3">
          <StatBlock
            size="sm"
            label="Availability"
            value={pct(impact.availability)}
            note="share of games, recency-weighted"
          />
          <StatBlock
            size="sm"
            label="Minutes est."
            value={impact.minutes_estimate?.toFixed(1) ?? "—"}
            note="per game, model estimate"
          />
          <div className="col-span-2 min-w-0 sm:col-span-1">
            <div className="eyebrow text-[0.625rem]">Evidence</div>
            {measurement?.available ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {measurement.evidence && <Badge status="derived">{measurement.evidence}</Badge>}
                <Badge status="info">{measurement.confidence}</Badge>
              </div>
            ) : (
              <p className="mt-1.5 text-[11px] text-faint">
                {intelligence ? "not served" : intelligenceError ? "did not load" : "loading…"}
              </p>
            )}
          </div>
        </div>
      ) : (
        measurement &&
        !measurement.available &&
        measurement.reason && (
          <p className="mt-3 text-[12px] leading-relaxed text-muted">
            <span className="font-semibold text-foreground">In a trade: </span>
            {measurement.reason}.
          </p>
        )
      )}

      {measurement?.available && (
        <Explainer summary="Method and limitations" className="mt-4">
          <p>{unitlessMethod(measurement.method)}</p>
          <p className="mt-1 text-faint">Source: {measurement.source}</p>
          {impact.model && (
            <p className="mt-1 text-[10.5px] text-faint">Model: {impact.model}</p>
          )}
          <Limitations limitations={measurement.limitations} />
        </Explainer>
      )}

      {scored && <p className="mt-3 text-[11px] leading-relaxed text-faint">{impact.note}</p>}
    </Panel>
  );
}

/* --------------------------------------------------------------------- fit */

/**
 * Fit is conditional on a team, so it is only ever shown against the user's chosen one.
 * With no team chosen the card asks for one — it never picks a team on the user's behalf
 * and never shows a placeholder score.
 */
export function FitCard({
  playerId,
  playerName,
  currentTeamId,
}: {
  playerId: string;
  playerName: string;
  currentTeamId: string | null;
}) {
  const hydrated = useHydrated();
  const favorite = useFavoriteTeam();
  const identity = teamIdentity(favorite?.abbreviation);
  const teamName = favorite ? identity.name : undefined;

  return (
    <Panel
      title="Fit with your team"
      subtitle="Conditional on one roster — scored only against the team you chose"
      accent={favorite ? identity.bright : undefined}
    >
      {!hydrated ? (
        <Skeleton className="h-20" />
      ) : !favorite ? (
        <div className="rounded-md border border-dashed border-line px-3.5 py-3">
          <p className="text-[13px] leading-relaxed text-muted">
            <span className="font-semibold text-foreground">No team chosen. </span>Fit only means
            something against one roster&apos;s needs, so Pivot does not guess one. Pick your team
            and this card shows how {playerName} would move it.
          </p>
          <ButtonLink href="/command-center" size="sm" variant="primary" className="mt-3">
            Choose your team
          </ButtonLink>
        </div>
      ) : favorite.id === currentTeamId ? (
        <div>
          <div className="eyebrow">Fit with {teamName}</div>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            <span className="font-semibold text-foreground">Already on this roster. </span>Fit
            scores an addition, so a player already on your team has nothing to score.
          </p>
          <Link
            href="/command-center"
            className="mt-3 inline-block text-[11px] text-signal underline underline-offset-2"
          >
            Change your team <span aria-hidden>→</span>
          </Link>
        </div>
      ) : (
        <div>
          <FitReadout
            playerId={playerId}
            teamId={favorite.id}
            teamAbbr={favorite.abbreviation}
            teamName={teamName}
          />
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-hairline pt-3">
            <ButtonLink href={`/trade-evaluator?team=${favorite.id}`} size="sm">
              Build a {favorite.abbreviation} trade
            </ButtonLink>
            <Link
              href="/command-center"
              className="text-[11px] text-muted underline underline-offset-2 hover:text-foreground"
            >
              Change your team
            </Link>
          </div>
        </div>
      )}
    </Panel>
  );
}

/* --------------------------------------------------------- role & comparables */

export function RolePanel({
  archetype,
  intelligence,
  comparables,
  scored,
}: {
  archetype: ArchetypeAssignment | null;
  intelligence: PlayerIntelligence | undefined;
  comparables: ComparablePlayer[];
  /** Whether this player has an impact estimate. Comparables are ranked by distance in
   *  impact, so an unscored player has none however many players share his role. */
  scored: boolean;
}) {
  const memberships = [...(intelligence?.archetypes ?? [])].sort(
    (a, b) => Number(b.primary) - Number(a.primary),
  );
  const hasRole = archetype !== null || memberships.length > 0;

  return (
    <Panel
      title="Role & comparables"
      subtitle="Who he plays like, and who sits nearest him in impact"
    >
      {!hasRole ? (
        <UnavailableNotice
          reason={`No role has been assigned to this player${intelligence ? ` for ${intelligence.season}` : ""}, so there are no comparables either.`}
        />
      ) : (
        <div className="space-y-4">
          {memberships.length > 0 ? (
            <ul className="space-y-3">
              {memberships.map((m) => (
                <li key={m.key} className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="display text-xl leading-tight text-balance text-foreground">
                      {m.label}
                    </span>
                    {m.primary && <Badge status="info">primary</Badge>}
                    <Badge status="derived">
                      {m.evidence} · {m.confidence}
                    </Badge>
                  </div>
                  {m.definition && (
                    <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{m.definition}</p>
                  )}
                  <p className="mt-1.5 text-[11px] leading-relaxed text-faint">
                    An inference drawn from the measured dimensions, not something observed.{" "}
                    {m.method}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <div className="display text-xl leading-tight text-balance text-foreground">
              {archetype?.label}
            </div>
          )}
          <Explainer summary="How roles are assigned">
            Assigned by a fixed rule chain over league percentiles — size first, then role within
            that size. A role label, not a quality rating, and the same profile always produces the
            same label.
          </Explainer>

          <div className="border-t border-hairline pt-3">
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="eyebrow">Comparable players</h3>
              <span className="text-[10px] text-faint">same role · nearest impact</span>
            </div>
            {comparables.length === 0 ? (
              <div className="mt-2">
                <UnavailableNotice
                  reason={
                    scored
                      ? "No other scored player shares this role."
                      : "Comparables are the players in this role nearest him in impact, and he has no impact estimate to measure from."
                  }
                />
              </div>
            ) : (
              <ul className="mt-1.5 divide-y divide-hairline">
                {comparables.map((c) => (
                  <li key={c.player_id}>
                    <Link
                      href={`/players/${c.player_id}`}
                      className="group flex items-center gap-3 py-2"
                    >
                      <span className="min-w-0 flex-1 truncate text-[14px] text-foreground group-hover:underline group-hover:underline-offset-2">
                        {c.name}
                      </span>
                      <ImpactInline tei={c.tei} />
                      <span className="w-11 shrink-0 text-right text-[11px] text-faint">
                        {indexText(c.tei)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {comparables.length > 0 && (
              <p className="mt-1 text-[10px] text-faint">
                league impact percentile · raw index
              </p>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ skills */

/** Declared order of the sides, so the reader meets them the same way every time. */
const SKILL_SIDES = [
  ["offense", "Offense"],
  ["defense", "Defense"],
  ["physical", "Physical"],
] as const;

/** A dimension that carries a number. Narrowed, never defaulted — there is no `?? 0` here. */
type MeasuredSkill = SkillEntry & { percentile: number };

/**
 * What Pivot measures about this player. Nothing basketball is decided in here: the
 * percentiles, counts and coverage sentence arrive decided; this groups and draws them.
 */
export function SkillsPanel({
  intelligence,
  error,
}: {
  intelligence: PlayerIntelligence | undefined;
  error: unknown;
}) {
  const measured =
    intelligence?.skills.filter((s): s is MeasuredSkill => s.available && s.percentile !== null) ??
    [];

  return (
    <Panel
      title="Measured skills"
      subtitle="League percentiles over the recency-weighted feature window"
      accent="var(--signal)"
      actions={
        <Link href="/methodology" className="eyebrow whitespace-nowrap text-signal">
          What Pivot measures <span aria-hidden>→</span>
        </Link>
      }
    >
      {error ? (
        <ErrorState message={`Could not load player intelligence: ${String(error)}`} />
      ) : !intelligence ? (
        <SkeletonRows rows={6} height="h-8" />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span
              className={`numeral text-2xl leading-none ${intelligence.skills_measured > 0 ? "text-signal" : "text-foreground"}`}
            >
              {intelligence.skills_measured}
              <span className="text-muted"> / {intelligence.skills_declared}</span>
            </span>
            <span className="eyebrow text-[0.625rem]">declared dimensions measured</span>
            <span className="data text-[11px] text-muted">{intelligence.season}</span>
          </div>
          <p className="text-[12px] leading-relaxed text-muted">{intelligence.coverage_note}</p>

          {measured.length === 0 ? (
            <UnavailableNotice reason="None of the declared dimensions could be measured for this player." />
          ) : (
            <>
              <div className="grid gap-x-10 gap-y-6 border-t border-hairline pt-5 md:grid-cols-2 xl:grid-cols-3">
                {SKILL_SIDES.map(([side, label]) => {
                  const rows = measured.filter((skill) => skill.side === side);
                  if (rows.length === 0) return null;
                  return (
                    <div key={side} className="min-w-0">
                      <h3 className="eyebrow text-[0.625rem] text-faint">{label}</h3>
                      <ul className="mt-2.5 space-y-3">
                        {rows.map((skill) => (
                          <SkillBar key={skill.key} skill={skill} />
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
              <p className="text-[10px] text-faint">
                bar = league percentile · tick = league median (50th)
              </p>
              {/* A number is never shown without its method being reachable. The row
                  title carries it on hover; this carries it to a keyboard as well. */}
              <Explainer summary="How each measured dimension is computed">
                <ul className="space-y-3">
                  {measured.map((skill) => (
                    <li key={skill.key}>
                      <span className="text-foreground">{skill.label}</span>
                      <span className="text-faint">
                        {" "}
                        · {skill.evidence} · {skill.confidence}
                      </span>
                      {skill.definition && <p>{skill.definition}</p>}
                      <p>{skill.method}</p>
                      <Limitations limitations={skill.limitations} />
                    </li>
                  ))}
                </ul>
              </Explainer>
              <SourceRail source={measured[0].source} />
            </>
          )}
        </div>
      )}
    </Panel>
  );
}

function SkillBar({ skill }: { skill: MeasuredSkill }) {
  const p = skill.percentile * 100;
  // Printed 1st–99th like every other percentile on the page; the bar keeps the raw value.
  const label = percentileText(p);
  return (
    <li className="min-w-0" title={methodTitle(skill)}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-[13px] text-foreground">{skill.label}</span>
        <span className="shrink-0 text-[11px] text-muted">
          <span className="text-foreground">{label}</span> pctl
          {skill.confidence === "heuristic" && <span className="text-faint"> · heuristic</span>}
        </span>
      </div>
      <div
        role="img"
        aria-label={`${skill.label} at the ${label} league percentile`}
        className="relative mt-1.5 h-2 w-full rounded-full bg-panel3"
      >
        <div className="h-full rounded-full bg-signal" style={{ width: `${Math.max(1.5, p)}%` }} />
        <span aria-hidden className="absolute -inset-y-[3px] left-1/2 w-px bg-foreground/45" />
      </div>
    </li>
  );
}

/* --------------------------------------------------------------- unmeasured */

/**
 * The declared dimensions Pivot cannot see. Not an appendix and not collapsed: a reader
 * who saw only the bars would conclude Pivot has seen everything. Dimensions that share a
 * reason are grouped under it so the reason is read once, but every dimension is still
 * named, with its side and definition.
 */
export function UnmeasuredPanel({
  intelligence,
}: {
  intelligence: PlayerIntelligence | undefined;
}) {
  const unmeasured = intelligence?.skills.filter((s) => !s.available) ?? [];
  if (!intelligence || unmeasured.length === 0) return null;

  const groups: { reason: string; skills: SkillEntry[] }[] = [];
  for (const skill of unmeasured) {
    const reason = skill.reason || "No reason was served.";
    const group = groups.find((g) => g.reason === reason);
    if (group) group.skills.push(skill);
    else groups.push({ reason, skills: [skill] });
  }

  return (
    <Panel
      title="Declared, not measured"
      subtitle="Pivot declares these dimensions and cannot see them in the data it ingests"
      actions={
        <span className="text-[11px] text-muted">
          <span className="text-foreground">{unmeasured.length}</span> dimension
          {unmeasured.length === 1 ? "" : "s"}
        </span>
      }
    >
      <p className="max-w-3xl text-[12px] leading-relaxed text-muted">
        They are listed rather than dropped, because a missing dimension is a limit of the model and
        not a statement that the dimension does not matter. The gap belongs to Pivot, not to the
        player.
      </p>
      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {groups.map((group) => (
          <section
            key={group.reason}
            className="min-w-0 rounded-md border border-dashed border-unavail/45 bg-unavail/[0.05] px-3.5 py-3"
          >
            <p className="flex items-start gap-2 text-[12px] leading-relaxed text-muted">
              <span aria-hidden className="text-unavail">
                —
              </span>
              <span>{group.reason}</span>
            </p>
            <ul className="mt-2.5 grid gap-x-4 gap-y-2 pl-4 sm:grid-cols-2">
              {group.skills.map((skill) => (
                <li key={skill.key} className="min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[13px] text-foreground">{skill.label}</span>
                    <span className="eyebrow text-[0.625rem] text-faint">{skill.side}</span>
                  </div>
                  {skill.definition && (
                    <p className="mt-0.5 text-[11px] leading-snug text-faint">{skill.definition}</p>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ shared */

/**
 * TEI is a unitless index (a weighted z-score composite × 2.5), not a per-100-possession
 * quantity. The intelligence endpoint's method sentence used to end "…, on a per-100
 * scale." (backend/app/services/intelligence.py, now corrected at the source). A server
 * still running the old copy would contradict the readout beside it, so that one clause
 * is dropped if it ever arrives; nothing else in the sentence is touched.
 */
function unitlessMethod(method: string): string {
  return method.replace(/,?\s*on a per-100 scale(?=\.|$)/i, "");
}

function Limitations({ limitations }: { limitations: string[] }) {
  if (!limitations || limitations.length === 0) return null;
  return (
    <ul className="mt-0.5 space-y-0.5 pl-4 text-[11px] leading-relaxed text-faint">
      {limitations.map((limitation) => (
        <li key={limitation} className="list-disc">
          {limitation}
        </li>
      ))}
    </ul>
  );
}

/**
 * The raw index at the precision the league percentile is ranked at (2 dp, as the impact
 * readouts print it). At 1 dp two neighbours both read "+1.9" beside different
 * percentiles, which looks like a contradiction rather than a rounding.
 */
function indexText(value: number | null): string {
  if (value === null) return "—";
  const r = Number(value.toFixed(2));
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(2)}`;
}

/** The hover-reachable half of the method rule; the disclosures carry the rest. */
function methodTitle(m: Measurement): string {
  const parts = [m.method];
  if (m.limitations?.length) parts.push(`Limitations: ${m.limitations.join(" ")}`);
  return parts.filter(Boolean).join(" — ");
}
