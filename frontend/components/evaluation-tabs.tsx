"use client";

/**
 * The panels behind the evaluation's tab strip — rendered inside the Trade Evaluator's
 * team-perspective panel and the saved-deal report.
 *
 * Each one takes a `TeamEvaluation` (and, where it needs it, one typed section of its
 * detail bag), renders, and holds no state the caller shares. `PrecedentTab` owns its
 * query and is meant to be mounted only while its tab is selected, which is what keeps
 * the comparable-trade search on demand.
 *
 * Every tab is built from the same editorial language as the evaluator: a quiet raised
 * card with a small caps label, the number that matters first, the caveat right under it,
 * and long-form explanation behind an Explainer. Nothing here estimates: an absent section
 * renders its own unavailable state.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo } from "react";
import { api } from "@/lib/api";
import { sectionOf } from "@/lib/evaluationDetail";
import {
  COMPONENT_EXPLAIN,
  COMPONENT_LABEL,
  NEED_LABEL,
  SKILL_LABEL,
  money,
  payrollDisclosure,
} from "@/lib/format";
import { comparablesResponseSchema } from "@/lib/schemas";
import type {
  AssetsDetail,
  ComparablesResponse,
  FitDetail,
  PerformanceDetail,
  PickMove,
  PlayerMove,
  RiskDetail,
  RosterShapeDetail,
  TeamEvaluation,
  TeamLegality,
  TimelineDetail,
} from "@/lib/types";
import {
  BeforeAfterBars,
  ComponentBars,
  TornadoChart,
  UncertaintyStrip,
} from "@/components/charts";
import { PrecedentPanel, RosterShapePanel } from "@/components/precedent";
import { direction, signed } from "@/components/reference/format";
import { ButtonLink, Explainer, StatBlock, UnavailableNotice } from "@/components/ui";

/* ------------------------------------------------------------------ pieces */

/** A raised card: quiet caps label, optional aside, content. */
function Block({
  label,
  aside,
  children,
  className,
}: {
  label?: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("min-w-0 rounded-xl border border-hairline bg-panel2/40 p-4", className)}>
      {(label || aside) && (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {label && <span className="title-sm text-foreground">{label}</span>}
          {aside && <span className="text-[11.5px] text-faint">{aside}</span>}
        </div>
      )}
      {children}
    </div>
  );
}

/** A row of StatBlocks separated by hairlines — the evaluator's ledger row. */
function StatStrip({ children, cols = 4 }: { children: React.ReactNode; cols?: 2 | 3 | 4 }) {
  return (
    <div
      className={clsx(
        "grid gap-px overflow-hidden rounded-xl border border-hairline bg-hairline",
        "grid-cols-2",
        // Three cells on a phone: two up, the third across the row — labels such as
        // "Roster spots" do not fit a third of 358px.
        cols === 3 && "sm:grid-cols-3 [&>*:nth-child(3)]:col-span-2 sm:[&>*:nth-child(3)]:col-span-1",
        cols === 4 && "sm:grid-cols-4",
      )}
    >
      {children}
    </div>
  );
}

function StatCell(props: React.ComponentProps<typeof StatBlock>) {
  return (
    <div className="min-w-0 bg-panel px-4 py-3">
      <StatBlock size="sm" {...props} />
    </div>
  );
}

/** ▲ / ▼ / ■ — direction as a glyph and a word for screen readers, never colour alone. */
function DirectionGlyph({ value, digits = 2 }: { value: number; digits?: number }) {
  const d = direction(value, digits);
  return (
    <>
      <span aria-hidden className={d > 0 ? "text-legal" : d < 0 ? "text-illegal" : "text-faint"}>
        {d > 0 ? "▲" : d < 0 ? "▼" : "■"}
      </span>
      <span className="sr-only">{d > 0 ? "improves" : d < 0 ? "worsens" : "unchanged"}</span>
    </>
  );
}

