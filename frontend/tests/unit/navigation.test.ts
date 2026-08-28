/**
 * Navigation.
 *
 * Before Pivot, nothing tested this: no test rendered any file under `app/`, and no test
 * asserted the contents of the nav arrays. A navigation restructure could therefore point a
 * primary destination at a route that does not exist and every check would stay green until
 * a human clicked it.
 *
 * These tests read the route manifest off disk rather than mocking it, so a nav entry and
 * the App Router directory it names cannot drift apart.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const FRONTEND = join(__dirname, "..", "..");
const APP = join(FRONTEND, "app");
const SHELL = readFileSync(join(FRONTEND, "components", "shell.tsx"), "utf8");
const NEXT_CONFIG = readFileSync(join(FRONTEND, "next.config.ts"), "utf8");

/** Pull the `href` values out of one nav array in shell.tsx. */
function hrefsIn(arrayName: string): string[] {
  const block = SHELL.split(`const ${arrayName}: NavLink[] = [`)[1];
  if (block === undefined) throw new Error(`${arrayName} not found in shell.tsx`);
  const body = block.split("];")[0];
  return [...body.matchAll(/href:\s*"([^"]+)"/g)].map((m) => m[1]);
}

/** Route paths the App Router actually serves, from the directory tree. */
function routeSegments(): string[] {
  return readdirSync(APP, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith("[") && !e.name.startsWith("_"))
    .map((e) => e.name);
}

describe("the nav points only at routes that exist", () => {
  const nav = [...hrefsIn("PRIMARY"), ...hrefsIn("GM_LAB"), ...hrefsIn("SECONDARY")];

  it("finds every nav array", () => {
    expect(hrefsIn("PRIMARY").length).toBeGreaterThan(0);
    expect(hrefsIn("GM_LAB").length).toBeGreaterThan(0);
    expect(hrefsIn("SECONDARY").length).toBeGreaterThan(0);
  });

  it.each(nav)("%s is a real App Router route", (href) => {
    if (href === "/") {
      expect(existsSync(join(APP, "page.tsx"))).toBe(true);
      return;
    }
    const segment = href.replace(/^\//, "").split("/")[0];
    expect(routeSegments()).toContain(segment);
    expect(existsSync(join(APP, segment, "page.tsx"))).toBe(true);
  });

  it("lists no route twice", () => {
    expect(new Set(nav).size).toBe(nav.length);
  });

  it("leads with the Command Center", () => {
    // `/` is the narrative homepage while the scroll experiment is on this branch, so the
    // Command Center has its own route. What is being asserted is unchanged: the product
    // surface a returning user wants is the first thing in the bar.
    expect(hrefsIn("PRIMARY")[0]).toBe("/command-center");
  });
});

describe("the nav follows the decision workflow", () => {
  /**
   * The order is the product's argument. The previous bar led with the Trade Evaluator,
   * which put the last step of the workflow first and made Pivot read as a calculator
   * collection. Observe (Players, Teams) comes before act (GM Lab).
   */
  it("puts the observe steps before the act steps", () => {
    const primary = hrefsIn("PRIMARY");
    expect(primary).toEqual(["/command-center", "/player-explorer", "/team-outlook"]);
  });

  it("groups the GM Lab modules rather than promoting one of them", () => {
    const lab = hrefsIn("GM_LAB");
    expect(lab).toContain("/trade-evaluator");
    expect(lab).toContain("/strategy-lab");
    expect(lab.length).toBeGreaterThan(1);
  });

  it("keeps the explanation surfaces reachable", () => {
    const secondary = hrefsIn("SECONDARY");
    expect(secondary).toContain("/methodology");
    expect(secondary).toContain("/data-health");
    expect(secondary).toContain("/about");
  });
});

describe("legacy URLs keep resolving", () => {
  /**
   * Routes were renamed once already, and every previously published URL still redirects.
   * The Pivot restructure changed navigation LABELS and grouping but deliberately no paths,
   * so this table must stay intact — a shared Trade Evaluator link carries a `?state=`
   * query string that Next forwards through the redirect.
   */
  const legacy = [
    "/trade-machine",
    "/trade-builder",
    "/compare",
    "/player-lab",
    "/team-hub",
    "/decision-room",
    "/cap-lab",
    "/data-status",
  ];

  it.each(legacy)("%s still has a redirect", (source) => {
    expect(NEXT_CONFIG).toContain(`source: "${source}"`);
  });

  it("redirects to routes that exist", () => {
    const destinations = [...NEXT_CONFIG.matchAll(/destination:\s*"([^"]+)"/g)]
      .map((m) => m[1])
      .filter((d) => !d.startsWith("http"))
      .map((d) => d.replace(/^\//, "").split("/")[0])
      .filter((segment) => segment.length > 0 && !segment.startsWith(":"));

    for (const segment of new Set(destinations)) {
      if (segment === "api") continue;
      expect(routeSegments()).toContain(segment);
    }
  });
});

describe("the product is named consistently", () => {
  /**
   * The repository previously shipped three different taglines simultaneously — "Basketball
   * Decision Intelligence" in the browser title, "NBA Front Office Simulator" in the OpenAPI
   * title, and a third in the wordmark. One name, one tagline, defined once and imported.
   */
  const BRAND = readFileSync(join(FRONTEND, "components", "brand.tsx"), "utf8");
  const LAYOUT = readFileSync(join(FRONTEND, "app", "layout.tsx"), "utf8");

  it("defines the name and tagline once, as exports", () => {
    expect(BRAND).toMatch(/export const PRODUCT_NAME = "Pivot"/);
    expect(BRAND).toMatch(/export const PRODUCT_TAGLINE = "Basketball Intelligence for Better Decisions"/);
  });

  it("uses the name in the browser title", () => {
    expect(LAYOUT).toContain("Pivot");
  });

  it("no longer renders the old wordmark in the shell", () => {
    expect(SHELL).not.toMatch(/ROSTER<span/);
    expect(BRAND).not.toMatch(/ROSTER<span/);
  });
});
