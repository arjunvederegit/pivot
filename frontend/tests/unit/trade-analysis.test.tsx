/**
 * W2.6 — the Trade Evaluator's asynchronous screen state (`useTradeAnalysis`).
 *
 * These tests drive the production hook through a small rendered consumer. The real `lib/api.ts`
 * client and the real `evaluateTrade` helper run; only `fetch` is stubbed, and every stubbed request
 * is a deferred promise the test settles by hand, so each race is an explicit event sequence.
 * Timers are fake; nothing sleeps and nothing reaches a network.
 *
 * What the consumer observes: `Harness` records the exposed state in a LAYOUT effect, which runs
 * only for renders that React commits. That is the closest jsdom gets to "what a frame could have
 * shown"; it is NOT a browser paint test. A discarded render-phase pass is never recorded.
 *
 * Fixtures: evaluation bodies are the backend-generated captures in
 * tests/fixtures/trade-evaluate-responses.json (see its `provenance`). Validation bodies are small
 * synthetic `LegalityResponse` objects distinguished by `league_year`; the hook passes them through
 * untouched (the validate call carries no response schema).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { StrictMode, useEffect, useLayoutEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { TradeEvaluationRequest, TradeEvaluationResult } from "@/lib/tradeEvaluate";
import type { LegalityResponse, PickMove, PlayerMove } from "@/lib/types";
import { VALIDATION_DEBOUNCE_MS, useTradeAnalysis } from "@/lib/useTradeAnalysis";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/* ----------------------------------------------------------------------- fixtures */

const FIXTURES = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/trade-evaluate-responses.json"), "utf8"),
) as Record<string, { body: Json }>;
const clone = <T,>(value: T): T => structuredClone(value);
const RESULT_NAMES = ["scored", "partial", "no_distribution", "insufficient_data", "suppressed_illegal"] as const;
type ResultName = (typeof RESULT_NAMES)[number];
const body = (name: ResultName): Json => FIXTURES[name].body;

/** Which captured response (if any) the screen is showing. */
function shown(evaluation: TradeEvaluationResult | null): ResultName | null {
  if (!evaluation) return null;
  const hit = RESULT_NAMES.find((n) => JSON.stringify(evaluation) === JSON.stringify(body(n)));
  if (!hit) throw new Error("the screen shows an evaluation that is not one of the fixtures");
  return hit;
}

const legality = (label: string): LegalityResponse => ({
  league_year: label,
  overall_status: "verified_legal",
  teams: {},
  rule_results: [],
  cap_parameters_source: "synthetic test value",
  contract_provider_configured: false,
});
const legalityShown = (validation: LegalityResponse | null) => validation?.league_year ?? null;

/* ------------------------------------------------------------------------ drafts */

interface Draft {
  label: string;
  /** The loaded-trade identity the page passes as `draftKey`. */
  key?: string | null;
  teams: string[];
  moves: PlayerMove[];
  picks: PickMove[];
  scenario: string | null;
  strategy: string;
}
const mv = (player: string, to = "T2"): PlayerMove => ({ player_id: player, from_team_id: "T1", to_team_id: to });
const pk = (year: number, protections: string | null = null): PickMove => ({
  from_team_id: "T1",
  to_team_id: "T2",
  draft_year: year,
  round_number: 1,
  protections,
  is_hypothetical: true,
});
const DRAFT_A: Draft = {
  label: "A",
  teams: ["T1", "T2"],
  moves: [mv("p1")],
  picks: [],
  scenario: null,
  strategy: "custom",
};
const draft = (label: string, change: Partial<Draft>): Draft => ({ ...DRAFT_A, label, ...change });
/** The same contents in freshly allocated arrays and objects, the way a re-render rebuilds them. */
const equivalent = (d: Draft): Draft => clone(d);

const evaluationRequestOf = (d: Draft): TradeEvaluationRequest => ({
  team_ids: d.teams,
  player_moves: d.moves,
  pick_moves: d.picks,
  scenario_id: d.scenario,
  strategy: d.strategy,
});
const eligible = (d: Draft) => d.teams.length >= 2 && d.moves.length > 0;

/* ------------------------------------------------------------------- fetch stub */

interface Call {
  path: string;
  raw: string;
  body: Json;
  settled: boolean;
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
          const call: Call = {
            path: String(url).replace("/api/v1", ""),
            raw: String(init?.body ?? ""),
            body: JSON.parse(String(init?.body ?? "null")),
            settled: false,
            resolve(payload, status = 200) {
              call.settled = true;
              res(
                new Response(JSON.stringify(payload), {
                  status,
                  statusText: status === 200 ? "OK" : "Error",
                  headers: { "content-type": "application/json" },
                }),
              );
            },
            reject(error) {
              call.settled = true;
              rej(error);
            },
          };
          calls.push(call);
        }),
    ),
  );
}

