/**
 * The Ask Pivot client: three fetch wrappers and one error translator.
 *
 * Why not `lib/api.ts`? Its `request()` takes no `AbortSignal`, and an answer from a local
 * model takes 10–60 seconds — the reader must be able to stop one, and the page must stop
 * waiting on its own if the server never replies. The error *class* is shared, though:
 * every failure here is an `ApiError` (or the `AssistantApiError` subclass that also keeps
 * the server's request id), so the app's React Query retry policy — never retry a 4xx —
 * applies unchanged.
 *
 * Cancellation, step by step (`askAssistant`):
 *  1. one internal `AbortController` drives the fetch;
 *  2. the caller's signal (the Stop button) is forwarded into it;
 *  3. a timer aborts it after `timeoutMs`, and records that *it* was the cause;
 *  4. when the fetch rejects, the recorded cause decides what is thrown: the caller's
 *     `AbortError` (a choice, rendered quietly as "Stopped"), or an `AssistantTimeoutError`
 *     (a failure, rendered with what to do next).
 * The abort also covers reading the response body, so a stalled stream cannot hang it.
 *
 * Aborting the fetch closes the HTTP connection; the backend notices the disconnect and
 * cancels the model call, which frees its single in-flight slot (otherwise the next
 * question would be refused with `assistant_busy`).
 */

import type { ZodType } from "zod";
import { ApiError } from "@/lib/api";
import {
  askResponseSchema,
  contextSchema,
  errorEnvelopeSchema,
  statusSchema,
} from "@/lib/assistant-schemas";
import type {
  AskRequest,
  AskResponse,
  AssistantContext,
  AssistantStatus,
} from "@/lib/assistant-types";

const BASE = "/api/v1/assistant";

/** How long the browser waits for one answer. The server's own budget is shorter (150s). */
export const ASK_TIMEOUT_MS = 180_000;

/** An API error that also carries the server's request id, for a bug report. */
export class AssistantApiError extends ApiError {
  requestId: string | null;
  constructor(status: number, code: string, message: string, requestId: string | null = null) {
    super(status, code, message);
    this.name = "AssistantApiError";
    this.requestId = requestId;
  }
}

/** The browser gave up waiting. Distinct from the server's own `provider_timeout` (504). */
export class AssistantTimeoutError extends Error {
  timeoutMs: number;
  constructor(timeoutMs: number) {
    super(`No answer after ${Math.round(timeoutMs / 1000)} seconds; the browser stopped waiting.`);
    this.name = "AssistantTimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

/** True for the error a fetch rejects with when its signal is aborted. */
export function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

/** 5xx statuses a proxy in front of the API answers with when the API itself did not. */
const PROXY_FAILURE_STATUSES: ReadonlySet<number> = new Set([500, 502, 503, 504]);

async function errorFromResponse(res: Response): Promise<AssistantApiError> {
  let code = "http_error";
  let message = `${res.status} ${res.statusText}`.trim();
  let requestId: string | null = null;
  try {
    const parsed = errorEnvelopeSchema.safeParse(await res.json());
    if (parsed.success) {
      code = parsed.data.error.code;
      message = parsed.data.error.message;
      requestId = parsed.data.error.request_id ?? null;
    }
  } catch {
    // Not JSON at all. Every call goes through Next's same-origin `/api/v1` rewrite, and when
    // the backend is down (or the proxy gives up waiting) Next answers *itself*: HTTP 500
    // with the plain-text body "Internal Server Error". fetch() resolves, so the TypeError
    // branch never sees it — name it here so the reader gets the "not reachable" steps.
    if (PROXY_FAILURE_STATUSES.has(res.status)) code = "upstream_unreachable";
  }
  return new AssistantApiError(res.status, code, message, requestId);
}

function validated<T>(path: string, payload: unknown, schema: ZodType): T {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    throw new AssistantApiError(
      502,
      "contract_mismatch",
      `The server returned an unexpected shape for ${path}` +
        (first ? ` (${first.path.join(".") || "root"}: ${first.message})` : "") +
        ". This is a bug, not a gap in the corpus — the response was not rendered.",
    );
  }
  return parsed.data as T;
}

async function call<T>(
  path: string,
  schema: ZodType,
  init: RequestInit & { signal?: AbortSignal },
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) throw await errorFromResponse(res);
  return validated<T>(path, await res.json(), schema);
}

