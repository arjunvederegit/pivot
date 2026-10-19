import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LegalityAssessment, SalaryAndContracts } from "@/components/trade/financials";
import { StagedPlayerNotice } from "@/components/trade/staged-player";
import { ResearchPreviewPanel } from "@/components/players/skill-evidence";
import { contractLength, contractLengthShort, dollars, signedMillions } from "@/lib/financials";
import type { LegalityAssessment as Assessment, PlayerFinancialsRecord, TradeFinancialAnalysis } from "@/lib/financials";
import type { RosterPlayer, Team } from "@/lib/types";

afterEach(cleanup);

const assessment = (over: Partial<Assessment> = {}): Assessment => ({
  verdict: "indeterminate",
  headline: "Indeterminate: 8 check(s) could not be verified from the data on file; no checked rule fails.",
  statement: "A rule that passes means only that rule passed. Pivot checks a subset of the CBA, so no result here is a statement that the trade is legal.",
  counts: { checked_passed: 4, failed: 0, unverified: 8 },
  indications: [],
  checked: [],
  failed: [],
  unverified: [],
  rules: [],
  unchecked_categories: [
    { category: "Cash considerations", note: "not modelled" },
    { category: "Existing trade exceptions", note: "not modelled" },
  ],
  engine_status: "conditionally_valid",
  ...over,
});

const fin = (over: Partial<PlayerFinancialsRecord> = {}): PlayerFinancialsRecord => ({
  player_id: "p1",
  name: "Star",
  financial_season: "2026-27",
  status: "priced",
  status_reason: "2026-27 cap hit on file",
  cap_hit: 30_000_000,
  base_salary: 30_000_000,
  total_cash: 30_000_000,
  salary_basis: "cap_hit",
  seasons: [],
  contract_end: { status: "ends", final_season: "2029-30", years_remaining: 4, years_remaining_is_lower_bound: false, has_gap: false, basis: "last row 2029-30" },
  contract_type: null,
  not_two_way: true,
  not_two_way_basis: "cap hit above the two-way salary",
  source: { provider: "balldontlie", name: "BALLDONTLIE /contracts/teams", retrieved_at: "2026-10-19T03:13:00" },
  ...over,
});

function analysis(over: Partial<TradeFinancialAnalysis> = {}): TradeFinancialAnalysis {
  const side = (players: string[], total: number | null, unknown: string[] = []) => ({
    players, count: players.length, known_cap_hit_total: total, known_count: players.length - unknown.length,
    unknown_players: unknown, complete: unknown.length === 0, cap_hit_total: unknown.length ? null : total,
    total_cash: null, base_salary_total: null,
  });
  return {
    financial_season: "2026-27",
    season_basis: { financial: "2026-27", roster: "2026-27", roster_is_provisional: true, statistics: "2025-26 regular season", note: "" },
    thresholds: { season: "2026-27", salary_cap: 164_961_000, luxury_tax: 200_428_000, first_apron: 209_015_000, second_apron: 221_686_000, source_name: "NBA" },
    definitions: "Cap hit is the amount counted against the cap.",
    players: [
      { player_id: "p1", name: "Star", from_team_id: "a", to_team_id: "b", financials: fin(), contract_type_stated: null, matching_class: "standard", matching_class_basis: "deduced" },
      { player_id: "p2", name: "Unpriced Guy", from_team_id: "b", to_team_id: "a", financials: fin({ player_id: "p2", name: "Unpriced Guy", status: "unpriced", status_reason: "the provider lists a row with no cap hit", cap_hit: null, base_salary: null, total_cash: null }), contract_type_stated: null, matching_class: null, matching_class_basis: null },
    ],
    teams: {
      a: {
        team_id: "a", abbreviation: "AAA", name: "Alpha", outgoing: side(["Star"], 30_000_000), incoming: side(["Unpriced Guy"], null, ["Unpriced Guy"]),
        net_salary_change: null, incoming_to_outgoing_ratio: null, net_salary_note: "Salary difference not stated: no cap hit is on file for Unpriced Guy. Unknown salaries are not counted as $0.",
        known_subtotal_before: 190_000_000, known_subtotal_after: null,
        coverage_before: { known: 190_000_000, players_known: 15, players_total: 18, complete: false }, coverage_after: null,
        subtotal_label: "Known salary subtotal", apron_status_at_least_after: null, apron_status_after: null,
        commitments_by_season: [
          { season: "2026-27", is_financial_season: true, outgoing: { known_total: 30_000_000, unknown_players: [], complete: true }, incoming: { known_total: 0, unknown_players: ["Unpriced Guy"], complete: false }, net_known: null },
          { season: "2027-28", is_financial_season: false, outgoing: { known_total: 30_000_000, unknown_players: [], complete: true }, incoming: { known_total: 0, unknown_players: ["Unpriced Guy"], complete: false }, net_known: null },
        ],
        matching_eligible: { outgoing: null, incoming: null, note: "" },
      },
    },
    not_on_file: { options: "Player/team option flags are not loaded." },
    legality_assessment: assessment(),
    contract_provider_configured: true,
    ...over,
  };
}

