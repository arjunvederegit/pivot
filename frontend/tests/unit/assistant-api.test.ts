/**
 * The Ask Pivot network boundary: what the schemas accept and refuse, how each error code
 * from the backend's envelope becomes a specific, actionable message, and the two ways an
 * answer can be cut short (the reader's Stop, the browser's own deadline).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  AssistantApiError,
  AssistantTimeoutError,
  SERVER_FLAG_STEP,
  askAssistant,
  describeAssistantError,
  getAssistantContext,
  getAssistantStatus,
} from "@/lib/assistant-api";
import { askResponseSchema, contextSchema, statusSchema } from "@/lib/assistant-schemas";
import type { AskRequest } from "@/lib/assistant-types";
import { askResponseFixture, contextFixture, json, sourceFixture, statusFixture } from "./assistant-fixtures";

const REQUEST: AskRequest = {
  question: "Who defends the point of attack best?",
  mode: "research",
  team_id: "COMETS",
  priorities: { point_of_attack_defense: "high" },
  previous_priorities: null,
  constraints: [],
  history: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("askResponseSchema", () => {
  it("accepts a realistic answer and keeps unknown keys (additive changes never break)", () => {
    const parsed = askResponseSchema.safeParse({ ...askResponseFixture(), brand_new_field: 1 });
    expect(parsed.success).toBe(true);
    expect((parsed.data as Record<string, unknown>).brand_new_field).toBe(1);
  });

  it("rejects an answer whose data_mode is not synthetic_demo", () => {
    const parsed = askResponseSchema.safeParse({ ...askResponseFixture(), data_mode: "nba_live" });
    expect(parsed.success).toBe(false);
    expect(parsed.error?.issues[0].message).toMatch(/synthetic_demo/);
  });

  it("rejects a single source whose data_mode is not synthetic_demo", () => {
    const answer = askResponseFixture({
      sources: [sourceFixture(), { ...sourceFixture(), data_mode: "real" as "synthetic_demo" }],
    });
    expect(askResponseSchema.safeParse(answer).success).toBe(false);
  });

  it("rejects an unknown answer_kind or attribution_status", () => {
    expect(askResponseSchema.safeParse({ ...askResponseFixture(), answer_kind: "verified" }).success).toBe(false);
    expect(
      askResponseSchema.safeParse({ ...askResponseFixture(), attribution_status: "fact_checked" }).success,
    ).toBe(false);
  });

  it("rejects a source without an excerpt — an empty passage must not look like a document", () => {
    const { excerpt: _excerpt, ...noExcerpt } = sourceFixture();
    void _excerpt;
    expect(askResponseSchema.safeParse(askResponseFixture({ sources: [noExcerpt as never] })).success).toBe(false);
  });

  it("drops a malformed debug block instead of the whole answer", () => {
    const parsed = askResponseSchema.safeParse({ ...askResponseFixture(), diagnostics: { k: "six" } });
    expect(parsed.success).toBe(true);
    expect(parsed.data?.diagnostics).toBeNull();
  });
});

describe("statusSchema and contextSchema", () => {
  it("accepts a ready status and a minimal switched-off one", () => {
    expect(statusSchema.safeParse(statusFixture()).success).toBe(true);
    const off = statusSchema.parse({ enabled: false });
    expect(off.ready).toBe(false);
    expect(off.issues).toEqual([]);
  });

  it("still rejects a status claiming another data mode", () => {
    expect(statusSchema.safeParse({ enabled: true, data_mode: "nba_live" }).success).toBe(false);
  });

  it("accepts the context catalogue and rejects a non-synthetic one", () => {
    expect(contextSchema.safeParse(contextFixture()).success).toBe(true);
    expect(contextSchema.safeParse({ ...contextFixture(), data_mode: "nba" }).success).toBe(false);
  });
});

describe("fetch wrappers", () => {
  it("POSTs the request as JSON to /api/v1/assistant/ask and returns the parsed answer", async () => {
    const fetchMock = vi.fn(async () => json(askResponseFixture()));
    vi.stubGlobal("fetch", fetchMock);
    const answer = await askAssistant(REQUEST);
    expect(answer.answer_kind).toBe("grounded_answer");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/assistant/ask");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(REQUEST);
  });

  it("reads status and context from their routes", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url.endsWith("/status") ? json(statusFixture()) : json(contextFixture()),
    );
    vi.stubGlobal("fetch", fetchMock);
    expect((await getAssistantStatus()).ready).toBe(true);
    expect((await getAssistantContext()).teams[0].id).toBe("COMETS");
  });

  it("refuses to render a non-synthetic answer: a contract_mismatch, not data", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ ...askResponseFixture(), data_mode: "nba_live" })));
    const error = await askAssistant(REQUEST).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AssistantApiError);
    expect((error as AssistantApiError).code).toBe("contract_mismatch");
    expect(describeAssistantError(error).kind).toBe("contract");
  });
});

describe("error envelope mapping", () => {
  const cases: [string, number, string, RegExp][] = [
    ["assistant_disabled", 404, "disabled", /switched off/i],
    ["assistant_local_only", 403, "local_only", /only answers this machine/i],
    ["validation_error", 422, "validation", /rejected/i],
    ["assistant_dependencies_missing", 503, "dependencies", /packages/i],
    ["corpus_unavailable", 503, "corpus", /corpus/i],
    ["provider_unavailable", 503, "provider", /Ollama isn't reachable/],
    ["model_unavailable", 503, "model", /Model not available/],
    ["assistant_busy", 503, "busy", /Another answer is still running/],
    ["provider_timeout", 504, "provider_timeout", /took too long/],
    ["assistant_internal_error", 500, "internal", /internal error/i],
  ];

  it.each(cases)("%s (%i) becomes a specific message", async (code, status, kind, title) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({ error: { code, message: `server says ${code}`, request_id: "req-9" } }, status),
      ),
    );
    const error = await askAssistant(REQUEST).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toBeInstanceOf(AssistantApiError);
    expect((error as AssistantApiError).status).toBe(status);
    expect((error as AssistantApiError).requestId).toBe("req-9");

    const info = describeAssistantError(error, { chat: "qwen3.5:2b", embedding: "nomic-embed-text" });
    expect(info.kind).toBe(kind);
    expect(info.title).toMatch(title);
    expect(info.detail).toContain(`server says ${code}`);
    expect(info.code).toBe(code);
    expect(info.requestId).toBe("req-9");
  });

  it("gives the exact commands for the local-stack failures", async () => {
    const provider = describeAssistantError(new ApiError(503, "provider_unavailable", "down"));
    expect(provider.steps).toEqual(["ollama serve"]);
    expect(provider.detail).toContain("Start it with: ollama serve");
    const model = describeAssistantError(new ApiError(503, "model_unavailable", "missing"), {
      chat: "qwen3.5:2b",
      embedding: "nomic-embed-text",
    });
    expect(model.steps).toEqual(["ollama pull qwen3.5:2b", "ollama pull nomic-embed-text"]);
    expect(describeAssistantError(new ApiError(422, "validation_error", "bad")).retryable).toBe(false);
  });

  it("names the dev proxy's own 500 (backend down) as an unreachable API, with the start command", async () => {
    // Exactly what Next's /api/v1 rewrite answers when the backend is not running, or when
    // the proxy stops waiting: no content-type, a plain-text body, and no envelope.
    const proxy500 = () => new Response("Internal Server Error", { status: 500, statusText: "Internal Server Error" });
    vi.stubGlobal("fetch", vi.fn(async () => proxy500()));
    for (const call of [() => askAssistant(REQUEST), () => getAssistantStatus()]) {
      const error = (await call().catch((e: unknown) => e)) as AssistantApiError;
      expect(error).toBeInstanceOf(AssistantApiError);
      expect(error.code).toBe("upstream_unreachable");
      const info = describeAssistantError(error);
      expect(info.kind).toBe("network");
      expect(info.title).toBe("The Pivot API isn't reachable");
      expect(info.steps).toEqual([SERVER_FLAG_STEP]);
      expect(info.detail).toContain("500 Internal Server Error");
      expect(info.retryable).toBe(true);
      expect(info.status).toBe(500);
    }
  });

  it("treats any non-JSON 5xx the same way (a proxy's HTML 502, say)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>Bad gateway</html>", { status: 502, statusText: "Bad Gateway" })),
    );
    const error = (await askAssistant(REQUEST).catch((e: unknown) => e)) as AssistantApiError;
    expect(error.code).toBe("upstream_unreachable");
    expect(error.message).toBe("502 Bad Gateway");
    expect(describeAssistantError(error).kind).toBe("network");
  });

  it("keeps a generic message when the API itself answered: JSON 5xx, or an unknown envelope code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "boom" }, 500)));
    const notEnvelope = (await askAssistant(REQUEST).catch((e: unknown) => e)) as AssistantApiError;
    expect(notEnvelope.code).toBe("http_error");
    expect(describeAssistantError(notEnvelope)).toMatchObject({ kind: "http", title: "The request failed" });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ error: { code: "brand_new_code", message: "server says so", request_id: "r" } }, 500)),
    );
    const unknown = (await askAssistant(REQUEST).catch((e: unknown) => e)) as AssistantApiError;
    expect(describeAssistantError(unknown)).toMatchObject({ kind: "http", detail: "server says so", retryable: true });
  });

  it("leaves a non-JSON 4xx alone: that is not a proxy failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Not Found", { status: 404, statusText: "Not Found" })));
    const error = (await askAssistant(REQUEST).catch((e: unknown) => e)) as AssistantApiError;
    expect(error.code).toBe("http_error");
    expect(describeAssistantError(error).title).toBe("The assistant route wasn't found");
  });

  it("names an unreachable API (fetch's TypeError) plainly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const error = await askAssistant(REQUEST).catch((e: unknown) => e);
    const info = describeAssistantError(error);
    expect(info.kind).toBe("network");
    expect(info.title).toBe("The Pivot API isn't reachable");
  });
});

describe("cancellation", () => {
  /** A fetch that never answers, but honours its signal like the real one. */
  function hangingFetch() {
    return vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          const abort = () => reject(new DOMException("The operation was aborted.", "AbortError"));
          if (init?.signal?.aborted) abort();
          init?.signal?.addEventListener("abort", abort);
        }),
    );
  }

  it("times out on its own and says so — a failure, not a Stop", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const error = await askAssistant(REQUEST, { timeoutMs: 20 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AssistantTimeoutError);
    const info = describeAssistantError(error);
    expect(info.kind).toBe("client_timeout");
    expect(info.title).toBe("The local model took too long");
    expect(info.retryable).toBe(true);
  });

  it("forwards the caller's Stop as an AbortError, rendered as 'Stopped'", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const controller = new AbortController();
    const pending = askAssistant(REQUEST, { signal: controller.signal, timeoutMs: 10_000 }).catch(
      (e: unknown) => e,
    );
    controller.abort();
    const error = await pending;
    expect((error as Error).name).toBe("AbortError");
    const info = describeAssistantError(error);
    expect(info.kind).toBe("aborted");
    expect(info.title).toBe("Stopped");
  });

  it("honours a signal that was already aborted", async () => {
    const fetchMock = hangingFetch();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();
    const error = await askAssistant(REQUEST, { signal: controller.signal }).catch((e: unknown) => e);
    expect((error as Error).name).toBe("AbortError");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("the Next.js API proxy", () => {
  it("waits longer than the backend's answer budget, so a slow local answer is not cut off at 30 s", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const config = readFileSync(resolve(__dirname, "../../next.config.ts"), "utf8");
    const match = /proxyTimeout:\s*([\d_]+)/.exec(config);
    expect(match).not.toBeNull();
    const ms = Number((match?.[1] ?? "0").replace(/_/g, ""));
    expect(ms).toBeGreaterThan(150_000); // backend ASSISTANT_REQUEST_TIMEOUT_SECONDS default
  });
});