export function getAssistantStatus(signal?: AbortSignal): Promise<AssistantStatus> {
  return call<AssistantStatus>("/status", statusSchema, { method: "GET", signal });
}

/** 404 `assistant_disabled` when the server flag is off. */
export function getAssistantContext(signal?: AbortSignal): Promise<AssistantContext> {
  return call<AssistantContext>("/context", contextSchema, { method: "GET", signal });
}

export async function askAssistant(
  body: AskRequest,
  { signal, timeoutMs = ASK_TIMEOUT_MS }: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<AskResponse> {
  // Already stopped: an aborted signal never fires `abort` again, so do not start at all.
  if (signal?.aborted) throw new DOMException("The request was stopped.", "AbortError");

  const controller = new AbortController();
  let timedOut = false;

  // Step 2: forward the caller's Stop.
  const forward = () => controller.abort();
  signal?.addEventListener("abort", forward, { once: true });

  // Step 3: the browser's own deadline.
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await call<AskResponse>("/ask", askResponseSchema, {
      method: "POST",
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (error) {
    // Step 4: say which of the two aborts this was.
    if (timedOut && isAbortError(error)) throw new AssistantTimeoutError(timeoutMs);
    if (isAbortError(error) || signal?.aborted) {
      throw new DOMException("The request was stopped.", "AbortError");
    }
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forward);
  }
}

/* ---------------------------------------------------------------- error copy */

export type AssistantErrorKind =
  | "aborted"
  | "client_timeout"
  | "network"
  | "disabled"
  | "local_only"
  | "validation"
  | "dependencies"
  | "corpus"
  | "provider"
  | "model"
  | "busy"
  | "provider_timeout"
  | "internal"
  | "contract"
  | "http"
  | "unknown";

export interface AssistantErrorInfo {
  kind: AssistantErrorKind;
  title: string;
  detail: string;
  /** Exact commands or actions, in order. Rendered as code lines. */
  steps?: string[];
  /** Whether sending the same request again could succeed without the reader changing it. */
  retryable: boolean;
  code?: string;
  status?: number;
  requestId?: string | null;
}

/** Model names for the copy; defaults are the backend's configured defaults. */
export interface ModelNames {
  chat?: string | null;
  embedding?: string | null;
}

export const INSTALL_STEP = "cd backend && .venv/bin/pip install -e '.[dev,assistant]'";
export const SERVER_FLAG_STEP =
  "cd backend && ASSISTANT_ENABLED=true .venv/bin/uvicorn app.main:app --reload --port 8000";

/** No reply from the API itself: the backend is down, or the dev proxy cannot reach it. */
function unreachable(): AssistantErrorInfo {
  return {
    kind: "network",
    title: "The Pivot API isn't reachable",
    detail:
      "The browser could not reach /api/v1 — the backend is probably not running, or the dev server cannot proxy to it.",
    steps: [SERVER_FLAG_STEP],
    retryable: true,
  };
}

/**
 * Turn anything an ask/status call can throw into words a GM can act on.
 *
 * The server's message is always kept (`detail`), because it is specific — the copy here
 * only adds a plain title and the next step. Unknown codes fall through to a generic title
 * with the server's message intact, never to a guess.
 */
export function describeAssistantError(error: unknown, models: ModelNames = {}): AssistantErrorInfo {
  const chat = models.chat || "qwen3.5:2b";
  const embedding = models.embedding || "nomic-embed-text";

  if (isAbortError(error)) {
    return {
      kind: "aborted",
      title: "Stopped",
      detail: "You stopped this answer before it finished. Nothing was kept from it.",
      retryable: true,
    };
  }
  if (error instanceof AssistantTimeoutError) {
    return {
      kind: "client_timeout",
      title: "The local model took too long",
      detail: `${error.message} A first question after a cold start loads the model and embeds the corpus, which can take a while on a laptop.`,
      steps: ["ollama ps", `ollama run ${chat} "hello"`],
      retryable: true,
    };
  }
  if (error instanceof TypeError) {
    // fetch() rejects with a TypeError when no HTTP response arrived at all.
    return unreachable();
  }
  if (error instanceof ApiError) {
    const requestId = error instanceof AssistantApiError ? error.requestId : null;
    const base = { code: error.code, status: error.status, requestId, detail: error.message };
    switch (error.code) {
      case "upstream_unreachable": {
        // The dev proxy's own non-JSON 5xx (see errorFromResponse): same cause, same fix.
        const info = unreachable();
        return {
          ...info,
          ...base,
          detail: `${info.detail} The dev server answered ${error.message} on its behalf, which it does when it cannot reach the backend or stops waiting for it.`,
        };
      }
      case "assistant_disabled":
        return {
          ...base,
          kind: "disabled",
          title: "Ask Pivot is switched off on the server",
          steps: [SERVER_FLAG_STEP],
          retryable: false,
        };
      case "assistant_local_only":
        return {
          ...base,
          kind: "local_only",
          title: "Ask Pivot only answers this machine",
          detail: `${error.message} The inference route is unauthenticated, so it serves loopback clients only. Open Pivot at http://localhost:3000 on the machine running the backend.`,
          retryable: false,
        };
      case "validation_error":
        return {
          ...base,
          kind: "validation",
          title: "The request was rejected",
          detail: `${error.message} Sending it again unchanged will fail the same way — adjust the question, team or constraints first.`,
          retryable: false,
        };
      case "assistant_dependencies_missing":
        return {
          ...base,
          kind: "dependencies",
          title: "The assistant's packages aren't installed",
          steps: [INSTALL_STEP, "Restart the backend afterwards."],
          retryable: true,
        };
      case "corpus_unavailable":
        return {
          ...base,
          kind: "corpus",
          title: "The fictional corpus didn't load",
          retryable: true,
        };
      case "provider_unavailable":
        return {
          ...base,
          kind: "provider",
          title: "Ollama isn't reachable",
          detail: `${error.message} Start it with: ollama serve`,
          steps: ["ollama serve"],
          retryable: true,
        };
      case "model_unavailable":
        return {
          ...base,
          kind: "model",
          title: "Model not available",
          steps: [`ollama pull ${chat}`, `ollama pull ${embedding}`],
          retryable: true,
        };
      case "assistant_busy":
        return {
          ...base,
          kind: "busy",
          title: "Another answer is still running",
          detail: `${error.message} The local model answers one question at a time — wait for the other one (in this tab or another) to finish, then retry.`,
          retryable: true,
        };
      case "provider_timeout":
        return {
          ...base,
          kind: "provider_timeout",
          title: "The local model took too long",
          detail: `${error.message} A cold model is slowest on its first answer; retrying once it is loaded is usually faster.`,
          steps: ["ollama ps"],
          retryable: true,
        };
      case "assistant_internal_error":
        return {
          ...base,
          kind: "internal",
          title: "Ask Pivot hit an internal error",
          retryable: true,
        };
      case "contract_mismatch":
        return {
          ...base,
          kind: "contract",
          title: "The answer arrived in an unexpected shape",
          retryable: false,
        };
      default:
        return {
          ...base,
          kind: "http",
          title: error.status === 404 ? "The assistant route wasn't found" : "The request failed",
          retryable: error.status >= 500,
        };
    }
  }
  return {
    kind: "unknown",
    title: "Something went wrong",
    detail: error instanceof Error ? error.message : String(error),
    retryable: true,
  };
}
