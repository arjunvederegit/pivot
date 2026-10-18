"use client";

/**
 * One assistant answer, as a card.
 *
 * The header makes three separate claims, and keeps them separate:
 *  1. **what kind of reply this is** (`answer_kind`) — whether a model wrote it from
 *     retrieved passages, or a deterministic rule answered without calling a model;
 *  2. **that the data is fictional** — on every answer, not just once per page, because an
 *     answer gets screenshotted and pasted on its own;
 *  3. **what the citation check found** — in words that never say "verified". A citation
 *     is a pointer to a passage the model was given; nothing here checked that the sentence
 *     beside it is true.
 *
 * Below the answer, each section renders only when the backend sent something for it.
 */

import { useId } from "react";
import { possessive, priorityLevelLabel, unsupportedLabel } from "@/lib/assistant-format";
import type {
  AnswerKind,
  AskRequest,
  AskResponse,
  AttributionStatus,
  AssistantSource,
} from "@/lib/assistant-types";
import { AnswerBody } from "@/components/assistant/answer-body";
import { ClaimBreakdown } from "@/components/assistant/claim-breakdown";
import { SourceChips } from "@/components/assistant/citation";
import { DebugPanel } from "@/components/assistant/debug-panel";
import { EvidenceGridTable } from "@/components/assistant/evidence-grid";
import { Badge, Button, Explainer } from "@/components/ui";

type OpenSource = (sourceId: string, opener: HTMLElement) => void;

const KIND: Record<AnswerKind, { status: string; label: string; title: string }> = {
  grounded_answer: {
    status: "info",
    label: "Grounded answer",
    title: "The local model answered from the passages retrieved for this question.",
  },
  partial_abstention: {
    status: "warning",
    label: "Partly answered",
    title: "The model answered part of the question; the corpus could not support the rest.",
  },
  abstention: {
    status: "unavailable",
    label: "Not in the corpus",
    title: "Nothing in the fictional corpus answers this. No model was called.",
  },
  clarification: {
    status: "warning",
    label: "Needs clarification",
    title: "The question is ambiguous, so the assistant asked instead of guessing. No model was called.",
  },
  unsupported_request: {
    status: "unavailable",
    label: "Outside the sandbox",
    title: "This asks for something the sandbox cannot do. No model was called.",
  },
};

export function AnswerKindBadge({ kind }: { kind: AnswerKind }) {
  const k = KIND[kind];
  return (
    <span title={k.title}>
      <Badge status={k.status}>{k.label}</Badge>
    </span>
  );
}

export function FictionalTag() {
  return (
    <span
      title="Every team, player, scout and report here is invented. None of it is NBA data."
      className="inline-flex items-center whitespace-nowrap rounded border border-conditional/45 px-1.5 py-[2px] text-[10px] font-semibold uppercase leading-tight tracking-[0.1em] text-conditional"
    >
      Fictional data
    </span>
  );
}

function AttributionLine({ status, invalid }: { status: AttributionStatus; invalid: string[] }) {
  switch (status) {
    case "citations_present_not_fact_verified":
      return (
        <span className="text-[12px] text-muted">
          <span aria-hidden>• </span>Citations present — not fact-checked
        </span>
      );
    case "citations_include_invalid_ids":
      return (
        <span className="text-[12px] font-medium text-illegal">
          <span aria-hidden>✕ </span>Cites ids that were not retrieved
          {invalid.length > 0 ? ` (${invalid.join(", ")})` : ""}
        </span>
      );
    case "no_citations":
      return (
        <span className="text-[12px] font-medium text-conditional">
          <span aria-hidden>! </span>No citations — nothing ties this answer to a passage
        </span>
      );
    case "not_applicable_no_model_answer":
      return (
        <span className="text-[12px] text-faint">
          <span aria-hidden>— </span>Deterministic reply — no model answer to check
        </span>
      );
  }
}

/** Checker findings that say the answer text itself is wrong or unsafe, not merely thin. */
const SERIOUS_WARNINGS = new Set([
  "invalid_citation",
  "number_contradicts_manifest",
  "number_for_unavailable_stat",
  "unsupported_legality_claim",
  "invented_fit_score",
  "injection_marker_echoed",
  "citation_not_supporting",
  "claim_contradicts_assessment",
]);

function SubSection({
  title,
  children,
  tone = "plain",
}: {
  title: string;
  children: React.ReactNode;
  tone?: "plain" | "conditional" | "unavailable";
}) {
  return (
    <section
      aria-label={title}
      className={
        tone === "conditional"
          ? "rounded-lg border border-conditional/30 bg-conditional/[0.05] px-4 py-3"
          : tone === "unavailable"
            ? "rounded-lg border border-dashed border-unavail/45 bg-unavail/[0.05] px-4 py-3"
            : ""
      }
    >
      <h4 className="eyebrow mb-2 text-foreground">{title}</h4>
      {children}
    </section>
  );
}

