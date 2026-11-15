/**
 * W2.7 — a failed live rules check is shown, and belongs to the request that failed.
 *
 * What runs for real: the production hook (`useTradeAnalysis`), the real `lib/api.ts` client, and the
 * production rules-check component (`RulesCheck` in components/trade/desk.tsx), wired the way the
 * Trade Evaluator page wires them (`validation`, `validating`, `validationError`, `hasMoves`). Only
 * `fetch` is stubbed; every stubbed request is a deferred promise the test settles by hand, timers are
 * fake, and nothing reaches a network. The page itself is not mounted (see the handoff's limitations).
 *
 * The consumer records, in a LAYOUT effect (so only for renders React commits), what the hook exposes
 * and whether the failure headline is in the document. That is "what a frame could have shown" in jsdom,
 * not a browser paint test.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { StrictMode, useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RulesCheck } from "@/components/trade/desk";
import { LEGALITY_LABEL } from "@/lib/format";
import type { LegalityResponse, LegalityStatus, PickMove, PlayerMove } from "@/lib/types";
import { VALIDATION_DEBOUNCE_MS, useTradeAnalysis } from "@/lib/useTradeAnalysis";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const HEADLINE = "Live rules check could not be completed.";
const FALLBACK = "The request to the rules engine did not complete.";
const DOMAIN_MESSAGE = "Player p1 is not on the roster of team T1.";
const DOMAIN_422 = { error: { code: "invalid_trade", message: DOMAIN_MESSAGE, request_id: "req-1" } };

/* ------------------------------------------------------------------------ drafts */

interface Draft {
  label: string;
  key?: string | null;
  teams: string[];
  moves: PlayerMove[];
  picks: PickMove[];
  scenario: string | null;
  strategy: string;
}
const mv = (player: string, to = "T2"): PlayerMove => ({ player_id: player, from_team_id: "T1", to_team_id: to });
const DRAFT_A: Draft = { label: "A", teams: ["T1", "T2"], moves: [mv("p1")], picks: [], scenario: null, strategy: "custom" };
const draft = (label: string, change: Partial<Draft>): Draft => ({ ...DRAFT_A, label, ...change });
const DRAFT_B = draft("B", { moves: [mv("p1"), mv("p2")] });
const eligible = (d: Draft) => d.teams.length >= 2 && d.moves.length > 0;

const legality = (year: string, status: LegalityStatus = "verified_legal"): LegalityResponse => ({
  league_year: year,
  overall_status: status,
  teams: {},
  rule_results: [],
  cap_parameters_source: "synthetic test value",
  contract_provider_configured: true,
});

/* -------------------------------------------------------------------- fetch stub */

interface Call {
  path: string;
  body: Json;
  resolve(payload: unknown, status?: number): void;
  reject(error: unknown): void;
}
let calls: Call[] = [];
const callsTo = (path: string) => calls.filter((c) => c.path === path);
const evalCalls = () => callsTo("/trades/evaluate");
const valCalls = () => callsTo("/trades/validate");

function installFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string, init?: RequestInit) =>
        new Promise<Response>((res, rej) => {
          calls.push({
            path: String(url).replace("/api/v1", ""),
            body: JSON.parse(String(init?.body ?? "null")),
            resolve: (payload, status = 200) =>
              res(
                new Response(JSON.stringify(payload), {
                  status,
                  statusText: status === 200 ? "OK" : "Error",
                  headers: { "content-type": "application/json" },
                }),
              ),
            reject: rej,
          });
        }),
    ),
  );
}

const flushQueryNotifications = () => vi.advanceTimersByTimeAsync(0);
async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await flushQueryNotifications();
  });
}
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
const pastDebounce = () => advance(VALIDATION_DEBOUNCE_MS);

/* ------------------------------------------------------------------ the consumer */

interface Commit {
  label: string;
  validation: LegalityResponse | null;
  validating: boolean;
  validationError: string | null | undefined;
  failureShown: boolean;
}
type Analysis = ReturnType<typeof useTradeAnalysis>;

let commits: Commit[] = [];
let analysisRef: { current: Analysis };
let evaluationErrors: unknown[] = [];

// The verdict heading keeps a no-break space before its dash; compare with ordinary spaces.
const text = () => (document.body.textContent ?? "").replace(/\u00a0/g, " ");
const failureShown = () => text().includes(HEADLINE);

