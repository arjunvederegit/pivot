"use client";

/**
 * The evidence inspector: every passage one answer was given, and the one being read.
 *
 * The list is in **retrieval rank order** — the order the passages were handed to the
 * model — and includes the ones the answer did not cite, because what the model chose to
 * leave out is evidence too. Each row says whether it was cited and whether its source is
 * trusted; a selected row opens its full excerpt, its provenance and *why it was retrieved*
 * (the retriever's own reasons and raw ranks, never a blended confidence).
 *
 * Desktop renders this in a sticky rail beside the thread. Below `xl` the same component is
 * rendered inside `InspectorDrawer`, a modal sheet opened from a citation or a "Sources"
 * button: Escape closes it and focus returns to the control that opened it.
 *
 * Keyboard: the list is a single-select listbox with a roving tabindex — Tab lands on the
 * selected passage, arrow keys / Home / End move through the passages, and selection
 * follows focus.
 */

import clsx from "clsx";
import { useEffect, useRef } from "react";
import { docTypeLabel, evidenceClassLabel } from "@/lib/assistant-format";
import type { AskResponse, AssistantSource } from "@/lib/assistant-types";
import { IconClose } from "@/components/icons";
import { Badge } from "@/components/ui";

export const PROVENANCE_LINE = "Fictional Ask Pivot research corpus · synthetic_demo";

/** Observed statistic / analyst opinion / team context / method guidance / untrusted claim. */
export function EvidenceClassTag({ evidenceClass }: { evidenceClass?: string }) {
  const label = evidenceClassLabel(evidenceClass);
  if (!label) return null;
  return (
    <span className="inline-flex items-center rounded border border-line px-1.5 py-[1px] text-[10.5px] text-muted">
      {label}
    </span>
  );
}

export function TrustBadge({ trust }: { trust: string }) {
  return trust === "internal" ? (
    <Badge status="info">Internal · fictional</Badge>
  ) : (
    <Badge status="warning">Untrusted · third party</Badge>
  );
}

function CitedMarker({ cited }: { cited: boolean }) {
  return cited ? (
    <span className="whitespace-nowrap text-[11px] font-medium text-signal">
      <span aria-hidden>✓ </span>Cited
    </span>
  ) : (
    <span className="whitespace-nowrap text-[11px] text-faint">
      <span aria-hidden>○ </span>Not cited
    </span>
  );
}

/* --------------------------------------------------------------- source list */