describe("legality assessment never says 'legal'", () => {
  it("shows the unchecked categories on an indeterminate verdict", () => {
    render(<LegalityAssessment analysis={analysis()} />);
    expect(screen.getByText("Indeterminate", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/2 categories not checked at all/)).toBeInTheDocument();
    expect(screen.getByText(/no result here is a statement that the trade is legal/)).toBeInTheDocument();
  });

  it("a pass is worded as passing the rules Pivot checks", () => {
    const a = analysis({ legality_assessment: assessment({ verdict: "passes_checked_rules", headline: "Passes every rule Pivot checks — not a statement that the trade is legal." }) });
    render(<LegalityAssessment analysis={a} />);
    expect(screen.getByText("Passes the rules Pivot checks")).toBeInTheDocument();
    expect(screen.queryByText(/^Legal$/)).toBeNull();
  });

  it("an indication is labelled as not a verified failure", () => {
    const a = analysis({
      legality_assessment: assessment({
        indications: [{ rule_code: "SALARY_MATCHING", title: "Salary matching", team: "AAA", explanation: "Incoming exceeds the limit.", confidence: "medium" }],
      }),
    });
    render(<LegalityAssessment analysis={a} />);
    expect(screen.getByText(/Indication — not a verified failure/)).toBeInTheDocument();
  });

  it("a prohibited verdict names a checked rule", () => {
    const a = analysis({ legality_assessment: assessment({ verdict: "prohibited", headline: "Prohibited under Salary matching (a checked rule fails).", counts: { checked_passed: 1, failed: 1, unverified: 0 } }) });
    render(<LegalityAssessment analysis={a} />);
    expect(screen.getByText("Prohibited under a checked rule")).toBeInTheDocument();
  });
});

