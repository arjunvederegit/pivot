import { afterEach, describe, expect, it, vi } from "vitest";
import {
  LARGE_HEADSHOT_MIN_PX,
  headshotTierForSize,
  normalizeNbaPlayerId,
  officialHeadshotUrl,
  officialHeadshotsEnabled,
  officialSource,
  resolveHeadshot,
} from "@/lib/headshots";

afterEach(() => vi.unstubAllEnvs());

const base = {
  nbaPlayerId: 2544,
  size: 32,
  official: "available" as const,
  localKnown: true,
  failed: new Set<string>(),
};

describe("official URL construction", () => {
  it("formats both CDN patterns from the NBA id", () => {
    expect(officialHeadshotUrl(2544, "large")).toBe(
      "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png",
    );
    expect(officialHeadshotUrl(2544, "small")).toBe(
      "https://cdn.nba.com/headshots/nba/latest/260x190/2544.png",
    );
  });

  it("handles ids from the 2025-26 draft class (7-digit ids)", () => {
    expect(officialHeadshotUrl(1643407, "small")).toBe(
      "https://cdn.nba.com/headshots/nba/latest/260x190/1643407.png",
    );
  });

  it.each([0, -1, 1.5, NaN, Infinity, "2544", "", null, undefined, {}, [], 2 ** 60])(
    "refuses to build a URL for %p (the CDN would answer any of them with HTTP 200)",
    (bad) => {
      expect(normalizeNbaPlayerId(bad)).toBeNull();
      expect(officialHeadshotUrl(bad, "small")).toBeNull();
      expect(officialSource(bad, "large")).toBeNull();
    },
  );

  it("never lets a path-shaped id steer the URL", () => {
    expect(officialHeadshotUrl("../../etc/passwd", "small")).toBeNull();
    expect(officialHeadshotUrl("2544/../1", "small")).toBeNull();
  });
});

describe("resolution selection", () => {
  it("uses the standard file for list-sized slots and the high-resolution file for portraits", () => {
    for (const size of [26, 28, 30, 32, 38, 56, 64, 84]) expect(headshotTierForSize(size)).toBe("small");
    for (const size of [LARGE_HEADSHOT_MIN_PX, 152, 240]) expect(headshotTierForSize(size)).toBe("large");
  });

  it("gives the large tier a srcset so a 1x screen is not sent a 200 KB file for a 152 px slot", () => {
    const large = officialSource(2544, "large");
    expect(large?.srcSet).toBe(
      "https://cdn.nba.com/headshots/nba/latest/260x190/2544.png 260w, " +
        "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png 1040w",
    );
    expect(officialSource(2544, "small")?.srcSet).toBeUndefined();
  });

  it("picks the tier from the display size inside resolveHeadshot", () => {
    const row = resolveHeadshot({ ...base, size: 30 });
    const portrait = resolveHeadshot({ ...base, size: 152 });
    expect(row).toMatchObject({ state: "image", source: { kind: "official" } });
    expect(row.state === "image" && row.source.src).toContain("/260x190/");
    expect(portrait.state === "image" && portrait.source.src).toContain("/1040x760/");
  });
});

describe("fallback chain", () => {
  it("prefers the official image when it is verified to exist", () => {
    const r = resolveHeadshot(base);
    expect(r.state === "image" && r.source.kind).toBe("official");
  });

  it("waits (requests nothing) while the availability probe is pending", () => {
    expect(resolveHeadshot({ ...base, official: "pending" })).toEqual({ state: "pending" });
  });

  it("falls back to the existing local photo when the official image is unavailable", () => {
    const r = resolveHeadshot({ ...base, official: "unavailable" });
    expect(r).toEqual({ state: "image", source: { kind: "local", src: "/api/v1/assets/players/2544" } });
  });

  it("falls back to the local photo when the official image failed to load", () => {
    const failed = new Set([officialHeadshotUrl(2544, "small") as string]);
    const r = resolveHeadshot({ ...base, failed });
    expect(r.state === "image" && r.source.kind).toBe("local");
  });

  it("ends at the placeholder when the local photo also failed, and never loops", () => {
    const failed = new Set([officialHeadshotUrl(2544, "small") as string, "/api/v1/assets/players/2544"]);
    expect(resolveHeadshot({ ...base, failed })).toEqual({ state: "placeholder" });
  });

  it("is the placeholder when nothing is known for the player", () => {
    expect(resolveHeadshot({ ...base, official: "unavailable", localKnown: false })).toEqual({
      state: "placeholder",
    });
  });

  it("is exactly today's behaviour when the official source is off", () => {
    expect(resolveHeadshot({ ...base, official: "off" })).toEqual({
      state: "image",
      source: { kind: "local", src: "/api/v1/assets/players/2544" },
    });
    expect(resolveHeadshot({ ...base, official: "off", localKnown: false })).toEqual({ state: "placeholder" });
  });

  it("an unmapped id resolves to the placeholder even if a manifest claims a photo", () => {
    for (const bad of [0, null, undefined, NaN]) {
      expect(resolveHeadshot({ ...base, nbaPlayerId: bad })).toEqual({ state: "placeholder" });
    }
  });
});

describe("historical views", () => {
  it("never uses the current official portrait, whatever the probe says", () => {
    for (const official of ["available", "pending", "unavailable", "off"] as const) {
      const r = resolveHeadshot({ ...base, historical: true, official });
      expect(r).toEqual({ state: "image", source: { kind: "local", src: "/api/v1/assets/players/2544" } });
    }
  });

  it("keeps the placeholder for a historical player with no local photo", () => {
    expect(resolveHeadshot({ ...base, historical: true, localKnown: false })).toEqual({ state: "placeholder" });
  });
});

describe("rights gate", () => {
  it("is off outside development: production and test builds never reach the CDN", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(officialHeadshotsEnabled()).toBe(false);
    vi.stubEnv("NODE_ENV", "test");
    expect(officialHeadshotsEnabled()).toBe(false);
  });

  it("is OFF by default in development: nothing is requested unless a developer opts in", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", undefined);
    expect(officialHeadshotsEnabled()).toBe(false);
  });

  it("is on in development only for the exact opt-in value", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", "on");
    expect(officialHeadshotsEnabled()).toBe(true);
  });

  it("stays off in development for every other flag value", () => {
    vi.stubEnv("NODE_ENV", "development");
    for (const value of ["off", "", "ON", "On", "true", "1", "yes", " on", "on "]) {
      vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", value);
      expect(officialHeadshotsEnabled(), JSON.stringify(value)).toBe(false);
    }
  });

  it("cannot be forced on in production or test by the opt-in flag", () => {
    vi.stubEnv("NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS", "on");
    for (const env of ["production", "test"]) {
      vi.stubEnv("NODE_ENV", env);
      expect(officialHeadshotsEnabled(), env).toBe(false);
    }
  });
});
