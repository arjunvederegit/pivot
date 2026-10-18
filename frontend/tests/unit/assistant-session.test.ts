/**
 * The tab-local conversation store.
 *
 * What is pinned: the conversation survives a reload of the tab (sessionStorage), is
 * capped, never sends a failed or unanswered turn back to the model as history, and keeps
 * working — in memory — when the browser refuses storage outright.
 */

import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  EMPTY_SESSION,
  MAX_STORED_MESSAGES,
  SESSION_KEY,
  appendMessages,
  buildHistory,
  clearConversation,
  decodeSession,
  getSession,
  isPersisting,
  previousPriorities,
  resetSessionStoreForTests,
  updateSession,
  useAssistantSession,
  type AssistantSession,
  type StoredMessage,
} from "@/lib/assistant-session";
import type { AskRequest } from "@/lib/assistant-types";
import { askResponseFixture } from "./assistant-fixtures";

const realStorage = window.sessionStorage;

function request(priorities: AskRequest["priorities"] = {}): AskRequest {
  return {
    question: "q",
    mode: "research",
    team_id: null,
    priorities,
    previous_priorities: null,
    constraints: [],
    history: [],
  };
}

let n = 0;
function user(content: string, req = request()): StoredMessage {
  return { id: `u${n++}`, role: "user", content, created_at: "2026-10-17T10:00:00Z", request: req };
}
function answer(content: string, req = request()): StoredMessage {
  return {
    id: `a${n++}`,
    role: "assistant",
    content,
    created_at: "2026-10-17T10:00:05Z",
    request: req,
    response: askResponseFixture({ answer: content }),
  };
}
function failure(): StoredMessage {
  return {
    id: `e${n++}`,
    role: "assistant",
    content: "",
    created_at: "2026-10-17T10:00:05Z",
    request: request(),
    error: { kind: "provider", title: "Ollama isn't reachable", detail: "down", retryable: true },
  };
}

beforeEach(() => {
  realStorage.clear();
  resetSessionStoreForTests();
});

afterEach(() => {
  Object.defineProperty(window, "sessionStorage", { value: realStorage, configurable: true, writable: true });
  resetSessionStoreForTests();
});

describe("persistence", () => {
  it("writes to sessionStorage under the versioned key and reads it back after a reload", () => {
    updateSession((s) => ({ ...s, team_id: "COMETS", mode: "roster_fit", priorities: { spacing: "high" } }));
    updateSession((s) => appendMessages(s, user("Who guards the point?"), answer("Reed [S03].")));
    expect(realStorage.getItem(SESSION_KEY)).toContain("Who guards the point?");

    resetSessionStoreForTests(); // a reload: memory gone, storage kept
    const restored = getSession();
    expect(restored.team_id).toBe("COMETS");
    expect(restored.mode).toBe("roster_fit");
    expect(restored.priorities).toEqual({ spacing: "high" });
    expect(restored.messages.map((m) => m.content)).toEqual(["Who guards the point?", "Reed [S03]."]);
  });

  it("treats a missing, malformed or other-version entry as an empty session", () => {
    expect(decodeSession(null)).toBe(EMPTY_SESSION);
    expect(decodeSession("{not json")).toBe(EMPTY_SESSION);
    expect(decodeSession(JSON.stringify({ version: 2, messages: [] }))).toBe(EMPTY_SESSION);
  });

  it("drops a stored answer that is no longer synthetic_demo instead of rendering it", () => {
    const tampered: AssistantSession = {
      ...EMPTY_SESSION,
      messages: [user("q"), { ...answer("a"), response: { ...askResponseFixture(), data_mode: "nba" as "synthetic_demo" } }],
    };
    const decoded = decodeSession(JSON.stringify(tampered));
    expect(decoded.messages.map((m) => m.role)).toEqual(["user"]);
  });

  it("drops priority keys and levels the API would reject", () => {
    const decoded = decodeSession(
      JSON.stringify({ version: 1, priorities: { spacing: "high", vibes: "high", size: "max" }, messages: [] }),
    );
    expect(decoded.priorities).toEqual({ spacing: "high" });
  });

  it("serves the empty session to the server render, whatever is stored", () => {
    updateSession((s) => appendMessages(s, user("stored question"), answer("stored answer")));
    function Probe() {
      const session = useAssistantSession();
      return createElement("span", null, `messages:${session.messages.length}`);
    }
    expect(renderToString(createElement(Probe))).toContain("messages:0");
  });

  it("clearing the conversation keeps the team, mode, priorities and constraints", () => {
    updateSession((s) => ({ ...s, team_id: "COMETS", constraints: ["No veterans"], priorities: { size: "low" } }));
    updateSession((s) => appendMessages(s, user("q"), answer("a")));
    clearConversation();
    const s = getSession();
    expect(s.messages).toEqual([]);
    expect(s.team_id).toBe("COMETS");
    expect(s.constraints).toEqual(["No veterans"]);
    expect(s.priorities).toEqual({ size: "low" });
  });
});

