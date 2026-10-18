"use client";

/**
 * The page-level states that are not a conversation: the sandbox banner every visitor
 * sees, the server-flag-off panel, the setup checklist when the local stack is not ready,
 * a status check that could not reach the server at all, and a re-check that failed after
 * an earlier one succeeded.
 */

import type { AssistantErrorInfo } from "@/lib/assistant-api";
import { INSTALL_STEP, SERVER_FLAG_STEP } from "@/lib/assistant-api";
import type { AssistantStatus } from "@/lib/assistant-types";
import { Button, UnavailableNotice } from "@/components/ui";

function CodeLine({ children }: { children: string }) {
  return (
    <code className="block w-fit max-w-full rounded-md border border-line bg-panel2 px-2 py-1 text-[12px] text-foreground [overflow-wrap:anywhere]">
      {children}
    </code>
  );
}

/** Persistent, above everything: this is a sandbox of invented data. */
export function SandboxBanner() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-conditional/30 bg-conditional/[0.06] px-4 py-3">
      <span
        aria-hidden
        className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-conditional/50 text-[11px] font-semibold text-conditional"
      >
        !
      </span>
      <p className="text-[12.5px] leading-relaxed text-muted">
        <span className="font-semibold text-foreground">FICTIONAL DATA / SANDBOX.</span> Every team,
        player, scout and report in Ask Pivot is invented for research practice — none of it is
        NBA data, and none of it touches Pivot’s real database or trade engines.
      </p>
    </div>
  );
}

/** The backend flag is off: say so plainly. Not a broken chat. */
export function DisabledPanel({ reason }: { reason?: string }) {
  return (
    <section aria-label="Ask Pivot is switched off" className="panel p-5 sm:p-6">
      <div className="eyebrow flex items-center gap-2">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-unavail" />
        Switched off on the server
      </div>
      <h2 className="title-lg mt-2 text-foreground">Ask Pivot is not enabled on this backend</h2>
      <p className="mt-3 max-w-2xl text-[14px] leading-relaxed text-muted">
        This page is built in, but the backend’s <code>ASSISTANT_ENABLED</code> flag is off,
        so the assistant API refuses every question. Nothing is broken — the feature is
        experimental and off by default.
        {reason ? ` (${reason})` : ""}
      </p>
      <div className="mt-4 space-y-2">
        <p className="text-[12.5px] text-faint">To turn it on for local development:</p>
        <CodeLine>{SERVER_FLAG_STEP}</CodeLine>
      </div>
    </section>
  );
}

interface Step {
  command: string;
  why: string;
  likely: boolean;
}

function setupSteps(status: AssistantStatus): Step[] {
  const p = status.provider;
  const chat = p?.chat_model || "qwen3.5:2b";
  const embedding = p?.embedding_model || "nomic-embed-text";
  // Reachable but with an error: the model list itself could not be read, so whether a
  // model is pulled is unknown — the backend's issue line names the real failure instead.
  const modelsKnown = p ? p.reachable && !p.error : false;
  return [
    {
      command: INSTALL_STEP,
      why: "the optional LangChain / Ollama packages",
      likely: status.dependencies ? !status.dependencies.installed : false,
    },
    { command: "ollama serve", why: "start the local model server", likely: p ? !p.reachable : false },
    {
      command: `ollama pull ${chat}`,
      why: "the chat model",
      likely: modelsKnown && !p?.chat_model_available,
    },
    {
      command: `ollama pull ${embedding}`,
      why: "the embedding model",
      likely: modelsKnown && !p?.embedding_model_available,
    },
    { command: SERVER_FLAG_STEP, why: "run the backend with the flag on", likely: false },
  ];
}

/** Enabled, not ready: what is missing and the exact commands that fix it. */
export function SetupNotice({
  status,
  onCheckAgain,
  checking,
}: {
  status: AssistantStatus;
  onCheckAgain: () => void;
  checking: boolean;
}) {
  const steps = setupSteps(status);
  return (
    <UnavailableNotice
      reason="The assistant is switched on, but its local stack is not ready yet, so questions are paused."
      steps={
        <div className="space-y-3">
          {status.issues.length > 0 && (
            <ul className="list-disc space-y-1 pl-5 text-[12.5px] leading-relaxed text-foreground marker:text-faint">
              {status.issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          )}
          <p className="text-[12.5px] text-muted">Setup, in order (from the repo root):</p>
          <ol className="space-y-2">
            {steps.map((step) => (
              <li key={step.command} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <CodeLine>{step.command}</CodeLine>
                <span className="text-[12px] text-faint">{step.why}</span>
                {step.likely && (
                  <span className="text-[11.5px] font-medium text-conditional">
                    <span aria-hidden>! </span>likely needed
                  </span>
                )}
              </li>
            ))}
          </ol>
          <Button size="sm" variant="secondary" onClick={onCheckAgain} disabled={checking}>
            {checking ? "Checking…" : "Check again"}
          </Button>
        </div>
      }
    />
  );
}

/** The status call itself failed — no answer about readiness at all. */
export function StatusError({
  info,
  onRetry,
  checking,
}: {
  info: AssistantErrorInfo;
  onRetry: () => void;
  checking: boolean;
}) {
  return (
    <div className="rounded-lg border border-illegal/40 bg-illegal/[0.07] px-4 py-3.5">
      <p className="flex items-start gap-2 text-[14px] font-medium text-illegal">
        <span aria-hidden className="text-[12px] leading-5">
          ✕
        </span>
        <span>{info.title}</span>
      </p>
      <div className="mt-1.5 space-y-2.5 pl-5">
        <p className="text-[13px] leading-relaxed text-muted [overflow-wrap:anywhere]">{info.detail}</p>
        {info.steps?.map((s) => <CodeLine key={s}>{s}</CodeLine>)}
        <Button size="sm" variant="secondary" onClick={onRetry} disabled={checking}>
          {checking ? "Checking…" : "Check again"}
        </Button>
      </div>
    </div>
  );
}

/**
 * A re-check of the status failed after an earlier one succeeded. The page keeps what it
 * last knew — the conversation, the composer, any error turn — and says the check failed.
 */
export function StatusRecheckWarning({
  info,
  onRetry,
  checking,
}: {
  info: AssistantErrorInfo;
  onRetry: () => void;
  checking: boolean;
}) {
  return (
    <div
      role="status"
      className="flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg border border-conditional/30 bg-conditional/[0.05] px-4 py-3"
    >
      <span aria-hidden className="text-[12px] leading-5 text-conditional">
        !
      </span>
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-[12.5px] leading-relaxed text-muted [overflow-wrap:anywhere]">
          <span className="font-medium text-foreground">Couldn’t re-check the assistant’s status.</span>{" "}
          {info.title}. {info.detail} The page shows what the last successful check found.
        </p>
        {info.steps?.map((s) => <CodeLine key={s}>{s}</CodeLine>)}
      </div>
      <Button size="sm" variant="secondary" onClick={onRetry} disabled={checking}>
        {checking ? "Checking…" : "Check again"}
      </Button>
    </div>
  );
}