/** Settle a request inside act, so React processes whatever it does to the screen state. */
async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await flushQueryNotifications();
  });
}
/** React Query tells its observers about a mutation's state change from a 0 ms timer, which fake timers hold back. */
const flushQueryNotifications = () => vi.advanceTimersByTimeAsync(0);
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
/** The debounce has elapsed and the validation request is on the wire. */
const pastDebounce = () => advance(VALIDATION_DEBOUNCE_MS);

/* ---------------------------------------------------------------- the consumer */

interface Commit {
  label: string;
  evaluation: TradeEvaluationResult | null;
  evaluating: boolean;
  validation: LegalityResponse | null;
  validating: boolean;
  validationError: string | null;
}
type Analysis = ReturnType<typeof useTradeAnalysis>;

let commits: Commit[] = [];
let analysisRef: { current: Analysis };
let errors: unknown[] = [];
let revealed: TradeEvaluationResult[] = [];

function Harness({ draft: d, unrelated = 0 }: { draft: Draft; unrelated?: number }) {
  const analysis = useTradeAnalysis({
    draftKey: d.key ?? null,
    evaluationRequest: evaluationRequestOf(d),
    validationRequest: eligible(d) ? { team_ids: d.teams, player_moves: d.moves, pick_moves: d.picks } : null,
    onEvaluationError: (e) => errors.push(e),
  });
  useLayoutEffect(() => {
    analysisRef.current = analysis;
    commits.push({
      label: d.label,
      evaluation: analysis.evaluation,
      evaluating: analysis.evaluating,
      validation: analysis.validation,
      validating: analysis.validating,
      validationError: analysis.validationError,
    });
  });
  return <div data-unrelated={unrelated}>{d.label}</div>;
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
  // The wrapper must be the same on every render, or React remounts the tree and drops its state.
  return strictMode ? <StrictMode>{element}</StrictMode> : element;
};
function mount(d: Draft, strict = false) {
  strictMode = strict;
  view = render(tree(d));
}
/** Commit a new draft (or an unrelated re-render of the same one). */
function setDraft(d: Draft, unrelated = 0) {
  const element = tree(d, unrelated);
  act(() => view.rerender(element));
}
const current = () => analysisRef.current;
const lastCommit = () => commits[commits.length - 1];
/** Evaluate with the current draft, as the Evaluate button does. */
async function clickEvaluate(onSuccess?: (r: TradeEvaluationResult) => void) {
  // async act: the mutation reaches `fetch` a few microtasks after the click.
  await act(async () => {
    current().evaluate(onSuccess ? { onSuccess } : undefined);
    await flushQueryNotifications();
  });
}
/** Evaluation requested, settled with a fixture, on the current draft. */
async function evaluateAndSettle(name: ResultName = "scored") {
  await clickEvaluate();
  const call = evalCalls().at(-1)!;
  await settle(() => call.resolve(body(name)));
  return call;
}
/** Draft is eligible, the debounce has passed, and the rules check is answered. */
async function validateAndSettle(label: string) {
  await pastDebounce();
  const call = valCalls().at(-1)!;
  await settle(() => call.resolve(legality(label)));
  return call;
}

