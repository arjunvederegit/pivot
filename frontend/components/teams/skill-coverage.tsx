"use client";

/**
 * What the rotation is strong at, skill by skill.
 *
 * Each entry is a Measurement, and an unmeasured skill says so with its reason. It is
 * deliberately not drawn as a midpoint marker: a roster whose skill cannot be seen is not
 * a roster that is average at it, and a marker at 50 would claim the second thing.
 */

import clsx from "clsx";
import { count, ordinal } from "@/lib/format";
import type { TeamProfile } from "@/lib/types";
import { Badge, ErrorState, Explainer, SkeletonRows, SourceRail, UnavailableNotice } from "@/components/ui";
import { PercentileAxis, PercentileTrack } from "@/components/teams/percentile-track";
import { TeamPanel } from "@/components/teams/team-panel";

/** label · track · value from `sm`; below it the track takes its own full-width line. */
const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_3rem] gap-x-4 sm:grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_3rem]";

export function SkillCoveragePanel({
  profile,
  error,
  className,
}: {
  profile: TeamProfile | undefined;
  error: unknown;
  className?: string;
}) {
  const coverage = profile?.skill_coverage ?? [];
  const measured = coverage.filter((skill) => skill.available);
  const weakKeys = new Set((profile?.weaknesses ?? []).map((n) => n.key));
  // Which headline need each skill answers, from the server's own need → skill map.
  const answers = new Map<string, string[]>();
  for (const need of profile?.needs ?? []) {
    if (need.addressed_by && weakKeys.has(need.key)) {
      answers.set(need.addressed_by, [...(answers.get(need.addressed_by) ?? []), need.label]);
    }
  }
  const sides = ["offense", "defense", "physical"] as const;
  // When every measured skill used the same number of rotation players, say it once.
  const counts = new Set(measured.map((s) => s.rotation_players_measured));
  const uniformCount = counts.size === 1 ? [...counts][0] : null;

  return (
    <TeamPanel
      className={clsx("flex flex-col", className)}
      bodyClassName="flex flex-1 flex-col"
      title="Skill coverage"
      subtitle="The top-10 rotation's third-best player in each skill, as a league player percentile — what the roster already covers. A player measure, not the team statistic used in the diagnosis above."
    >
      {error ? (
        <ErrorState message={`Could not load skill coverage: ${String(error)}`} />
      ) : !profile ? (
        <SkeletonRows rows={8} height="h-8" />
      ) : coverage.length === 0 ? (
        <UnavailableNotice reason="No skill dimensions were returned for this roster." />
      ) : (
        <>
          <div className="space-y-4">
            {sides
              .filter((side) => coverage.some((s) => s.side === side))
              .map((side) => (
                <div key={side}>
                  <h4 className="eyebrow">{side}</h4>
                  <ul className="mt-1.5 divide-y divide-hairline border-t border-hairline">
                    {coverage
                      .filter((s) => s.side === side)
                      .map((skill) => {
                        const answering = answers.get(skill.key);
                        // The schema ties `available` to a value; check both so a missing
                        // value can never be drawn as the 0th percentile.
                        const value =
                          skill.available && typeof skill.value === "number" ? skill.value * 100 : null;
                        return (
                          <li
                            key={skill.key}
                            className={`${ROW_GRID} items-center gap-y-1.5 py-2`}
                            title={skill.available ? skill.method : skill.reason}
                          >
                            <span className="min-w-0 text-[13px] leading-tight text-foreground">
                              {skill.label}
                            </span>
                            {value !== null ? (
                              <>
                                <PercentileTrack
                                  value={value}
                                  className="col-span-2 row-start-2 sm:col-span-1 sm:row-start-auto"
                                />
                                <span className="numeral text-right text-[13.5px] text-foreground">
                                  {ordinal(value)}
                                </span>
                                {!uniformCount && (
                                  <span className="col-span-2 text-[11px] text-faint sm:col-span-3">
                                    {count(skill.rotation_players_measured, "rotation player")} measured
                                  </span>
                                )}
                              </>
                            ) : (
                              <>
                                <span className="justify-self-end sm:col-span-2">
                                  <Badge status="unavailable">not measured</Badge>
                                </span>
                                <p className="col-span-2 mt-0.5 text-[11.5px] leading-relaxed text-muted sm:col-span-3">
                                  {skill.reason || "No value was served for this skill."}
                                </p>
                              </>
                            )}
                            {answering && (
                              <p className="col-span-2 text-[11.5px] text-muted sm:col-span-3">
                                Answers the need{" "}
                                <span className="font-medium text-illegal">{answering.join(", ")}</span>
                              </p>
                            )}
                          </li>
                        );
                      })}
                  </ul>
                </div>
              ))}
            <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_3rem]">
              <span className="hidden sm:block" />
              <PercentileAxis note="median" />
            </div>
          </div>
          <div className="mt-auto space-y-2.5 pt-4">
            <p className="text-[11.5px] leading-relaxed text-faint">
              {measured.length} of {coverage.length} declared skills are measured for this
              rotation
              {uniformCount !== null
                ? `, each across ${count(uniformCount, "rotation player")}`
                : ""}
              . The rest are listed with the reason rather than dropped.
            </p>
            <Explainer summary="How coverage is measured">
              {measured[0]?.method
                ? `${measured[0].method.charAt(0).toUpperCase()}${measured[0].method.slice(1)}. `
                : ""}
              The third-best value is the same statistic the trade evaluator prices redundancy
              against. The acquisition search applies the same third-best rule across the whole
              roster rather than the rotation only, so the bar it prints for a skill can sit
              above the one shown here.
            </Explainer>
            <SourceRail source={measured[0]?.source ?? "Pivot roster profile"} />
          </div>
        </>
      )}
    </TeamPanel>
  );
}