describe("cap", () => {
  it(`keeps the last ${MAX_STORED_MESSAGES} messages, never leading with a reply`, () => {
    let s: AssistantSession = EMPTY_SESSION;
    for (let i = 0; i < 25; i++) s = appendMessages(s, user(`q${i}`), answer(`a${i}`));
    expect(s.messages.length).toBeLessThanOrEqual(MAX_STORED_MESSAGES);
    expect(s.messages[0].role).toBe("user");
    expect(s.messages[s.messages.length - 1].content).toBe("a24");

    // An odd overflow would leave an orphaned answer at the top; it is dropped too.
    const odd = appendMessages(s, user("extra"));
    expect(odd.messages[0].role).toBe("user");
    expect(odd.messages.length).toBeLessThanOrEqual(MAX_STORED_MESSAGES);
  });
});

describe("history sent to the model", () => {
  it("sends answered turns only — never an error, a stop, or an unanswered question", () => {
    const messages = [
      user("first"),
      answer("first answer"),
      user("this one failed"),
      failure(),
      user("second"),
      answer("second answer"),
      user("never answered"),
    ];
    expect(buildHistory(messages)).toEqual([
      { role: "user", content: "first" },
      { role: "assistant", content: "first answer" },
      { role: "user", content: "second" },
      { role: "assistant", content: "second answer" },
    ]);
  });

  it("sends at most the last 12 messages, oldest first", () => {
    const messages: StoredMessage[] = [];
    for (let i = 0; i < 10; i++) messages.push(user(`q${i}`), answer(`a${i}`));
    const history = buildHistory(messages);
    expect(history).toHaveLength(12);
    expect(history[0]).toEqual({ role: "user", content: "q4" });
    expect(history[11]).toEqual({ role: "assistant", content: "a9" });
  });

  it("clips a history message to the API's 6,000-character bound instead of failing the turn", () => {
    const long = "x".repeat(7000);
    const [, reply] = buildHistory([user("q"), answer(long)]);
    expect(reply.content.length).toBe(6000);
  });

  it("reports the priorities of the last answered turn as previous_priorities", () => {
    expect(previousPriorities([])).toBeNull();
    const messages = [
      user("q1"),
      answer("a1", request({ spacing: "high" })),
      user("q2"),
      failure(), // a failed turn's priorities never reached the model
    ];
    expect(previousPriorities(messages)).toEqual({ spacing: "high" });
  });
});

describe("storage that throws", () => {
  it("keeps working in memory, and says it is not persisting", () => {
    const throwing = {
      getItem: () => {
        throw new DOMException("denied", "SecurityError");
      },
      setItem: () => {
        throw new DOMException("quota", "QuotaExceededError");
      },
      removeItem: () => {},
      clear: () => {},
      key: () => null,
      length: 0,
    };
    Object.defineProperty(window, "sessionStorage", { value: throwing, configurable: true, writable: true });
    resetSessionStoreForTests();

    expect(() => getSession()).not.toThrow();
    expect(getSession()).toBe(EMPTY_SESSION);
    expect(() => updateSession((s) => appendMessages(s, user("still works"), answer("yes")))).not.toThrow();
    expect(getSession().messages).toHaveLength(2);
    expect(isPersisting()).toBe(false);
  });
});