beforeEach(() => {
  vi.useFakeTimers();
  strictMode = false;
  installFetch();
  commits = [];
  errors = [];
  revealed = [];
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

/* ======================================================================= 1. controls */

describe("current requests keep working (controls)", () => {
  it("publishes a current evaluation's data unchanged and sends the page's exact request", async () => {
    mount(DRAFT_A);
    await clickEvaluate((r) => revealed.push(r));
    expect(evalCalls()).toHaveLength(1);
    expect(evalCalls()[0].raw).toBe(JSON.stringify(evaluationRequestOf(DRAFT_A)));
    expect(Object.keys(evalCalls()[0].body)).toEqual(["team_ids", "player_moves", "pick_moves", "scenario_id", "strategy"]);
    expect(current().evaluating).toBe(true);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(current().evaluation).toEqual(JSON.parse(JSON.stringify(body("scored"))));
    expect(current().evaluating).toBe(false);
    expect(revealed).toHaveLength(1); // the page's reveal-the-results callback runs for the current success
    expect(errors).toEqual([]);
  });

  it.each(["partial", "no_distribution", "insufficient_data", "suppressed_illegal"] as const)(
    "keeps the supported %s response usable",
    async (name) => {
      mount(DRAFT_A);
      await evaluateAndSettle(name);
      expect(shown(current().evaluation)).toBe(name);
      expect(current().evaluating).toBe(false);
    },
  );

  it("ends pending state on a current failure and reports it through the page's error mechanism", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await settle(() => evalCalls()[0].resolve({ error: { code: "validation_error", message: "bad deal" } }, 422));
    expect(errors).toHaveLength(1);
    expect(errors[0]).toBeInstanceOf(ApiError);
    expect(errors[0]).toMatchObject({ status: 422, code: "validation_error", message: "bad deal" });
    expect(current().evaluating).toBe(false);
    expect(current().evaluation).toBeNull();
  });

  it("keeps a current contract_mismatch visible through the same mechanism", async () => {
    mount(DRAFT_A);
    const malformed = clone(body("scored"));
    delete Object.values<Json>(malformed.evaluations)[0].weights;
    await clickEvaluate();
    await settle(() => evalCalls()[0].resolve(malformed));
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatchObject({ status: 502, code: "contract_mismatch" });
    expect((errors[0] as Error).message).toContain("/trades/evaluate");
    expect(current().evaluation).toBeNull();
    expect(current().evaluating).toBe(false);
  });

  it("completes a current validation and its loading state", async () => {
    mount(DRAFT_A);
    expect(valCalls()).toHaveLength(0);
    await pastDebounce();
    expect(valCalls()).toHaveLength(1);
    expect(valCalls()[0].raw).toBe(JSON.stringify({ team_ids: DRAFT_A.teams, player_moves: DRAFT_A.moves, pick_moves: DRAFT_A.picks }));
    expect(Object.keys(valCalls()[0].body)).toEqual(["team_ids", "player_moves", "pick_moves"]);
    expect(current().validating).toBe(true);
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(legalityShown(current().validation)).toBe("A");
    expect(current().validating).toBe(false);
  });

  it("treats a failed current validation as no validation, and now says that it failed", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    expect(current().validationError).toBeNull();
    await settle(() => valCalls()[0].reject(new TypeError("Failed to fetch")));
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
    expect(current().validationError).toBe("The request to the rules engine did not complete.");
  });

  it("carries a current structured rejection's own message, and a current success carries no error", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    await settle(() => valCalls()[0].resolve({ error: { code: "invalid_trade", message: "p1 is not on T1" } }, 422));
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
    expect(current().validationError).toBe("p1 is not on T1");
    setDraft(draft("B", { moves: [mv("p1"), mv("p2")] }));
    expect(current().validationError).toBeNull();
    await pastDebounce();
    expect(current().validationError).toBeNull(); // a start clears it
    await settle(() => valCalls()[1].resolve(legality("B")));
    expect(legalityShown(current().validation)).toBe("B");
    expect(current().validationError).toBeNull();
  });
});

/* ============================================ 2. relevant input changes, no replacement */

/** Every input that determines the evaluation (and which of them also determine the rules check). */
const CHANGES: { name: string; next: Draft; revalidates: boolean }[] = [
  { name: "a player move is added", next: draft("B", { moves: [mv("p1"), mv("p2")] }), revalidates: true },
  { name: "a player is sent elsewhere", next: draft("B", { moves: [mv("p1", "T3")] }), revalidates: true },
  { name: "a team is added", next: draft("B", { teams: ["T1", "T2", "T3"] }), revalidates: true },
  { name: "a pick is added", next: draft("B", { picks: [pk(2027)] }), revalidates: true },
  { name: "a pick's protections change", next: draft("B", { picks: [pk(2027, "top-5")] }), revalidates: true },
  { name: "the scenario id changes", next: draft("B", { scenario: "scn-1" }), revalidates: false },
  { name: "the effective strategy changes (scenario data arrives)", next: draft("B", { scenario: "scn-1", strategy: "win_now" }), revalidates: false },
  {
    name: "a shared link restores a different deal wholesale",
    next: draft("B", { teams: ["T4", "T5"], moves: [{ player_id: "q9", from_team_id: "T4", to_team_id: "T5" }], picks: [pk(2029)] }),
    revalidates: true,
  },
];

