/**
 * W2.6B — who may run a current evaluation's success callback or error notification.
 *
 * The reducer in `useTradeAnalysis` decides what is displayed. The callback and the notification
 * are side effects, decided separately, and must belong to the request that is current for the
 * committed screen even when that request completes BEFORE its own `start` update has been committed.
 * (The older code asked a mirror of committed state, which could still hold the previous request id,
 * so the reducer accepted the result while the callback or toast was silently dropped.)
 *
 * Real: the production `useTradeAnalysis`, `evaluateTrade`, `lib/api.ts` and React Query's mutation.
 * Stubbed: `fetch` only, as deferred promises the test settles by hand (fixtures from
 * tests/fixtures/trade-evaluate-responses.json; validation bodies are small synthetic objects).
 *
 * The completion checkpoint. TanStack Query 5.101.3 (`Mutation.execute`, src/mutation.ts) awaits
 * `this.options.onSuccess` (L246) or `this.options.onError` (L288) — the hook's production handlers —
 * and only then `mutationCache.config.onSettled` (L254 / L300). The `MutationCache` below is owned by
 * the test, so its `onSettled` is a checkpoint that runs strictly AFTER the production handler on both
 * paths, whether or not that handler invoked the callback. Waiting on it can therefore never hang on
 * the very behaviour under test. At the checkpoint the test records (a) whether any committed render
 * already showed `evaluating === true` (the request's `start` is committed) and (b) how many
 * callbacks/notifications the handler had produced.
 *
 * "Batched" means `evaluate()` is called inside one `act` scope, where React cannot commit its
 * updates until the scope ends. That is a controlled way to make completion precede the commit of
 * `start`; nothing here asserts that a real click must or must not commit first.
 */

import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradeEvaluationRequest, TradeEvaluationResult } from "@/lib/tradeEvaluate";
import type { LegalityResponse } from "@/lib/types";
import { VALIDATION_DEBOUNCE_MS, useTradeAnalysis } from "@/lib/useTradeAnalysis";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const FIXTURES = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/trade-evaluate-responses.json"), "utf8"),
) as Record<string, { body: Json }>;
const SCORED = FIXTURES.scored.body;
const PARTIAL = FIXTURES.partial.body;
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

const mv = (id: string) => ({ player_id: id, from_team_id: "T1", to_team_id: "T2" });
interface Draft {
  label: string;
  moves: ReturnType<typeof mv>[];
}
const A: Draft = { label: "A", moves: [mv("p1")] };
const B: Draft = { label: "B", moves: [mv("p1"), mv("p2")] };

/* -------------------------------------------------------------- fetch stub */

interface Call {
  path: string;
  settle(status: number, payload: unknown): void;
  reject(error: unknown): void;
}
let calls: Call[] = [];
let waiters: (() => boolean)[] = [];
const callsTo = (path: string) => calls.filter((c) => c.path === path);
/** Resolves once the n-th request to `path` has been made. */
function nthCall(path: string, n: number): Promise<Call> {
  return new Promise((resolveCall) => {
    const ready = () => callsTo(path).length >= n;
    if (ready()) return resolveCall(callsTo(path)[n - 1]);
    waiters.push(() => {
      if (!ready()) return false;
      resolveCall(callsTo(path)[n - 1]);
      return true;
    });
  });
}
function installFetch() {
  calls = [];
  waiters = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string) =>
        new Promise<Response>((res, rej) => {
          calls.push({
            path: String(url).replace("/api/v1", ""),
            settle: (status, payload) =>
              res(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } })),
            reject: rej,
          });
          waiters = waiters.filter((wake) => !wake());
        }),
    ),
  );
}

/* ---------------------------------------------------------------- the probe */

interface Commit {
  label: string;
  evaluating: boolean;
  evaluation: TradeEvaluationResult | null;
}
interface Checkpoint {
  /** Some committed render already showed this evaluation pending. */
  startCommitted: boolean;
  successCallbacks: number;
  notifications: number;
}
let commits: Commit[] = [];
let checkpoints: Checkpoint[] = [];
let settledWaiters: (() => void)[] = [];
let analysis: ReturnType<typeof useTradeAnalysis>;
let notifications: unknown[] = [];
let successCalls: Record<string, TradeEvaluationResult[]> = {};

/** The test-owned checkpoint: React Query awaits the hook's production handler first. */
const mutationCache = () =>
  new MutationCache({
    onSettled: () => {
      checkpoints.push({
        startCommitted: commits.some((c) => c.evaluating),
        successCallbacks: Object.values(successCalls).reduce((n, l) => n + l.length, 0),
        notifications: notifications.length,
      });
      settledWaiters.splice(0).forEach((w) => w());
    },
  });
const nextSettled = () => new Promise<void>((r) => settledWaiters.push(r));

