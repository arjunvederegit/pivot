import { expect, test } from "@playwright/test";

/**
 * Core Pivot flows against a live local stack with an ingested database
 * (`make sync-data && make train && make score`, plus `make index-assets` /
 * `make import-stats-csv` for full coverage). No NBA.com calls happen here.
 */

test("the home dashboard says what Pivot is and opens the core tools", async ({ page }) => {
  // `/` is the intelligence dashboard: an editorial hero over an illustrated arena, then the
  // working modules. What is worth pinning is that it names the product's purpose, opens
  // the core tool, shows the real workflow, and does not claim a capability Pivot lacks.
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Pivot — home" })).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 1, name: /Sharper decisions\.\s*Better basketball\./ }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the Trade Evaluator" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Start with your roster" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "The decision workflow" })).toBeVisible();

  // Unbuilt modules are named as unbuilt, not implied.
  await expect(page.getByText("Not built yet")).toBeVisible();

  // Every core tool is one click away in the sidebar.
  const nav = page.getByRole("navigation", { name: "Primary" });
  for (const name of ["Scouting", "Teams", "Trade Evaluator", "Strategy Lab", "Cap Center"]) {
    await expect(nav.getByRole("link", { name })).toBeVisible();
  }

  // Scrolling is native: nothing pins the window at the top.
  const moved = await page.evaluate(async () => {
    window.scrollTo(0, 800);
    await new Promise((r) => setTimeout(r, 250));
    return window.scrollY;
  });
  expect(moved).toBeGreaterThan(300);
});

test("overview presents the platform with honest data status", async ({ page }) => {
  // `/command-center` is the home dashboard's former address and still serves it.
  await page.goto("/command-center");
  await expect(page.getByRole("link", { name: "Pivot — home" })).toBeVisible();
  // The Command Center's old address serves the home dashboard.
  await expect(
    page.getByRole("heading", { level: 1, name: /Sharper decisions\.\s*Better basketball\./ }),
  ).toBeVisible();
  // primary and secondary calls to action
  await expect(page.getByRole("link", { name: "Start with your roster" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the Trade Evaluator" })).toBeVisible();
  // the launcher reaches every active module in one click
  await expect(page.getByRole("heading", { name: "The decision workflow" })).toBeVisible();
  // roadmap items are described, never presented as usable
  await expect(page.getByText("Not built yet")).toBeVisible();
  await expect(page.getByText("Scenario Engine")).toBeVisible();
});

test("renamed modules keep old links working", async ({ page }) => {
  for (const [oldPath, heading] of [
    ["/trade-machine", "Trade Evaluator"],
    ["/compare", "Decision board"],
    ["/player-lab", "Player Explorer"],
  ] as const) {
    await page.goto(oldPath);
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible({
      timeout: 20_000,
    });
  }
});

test("data health reports sources and never hides a missing one", async ({ page }) => {
  await page.goto("/data-health");
  await expect(page.getByText("Current NBA data").first()).toBeVisible({ timeout: 20_000 });
  // contracts are not imported by default — that must be stated, not glossed over
  await expect(page.getByText(/not imported|not configured|unavailable/i).first()).toBeVisible();
});

test("player explorer lists imported season totals", async ({ page }) => {
  await page.goto("/player-explorer");
  await expect(page.getByRole("heading", { name: /Player Explorer/i, level: 1 })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText(/season totals/i).first()).toBeVisible({ timeout: 20_000 });
});

/**
 * The Strategy Lab is a *comparison* board: with fewer than two saved deals it renders
 * an empty state by design, so the flow below needs a second deal to exist. Creating it
 * through the API is deliberate test setup — the UI path is what the flow itself
 * exercises. Without this the test only passed because a developer database happened to
 * carry deals left over from earlier runs (the pollution R1-7 removes).
 */
async function seedComparisonDeal(request: import("@playwright/test").APIRequestContext) {
  const api = "http://localhost:8000/api/v1";
  const teams = await (await request.get(`${api}/teams`)).json();
  const [teamA, teamB] = teams.slice(0, 2);
  const roster = await (await request.get(`${api}/teams/${teamA.id}/roster`)).json();
  const player = roster.roster[0];
  await request.post(`${api}/trades`, {
    data: {
      name: "E2E comparison baseline",
      team_ids: [teamA.id, teamB.id],
      player_moves: [
        { player_id: player.player_id, from_team_id: teamA.id, to_team_id: teamB.id },
      ],
      pick_moves: [],
    },
  });
}

test("full flow: team outlook → strategy → trade evaluator → rules → evaluate → save → compare", async ({
  page,
  request,
}) => {
  await seedComparisonDeal(request);

  // 1. Team Outlook: open a team
  await page.goto("/team-outlook");
  await page.getByRole("link", { name: /Celtics/ }).first().click();
  await expect(page.getByText(/team strategy/i).first()).toBeVisible({ timeout: 25_000 });

  // 2. Save a strategy
  await page.getByRole("button", { name: /Save strategy/i }).click();
  await expect(page.getByText(/Strategy saved/i).first()).toBeVisible({ timeout: 15_000 });

  // 3. Trade Evaluator, carrying the team through (exact name: "Trade Evaluator"
  //    in the nav also matches a loose /Trade/ pattern)
  await page.getByRole("link", { name: "Start a trade", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Trade Evaluator", level: 1 })).toBeVisible({
    timeout: 20_000,
  });

  // 4. Add a second team, then move a player with the accessible control
  await page.getByRole("button", { name: /Add team/i }).first().click();
  await page.getByRole("button", { name: /Atlanta Hawks|Hawks/ }).first().click();
  await page.getByRole("button", { name: /Send .* to/ }).first().click({ timeout: 20_000 });

  // 5. Live rules check reports one of the four honest states
  await expect(
    page
      .getByText(/Passes rules check|Fails rules check|Incomplete check|Not checked/)
      .first(),
  ).toBeVisible({ timeout: 25_000 });

  // 6. Evaluate and read the verdict.
  //
  //    Two honest outcomes are possible and which one appears depends on the database:
  //    a fan verdict, or an explicit refusal when the deal fails a verified rule (a
  //    counterparty already carrying 18 players cannot receive a 19th). Both are
  //    correct; a *decision score on an illegal deal* is not, which is what the next
  //    assertion pins.
  await page.getByRole("button", { name: /Evaluate this deal/i }).click();
  await expect(
    page
      .getByText(
        /Clear win|Roughly neutral|Net negative|Clear loss|Cannot fully evaluate|No decision score/,
      )
      .first(),
  ).toBeVisible({ timeout: 40_000 });

  const refused = await page.getByText("No decision score").first().isVisible();
  if (refused) {
    // The refusal must name the rule that caused it, not just withhold the number.
    await expect(page.getByText(/ROSTER_SIZE|SALARY_MATCHING|STEPIEN|APRON/).first()).toBeVisible();
  }

  // 7. Save the deal → full report
  await page.getByLabel(/Deal name/i).fill("E2E Pivot deal");
  await page.getByRole("button", { name: /^Save deal$/i }).click();
  await expect(page.getByRole("heading", { name: "E2E Pivot deal" })).toBeVisible({
    timeout: 40_000,
  });

  // 8. Strategy Lab lists it
  await page.goto("/strategy-lab");
  await expect(page.getByText("E2E Pivot deal").first()).toBeVisible({ timeout: 20_000 });
});
