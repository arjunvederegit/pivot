/**
 * The Ask Pivot workspace, end to end inside jsdom, against a mocked `fetch`.
 *
 * Pinned behaviour: a question shows as the GM's turn, then a loading card, then an answer
 * carrying the FICTIONAL DATA tag; a citation opens the passage it points at (excerpt,
 * author, date); a local-stack failure renders as the specific fix, and Retry re-sends the
 * exact same request; a switched-off server renders a plain panel, not a broken chat; and
 * Enter sends while Shift+Enter (and Enter during IME composition) does not.
 *
 * RTL's auto-cleanup does not run in this suite (globals are off), so every test unmounts
 * through `cleanup()` in `afterEach`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantWorkspace } from "@/components/assistant/workspace";
import { resetPreferencesForTests } from "@/lib/assistant-preferences";
import { SESSION_KEY, getSession, resetSessionStoreForTests } from "@/lib/assistant-session";
import type { AskRequest } from "@/lib/assistant-types";
import { askResponseFixture, contextFixture, json, statusFixture } from "./assistant-fixtures";

const COMPOSER = "Ask Pivot a question about the fictional league";

type AskHandler = (body: AskRequest, init?: RequestInit) => Promise<Response>;

/** What Next's /api/v1 rewrite answers on its own when the backend is down. */
const proxy500 = () => new Response("Internal Server Error", { status: 500, statusText: "Internal Server Error" });

/** An /ask that never answers, but rejects like fetch when its signal is aborted. */
const hangingAsk: AskHandler = (_body, init) =>
  new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError")));
  });

