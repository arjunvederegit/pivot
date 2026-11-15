/**
 * W2.6C — the delayed reveal of a new evaluation belongs to ONE accepted request and to the results
 * section that displays it.
 *
 * Exercised: the production `useTradeAnalysis` (with its synchronous request ownership), the
 * production `useRevealResults`, `useSelectedTeam`, `evaluateTrade`, `lib/api.ts` and React Query's
 * mutation, in a small rendered consumer that wires them the way the page does:
 * `evaluate({ onSuccess: (_result, accepted) => reveal.arm(accepted) })`, a results `<section
 * id="deal-evaluation">` rendered only while an evaluation exists and given `reveal.sectionRef`.
 * NOT exercised: the evaluator page itself (it is not exported from a Next page file); its wiring is
 * covered by the narrow source assertions at the end.
 *
 * Stubbed: `fetch` (deferred promises), `Element.scrollIntoView` (recorded with its element and
 * options; jsdom has none) and `window.matchMedia` (reduced motion). Fake timers; nothing sleeps.
 * All of these are restored after every test.
 *
 * React Query tells its observers about a mutation's state from a 0 ms timer; under fake timers any
 * advance of time would fire it, re-render, and so commit a start the test wants left uncommitted.
 * The hook never reads that mutation state, so `notifyManager.setScheduler(() => {})` suppresses those
 * scheduled notifications for the duration of each test (they are dropped, not queued or replayed;
 * the default scheduler is restored afterwards). That is a test device to force an ordering, not a
 * model of ordinary browser scheduling; it lets time pass inside one `act` scope with the start
 * uncommitted.
 *
 * Timing checkpoint for "the request completed but its result has not committed": the test-owned
 * `MutationCache.onSettled`, which TanStack Query 5.101.3 runs after the hook's own `onSuccess`
 * (src/mutation.ts L246 then L254). Everything inside one `act` scope is uncommitted until the scope
 * ends, so the consumer's DOM still shows the previous state when the checkpoint fires.
 *
 * Results are told apart by fixture: the consumer stamps each section with `data-result`.
 */

import { MutationCache, QueryClient, QueryClientProvider, notifyManager } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { StrictMode, useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradeEvaluationRequest, TradeEvaluationResult } from "@/lib/tradeEvaluate";
import type { LegalityResponse } from "@/lib/types";
import { REVEAL_DELAY_MS, useRevealResults } from "@/lib/useRevealResults";
import { resolveActiveTeam, useSelectedTeam } from "@/lib/useSelectedTeam";
import { VALIDATION_DEBOUNCE_MS, useTradeAnalysis } from "@/lib/useTradeAnalysis";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const FIXTURES = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/trade-evaluate-responses.json"), "utf8"),
) as Record<string, { body: Json }>;
const BODIES = { scored: FIXTURES.scored.body, partial: FIXTURES.partial.body } as const;
type Name = keyof typeof BODIES;
const [TEAM_A, TEAM_B] = Object.keys(BODIES.scored.evaluations);

const nameOf = (e: TradeEvaluationResult | null): Name | null =>
  e ? ((Object.keys(BODIES) as Name[]).find((n) => JSON.stringify(e) === JSON.stringify(BODIES[n])) ?? null) : null;

const mv = (id: string) => ({ player_id: id, from_team_id: TEAM_A, to_team_id: TEAM_B });
interface Draft {
  moves: ReturnType<typeof mv>[];
  key?: string | null;
}
const A: Draft = { moves: [mv("p1")] };
const B: Draft = { moves: [mv("p1"), mv("p2")] };

/* ------------------------------------------------------------ browser stubs */

interface Scroll {
  element: HTMLElement;
  result: string | undefined;
  options: ScrollIntoViewOptions;
}
let scrolls: Scroll[] = [];
let reducedMotion = false;

function installBrowserStubs() {
  scrolls = [];
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value(this: HTMLElement, options: ScrollIntoViewOptions) {
      scrolls.push({ element: this, result: this.dataset.result, options });
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({ matches: reducedMotion && query.includes("prefers-reduced-motion"), media: query }),
  });
}
function removeBrowserStubs() {
  // jsdom defines neither; remove them so no test sees another's stub.
  delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
  delete (window as { matchMedia?: unknown }).matchMedia;
}

/* -------------------------------------------------------------- fetch stub */