/** A bar centred on zero, filled toward the value; `scale` is the |value| at full width. */
function DivergingBar({ value, scale, className }: { value: number; scale: number; className?: string }) {
  const width = Math.min(50, (Math.abs(value) / (scale || 1)) * 50);
  const d = direction(value, 2);
  return (
    <span aria-hidden className={clsx("relative block h-1.5 rounded-full bg-panel3", className)}>
      <span className="absolute -inset-y-1 left-1/2 w-px bg-faint" />
      {d !== 0 && (
        <span
          className="absolute inset-y-0 rounded-full"
          style={{
            left: d > 0 ? "50%" : `${50 - width}%`,
            width: `${Math.max(1, width)}%`,
            background: d > 0 ? "var(--signal)" : "var(--illegal)",
            opacity: 0.85,
          }}
        />
      )}
    </span>
  );
}

/* --------------------------------------------------------------- precedent */

export function PrecedentTab({
  focalTeamId,
  teamIds,
  playerMoves,
  pickMoves,
}: {
  focalTeamId: string;
  teamIds: string[];
  playerMoves: PlayerMove[];
  pickMoves: PickMove[];
}) {
  const query = useQuery({
    queryKey: ["comparables", focalTeamId, playerMoves, pickMoves],
    queryFn: () =>
      api.post<ComparablesResponse>(
        "/trades/comparables",
        {
          team_ids: teamIds,
          focal_team_id: focalTeamId,
          player_moves: playerMoves,
          pick_moves: pickMoves,
          k: 5,
        },
        comparablesResponseSchema,
      ),
  });
  return (
    <PrecedentPanel
      data={query.data}
      loading={query.isPending}
      error={query.error ? String(query.error) : undefined}
    />
  );
}

/* ------------------------------------------------------------------ impact */

/** True when the simulation produced a 10th–90th band to print beside a wins figure. */
function hasBand(teamEval: TeamEvaluation): boolean {
  const u = teamEval.uncertainty;
  return Boolean(
    u &&
      u.prob_positive !== null &&
      u.prob_positive !== undefined &&
      Number.isFinite(u.p10) &&
      Number.isFinite(u.p90),
  );
}

