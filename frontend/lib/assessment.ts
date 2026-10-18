/**
 * A qualified reading of one team's side of a deal — built only from fields the evaluation
 * already served. Nothing here changes a number or invents a threshold: the headline word comes
 * from the existing `fanVerdict` bucket (itself a function of `composite_utility` and
 * `confidence`), and the reason, range and limitations are the evaluation's own drivers,
 * outcome band, legality status and excluded components.
 *
 * The point is presentation. A favourable basketball estimate does not establish that the deal
 * is legal, financially sound or feasible, so the limitations travel with the headline.
 */

import { COMPONENT_LABEL, VERDICT_LABEL, fanVerdict } from "@/lib/format";
import type { TeamEvaluation } from "@/lib/types";

export type AssessmentTone = "favorable" | "neutral" | "unfavorable" | "unknown";

export interface Assessment {
  /** "Model favors Chicago on the evaluated factors." — a qualified statement, never "Clear win". */
  headline: string;
  tone: AssessmentTone;
  /** The upstream category ("Clear win"), to be shown labelled as a model category. */
  category: string;
  /** The single largest driver in the direction of the headline, in the model's own terms. */
  reason: string | null;
  /** What the deal's basketball estimate does NOT cover or establish. Empty = nothing material. */
  limitations: string[];
}

const LEGALITY_LIMIT: Record<string, string> = {
  conditionally_valid: "Trade-rule check is incomplete — some rules could not be verified with the data on file.",
  not_evaluated: "Trade rules were not checked — required data is missing.",
};

export function assessSide(ev: TeamEvaluation, name: string): Assessment {
  const key = fanVerdict(ev.composite_utility, ev.confidence);
  const category = VERDICT_LABEL[key];
  const limitations: string[] = [];

  if (ev.excluded_components.length > 0) {
    limitations.push(
      `${ev.excluded_components.map((c) => COMPONENT_LABEL[c] ?? c).join(", ")} could not be scored — the score covers only the remaining factors.`,
    );
  }
  const legality = LEGALITY_LIMIT[ev.legality.status];
  if (legality) limitations.push(legality);
  if (ev.confidence === "low") limitations.push("The model reports low confidence in this evaluation.");
  if (ev.has_unmodeled_players) limitations.push("Some roster players have no impact estimate and are left out of the projection.");

  const drivers = [...(ev.drivers ?? [])].sort((a, b) => b.contribution - a.contribution);
  const up = drivers.find((d) => d.contribution > 0.5);
  const down = [...drivers].reverse().find((d) => d.contribution < -0.5);
  const label = (c: string) => COMPONENT_LABEL[c] ?? c;
  const fmt = (n: number) => `${n > 0 ? "+" : "−"}${Math.abs(n).toFixed(1)}`;

  switch (key) {
    case "clear_win":
      return {
        headline: `Model favors ${name} on the evaluated factors`,
        tone: "favorable",
        category,
        reason: up ? `Main reason: ${label(up.component)} (${fmt(up.contribution)} score points).` : null,
        limitations,
      };
    case "neutral":
      return {
        headline: `Model reads this as roughly neutral for ${name}`,
        tone: "neutral",
        category,
        reason: up && down ? `${label(up.component)} (${fmt(up.contribution)}) is offset by ${label(down.component)} (${fmt(down.contribution)}).` : null,
        limitations,
      };
    case "net_negative":
    case "clear_loss":
      return {
        headline:
          key === "clear_loss"
            ? `Model reads this as unfavorable for ${name} on the evaluated factors`
            : `Model leans against this deal for ${name}`,
        tone: "unfavorable",
        category,
        reason: down ? `Main reason: ${label(down.component)} (${fmt(down.contribution)} score points).` : null,
        limitations,
      };
    default:
      return {
        headline: `Not enough to assess this deal for ${name}`,
        tone: "unknown",
        category,
        reason: null,
        limitations: limitations.length ? limitations : ["The evaluation did not produce a decision score."],
      };
  }
}
