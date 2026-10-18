"use client";

/**
 * "How this answer breaks down": every sentence labelled as an observed fact, an analyst's
 * view, the assistant's own inference, or something the sources do not contain.
 *
 * The labels are assigned by the backend after generation (`assistant_rag/epistemics.py`)
 * from what each sentence cites and from the fact manifest — not by the model — so the
 * distinction holds even when a small local model ignores its formatting instructions.
 * A number that matches the fact sheet gets a ✓; one that contradicts it gets a ✕.
 *
 * A sentence the answer guard removed is not a statement of any kind: its marker is shown
 * as a "removed" row (as the answer body shows it) and left out of the counts — whether the
 * backend sent it as a claim or not.
 */

import { CLAIM_KINDS, claimKindLabel, isRemovedMarker, splitRemovedMarkers } from "@/lib/assistant-format";
import type { AssistantClaim, ClaimSummary } from "@/lib/assistant-types";
import { Explainer } from "@/components/ui";

/** Not a backend kind: the tag for a row that is only a guard marker. */
const REMOVED = "removed";

const KIND_TONE: Record<string, string> = {
  [REMOVED]: "border-dashed border-illegal/45 text-illegal",
  observed_fact: "border-legal/40 text-legal",
  analyst_opinion: "border-signal/40 text-signal",
  inference: "border-line text-foreground",
  unavailable: "border-dashed border-unavail/50 text-unavail",
  untrusted_claim: "border-conditional/45 text-conditional",
  question: "border-line text-muted",
};

function KindTag({ kind }: { kind: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded border px-1.5 py-[1px] text-[10px] font-semibold uppercase tracking-[0.08em] ${
        KIND_TONE[kind] ?? "border-line text-muted"
      }`}
    >
      {kind === REMOVED ? "Removed by checks" : claimKindLabel(kind)}
    </span>
  );
}

function CheckGlyph({ check }: { check: string }) {
  if (check === "matches_manifest")
    return (
      <span className="text-legal" title="The number matches the fact sheet">
        ✓<span className="sr-only"> number matches the fact sheet</span>
      </span>
    );
  if (check === "contradicts_manifest")
    return (
      <span className="text-illegal" title="A number here contradicts the fact sheet">
        ✕<span className="sr-only"> number contradicts the fact sheet</span>
      </span>
    );
  if (check === "unverified")
    return (
      <span className="text-conditional" title="A number here could not be checked">
        ~<span className="sr-only"> number could not be checked</span>
      </span>
    );
  return null;
}

/** A claim's text, with any guard marker glued onto it shown as the note it is. */
function ClaimText({ text }: { text: string }) {
  return (
    <>
      {splitRemovedMarkers(text).map((part, i) =>
        part.type === "text" ? (
          <span key={i}>{part.text}</span>
        ) : (
          <span
            key={i}
            className="mx-0.5 inline rounded border border-dashed border-illegal/45 bg-illegal/[0.06] px-1.5 py-[1px] text-[11.5px]"
          >
            <span aria-hidden className="mr-1 text-illegal">
              ✕
            </span>
            <span className="font-medium text-foreground">Removed by Ask Pivot’s checks:</span> {part.reason}.
          </span>
        ),
      )}
    </>
  );
}

export function ClaimBreakdown({ claims, summary }: { claims: AssistantClaim[]; summary: ClaimSummary | null }) {
  const removed = claims.filter((c) => isRemovedMarker(c.text));
  if (claims.length === removed.length) return null;
  let counts: Record<string, number>;
  if (summary) {
    // An older backend counted each marker under the kind it guessed for it; take them back out.
    counts = { ...summary };
    for (const c of removed) if ((counts[c.kind] ?? 0) > 0) counts[c.kind] -= 1;
  } else {
    counts = claims
      .filter((c) => !removed.includes(c))
      .reduce<Record<string, number>>((acc, c) => ({ ...acc, [c.kind]: (acc[c.kind] ?? 0) + 1 }), {});
  }
  const present = CLAIM_KINDS.filter((k) => (counts[k.kind] ?? 0) > 0);

  return (
    <section aria-label="How this answer breaks down">
      <h4 className="eyebrow mb-2 text-foreground">How this answer breaks down</h4>
      <ul className="flex flex-wrap gap-2" aria-label="Kinds of statement in this answer">
        {present.map((k) => (
          <li key={k.kind} title={k.hint} className="flex items-center gap-1.5 text-[12px] text-muted">
            <KindTag kind={k.kind} />
            <span className="numeral text-foreground">{counts[k.kind]}</span>
          </li>
        ))}
      </ul>
      <Explainer summary="Sentence by sentence" className="mt-2">
        <ol className="space-y-2.5">
          {claims.map((c, i) => {
            const isRemoved = removed.includes(c);
            return (
              <li key={`${i}-${c.text.slice(0, 24)}`} className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-start sm:gap-3">
                <span className="flex items-center gap-1.5 sm:w-[9.5rem] sm:shrink-0">
                  <KindTag kind={isRemoved ? REMOVED : c.kind} />
                  {!isRemoved && <CheckGlyph check={c.check} />}
                </span>
                <span className="min-w-0 text-[12.5px] leading-relaxed text-muted [overflow-wrap:anywhere]">
                  {/* The sentence keeps its own [Sxx] markers; the citation buttons live in the
                      answer above, so each source has one control with its accessible name. */}
                  {isRemoved ? (
                    splitRemovedMarkers(c.text)
                      .flatMap((part) => (part.type === "removed" ? [`${part.reason}.`] : []))
                      .join(" ")
                  ) : (
                    <ClaimText text={c.text} />
                  )}
                  {!isRemoved && c.note && <span className="mt-0.5 block text-[11.5px] text-faint">{c.note}</span>}
                </span>
              </li>
            );
          })}
        </ol>
      </Explainer>
    </section>
  );
}