function Harness({ draft: d, unrelated = 0 }: { draft: Draft; unrelated?: number }) {
  const analysis = useTradeAnalysis({
    draftKey: d.key ?? null,
    evaluationRequest: {
      team_ids: d.teams,
      player_moves: d.moves,
      pick_moves: d.picks,
      scenario_id: d.scenario,
      strategy: d.strategy,
    },
    validationRequest: eligible(d) ? { team_ids: d.teams, player_moves: d.moves, pick_moves: d.picks } : null,
    onEvaluationError: (e) => evaluationErrors.push(e),
  });
  useLayoutEffect(() => {
    analysisRef.current = analysis;
    commits.push({
      label: d.label,
      validation: analysis.validation,
      validating: analysis.validating,
      validationError: analysis.validationError,
      failureShown: failureShown(),
    });
  });
  return (
    <div data-unrelated={unrelated}>
      <RulesCheck
        validation={analysis.validation}
        validating={analysis.validating}
        validationError={analysis.validationError}
        hasMoves={d.moves.length > 0}
      />
    </div>
  );
}

let queryClient: QueryClient;
let view: ReturnType<typeof render>;
let strictMode = false;
const tree = (d: Draft, unrelated = 0) => {
  const element = (
    <QueryClientProvider client={queryClient}>
      <Harness draft={d} unrelated={unrelated} />
    </QueryClientProvider>
  );
  return strictMode ? <StrictMode>{element}</StrictMode> : element;
};
function mount(d: Draft, strict = false) {
  strictMode = strict;
  view = render(tree(d));
}
function setDraft(d: Draft, unrelated = 0) {
  const element = tree(d, unrelated);
  act(() => view.rerender(element));
}
const current = () => analysisRef.current;
const failWith = (n: number, error: unknown = new TypeError("Failed to fetch")) => settle(() => valCalls()[n].reject(error));
const rejectWith422 = (n: number) => settle(() => valCalls()[n].resolve(DOMAIN_422, 422));

beforeEach(() => {
  vi.useFakeTimers();
  strictMode = false;
  installFetch();
  commits = [];
  evaluationErrors = [];
  analysisRef = { current: undefined as unknown as Analysis };
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Nothing on the screen claims the deal is legal, illegal, or incomplete. */
const noVerdict = () => {
  for (const label of Object.values(LEGALITY_LABEL)) expect(text()).not.toContain(label);
  expect(text()).not.toContain("Rule by rule");
};

/* ====================================================== 1. a current failure is shown */

describe("a current failure of the rules check", () => {
  it("shows a structured 422's own message, ends pending, and states no verdict", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    expect(valCalls()).toHaveLength(1);
    expect(current().validating).toBe(true);
    expect(text()).toContain("Checking"); // the existing pending presentation
    expect(failureShown()).toBe(false);

    await rejectWith422(0);

    expect(current().validating).toBe(false);
    expect(current().validation).toBeNull();
    expect(text()).not.toContain("Checking");
    expect(text()).toContain(HEADLINE);
    expect(text()).toContain(DOMAIN_MESSAGE);
    expect(text()).not.toContain("No check to run yet"); // no idle explanation beside the failure
    noVerdict();
    expect(evaluationErrors).toEqual([]); // not the evaluation's error toast
    expect(valCalls()).toHaveLength(1);
  });

  it("falls back to a short stable sentence for a network failure, with no retry and no toast", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await failWith(0, new TypeError("Failed to fetch"));

    expect(text()).toContain(HEADLINE);
    expect(text()).toContain(FALLBACK);
    expect(text()).not.toContain("TypeError");
    expect(text()).not.toContain("Failed to fetch");
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
    noVerdict();
    await advance(60_000);
    expect(valCalls()).toHaveLength(1); // no automatic resubmission
    expect(evaluationErrors).toEqual([]);
  });

  it("shows the client's status line when the server sends no structured message", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await settle(() => valCalls()[0].resolve({}, 500));
    expect(text()).toContain(HEADLINE);
    expect(text()).toContain("500 Error"); // the existing client's own wording for an unstructured failure
  });

  it("does not render an unusable (object-valued) message as text", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await settle(() => valCalls()[0].resolve({ error: { code: "x", message: { nested: true } } }, 422));
    expect(text()).toContain(FALLBACK);
    expect(text()).not.toContain("[object Object]");
  });
});

describe("successful checks (controls)", () => {
  it.each(["verified_legal", "verified_illegal", "conditionally_valid", "not_evaluated"] as const)(
    "still render %s with its established label and no failure message",
    async (status) => {
      mount(DRAFT_A);
      await pastDebounce();
      await settle(() => valCalls()[0].resolve(legality("2025-26", status)));
      expect(text()).toContain(LEGALITY_LABEL[status]);
      expect(text()).toContain("Cap year 2025-26");
      expect(text()).not.toContain(HEADLINE);
      expect(current().validating).toBe(false);
    },
  );

  it("renders a failure only when there is one: the idle and pending states are unchanged", () => {
    mount(draft("none", { moves: [] }));
    expect(text()).toContain("No check to run yet");
    expect(failureShown()).toBe(false);
  });
});