interface Call {
  path: string;
  settle(status: number, payload: unknown): void;
}
let calls: Call[] = [];
let waiters: (() => boolean)[] = [];
const callsTo = (path: string) => calls.filter((c) => c.path === path);
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
        new Promise<Response>((res) => {
          calls.push({
            path: String(url).replace("/api/v1", ""),
            settle: (status, payload) =>
              res(new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } })),
          });
          waiters = waiters.filter((wake) => !wake());
        }),
    ),
  );
}

/* ------------------------------------------------------------- the consumer */

interface Commit {
  evaluation: TradeEvaluationResult | null;
}
let commits: Commit[] = [];
let settledWaiters: (() => void)[] = [];
let checkpointSawNewResult: boolean[] = [];
let analysis: ReturnType<typeof useTradeAnalysis>;
let selectTeam: (id: string) => void;
let armDirectly: ReturnType<typeof useRevealResults<TradeEvaluationResult>>["arm"];
let expectedAtCheckpoint: Name | null = null;

const mutationCache = () =>
  new MutationCache({
    onSettled: () => {
      // At the checkpoint: has any committed render already shown the result this request produced?
      checkpointSawNewResult.push(commits.some((c) => nameOf(c.evaluation) === expectedAtCheckpoint));
      settledWaiters.splice(0).forEach((w) => w());
    },
  });
const nextSettled = () => new Promise<void>((r) => settledWaiters.push(r));

function Consumer({ draft, validate = false }: { draft: Draft; validate?: boolean }) {
  const request: TradeEvaluationRequest = {
    team_ids: [TEAM_A, TEAM_B],
    player_moves: draft.moves,
    pick_moves: [],
    scenario_id: null,
    strategy: "custom",
  };
  const a = useTradeAnalysis({
    draftKey: draft.key ?? null,
    evaluationRequest: request,
    validationRequest: validate ? { team_ids: request.team_ids, player_moves: draft.moves, pick_moves: [] } : null,
    onEvaluationError: () => {},
  });
  const { arm, sectionRef } = useRevealResults(a.evaluation);
  const [selected, select] = useSelectedTeam(request.team_ids, draft.key ?? null);
  useLayoutEffect(() => {
    analysis = a;
    selectTeam = select;
    armDirectly = arm;
    evaluateAndReveal = () => a.evaluate({ onSuccess: (_result, accepted) => arm(accepted) });
    commits.push({ evaluation: a.evaluation });
  });
  // The results section exists only while there is an evaluation, as on the page.
  return a.evaluation ? (
    <section
      id="deal-evaluation"
      ref={sectionRef}
      data-result={nameOf(a.evaluation) ?? "unknown"}
      data-active={resolveActiveTeam(selected, a.evaluation.evaluations, request.team_ids)}
    />
  ) : null;
}
let evaluateAndReveal: () => void;

let client: QueryClient;
let strict = false;
let view: ReturnType<typeof render>;
const tree = (draft: Draft, validate: boolean) => {
  const element = (
    <QueryClientProvider client={client}>
      <Consumer draft={draft} validate={validate} />
    </QueryClientProvider>
  );
  return strict ? <StrictMode>{element}</StrictMode> : element;
};
let validateOn = false;
const mount = (draft: Draft, validate = false) => {
  validateOn = validate;
  view = render(tree(draft, validate));
};
const setDraft = (draft: Draft) => act(() => view.rerender(tree(draft, validateOn)));

beforeEach(() => {
  notifyManager.setScheduler(() => {}); // suppress observer notifications: see the header
  vi.useFakeTimers();
  installBrowserStubs();
  installFetch();
  reducedMotion = false;
  strict = false;
  commits = [];
  settledWaiters = [];
  checkpointSawNewResult = [];
  expectedAtCheckpoint = null;
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
  removeBrowserStubs();
  notifyManager.setScheduler((callback) => setTimeout(callback, 0)); // React Query's own default
});

const tick = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
/** Start an evaluation (start committed) and answer it; the result commits when the act scope ends. */
async function evaluateAndAccept(name: Name) {
  await act(async () => void evaluateAndReveal());
  const n = callsTo("/trades/evaluate").length;
  const settled = nextSettled();
  expectedAtCheckpoint = name;
  await act(async () => {
    (await nthCall("/trades/evaluate", n)).settle(200, BODIES[name]);
    await settled;
  });
}
const resultsInDom = () => Array.from(document.querySelectorAll<HTMLElement>("#deal-evaluation")).map((e) => e.dataset.result);