describe("a relevant input change, with no replacement request", () => {
  it.each(CHANGES)("hides a displayed evaluation at the very render that carries the change: $name", async ({ next }) => {
    mount(DRAFT_A);
    await evaluateAndSettle("scored");
    expect(shown(current().evaluation)).toBe("scored");
    setDraft(next);
    // Every COMMITTED render for the new inputs: none may show the old deal's analysis.
    const forNext = commits.filter((c) => c.label === "B");
    expect(forNext.length).toBeGreaterThan(0);
    for (const c of forNext) expect(c.evaluation).toBeNull();
    expect(current().evaluation).toBeNull();
  });

  it.each(CHANGES)("keeps a pending old result from appearing afterwards: $name", async ({ next }) => {
    mount(DRAFT_A);
    await clickEvaluate();
    setDraft(next);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(current().evaluation).toBeNull();
    for (const c of commits.filter((c) => c.label === "B")) expect(c.evaluation).toBeNull();
  });

  it.each(CHANGES)("does not report a late obsolete failure: $name", async ({ next }) => {
    mount(DRAFT_A);
    await clickEvaluate();
    setDraft(next);
    await settle(() => evalCalls()[0].reject(new TypeError("Failed to fetch")));
    expect(errors).toEqual([]);
  });

  it.each(CHANGES)("does not let the obsolete pending evaluation block the new draft: $name", async ({ next }) => {
    mount(DRAFT_A);
    await clickEvaluate();
    setDraft(next);
    expect(current().evaluating).toBe(false);
    expect(lastCommit().evaluating).toBe(false);
    await clickEvaluate();
    expect(evalCalls()).toHaveLength(2);
    expect(evalCalls()[1].body).toEqual(JSON.parse(JSON.stringify(evaluationRequestOf(next))));
    expect(current().evaluating).toBe(true);
    await settle(() => evalCalls()[1].resolve(body("partial")));
    expect(shown(current().evaluation)).toBe("partial");
    expect(current().evaluating).toBe(false);
  });

  it.each(CHANGES)("never starts a replacement evaluation by itself: $name", async ({ next }) => {
    mount(DRAFT_A);
    await evaluateAndSettle();
    setDraft(next);
    await advance(5000);
    expect(evalCalls()).toHaveLength(1);
  });

  it.each(CHANGES)("hides the old rules check, or keeps it only when the validation inputs are unchanged: $name", async ({ next, revalidates }) => {
    mount(DRAFT_A);
    await validateAndSettle("A");
    expect(legalityShown(current().validation)).toBe("A");
    setDraft(next);
    for (const c of commits.filter((c) => c.label === "B")) {
      expect(legalityShown(c.validation)).toBe(revalidates ? null : "A");
    }
    // The existing debounce and eligibility still decide whether a new check goes out.
    await pastDebounce();
    expect(valCalls()).toHaveLength(revalidates ? 2 : 1);
    if (revalidates) {
      expect(valCalls()[1].body).toEqual(JSON.parse(JSON.stringify({ team_ids: next.teams, player_moves: next.moves, pick_moves: next.picks })));
    }
  });

  it("clears both analyses, and sends nothing, when the draft becomes ineligible", async () => {
    mount(DRAFT_A);
    await validateAndSettle("A");
    await evaluateAndSettle("scored");
    const ineligible = draft("B", { moves: [] });
    setDraft(ineligible);
    for (const c of commits.filter((c) => c.label === "B")) {
      expect(c.evaluation).toBeNull();
      expect(c.validation).toBeNull();
      expect(c.validating).toBe(false);
    }
    await advance(5000);
    expect(valCalls()).toHaveLength(1); // no replacement validation for a draft that cannot be checked
    expect(evalCalls()).toHaveLength(1);
  });

  it("drops an in-flight old validation when the draft becomes ineligible", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    setDraft(draft("B", { teams: ["T1"], moves: [] }));
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
  });
});

/* ===================================================================== 3. A -> B -> A */

describe("A -> B -> A", () => {
  const B = draft("B", { moves: [mv("p1"), mv("p2")] });
  const A2 = { ...DRAFT_A, label: "A2" };

  it("keeps the first evaluation request obsolete, and does not revive cleared results", async () => {
    mount(DRAFT_A);
    await clickEvaluate(); // evalCalls[0], for A
    setDraft(B);
    setDraft(A2);
    expect(current().evaluating).toBe(false);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(current().evaluation).toBeNull();
    for (const c of commits.filter((c) => c.label !== "A")) expect(c.evaluation).toBeNull();
    // ... and legitimate new work for A still works.
    await clickEvaluate();
    await settle(() => evalCalls()[1].resolve(body("partial")));
    expect(shown(current().evaluation)).toBe("partial");
  });

  it("does not revive an evaluation that was displayed for the first A", async () => {
    mount(DRAFT_A);
    await evaluateAndSettle("scored");
    setDraft(B);
    setDraft(A2);
    for (const c of commits.filter((c) => c.label !== "A")) expect(c.evaluation).toBeNull();
    expect(current().evaluation).toBeNull();
  });

  it("keeps the first rules-check request obsolete", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // valCalls[0], for A, in flight
    setDraft(B);
    setDraft(A2);
    await settle(() => valCalls()[0].resolve(legality("first A")));
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
    await pastDebounce(); // the debounce for the second A
    expect(valCalls()).toHaveLength(2);
    await settle(() => valCalls()[1].resolve(legality("second A")));
    expect(legalityShown(current().validation)).toBe("second A");
  });
});

/* ======================================= 4. same-input reevaluation and latest request */

