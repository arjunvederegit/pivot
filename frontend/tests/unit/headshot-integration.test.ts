import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import nextConfig from "../../next.config";

const ROOT = path.resolve(__dirname, "../..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) return sources(full);
    return /\.(ts|tsx)$/.test(entry) ? [full] : [];
  });
}
const rel = (f: string) => path.relative(ROOT, f);
const app = [...sources(path.join(ROOT, "app")), ...sources(path.join(ROOT, "components")), ...sources(path.join(ROOT, "lib"))];

describe("Next.js image configuration", () => {
  it("does not route the NBA CDN through the Next image optimizer", () => {
    // The optimizer would fetch NBA bytes server-side, re-encode and re-serve them from Pivot's own
    // origin. That is redistribution, which the rights constraint forbids until cleared, so the CDN
    // is deliberately absent from images.remotePatterns / domains and portraits are plain <img>.
    const images = (nextConfig as { images?: { remotePatterns?: unknown[]; domains?: string[] } }).images;
    const blob = JSON.stringify(images ?? {});
    expect(blob).not.toMatch(/nba\.com/);
    expect(images?.domains ?? []).toEqual([]);
    expect(images?.remotePatterns ?? []).toEqual([]);
  });

  it("keeps the /api/v1 backend proxy and adds no rewrite or redirect for the CDN", async () => {
    const rewrites = (await nextConfig.rewrites!()) as { source: string }[];
    expect(rewrites.map((r) => r.source)).toContain("/api/v1/:path*");
    expect(JSON.stringify(rewrites)).not.toMatch(/nba\.com/);
  });
});

describe("one place builds headshot URLs", () => {
  it("only lib/headshots.ts contains the CDN URL pattern", () => {
    const offenders = app.filter((f) => /https:\/\/cdn\.nba\.com/.test(readFileSync(f, "utf8"))).map(rel);
    expect(offenders).toEqual(["lib/headshots.ts"]);
  });

  it("no component builds a player photo URL or renders a player <img> itself", () => {
    for (const f of app) {
      if (rel(f) === "components/media.tsx" || rel(f) === "lib/teamIdentity.ts" || rel(f) === "lib/headshots.ts") continue;
      const text = readFileSync(f, "utf8");
      expect(text, rel(f)).not.toMatch(/assets\/players/);
      expect(text, rel(f)).not.toMatch(/playerPhotoSrc/);
    }
  });
});

describe("every existing player-image consumer still goes through PlayerPhoto", () => {
  const consumers = [
    "app/player-explorer/page.tsx",
    "app/salary-cap-center/page.tsx",
    "components/command/team-context.tsx",
    "components/players/compare.tsx",
    "components/players/directory.tsx",
    "components/players/file-header.tsx",
    "components/players/library.tsx",
    "components/strategy/parts.tsx",
    "components/strategy/selection.tsx",
    "components/teams/roster-depth.tsx",
    "components/trade/desk.tsx",
    "components/trade/drawer.tsx",
    "components/trade/overview.tsx",
    "components/trade/sheet.tsx",
    "components/trade/workspace.tsx",
  ];

  it.each(consumers)("%s passes an NBA id and imports the shared component", (file) => {
    const text = readFileSync(path.join(ROOT, file), "utf8");
    expect(text).toMatch(/import \{[^}]*PlayerPhoto[^}]*\} from "@\/components\/media"/);
    expect(text).toMatch(/nbaPlayerId=\{/);
    // The id handed over is an NBA id, never Pivot's internal uuid.
    for (const m of text.matchAll(/nbaPlayerId=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/g)) {
      expect(m[1], `${file}: ${m[0]}`).not.toMatch(/(^|[^_a-z])player_id\b(?!\])|\.id\b/);
    }
  });

  it("covers every file that renders PlayerPhoto (a new consumer must be added to this list)", () => {
    const found = app
      .filter((f) => /<PlayerPhoto/.test(readFileSync(f, "utf8")))
      .map(rel)
      .filter((f) => f !== "components/media.tsx")
      .sort();
    expect(found).toEqual([...consumers].sort());
  });

  it("the retired-player profile is marked historical; list views are not", () => {
    const header = readFileSync(path.join(ROOT, "components/players/file-header.tsx"), "utf8");
    expect(header).toMatch(/historical=\{!player\.is_active\}/);
    for (const f of consumers.filter((c) => c !== "components/players/file-header.tsx")) {
      expect(readFileSync(path.join(ROOT, f), "utf8"), f).not.toMatch(/historical/);
    }
  });
});