function mockApi({
  status = statusFixture() as unknown,
  statusHandler,
  ask,
}: { status?: unknown; statusHandler?: () => Promise<Response>; ask?: AskHandler } = {}) {
  const asked: AskRequest[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/assistant/status")) return statusHandler ? statusHandler() : json(status);
    if (url.endsWith("/assistant/context")) return json(contextFixture());
    if (url.endsWith("/assistant/ask")) {
      const body = JSON.parse(String(init?.body)) as AskRequest;
      asked.push(body);
      return ask ? ask(body, init) : json(askResponseFixture());
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, asked };
}

function renderWorkspace({ strict = false }: { strict?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (
    <QueryClientProvider client={client}>
      <AssistantWorkspace />
    </QueryClientProvider>
  );
  return { ...render(strict ? <StrictMode>{tree}</StrictMode> : tree), client };
}

async function readyComposer(): Promise<HTMLTextAreaElement> {
  const box = (await screen.findByLabelText(COMPOSER)) as HTMLTextAreaElement;
  await waitFor(() => expect(box).not.toBeDisabled());
  return box;
}

function typeAndSend(box: HTMLTextAreaElement, text: string) {
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  resetSessionStoreForTests();
  resetPreferencesForTests();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("asking a question", () => {
  it("shows the GM's turn, then a loading card, then an answer tagged FICTIONAL DATA", async () => {
    const reply = deferred<Response>();
    const { asked } = mockApi({ ask: () => reply.promise });
    renderWorkspace();

    const box = await readyComposer();
    typeAndSend(box, "Who defends the point of attack best?");

    const log = screen.getByRole("log");
    expect(within(log).getByText("Who defends the point of attack best?")).toBeInTheDocument();
    expect(within(log).getByText("Researching the fictional corpus…")).toBeInTheDocument();
    expect(box.value).toBe("");
    expect(document.activeElement).toBe(box);

    await act(async () => reply.resolve(json(askResponseFixture())));

    expect(await within(log).findByText("Fictional data")).toBeInTheDocument();
    expect(within(log).getByText("Grounded answer")).toBeInTheDocument();
    expect(within(log).getByText("Citations present — not fact-checked")).toBeInTheDocument();
    expect(within(log).queryByText("Researching the fictional corpus…")).toBeNull();

    // The request carried the context, and no history on a first question.
    expect(asked).toHaveLength(1);
    expect(asked[0]).toMatchObject({
      question: "Who defends the point of attack best?",
      mode: "research",
      team_id: null,
      history: [],
      previous_priorities: null,
    });
  });

  it("marks a citation to an id that was not retrieved, and does not make it clickable", async () => {
    mockApi();
    renderWorkspace();
    typeAndSend(await readyComposer(), "Compare Reed and Vance");
    const log = screen.getByRole("log");
    await within(log).findByText("Fictional data");
    // The fixture answer cites [S05, S06]; only S05 was retrieved.
    expect(within(log).queryByRole("button", { name: /^Source S06/ })).toBeNull();
    expect(within(log).getByText(/cited, but not retrieved/)).toBeInTheDocument();
  });

  it("sends the previous answered turn as history on the next question", async () => {
    const { asked } = mockApi();
    renderWorkspace();
    const box = await readyComposer();
    typeAndSend(box, "First question");
    await within(screen.getByRole("log")).findByText("Fictional data");
    typeAndSend(box, "Second question");
    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[1].history).toEqual([
      { role: "user", content: "First question" },
      { role: "assistant", content: askResponseFixture().answer },
    ]);
  });
});

describe("the evidence inspector", () => {
  it("opens the cited passage: excerpt, author and date", async () => {
    mockApi();
    renderWorkspace();
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");
    const log = screen.getByRole("log");
    await within(log).findByText("Fictional data");
    const inspector = screen.getByRole("complementary", { name: "Evidence inspector" });

    fireEvent.click(within(log).getByRole("button", { name: "Source S05: Theo Vance — shooting volume and defensive role" }));
    expect(within(inspector).getByText(/Vance takes seven threes a game/)).toBeInTheDocument();
    expect(within(inspector).getAllByText("Priya Raman").length).toBeGreaterThan(0);

    fireEvent.click(within(log).getAllByRole("button", { name: "Source S03: Niko Reed — defensive evaluation" })[0]);
    expect(within(inspector).getByText(/Reed navigates screens as well as any guard/)).toBeInTheDocument();
    expect(within(inspector).getByText("Dana Whitfield")).toBeInTheDocument();
    expect(within(inspector).getAllByText(/2026-02-14/).length).toBeGreaterThan(0);
    expect(within(inspector).getByText(/Fictional Ask Pivot research corpus · synthetic_demo/)).toBeInTheDocument();
  });

  it("moves through passages with the arrow keys", async () => {
    mockApi();
    renderWorkspace();
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");
    await within(screen.getByRole("log")).findByText("Fictional data");
    const inspector = screen.getByRole("complementary", { name: "Evidence inspector" });
    const options = within(inspector).getAllByRole("option");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(options[0]).toHaveAttribute("tabindex", "0");
    expect(options[1]).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(options[0], { key: "ArrowDown" });
    const after = within(inspector).getAllByRole("option");
    expect(after[1]).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(after[1]);
    expect(within(inspector).getByText(/Vance takes seven threes a game/)).toBeInTheDocument();
  });

  it("opens as a drawer below xl, and Escape returns focus to the citation", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    mockApi();
    renderWorkspace();
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");
    const log = screen.getByRole("log");
    await within(log).findByText("Fictional data");

    const chip = within(log).getAllByRole("button", { name: "Source S03: Niko Reed — defensive evaluation" })[0];
    chip.focus();
    fireEvent.click(chip);
    const dialog = await screen.findByRole("dialog", { name: "Evidence" });
    expect(within(dialog).getByText(/Reed navigates screens/)).toBeInTheDocument();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close evidence" }));

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(chip);
  });
});

describe("failures", () => {
  it("names a 503 provider_unavailable with its fix, and Retry re-sends the same request", async () => {
    let calls = 0;
    const { asked } = mockApi({
      ask: async () => {
        calls += 1;
        return calls === 1
          ? json(
              {
                error: {
                  code: "provider_unavailable",
                  message: "Ollama is not reachable at http://localhost:11434.",
                  request_id: "req-1",
                },
              },
              503,
            )
          : json(askResponseFixture());
      },
    });
    renderWorkspace();
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");

    const log = screen.getByRole("log");
    expect(await within(log).findByText("Ollama isn't reachable")).toBeInTheDocument();
    expect(within(log).getByText(/Start it with: ollama serve/)).toBeInTheDocument();
    expect(within(log).getByText("ollama serve", { selector: "code" })).toBeInTheDocument();

    const retry = within(log).getByRole("button", { name: "Retry" });
    await waitFor(() => expect(retry).not.toBeDisabled());
    fireEvent.click(retry);

    expect(await within(log).findByText("Fictional data")).toBeInTheDocument();
    expect(within(log).queryByText("Ollama isn't reachable")).toBeNull();
    expect(asked).toHaveLength(2);
    expect(asked[1]).toEqual(asked[0]);
    // The question is not duplicated by the retry.
    expect(within(log).getAllByText("Who defends the point of attack best?")).toHaveLength(1);
  });

  it("renders Stop as a quiet 'Stopped' turn, not an error", async () => {
    mockApi({
      ask: (_body, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("The operation was aborted.", "AbortError")),
          );
        }),
    });
    renderWorkspace();
    typeAndSend(await readyComposer(), "A slow one");
    const log = screen.getByRole("log");
    fireEvent.click(within(log).getByRole("button", { name: "Stop" }));
    expect(await within(log).findByText("Stopped.")).toBeInTheDocument();
    expect(within(log).getByRole("button", { name: "Ask again" })).toBeInTheDocument();
  });

  it("shows a clean panel, not a chat, when the server flag is off", async () => {
    const { fetchMock } = mockApi({ status: { enabled: false } });
    renderWorkspace();
    expect(await screen.findByText("Ask Pivot is not enabled on this backend")).toBeInTheDocument();
    expect(screen.queryByLabelText(COMPOSER)).toBeNull();
    expect(screen.queryByRole("log")).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/context"))).toBe(false);
  });

  it("lists what is missing, with setup steps, and keeps the composer locked when not ready", async () => {
    mockApi({
      status: statusFixture({
        ready: false,
        issues: ["Ollama is not reachable at http://localhost:11434"],
        provider: { ...statusFixture().provider!, reachable: false },
      }),
    });
    renderWorkspace();
    expect(await screen.findByText("Ollama is not reachable at http://localhost:11434")).toBeInTheDocument();
    expect(screen.getByText("ollama serve")).toBeInTheDocument();
    expect(screen.getByText("ollama pull qwen3.5:2b")).toBeInTheDocument();
    expect(screen.getByText("ollama pull nomic-embed-text")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
    expect(screen.getByLabelText(COMPOSER)).toBeDisabled();
  });

  it("does not blame unpulled models when Ollama answered but its model list failed", async () => {
    mockApi({
      status: statusFixture({
        ready: false,
        issues: ["Ollama at http://localhost:11434 answered 500 to /api/tags."],
        provider: {
          ...statusFixture().provider!,
          reachable: true,
          chat_model_available: false,
          embedding_model_available: false,
          error: "Ollama at http://localhost:11434 answered 500 to /api/tags.",
        },
      }),
    });
    renderWorkspace();
    expect(await screen.findByText("Ollama at http://localhost:11434 answered 500 to /api/tags.")).toBeInTheDocument();
    expect(screen.getByText("ollama pull qwen3.5:2b")).toBeInTheDocument(); // still listed as a setup step…
    expect(screen.queryByText(/likely needed/)).toBeNull(); // …but not flagged as the cause
  });
});

