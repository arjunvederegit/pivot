import { render, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssistantAnswer } from "@/components/assistant/answer";
import { ClaimBreakdown } from "@/components/assistant/claim-breakdown";
import { DebugPanel } from "@/components/assistant/debug-panel";
import { EvidenceClassTag } from "@/components/assistant/inspector";
import { claimKindLabel, evidenceClassLabel } from "@/lib/assistant-format";
import { askResponseSchema } from "@/lib/assistant-schemas";
import type { AskRequest, AskResponse, AssistantClaim } from "@/lib/assistant-types";
import { askResponseFixture } from "./assistant-fixtures";

function renderAnswer(response: AskResponse, extra: { request?: AskRequest; debug?: boolean } = {}) {
  return render(
    <AssistantAnswer
      response={response}
      turnNumber={1}
      sources={response.sources}
      onOpenSource={() => {}}
      onShowSources={() => {}}
      onFollowUp={() => {}}
      followUpsDisabled={false}
      debug={extra.debug ?? false}
      selectedSourceId={null}
      dimensionLabel={(key) => key.replace(/_/g, " ")}
      shownInInspector={false}
      request={extra.request}
    />,
  );
}

const MARKER =
  "⟨Removed by Ask Pivot's checks: it stated a trade-legality verdict; the sandbox has no contract data or CBA engine.⟩";

function claim(overrides: Partial<AssistantClaim>): AssistantClaim {
  return { text: "", kind: "inference", section: null, source_ids: [], check: "not_numeric", note: null, ...overrides };
}

// Vitest globals are off, so RTL does not auto-clean: each test scopes queries to its container.

