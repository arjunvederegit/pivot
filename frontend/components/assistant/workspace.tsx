"use client";

/**
 * Pivot Intelligence — the Ask Pivot workspace.
 *
 * An evidence-grounded research partner for a GM, over a FICTIONAL scouting corpus. It
 * reads, cites and says what it cannot know; it does not decide. The layout keeps that
 * division visible: the conversation on the left, and on the right the evidence each answer
 * was given, so a claim can always be checked against the passage it cites.
 *
 * Data flow, top to bottom:
 *   GET /assistant/status    (React Query) — is the server flag on, is the local stack ready
 *   GET /assistant/context   (React Query) — fictional teams, priority dimensions, starters
 *   POST /assistant/ask      (manual async) — one answer; manual so Stop can abort it
 *   sessionStorage           (lib/assistant-session) — the conversation, this tab only
 *
 * Gating happens twice, and they mean different things: the page only exists in a build
 * made with NEXT_PUBLIC_ASSISTANT_ENABLED=true (app/assistant/page.tsx 404s otherwise), and
 * at runtime the backend's own ASSISTANT_ENABLED decides whether questions are answered.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  askAssistant,
  describeAssistantError,
  getAssistantContext,
  getAssistantStatus,
  type AssistantErrorKind,
} from "@/lib/assistant-api";
import { possessive } from "@/lib/assistant-format";
import { useDebugPreference } from "@/lib/assistant-preferences";
import {
  appendMessages,
  buildHistory,
  clearConversation,
  getSession,
  newMessageId,
  previousPriorities,
  updateSession,
  useAssistantSession,
  usePersisting,
  type StoredMessage,
} from "@/lib/assistant-session";
import {
  MAX_CONSTRAINTS,
  PRIORITY_LEVELS,
  teamDefaults,
  type AskRequest,
  type AssistantContext,
  type AssistantMode,
  type AssistantTeam,
  type Priorities,
} from "@/lib/assistant-types";
import { AssistantAnswer } from "@/components/assistant/answer";
import { Composer } from "@/components/assistant/composer";
import { ContextBar } from "@/components/assistant/context-bar";
import {
  EvidenceInspector,
  InspectorDrawer,
  pickSelectedSource,
  rankOrder,
} from "@/components/assistant/inspector";
import {
  DisabledPanel,
  SandboxBanner,
  SetupNotice,
  StatusError,
  StatusRecheckWarning,
} from "@/components/assistant/states";
import { EmptyThread, ErrorTurn, PendingTurn, UnansweredNote, UserTurn } from "@/components/assistant/turns";
import { Badge, Button, PageHeader, Skeleton } from "@/components/ui";

/** The inspector rail appears at Tailwind's `xl`; below it the inspector is a drawer. */
const DESKTOP_QUERY = "(min-width: 1280px)";
const DESKTOP_INSPECTOR_ID = "evidence-inspector-desktop";

/** Error kinds after which the readiness shown in the header is probably stale. */
const REFRESH_STATUS_AFTER: ReadonlySet<AssistantErrorKind> = new Set([
  "provider",
  "model",
  "dependencies",
  "corpus",
  "disabled",
  "network",
]);

