import { expect, test, type Page } from "@playwright/test";
import { askResponseFixture, contextFixture, sourceFixture, statusFixture } from "../unit/assistant-fixtures";

/**
 * Ask Pivot (/assistant) in a real browser.
 *
 * Which half runs depends on how the frontend under test was started, because the flag is
 * inlined at build / dev-server start:
 *
 *  - without NEXT_PUBLIC_ASSISTANT_ENABLED=true (the default, and CI): the route must be
 *    the app's ordinary not-found page and the sidebar must not mention Ask Pivot;
 *  - with it: every /api/v1/assistant/* call is answered by `page.route` with FICTIONAL
 *    fixtures — no Ollama, no backend assistant — and a two-turn conversation is walked
 *    through, a citation opened and the debug view toggled.
 *
 * The rest of the shell (data-health, teams search) still talks to the suite's demo
 * backend, as every other spec does.
 */

const ENABLED = process.env.NEXT_PUBLIC_ASSISTANT_ENABLED === "true";

test.describe("Ask Pivot without the build flag", () => {
  test.skip(ENABLED, "the frontend was started with NEXT_PUBLIC_ASSISTANT_ENABLED=true");

  test("/assistant is the not-found page and the nav does not offer it", async ({ page }) => {
    // Not a status assertion: the root `app/loading.tsx` puts every page inside a Suspense
    // boundary, so a `notFound()` there streams the not-found UI with HTTP 200 plus a
    // robots noindex tag (Next's documented streaming behaviour; see the "Status Codes"
    // section of next/dist/docs/.../file-conventions/loading.md).
    await page.goto("/assistant");
    await expect(page.getByRole("heading", { name: "There is no page at this address" })).toBeVisible();
    await expect(page.locator('meta[name="robots"][content*="noindex"]').first()).toBeAttached();
    await expect(page).not.toHaveTitle(/Pivot Intelligence/);

    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "Primary" }).first();
    await expect(nav.getByRole("link", { name: "Home" })).toBeVisible();
    await expect(nav.getByRole("link", { name: /Ask Pivot/ })).toHaveCount(0);
    await expect(nav.getByText("Intelligence", { exact: true })).toHaveCount(0);
  });
});

test.describe("Ask Pivot with the build flag", () => {
  test.skip(!ENABLED, "the frontend was started without NEXT_PUBLIC_ASSISTANT_ENABLED=true");

  async function mockAssistant(page: Page) {
    const asked: Record<string, unknown>[] = [];
    await page.route("**/api/v1/assistant/status", (route) => route.fulfill({ json: statusFixture() }));
    await page.route("**/api/v1/assistant/context", (route) => route.fulfill({ json: contextFixture() }));
    await page.route("**/api/v1/assistant/ask", async (route) => {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      asked.push(body);
      const second = asked.length > 1;
      await route.fulfill({
        json: second
          ? askResponseFixture({
              answer: "Against bigger wings the reports are thinner [S11].",
              sources: [
                sourceFixture({
                  source_id: "S11",
                  chunk_id: "S11@v1#1",
                  title: "Wes Calder — wing stopper (2023 evaluation)",
                  author: "Owen Bramwell",
                  report_date: "2023-11-02",
                  excerpt: "Calder switched one through four in the 2023 sample.",
                }),
              ],
              cited_source_ids: ["S11"],
              follow_ups: [],
            })
          : askResponseFixture(),
      });
    });
    return asked;
  }

  test("a two-turn conversation, a citation and the debug view", async ({ page }) => {
    const asked = await mockAssistant(page);
    await page.goto("/assistant");

    await expect(page.getByRole("heading", { name: "Pivot Intelligence", level: 1 })).toBeVisible();
    await expect(page.getByText("FICTIONAL DATA / SANDBOX.")).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Primary" }).first();
    await expect(nav.getByRole("link", { name: /Ask Pivot/ })).toHaveAttribute("aria-current", "page");

    // Turn 1: typed, sent with Enter.
    const composer = page.getByLabel("Ask Pivot a question about the fictional league");
    await expect(composer).toBeEnabled();
    await composer.fill("Who defends the point of attack best?");
    await composer.press("Enter");
    const log = page.getByRole("log");
    await expect(log.getByText("Grounded answer")).toBeVisible();
    await expect(log.getByText("Fictional data").first()).toBeVisible();
    await expect(composer).toBeFocused();

    // A citation opens its passage in the inspector rail.
    await log.getByRole("button", { name: "Source S05: Theo Vance — shooting volume and defensive role" }).click();
    const inspector = page.getByRole("complementary", { name: "Evidence inspector" });
    await expect(inspector.getByText(/Vance takes seven threes a game/)).toBeVisible();

    // Turn 2: a follow-up chip, carrying turn 1 as history.
    await log.getByRole("button", { name: "How does Reed hold up against bigger wings?" }).click();
    await expect(log.getByText(/Against bigger wings the reports are thinner/)).toBeVisible();
    expect(asked).toHaveLength(2);
    expect((asked[1].history as unknown[]).length).toBe(2);

    // Debug: toggled on, it shows each answer's diagnostics.
    const debug = page.getByRole("button", { name: "Debug" });
    await debug.click();
    await expect(debug).toHaveAttribute("aria-pressed", "true");
    await expect(log.getByRole("region", { name: "Debug diagnostics" }).first()).toBeVisible();
    await expect(log.getByText("Tracing was off for this answer — nothing about it was sent to LangSmith.").first()).toBeVisible();

    // No horizontal overflow at phone width.
    await page.setViewportSize({ width: 375, height: 800 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