describe("a new evaluation of unchanged inputs, and overlapping evaluations", () => {
  it("clears the prior output at once and shows the pending state", async () => {
    mount(DRAFT_A);
    await evaluateAndSettle("scored");
    const before = commits.length;
    await clickEvaluate();
    const after = commits.slice(before);
    expect(after.length).toBeGreaterThan(0);
    for (const c of after) {
      expect(c.evaluation).toBeNull();
      expect(c.evaluating).toBe(true);
    }
    await settle(() => evalCalls()[1].resolve(body("partial")));
    expect(shown(current().evaluation)).toBe("partial");
    expect(current().evaluating).toBe(false);
  });

  it("does not leave the previous evaluation looking like the answer to a failed request", async () => {
    mount(DRAFT_A);
    await evaluateAndSettle("scored");
    await clickEvaluate();
    await settle(() => evalCalls()[1].reject(new TypeError("Failed to fetch")));
    expect(current().evaluation).toBeNull();
    expect(current().evaluating).toBe(false);
    expect(errors).toHaveLength(1);
  });

  it("does not clear a valid evaluation merely because a same-context validation runs", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await advance(100);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    const before = commits.length;
    await pastDebounce(); // validation starts (and later finishes) in the same context
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(shown(current().evaluation)).toBe("scored");
    for (const c of commits.slice(before)) expect(shown(c.evaluation)).toBe("scored");
    expect(legalityShown(current().validation)).toBe("A");
  });

  it("lets the newer of two overlapping evaluations own the outcome, newer first", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await clickEvaluate();
    expect(evalCalls()).toHaveLength(2);
    await settle(() => evalCalls()[1].resolve(body("partial")));
    expect(shown(current().evaluation)).toBe("partial");
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(shown(current().evaluation)).toBe("partial"); // an older success cannot overwrite a newer one
    expect(current().evaluating).toBe(false);
  });

  it("lets the newer of two overlapping evaluations own the outcome, older first", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await clickEvaluate();
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(current().evaluation).toBeNull(); // not the newer request's answer
    expect(current().evaluating).toBe(true); // the newer request is still pending
    await settle(() => evalCalls()[1].resolve(body("partial")));
    expect(shown(current().evaluation)).toBe("partial");
    expect(current().evaluating).toBe(false);
  });
});

/* ========================================= 5. stale errors and completion cleanup */

describe("stale rejections and completion cleanup", () => {
  it("evaluation: an older rejection after a newer success changes nothing", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await clickEvaluate();
    await settle(() => evalCalls()[1].resolve(body("partial")));
    await settle(() => evalCalls()[0].reject(new TypeError("Failed to fetch")));
    expect(shown(current().evaluation)).toBe("partial");
    expect(current().evaluating).toBe(false);
    expect(errors).toEqual([]);
  });

  it.each(["success", "rejection"] as const)(
    "evaluation: an older %s while newer work is pending does not clear the pending state",
    async (how) => {
      mount(DRAFT_A);
      await clickEvaluate();
      await clickEvaluate();
      await settle(() => (how === "success" ? evalCalls()[0].resolve(body("scored")) : evalCalls()[0].reject(new TypeError("down"))));
      expect(current().evaluating).toBe(true);
      expect(errors).toEqual([]);
      await settle(() => evalCalls()[1].resolve(body("partial")));
      expect(current().evaluating).toBe(false);
      expect(shown(current().evaluation)).toBe("partial");
    },
  );

  it("evaluation: a newer request's own failure is still reported once", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await clickEvaluate();
    await settle(() => evalCalls()[0].resolve(body("scored")));
    await settle(() => evalCalls()[1].reject(new TypeError("down")));
    expect(errors).toHaveLength(1);
    expect(current().evaluating).toBe(false);
    expect(current().evaluation).toBeNull();
  });

  const NEXT = draft("B", { moves: [mv("p1"), mv("p2")] });

  it("validation: an older rejection after a newer success changes nothing", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // v0 for A
    setDraft(NEXT);
    await pastDebounce(); // v1 for B
    await settle(() => valCalls()[1].resolve(legality("B")));
    await settle(() => valCalls()[0].reject(new TypeError("down")));
    expect(legalityShown(current().validation)).toBe("B");
    expect(current().validating).toBe(false);
    expect(current().validationError).toBeNull();
  });

  it("validation: an older success after a newer success cannot overwrite it", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    setDraft(NEXT);
    await pastDebounce();
    await settle(() => valCalls()[1].resolve(legality("B")));
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(legalityShown(current().validation)).toBe("B");
    expect(current().validating).toBe(false);
  });

  it.each(["success", "rejection"] as const)(
    "validation: an older %s while newer work is pending does not clear the loading state",
    async (how) => {
      mount(DRAFT_A);
      await pastDebounce(); // v0
      setDraft(NEXT);
      await pastDebounce(); // v1 pending
      expect(current().validating).toBe(true);
      await settle(() => (how === "success" ? valCalls()[0].resolve(legality("A")) : valCalls()[0].reject(new TypeError("down"))));
      expect(current().validating).toBe(true); // the old finally must not unlock the new request's indicator
      expect(current().validation).toBeNull();
      expect(current().validationError).toBeNull(); // nor may an obsolete rejection report itself
      await settle(() => valCalls()[1].resolve(legality("B")));
      expect(current().validating).toBe(false);
      expect(legalityShown(current().validation)).toBe("B");
    },
  );
});

