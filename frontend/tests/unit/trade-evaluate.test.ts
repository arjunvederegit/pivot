/**
 * The Trade Evaluator's request boundary (W2.5): `evaluateTrade` is what the page calls, and it goes
 * through the real `lib/api.ts` client. Only `fetch` is stubbed, so these tests exercise the shipped
 * client, the shipped schema wiring and the shipped `ApiError` path together.
 *
 * Fixtures: `tests/fixtures/trade-evaluate-responses.json` holds responses produced by the real backend
 * (POST /trades/evaluate, in-process, over the repo's synthetic `seeded_league` fixture; see its
 * `provenance`). Anything derived from them here is a deliberate mutation and says so.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { evaluateResponseSchema } from "@/lib/schemas";
import { evaluateTrade, type TradeEvaluationRequest } from "@/lib/tradeEvaluate";

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

interface Captured {
  request: TradeEvaluationRequest;
  status: number;
  body: Json;
}

const FIXTURES = JSON.parse(
  readFileSync(resolve(__dirname, "../fixtures/trade-evaluate-responses.json"), "utf8"),
) as Record<string, Captured>;

const clone = <T>(value: T): T => structuredClone(value);
const evaluationOf = (body: Json, index = 0): Json => Object.values(body.evaluations)[index] as Json;

/** One fetch response per call; a Response body can be read only once. */
function stubFetch(status: number, body: unknown, rawBody?: string) {
  const fetchStub = vi.fn(
    async () =>
      new Response(rawBody ?? JSON.stringify(body), {
        status,
        statusText: status === 200 ? "OK" : "Error",
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

type Outcome = { kind: "resolved"; value: any } | { kind: "rejected"; error: any }; // eslint-disable-line @typescript-eslint/no-explicit-any

async function settle(request: TradeEvaluationRequest): Promise<Outcome> {
  try {
    return { kind: "resolved", value: await evaluateTrade(request) };
  } catch (error) {
    return { kind: "rejected", error };
  }
}

/** The page's request: the deal the backend capture was generated from, in the page's body shape. */
function requestFor(name: string): TradeEvaluationRequest {
  const { team_ids, player_moves, pick_moves } = FIXTURES[name].request;
  return { team_ids, player_moves, pick_moves, scenario_id: null, strategy: "custom" };
}

/** What a body looks like after crossing the wire (JSON has no -0, for instance). */
const wire = <T>(body: T): T => JSON.parse(JSON.stringify(body));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the fixtures are what the backend produced", () => {
  it("covers every response branch the evaluator renders", () => {
    const statuses = (name: string) =>
      Object.values(FIXTURES[name].body.evaluations as Record<string, Json>).map((e) => e.decision_status);
    expect(statuses("scored")).toEqual(["scored", "scored"]);
    expect(statuses("partial")).toEqual(["scored", "scored"]);
    expect(statuses("no_distribution")).toEqual(["scored", "scored"]);
    expect(statuses("insufficient_data")).toEqual(["insufficient_data", "insufficient_data"]);
    expect(statuses("suppressed_illegal")).toEqual(["suppressed_illegal", "suppressed_illegal"]);
    expect(FIXTURES.suppressed_illegal.status).toBe(200); // an illegal deal is a successful evaluation
    expect(FIXTURES.http_422.status).toBe(422);
  });
});

describe("evaluateTrade: legitimate responses survive with their contents", () => {
  it.each(["scored", "partial", "no_distribution", "insufficient_data", "suppressed_illegal"])(
    "accepts the backend's %s response unchanged",
    async (name) => {
      const { body } = FIXTURES[name];
      stubFetch(200, body);
      const outcome = await settle(requestFor(name));
      expect(outcome.kind).toBe("resolved");
      // Deep equality covers the nested analytical objects (detail, uncertainty, season_basis, ...).
      expect((outcome as { value: unknown }).value).toEqual(wire(body));
    },
  );

  it("sends exactly the request it was given: URL, method, body, headers", async () => {
    const request = requestFor("scored");
    const fetchStub = stubFetch(200, FIXTURES.scored.body);
    await evaluateTrade(request);
    expect(fetchStub).toHaveBeenCalledTimes(1);
    const [url, init] = fetchStub.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/trades/evaluate");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify(request));
    expect(Object.keys(JSON.parse(init.body as string))).toEqual([
      "team_ids",
      "player_moves",
      "pick_moves",
      "scenario_id",
      "strategy",
    ]);
    expect(init.headers).toEqual({ "content-type": "application/json" });
  });

  it("keeps the supported null and unavailable representations", async () => {
    const insufficient = clone(FIXTURES.insufficient_data.body);
    const partial = clone(FIXTURES.partial.body);
    const noDistribution = clone(FIXTURES.no_distribution.body);
    const suppressed = clone(FIXTURES.suppressed_illegal.body);
    for (const body of [insufficient, partial, noDistribution, suppressed]) {
      stubFetch(200, body);
      const out = await settle(requestFor("scored"));
      expect(out.kind).toBe("resolved");
      expect((out as { value: unknown }).value).toEqual(wire(body));
    }
    expect(evaluationOf(insufficient).composite_utility).toBeNull();
    expect(evaluationOf(insufficient).uncertainty.prob_positive).toEqual(expect.any(Number)); // a real distribution
    expect(evaluationOf(partial).excluded_components).toEqual(["contract", "assets"]);
    expect(evaluationOf(partial).components.contract).toBeNull();
    expect(evaluationOf(noDistribution).uncertainty).toMatchObject({
      n_draws: 0,
      prob_positive: null,
      top_uncertainty_drivers: [],
    });
    expect(typeof evaluationOf(noDistribution).uncertainty.unavailable).toBe("string");
    expect(evaluationOf(suppressed).uncertainty).toEqual({}); // no distribution for a deal that cannot happen
    expect(evaluationOf(suppressed).sensitivity_tornado).toEqual([]);
  });

  it("does not coerce, clip or default: zero and negative values come back as sent", async () => {
    // MUTATION of the backend's scored response: zero utility, zero probability, negative band, zero weight.
    const body = clone(FIXTURES.scored.body);
    const ev = evaluationOf(body);
    ev.composite_utility = 0;
    ev.components.performance = 0;
    ev.weights.risk = 0;
    ev.incoming[0].tei = -2.5;
    ev.uncertainty = { ...ev.uncertainty, prob_positive: 0, median: -0.4, p10: -4.2, p90: 0, mean: -1.1 };
    stubFetch(200, body);
    const out = await settle(requestFor("scored"));
    expect(out.kind).toBe("resolved");
    const got = evaluationOf((out as { value: Json }).value);
    expect(got.composite_utility).toBe(0);
    expect(got.components.performance).toBe(0);
    expect(got.weights.risk).toBe(0);
    expect(got.incoming[0].tei).toBe(-2.5);
    expect(got.uncertainty.prob_positive).toBe(0);
    expect(got.uncertainty.p10).toBe(-4.2);
    expect(got.uncertainty.p90).toBe(0);
    expect((out as { value: unknown }).value).toEqual(wire(body));
  });

  it.each(["scored", "partial", "no_distribution", "insufficient_data", "suppressed_illegal"])(
    "the schema's own parse of the %s capture is lossless (nested objects included)",
    (name) => {
      const body = wire(FIXTURES[name].body);
      const parsed = evaluateResponseSchema.safeParse(body);
      expect(parsed.success).toBe(true);
      expect(parsed.data).toEqual(body);
    },
  );

  it("keeps additive fields, including nested ones", async () => {
    // MUTATION: new keys at the top level, inside legality, inside an evaluation and inside nested blocks.
    const body = clone(FIXTURES.scored.body);
    body.brand_new_top_level = { a: 1 };
    body.legality.brand_new_legality_key = "x";
    const ev = evaluationOf(body);
    ev.brand_new_evaluation_key = [1, 2, 3];
    ev.uncertainty.brand_new_uncertainty_key = { nested: true };
    ev.weights.brand_new_weight = 0.01;
    ev.sensitivity_tornado[0].brand_new_bar_key = "kept";
    ev.legality.brand_new_team_legality_key = 7;
    stubFetch(200, body);
    const out = await settle(requestFor("scored"));
    expect(out.kind).toBe("resolved");
    expect((out as { value: unknown }).value).toEqual(wire(body));
    // and the schema itself keeps them in what it parses
    const parsed = evaluateResponseSchema.safeParse(wire(body));
    expect(parsed.success).toBe(true);
    expect(parsed.data).toEqual(wire(body));
  });
});

describe("evaluateTrade: a malformed successful response is a contract error, not a result", () => {
  // Each is a MUTATION of the backend's scored response (the field a page read would trip on).
  const MALFORMED: [string, (body: Json) => void, string][] = [
    ["no legality.teams (the page does Object.entries on it)", (b) => delete b.legality.teams, "legality.teams"],
    ["legality.teams is a list", (b) => (b.legality.teams = []), "legality.teams"],
    ["no weights (Drivers lists them)", (b) => delete evaluationOf(b).weights, "weights"],
    ["a weight that arrived as a string", (b) => (evaluationOf(b).weights.risk = "0.15"), "weights.risk"],
    ["no sensitivity_tornado (the risk tab reads .length)", (b) => delete evaluationOf(b).sensitivity_tornado, "sensitivity_tornado"],
    ["a tornado bar with a string bound", (b) => (evaluationOf(b).sensitivity_tornado[0].utility_low = "41.2"), "sensitivity_tornado"],
    ["no per-team legality (the assessment reads its status)", (b) => delete evaluationOf(b).legality, "legality"],
    ["no uncertainty.n_draws (the risk tab formats it)", (b) => delete evaluationOf(b).uncertainty.n_draws, "n_draws"],
    ["n_draws as a string", (b) => (evaluationOf(b).uncertainty.n_draws = "2000"), "n_draws"],
    ["no uncertainty.top_uncertainty_drivers (the risk tab reads .length)", (b) => delete evaluationOf(b).uncertainty.top_uncertainty_drivers, "top_uncertainty_drivers"],
    ["a band with a numeric prob_positive but no p10", (b) => delete evaluationOf(b).uncertainty.p10, "p10"],
    ["a band whose median is a string", (b) => (evaluationOf(b).uncertainty.median = "1.9"), "median"],
    ["a TEI that arrived as a string", (b) => (evaluationOf(b).incoming[0].tei = "1.2"), "tei"],
    ["a scored evaluation with a null composite", (b) => (evaluationOf(b).composite_utility = null), "composite"],
    ["a composite utility that arrived as a string", (b) => (evaluationOf(b).composite_utility = "51.3"), "composite_utility"],
    ["no excluded_components", (b) => delete evaluationOf(b).excluded_components, "excluded_components"],
    ["no evaluations at all", (b) => delete b.evaluations, "evaluations"],
  ];

  it.each(MALFORMED)("rejects: %s", async (_label, mutate, mentions) => {
    const body = clone(FIXTURES.scored.body);
    mutate(body);
    stubFetch(200, body);
    const out = await settle(requestFor("scored"));
    // Before the schema was wired in, this payload resolved and became the page's evaluation.
    expect(out.kind).toBe("rejected");
    const error = (out as { error: ApiError }).error;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.name).toBe("ApiError");
    expect(error.status).toBe(502); // the client's own judgement; the backend answered 200
    expect(error.code).toBe("contract_mismatch");
    expect(error.message).toContain("/trades/evaluate");
    expect(error.message).toContain(mentions);
    expect(error.message).toContain("the response was not rendered");
    expect(String(error)).toBe(error.message);
  });

  it("applies the same rules to the other response branches", async () => {
    // MUTATIONS of the suppressed and insufficient-data captures.
    const noWeights = clone(FIXTURES.suppressed_illegal.body);
    delete evaluationOf(noWeights).weights;
    const noDrivers = clone(FIXTURES.insufficient_data.body);
    delete evaluationOf(noDrivers).uncertainty.top_uncertainty_drivers;
    for (const body of [noWeights, noDrivers]) {
      stubFetch(200, body);
      const out = await settle(requestFor("scored"));
      expect(out.kind).toBe("rejected");
      expect((out as { error: ApiError }).error).toMatchObject({ status: 502, code: "contract_mismatch" });
    }
  });

  it("rejects a body that is not an evaluation at all", async () => {
    for (const body of [{}, { legality: { overall_status: "x" } }, [], null, "ok"]) {
      stubFetch(200, body);
      const out = await settle(requestFor("scored"));
      expect(out.kind).toBe("rejected");
      expect((out as { error: ApiError }).error).toMatchObject({ code: "contract_mismatch" });
    }
  });
});

describe("evaluateTrade: failures that are not contract problems keep their behaviour", () => {
  it("turns the backend's error envelope into an ApiError carrying its code and message", async () => {
    const { status, body } = FIXTURES.http_422;
    stubFetch(status, body);
    const out = await settle(requestFor("http_422"));
    expect(out.kind).toBe("rejected");
    const error = (out as { error: ApiError }).error;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "validation_error" });
    expect(error.message).toBe(body.error.message);
  });

  it("falls back to the status line when an error body is not JSON", async () => {
    stubFetch(500, null, "<html>upstream exploded</html>");
    const out = await settle(requestFor("scored"));
    const error = (out as { error: ApiError }).error;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 500, code: "http_error", message: "500 Error" });
  });

  it("does not run a schema over an error response", async () => {
    stubFetch(404, { error: { code: "not_found", message: "no such scenario" } });
    const out = await settle(requestFor("scored"));
    expect((out as { error: ApiError }).error).toMatchObject({ status: 404, code: "not_found" });
  });

  it("lets a network failure through as the transport's own error", async () => {
    const failure = new TypeError("Failed to fetch");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
    const out = await settle(requestFor("scored"));
    expect(out.kind).toBe("rejected");
    expect((out as { error: unknown }).error).toBe(failure);
    expect((out as { error: unknown }).error).not.toBeInstanceOf(ApiError);
  });
});