export function AssistantAnswer({
  response,
  turnNumber,
  sources,
  onOpenSource,
  onShowSources,
  onFollowUp,
  followUpsDisabled,
  debug,
  selectedSourceId,
  dimensionLabel,
  shownInInspector,
  request,
}: {
  response: AskResponse;
  turnNumber: number;
  sources: AssistantSource[];
  onOpenSource: OpenSource;
  onShowSources: (opener: HTMLElement) => void;
  onFollowUp: (question: string) => void;
  followUpsDisabled: boolean;
  debug: boolean;
  selectedSourceId: string | null;
  dimensionLabel: (key: string) => string;
  shownInInspector: boolean;
  /** The request this answers, when known — it says whether the GM sent any priorities. */
  request?: AskRequest;
}) {
  const headingId = useId();
  const ctx = response.context_used;
  // Roster fit with a team, and the GM sent no priorities: the backend fell back to the
  // team's documented defaults (`policy.effective_priorities`) and echoes *those* here.
  const teamDefaultPriorities =
    request !== undefined &&
    Object.keys(request.priorities).length === 0 &&
    ctx.mode === "roster_fit" &&
    ctx.team_id !== null &&
    Object.keys(ctx.priorities).length > 0;
  const teamLabel = ctx.team_name ? possessive(ctx.team_name) : "team’s";
  const serious = response.warnings.filter((w) => SERIOUS_WARNINGS.has(w.code));
  const notes = response.warnings.length - serious.length;

  return (
    <article aria-labelledby={headingId} className="panel fade-in min-w-0 overflow-hidden">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-hairline px-4 py-3 sm:px-5">
        <h3 id={headingId} className="sr-only">
          Answer {turnNumber}
        </h3>
        <AnswerKindBadge kind={response.answer_kind} />
        <FictionalTag />
        <AttributionLine status={response.attribution_status} invalid={response.invalid_citations} />
        <span className="ml-auto flex items-center gap-2">
          {shownInInspector && (
            <span className="hidden text-[11px] text-faint xl:inline">Shown in Evidence</span>
          )}
          <Button
            size="sm"
            variant="ghost"
            disabled={sources.length === 0}
            onClick={(event) => onShowSources(event.currentTarget)}
            aria-label={`Sources for answer ${turnNumber} (${sources.length})`}
          >
            Sources ({sources.length})
          </Button>
        </span>
      </header>

      <div className="space-y-5 px-4 py-4 sm:px-5">
        {ctx.priority_changes.length > 0 && (
          <p className="text-[12px] leading-relaxed text-muted">
            <span className="font-medium text-foreground">Priorities changed since the last answer: </span>
            {/* With team defaults in force, "X removed" would contradict the list below. */}
            {teamDefaultPriorities
              ? `you cleared yours, so the ${teamLabel} documented defaults were used instead.`
              : ctx.priority_changes
                  .map((c) =>
                    !c.before && c.after
                      ? `${c.label} added (${priorityLevelLabel(c.after)})`
                      : c.before && !c.after
                        ? `${c.label} removed (was ${priorityLevelLabel(c.before)})`
                        : `${c.label} ${priorityLevelLabel(c.before ?? "")} → ${priorityLevelLabel(c.after ?? "")}`,
                  )
                  .join("; ")}
          </p>
        )}

        <AnswerBody
          text={response.answer}
          sources={sources}
          onOpen={onOpenSource}
          selectedSourceId={selectedSourceId}
        />

        <ClaimBreakdown claims={response.claims ?? []} summary={response.claim_summary ?? null} />

        {response.limitations.length > 0 && (
          <SubSection title="Limitations">
            <ul className="list-disc space-y-1 pl-5 text-[13px] leading-relaxed text-muted marker:text-faint">
              {response.limitations.map((l, i) => (
                <li key={`${i}-${l}`}>{l}</li>
              ))}
            </ul>
          </SubSection>
        )}

        {response.conflicts.length > 0 && (
          <SubSection title="Sources disagree" tone="conditional">
            <ul className="space-y-3">
              {response.conflicts.map((c) => (
                <li key={c.id} className="text-[13px] leading-relaxed">
                  <p className="text-foreground">
                    <span aria-hidden className="mr-1 text-conditional">
                      ~
                    </span>
                    {c.summary}{" "}
                    <SourceChips ids={c.source_ids} sources={sources} onOpen={onOpenSource} />
                  </p>
                  <p className="mt-1 text-[12.5px] text-muted">{c.guidance}</p>
                </li>
              ))}
            </ul>
          </SubSection>
        )}

        {response.unsupported_requests.length > 0 && (
          <SubSection title="Outside the sandbox" tone="unavailable">
            <ul className="space-y-2">
              {response.unsupported_requests.map((u) => (
                <li key={`${u.kind}-${u.message}`} className="text-[13px] leading-relaxed text-muted">
                  <span aria-hidden className="mr-1 text-unavail">
                    —
                  </span>
                  <span className="font-medium text-foreground">{unsupportedLabel(u.kind)}.</span>{" "}
                  {u.message}
                </li>
              ))}
            </ul>
          </SubSection>
        )}

        {response.evidence_grid && (
          <SubSection title="Evidence grid">
            <EvidenceGridTable
              grid={response.evidence_grid}
              sources={sources}
              conflicts={response.conflicts}
              dimensionLabel={dimensionLabel}
              onOpen={onOpenSource}
            />
          </SubSection>
        )}

        {response.guard?.applied && (
          <p role="status" className="rounded-lg border border-dashed border-illegal/40 px-3 py-2 text-[12.5px] leading-relaxed text-muted">
            <span aria-hidden className="mr-1 text-illegal">
              ✕
            </span>
            <span className="font-medium text-foreground">
              The checks removed {response.guard.removals.length} sentence
              {response.guard.removals.length === 1 ? "" : "s"} from the model’s answer
            </span>{" "}
            before showing it — each is marked where it stood. The model’s original text is in Debug.
          </p>
        )}

        {response.warnings.length > 0 && (
          <section aria-label="Checks">
            {serious.length > 0 && (
              <p className="mb-2 text-[12.5px] font-medium text-illegal">
                <span aria-hidden>✕ </span>
                The answer checker flagged {serious.length} problem{serious.length === 1 ? "" : "s"} in
                this answer — read it with that in mind.
              </p>
            )}
            <Explainer
              summary={
                <span>
                  Checks ·{" "}
                  {[
                    serious.length ? `${serious.length} flagged` : null,
                    notes ? `${notes} note${notes === 1 ? "" : "s"}` : null,
                  ]
                    .filter(Boolean)
                    .join(", ")}
                </span>
              }
            >
              <ul className="space-y-2">
                {response.warnings.map((w, i) => (
                  <li key={`${w.code}-${i}`}>
                    <code
                      className={
                        SERIOUS_WARNINGS.has(w.code) ? "text-[11.5px] text-illegal" : "text-[11.5px] text-muted"
                      }
                    >
                      {w.code}
                    </code>{" "}
                    <span className="text-muted">{w.message}</span>{" "}
                    <SourceChips ids={w.source_ids} sources={sources} onOpen={onOpenSource} />
                  </li>
                ))}
              </ul>
            </Explainer>
          </section>
        )}

        <Explainer summary="What the assistant was told">
          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            <div>
              <dt className="text-faint">Team</dt>
              <dd className="text-foreground">{ctx.team_name ?? "No team selected"}</dd>
            </div>
            <div>
              <dt className="text-faint">Mode</dt>
              <dd className="text-foreground">{ctx.mode === "roster_fit" ? "Roster fit" : "Research"}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-faint">
                {teamDefaultPriorities
                  ? `Priorities used — the ${teamLabel} documented defaults (you set none)`
                  : "Your priorities"}
              </dt>
              <dd className="text-foreground">
                {Object.keys(ctx.priorities).length
                  ? Object.entries(ctx.priorities)
                      .map(([k, v]) => `${dimensionLabel(k)} · ${priorityLevelLabel(v)}`)
                      .join("; ")
                  : "None"}
              </dd>
            </div>
            {ctx.constraints.length > 0 && (
              <div className="sm:col-span-2">
                <dt className="text-faint">Your constraints</dt>
                <dd>
                  <ul className="list-disc pl-4 text-foreground">
                    {ctx.constraints.map((c, i) => (
                      <li key={`${i}-${c}`}>{c}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            )}
            <div className="sm:col-span-2">
              <dt className="text-faint">Conversation history</dt>
              <dd className="text-foreground">
                {ctx.history_messages_used} of {ctx.history_messages_received} earlier messages used
                {ctx.history_truncated ? " — older ones were left out to fit the model's context" : ""}
              </dd>
            </div>
            {ctx.gm_statements.length > 0 && (
              <div className="sm:col-span-2">
                <dt className="text-faint">
                  Things you said — treated as your hypotheses, not as evidence
                </dt>
                <dd>
                  <ul className="list-disc pl-4 text-foreground">
                    {ctx.gm_statements.map((s, i) => (
                      <li key={`${i}-${s}`}>{s}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            )}
          </dl>
        </Explainer>

        {response.follow_ups.length > 0 && (
          <section aria-label="Follow-up questions">
            <h4 className="eyebrow mb-2">Follow up</h4>
            <ul className="flex flex-wrap gap-2">
              {response.follow_ups.map((q, i) => (
                <li key={`${i}-${q}`} className="min-w-0 max-w-full">
                  <button
                    type="button"
                    disabled={followUpsDisabled}
                    onClick={() => onFollowUp(q)}
                    className="max-w-full rounded-lg border border-line bg-panel2 px-3 py-1.5 text-left text-[12.5px] leading-snug text-muted transition-colors hover:border-signal/45 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40 [overflow-wrap:anywhere]"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        {debug && <DebugPanel response={response} />}
      </div>

      <footer className="border-t border-hairline px-4 py-2.5 text-[11.5px] leading-relaxed text-faint sm:px-5">
        {response.attribution_note}
      </footer>
    </article>
  );
}