describe("salary and contracts — unknown is unknown", () => {
  it("an unpriced player is 'unknown', never $0, and the net change is 'not stated'", () => {
    render(<SalaryAndContracts analysis={analysis()} teamNames={{ a: "AAA", b: "BBB" }} />);
    expect(screen.getByText("unknown", { selector: "span.text-unavail" })).toBeInTheDocument();
    expect(screen.getByTestId("net-AAA").textContent).toContain("not stated");
    expect(screen.getByText(/Unknown salaries are not counted as \$0/)).toBeInTheDocument();
    // the only "$0" on the page is the sentence saying unknowns are NOT counted as $0 — never a figure
    expect(document.body.textContent).not.toContain("$0.0M");
    expect(document.body.textContent).not.toMatch(/\$0(?![.,\d])(?! \()(?!\.)(?!\s*(?:\.|\)))/);
  });

  it("the subtotal carries its label and a floor sign when coverage is partial", () => {
    render(<SalaryAndContracts analysis={analysis()} teamNames={{ a: "AAA" }} />);
    expect(screen.getByText(/Known salary subtotal before → after/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("≥ $190.0M");
    expect(document.body.textContent).toContain("15 of 18 rostered players priced");
  });

  it("names the three seasons it mixes", () => {
    render(<SalaryAndContracts analysis={analysis()} teamNames={{ a: "AAA" }} />);
    expect(document.body.textContent).toContain("Cap hits for the 2026-27 season");
    expect(document.body.textContent).toContain("roster 2026-27 (provisional)");
    expect(document.body.textContent).toContain("2025-26 regular season");
  });

  it("links each traded player to his file with canonical ids only", () => {
    const a = analysis({ players: analysis().players.map((p, i) => ({ ...p, player_id: i === 0 ? "a2ed238d-7cca-48e8-9fcb-e4703c27b421" : "not-a-canonical-id" })) });
    render(<SalaryAndContracts analysis={a} teamNames={{ a: "AAA" }} />);
    const hrefs = [...document.querySelectorAll("a")].map((x) => x.getAttribute("href"));
    expect(hrefs).toContain("/players/a2ed238d-7cca-48e8-9fcb-e4703c27b421?from=trade-evaluator");
    expect(hrefs).toContain("/player-explorer");
  });
});

describe("financial wording helpers", () => {
  it("never renders null as a dollar figure", () => {
    expect(dollars(null)).toBe("—");
    expect(dollars(0)).toBe("$0");
    expect(signedMillions(null)).toBe("not stated");
    expect(signedMillions(28_456_566)).toBe("+$28.5M");
    expect(signedMillions(-2_000_000)).toBe("−$2.0M");
  });

  it("a lower-bound length carries a plus and 'at least'; an unknown length says so", () => {
    const base = fin().contract_end;
    expect(contractLength(base)).toBe("4 seasons · through 2029-30");
    expect(contractLength({ ...base, status: "through_horizon", final_season: "2030-31", years_remaining: 5, years_remaining_is_lower_bound: true })).toBe("5+ seasons · through at least 2030-31");
    expect(contractLength({ ...base, status: "unknown", years_remaining: null, final_season: null })).toBe("length unknown");
    expect(contractLengthShort({ ...base, status: "through_horizon", final_season: "2030-31" })).toBe("thru ≥ 30-31");
    expect(contractLength(null)).toBe("length unknown");
  });
});

describe("staged player notice verifies before it applies", () => {
  const team = (id: string, abbreviation: string): Team => ({ id, abbreviation, full_name: abbreviation } as unknown as Team);
  const row = { player_id: "a2ed238d-7cca-48e8-9fcb-e4703c27b421", name: "Star", salary: 58_000_000, salary_season: "2026-27" } as unknown as RosterPlayer;
  const ctx = { playerId: "a2ed238d-7cca-48e8-9fcb-e4703c27b421", teamId: "t1", statsSeason: "2025-26", from: "player-explorer" as const };

  it("names the season difference when salary and evidence seasons differ", () => {
    render(<StagedPlayerNotice playerId={ctx.playerId} context={ctx} teams={[team("t1", "BOS")]} teamIds={["t1"]} rosterLoaded rosterRow={row} originTeamId="t1" movedTo={null} onDismiss={() => {}} />);
    expect(screen.getByText(/Seasons differ/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("cap hit of $58.0M");
  });

  it("says the context was not applied when the player is not on the roster it loaded", () => {
    render(<StagedPlayerNotice playerId={ctx.playerId} context={ctx} teams={[team("t1", "BOS")]} teamIds={["t1"]} rosterLoaded rosterRow={undefined} originTeamId={null} movedTo={null} onDismiss={() => {}} />);
    expect(screen.getByText(/not applied/)).toBeInTheDocument();
    expect(screen.getByText("not verified")).toBeInTheDocument();
  });

  it("an unpriced carried player is 'no cap hit on file', not $0", () => {
    render(<StagedPlayerNotice playerId={ctx.playerId} context={ctx} teams={[team("t1", "BOS")]} teamIds={["t1"]} rosterLoaded rosterRow={{ ...row, salary: null } as RosterPlayer} originTeamId="t1" movedTo={null} onDismiss={() => {}} />);
    expect(document.body.textContent).toContain("no cap hit on file (unknown, not $0)");
  });
});

describe("research preview is closed by default and fetches experimental data only when opened", () => {
  function wrap(ui: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  }

  it("does not request experimental estimates until the reader opens the panel; then labels every record", async () => {
    const calls: string[] = [];
    const record = (quantity: string, kind: "inferred" | "projected") => ({
      player_id: "p", quantity, unit: "fraction of 1", output_kind: kind, value: 0.36,
      period: { season: "2025-26", season_type: "regular", as_of_season: "2025-26" },
      target_period: kind === "projected" ? { season: "2026-27", season_type: "regular" } : null,
      interval: { low: 0.3, high: 0.4, nominal_level: 0.8, method: "m", calibration: null, kind: kind === "projected" ? "prediction" : "credible", interpretation: "given the player plays at least 500 minutes" },
      sample: null,
      model: { name: "m", version: "cand-x", status: "candidate", evaluation_status: "holdout_confirmed", evaluation_ref: null, methodology: "" },
      provenance: { sources: [], data_snapshot_id: "ds-1", code_commit: "abc" }, assumptions: [], limitations: [], reliability: null, conditioning: null,
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      const body = url.includes("kinds=measured")
        ? { player_id: "p", as_of: "2025-26", context: {}, records: [], reliability_statements: {}, unavailable: [], notices: [] }
        : { player_id: "p", as_of: "2025-26", context: {}, records: [record("fg3_pct", "inferred"), record("fg3_pct", "projected")], reliability_statements: {}, unavailable: [], notices: ["inferred and projected values below come from candidate models: they are experimental, not validated or promoted"] };
      return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }));
    wrap(<ResearchPreviewPanel playerId="p" />);
    await waitFor(() => expect(calls.some((c) => c.includes("kinds=measured"))).toBe(true));
    expect(calls.some((c) => c.includes("include_experimental"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Show experimental estimates/ }));
    await waitFor(() => expect(calls.some((c) => c.includes("include_experimental=true"))).toBe(true));
    await waitFor(() => expect(screen.getAllByText("experimental").length).toBeGreaterThanOrEqual(2));
    // a candidate whose holdout confirmed is STILL experimental, never "validated"
    expect(screen.queryByText("validated")).toBeNull();
    expect(document.body.textContent).toContain("conditional on the player playing a rotation role");
    vi.unstubAllGlobals();
  });

  it("hides itself when the server has the preview route off (404)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "not_found", message: "disabled" } }), { status: 404, headers: { "content-type": "application/json" } })));
    const { container } = wrap(<ResearchPreviewPanel playerId="p" />);
    await waitFor(() => expect(container.querySelector("section")).toBeNull());
    vi.unstubAllGlobals();
  });
});