describe("kinds of statement", () => {
  it("labels the four kinds a GM must keep apart", () => {
    expect(claimKindLabel("observed_fact")).toBe("Observed facts");
    expect(claimKindLabel("analyst_opinion")).toBe("Analyst views");
    expect(claimKindLabel("inference")).toBe("Inference");
    expect(claimKindLabel("unavailable")).toBe("Not available");
    expect(evidenceClassLabel("untrusted_claim")).toBe("Untrusted claim");
    expect(evidenceClassLabel(undefined)).toBeNull();
  });

  it("renders counts per kind and every sentence with its check glyph", () => {
    const { container, unmount } = render(
      <ClaimBreakdown
        claims={[
          { text: "Theo Vance shoots 40.4% from three [S04].", kind: "observed_fact", section: null, source_ids: ["S04"], check: "matches_manifest", note: null },
          { text: "Theo Vance shoots 10.4% from three [S04].", kind: "observed_fact", section: null, source_ids: ["S04"], check: "contradicts_manifest", note: "A number here does not match the fact manifest (see Checks)." },
          { text: "My read: Vance fits your spacing rule.", kind: "inference", section: null, source_ids: [], check: "not_numeric", note: null },
        ]}
        summary={{ observed_fact: 2, analyst_opinion: 0, inference: 1, unavailable: 0, untrusted_claim: 0, question: 0 }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("How this answer breaks down");
    expect(text).toContain("Observed facts2");
    expect(text).toContain("Inference1");
    expect(text).not.toContain("Analyst views0"); // kinds with no sentences are not listed
    expect(container.querySelector('[title="The number matches the fact sheet"]')).not.toBeNull();
    expect(container.querySelector('[title="A number here contradicts the fact sheet"]')).not.toBeNull();
    unmount();
  });

  it("renders nothing for an answer with no classified sentences", () => {
    const { container, unmount } = render(<ClaimBreakdown claims={[]} summary={null} />);
    expect(container.textContent).toBe("");
    unmount();
  });

  it("shows a source's kind of evidence", () => {
    const { container, unmount } = render(<EvidenceClassTag evidenceClass="observed_statistic" />);
    expect(container.textContent).toBe("Observed statistics");
    unmount();
  });

  it("accepts responses with and without the breakdown, and a malformed breakdown never loses the answer", () => {
    expect(askResponseSchema.safeParse(askResponseFixture()).success).toBe(true);
    const older: Record<string, unknown> = { ...askResponseFixture() };
    delete older.claims;
    delete older.claim_summary;
    expect(askResponseSchema.safeParse(older).success).toBe(true);
    const malformed = { ...askResponseFixture(), claims: "not a list", claim_summary: 7 };
    const parsed = askResponseSchema.safeParse(malformed);
    expect(parsed.success).toBe(true);
  });
});

describe("guard markers in the claim breakdown", () => {
  const fact = claim({
    text: "Theo Vance shoots 40.4% from three [S04].",
    kind: "observed_fact",
    source_ids: ["S04"],
    check: "matches_manifest",
  });

  it("shows a marker the backend classified as a 'removed' row, not as 'Not available', and does not count it", () => {
    // The shape a backend that still classifies markers sends (epistemics guessed 'unavailable').
    const { container, unmount } = render(
      <ClaimBreakdown
        claims={[fact, claim({ text: MARKER, kind: "unavailable" })]}
        summary={{ observed_fact: 1, analyst_opinion: 0, inference: 0, unavailable: 1, untrusted_claim: 0, question: 0 }}
      />,
    );
    const kinds = within(container).getByRole("list", { name: "Kinds of statement in this answer" });
    expect(kinds.textContent).toContain("Observed facts1");
    expect(kinds.textContent).not.toContain("Not available");
    const text = container.textContent ?? "";
    expect(text).not.toContain("⟨");
    expect(text).toContain("Removed by checks");
    expect(text).toContain("it stated a trade-legality verdict; the sandbox has no contract data or CBA engine.");
    expect(text).not.toMatch(/Not available.*trade-legality/);
    unmount();
  });

  it("counts from the claims themselves when there is no summary, still without the marker", () => {
    const { container, unmount } = render(
      <ClaimBreakdown claims={[fact, claim({ text: MARKER, kind: "inference" })]} summary={null} />,
    );
    const kinds = within(container).getByRole("list", { name: "Kinds of statement in this answer" });
    expect(kinds.textContent).toBe("Observed facts1");
    unmount();
  });

  it("renders a marker glued onto a sentence as the removed note, never as raw text", () => {
    const { container, unmount } = render(
      <ClaimBreakdown
        claims={[claim({ ...fact, text: `${fact.text} ${MARKER}` })]}
        summary={{ observed_fact: 1, analyst_opinion: 0, inference: 0, unavailable: 0, untrusted_claim: 0, question: 0 }}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toContain("⟨");
    expect(text).toContain("Theo Vance shoots 40.4% from three [S04].");
    expect(text).toContain("Removed by Ask Pivot’s checks: it stated a trade-legality verdict");
    unmount();
  });

  it("renders unchanged once the backend stops classifying markers, and nothing when only markers remain", () => {
    const { container, unmount } = render(
      <ClaimBreakdown
        claims={[fact]}
        summary={{ observed_fact: 1, analyst_opinion: 0, inference: 0, unavailable: 0, untrusted_claim: 0, question: 0 }}
      />,
    );
    expect(container.textContent).toContain("Observed facts1");
    unmount();
    const onlyMarkers = render(<ClaimBreakdown claims={[claim({ text: MARKER, kind: "unavailable" })]} summary={null} />);
    expect(onlyMarkers.container.textContent).toBe("");
    onlyMarkers.unmount();
  });
});

describe("the debug panel", () => {
  const timings = [
    { stage: "query_context", ms: 1.5 },
    { stage: "retrieval", ms: 12 },
    { stage: "model_call", ms: 14_000 },
    { stage: "retrieval.dense", ms: 8 },
    { stage: "retrieval.lexical", ms: 3 },
    { stage: "final_response", ms: 2 },
    { stage: "total", ms: 14_020 },
  ];
  const withTimings = (rows: { stage: string; ms: number }[]) =>
    askResponseFixture({ diagnostics: { ...askResponseFixture().diagnostics!, timings: rows } });

  it("states the backend's own total, not a sum of every row (sub-stages and total included)", () => {
    const { container, unmount } = render(<DebugPanel response={withTimings(timings)} />);
    expect(container.textContent).toContain("Stage timings · 14.02 s total");
    unmount();
  });

  it("without a total row, adds only the top-level stages", () => {
    const { container, unmount } = render(
      <DebugPanel response={withTimings(timings.filter((t) => t.stage !== "total"))} />,
    );
    // 1.5 + 12 + 14000 + 2 — the retrieval.* rows are already inside `retrieval`.
    expect(container.textContent).toContain("Stage timings · 14.02 s total");
    expect(container.textContent).not.toContain("14.03 s total");
    unmount();
  });

  it("makes every scrolling box reachable by keyboard: candidates, query text, raw answer", () => {
    const response = askResponseFixture({
      diagnostics: { ...askResponseFixture().diagnostics!, raw_answer: "The original answer." },
    });
    const { container, unmount } = render(<DebugPanel response={response} />);
    for (const name of [
      "Retrieval candidates, scrollable",
      "Query text sent to the retrievers, scrollable",
      "The model’s original answer, scrollable",
    ]) {
      expect(within(container).getByRole("region", { name })).toHaveAttribute("tabindex", "0");
    }
    unmount();
  });
});

describe("the answer card", () => {
  const roster = (priorities: Record<string, string>, priorityChanges: AskResponse["context_used"]["priority_changes"] = []) =>
    askResponseFixture({
      context_used: {
        ...askResponseFixture().context_used,
        mode: "roster_fit",
        priorities,
        priority_changes: priorityChanges,
      },
    });
  const request = (priorities: AskRequest["priorities"]): AskRequest => ({
    question: "Who fits?",
    mode: "roster_fit",
    team_id: "COMETS",
    priorities,
    previous_priorities: null,
    constraints: [],
    history: [],
  });

  it("labels priorities the GM did not set as the team's documented defaults", () => {
    const defaults = { spacing: "must_preserve", point_of_attack_defense: "high" };
    const changes = [
      { key: "spacing" as const, label: "Spacing", before: "must_preserve" as const, after: null },
      { key: "point_of_attack_defense" as const, label: "Point-of-attack defense", before: "high" as const, after: null },
    ];
    const { container, unmount } = renderAnswer(roster(defaults, changes), { request: request({}) });
    const text = container.textContent ?? "";
    expect(text).toContain("Priorities used — the Hartford Comets’ documented defaults (you set none)");
    expect(text).not.toContain("Your priorities");
    // Not "Spacing removed" while the same card lists Spacing as in force.
    expect(text).toContain("you cleared yours, so the Hartford Comets’ documented defaults were used instead.");
    expect(text).not.toContain("Spacing removed");
    unmount();
  });

  it("keeps 'Your priorities' when the GM sent them, and outside roster fit", () => {
    const set = renderAnswer(roster({ spacing: "high" }), { request: request({ spacing: "high" }) });
    expect(set.container.textContent).toContain("Your priorities");
    set.unmount();
    const research = renderAnswer(askResponseFixture(), { request: { ...request({}), mode: "research" } });
    expect(research.container.textContent).toContain("Your priorities");
    research.unmount();
  });

  it("renders repeated statements, constraints, limitations and follow-ups without key collisions", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const response = askResponseFixture({
      limitations: ["No playoff data.", "No playoff data."],
      follow_ups: ["Why?", "Why?"],
      context_used: {
        ...askResponseFixture().context_used,
        constraints: ["Keep Niko Reed", "Keep Niko Reed"],
        gm_statements: ["Why?", "Why?"],
      },
    });
    const { container, rerender, unmount } = renderAnswer(response);
    rerender(
      <AssistantAnswer
        response={response}
        turnNumber={1}
        sources={response.sources}
        onOpenSource={() => {}}
        onShowSources={() => {}}
        onFollowUp={() => {}}
        followUpsDisabled
        debug
        selectedSourceId="S03"
        dimensionLabel={(key) => key}
        shownInInspector
      />,
    );
    expect(error.mock.calls.filter((args) => String(args[0]).includes("same key"))).toEqual([]);
    expect(within(container).getAllByText("Keep Niko Reed")).toHaveLength(2);
    expect(within(container).getAllByRole("button", { name: "Why?" })).toHaveLength(2);
    error.mockRestore();
    unmount();
  });

  it("labels an unknown_entity request like its siblings", () => {
    const { container, unmount } = renderAnswer(
      askResponseFixture({
        answer_kind: "unsupported_request",
        unsupported_requests: [
          { kind: "unknown_entity", message: "'Zebulon Quackenbush' is not a player or team in the sandbox." },
        ],
      }),
    );
    expect(container.textContent).toContain("Unknown player or team (not in the sandbox).");
    expect(container.textContent).not.toContain("unknown entity.");
    unmount();
  });
});
