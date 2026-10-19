import { describe, expect, it } from "vitest";
import {
  conditioningSentence,
  evaluationWords,
  evidenceTier,
  formatInterval,
  formatQuantity,
  sampleSentence,
  thinSample,
  type IntelRecord,
} from "@/lib/skillEvidence";

function record(over: Partial<IntelRecord> = {}): IntelRecord {
  return {
    player_id: "p",
    quantity: "fg3_pct",
    unit: "fraction of 1",
    output_kind: "inferred",
    value: 0.3635,
    period: { season: "2025-26", season_type: "regular", as_of_season: "2025-26" },
    target_period: null,
    interval: null,
    sample: null,
    model: { name: "m", version: "v", status: "candidate", evaluation_status: "development_only", evaluation_ref: null, methodology: "" },
    provenance: { sources: [], data_snapshot_id: null, code_commit: null },
    assumptions: [],
    limitations: [],
    reliability: null,
    conditioning: null,
    ...over,
  };
}

describe("evidence tier — nothing modelled reads as validated unless its own record says so", () => {
  it("measured values are measured", () => {
    expect(evidenceTier(record({ output_kind: "measured", model: null }))).toBe("measured");
  });
  it("a candidate is experimental even when its holdout confirmed", () => {
    for (const kind of ["inferred", "projected"] as const) {
      expect(
        evidenceTier(record({ output_kind: kind, model: { ...record().model!, status: "candidate", evaluation_status: "holdout_confirmed" } })),
      ).toBe("experimental");
    }
  });
  it("an active model without a confirmed holdout is experimental", () => {
    expect(evidenceTier(record({ model: { ...record().model!, status: "active", evaluation_status: "benchmarked" } }))).toBe("experimental");
  });
  it("only active AND holdout-confirmed is validated", () => {
    expect(evidenceTier(record({ model: { ...record().model!, status: "active", evaluation_status: "holdout_confirmed" } }))).toBe("validated");
  });
  it("a modelled record with no model never qualifies", () => {
    expect(evidenceTier(record({ model: null }))).toBe("experimental");
  });
});

describe("wording", () => {
  it("formats percentages, ratios and counts in their own units", () => {
    expect(formatQuantity("fg3_pct", 0.36351)).toBe("36.4%");
    expect(formatQuantity("ast_to_ratio", 2.18)).toBe("2.18");
    expect(formatQuantity("games_played", 16)).toBe("16");
    expect(formatQuantity("minutes_per_game", 32.6)).toBe("32.6");
    expect(formatQuantity("fg3_pct", null)).toBe("—");
  });
  it("names the interval kind and level", () => {
    const text = formatInterval("fg3_pct", {
      low: 0.349, high: 0.378, nominal_level: 0.8, method: "", calibration: null, kind: "credible", interpretation: "",
    });
    expect(text).toBe("34.9% – 37.8% (80% credible range)");
    expect(formatInterval("fg3_pct", null)).toBeNull();
  });
  it("says what a figure rests on and flags reconstructed counts", () => {
    const r = record({
      output_kind: "measured",
      sample: { games: 16, minutes: 521.6, attempts: 143, unit: "three-point attempts", makes: 47, counts_quality: "exact", attempts_min: null, attempts_max: null, seasons_used: null },
    });
    expect(sampleSentence(r)).toBe("47 of 143 three-point attempts · 16 games");
    const rough = record({
      output_kind: "measured",
      sample: { games: 60, minutes: 1000, attempts: 143, unit: "three-point attempts", makes: 47, counts_quality: "range", attempts_min: 140, attempts_max: 146, seasons_used: null },
    });
    expect(sampleSentence(rough)).toContain("reconstructed");
    expect(sampleSentence(rough)).toContain("47 of 140–146");
  });
  it("flags a thin season", () => {
    expect(thinSample(record({ sample: { games: 16, minutes: 521, attempts: null, unit: null, makes: null, counts_quality: null, attempts_min: null, attempts_max: null, seasons_used: null } }))).toBe(true);
    expect(thinSample(record({ sample: { games: 70, minutes: 2200, attempts: null, unit: null, makes: null, counts_quality: null, attempts_min: null, attempts_max: null, seasons_used: null } }))).toBe(false);
  });
  it("a projection always carries its condition; a non-projection never invents one", () => {
    expect(conditioningSentence(record({ output_kind: "projected" }))).toMatch(/conditional/);
    expect(conditioningSentence(record({ output_kind: "inferred" }))).toBeNull();
  });
  it("explains an evaluation status in words, and never dresses development evidence as confirmation", () => {
    expect(evaluationWords("development_only")).toMatch(/not confirmed/);
    expect(evaluationWords("holdout_confirmed")).toMatch(/small effect, not a claim of certainty/);
    expect(evaluationWords("holdout_confirmed")).toMatch(/still a candidate/);
    expect(evaluationWords("unevaluated")).toBe("not evaluated");
    expect(evaluationWords(undefined)).toBe("unknown");
  });
});
