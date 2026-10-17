import { describe, expect, it } from "vitest";
import {
  bestByComponent,
  decisionScore,
  distributionOf,
  rankBoard,
  sharedWinsAxis,
  signed,
  unrankedReason,
  winsTickStep,
  type BoardAlternative,
  type BoardComparison,
} from "@/components/strategy/model";

/* Synthetic fixtures only. */
function alt(
  id: string,
  components: Record<string, number | null>,
  extra: Partial<BoardAlternative> = {},
): BoardAlternative {
  return {
    trade_id: id,
    name: id,
    legality_status: "conditionally_valid",
    decision_status: "scored",
    confidence: "medium",
    components,
    uncertainty: null,
    incoming: [],
    outgoing: [],
    ...extra,
  } as unknown as BoardAlternative;
}

describe("signed", () => {
  it("takes the sign from the rounded value, so a tiny negative is never −0.0", () => {
    expect(signed(-0.04)).toBe("+0.0");
    expect(signed(1.26)).toBe("+1.3");
    expect(signed(-1.26)).toBe("−1.3");
    expect(signed(null)).toBe("—");
  });
});

describe("decisionScore — Σ(w·c)/Σ(w) over non-null components", () => {
  it("renormalizes over the components that were scored", () => {
    const w = { performance: 1, fit: 1, contract: 1 };
    // contract is unavailable, so only performance and fit count
    expect(decisionScore(w, { performance: 70, fit: 50, contract: null })).toBeCloseTo(60);
  });

  it("returns null — never 0 — when nothing weighted could be scored", () => {
    expect(decisionScore({ performance: 0, fit: 0 }, { performance: 70, fit: 50 })).toBeNull();
    expect(decisionScore({ contract: 1 }, { contract: null })).toBeNull();
  });
});

describe("rankBoard", () => {
  const comparison = {
    alternatives: [
      alt("a", { performance: 60, fit: 55 }),
      alt("b", { performance: 70, fit: 40 }),
      alt("illegal", { performance: null }, { decision_status: "suppressed_illegal" } as Partial<BoardAlternative>),
      alt("nothing", { performance: null }, { decision_status: "insufficient_data" } as Partial<BoardAlternative>),
    ],
  } as unknown as BoardComparison;

  it("ranks scored deals by the live score and lists the rest without ranking them", () => {
    const { ranked, unrankable } = rankBoard(comparison, { performance: 1, fit: 1 });
    expect(ranked.map((r) => r.alt.trade_id)).toEqual(["a", "b"]);
    expect(unrankable.map((a) => a.trade_id).sort()).toEqual(["illegal", "nothing"]);
  });

  it("re-ranks under different priorities", () => {
    const { ranked } = rankBoard(comparison, { performance: 1, fit: 0 });
    expect(ranked[0].alt.trade_id).toBe("b");
  });

  it("an illegal deal is never a leader, whatever the weights", () => {
    const { ranked } = rankBoard(comparison, { performance: 100, fit: 100 });
    expect(ranked.some((r) => r.alt.trade_id === "illegal")).toBe(false);
  });

  it("gives each kind of unranked deal its own reason", () => {
    expect(unrankedReason(comparison.alternatives[2])).toMatch(/fails a verified rule/);
    expect(unrankedReason(comparison.alternatives[3])).toMatch(/nothing could be scored/);
    expect(unrankedReason(comparison.alternatives[0])).toMatch(/weighted 0/);
  });
});

describe("shared wins axis", () => {
  it("always lands on a multiple of its tick step, so the zero line is a tick", () => {
    const wide = [
      alt("w", {}, {
        uncertainty: { median: 3, p10: -6.4, p90: 6.9, prob_positive: 0.6, n_draws: 2000 },
      } as Partial<BoardAlternative>),
    ];
    const axis = sharedWinsAxis(wide);
    expect(axis % winsTickStep(axis)).toBe(0);
    expect(axis).toBeGreaterThanOrEqual(7);
  });

  it("ignores deals with no distribution instead of treating them as zero", () => {
    expect(distributionOf(null)).toBeNull();
    expect(distributionOf({ median: 0, p10: 0, p90: 0, prob_positive: null } as never)).toBeNull();
    expect(sharedWinsAxis([alt("none", {})])).toBe(1);
  });
});

describe("bestByComponent", () => {
  it("reports null for a component no deal has, not 0", () => {
    const best = bestByComponent([alt("a", { performance: 60, contract: null }), alt("b", { performance: 70 })]);
    expect(best.performance).toBe(70);
    expect(best.contract).toBeNull();
  });
});