/* ======================================================= 1. a current success */

describe.each([
  { motion: "normally", reduced: false, behavior: "smooth" },
  { motion: "under reduced motion", reduced: true, behavior: "auto" },
])("a current success $motion", ({ reduced, behavior }) => {
  it(`scrolls its own results section exactly once, at ${REVEAL_DELAY_MS} ms, with behavior ${behavior}`, async () => {
    reducedMotion = reduced;
    mount(A);
    await evaluateAndAccept("scored");
    expect(resultsInDom()).toEqual(["scored"]);
    await tick(REVEAL_DELAY_MS - 1);
    expect(scrolls).toHaveLength(0); // not before the deadline
    await tick(1);
    expect(scrolls).toHaveLength(1);
    expect(scrolls[0].options).toEqual({ behavior, block: "start" });
    expect(scrolls[0].element).toBe(document.getElementById("deal-evaluation"));
    expect(scrolls[0].result).toBe("scored");
    await tick(5000);
    expect(scrolls).toHaveLength(1); // and never again
  });
});

/* ===================================== 2. completion before the result commits */

describe("a current success that completes before its own result has committed", () => {
  it("never scrolls the preceding result, then reveals its own once, 80 ms after it commits", async () => {
    mount(A);
    await evaluateAndAccept("scored"); // the preceding result, committed
    await tick(REVEAL_DELAY_MS);
    expect(scrolls.map((s) => s.result)).toEqual(["scored"]); // its own reveal, already delivered
    scrolls.length = 0;

    await act(async () => {
      evaluateAndReveal(); // second evaluation; its start is not committed in this scope
      const call = await nthCall("/trades/evaluate", 2);
      const settled = nextSettled();
      expectedAtCheckpoint = "partial";
      call.settle(200, BODIES.partial); // completes ...
      await settled; // ... and the hook's production handler has run
      // The matching result has NOT committed, and the preceding one is still what is mounted.
      expect(checkpointSawNewResult.at(-1)).toBe(false);
      expect(resultsInDom()).toEqual(["scored"]);
      await vi.advanceTimersByTimeAsync(REVEAL_DELAY_MS + 40); // time passes over the preceding section
      expect(scrolls).toHaveLength(0);
    });
    // Now the matching result has committed.
    expect(resultsInDom()).toEqual(["partial"]);
    expect(scrolls).toHaveLength(0);
    await tick(REVEAL_DELAY_MS - 1);
    expect(scrolls).toHaveLength(0);
    await tick(1);
    expect(scrolls.map((s) => s.result)).toEqual(["partial"]);
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });
});

describe("a reveal armed for a result that is not the one on screen", () => {
  it("never scrolls what is on screen (the hook's own guard, independent of the page's call order)", async () => {
    mount(A);
    await evaluateAndAccept("scored");
    await tick(REVEAL_DELAY_MS); // its own reveal, delivered
    scrolls.length = 0;
    const elsewhere = structuredClone(BODIES.partial) as unknown as TradeEvaluationResult; // another request's result object
    await act(async () => armDirectly({ result: elsewhere, isCurrent: () => true }));
    await tick(5000);
    expect(scrolls).toHaveLength(0);
  });
});

/* ==================================== 3. a new evaluation of identical inputs */

describe("a new evaluation with identical inputs", () => {
  it("makes the earlier reveal obsolete before the new start commits, then reveals itself", async () => {
    mount(A);
    await evaluateAndAccept("scored"); // A accepted and committed at t = 0; its deadline is t = 80
    await tick(10);
    await act(async () => {
      evaluateAndReveal(); // the second request takes ownership; its start is not committed
      await nthCall("/trades/evaluate", 2);
      expect(resultsInDom()).toEqual(["scored"]); // the first result is still the mounted target
      await vi.advanceTimersByTimeAsync(REVEAL_DELAY_MS); // past the first deadline
      expect(scrolls).toHaveLength(0);
      const settled = nextSettled();
      expectedAtCheckpoint = "partial";
      (await nthCall("/trades/evaluate", 2)).settle(200, BODIES.partial);
      await settled;
    });
    expect(resultsInDom()).toEqual(["partial"]);
    expect(scrolls).toHaveLength(0);
    await tick(REVEAL_DELAY_MS);
    expect(scrolls.map((s) => s.result)).toEqual(["partial"]);
  });
});