describe("the composer", () => {
  it("sends on Enter, but not on Shift+Enter or while an IME is composing", async () => {
    const { asked } = mockApi();
    renderWorkspace();
    const box = await readyComposer();

    fireEvent.change(box, { target: { value: "Line one" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(asked).toHaveLength(0);
    expect(box.value).toBe("Line one");

    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0].question).toBe("Line one");
  });

  it("loads a team's default priorities and constraints when the team is chosen", async () => {
    const { asked } = mockApi();
    renderWorkspace();
    const box = await readyComposer();
    fireEvent.change(await screen.findByLabelText("Fictional team"), { target: { value: "COMETS" } });
    expect(screen.getByLabelText("Spacing level")).toHaveValue("must_preserve");
    expect(box.placeholder).toContain("Hartford Comets’ spacing");

    typeAndSend(box, "Where are we thin?");
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]).toMatchObject({
      team_id: "COMETS",
      priorities: { point_of_attack_defense: "high", spacing: "must_preserve", rebounding: "low" },
      constraints: ["Closing lineups need four credible shooters."],
    });
  });
});

describe("accessible names", () => {
  it("names the composer, the Debug toggle and each priority level after what they show", async () => {
    mockApi();
    renderWorkspace();
    const box = await readyComposer();
    // The box carries its own name: the placeholder changes with the team and priorities.
    expect(box).toHaveAttribute("aria-label", COMPOSER);
    const before = box.placeholder;
    fireEvent.change(await screen.findByLabelText("Fictional team"), { target: { value: "COMETS" } });
    expect(box.placeholder).not.toBe(before);
    expect(screen.getByRole("textbox", { name: COMPOSER })).toBe(box);

    const debug = screen.getByRole("button", { name: /^Debug/ });
    expect(debug.getAttribute("aria-label")).toMatch(/^Debug\b/);
    expect(debug).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(debug);
    expect(debug).toHaveAttribute("aria-pressed", "true");

    expect(screen.getByRole("combobox", { name: "Point-of-attack defense level" })).toHaveValue("high");
    expect(screen.getByRole("combobox", { name: "Spacing level" })).toHaveValue("must_preserve");
  });
});