/* ================================================================ 6. validation debounce */

describe("the rules-check debounce", () => {
  it("sends nothing before 450 ms and exactly one request at 450 ms", async () => {
    expect(VALIDATION_DEBOUNCE_MS).toBe(450);
    mount(DRAFT_A);
    await advance(449);
    expect(valCalls()).toHaveLength(0);
    await advance(1);
    expect(valCalls()).toHaveLength(1);
  });

  it("sends only the latest snapshot after rapid changes, and clears the superseded timers", async () => {
    mount(DRAFT_A);
    const steps = [draft("B1", { moves: [mv("p1"), mv("p2")] }), draft("B2", { moves: [mv("p1"), mv("p2"), mv("p3")] }), draft("B3", { moves: [mv("p3")] })];
    for (const step of steps) {
      await advance(100);
      setDraft(step);
      expect(vi.getTimerCount()).toBe(1); // only the newest debounce timer is alive
    }
    await advance(449);
    expect(valCalls()).toHaveLength(0);
    await advance(1);
    expect(valCalls()).toHaveLength(1);
    expect(valCalls()[0].body.player_moves).toEqual([mv("p3")]);
  });

  it("ignores an old response that settles during the new draft's debounce interval", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // v0 in flight, validating
    setDraft(draft("B", { moves: [mv("p1"), mv("p2")] }));
    expect(current().validating).toBe(false); // the old request's loading state is not the new draft's
    await advance(200);
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(current().validation).toBeNull();
    expect(current().validating).toBe(false);
    for (const c of commits.filter((c) => c.label === "B")) expect(c.validation).toBeNull();
    for (const c of commits.filter((c) => c.label === "B")) expect(c.validationError).toBeNull();
    await advance(250); // the debounce completes and the new request is the only one that counts
    expect(valCalls()).toHaveLength(2);
    expect(current().validating).toBe(true);
  });

  it("keeps out-of-order responses from overwriting the current result or loading state", async () => {
    mount(DRAFT_A);
    await pastDebounce();
    setDraft(draft("B", { moves: [mv("p2")] }));
    await pastDebounce();
    setDraft(draft("C", { moves: [mv("p3")] }));
    await pastDebounce();
    await settle(() => valCalls()[2].resolve(legality("C")));
    await settle(() => valCalls()[0].resolve(legality("A")));
    await settle(() => valCalls()[1].resolve(legality("B")));
    expect(legalityShown(current().validation)).toBe("C");
    expect(current().validating).toBe(false);
  });
});

/* ================================================ 7. channel independence, unchanged renders */

describe("independent channels and unchanged renders", () => {
  it("lets evaluation and validation interleave in one context without invalidating each other", async () => {
    mount(DRAFT_A);
    await clickEvaluate(); // evaluation pending
    await pastDebounce(); // validation starts while the evaluation is pending
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(current().evaluating).toBe(true);
    expect(legalityShown(current().validation)).toBe("A");
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(shown(current().evaluation)).toBe("scored");
    expect(legalityShown(current().validation)).toBe("A");
    expect(current().evaluating).toBe(false);
    expect(current().validating).toBe(false);
  });

  it("lets validation settle after the evaluation without losing the evaluation", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // validation in flight
    await evaluateAndSettle("scored");
    expect(current().validating).toBe(true);
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(shown(current().evaluation)).toBe("scored");
    expect(legalityShown(current().validation)).toBe("A");
  });

  it("does not let a strategy-only change disturb the rules check or its request", async () => {
    mount(DRAFT_A);
    await pastDebounce(); // in flight
    setDraft(draft("S", { scenario: "scn-1", strategy: "win_now" }));
    expect(current().validating).toBe(true);
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(legalityShown(current().validation)).toBe("A"); // still the answer to the same rules inputs
    await advance(5000);
    expect(valCalls()).toHaveLength(1);
  });

  it("clears neither result, and sends nothing, for an equivalent re-render", async () => {
    mount(DRAFT_A);
    await validateAndSettle("A");
    await evaluateAndSettle("scored");
    const before = commits.length;
    for (let n = 1; n <= 3; n++) setDraft(equivalent({ ...DRAFT_A, label: "A-again" }), n); // new objects, same contents
    await advance(5000);
    expect(valCalls()).toHaveLength(1);
    expect(evalCalls()).toHaveLength(1);
    expect(commits.length).toBeGreaterThan(before);
    for (const c of commits.slice(before)) {
      expect(shown(c.evaluation)).toBe("scored");
      expect(legalityShown(c.validation)).toBe("A");
    }
  });

  it("treats the same moves in a different order as a different request", async () => {
    // Payload ordering is preserved, never normalised: a reordered body is a different body.
    const two = draft("two", { moves: [mv("p1"), mv("p2")] });
    const swapped = draft("swapped", { moves: [mv("p2"), mv("p1")] });
    mount(two);
    await evaluateAndSettle("scored");
    setDraft(swapped);
    expect(current().evaluation).toBeNull();
    expect(two.moves[0].player_id).toBe("p1"); // and nothing was sorted in place to find out
  });
});

