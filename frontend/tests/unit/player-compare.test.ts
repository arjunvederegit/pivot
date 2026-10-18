import { describe, expect, it } from "vitest";
import {
  axisPercentile,
  percentileText,
  rowLeaders,
  shortName,
  type PlacedValue,
} from "@/components/players/stats";

const placed = (value: number | null, qualified = true): PlacedValue => ({
  value,
  qualified,
  percentile: value === null || !qualified ? null : 50,
});

describe("rowLeaders — who leads a comparison row", () => {
  it("never lets an unqualified value lead, however good it looks", () => {
    // a 4-attempt 75% from three is not a measurement
    const leaders = rowLeaders([placed(0.75, false), placed(0.4), placed(0.38)], "high");
    expect([...leaders]).toEqual([1]);
  });

  it("needs at least two comparable values before anyone leads", () => {
    expect(rowLeaders([placed(0.5), placed(0.9, false)], "high").size).toBe(0);
    expect(rowLeaders([placed(0.5), placed(null)], "high").size).toBe(0);
  });

  it("reads lower-is-better rows the right way round, and shares ties", () => {
    expect([...rowLeaders([placed(3.1), placed(1.2), placed(2)], "low")]).toEqual([1]);
    expect([...rowLeaders([placed(2), placed(2), placed(1)], "high")]).toEqual([0, 1]);
  });
});

describe("axisPercentile", () => {
  it("reverses a lower-is-better axis so right always means better", () => {
    expect(axisPercentile(90, "high")).toBe(90);
    expect(axisPercentile(90, "low")).toBe(10);
  });
});

describe("percentileText", () => {
  it("holds printed percentiles to 1st–99th, like the impact readouts", () => {
    expect(percentileText(99.8)).toBe("99th");
    expect(percentileText(0.2)).toBe("1st");
    expect(percentileText(52.4)).toBe("52nd");
  });
});

describe("shortName", () => {
  it("keys a marker to a surname, keeping generational suffixes", () => {
    expect(shortName("Shai Gilgeous-Alexander")).toBe("Gilgeous-Alexander");
    expect(shortName("Kevin McCullar Jr.")).toBe("McCullar Jr.");
    expect(shortName("Nenê")).toBe("Nenê");
  });
});