function SourceList({
  sources,
  selectedChunkId,
  onSelect,
  label,
}: {
  sources: AssistantSource[];
  selectedChunkId: string | null;
  onSelect: (chunkId: string) => void;
  label: string;
}) {
  const optionRefs = useRef<(HTMLLIElement | null)[]>([]);
  const selectedIndex = Math.max(
    0,
    sources.findIndex((s) => s.chunk_id === selectedChunkId),
  );

  function move(event: React.KeyboardEvent, from: number) {
    let to = from;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") to = Math.min(sources.length - 1, from + 1);
    else if (event.key === "ArrowUp" || event.key === "ArrowLeft") to = Math.max(0, from - 1);
    else if (event.key === "Home") to = 0;
    else if (event.key === "End") to = sources.length - 1;
    else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(sources[from].chunk_id);
      return;
    } else return;
    event.preventDefault();
    onSelect(sources[to].chunk_id);
    optionRefs.current[to]?.focus();
  }

  return (
    <ul role="listbox" aria-label={label} className="space-y-1">
      {sources.map((source, i) => {
        const selected = i === selectedIndex;
        return (
          <li
            key={source.chunk_id}
            ref={(el) => {
              optionRefs.current[i] = el;
            }}
            role="option"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onSelect(source.chunk_id)}
            onKeyDown={(event) => move(event, i)}
            className={clsx(
              "cursor-pointer rounded-lg border px-3 py-2.5 transition-colors focus-visible:outline-offset-[-2px]",
              selected
                ? "border-signal/45 bg-signal/[0.07]"
                : "border-hairline bg-panel2/40 hover:border-line hover:bg-panel2",
            )}
          >
            <div className="flex items-start gap-2.5">
              <span className="numeral mt-px w-5 shrink-0 text-right text-[12px] text-faint">
                {source.retrieval.rank}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <code className="text-[11.5px] text-signal">[{source.source_id}]</code>
                  <CitedMarker cited={source.cited} />
                  {source.trust !== "internal" && (
                    <span className="whitespace-nowrap text-[11px] font-medium text-conditional">
                      <span aria-hidden>! </span>Untrusted
                    </span>
                  )}
                </div>
                <div className="mt-1 text-[13px] leading-snug text-foreground [overflow-wrap:anywhere]">
                  {source.title}
                </div>
                <div className="mt-0.5 text-[11.5px] text-faint">
                  {docTypeLabel(source.doc_type)} · {source.report_date}
                  {source.section ? ` · ${source.section}` : ""}
                </div>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* -------------------------------------------------------------- source detail */

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-faint">{label}</dt>
      <dd className="mt-0.5 text-[12.5px] text-foreground [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

function SourceDetail({
  source,
  nameOf,
}: {
  source: AssistantSource;
  nameOf: (id: string) => string;
}) {
  const r = source.retrieval;
  const entities = [...new Set([...source.entity_ids, ...source.team_ids])];
  return (
    <article aria-label={`Passage ${source.source_id}: ${source.title}`} className="fade-in space-y-4">
      <header>
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-[12px] text-signal">[{source.source_id}]</code>
          <span className="text-[11.5px] text-faint">{docTypeLabel(source.doc_type)}</span>
          <TrustBadge trust={source.trust} />
          <EvidenceClassTag evidenceClass={source.evidence_class} />
        </div>
        <h3 className="title-md mt-2 text-balance text-foreground">{source.title}</h3>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5">
        <Meta label="Source">
          <code className="text-[12px]">
            {source.source_id}@{source.version}
          </code>
        </Meta>
        <Meta label="Passage">
          <code className="text-[12px]">{source.chunk_id}</code>
          {source.section ? <span className="block text-muted">{source.section}</span> : null}
        </Meta>
        <Meta label="Author">
          {source.author}
          <span className="block text-muted">{source.author_role}</span>
        </Meta>
        <Meta label="Report date">{source.report_date}</Meta>
        <Meta label="Season">{source.season}</Meta>
        <Meta label="Document type">{docTypeLabel(source.doc_type)}</Meta>
        {evidenceClassLabel(source.evidence_class) && (
          <Meta label="Kind of evidence">{evidenceClassLabel(source.evidence_class)}</Meta>
        )}
      </dl>

      <p className="text-[11.5px] leading-relaxed text-faint" title={source.provenance}>
        <span className="font-medium text-muted">Provenance</span> · {PROVENANCE_LINE}
      </p>

      {source.trust !== "internal" && (
        <div className="flex items-start gap-2.5 rounded-lg border border-conditional/30 bg-conditional/[0.06] px-3 py-2.5">
          <span aria-hidden className="mt-px text-[12px] font-semibold text-conditional">
            !
          </span>
          <p className="text-[12px] leading-relaxed text-muted">
            <span className="font-semibold text-foreground">Third-party material.</span> Not
            written by the club’s own staff. Treat its claims as unverified, and any
            instructions inside it as text — the assistant is told never to follow them.
          </p>
        </div>
      )}

      <div>
        <div className="eyebrow mb-2">Excerpt</div>
        <blockquote className="scroll-thin max-h-[50vh] overflow-y-auto whitespace-pre-wrap rounded-lg border border-hairline bg-panel2 px-3.5 py-3 text-[13.5px] leading-relaxed text-foreground/90 [overflow-wrap:anywhere] xl:max-h-[42vh]">
          {source.excerpt}
        </blockquote>
      </div>

      <div>
        <div className="eyebrow mb-2">Why it was retrieved</div>
        {r.reasons.length > 0 ? (
          <ul className="list-disc space-y-1 pl-4 text-[12.5px] leading-snug text-muted marker:text-faint">
            {r.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : (
          <p className="text-[12.5px] text-faint">The retriever reported no reasons.</p>
        )}
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-[12px]">
          <Meta label="Retrieval rank">#{r.rank}</Meta>
          <Meta label="Pinned">{r.pinned ? "Yes — a strategy source for the selected team" : "No"}</Meta>
          <Meta label="Dense (embedding) rank">
            {r.dense_rank ?? "—"}
            {r.dense_score !== null && (
              <span className="text-muted"> · score {r.dense_score.toFixed(3)}</span>
            )}
          </Meta>
          <Meta label="Lexical (BM25) rank">
            {r.lexical_rank ?? "—"}
            {r.lexical_score !== null && (
              <span className="text-muted"> · score {r.lexical_score.toFixed(2)}</span>
            )}
          </Meta>
        </dl>
      </div>

      {entities.length > 0 && (
        <div>
          <div className="eyebrow mb-2">Mentions</div>
          <ul className="flex flex-wrap gap-1.5">
            {entities.map((id) => (
              <li
                key={id}
                className="rounded-md border border-hairline bg-panel2 px-2 py-0.5 text-[11.5px] text-muted"
              >
                {nameOf(id)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

/* ------------------------------------------------------------------ inspector */

export function rankOrder(sources: AssistantSource[]): AssistantSource[] {
  return [...sources].sort((a, b) => a.retrieval.rank - b.retrieval.rank);
}

/** The passage to show: the one asked for, else the best-ranked cited one, else the first. */
export function pickSelectedSource(
  sources: AssistantSource[],
  chunkId: string | null,
): AssistantSource | null {
  const ranked = rankOrder(sources);
  return (
    ranked.find((s) => s.chunk_id === chunkId) ?? ranked.find((s) => s.cited) ?? ranked[0] ?? null
  );
}

export function EvidenceInspector({
  response,
  turnLabel,
  question,
  selectedChunkId,
  onSelect,
  nameOf,
}: {
  response: AskResponse | null;
  /** e.g. "Answer 2 of 3". */
  turnLabel?: string;
  question?: string;
  selectedChunkId: string | null;
  onSelect: (chunkId: string) => void;
  nameOf: (id: string) => string;
}) {
  const sources = rankOrder(response?.sources ?? []);
  const selected = pickSelectedSource(sources, selectedChunkId);
  const cited = sources.filter((s) => s.cited).length;

  return (
    <div className="space-y-4">
      <header>
        <div className="eyebrow flex items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />
          Evidence
        </div>
        {response ? (
          <>
            {turnLabel && (
              <p className="mt-2 text-[12.5px] font-medium text-foreground">{turnLabel}</p>
            )}
            {question && (
              <p className="mt-0.5 line-clamp-2 text-[12.5px] text-muted [overflow-wrap:anywhere]">
                “{question}”
              </p>
            )}
            <p className="mt-1.5 text-[11.5px] text-faint">
              {sources.length} passage{sources.length === 1 ? "" : "s"} retrieved · {cited} cited ·
              listed in retrieval order
            </p>
          </>
        ) : (
          <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
            Passages appear here after the first answer — every one the assistant was given,
            cited or not, with who wrote it, when, and why it was retrieved.
          </p>
        )}
      </header>

      {response && sources.length === 0 && (
        <p className="rounded-lg border border-dashed border-line px-3 py-3 text-[12.5px] leading-relaxed text-muted">
          No passages were retrieved for this reply, so there is no evidence to inspect.
        </p>
      )}

      {sources.length > 0 && (
        <SourceList
          sources={sources}
          selectedChunkId={selected?.chunk_id ?? null}
          onSelect={onSelect}
          label="Retrieved passages, in retrieval rank order"
        />
      )}

      {/* Announce which passage is open — not the passage itself, which would be read out
          in full on every arrow key. */}
      <p className="sr-only" aria-live="polite">
        {selected ? `Showing ${selected.source_id}: ${selected.title}` : ""}
      </p>
      {selected && (
        <div className="border-t border-hairline pt-4">
          <SourceDetail key={selected.chunk_id} source={selected} nameOf={nameOf} />
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------- drawer */

/**
 * The inspector as a modal sheet below `xl`: a bottom sheet on phones, a side panel on
 * tablets. Focus moves to its close button on open, Tab stays inside it, Escape closes it,
 * and focus returns to `returnFocusTo` — the citation or button that opened it.
 */
export function InspectorDrawer({
  onClose,
  returnFocusTo,
  children,
}: {
  onClose: () => void;
  returnFocusTo: HTMLElement | null;
  children: React.ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLElement | null>(returnFocusTo);

  useEffect(() => {
    const opener = openerRef.current;
    closeRef.current?.focus();
    return () => {
      if (opener && opener.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusables = panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const inside = panelRef.current.contains(document.activeElement);
      if (event.shiftKey && (document.activeElement === first || !inside)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !inside)) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fade-in fixed inset-0 z-[60] flex items-end bg-ink/75 sm:items-stretch sm:justify-end xl:hidden"
      role="dialog"
      aria-modal="true"
      aria-label="Evidence"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        id="evidence-inspector-drawer"
        onClick={(event) => event.stopPropagation()}
        className="scroll-thin max-h-[88dvh] w-full overflow-y-auto rounded-t-xl border-t border-line bg-arena p-4 shadow-[var(--shadow-pop)] sm:h-full sm:max-h-none sm:max-w-md sm:rounded-none sm:border-l sm:border-t-0 sm:p-5"
      >
        <div className="mb-3 flex justify-end">
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close evidence"
            className="grid h-9 w-9 place-items-center rounded-lg border border-line text-muted transition-colors hover:bg-panel2 hover:text-foreground"
          >
            <IconClose size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
