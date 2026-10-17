// Visual QA harness: screenshots every required route at every required viewport,
// reports console errors, horizontal overflow and empty pages, and **exits non-zero**
// when it finds any of them, so it can be used as a gate.
//
// Usage (from anywhere in the repo):
//   node scripts/visual_qa.mjs [outDir] [routeFilter]
//   make visual-qa OUT=docs/qa/run
// Requires the local stack running (frontend :3000, backend :8000).

import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "fs";

// ESM resolves bare specifiers from *this file's* directory upward, and there is no
// node_modules at the repo root — only under frontend/. Resolve from there explicitly
// rather than depending on the caller's working directory.
const requireFromFrontend = createRequire(
  new URL("../frontend/package.json", import.meta.url),
);
const { chromium } = requireFromFrontend("@playwright/test");

const OUT = decodeURIComponent(
  new URL(`../${process.argv[2] ?? "docs/qa/current"}/`, import.meta.url).pathname,
);
const FILTER = process.argv[3] ?? "";
mkdirSync(OUT, { recursive: true });

const VIEWPORTS = [
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1366x768", width: 1366, height: 768 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1024x768", width: 1024, height: 768 },
  { name: "768x1024", width: 768, height: 1024 },
  { name: "390x844", width: 390, height: 844 },
];

const base = "http://localhost:3000";
const api = "http://localhost:8000/api/v1";

const teams = await (await fetch(`${api}/teams`)).json();
const team = teams.find((t) => t.abbreviation === "BOS") ?? teams[0];
const trades = await (await fetch(`${api}/trades`)).json();
const players = await (await fetch(`${api}/players?limit=1`)).json();
const player = players.players?.[0];

const ROUTES = [
  { name: "overview", path: "/" },
  // The home dashboard's former address; it must keep rendering the dashboard.
  { name: "command-center", path: "/command-center" },
  { name: "trade-empty", path: "/trade-evaluator" },
  { name: "trade-populated", path: `/trade-evaluator?team=${team.id}` },
  { name: "strategy-lab", path: "/strategy-lab" },
  { name: "player-explorer", path: "/player-explorer" },
  { name: "team-outlook-index", path: "/team-outlook" },
  { name: "team-outlook", path: `/team-outlook/${team.id}` },
  { name: "salary-cap-center", path: `/salary-cap-center?team=${team.id}` },
  { name: "methodology", path: "/methodology" },
  { name: "data-health", path: "/data-health" },
  // R7: `/about` is in the global nav and was not in this list, so a route a user can
  // reach in one click had never been screenshotted or checked for console errors.
  { name: "about", path: "/about" },
  ...(player ? [{ name: "player-detail", path: `/players/${player.id}` }] : []),
  // Error paths are part of the product surface: an unknown id must render a designed
  // not-found state, not an unstyled crash.
  { name: "player-invalid", path: "/players/not-a-real-player-id", expectNotFound: true },
  { name: "team-invalid", path: "/team-outlook/not-a-real-team-id", expectNotFound: true },
  ...(trades.length
    ? [{ name: "trade-result", path: `/trades/${trades[0].id}` }]
    : []),
].filter((r) => !FILTER || r.name.includes(FILTER));

const browser = await chromium.launch();
const report = [];

for (const viewport of VIEWPORTS) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text().slice(0, 200));
  });
  page.on("pageerror", (err) => errors.push(`pageerror: ${String(err).slice(0, 200)}`));

  for (const route of ROUTES) {
    errors.length = 0;
    try {
      await page.goto(base + route.path, { waitUntil: "networkidle", timeout: 30000 });
    } catch {
      await page.goto(base + route.path, { waitUntil: "domcontentloaded", timeout: 30000 });
    }
    await page.waitForTimeout(1400);

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));

    // Logos and photos load lazily, so a full-page capture taken from the top shows empty
    // plates for everything below the fold. Walk the page a screen at a time first, the
    // way a reader would, then return to the top for the capture.
    await page.evaluate(async () => {
      const step = Math.max(200, window.innerHeight - 120);
      for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await new Promise((r) => setTimeout(r, 120));
      }
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(600);

    await page.screenshot({
      path: `${OUT}${route.name}__${viewport.name}.png`,
      fullPage: true,
    });

    // A designed not-found state must actually render something; a blank body means
    // the route crashed rather than refusing politely.
    const bodyChars = await page.evaluate(
      () => (document.body?.innerText ?? "").trim().length,
    );

    // On a deliberately invalid id the browser logs the 404 fetch itself. That is the
    // app behaving correctly, so it is not a problem — an *unhandled* error still is.
    const reported = route.expectNotFound
      ? errors.filter((e) => !/Failed to load resource.*404/i.test(e))
      : [...errors];

    report.push({
      route: route.name,
      viewport: viewport.name,
      horizontalOverflowPx: Math.max(0, overflow.scrollWidth - overflow.clientWidth),
      consoleErrors: reported,
      emptyBody: bodyChars < 40,
    });
  }
  await context.close();
}

await browser.close();

const problems = report.filter(
  (r) =>
    r.horizontalOverflowPx > 0 ||
    r.consoleErrors.length > 0 ||
    r.emptyBody,
);
writeFileSync(`${OUT}report.json`, JSON.stringify({ report, problems }, null, 2));
console.log(`captured ${report.length} screenshots into ${OUT}`);
if (problems.length === 0) {
  console.log(
    "CLEAN: no horizontal overflow, no console errors, no empty pages",
  );
} else {
  console.log(`PROBLEMS (${problems.length}):`);
  for (const p of problems) {
    console.log(
      `  ${p.route} @ ${p.viewport}: overflow=${p.horizontalOverflowPx}px` +
        ` errors=${p.consoleErrors.length ? p.consoleErrors[0] : 0}` +
        `${p.emptyBody ? " EMPTY-BODY" : ""}`,
    );
  }
}

// The harness is a gate, not a log. Without this it can never fail a build, and wiring
// it into CI would produce a vacuous check.
process.exit(problems.length === 0 ? 0 : 1);