function Probe({ draft, validate = false }: { draft: Draft; validate?: boolean }) {
  const request: TradeEvaluationRequest = {
    team_ids: ["T1", "T2"],
    player_moves: draft.moves,
    pick_moves: [],
    scenario_id: null,
    strategy: "custom",
  };
  const a = useTradeAnalysis({
    draftKey: null,
    evaluationRequest: request,
    validationRequest: validate ? { team_ids: request.team_ids, player_moves: draft.moves, pick_moves: [] } : null,
    onEvaluationError: (e) => notifications.push(e),
  });
  useLayoutEffect(() => {
    analysis = a;
    commits.push({ label: draft.label, evaluating: a.evaluating, evaluation: a.evaluation });
  });
  return null;
}

let client: QueryClient;
let view: ReturnType<typeof render>;
const tree = (draft: Draft, validate: boolean) => (
  <QueryClientProvider client={client}>
    <Probe draft={draft} validate={validate} />
  </QueryClientProvider>
);
let validating = false;
const mount = (draft: Draft, validate = false) => {
  validating = validate;
  view = render(tree(draft, validate));
};
const setDraft = (draft: Draft) => act(() => view.rerender(tree(draft, validating)));
/** `evaluate()` reporting its success callbacks under `name`. */
const evaluateAs = (name: string, caller = analysis.evaluate) =>
  caller({ onSuccess: (r) => (successCalls[name] ??= []).push(r) });