function isDesktop(): boolean {
  return typeof window.matchMedia === "function" ? window.matchMedia(DESKTOP_QUERY).matches : true;
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

function strongestPriority(priorities: Priorities): string | null {
  for (const level of PRIORITY_LEVELS) {
    const key = Object.entries(priorities).find(([, v]) => v === level)?.[0];
    if (key) return key;
  }
  return null;
}

function placeholderFor(
  mode: AssistantMode,
  team: AssistantTeam | null,
  priorities: Priorities,
  dimensionLabel: (key: string) => string,
): string {
  const top = strongestPriority(priorities);
  const topic = top ? dimensionLabel(top).toLowerCase() : null;
  if (team) {
    return mode === "roster_fit"
      ? `Which fictional players fit the ${possessive(team.name)} priorities${topic ? `, starting with ${topic}` : ""}?`
      : `Ask about the ${possessive(team.name)} ${topic ?? "roster needs"}…`;
  }
  return mode === "roster_fit"
    ? "Pick a team above, or ask how a player's reports read against one priority…"
    : "Ask about a fictional player, a team, or where two scouting reports disagree…";
}

/** Asks before clearing; focus lands on Cancel so a stray Enter is harmless. */
function ConfirmClear({
  count,
  onConfirm,
  onCancel,
}: {
  count: number;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  return (
    <span role="group" aria-label="Confirm a new conversation" className="flex flex-wrap items-center gap-2">
      <span className="text-[12.5px] text-muted">
        Clear {count} message{count === 1 ? "" : "s"}?
      </span>
      <Button size="sm" variant="danger" onClick={onConfirm}>
        Clear
      </Button>
      <Button ref={cancelRef} size="sm" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
    </span>
  );
}

interface Pending {
  /** The id the reply will be stored under. */
  id: string;
}

export function AssistantWorkspace() {
  const queryClient = useQueryClient();
  const session = useAssistantSession();
  const persisting = usePersisting();
  const [debug, setDebug] = useDebugPreference();

  const status = useQuery({
    queryKey: ["assistant-status"],
    queryFn: ({ signal }) => getAssistantStatus(signal),
    staleTime: 30_000,
    retry: false,
  });
  const enabled = status.data?.enabled === true;
  const ready = enabled && status.data?.ready === true;

  const context = useQuery({
    queryKey: ["assistant-context"],
    queryFn: ({ signal }) => getAssistantContext(signal),
    enabled,
    staleTime: 5 * 60_000,
  });

  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [inspected, setInspected] = useState<{ messageId: string | null; chunkId: string | null }>({
    messageId: null,
    chunkId: null,
  });
  const [drawer, setDrawer] = useState<{ opener: HTMLElement | null } | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  /** Bumped on "New conversation": a reply to a cleared thread is dropped, not appended. */
  const generationRef = useRef(0);
  /**
   * False once the page is left. The unmount abort is not the reader's Stop, so a request
   * it cut short stores nothing — not a "You stopped this answer" turn. (Set in an effect,
   * not at render, so StrictMode's mount → unmount → mount ends up true.)
   */
  const mountedRef = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const newConversationRef = useRef<HTMLButtonElement>(null);
  /** Set by Cancel: focus goes back to "New conversation" once it is rendered again. */
  const refocusNewConversationRef = useRef(false);
  /** The turn to bring into view once it renders — set only by a send in this page. */
  const scrollTargetRef = useRef<string | null>(null);

  /* ------------------------------------------------------------- derived data */

  const catalog: AssistantContext | undefined = context.data;
  const team = catalog?.teams.find((t) => t.id === session.team_id) ?? null;
  // A stored team id the catalogue no longer has is dropped here, not sent (it would 422).
  const teamId = team?.id ?? null;

  const dimensionLabel = useCallback(
    (key: string) =>
      catalog?.priority_dimensions.find((d) => d.key === key)?.label ?? key.replace(/_/g, " "),
    [catalog],
  );
  const nameOf = (id: string) =>
    catalog?.players.find((p) => p.id === id)?.name ??
    catalog?.teams.find((t) => t.id === id)?.name ??
    id;
  const teamName = (id: string | null) =>
    id ? (catalog?.teams.find((t) => t.id === id)?.name ?? id) : null;
  const models = {
    chat: status.data?.provider?.chat_model,
    embedding: status.data?.provider?.embedding_model,
  };

  const messages = session.messages;
  const answers = messages.filter((m) => m.role === "assistant" && m.response);
  const answerNumber = new Map(answers.map((m, i) => [m.id, i + 1]));
  const active =
    answers.find((m) => m.id === inspected.messageId) ?? answers[answers.length - 1] ?? null;
  const activeIndex = active ? messages.findIndex((m) => m.id === active.id) : -1;
  const activeQuestion =
    activeIndex > 0 && messages[activeIndex - 1].role === "user"
      ? messages[activeIndex - 1].content
      : undefined;
  const activeSelected = active?.response
    ? pickSelectedSource(active.response.sources, inspected.chunkId)
    : null;

  const starters = (catalog?.starters ?? [])
    .filter((s) => s.mode === session.mode && (s.team_id === null || s.team_id === teamId))
    .sort((a, b) => Number(b.team_id !== null) - Number(a.team_id !== null))
    .slice(0, 6);

  /* ---------------------------------------------------------------- effects */

  // Leaving the page stops an answer in flight: it frees the backend's single slot.
  useEffect(() => {
    mountedRef.current = true;
    const inflight = abortRef;
    return () => {
      mountedRef.current = false;
      inflight.current?.abort();
    };
  }, []);

  // Cancel unmounts the focused control; the button that replaces it takes focus instead.
  useEffect(() => {
    if (confirmingClear || !refocusNewConversationRef.current) return;
    refocusNewConversationRef.current = false;
    newConversationRef.current?.focus();
  }, [confirmingClear]);

  // Bring the new turn into view: the loading card while it runs, the answer's top when
  // it lands. Only for sends made on this page — a restored thread does not jump on load.
  const lastId = messages[messages.length - 1]?.id;
  useEffect(() => {
    const target = scrollTargetRef.current;
    if (!target) return;
    const el = document.getElementById(`turn-${target}`);
    if (!el || typeof el.scrollIntoView !== "function") return;
    const arrived = lastId === target;
    el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: arrived ? "start" : "nearest" });
    if (arrived) scrollTargetRef.current = null;
  }, [pending, lastId]);

  // A drawer left open while the window widens past `xl` would trap focus invisibly.
  useEffect(() => {
    if (!drawer || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = () => {
      if (mq.matches) setDrawer(null);
    };
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, [drawer]);

  const closeDrawer = useCallback(() => setDrawer(null), []);

  /* ----------------------------------------------------------------- asking */

  function buildRequest(question: string): AskRequest {
    const s = getSession();
    return {
      question,
      mode: s.mode,
      team_id: teamId,
      priorities: { ...s.priorities },
      previous_priorities: previousPriorities(s.messages),
      constraints: s.constraints
        .map((c) => c.trim())
        .filter(Boolean)
        .slice(0, MAX_CONSTRAINTS),
      history: buildHistory(s.messages),
    };
  }

  /**
   * Send one request. `question` adds the GM's turn first; `replacing` removes a failed
   * turn that Retry is re-running. The reply — answer, error or "Stopped" — is appended
   * when the request settles, unless the thread was cleared meanwhile.
   */
  function run(request: AskRequest, { question, replacing }: { question?: string; replacing?: string } = {}) {
    if (pending) return;
    const generation = generationRef.current;
    if (question !== undefined) {
      updateSession((s) =>
        appendMessages(s, {
          id: newMessageId(),
          role: "user",
          content: question,
          created_at: new Date().toISOString(),
          request,
        }),
      );
    }
    if (replacing) {
      updateSession((s) => ({ ...s, messages: s.messages.filter((m) => m.id !== replacing) }));
    }

    const replyId = newMessageId();
    const controller = new AbortController();
    abortRef.current = controller;
    scrollTargetRef.current = replyId;
    setPending({ id: replyId });
    setInspected({ messageId: null, chunkId: null });
    composerRef.current?.focus();

    const settle = (reply: Pick<StoredMessage, "content" | "response" | "error">) => {
      if (generation !== generationRef.current || !mountedRef.current) return;
      updateSession((s) =>
        appendMessages(s, {
          id: replyId,
          role: "assistant",
          created_at: new Date().toISOString(),
          request,
          ...reply,
        }),
      );
    };

    askAssistant(request, { signal: controller.signal })
      .then((response) => settle({ content: response.answer, response }))
      .catch((error: unknown) => {
        const info = describeAssistantError(error, models);
        settle({ content: "", error: info });
        if (REFRESH_STATUS_AFTER.has(info.kind)) {
          void queryClient.invalidateQueries({ queryKey: ["assistant-status"] });
        }
      })
      .finally(() => {
        if (abortRef.current === controller) abortRef.current = null;
        if (generation === generationRef.current) setPending(null);
      });
  }

  function ask(question: string) {
    const q = question.trim();
    if (!q || !ready || pending) return;
    run(buildRequest(q), { question: q });
  }

  function submitDraft() {
    if (!draft.trim() || !ready || pending) return;
    ask(draft);
    setDraft("");
  }

  function stop() {
    abortRef.current?.abort();
    // Both Stop controls go away (the card unmounts; the composer's turns back into Send),
    // so keep keyboard focus in the place the next question is typed.
    composerRef.current?.focus();
  }

  function newConversation() {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    clearConversation();
    setPending(null);
    setConfirmingClear(false);
    setInspected({ messageId: null, chunkId: null });
    setDrawer(null);
    composerRef.current?.focus();
  }

  /* -------------------------------------------------------------- evidence */

  function openSource(messageId: string, sourceId: string, opener: HTMLElement) {
    const response = messages.find((m) => m.id === messageId)?.response;
    const chunk = rankOrder(response?.sources ?? []).find((s) => s.source_id === sourceId);
    setInspected({ messageId, chunkId: chunk?.chunk_id ?? null });
    if (!isDesktop()) setDrawer({ opener });
  }

  function showSources(messageId: string, opener: HTMLElement) {
    setInspected({ messageId, chunkId: null });
    if (!isDesktop()) {
      setDrawer({ opener });
      return;
    }
    requestAnimationFrame(() =>
      document
        .querySelector<HTMLElement>(`#${DESKTOP_INSPECTOR_ID} [role="option"][tabindex="0"]`)
        ?.focus(),
    );
  }

  /* --------------------------------------------------------- context edits */

  function chooseTeam(next: AssistantTeam | null) {
    // A team brings its own starting position. The *previous* priorities the backend
    // compares against come from the last answered turn (`previousPriorities`), so a team
    // switch is reported to it as the priority change it is.
    const defaults = next ? teamDefaults(next) : { priorities: {}, constraints: [] };
    updateSession((s) => ({
      ...s,
      team_id: next?.id ?? null,
      priorities: defaults.priorities,
      constraints: defaults.constraints,
    }));
  }

  /* ------------------------------------------------------------------ view */

  const checking = status.isFetching;
  const header = (
    <PageHeader
      eyebrow="Ask Pivot · Experimental"
      title="Pivot Intelligence"
      lede="Research the game. Pressure-test the move. Make the call."
      meta={
        <>
          <Badge status="warning">Fictional data</Badge>
          {status.data?.provider && (
            <Badge status="info">Local model · {status.data.provider.chat_model}</Badge>
          )}
          {status.data?.tracing?.enabled && (
            <span title="Prompts, retrieved fictional passages and answers are sent to LangSmith while tracing is on.">
              <Badge status="warning">
                Tracing to LangSmith{status.data.tracing.project ? ` · ${status.data.tracing.project}` : ""}
              </Badge>
            </span>
          )}
        </>
      }
      actions={
        enabled ? (
          <>
            {confirmingClear ? (
              <ConfirmClear
                count={messages.length}
                onConfirm={newConversation}
                onCancel={() => {
                  refocusNewConversationRef.current = true;
                  setConfirmingClear(false);
                }}
              />
            ) : (
              <Button
                ref={newConversationRef}
                size="sm"
                variant="secondary"
                onClick={() => (messages.length > 0 || pending ? setConfirmingClear(true) : newConversation())}
              >
                New conversation
              </Button>
            )}
            <Button
              size="sm"
              variant={debug ? "secondary" : "ghost"}
              aria-pressed={debug}
              onClick={() => setDebug(!debug)}
              // The name starts with the visible word, so "Debug" finds it by voice or search.
              aria-label="Debug: show how each answer was produced"
              title="Show how each answer was produced: retrieval candidates, thresholds, timings"
            >
              <span aria-hidden className={debug ? "text-signal" : "text-faint"}>
                {debug ? "●" : "○"}
              </span>
              Debug
            </Button>
          </>
        ) : undefined
      }
    />
  );

  let body: React.ReactNode;
  if (status.isPending) {
    body = (
      <div className="space-y-4" role="status" aria-label="Checking the local assistant">
        <Skeleton className="h-28" />
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Skeleton className="h-72" />
          <Skeleton className="hidden h-72 xl:block" />
        </div>
      </div>
    );
  } else if (status.isError && status.data === undefined) {
    // No status has ever loaded. A failed *re*-check keeps the page (see below): React
    // Query keeps the last good data, and unmounting the thread for it would hide the
    // conversation, the composer and the very error turn that caused the re-check.
    const err = status.error;
    body =
      err instanceof ApiError && err.status === 404 ? (
        <DisabledPanel reason="the status route answered 404" />
      ) : (
        <StatusError
          info={describeAssistantError(err, models)}
          onRetry={() => void status.refetch()}
          checking={checking}
        />
      );
  } else if (!enabled) {
    body = <DisabledPanel />;
  } else {
    const followUpsDisabled = !ready || pending !== null;
    body = (
      <div className="space-y-6">
        {!ready && status.data && (
          <SetupNotice status={status.data} onCheckAgain={() => void status.refetch()} checking={checking} />
        )}

        <ContextBar
          context={catalog}
          loading={context.isPending}
          error={context.error ? describeAssistantError(context.error).detail : null}
          onRetry={() => void context.refetch()}
          teamId={teamId}
          mode={session.mode}
          priorities={session.priorities}
          constraints={session.constraints}
          onTeam={chooseTeam}
          onMode={(mode) => updateSession((s) => ({ ...s, mode }))}
          onPriorities={(priorities) => updateSession((s) => ({ ...s, priorities }))}
          onConstraints={(constraints) => updateSession((s) => ({ ...s, constraints }))}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <section aria-labelledby="assistant-thread-heading" className="min-w-0 space-y-4">
            <h2 id="assistant-thread-heading" className="sr-only">
              Conversation
            </h2>
            <div
              role="log"
              aria-live="polite"
              aria-relevant="additions"
              aria-label="Conversation with Ask Pivot"
              className="space-y-5"
            >
              {messages.length === 0 && !pending ? (
                <EmptyThread starters={starters} mode={session.mode} disabled={!ready} onStart={ask} />
              ) : (
                messages.map((m, i) => (
                  <Turn
                    key={m.id}
                    message={m}
                    next={messages[i + 1]}
                    isLast={i === messages.length - 1}
                    pending={pending !== null}
                    canRetry={ready && pending === null}
                    onRetry={run}
                    renderAnswer={(response) => (
                      <AssistantAnswer
                        response={response}
                        request={m.request}
                        turnNumber={answerNumber.get(m.id) ?? 0}
                        sources={response.sources}
                        onOpenSource={(sid, opener) => openSource(m.id, sid, opener)}
                        onShowSources={(opener) => showSources(m.id, opener)}
                        onFollowUp={ask}
                        followUpsDisabled={followUpsDisabled}
                        debug={debug}
                        selectedSourceId={active?.id === m.id ? (activeSelected?.source_id ?? null) : null}
                        dimensionLabel={dimensionLabel}
                        shownInInspector={active?.id === m.id && answers.length > 1}
                      />
                    )}
                    teamName={teamName}
                  />
                ))
              )}
              {pending && (
                <div id={`turn-${pending.id}`}>
                  <PendingTurn key={pending.id} onStop={stop} />
                </div>
              )}
            </div>

            {!persisting && (
              <p className="text-[11.5px] text-faint">
                This browser is blocking session storage, so the conversation will not survive a
                reload. It still works for as long as the page stays open.
              </p>
            )}

            <div className="sticky bottom-3 z-20" data-bottom-bar>
              <Composer
                value={draft}
                onChange={setDraft}
                onSend={submitDraft}
                onStop={stop}
                pending={pending !== null}
                disabled={!ready}
                disabledReason={
                  ready
                    ? null
                    : "Questions unlock once the local model and corpus are ready — see the setup steps above."
                }
                placeholder={placeholderFor(session.mode, team, session.priorities, dimensionLabel)}
                textareaRef={composerRef}
              />
            </div>
          </section>

          <aside id={DESKTOP_INSPECTOR_ID} aria-label="Evidence inspector" className="hidden min-w-0 xl:block">
            <div className="panel scroll-thin p-4 xl:sticky xl:top-6 xl:max-h-[calc(100dvh-3rem)] xl:overflow-y-auto">
              <EvidenceInspector
                response={active?.response ?? null}
                turnLabel={active ? `Answer ${answerNumber.get(active.id)} of ${answers.length}` : undefined}
                question={activeQuestion}
                selectedChunkId={activeSelected?.chunk_id ?? null}
                onSelect={(chunkId) => setInspected({ messageId: active?.id ?? null, chunkId })}
                nameOf={nameOf}
              />
            </div>
          </aside>
        </div>

        {drawer && (
          <InspectorDrawer onClose={closeDrawer} returnFocusTo={drawer.opener}>
            <EvidenceInspector
              response={active?.response ?? null}
              turnLabel={active ? `Answer ${answerNumber.get(active.id)} of ${answers.length}` : undefined}
              question={activeQuestion}
              selectedChunkId={activeSelected?.chunk_id ?? null}
              onSelect={(chunkId) => setInspected({ messageId: active?.id ?? null, chunkId })}
              nameOf={nameOf}
            />
          </InspectorDrawer>
        )}
      </div>
    );
  }

  const recheckFailed = status.isError && status.data !== undefined;
  return (
    <div className="space-y-6">
      {header}
      <SandboxBanner />
      {recheckFailed && (
        <StatusRecheckWarning
          info={describeAssistantError(status.error, models)}
          onRetry={() => void status.refetch()}
          checking={checking}
        />
      )}
      {body}
    </div>
  );
}

/* -------------------------------------------------------------------- turn */

/**
 * One stored message. A module-level component (not defined inside the workspace) so React
 * keeps its identity across renders.
 */
function Turn({
  message,
  next,
  isLast,
  pending,
  canRetry,
  onRetry,
  renderAnswer,
  teamName,
}: {
  message: StoredMessage;
  next: StoredMessage | undefined;
  isLast: boolean;
  pending: boolean;
  canRetry: boolean;
  onRetry: (request: AskRequest, opts?: { replacing?: string }) => void;
  renderAnswer: (response: NonNullable<StoredMessage["response"]>) => React.ReactNode;
  teamName: (id: string | null) => string | null;
}) {
  const request = message.request;
  if (message.role === "user") {
    const unanswered = !next || next.role !== "assistant";
    return (
      <div id={`turn-${message.id}`} className="space-y-3">
        <UserTurn content={message.content} request={request} teamName={teamName} />
        {unanswered && !(isLast && pending) && (
          <UnansweredNote
            onRetry={isLast && request ? () => onRetry(request) : undefined}
            retryDisabled={!canRetry}
          />
        )}
      </div>
    );
  }
  if (message.response) {
    return <div id={`turn-${message.id}`}>{renderAnswer(message.response)}</div>;
  }
  if (message.error) {
    return (
      <div id={`turn-${message.id}`}>
        <ErrorTurn
          error={message.error}
          onRetry={isLast && request ? () => onRetry(request, { replacing: message.id }) : undefined}
          retryDisabled={!canRetry}
        />
      </div>
    );
  }
  return null;
}
