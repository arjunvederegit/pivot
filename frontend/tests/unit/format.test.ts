import { describe, expect, it } from "vitest";
import { count, height, money, pct, tei } from "@/lib/format";

describe("money", () => {
  it("formats millions", () => {
    expect(money(25_500_000)).toBe("$25.5M");
  });
  it("is honest about missing values", () => {
    expect(money(null)).toBe("unavailable");
    expect(money(undefined)).toBe("unavailable");
  });
});

describe("tei", () => {
  it("signs positive values", () => {
    expect(tei(2.34)).toBe("+2.3");
    expect(tei(-1.2)).toBe("-1.2");
  });
  it("dashes missing values", () => {
    expect(tei(null)).toBe("—");
  });
});

describe("pct", () => {
  it("renders share as percent", () => {
    expect(pct(0.873)).toBe("87%");
    expect(pct(null)).toBe("—");
  });
});

describe("height", () => {
  it("converts inches to feet notation", () => {
    expect(height(80)).toBe(`6'8"`);
    expect(height(null)).toBe("—");
  });
});

describe("count", () => {
  it("agrees with the number", () => {
    // "1 saved deals" is the kind of thing that reads as a product nobody proofread,
    // and `array.length` reaches 1 far more often than it looks like it will.
    expect(count(1, "saved deal")).toBe("1 saved deal");
    expect(count(2, "saved deal")).toBe("2 saved deals");
    expect(count(0, "saved deal")).toBe("0 saved deals");
  });
  it("takes an explicit plural for irregular nouns", () => {
    expect(count(1, "franchise", "franchises")).toBe("1 franchise");
    expect(count(30, "franchise", "franchises")).toBe("30 franchises");
  });
  it("groups large counts", () => {
    expect(count(1151, "team-side")).toBe("1,151 team-sides");
  });
});

describe("formatDay (R9.3A) — a bare date is a day, not an instant", () => {
  it("does not shift an expected-return date to the previous evening or invent a time", async () => {
    const { formatDay } = await import("@/lib/format");
    expect(formatDay("2026-10-20")).toBe("Oct 20, 2026");
    expect(formatDay("2027-07-01")).toBe("Jul 1, 2027");
    expect(formatDay(null)).toBe("unknown");
    expect(formatDay("2026-10-20")).not.toMatch(/PM|AM/);
  });
});
