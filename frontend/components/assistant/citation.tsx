"use client";

/**
 * Source-id chips.
 *
 * Three states, each with a glyph or word as well as a colour:
 *  - **retrieved** — a mint-outlined button. It opens the passage in the evidence
 *    inspector. "Retrieved" is all it proves: the passage exists and was read for this
 *    answer, not that the sentence beside it is true.
 *  - **not retrieved, in the answer** — coral with ✕, not clickable. The model cited an id
 *    that was not among the passages it was given, so the claim beside it has no retrieved
 *    support (the backend flags the same id as `citations_include_invalid_ids`).
 *  - **reference** — a quiet outline, not clickable. A source the *deterministic* parts of
 *    the response point at (an evidence-grid cell, a known conflict) that happened not to be
 *    retrieved for this question. Not an error — just nothing to open.
 */

import clsx from "clsx";
import type { AssistantSource } from "@/lib/assistant-types";

const BASE =
  "inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-px align-baseline text-[11.5px] font-medium leading-[1.35] tabular-nums";

export function CitationChip({
  id,
  source,
  onOpen,
  inAnswer = true,
  selected = false,
}: {
  id: string;
  /** The retrieved passage this id points at, if any. */
  source?: AssistantSource;
  onOpen?: (sourceId: string, opener: HTMLElement) => void;
  /** True when the model wrote this citation (an unmatched id is then an error). */
  inAnswer?: boolean;
  selected?: boolean;
}) {
  if (source && onOpen) {
    return (
      <button
        type="button"
        aria-label={`Source ${id}: ${source.title}`}
        title={`${id} · ${source.title}`}
        onClick={(event) => onOpen(id, event.currentTarget)}
        className={clsx(
          BASE,
          "cursor-pointer border-signal/50 text-signal transition-colors hover:bg-signal/10",
          selected && "bg-signal/15",
        )}
      >
        {id}
      </button>
    );
  }
  if (inAnswer) {
    return (
      <span
        title={`${id} was cited but was not among the passages retrieved for this answer, so the claim it is attached to has no retrieved support.`}
        className={clsx(BASE, "border-illegal/50 bg-illegal/10 text-illegal")}
      >
        <span aria-hidden>✕</span>
        {id}
        <span className="sr-only"> (cited, but not retrieved for this answer)</span>
      </span>
    );
  }
  return (
    <span
      title={`${id} was not among the passages retrieved for this question, so there is no excerpt to open.`}
      className={clsx(BASE, "border-line text-muted")}
    >
      {id}
      <span className="sr-only"> (not retrieved for this question)</span>
    </span>
  );
}

/** A row of chips for a list of ids, resolved against one answer's sources. */
export function SourceChips({
  ids,
  sources,
  onOpen,
  inAnswer = false,
}: {
  ids: string[];
  sources: AssistantSource[];
  onOpen?: (sourceId: string, opener: HTMLElement) => void;
  inAnswer?: boolean;
}) {
  if (ids.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {ids.map((id) => (
        <CitationChip
          key={id}
          id={id}
          source={sources.find((s) => s.source_id === id)}
          onOpen={onOpen}
          inAnswer={inAnswer}
        />
      ))}
    </span>
  );
}
