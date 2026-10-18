/**
 * The Ask Pivot conversation, kept in this browser tab only.
 *
 * **Where it lives.** `sessionStorage`, under one key. That is the privacy promise the
 * feature makes (docs/pivot/assistant-v2-architecture.md §5): the server stores nothing, and
 * the conversation lasts as long as the tab — a reload keeps it, closing the tab ends it, and
 * a second tab starts its own. Nothing here is sent anywhere except as the bounded `history`
 * of the next question.
 *
 * **How React reads it.** A `useSyncExternalStore` store, the same idiom as
 * `lib/favoriteTeam.ts`: the server render (and the hydration render) see `EMPTY_SESSION`,
 * the stored conversation arrives in the commit after, and nothing calls setState in an
 * effect. Unlike the favourite-team store, **memory is the source of truth** and storage is
 * only its copy: when `setItem` throws (Safari private mode, a full quota) the conversation
 * keeps working for the life of the page; it simply does not survive a reload. A store that
 * re-read storage on every snapshot would silently drop every write in that case.
 *
 * **What is sent back to the model.** `buildHistory` — the last 12 *answered* messages, as
 * `{role, content}`. An assistant message's content is its answer text. Errors, stopped
 * turns and questions that never got an answer are left out: the model must not be told it
 * said something it did not say, or be shown a question it never answered.
 */

import { useSyncExternalStore } from "react";
import { askResponseSchema } from "@/lib/assistant-schemas";
import {
  MAX_HISTORY_MESSAGE_CHARS,
  sanitizePriorities,
  type AskRequest,
  type AskResponse,
  type AssistantMode,
  type ChatMessageIn,
  type ChatRole,
  type Priorities,
} from "@/lib/assistant-types";
import type { AssistantErrorInfo } from "@/lib/assistant-api";

export const SESSION_KEY = "pivot.assistant.session.v1";
/** Messages kept in the tab. Older ones fall off the top. */
export const MAX_STORED_MESSAGES = 40;
/** Prior messages sent with each question. The backend accepts up to 20 and keeps fewer. */
export const HISTORY_FOR_API = 12;

export interface StoredMessage {
  id: string;
  role: ChatRole;
  /** The question, or the answer text. Empty for an assistant turn that failed. */
  content: string;
  created_at: string;
  /** The exact request this turn sent — what Retry re-sends. */
  request?: AskRequest;
  response?: AskResponse;
  error?: AssistantErrorInfo;
}

export interface AssistantSession {
  version: 1;
  team_id: string | null;
  mode: AssistantMode;
  priorities: Priorities;
  constraints: string[];
  messages: StoredMessage[];
}

export const EMPTY_SESSION: AssistantSession = Object.freeze({
  version: 1,
  team_id: null,
  mode: "research",
  priorities: {},
  constraints: [],
  messages: [],
}) as AssistantSession;

/* -------------------------------------------------------------- pure helpers */

/** Append, then cap. A reply never leads the thread: an orphaned answer is dropped too. */
export function appendMessages(
  session: AssistantSession,
  ...messages: StoredMessage[]
): AssistantSession {
  const next = [...session.messages, ...messages].slice(-MAX_STORED_MESSAGES);
  while (next.length && next[0].role === "assistant") next.shift();
  return { ...session, messages: next };
}

/** A user message counts as answered when the very next message is an assistant answer. */
function answeredPairs(messages: StoredMessage[]): StoredMessage[] {
  const kept: StoredMessage[] = [];
  for (let i = 0; i < messages.length - 1; i++) {
    const question = messages[i];
    const reply = messages[i + 1];
    if (question.role === "user" && reply.role === "assistant" && reply.response && !reply.error) {
      kept.push(question, reply);
      i++;
    }
  }
  return kept;
}

function clip(content: string): string {
  // The API rejects a history message over 6,000 characters (a 422, not a truncation), so
  // the client clips its own copy rather than lose the whole turn. Answers are capped at
  // ~900 tokens, so in practice this never fires.
  return content.length > MAX_HISTORY_MESSAGE_CHARS
    ? `${content.slice(0, MAX_HISTORY_MESSAGE_CHARS - 1)}…`
    : content;
}

/** The `history` for the next request: the last 12 answered messages, oldest first. */
export function buildHistory(messages: StoredMessage[], limit = HISTORY_FOR_API): ChatMessageIn[] {
  return answeredPairs(messages)
    .slice(-limit)
    .filter((m) => m.content.trim().length > 0)
    .map((m) => ({ role: m.role, content: clip(m.content) }));
}

