import { describe, expect, it } from "vitest";
import {
  histogram,
  impactReferenceSchema,
  percentileDisplay,
  percentileOf,
  placeImpact,
  positionLabel,
  rankOf,
  type ImpactReference,
} from "@/lib/impact";

/* Synthetic fixture: the shape the backend serves, with ties at +1.00. */
const VALUES = [-1.75, -0.2, 0.5, 1.0, 1.0, 2.5];
const reference: ImpactReference = {
  available: true,
  metric: "Pivot Estimated Impact (TEI)",
  scale: "Unitless index. Not points per 100 possessions.",
  season: "2025-26",
  model: { version: "v", algorithm: "a", training_period: "2023-24..2025-26", trained_at: null },
  population: {
    size: VALUES.length,
    definition: "fixture",
    min_window_minutes: 200,
    rostered_scored: 5,
    rostered_unscored: 1,
    unrostered_scored: 1,
    distinct_values: 5,
  },
  values: VALUES,
  precision: 2,
  percentile_method: "mid-rank",
  rank_method: "competition",
  limitations: [],
};

describe("percentileOf — mid-rank, matching the backend", () => {
  it("counts equal values as half", () => {
    expect(percentileOf(2.5, VALUES)).toBeCloseTo((100 * 5.5) / 6);
    expect(percentileOf(1.0, VALUES)).toBeCloseTo((100 * (3 + 1)) / 6);
    expect(percentileOf(-1.75, VALUES)).toBeCloseTo((100 * 0.5) / 6);
  });

  it("never reaches 0 or 100 for a member of the population", () => {
    const big = Array.from({ length: 512 }, (_, i) => i / 10);
    expect(percentileOf(big[0], big)).toBeGreaterThan(0);
    expect(percentileOf(big[511], big)).toBeLessThan(100);
  });

  it("refuses an empty population instead of inventing a position", () => {
    expect(() => percentileOf(1, [])).toThrow();
  });
});

describe("rankOf — competition ranking", () => {
  it("gives tied players the same rank and says how many share it", () => {
    expect(rankOf(2.5, VALUES)).toEqual({ rank: 1, tied: 1 });
    expect(rankOf(1.0, VALUES)).toEqual({ rank: 2, tied: 2 });
    expect(rankOf(0.5, VALUES)).toEqual({ rank: 4, tied: 1 });
    expect(rankOf(-1.75, VALUES)).toEqual({ rank: 6, tied: 1 });
  });
});

describe("placeImpact", () => {
  it("returns null — never a zero or a median — for a missing estimate", () => {
    expect(placeImpact(null, reference)).toBeNull();
    expect(placeImpact(undefined, reference)).toBeNull();
  });

  it("returns null when there is no population to place against", () => {
    expect(placeImpact(1.0, undefined)).toBeNull();
    expect(placeImpact(1.0, { available: false, reason: "no model" })).toBeNull();
  });

  it("compares at served precision, so a served value is always a member", () => {
    const placed = placeImpact(1.004, reference);
    expect(placed?.value).toBe(1.0);
    expect(placed?.rank).toBe(2);
    expect(placed?.tied).toBe(2);
    expect(placed?.size).toBe(6);
  });

  it("places the 10–90% band in the same population", () => {
    const placed = placeImpact(1.0, reference, { low: 0.5, high: 2.5 });
    expect(placed?.band?.percentileLow).toBeCloseTo(percentileOf(0.5, VALUES));
    expect(placed?.band?.percentileHigh).toBeCloseTo(percentileOf(2.5, VALUES));
  });

  it("omits the band rather than guessing one", () => {
    expect(placeImpact(1.0, reference, { low: null, high: 2 })?.band).toBeNull();
    expect(placeImpact(1.0, reference)?.band).toBeNull();
  });
});

describe("positionLabel — factual fractions, not tiers", () => {
  it("describes rank as a share of the scored population", () => {
    expect(positionLabel(1, 512)).toBe("Top 1%");
    expect(positionLabel(20, 512)).toBe("Top 5%");
    expect(positionLabel(50, 512)).toBe("Top 10%");
    expect(positionLabel(120, 512)).toBe("Top quarter");
    expect(positionLabel(256, 512)).toBe("Upper half");
    expect(positionLabel(300, 512)).toBe("Lower half");
    expect(positionLabel(450, 512)).toBe("Bottom quarter");
    expect(positionLabel(500, 512)).toBe("Bottom 10%");
  });

  it("never uses star/starter/role vocabulary", () => {
    for (let r = 1; r <= 512; r += 7) {
      expect(positionLabel(r, 512)).not.toMatch(/star|starter|role|elite|bench/i);
    }
  });
});

describe("percentileDisplay", () => {
  it("never claims the 0th or 100th percentile after rounding", () => {
    expect(percentileDisplay(99.9)).toBe(99);
    expect(percentileDisplay(0.1)).toBe(1);
    expect(percentileDisplay(62.4)).toBe(62);
  });
});

describe("impactReferenceSchema", () => {
  it("accepts the served shape and the unavailable shape", () => {
    expect(impactReferenceSchema.safeParse(reference).success).toBe(true);
    expect(impactReferenceSchema.safeParse({ available: false, reason: "x" }).success).toBe(true);
  });

  it("rejects an unsorted population or a size that disagrees with it", () => {
    expect(
      impactReferenceSchema.safeParse({ ...reference, values: [2, 1, 0, 0, 0, 0] }).success,
    ).toBe(false);
    expect(
      impactReferenceSchema.safeParse({
        ...reference,
        population: { ...reference.population, size: 7 },
      }).success,
    ).toBe(false);
  });
});

describe("histogram", () => {
  it("bins every value exactly once", () => {
    const h = histogram(VALUES, 4);
    expect(h.counts.reduce((a, b) => a + b, 0)).toBe(VALUES.length);
    expect(h.min).toBe(-1.75);
    expect(h.max).toBe(2.5);
  });
});
