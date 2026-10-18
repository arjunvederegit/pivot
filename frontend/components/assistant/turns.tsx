"use client";

/**
 * The non-answer turns of the thread: the GM's question, the in-flight card, a failed or
 * stopped turn, and the empty thread's starting points.
 */

import { useEffect, useState } from "react";
import type { AssistantErrorInfo } from "@/lib/assistant-api";
import type { AssistantMode, AskRequest } from "@/lib/assistant-types";
import { Button, Skeleton } from "@/components/ui";

/* -------------------------------------------------------------------- question */

export function UserTurn({
  content,
  request,
  teamName,
}: {
  content: string;
  request?: AskRequest;
  teamName: (id: string | null) => string | null;
}) {
  const priorities = request ? Object.keys(request.priorities).length : 0;
  const team = request ? teamName(request.team_id) : null;
  return (
    <div className="flex justify-end">
      <div className="min-w-0 max-w-[min(85%,44rem)] rounded-xl rounded-br-sm border border-hairline bg-panel2 px-4 py-2.5">
        <p className="sr-only">You asked:</p>
        <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-foreground [overflow-wrap:anywhere]">
          {content}
        </p>
        {request && (
          <p className="mt-1.5 text-right text-[11px] text-faint">
            {team ?? "No team"} · {request.mode === "roster_fit" ? "Roster fit" : "Research"}
            {priorities ? ` · ${priorities} priorit${priorities === 1 ? "y" : "ies"}` : ""}
            {request.constraints.length ? ` · ${request.constraints.length} constraint${request.constraints.length === 1 ? "" : "s"}` : ""}
          </p>
        )}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------- pending */

/**
 * A local model takes 10–60 seconds; the elapsed counter says the wait is expected. It
 * counts from its own mount, which is the moment the request was sent.
 */
export function PendingTurn({ onStop }: { onStop: () => void }) {
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(startedAt);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));

  return (
    <div className="panel min-w-0 overflow-hidden" aria-busy="true">
      <div className="flex flex-wrap items-center gap-3 border-b border-hairline px-4 py-3 sm:px-5">
        <span
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-line border-t-signal motion-reduce:animate-none"
        />
        <p className="text-[13px] text-foreground">
          Researching the fictional corpus…
        </p>
        <span aria-hidden className="numeral text-[12px] text-faint">
          {seconds}s
        </span>
        <Button size="sm" variant="secondary" className="ml-auto" onClick={onStop}>
          Stop
        </Button>
      </div>
      <div className="space-y-2.5 px-4 py-4 sm:px-5">
        <Skeleton className="h-3.5 w-[92%]" />
        <Skeleton className="h-3.5 w-[84%]" />
        <Skeleton className="h-3.5 w-[66%]" />
        <p className="pt-1 text-[12px] leading-relaxed text-faint">
          {seconds < 15
            ? "Retrieving passages, then asking the local model to answer only from them."
            : "Local models take 10–60 seconds, longer on the first question while the model loads."}
        </p>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------------- error */

export function ErrorTurn({
  error,
  onRetry,
  retryDisabled,
}: {
  error: AssistantErrorInfo;
  /** Absent when this is not the latest turn, or the error cannot be fixed by resending. */
  onRetry?: () => void;
  retryDisabled?: boolean;
}) {
  if (error.kind === "aborted") {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-hairline bg-panel2/40 px-4 py-3">
        <span aria-hidden className="text-[12px] text-faint">
          ■
        </span>
        <p className="min-w-0 flex-1 text-[13px] text-muted">
          <span className="font-medium text-foreground">{error.title}.</span> {error.detail}
        </p>
        {onRetry && (
          <Button size="sm" variant="secondary" onClick={onRetry} disabled={retryDisabled}>
            Ask again
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-illegal/40 bg-illegal/[0.07] px-4 py-3.5">
      <p className="flex items-start gap-2 text-[14px] font-medium text-illegal">
        <span aria-hidden className="text-[12px] leading-5">
          ✕
        </span>
        <span>{error.title}</span>
      </p>
      <div className="mt-1.5 space-y-2.5 pl-5">
        <p className="text-[13px] leading-relaxed text-muted [overflow-wrap:anywhere]">{error.detail}</p>
        {error.steps && error.steps.length > 0 && (
          <ol className="space-y-1.5">
            {error.steps.map((step) => (
              <li key={step}>
                <code className="block w-fit max-w-full rounded-md border border-line bg-panel2 px-2 py-1 text-[12px] text-foreground [overflow-wrap:anywhere]">
                  {step}
                </code>
              </li>
            ))}
          </ol>
        )}
        <div className="flex flex-wrap items-center gap-3">
          {onRetry && error.retryable && (
            <Button size="sm" variant="secondary" onClick={onRetry} disabled={retryDisabled}>
              Retry
            </Button>
          )}
          {(error.code || error.requestId) && (
            <span className="text-[11px] text-faint">
              {error.code ? <code>{error.code}</code> : null}
              {error.requestId ? <> · request {error.requestId}</> : null}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** A question with no recorded reply — the page was reloaded, or left, while it ran. */
export function UnansweredNote({ onRetry, retryDisabled }: { onRetry?: () => void; retryDisabled?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-line px-4 py-3">
      <p className="min-w-0 flex-1 text-[12.5px] text-muted">
        No answer was recorded for this question — the page was probably reloaded or left while
        it ran.
      </p>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry} disabled={retryDisabled}>
          Ask again
        </Button>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- empty thread */

const CAN = [
  "Find what the fictional scouts wrote about a player, a team or a skill — with every passage attached.",
  "Pressure-test your read: name your hypothesis, and see which reports support it, contradict it, or are silent.",
  "Lay a team's stated priorities against documented observations, and show where reports disagree.",
];

const CANNOT = [
  "Judge trade legality or the CBA, or read contracts and cap figures.",
  "Compute a fit score, rank players or recommend a move — the call is yours.",
  "Discuss real NBA players or teams. Every name here is invented.",
];

export function EmptyThread({ mode }: { mode: AssistantMode }) {
  return (
    <div className="px-1 py-6 sm:py-10">
      <h2 className="text-[1.35rem] font-semibold leading-tight tracking-[-0.015em] text-foreground">
        {mode === "roster_fit" ? "Read the roster against your priorities" : "Ask about the fictional league"}
      </h2>
      <p className="mt-2 max-w-[60ch] text-[15px] leading-relaxed text-muted">
        Ask about a fictional player, a team&apos;s needs, or where two scouting reports disagree. Set
        the team, mode, strategy and constraints from the chips above the box; open Suggestions for
        starter questions.
      </p>
      <details className="group mt-5 max-w-[60ch] text-[13.5px] leading-relaxed">
        <summary className="cursor-pointer list-none text-muted transition-colors hover:text-foreground">
          <span aria-hidden className="mr-1.5 inline-block w-3 text-center transition-transform group-open:rotate-90">
            ›
          </span>
          What Ask Pivot can and cannot do
        </summary>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div>
            <p className="mb-1.5 font-medium text-foreground">Can</p>
            <ul className="list-disc space-y-1 pl-5 text-muted marker:text-faint">
              {CAN.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="mb-1.5 font-medium text-foreground">Cannot</p>
            <ul className="list-disc space-y-1 pl-5 text-muted marker:text-faint">
              {CANNOT.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        </div>
      </details>
    </div>
  );
}