/* ====================== 2. the failure belongs to its context: clearing and recovery */

describe("a failure leaves with the context it belonged to", () => {
  it("is gone from the first committed render of a relevant edit, and a later success renders normally", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await rejectWith422(0);
    expect(failureShown()).toBe(true); // the positive control: it was visible

    const before = commits.length;
    setDraft(DRAFT_B);
    const after = commits.slice(before);
    expect(after.length).toBeGreaterThan(0);
    for (const c of after) {
      expect(c.validationError).toBeNull(); // not even a committed render pairs B's inputs with A's error
      expect(c.failureShown).toBe(false);
    }
    expect(failureShown()).toBe(false); // and it stays gone during B's debounce
    await advance(VALIDATION_DEBOUNCE_MS - 1);
    expect(failureShown()).toBe(false);

    await advance(1);
    expect(valCalls()).toHaveLength(2);
    expect(text()).toContain("Checking");
    expect(failureShown()).toBe(false);
    await settle(() => valCalls()[1].resolve(legality("B")));
    expect(text()).toContain(LEGALITY_LABEL.verified_legal);
    expect(text()).toContain("Cap year B");
    expect(failureShown()).toBe(false);
  });

  it("is cleared when the draft becomes ineligible, which then shows the ordinary empty state", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await failWith(0);
    expect(failureShown()).toBe(true);
    const before = commits.length;
    setDraft(draft("none", { moves: [] }));
    for (const c of commits.slice(before)) {
      expect(c.validationError).toBeNull();
      expect(c.failureShown).toBe(false);
    }
    expect(text()).toContain("No check to run yet");
    await advance(5_000);
    expect(valCalls()).toHaveLength(1);
  });

  it("is cleared by a saved-draft identity change even when the request fields are identical", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await failWith(0);
    expect(failureShown()).toBe(true);
    const before = commits.length;
    setDraft(draft("A, saved", { key: "trade-1" }));
    for (const c of commits.slice(before)) {
      expect(c.validationError).toBeNull();
      expect(c.failureShown).toBe(false);
    }
    await pastDebounce();
    expect(valCalls()).toHaveLength(2); // a new context is checked afresh
    await settle(() => valCalls()[1].resolve(legality("saved")));
    expect(text()).toContain("Cap year saved");
    expect(failureShown()).toBe(false);
  });

  it("does not revive an obsolete completion when the draft goes A -> B -> A", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // v0 for the first A
    setDraft(DRAFT_B);
    await pastDebounce(); // v1 for B
    setDraft(draft("A again", {}));
    await pastDebounce(); // v2 for the second A, pending
    expect(valCalls()).toHaveLength(3);
    expect(current().validating).toBe(true);

    await failWith(0); // the first A's late failure
    expect(failureShown()).toBe(false);
    expect(current().validating).toBe(true);
    await settle(() => valCalls()[1].resolve(legality("B"))); // B's late success
    expect(text()).not.toContain("Cap year B");
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(true);

    await settle(() => valCalls()[2].resolve(legality("second A")));
    expect(text()).toContain("Cap year second A");
  });
});

/* ========================================================= 3. obsolete completions */

describe("an obsolete completion", () => {
  it("rejecting late cannot replace the current success with an error", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // v0
    setDraft(DRAFT_B);
    await pastDebounce(); // v1
    await settle(() => valCalls()[1].resolve(legality("B")));
    expect(text()).toContain("Cap year B"); // precondition: B's result is on the screen

    await rejectWith422(0);
    expect(text()).toContain("Cap year B");
    expect(failureShown()).toBe(false);
    expect(current().validationError).toBeNull();
    expect(current().validating).toBe(false);
  });

  it("rejecting late cannot inject an error or end loading while the current request is pending", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // v0
    setDraft(DRAFT_B);
    await pastDebounce(); // v1 pending
    expect(current().validating).toBe(true);
    await rejectWith422(0);
    expect(failureShown()).toBe(false);
    expect(current().validationError).toBeNull();
    expect(current().validating).toBe(true);
    expect(text()).toContain("Checking");
    await settle(() => valCalls()[1].resolve(legality("B")));
    expect(text()).toContain("Cap year B");
  });

  it("succeeding late cannot erase the current failure", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // v0 in flight
    setDraft(DRAFT_B);
    await pastDebounce(); // v1
    await rejectWith422(1);
    expect(text()).toContain(DOMAIN_MESSAGE); // precondition: B's failure is visible before A settles
    expect(current().validating).toBe(false);

    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(text()).toContain(HEADLINE);
    expect(text()).toContain(DOMAIN_MESSAGE);
    expect(text()).not.toContain("Cap year A");
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
  });

  it("failing late cannot replace the current failure's message either", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    setDraft(DRAFT_B);
    await pastDebounce();
    await rejectWith422(1);
    await failWith(0, new TypeError("down"));
    expect(text()).toContain(DOMAIN_MESSAGE);
    expect(text()).not.toContain(FALLBACK);
  });
});

