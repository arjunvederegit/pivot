/**
 * W2.6A — the selected team survives re-evaluation.
 *
 * The page keeps the selection in `useSelectedTeam` (above the conditional result section) and
 * resolves what to show with `resolveActiveTeam`; both are exercised here as shipped, next to the
 * production `useTradeAnalysis` and the real `lib/api.ts` client (only `fetch` is stubbed, every
 * request a deferred promise). `EvaluationSection` itself is not exported from the Next page file,
 * so `Result` below stands in for it in exactly one respect that matters: it is mounted only while
 * an evaluation exists and holds no selection state of its own. The page's own wiring of these
 * hooks is verified by source inspection, not here.
 *
 * Fixtures: the backend-generated captures in tests/fixtures/trade-evaluate-responses.json.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useLayoutEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradeEvaluationRequest, TradeEvaluationResult } from "@/lib/tradeEvaluate";
import { resolveActiveTeam, useSelectedTeam } from "@/lib/useSelectedTeam";
import { useTradeAnalysis } from "@/lib/useTradeAnalysis";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const FIXTURES = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/trade-evaluate-responses.json"), "utf8"),
) as Record<string, { body: Json }>;
const body = (name: "scored" | "partial" | "insufficient_data"): Json => FIXTURES[name].body;
/** The two teams of the captured deal; the evaluations are keyed by them. */
const [TEAM_A, TEAM_B] = Object.keys(body("scored").evaluations);

interface Draft {
  teams: string[];
  moves: { player_id: string; from_team_id: string; to_team_id: string }[];
}
const DEAL: Draft = { teams: [TEAM_A, TEAM_B], moves: [{ player_id: "p1", from_team_id: TEAM_A, to_team_id: TEAM_B }] };
const request = (d: Draft): TradeEvaluationRequest => ({
  team_ids: d.teams,
  player_moves: d.moves,
  pick_moves: [],
  scenario_id: null,
  strategy: "custom",
});

/* ---------------------------------------------------------------- fetch stub */

interface Call {
  resolve(payload: unknown): void;
  reject(error: unknown): void;
}
let calls: Call[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((res, rej) => {
          calls.push({
            resolve: (payload) =>
              res(new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } })),
            reject: rej,
          });
        }),
    ),
  );
});

/* ------------------------------------------------------------------- harness */

interface Commit {
  evaluation: TradeEvaluationResult | null;
  selectedTeam: string | null;
  active: string | null;
}
let commits: Commit[] = [];
let errors: unknown[] = [];
let handle: { select(teamId: string): void; evaluate(): void; selectedTeam: string | null };

/** What EvaluationSection is to the page: mounted only while there is an evaluation, no selection state inside. */
function Result({ active }: { active: string }) {
  return <div data-testid="result" data-active={active} />;
}

function Harness({ draft, draftKey = null }: { draft: Draft; draftKey?: string | null }) {
  const analysis = useTradeAnalysis({
    draftKey,
    evaluationRequest: request(draft),
    validationRequest: null,
    onEvaluationError: (e) => errors.push(e),
  });
  const [selectedTeam, select] = useSelectedTeam(draft.teams, draftKey);
  const active = analysis.evaluation ? resolveActiveTeam(selectedTeam, analysis.evaluation.evaluations, draft.teams) : null;
  useLayoutEffect(() => {
    handle = { select, evaluate: () => analysis.evaluate(), selectedTeam };
    commits.push({ evaluation: analysis.evaluation, selectedTeam, active });
  });
  return analysis.evaluation && active ? <Result active={active} /> : null;
}

let client: QueryClient;
let view: ReturnType<typeof render>;
const tree = (draft: Draft, draftKey: string | null = null) => (
  <QueryClientProvider client={client}>
    <Harness draft={draft} draftKey={draftKey} />
  </QueryClientProvider>
);
const mount = (draft: Draft = DEAL, draftKey: string | null = null) => void (view = render(tree(draft, draftKey)));
const setDraft = (draft: Draft, draftKey: string | null = null) => act(() => view.rerender(tree(draft, draftKey)));

beforeEach(() => {
  commits = [];
  errors = [];
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.unstubAllGlobals();
});

const evaluate = () => act(async () => handle.evaluate());
const select = (teamId: string) => act(() => handle.select(teamId));
const settle = (fn: () => void) => act(async () => fn());
/** Evaluate, then answer the request with a fixture. */
async function evaluateAndAnswer(name: "scored" | "partial" | "insufficient_data" = "scored") {
  await evaluate();
  await settle(() => calls.at(-1)!.resolve(body(name)));
}
const shownTeam = () => screen.queryByTestId("result")?.getAttribute("data-active") ?? null;

/* ------------------------------------------------------------------- tests */

describe("resolveActiveTeam", () => {
  const evaluations = { a: {}, b: {} };
  it("shows the first team when nothing is selected", () => expect(resolveActiveTeam(null, evaluations, ["a", "b"])).toBe("a"));
  it("shows the selected team when the evaluation has it", () => expect(resolveActiveTeam("b", evaluations, ["a", "b"])).toBe("b"));
  it("falls back to the first team when the evaluation lacks the selected one", () =>
    expect(resolveActiveTeam("c", evaluations, ["a", "b"])).toBe("a"));
});

