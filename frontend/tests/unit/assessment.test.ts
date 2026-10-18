import { describe, expect, it } from "vitest";
import { assessSide } from "@/lib/assessment";
import type { TeamEvaluation } from "@/lib/types";

/** The Chicago case from review: favourable score, but components and the rules check are incomplete. */
function evaluation(over: Partial<TeamEvaluation> = {}): TeamEvaluation {
  return {
    team_id: "chi",
    legality: { status: "conditionally_valid" } as TeamEvaluation["legality"],
    decision_status: "scored",
    suppression: null,
    composite_utility: 62,
    confidence: "medium",
    components: {},
    excluded_components: ["contract", "assets"],
    drivers: [
      { component: "performance", score: 70, weight: 0.3, contribution: 4.2 },
      { component: "fit", score: 45, weight: 0.2, contribution: -1.1 },
    ],
    weights: {},
    detail: {},
    uncertainty: { n_draws: 2000, median: 0.9, p10: -1.6, p90: 3.5, prob_positive: 0.68, top_uncertainty_drivers: [] },
    sensitivity_tornado: [],
    incoming: [],
    outgoing: [],
    evaluated_at: "2026-10-18T00:00:00Z",
    ...over,
  } as TeamEvaluation;
}

describe("assessSide", () => {
  it("qualifies a favourable score and keeps the category and limitations beside it", () => {
    const a = assessSide(evaluation(), "Chicago");
    expect(a.headline).toBe("Model favors Chicago on the evaluated factors");
    expect(a.category).toBe("Clear win");
    expect(a.headline).not.toMatch(/^Clear win$/);
    expect(a.reason).toMatch(/On-court impact/);
    expect(a.limitations.join(" ")).toMatch(/could not be scored/);
    expect(a.limitations.join(" ")).toMatch(/rule check is incomplete/i);
  });

  it("reports nothing material when the evaluation is complete", () => {
    const a = assessSide(
      evaluation({ excluded_components: [], legality: { status: "verified_legal" } as TeamEvaluation["legality"] }),
      "Chicago",
    );
    expect(a.limitations).toEqual([]);
  });

  it("does not turn low confidence into an assessment", () => {
    const a = assessSide(evaluation({ confidence: "low" }), "Chicago");
    expect(a.tone).toBe("unknown");
    expect(a.headline).toMatch(/Not enough to assess/);
  });

  it("uses unfavourable wording, with the largest negative driver as the reason", () => {
    const a = assessSide(
      evaluation({
        composite_utility: 35,
        drivers: [{ component: "fit", score: 30, weight: 0.2, contribution: -6.9 }],
      }),
      "San Antonio",
    );
    expect(a.tone).toBe("unfavorable");
    expect(a.reason).toMatch(/Roster fit/);
  });
});