/* =========================================================================== 8. lifecycle */

describe("unmount and StrictMode", () => {
  it("clears the scheduled timer and invalidates outstanding work on unmount", async () => {
    const spyError = vi.spyOn(console, "error").mockImplementation(() => {});
    mount(DRAFT_A);
    expect(vi.getTimerCount()).toBe(1); // the debounce
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
    await advance(5000);
    expect(valCalls()).toHaveLength(0);

    // Outstanding requests, then unmount, then late completions.
    mount(DRAFT_A);
    await clickEvaluate((r) => revealed.push(r));
    await pastDebounce();
    view.unmount();
    await settle(() => valCalls()[0].resolve(legality("A")));
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(revealed).toEqual([]); // no screen-owned side effect after unmount
    expect(errors).toEqual([]);
    expect(spyError).not.toHaveBeenCalled();
  });

  it("raises no error notification for a rejection that arrives after unmount", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    view.unmount();
    await settle(() => evalCalls()[0].reject(new TypeError("Failed to fetch")));
    expect(errors).toEqual([]);
  });

  it("does not disable later legitimate work after StrictMode's setup/cleanup/setup", async () => {
    mount(DRAFT_A, true);
    await pastDebounce();
    expect(valCalls()).toHaveLength(1); // a single request, not one per effect pass
    await settle(() => valCalls()[0].resolve(legality("A")));
    expect(legalityShown(current().validation)).toBe("A");
    await evaluateAndSettle("scored");
    expect(shown(current().evaluation)).toBe("scored");
    expect(errors).toEqual([]);
    // an edit still invalidates, and the next request still publishes
    setDraft(draft("B", { moves: [mv("p1"), mv("p2")] }));
    expect(current().evaluation).toBeNull();
    await evaluateAndSettle("partial");
    expect(shown(current().evaluation)).toBe("partial");
  });
});

/* ===================================================================== 9. saved/shared */