beforeEach(() => {
  commits = [];
  checkpoints = [];
  settledWaiters = [];
  notifications = [];
  successCalls = {};
  installFetch();
  client = new QueryClient({
    mutationCache: mutationCache(),
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const FAILURE = new TypeError("Failed to fetch");
const finish = (call: Call, outcome: "success" | "failure", body: Json = SCORED) =>
  outcome === "success" ? call.settle(200, body) : call.reject(FAILURE);

/* ----------------------------------------------------- current requests */

describe.each(["success", "failure"] as const)("a current evaluation that completes with a %s", (outcome) => {
  /** Committed start: the ordinary path (control). */
  it("(control) performs its callback or notification exactly once when its start was committed first", async () => {
    mount(A);
    await act(async () => void evaluateAs("cur"));
    expect(commits.some((c) => c.evaluating)).toBe(true); // start committed before completion
    const settled = nextSettled();
    await act(async () => {
      finish(callsTo("/trades/evaluate")[0], outcome);
      await settled;
    });
    expect(checkpoints).toHaveLength(1);
    expect(checkpoints[0].startCommitted).toBe(true);
    expectOutcome(outcome, SCORED, 1);
  });

  it("performs its callback or notification exactly once even though its start has not committed yet", async () => {
    mount(A);
    await act(async () => {
      evaluateAs("cur"); // inside one act scope: React cannot commit the start until the scope ends
      const call = await nthCall("/trades/evaluate", 1);
      const settled = nextSettled();
      finish(call, outcome); // the request completes ...
      await settled; // ... and the production handler has run (checkpoint is awaited after it)
    });
    // The precondition that makes this a test of the ordering: at the checkpoint no committed
    // render had shown the evaluation as pending.
    expect(checkpoints).toHaveLength(1);
    expect(checkpoints[0].startCommitted).toBe(false);
    // The handler itself produced the effect (observed AT the checkpoint, not after later commits).
    expect(outcome === "success" ? checkpoints[0].successCallbacks : checkpoints[0].notifications).toBe(1);
    expectOutcome(outcome, SCORED, 1);
  });
});

/** Final state and exact side-effect counts for a single current request. */
function expectOutcome(outcome: "success" | "failure", body: Json, count: number) {
  if (outcome === "success") {
    expect(same(analysis.evaluation, body)).toBe(true);
    expect(Object.values(successCalls).flat()).toHaveLength(count);
    expect(same(Object.values(successCalls).flat()[0], body)).toBe(true);
    expect(notifications).toEqual([]);
  } else {
    expect(analysis.evaluation).toBeNull();
    expect(notifications).toEqual([FAILURE]); // the existing error object, notified once
    expect(Object.values(successCalls).flat()).toEqual([]);
  }
  expect(analysis.evaluating).toBe(false); // pending state ended; the reducer accepted the completion
}

/* ------------------------------------------------ newer requests, same context */

describe.each(["success", "failure"] as const)("an older evaluation that completes with a %s after a newer one has started", (olderOutcome) => {
  it("neither notifies nor disturbs the newer request, even before the newer start commits", async () => {
    mount(A);
    await act(async () => {
      evaluateAs("older");
      const first = await nthCall("/trades/evaluate", 1);
      evaluateAs("newer"); // the newer eligible request starts; its start is not committed
      const second = await nthCall("/trades/evaluate", 2);

      let settled = nextSettled();
      finish(first, olderOutcome, SCORED);
      await settled;
      expect(checkpoints[0].startCommitted).toBe(false); // neither start has committed
      expect(checkpoints[0].successCallbacks).toBe(0); // the older handler produced nothing
      expect(checkpoints[0].notifications).toBe(0);

      settled = nextSettled();
      second.settle(200, PARTIAL);
      await settled;
    });
    expect(successCalls.older ?? []).toEqual([]);
    expect(successCalls.newer).toHaveLength(1);
    expect(same(successCalls.newer[0], PARTIAL)).toBe(true);
    expect(notifications).toEqual([]);
    expect(same(analysis.evaluation, PARTIAL)).toBe(true);
    expect(analysis.evaluating).toBe(false);
  });
});

/* ------------------------------------------------ obsolete captured callers */

describe.each(["success", "failure"] as const)("a retained evaluate() from an earlier context; the current request ends in %s", (outcome) => {
  it("does not send a request, take ownership, or suppress the current request's callback or notification", async () => {
    mount(A);
    const evaluateA = analysis.evaluate; // captured while context A is current
    await setDraft(B); // context B commits
    await act(async () => void evaluateAs("B")); // a valid evaluation for B (start committed)
    expect(callsTo("/trades/evaluate")).toHaveLength(1);
    expect(analysis.evaluating).toBe(true);

    await act(async () => void evaluateAs("A-retained", evaluateA)); // the obsolete caller
    expect(callsTo("/trades/evaluate")).toHaveLength(1); // rejected before any work was started
    expect(analysis.evaluating).toBe(true); // B is still the pending request

    const settled = nextSettled();
    await act(async () => {
      finish(callsTo("/trades/evaluate")[0], outcome, PARTIAL);
      await settled;
    });
    expect(successCalls["A-retained"] ?? []).toEqual([]);
    if (outcome === "success") {
      expect(successCalls.B).toHaveLength(1);
      expect(same(analysis.evaluation, PARTIAL)).toBe(true); // B's analysis, never A's
      expect(notifications).toEqual([]);
    } else {
      expect(notifications).toEqual([FAILURE]);
      expect(successCalls.B ?? []).toEqual([]);
      expect(analysis.evaluation).toBeNull();
    }
    expect(analysis.evaluating).toBe(false);
  });

  it("(batched) refuses the retained call, and the current request keeps its callback, before B's start commits", async () => {
    mount(A);
    const evaluateA = analysis.evaluate;
    await setDraft(B);
    await act(async () => {
      evaluateAs("B");
      const call = await nthCall("/trades/evaluate", 1);
      evaluateAs("A-retained", evaluateA); // B's start has not committed; the obsolete caller must still be refused
      expect(callsTo("/trades/evaluate")).toHaveLength(1);
      const settled = nextSettled();
      finish(call, outcome, PARTIAL);
      await settled;
    });
    expect(successCalls["A-retained"] ?? []).toEqual([]);
    expect(outcome === "success" ? successCalls.B : notifications).toHaveLength(1);
  });
});

describe("a retained evaluate() after unmount", () => {
  it("sends nothing and takes no ownership", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    mount(A);
    const retained = analysis.evaluate;
    view.unmount();
    await act(async () => void evaluateAs("late", retained));
    expect(callsTo("/trades/evaluate")).toHaveLength(0);
    expect(successCalls.late ?? []).toEqual([]);
    expect(notifications).toEqual([]);
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });
});

/* ------------------------------------------ the validation channel is separate */

describe.each(["success", "failure"] as const)("validation activity while an evaluation is pending; the evaluation ends in %s", (outcome) => {
  it("cannot take the evaluation's callback or notification away", async () => {
    vi.useFakeTimers();
    mount(A, true);
    await act(async () => void evaluateAs("cur"));
    await act(async () => void (await vi.advanceTimersByTimeAsync(VALIDATION_DEBOUNCE_MS))); // validation request #2 goes out
    expect(callsTo("/trades/validate")).toHaveLength(1);
    const legality: LegalityResponse = {
      league_year: "synthetic",
      overall_status: "verified_legal",
      teams: {},
      rule_results: [],
      cap_parameters_source: "synthetic test value",
      contract_provider_configured: false,
    };
    await act(async () => callsTo("/trades/validate")[0].settle(200, legality));
    expect(analysis.validation?.league_year).toBe("synthetic");
    expect(analysis.evaluating).toBe(true); // validation neither finished nor disturbed the evaluation

    const settled = nextSettled();
    await act(async () => {
      finish(callsTo("/trades/evaluate")[0], outcome);
      await settled;
    });
    expectOutcome(outcome, SCORED, 1);
    expect(analysis.validation?.league_year).toBe("synthetic"); // and the evaluation did not disturb validation
  });
});