describe("the selected team survives re-evaluation", () => {
  it("keeps the second team through a pending re-evaluation and shows it again with the new result", async () => {
    mount();
    await evaluateAndAnswer("scored");
    expect(shownTeam()).toBe(TEAM_A); // nothing chosen yet: the first team
    await select(TEAM_B);
    expect(shownTeam()).toBe(TEAM_B);

    const before = commits.length;
    await evaluate(); // a new evaluation of unchanged inputs
    expect(calls).toHaveLength(2);
    // The old analysis is gone while the request is pending, and the choice is still remembered.
    expect(screen.queryByTestId("result")).toBeNull();
    for (const c of commits.slice(before)) {
      expect(c.evaluation).toBeNull();
      expect(c.selectedTeam).toBe(TEAM_B);
    }
    expect(handle.selectedTeam).toBe(TEAM_B);

    await settle(() => calls[1].resolve(body("partial")));
    expect(JSON.stringify(commits.at(-1)!.evaluation)).toBe(JSON.stringify(body("partial")));
    expect(shownTeam()).toBe(TEAM_B);
  });

  it("keeps the remembered team through an edit that clears the result, then evaluates for it", async () => {
    mount();
    await evaluateAndAnswer();
    await select(TEAM_B);
    await setDraft({ ...DEAL, moves: [...DEAL.moves, { player_id: "p2", from_team_id: TEAM_A, to_team_id: TEAM_B }] });
    expect(screen.queryByTestId("result")).toBeNull();
    expect(handle.selectedTeam).toBe(TEAM_B);
    await evaluateAndAnswer("partial");
    expect(shownTeam()).toBe(TEAM_B);
  });
});

describe("the selection is a team identity, not a position", () => {
  it("keeps a selected first team when the teams are reordered", async () => {
    mount();
    await evaluateAndAnswer();
    await select(TEAM_A); // an explicit choice of the team that is first today
    await setDraft({ teams: [TEAM_B, TEAM_A], moves: DEAL.moves });
    expect(handle.selectedTeam).toBe(TEAM_A);
    await evaluateAndAnswer("partial");
    expect(shownTeam()).toBe(TEAM_A); // by position it would now be TEAM_B
  });

  it("falls back to the first team when the selected team leaves the deal, and does not bring it back", async () => {
    mount();
    await evaluateAndAnswer();
    await select(TEAM_B);
    await setDraft({ teams: [TEAM_A], moves: [] }); // TEAM_B removed
    expect(handle.selectedTeam).toBeNull();
    expect(screen.queryByTestId("result")).toBeNull();
    await setDraft(DEAL); // TEAM_B added back
    expect(handle.selectedTeam).toBeNull(); // the old choice did not wait around
    await evaluateAndAnswer("partial");
    expect(shownTeam()).toBe(TEAM_A);
  });

  it("falls back to the first team for a response that lacks the remembered team, and remembers the choice", async () => {
    mount();
    await evaluateAndAnswer();
    await select(TEAM_B);
    const lacking = structuredClone(body("partial"));
    delete lacking.evaluations[TEAM_B];
    await evaluate();
    await settle(() => calls[1].resolve(lacking));
    expect(shownTeam()).toBe(TEAM_A);
    expect(handle.selectedTeam).toBe(TEAM_B);
    await evaluateAndAnswer("insufficient_data");
    expect(shownTeam()).toBe(TEAM_B);
  });

  it("forgets the selection when a different saved trade becomes the draft", async () => {
    mount(DEAL, null);
    await evaluateAndAnswer();
    await select(TEAM_B);
    await setDraft(DEAL, "trade-1"); // identical inputs, adopted from a saved trade
    expect(handle.selectedTeam).toBeNull();
    await evaluateAndAnswer("partial");
    expect(shownTeam()).toBe(TEAM_A);
  });
});

describe("a failed re-evaluation, then a retry", () => {
  it("never shows the old analysis, keeps the selection, and shows the retry's result for that team", async () => {
    mount();
    await evaluateAndAnswer("scored");
    await select(TEAM_B);

    const before = commits.length;
    await evaluate();
    await settle(() => calls[1].reject(new TypeError("Failed to fetch")));
    expect(errors).toHaveLength(1);
    expect(screen.queryByTestId("result")).toBeNull();
    expect(handle.selectedTeam).toBe(TEAM_B);

    await evaluate(); // retry
    await settle(() => calls[2].resolve(body("partial")));
    expect(shownTeam()).toBe(TEAM_B);
    expect(JSON.stringify(commits.at(-1)!.evaluation)).toBe(JSON.stringify(body("partial")));
    // From the failed request onward, no committed render showed the first evaluation again.
    for (const c of commits.slice(before)) {
      expect(c.evaluation === null || JSON.stringify(c.evaluation) === JSON.stringify(body("partial"))).toBe(true);
    }
  });
});

describe("control: the previous structure forgets the choice", () => {
  /** The old shape, for contrast: selection state INSIDE the conditionally mounted result. */
  function LocalStateResult({ teams }: { teams: string[] }) {
    const [active, setActive] = useState(teams[0]);
    return <button data-testid="local" onClick={() => setActive(teams[1])}>{active}</button>;
  }
  it("loses the choice when the result unmounts and mounts again", () => {
    const teams = [TEAM_A, TEAM_B];
    const first = render(<LocalStateResult teams={teams} />);
    act(() => screen.getByTestId("local").click());
    expect(screen.getByTestId("local").textContent).toBe(TEAM_B);
    first.rerender(<></>); // evaluation cleared
    first.rerender(<LocalStateResult teams={teams} />); // evaluation arrives
    expect(screen.getByTestId("local").textContent).toBe(TEAM_A);
  });
});