describe("adopting an authoritative result, and restoring a draft", () => {
  it("displays an adopted result with the inputs it was loaded with", async () => {
    mount(DRAFT_A);
    const adopted = JSON.parse(JSON.stringify(body("insufficient_data"))) as TradeEvaluationResult;
    act(() => current().adopt(evaluationRequestOf(DRAFT_A), adopted));
    expect(shown(current().evaluation)).toBe("insufficient_data");
    expect(current().evaluating).toBe(false);
    // an unrelated render must not erase a legitimate adopted result
    setDraft(equivalent({ ...DRAFT_A, label: "A-again" }), 1);
    expect(shown(current().evaluation)).toBe("insufficient_data");
  });

  it("adopts inputs and result in one batch without a blank frame in between", async () => {
    mount(DRAFT_A);
    await evaluateAndSettle("scored");
    const loaded = draft("loaded", { teams: ["T4", "T5"], moves: [{ player_id: "q9", from_team_id: "T4", to_team_id: "T5" }] });
    const before = commits.length;
    act(() => {
      current().adopt(evaluationRequestOf(loaded), JSON.parse(JSON.stringify(body("partial"))));
      view.rerender(tree(loaded));
    });
    const forLoaded = commits.slice(before).filter((c) => c.label === "loaded");
    expect(forLoaded.length).toBeGreaterThan(0);
    for (const c of forLoaded) expect(shown(c.evaluation)).toBe("partial");
    await advance(1000);
    expect(shown(current().evaluation)).toBe("partial");
  });

  it("supersedes an earlier request even when the adopted inputs are identical", async () => {
    mount(DRAFT_A);
    await clickEvaluate(); // pending, for exactly the inputs about to be adopted
    act(() => current().adopt(evaluationRequestOf(DRAFT_A), JSON.parse(JSON.stringify(body("insufficient_data")))));
    expect(current().evaluating).toBe(false);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(shown(current().evaluation)).toBe("insufficient_data");
  });

  it("treats a loaded trade as the authoritative draft: identical inputs still supersede earlier work", async () => {
    mount(DRAFT_A);
    await clickEvaluate();
    await pastDebounce();
    setDraft({ ...equivalent(DRAFT_A), label: "loaded", key: "trade-1" }); // same inputs, adopted from a saved trade
    expect(current().evaluating).toBe(false);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    await settle(() => valCalls()[0].resolve(legality("old")));
    expect(current().evaluation).toBeNull();
    expect(current().validation).toBeNull();
  });

  it("hides an adopted analysis on the next relevant edit", async () => {
    mount(DRAFT_A);
    act(() => current().adopt(evaluationRequestOf(DRAFT_A), JSON.parse(JSON.stringify(body("scored")))));
    setDraft(draft("B", { picks: [pk(2027)] }));
    expect(current().evaluation).toBeNull();
    for (const c of commits.filter((c) => c.label === "B")) expect(c.evaluation).toBeNull();
  });

  it("does not let an older load callback undo a newer edit", async () => {
    mount(DRAFT_A);
    setDraft(draft("B", { picks: [pk(2027)] })); // the user keeps editing
    act(() => current().adopt(evaluationRequestOf(DRAFT_A), JSON.parse(JSON.stringify(body("scored"))))); // the older load lands late
    expect(current().evaluation).toBeNull();
    for (const c of commits.filter((c) => c.label === "B")) expect(c.evaluation).toBeNull();
    setDraft({ ...DRAFT_A, label: "A2" }); // returning to the loaded inputs does not revive it
    expect(current().evaluation).toBeNull();
  });

  it("applies the relevant invalidation rules to a restored shared deal, pending work included", async () => {
    mount(DRAFT_A);
    await validateAndSettle("A");
    await clickEvaluate();
    const restored = draft("shared", { teams: ["T7", "T8"], moves: [{ player_id: "z1", from_team_id: "T7", to_team_id: "T8" }], picks: [pk(2030)] });
    setDraft(restored);
    expect(current().evaluation).toBeNull();
    expect(current().validation).toBeNull();
    expect(current().evaluating).toBe(false);
    await settle(() => evalCalls()[0].resolve(body("scored")));
    expect(current().evaluation).toBeNull();
  });
});

/* ============================================ clearEvaluation: an edit that leaves the request as it was */

describe("clearEvaluation (the page's edit handlers)", () => {
  it("hides the displayed evaluation at once, keeps the rules check, and lets new work through", async () => {
    mount(DRAFT_A);
    await validateAndSettle("A");
    await evaluateAndSettle("scored");
    const before = commits.length;
    act(() => current().clearEvaluation());
    const after = commits.slice(before);
    expect(after.length).toBeGreaterThan(0);
    for (const c of after) expect(c.evaluation).toBeNull();
    expect(legalityShown(current().validation)).toBe("A"); // the rules check is a separate channel
    await evaluateAndSettle("partial");
    expect(shown(current().evaluation)).toBe("partial");
  });

  it.each(["success", "rejection"] as const)(
    "supersedes a pending evaluation of the same inputs: a late %s changes nothing",
    async (how) => {
      mount(DRAFT_A);
      await clickEvaluate();
      act(() => current().clearEvaluation());
      expect(current().evaluating).toBe(false);
      await settle(() => (how === "success" ? evalCalls()[0].resolve(body("scored")) : evalCalls()[0].reject(new TypeError("down"))));
      expect(current().evaluation).toBeNull();
      expect(current().evaluating).toBe(false);
      expect(errors).toEqual([]);
    },
  );
});

/* ================================================== sensitivity of the committed-render probe */

describe("the committed-render probe can see a passive-effect-only clearing bug", () => {
  /** A deliberately WRONG hook: it clears the result in a passive effect, after the new inputs rendered. */
  function useClearInEffect(signature: string) {
    const [state, setState] = useState<{ signature: string; result: string | null }>({ signature, result: "old analysis" });
    useEffect(() => {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- the deliberate bug this control models
      setState((s) => (s.signature === signature ? s : { signature, result: null }));
    }, [signature]);
    return state.result;
  }
  it("records the stale committed render that such a bug produces", () => {
    const seen: { signature: string; result: string | null }[] = [];
    function Wrong({ signature }: { signature: string }) {
      const result = useClearInEffect(signature);
      useLayoutEffect(() => {
        seen.push({ signature, result });
      });
      return null;
    }
    const wrong = render(<Wrong signature="A" />);
    act(() => wrong.rerender(<Wrong signature="B" />));
    // Flushing effects with act hides it from a plain post-act assertion ...
    expect(seen.at(-1)).toEqual({ signature: "B", result: null });
    // ... but the commit log shows the render in which B was on screen with A's analysis.
    expect(seen.some((c) => c.signature === "B" && c.result === "old analysis")).toBe(true);
  });
});