export function ImpactTab({ teamEval, perf }: { teamEval: TeamEvaluation; perf: PerformanceDetail }) {
  const shape = sectionOf<RosterShapeDetail>(teamEval.detail, "roster_shape");
  const rows = useMemo(() => {
    const before = perf.rotation_before ?? [];
    const after = perf.rotation_after ?? [];
    if (before.length === 0 && after.length === 0) return [];
    const byId = new Map<string, { name: string; before: number; after: number }>();
    for (const r of before) byId.set(r.player_id, { name: r.name, before: r.minutes, after: 0 });
    for (const r of after) {
      const existing = byId.get(r.player_id);
      if (existing) existing.after = r.minutes;
      else byId.set(r.player_id, { name: r.name, before: 0, after: r.minutes });
    }
    return [...byId.values()]
      .sort((a, b) => Math.max(b.before, b.after) - Math.max(a.before, a.after))
      .slice(0, 8);
  }, [perf]);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="min-w-0 space-y-4">
        <Block>
          <ComponentBars components={teamEval.components} excluded={teamEval.excluded_components} />
          <Explainer summary="What each component measures" className="mt-3">
            <dl className="space-y-1.5">
              {Object.entries(COMPONENT_EXPLAIN).map(([key, explanation]) => (
                <div key={key}>
                  <dt className="inline font-semibold text-foreground">{COMPONENT_LABEL[key]}: </dt>
                  <dd className="inline">{explanation}</dd>
                </div>
              ))}
            </dl>
          </Explainer>
        </Block>
        <RosterShapePanel shape={shape} />
      </div>
      <div className="min-w-0 space-y-4">
        <Block>
          {rows.length > 0 ? (
            <BeforeAfterBars
              rows={rows}
              title="Rotation minutes — before vs after"
              unit="projected minutes per game"
              why="Where the deal actually changes who plays; a trade that never reaches the floor can't move the record."
            />
          ) : (
            <UnavailableNotice reason="This evaluation did not return a rotation breakdown, so the before-and-after minutes chart can't be drawn." />
          )}
          {perf.delta_wins !== undefined && (
            // A projected wins change never travels without its band: the point figure
            // here is the reallocation alone, so the simulated 10th–90th range sits beside it.
            <p className="mt-3 border-t border-hairline pt-2.5 text-[12px] leading-relaxed text-muted">
              Reallocating those minutes moves the projection by{" "}
              <span className="numeral text-foreground">{signed(perf.delta_wins, 1)}</span> wins
              before uncertainty is applied
              {hasBand(teamEval) ? (
                <>
                  {" "}(10th–90th percentile with it:{" "}
                  <span className="numeral text-foreground">
                    {signed(teamEval.uncertainty.p10, 1)} to {signed(teamEval.uncertainty.p90, 1)}
                  </span>{" "}
                  wins over a full 82-game season).
                </>
              ) : (
                <>
                  . No 10th–90th band accompanies it:{" "}
                  {teamEval.uncertainty.unavailable ?? "the simulation produced no outcome distribution"}.
                </>
              )}
            </p>
          )}
        </Block>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------- fit */

export function FitTab({ teamEval }: { teamEval: TeamEvaluation }) {
  const fit = sectionOf<FitDetail>(teamEval.detail, "fit");
  if (fit.unavailable) {
    return (
      <UnavailableNotice
        reason={fit.unavailable}
        steps={
          <ButtonLink href="/team-outlook" size="sm">
            Open Team Outlook
          </ButtonLink>
        }
      />
    );
  }

  const needs = fit.needs ?? {};
  const addressed = fit.needs_addressed ?? {};
  const skills = fit.skill_delta ?? {};
  const redundancies = fit.redundancies ?? {};
  const notAddressable = fit.needs_not_addressable ?? {};
  const rankedNeeds = Object.entries(needs)
    .filter(([, severity]) => severity > 0)
    .sort((a, b) => b[1] - a[1]);
  const maxSeverity = Math.max(0.001, ...rankedNeeds.map(([, s]) => s));
  const rankedSkills = Object.entries(skills).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const skillScale = Math.max(0.25, ...rankedSkills.map(([, v]) => Math.abs(v)));
  const penalised = Object.entries(redundancies).filter(([, v]) => v > 0);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Block label="Needs this deal addresses" aside="bar = severity · number = movement">
        <p className="-mt-1 text-[12px] leading-snug text-muted">
          Only needs the model measured as real for this roster are listed. The bar is how severe
          the need is; the number is how far this deal moves it.
        </p>
        {rankedNeeds.length === 0 ? (
          <p className="mt-3 text-[13px] text-muted">
            No measurable roster need is outstanding for this team, so fit is scored on redundancy
            alone.
          </p>
        ) : (
          <ul className="mt-3 space-y-2.5">
            {rankedNeeds.map(([key, severity]) => {
              // Three states, not two. A need whose skill is not measured on both sides of
              // the deal is absent from `needs_addressed` (fit.py skips it) without being
              // "not addressable" — that is a missing comparison, not a movement of 0.00.
              const unscored = key in notAddressable;
              const compared = key in addressed;
              const delta = addressed[key] ?? 0;
              return (
                <li key={key}>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate text-[13px] text-foreground">
                      {NEED_LABEL[key] ?? key}
                    </span>
                    {unscored ? (
                      <span className="shrink-0 text-[12px] text-unavail">not scored</span>
                    ) : !compared ? (
                      <span
                        className="shrink-0 text-[12px] text-unavail"
                        title="The skill that answers this need is not measured for the players on both sides of the deal, so no movement is computed."
                      >
                        not compared
                      </span>
                    ) : (
                      <span className="numeral flex shrink-0 items-baseline gap-1.5 text-[13px]">
                        <DirectionGlyph value={delta} />
                        <span
                          className={
                            direction(delta, 2) > 0
                              ? "text-legal"
                              : direction(delta, 2) < 0
                                ? "text-illegal"
                                : "text-faint"
                          }
                        >
                          {signed(delta, 2)}
                        </span>
                      </span>
                    )}
                  </div>
                  <div
                    className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-panel3"
                    role="img"
                    aria-label={`${NEED_LABEL[key] ?? key} severity ${severity.toFixed(2)} of 1`}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.max(2, (severity / maxSeverity) * 100)}%`,
                        background: unscored || !compared ? "var(--unknown)" : "var(--signal)",
                        opacity: unscored || !compared ? 0.6 : 0.85,
                      }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {Object.keys(notAddressable).length > 0 && (
          <div className="mt-3 space-y-1 border-t border-hairline pt-2.5">
            {Object.entries(notAddressable).map(([key, reason]) => (
              <p key={key} className="text-[11.5px] leading-snug text-faint">
                <span className="text-muted">{NEED_LABEL[key] ?? key}</span> is measured for
                this roster but not scored here — {reason}.
              </p>
            ))}
          </div>
        )}
      </Block>

      <Block label="Skill balance change" aside="arriving − leaving · minutes-weighted">
        <p className="-mt-1 text-[12px] leading-snug text-muted">
          Minutes-weighted skill profile arriving minus the profile leaving. Right of the centre
          line the roster gains the skill; left, it gives it up.
        </p>
        {rankedSkills.length === 0 ? (
          <p className="mt-3 text-[13px] text-muted">No skill profile was returned for this deal.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {rankedSkills.map(([key, value]) => (
              <li
                key={key}
                className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)_3.25rem] items-center gap-3"
              >
                <span className="truncate text-[12.5px] text-foreground">
                  {SKILL_LABEL[key] ?? key.replaceAll("_", " ")}
                </span>
                <DivergingBar value={value} scale={skillScale} />
                <span
                  className={clsx(
                    "numeral text-right text-[13px]",
                    direction(value, 2) > 0
                      ? "text-legal"
                      : direction(value, 2) < 0
                        ? "text-illegal"
                        : "text-faint",
                  )}
                >
                  {signed(value, 2)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {Object.keys(redundancies).length > 0 && (
          <p className="mt-3 border-t border-hairline pt-2.5 text-[11.5px] leading-snug text-faint">
            Redundancy penalty applied to:{" "}
            {penalised
              .map(([k, v]) => `${SKILL_LABEL[k] ?? k.replaceAll("_", " ")} (${v.toFixed(2)})`)
              .join(", ") || "none"}
            .
          </p>
        )}
      </Block>
    </div>
  );
}

/* --------------------------------------------------------------------- cap */

/**
 * Partial contract coverage in the cap tab (R2c). Payroll appears as a floor with the
 * coverage attached; the apron row states only what the known salaries already prove.
 * "Not yet proven above the tax" is not "below the tax", and this must never imply it.
 */
export function PartialCapPosition({ legality }: { legality: TeamLegality }) {
  const before = legality.payroll_coverage_before;
  const after = legality.payroll_coverage_after;
  const shownBefore = payrollDisclosure(
    legality.payroll_before,
    legality.payroll_known_before,
    before,
  );
  const shownAfter = payrollDisclosure(legality.payroll_after, legality.payroll_known_after, after);
  return (
    <div className="space-y-3">
      <StatStrip>
        <StatCell label="Payroll before" value={shownBefore.value} note={shownBefore.note} />
        <StatCell
          label="Payroll after"
          value={shownAfter.value}
          note={after ? shownAfter.note : "a traded salary is unknown"}
        />
        <StatCell
          label="Apron before"
          value={legality.apron_status_at_least_before ?? "not proven"}
          note={legality.apron_status_at_least_before ? "at least" : "known salaries clear no line"}
        />
        <StatCell
          label="Apron after"
          value={legality.apron_status_at_least_after ?? "not proven"}
          note={legality.apron_status_at_least_after ? "at least" : "known salaries clear no line"}
        />
      </StatStrip>
      <UnavailableNotice
        reason={
          <>
            {legality.payroll_coverage_note}{" "}
            Payroll is shown as a floor, so cap and apron position stay unverified — the missing
            salaries could cross any threshold.
          </>
        }
      />
    </div>
  );
}

export function CapTab({ teamEval }: { teamEval: TeamEvaluation }) {
  const contract = sectionOf<{ unavailable?: string; net_surplus_cap_share?: number; method?: string }>(
    teamEval.detail,
    "contract",
  );
  const assets = sectionOf<AssetsDetail>(teamEval.detail, "assets");
  const legality = teamEval.legality;
  const known = legality.payroll_before !== null && legality.payroll_after !== null;
  // A floor needs at least one priced contract to be a floor. With none on file the
  // honest statement is "not imported", not a coverage line reading "0 of 16".
  const partial =
    legality.payroll_known_before !== null &&
    legality.payroll_coverage_before?.players_known !== 0;
  const picks = [...(assets.picks_priced ?? []), ...(assets.picks_not_priced ?? [])];

  return (
    <div className="space-y-4">
      {known ? (
        <StatStrip>
          <StatCell label="Payroll before" value={money(legality.payroll_before)} />
          <StatCell label="Payroll after" value={money(legality.payroll_after)} />
          <StatCell label="Apron before" value={legality.apron_status_before ?? "—"} />
          <StatCell label="Apron after" value={legality.apron_status_after ?? "—"} />
        </StatStrip>
      ) : partial ? (
        <PartialCapPosition legality={legality} />
      ) : (
        <UnavailableNotice
          reason={
            contract.unavailable ??
            "Contract data isn't imported, so payroll, apron position and contract value can't be computed for this team."
          }
          steps={
            <ButtonLink href="/salary-cap-center" size="sm" variant="secondary">
              Open the Salary-Cap Center
            </ButtonLink>
          }
        />
      )}

      {contract.net_surplus_cap_share !== undefined && (
        <p className="text-[13px] leading-relaxed text-muted">
          Net contract surplus{" "}
          <span className="numeral text-foreground">
            {signed(contract.net_surplus_cap_share * 100, 2)}%
          </span>{" "}
          of the cap. {contract.method}
        </p>
      )}

      <Block label="Draft capital & flexibility" aside="in this deal">
        <StatStrip cols={3}>
          {/* The backend always counts picks when it returns this section; if the section
              is missing, the count is unknown — a dash, not a zero. */}
          <StatCell label="Picks in" value={assets.picks_in ?? "—"} note="received" />
          <StatCell label="Picks out" value={assets.picks_out ?? "—"} note="sent" />
          <StatCell
            label="Roster spots"
            value={assets.roster_spots_delta !== undefined ? signed(assets.roster_spots_delta, 0) : "—"}
            note="net change"
          />
        </StatStrip>

        {picks.length > 0 && (
          <ul className="mt-3 divide-y divide-hairline rounded-lg border border-hairline bg-panel">
            {picks.map((p, i) => (
              <li
                key={`${p.pick}-${p.direction}-${i}`}
                className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-3 py-2.5 text-[12px]"
              >
                <span className="text-[12px] font-medium text-foreground">
                  <span aria-hidden className={p.direction === "in" ? "text-signal" : "text-muted"}>
                    {p.direction === "in" ? "← " : "→ "}
                  </span>
                  {p.direction === "in" ? "In" : "Out"}
                </span>
                <span className="min-w-0 text-foreground">{p.pick}</span>
                <span className="numeral text-foreground">
                  {p.point !== null
                    ? `${p.point.toFixed(2)} (${p.low.toFixed(2)}–${p.high.toFixed(2)})`
                    : `${p.low.toFixed(2)}–${p.high.toFixed(2)}`}
                </span>
                <span className="text-faint">
                  slots {p.slot_support.min_slot}–{p.slot_support.max_slot}
                  {p.precision !== "interval" && ` · no point estimate (${p.precision})`}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-3 space-y-1 text-[11.5px] leading-snug text-faint">
          {assets.unavailable ? (
            <p>{assets.unavailable}</p>
          ) : (
            assets.precision_note && <p>{assets.precision_note}</p>
          )}
          {assets.payroll_delta !== undefined && (
            <p>
              Payroll change{" "}
              <span className="numeral text-muted">
                {assets.payroll_delta >= 0 ? "+" : "−"}
                {money(Math.abs(assets.payroll_delta))}
              </span>
              . {assets.payroll_scored_note}.
            </p>
          )}
          {assets.payroll_note && <p>{assets.payroll_note}.</p>}
        </div>
      </Block>
    </div>
  );
}

/* ---------------------------------------------------------------- timeline */

export function TimelineTab({ teamEval }: { teamEval: TeamEvaluation }) {
  const timeline = sectionOf<TimelineDetail>(teamEval.detail, "timeline");
  if (timeline.unavailable || timeline.incoming_alignment === undefined) {
    return (
      <UnavailableNotice
        reason={
          timeline.unavailable ??
          "Player ages are missing for at least one asset, so competitive-window alignment can't be scored."
        }
      />
    );
  }
  const arriving = timeline.incoming_alignment ?? 0;
  const departing = timeline.outgoing_alignment ?? 0;
  const delta = arriving - departing;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <Block label="Age against the competitive window" aside="0 = misaligned · 1 = aligned">
        {/* Two readings on one 0–1 track, so the comparison is a distance, not two numbers. */}
        <div className="relative mt-5 h-8" aria-hidden>
          <span className="absolute inset-x-0 top-1/2 h-px bg-line" />
          {[0, 0.25, 0.5, 0.75, 1].map((t) => (
            <span
              key={t}
              className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-faint"
              style={{ left: `${t * 100}%` }}
            />
          ))}
          <span
            className="absolute top-1/2 h-px -translate-y-1/2 bg-signal/60"
            style={{
              left: `${Math.min(arriving, departing) * 100}%`,
              width: `${Math.abs(delta) * 100}%`,
            }}
          />
          <TimelineMarker at={departing} label="out" tone="var(--series-b)" above />
          <TimelineMarker at={arriving} label="in" tone="var(--signal)" />
        </div>
        <div className="numeral mt-1 flex justify-between text-[10px] font-medium text-faint" aria-hidden>
          <span>0</span>
          <span>0.5</span>
          <span>1</span>
        </div>
        <p className="mt-3 text-[13px] leading-relaxed text-muted">
          {delta > 0.02
            ? "The players arriving align better with this team's stated window than the players leaving."
            : delta < -0.02
              ? "The players leaving aligned better with this team's stated window than the players arriving."
              : "Arriving and departing players sit at roughly the same point in this team's window."}{" "}
          Alignment is a documented age-vs-strategy curve, not a projection of future production.
        </p>
      </Block>
      <StatStrip cols={3}>
        <StatCell label="Strategy" value={(timeline.strategy ?? "custom").replaceAll("_", " ")} />
        <StatCell label="Arriving" value={arriving.toFixed(2)} note="alignment, 0–1" />
        <StatCell label="Departing" value={departing.toFixed(2)} note="alignment, 0–1" />
      </StatStrip>
    </div>
  );
}

function TimelineMarker({
  at,
  label,
  tone,
  above = false,
}: {
  at: number;
  label: string;
  tone: string;
  above?: boolean;
}) {
  return (
    <span
      className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
      style={{ left: `${Math.min(1, Math.max(0, at)) * 100}%` }}
    >
      <span className="block h-3 w-3 rounded-full border-2" style={{ borderColor: tone, background: "var(--arena)" }} />
      <span
        className={clsx(
          "absolute left-1/2 -translate-x-1/2 text-[11px] font-medium",
          above ? "bottom-full mb-0.5" : "top-full mt-0.5",
        )}
        style={{ color: tone }}
      >
        {label}
      </span>
    </span>
  );
}

/* -------------------------------------------------------------------- risk */

function AvailabilityBar({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  note: string;
  tone: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="eyebrow">{label}</span>
        <span className="numeral text-[15px] text-foreground">
          {value === null || value === undefined ? "—" : `${(value * 100).toFixed(0)}%`}
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-panel3" aria-hidden>
        {value !== null && value !== undefined && (
          <div className="h-full rounded-full" style={{ width: `${value * 100}%`, background: tone }} />
        )}
      </div>
      <p className="mt-1 text-[11px] leading-snug text-muted">{note}</p>
    </div>
  );
}

export function RiskTab({ teamEval }: { teamEval: TeamEvaluation }) {
  const risk = sectionOf<RiskDetail>(teamEval.detail, "risk");
  // `prob_positive` is the simulation's, and it is deliberately NOT the risk component:
  // it is the performance projection restated as a probability, and scoring it made
  // `risk` 0.86-correlated with `performance` (R5-1b).
  const probPositive = teamEval.uncertainty.prob_positive;
  const pct = (v: number | null | undefined) =>
    v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`;
  const delta = risk.availability_delta;
  const arrivingMeasured = risk.incoming_availability !== null && risk.incoming_availability !== undefined;
  const departingMeasured = risk.outgoing_availability !== null && risk.outgoing_availability !== undefined;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div className="min-w-0 space-y-4">
        <Block label="Availability exposure · the risk score" aside="historical games played">
          {risk.unavailable ? (
            <UnavailableNotice reason={`${risk.unavailable}.`} />
          ) : (
            <>
              <div className="space-y-3">
                <AvailabilityBar
                  label="Arriving"
                  value={risk.incoming_availability ?? risk.roster_availability}
                  tone="var(--signal)"
                  note={
                    arrivingMeasured
                      ? `${risk.incoming_availability_players ?? 0} player(s), minutes-weighted`
                      : `no arriving player with a games-played history — priced at this roster's ${pct(
                          risk.roster_availability,
                        )}`
                  }
                />
                <AvailabilityBar
                  label="Departing"
                  value={risk.outgoing_availability ?? risk.roster_availability}
                  tone="var(--series-b)"
                  note={
                    departingMeasured
                      ? `${risk.outgoing_availability_players ?? 0} player(s), minutes-weighted`
                      : `no departing player with a games-played history — priced at this roster's ${pct(
                          risk.roster_availability,
                        )}`
                  }
                />
              </div>
              {delta !== undefined && (
                <p className="mt-3 border-t border-hairline pt-2.5 text-[12px] leading-snug text-muted">
                  The risk score is this{" "}
                  <span className="numeral text-foreground">{signed(delta * 100, 1)} pt</span> change
                  in the availability of the minutes involved — nothing else.{" "}
                  {delta > 0.02
                    ? "This deal sheds games-missed exposure, so the score sits above 50."
                    : delta < -0.02
                      ? "This deal takes on games-missed exposure, so the score sits below 50."
                      : "Exposure is roughly unchanged."}{" "}
                  Availability is historical games played, not a medical prediction.
                </p>
              )}
            </>
          )}
        </Block>

        <Block label="Projection uncertainty" aside="reported, not scored">
          <UncertaintyStrip u={teamEval.uncertainty} />
          {teamEval.uncertainty.top_uncertainty_drivers.length > 0 && (
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {teamEval.uncertainty.top_uncertainty_drivers.map((d) => (
                <li
                  key={d.side}
                  className="flex items-baseline justify-between gap-3 rounded-lg bg-panel px-3 py-2 text-[12px]"
                >
                  <span className="text-muted">
                    {d.side.charAt(0).toUpperCase() + d.side.slice(1)} side spread
                  </span>
                  <span className="numeral text-foreground">±{d.spread_wins.toFixed(1)} wins</span>
                </li>
              ))}
            </ul>
          )}
          <Explainer summary="Why the chance it helps is not the risk score" className="mt-3">
            <p>
              <span className="numeral text-foreground">{pct(probPositive)}</span> of{" "}
              {teamEval.uncertainty.n_draws.toLocaleString()}
              {" "}simulations produce a positive win
              impact. That is the projection&apos;s own uncertainty and is reported here, not scored:
              it is the performance component restated as a probability, and counting it as risk
              made the two components 0.86-correlated.
            </p>
            {risk.legality_verification && risk.legality_verification.share !== null && (
              <p className="mt-1.5">
                {risk.legality_verification.rules_with_a_definite_verdict} of{" "}
                {risk.legality_verification.rules_evaluated} implemented CBA checks reached a verdict
                for this team. Reported, never scored — what moves it is which contract fields the
                configured provider supplies, not the deal.
              </p>
            )}
          </Explainer>
        </Block>
      </div>

      <Block className="self-start">
        {teamEval.sensitivity_tornado.length > 0 ? (
          <TornadoChart bars={teamEval.sensitivity_tornado} />
        ) : (
          <UnavailableNotice reason="No sensitivity range was returned for this evaluation." />
        )}
        <p className="mt-3 border-t border-hairline pt-2.5 text-[11.5px] leading-snug text-faint">
          Each bar swings one priority weight by ±50% while the others re-scale. A wide bar means
          the verdict depends on your priorities, not on the deal.
        </p>
      </Block>
    </div>
  );
}
