import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SeasonBasisNotice, SeasonContextLine } from "@/components/season-context";
import type { SeasonBasis, SeasonContext } from "@/lib/types";

afterEach(cleanup);

const context = (over: Partial<SeasonContext["roster"]> = {}): SeasonContext => ({
  roster: {
    season: "2026-27",
    status: "provisional_preseason",
    provisional: true,
    fell_back: false,
    snapshot_retrieved_at: "2026-10-18T12:00:00Z",
    ...over,
  },
  performance: {
    season: "2025-26",
    label: "2025-26 regular season",
    is_baseline: true,
    behind_roster_season: true,
  },
  contract: { season: "2026-27", state: "missing" },
  notes: [],
});

describe("SeasonContextLine (R8.2)", () => {
  it("names the roster season and the statistics season separately", () => {
    render(<SeasonContextLine context={context()} />);
    const line = screen.getByTestId("season-context");
    expect(line.textContent).toContain("2026-27 roster");
    expect(line.textContent).toContain("2025-26 regular season");
    expect(line.textContent).toContain("latest completed");
    expect(line.textContent).toContain("provisional");
  });

  it("says when the roster is last season's because the new one is not ingested", () => {
    render(<SeasonContextLine context={context({ fell_back: true })} />);
    expect(screen.getByTestId("season-context").textContent).toContain("new one not ingested");
  });

  it("claims nothing when the server sent no context and no seasons", () => {
    const { container } = render(<SeasonContextLine />);
    expect(container.firstChild).toBeNull();
  });

  it("does not call a roster 'latest completed' when both seasons agree", () => {
    const ctx = context({ season: "2025-26", provisional: false });
    render(<SeasonContextLine context={ctx} statisticsSeason="2025-26" />);
    expect(screen.getByTestId("season-context").textContent).not.toContain("provisional");
  });
});

describe("SeasonBasisNotice (R8.2)", () => {
  const basis = (limitations: SeasonBasis["limitations"]): SeasonBasis => ({
    roster_season: "2026-27",
    performance_season: "2025-26",
    team_needs_season: "2025-26",
    contract_season: "2026-27",
    roster_differs_from_performance: limitations.length > 0,
    limitations,
  });

  it("states the limitation in the server's words", () => {
    render(
      <SeasonBasisNotice
        basis={basis([
          { component: "fit", reason: "team_needs_describe_previous_team", message: "Needs were measured on the 2025-26 team." },
        ])}
      />,
    );
    expect(screen.getByTestId("season-basis-notice").textContent).toContain("Needs were measured on the 2025-26 team.");
  });

  it("names a player who is on no current roster even when the seasons agree", () => {
    render(
      <SeasonBasisNotice
        basis={basis([])}
        warnings={[{ player_id: "p1", message: "Left The League is on no 2026-27 roster in Pivot's snapshot." }]}
      />,
    );
    expect(screen.getByTestId("season-basis-notice").textContent).toContain("Left The League is on no 2026-27 roster");
  });

  it("renders nothing when there is nothing to disclose", () => {
    const { container } = render(<SeasonBasisNotice basis={basis([])} />);
    expect(container.firstChild).toBeNull();
    const none = render(<SeasonBasisNotice />);
    expect(none.container.firstChild).toBeNull();
  });
});