describe.each([
  { mode: "normally", strictMode: false },
  { mode: "under StrictMode", strictMode: true },
])("a newer reveal armed before an obsolete timer finishes, $mode", ({ strictMode }) => {
  it("is not erased by the obsolete timer: it still reveals its own result once, 80 ms after it commits", async () => {
    strict = strictMode;
    mount(A);
    await evaluateAndAccept("scored"); // A accepted and committed at t = 0; its timer is pending for t = 80
    await tick(10); // t = 10
    expect(scrolls).toHaveLength(0);

    await act(async () => {
      evaluateAndReveal(); // B, identical inputs; its start is not committed in this scope
      const call = await nthCall("/trades/evaluate", 2);
      const settled = nextSettled();
      expectedAtCheckpoint = "partial";
      call.settle(200, BODIES.partial); // B succeeds BEFORE A's deadline ...
      await settled; // ... and the production success handler has run, so it has called arm(B)

      // Preconditions, asserted rather than assumed: B has not committed, A's section is still shown.
      expect(checkpointSawNewResult.at(-1)).toBe(false);
      expect(commits.some((c) => nameOf(c.evaluation) === "partial")).toBe(false);
      expect(resultsInDom()).toEqual(["scored"]);

      await vi.advanceTimersByTimeAsync(REVEAL_DELAY_MS - 10 + 5); // through A's old deadline (t = 80), to t = 85
      expect(scrolls).toHaveLength(0); // A's timer fired, failed its isCurrent() check, and did not scroll
      expect(resultsInDom()).toEqual(["scored"]); // B still has not committed
    });

    // B's result commits now (t = 85); its reveal was armed before A's timer finished.
    expect(resultsInDom()).toEqual(["partial"]);
    expect(scrolls).toHaveLength(0);
    await tick(REVEAL_DELAY_MS - 1);
    expect(scrolls).toHaveLength(0); // not before 80 ms after B's section committed
    await tick(1);
    expect(scrolls).toHaveLength(1); // B's legitimate reveal (the failing assertion before the fix)
    expect(scrolls[0].result).toBe("partial");
    expect(scrolls[0].element).toBe(document.getElementById("deal-evaluation"));
    expect(scrolls[0].options).toEqual({ behavior: "smooth", block: "start" });
    await tick(5000);
    expect(scrolls).toHaveLength(1); // and no duplicate
  });
});

/* =========== 4./5. a newer result is present at the old deadline; invalidation */

const INVALIDATIONS: { name: string; invalidate: () => Promise<unknown> | void }[] = [
  { name: "a new evaluation of identical inputs", invalidate: () => {} },
  { name: "a relevant input change", invalidate: () => setDraft(B) },
  { name: "a saved-draft key change with equal inputs", invalidate: () => setDraft({ ...A, key: "trade-1" }) },
  { name: "clearEvaluation()", invalidate: () => act(() => analysis.clearEvaluation()) },
];

describe.each(INVALIDATIONS)("$name, then a quick newer result", ({ invalidate }) => {
  it("does not let the old timer scroll the newer result, which reveals at its own deadline", async () => {
    mount(A);
    await evaluateAndAccept("scored"); // committed at t = 0: old deadline t = 80
    await tick(10);
    await invalidate();
    await evaluateAndAccept("partial"); // the newer result is mounted by t = 10: its deadline is t = 90
    expect(resultsInDom()).toEqual(["partial"]);
    await tick(70); // t = 80, the old deadline, with the newer results section present
    expect(scrolls).toHaveLength(0);
    await tick(9); // t = 89
    expect(scrolls).toHaveLength(0);
    await tick(1); // t = 90
    expect(scrolls.map((s) => s.result)).toEqual(["partial"]);
    expect(scrolls[0].element).toBe(document.getElementById("deal-evaluation"));
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });

});

// The first entry replaces the result with a newer request; these three merely invalidate it.
describe.each(INVALIDATIONS.slice(1))("$name with no replacement result", ({ invalidate }) => {
  it("(control) leaves nothing to scroll", async () => {
    mount(A);
    await evaluateAndAccept("scored");
    await tick(10);
    await invalidate();
    await tick(5000);
    expect(scrolls).toHaveLength(0);
  });
});

/* ===================================================== 6. unmount and remount */