/**
 * The priorities in force on the last *answered* turn, or null on a first question. The
 * backend compares them with the current ones and names what changed ("Spacing: high →
 * must preserve"), which is how a team switch or a re-weighting becomes visible to it.
 */
export function previousPriorities(messages: StoredMessage[]): Priorities | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "assistant" && m.response && m.request) return { ...m.request.priorities };
  }
  return null;
}

export function newMessageId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    /* insecure context: fall through */
  }
  return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/* ----------------------------------------------------------------- decoding */

const MODES: readonly AssistantMode[] = ["research", "roster_fit"];

function decodeMessage(raw: unknown): StoredMessage | null {
  if (typeof raw !== "object" || raw === null) return null;
  const m = raw as Record<string, unknown>;
  if (typeof m.id !== "string" || (m.role !== "user" && m.role !== "assistant")) return null;
  if (typeof m.content !== "string" || typeof m.created_at !== "string") return null;
  const message: StoredMessage = {
    id: m.id,
    role: m.role,
    content: m.content,
    created_at: m.created_at,
  };
  if (typeof m.request === "object" && m.request !== null) message.request = m.request as AskRequest;
  if (m.response !== undefined) {
    // A stored answer is re-validated with the same schema the network response passed:
    // an entry written by an older build, or edited by hand, is dropped — never rendered
    // half-shaped, and never rendered as anything but synthetic data.
    const parsed = askResponseSchema.safeParse(m.response);
    if (!parsed.success) return null;
    message.response = parsed.data as AskResponse;
  }
  if (typeof m.error === "object" && m.error !== null) {
    const e = m.error as Record<string, unknown>;
    if (typeof e.title === "string" && typeof e.detail === "string") {
      message.error = m.error as AssistantErrorInfo;
    }
  }
  if (message.role === "assistant" && !message.response && !message.error) return null;
  return message;
}

/** Parse a stored string. Anything malformed yields the empty session, never a throw. */
export function decodeSession(raw: string | null): AssistantSession {
  if (!raw) return EMPTY_SESSION;
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value?.version !== 1) return EMPTY_SESSION;
    const messages = Array.isArray(value.messages)
      ? value.messages.map(decodeMessage).filter((m): m is StoredMessage => m !== null)
      : [];
    return appendMessages(
      {
        version: 1,
        team_id: typeof value.team_id === "string" ? value.team_id : null,
        mode: MODES.includes(value.mode as AssistantMode) ? (value.mode as AssistantMode) : "research",
        priorities: sanitizePriorities(value.priorities as Record<string, string>),
        constraints: Array.isArray(value.constraints)
          ? value.constraints.filter((c): c is string => typeof c === "string")
          : [],
        messages: [],
      },
      ...messages,
    );
  } catch {
    return EMPTY_SESSION;
  }
}

/* -------------------------------------------------------------------- store */

const listeners = new Set<() => void>();
/** The live session. `null` until first read in the browser. */
let current: AssistantSession | null = null;
/** False once a write has failed: the conversation is then memory-only for this page. */
let persisting = true;

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function load(): AssistantSession {
  try {
    return decodeSession(storage()?.getItem(SESSION_KEY) ?? null);
  } catch {
    return EMPTY_SESSION;
  }
}

function snapshot(): AssistantSession {
  if (current === null) current = load();
  return current;
}

function serverSnapshot(): AssistantSession {
  return EMPTY_SESSION;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function write(next: AssistantSession) {
  current = next;
  try {
    const store = storage();
    if (!store) throw new Error("sessionStorage unavailable");
    store.setItem(SESSION_KEY, JSON.stringify(next));
    persisting = true;
  } catch {
    // Private mode or a full quota: keep going in memory. The page says so (see
    // `isPersisting`), rather than pretending the conversation will survive a reload.
    persisting = false;
  }
  for (const listener of listeners) listener();
}

export function useAssistantSession(): AssistantSession {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

export function getSession(): AssistantSession {
  return snapshot();
}

export function updateSession(update: (session: AssistantSession) => AssistantSession): void {
  write(update(snapshot()));
}

/** Clear the thread, keep the team, mode, priorities and constraints. */
export function clearConversation(): void {
  updateSession((s) => ({ ...s, messages: [] }));
}

/** Whether the last write reached sessionStorage. */
export function isPersisting(): boolean {
  return persisting;
}

export function usePersisting(): boolean {
  return useSyncExternalStore(subscribe, isPersisting, () => true);
}

/** Tests only: forget the in-memory copy so the next read comes from storage again. */
export function resetSessionStoreForTests(): void {
  current = null;
  persisting = true;
}