/* ============================================================ 4. channel independence */

describe("the failure survives changes that are not the rules check's inputs", () => {
  it("keeps it, and sends no new request, for a scenario-only or strategy-only change", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await rejectWith422(0);
    const before = commits.length;
    setDraft(draft("scenario", { scenario: "scn-1" }));
    setDraft(draft("strategy", { scenario: "scn-1", strategy: "win_now" }));
    for (const c of commits.slice(before)) {
      expect(c.validationError).toBe(DOMAIN_MESSAGE);
      expect(c.failureShown).toBe(true);
    }
    await advance(5_000);
    expect(valCalls()).toHaveLength(1);
    expect(text()).toContain(DOMAIN_MESSAGE);
  });

  it("keeps it for an equivalent re-render and an unrelated render, with no retry", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await failWith(0);
    setDraft(structuredClone(DRAFT_A), 1);
    setDraft(structuredClone(DRAFT_A), 2);
    expect(text()).toContain(FALLBACK);
    await advance(5_000);
    expect(valCalls()).toHaveLength(1);
  });

  it("is neither erased nor taken over by an evaluation starting, succeeding, failing, or being cleared", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await rejectWith422(0);

    await act(async () => {
      current().evaluate();
      await flushQueryNotifications();
    });
    expect(evalCalls()).toHaveLength(1);
    expect(text()).toContain(DOMAIN_MESSAGE); // evaluation start
    await settle(() => evalCalls()[0].resolve({ error: { code: "x", message: "evaluation said no" } }, 422));
    expect(evaluationErrors).toHaveLength(1); // the evaluation's own failure still goes to its own mechanism
    expect(text()).toContain(DOMAIN_MESSAGE);
    expect(text()).not.toContain("evaluation said no"); // and never into the rules-check region

    act(() => current().clearEvaluation()); // evaluation-only clearing
    expect(text()).toContain(DOMAIN_MESSAGE);
    await advance(5_000);
    expect(valCalls()).toHaveLength(1);
    expect(current().validationError).toBe(DOMAIN_MESSAGE);
  });
});

/* ===================================================== 5. lifecycle and fast completion */

describe("lifecycle", () => {
  it("publishes nothing after the component has unmounted", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const committedBefore = commits.length;
    view.unmount();
    await failWith(0);
    expect(commits).toHaveLength(committedBefore);
    expect(error).not.toHaveBeenCalled();
    expect(evaluationErrors).toEqual([]);
  });

  it("shows a current failure under StrictMode, from one request", async () => {
    mount(DRAFT_A, true);
    await pastDebounce();
    expect(valCalls()).toHaveLength(1);
    await rejectWith422(0);
    expect(text()).toContain(DOMAIN_MESSAGE);
    expect(current().validating).toBe(false);
  });

  it("keeps a failure that settles in the same batch as its own start", async () => {
    mount(DRAFT_A);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(VALIDATION_DEBOUNCE_MS); // the start is dispatched here ...
      valCalls()[0].reject(new TypeError("Failed to fetch")); // ... and the failure arrives before anything is committed
      await flushQueryNotifications();
    });
    expect(valCalls()).toHaveLength(1);
    expect(current().validating).toBe(false);
    expect(current().validationError).toBe(FALLBACK);
    expect(text()).toContain(HEADLINE);
    expect(text()).toContain(FALLBACK);
  });
});

/* ============================================================== 6. page wiring (source) */

describe("the Trade Evaluator page's wiring (source assertions; the page is not mounted)", () => {
  const page = readFileSync(resolve(__dirname, "../../app/trade-evaluator/page.tsx"), "utf8");

  it("takes the hook's validationError and hands it to the rules-check component it renders", () => {
    expect(page).toMatch(/\bvalidationError,\s*\}\s*=\s*useTradeAnalysis\(/);
    expect(page).toMatch(/<RulesCheck[^>]*validationError=\{validationError\}/);
    expect(page).toContain('import { DealLedger, RulesCheck, RunIt } from "@/components/trade/desk";');
  });
});