describe("team defaults in roster fit", () => {
  it("says the team's documented defaults will be used when no priority or constraint is set", async () => {
    mockApi();
    renderWorkspace();
    await readyComposer();
    fireEvent.change(await screen.findByLabelText("Fictional team"), { target: { value: "COMETS" } });
    fireEvent.click(screen.getByRole("radio", { name: "Roster fit" }));
    for (const name of ["Spacing level", "Point-of-attack defense level", "Rebounding level"]) {
      fireEvent.change(screen.getByRole("combobox", { name }), { target: { value: "remove" } });
    }
    expect(
      screen.getByText(
        "None set — the Hartford Comets’ documented defaults will be used: Spacing · Must preserve; Point-of-attack defense · High; Rebounding · Low.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove constraint 1" }));
    expect(screen.getByText("None set — the Hartford Comets’ documented constraints will be used")).toBeInTheDocument();

    // Research mode does not fall back to anything.
    fireEvent.click(screen.getByRole("radio", { name: "Research" }));
    expect(screen.getByText("None set — the assistant reads the question alone.")).toBeInTheDocument();
  });
});

describe("a failed status re-check", () => {
  it("keeps the conversation and the composer, with an inline warning and Check again", async () => {
    let statusDown = false;
    mockApi({ statusHandler: async () => (statusDown ? Promise.reject(new TypeError("Failed to fetch")) : json(statusFixture())) });
    const { client } = renderWorkspace();
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");
    await within(screen.getByRole("log")).findByText("Fictional data");

    statusDown = true;
    await act(async () => client.invalidateQueries({ queryKey: ["assistant-status"] }));

    expect(await screen.findByText("Couldn’t re-check the assistant’s status.")).toBeInTheDocument();
    expect(within(screen.getByRole("log")).getByText("Fictional data")).toBeInTheDocument();
    expect(screen.getByLabelText(COMPOSER)).toBeInTheDocument();
    expect(screen.getByText(/The Pivot API isn't reachable/)).toBeInTheDocument();

    statusDown = false;
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    await waitFor(() => expect(screen.queryByText("Couldn’t re-check the assistant’s status.")).toBeNull());
    expect(screen.getByRole("log")).toBeInTheDocument();
  });

  it("keeps the error turn and its Retry visible when the backend goes down behind the proxy", async () => {
    let backendDown = false;
    mockApi({
      statusHandler: async () => (backendDown ? proxy500() : json(statusFixture())),
      ask: async () => proxy500(),
    });
    renderWorkspace();
    const box = await readyComposer();
    backendDown = true;
    typeAndSend(box, "Who defends the point of attack best?");

    const log = screen.getByRole("log");
    // A proxy 500 is "not reachable", with the start command, and re-checks the status…
    expect(await within(log).findByText("The Pivot API isn't reachable")).toBeInTheDocument();
    expect(within(log).getByText(/ASSISTANT_ENABLED=true/, { selector: "code" })).toBeInTheDocument();
    expect(await screen.findByText("Couldn’t re-check the assistant’s status.")).toBeInTheDocument();
    // …and the failed re-check no longer hides the turn that triggered it.
    expect(within(screen.getByRole("log")).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("still shows the full-page error when no status has ever loaded", async () => {
    mockApi({ statusHandler: async () => proxy500() });
    renderWorkspace();
    expect(await screen.findByText("The Pivot API isn't reachable")).toBeInTheDocument();
    expect(screen.queryByRole("log")).toBeNull();
    expect(screen.queryByText("Couldn’t re-check the assistant’s status.")).toBeNull();
  });
});

describe("keyboard focus", () => {
  it("returns focus to 'New conversation' after Cancel", async () => {
    mockApi();
    renderWorkspace();
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");
    await within(screen.getByRole("log")).findByText("Fictional data");

    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(document.activeElement).toBe(cancel);
    fireEvent.click(cancel);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "New conversation" })));
  });

  it("moves focus to the composer after Stop, on the loading card or in the composer", async () => {
    mockApi({ ask: hangingAsk });
    renderWorkspace();
    const box = await readyComposer();
    const log = screen.getByRole("log");

    typeAndSend(box, "A slow one");
    const cardStop = within(log).getByRole("button", { name: "Stop" });
    cardStop.focus();
    fireEvent.click(cardStop);
    expect(await within(log).findByText("Stopped.")).toBeInTheDocument();
    expect(document.activeElement).toBe(box);

    typeAndSend(box, "Another slow one");
    const composerStop = within(box.closest("form") as HTMLElement).getByRole("button", { name: "Stop" });
    composerStop.focus();
    fireEvent.click(composerStop);
    await waitFor(() => expect(within(log).getAllByText("Stopped.")).toHaveLength(2));
    expect(document.activeElement).toBe(box);
  });
});

describe("leaving the page mid-answer", () => {
  it("frees the backend slot but stores no 'You stopped this answer' turn", async () => {
    let aborted = false;
    mockApi({
      ask: (body, init) => {
        init?.signal?.addEventListener("abort", () => {
          aborted = true;
        });
        return hangingAsk(body, init);
      },
    });
    const view = renderWorkspace();
    typeAndSend(await readyComposer(), "A slow one");
    await screen.findByText("Researching the fictional corpus…");

    view.unmount(); // what a client-side navigation away from /assistant does
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(aborted).toBe(true);
    expect(getSession().messages.map((m) => m.role)).toEqual(["user"]);
    expect(window.sessionStorage.getItem(SESSION_KEY) ?? "").not.toContain("You stopped this answer");

    // Coming back: the question is shown as unanswered, with Ask again — not as "Stopped".
    renderWorkspace();
    expect(await screen.findByText(/No answer was recorded for this question/)).toBeInTheDocument();
    expect(screen.queryByText("Stopped.")).toBeNull();
    expect(screen.getByRole("button", { name: "Ask again" })).toBeInTheDocument();
  });

  it("still stores answers under StrictMode's mount, unmount, mount", async () => {
    mockApi();
    renderWorkspace({ strict: true });
    typeAndSend(await readyComposer(), "Who defends the point of attack best?");
    expect(await within(screen.getByRole("log")).findByText("Fictional data")).toBeInTheDocument();
    expect(getSession().messages.map((m) => m.role)).toEqual(["user", "assistant"]);
  });
});