describe("unmount with a reveal pending, then a new instance with the same results id", () => {
  it("does not scroll the new instance's results; the new instance reveals its own", async () => {
    mount(A);
    await evaluateAndAccept("scored"); // committed at t = 0: deadline t = 80
    await tick(10);
    view.unmount();
    expect(resultsInDom()).toEqual([]);
    commits = [];
    mount(A); // a later mount of the same page: same DOM id
    await evaluateAndAccept("partial"); // committed at t = 10: deadline t = 90
    expect(resultsInDom()).toEqual(["partial"]);
    await tick(70); // t = 80
    expect(scrolls).toHaveLength(0);
    await tick(10); // t = 90
    expect(scrolls.map((s) => s.result)).toEqual(["partial"]);
    expect(scrolls[0].element).toBe(document.getElementById("deal-evaluation"));
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });
});

/* ============================================================ 7. unrelated work */

describe("unrelated renders while a reveal is pending", () => {
  it("do not postpone the deadline or create a second reveal (selected team, unrelated rerender)", async () => {
    mount(A);
    await evaluateAndAccept("scored"); // deadline t = 80
    await tick(30);
    await act(async () => selectTeam(TEAM_B));
    expect(document.getElementById("deal-evaluation")?.dataset.active).toBe(TEAM_B);
    await setDraft({ moves: [mv("p1")] }); // equivalent contents, new objects
    await tick(49); // t = 79
    expect(scrolls).toHaveLength(0);
    await tick(1); // t = 80: the original deadline
    expect(scrolls).toHaveLength(1);
    await act(async () => selectTeam(TEAM_A));
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });

  it("are not disturbed by validation activity, which also cannot cause another reveal", async () => {
    mount(A, true);
    await tick(VALIDATION_DEBOUNCE_MS - 20); // t = 430: the rules check is about to go out
    await evaluateAndAccept("scored"); // committed at t = 430: deadline t = 510
    await tick(20); // t = 450: the validation request is sent
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
    await tick(59); // t = 509
    expect(scrolls).toHaveLength(0);
    await tick(1); // t = 510
    expect(scrolls.map((s) => s.result)).toEqual(["scored"]);
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });
});

/* ========================================================= 8. StrictMode */

describe("under StrictMode", () => {
  it("reveals a legitimate result once", async () => {
    strict = true;
    mount(A);
    await evaluateAndAccept("scored");
    await tick(REVEAL_DELAY_MS - 1);
    expect(scrolls).toHaveLength(0);
    await tick(1);
    expect(scrolls.map((s) => s.result)).toEqual(["scored"]);
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });

  it("still refuses an obsolete reveal, and delivers the newer one", async () => {
    strict = true;
    mount(A);
    await evaluateAndAccept("scored"); // deadline t = 80
    await tick(10);
    await act(async () => {
      evaluateAndReveal();
      await nthCall("/trades/evaluate", 2);
      await vi.advanceTimersByTimeAsync(REVEAL_DELAY_MS);
      expect(scrolls).toHaveLength(0);
      const settled = nextSettled();
      expectedAtCheckpoint = "partial";
      (await nthCall("/trades/evaluate", 2)).settle(200, BODIES.partial);
      await settled;
    });
    await tick(REVEAL_DELAY_MS);
    expect(scrolls.map((s) => s.result)).toEqual(["partial"]);
    await tick(5000);
    expect(scrolls).toHaveLength(1);
  });
});

/* ============================================ 9. the page's wiring (source only) */

describe("the Trade Evaluator page's wiring (source assertions; the page is not mounted)", () => {
  const page = readFileSync(resolve(__dirname, "../../app/trade-evaluator/page.tsx"), "utf8");

  it("no longer schedules an unowned timer or looks the results up by a global id", () => {
    expect(page).not.toMatch(/function revealResults/);
    expect(page).not.toMatch(/onSuccess:\s*revealResults/);
    expect(page).not.toMatch(/getElementById\("deal-evaluation"\)/);
  });

  it("starts every evaluation through one handler that arms the owned reveal, and gives the section its ref", () => {
    expect(page).toMatch(/useRevealResults\(evaluation\)/);
    expect(page.match(/onClick=\{evaluateAndReveal\}|onEvaluate=\{evaluateAndReveal\}/g)).toHaveLength(2);
    expect(page.match(/evaluate\(\{ onSuccess:/g)).toHaveLength(1);
    expect(page).toMatch(/reveal\.arm\(accepted\)/);
    expect(page).toMatch(/sectionRef=\{reveal\.sectionRef\}/);
    expect(page).toMatch(/ref=\{sectionRef\}/);
  });
});
