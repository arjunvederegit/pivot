"use client";

/**
 * Suggested deals — **parked for the R5 rebuild** (R1-8).
 *
 * This renders the output of `POST /trades/generate`. The endpoint and this component
 * both work; what does not work is the search behind them. Measured, the generator
 * reaches **13.8 %** of counterparties (4 of 29): it exhausts a 400-evaluation budget
 * after roughly six teams, walks them in unordered insertion order, and discloses none
 * of that. With no salary matching it proposed Donovan Mitchell *and* James Harden for
 * Jordan Walsh, at a counterparty utility of 52.0 — above the 42.0 acceptance floor
 * (QA-10).
 *
 * The Trade Evaluator's entry point is removed until R5 makes the search
 * salary-matched, deterministically ordered and honest about truncation. Keeping the
 * component intact means that rebuild starts from working scaffolding.
 */

import { LEGALITY_SHORT } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type { GeneratedCandidate } from "@/lib/types";
import { TeamLogo } from "@/components/media";
import { Badge, Button, EmptyState, Panel, SourceRail } from "@/components/ui";

export function SuggestedDeals({
  note,
  candidates,
  focalTeamId,
  onLoad,
  onDismiss,
}: {
  note: string;
  candidates: GeneratedCandidate[];
  focalTeamId: string | undefined;
  onLoad: (candidate: GeneratedCandidate) => void;
  onDismiss: () => void;
}) {
  return (
    <Panel
      title="Suggested deals"
      subtitle={note}
      accent="var(--signal)"
      actions={
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      }
    >
      {candidates.length === 0 ? (
        <EmptyState
          title="No candidate cleared the constraints"
          hint="Loosen the untouchable list in your strategy, or add a second team by hand and build the deal yourself."
        />
      ) : (
        <ul className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
          {candidates.map((candidate, i) => {
            const identity = teamIdentity(candidate.counterparty.abbreviation);
            return (
              <li
                key={`${candidate.counterparty.team_id}-${i}`}
                className="rounded-lg border border-hairline bg-panel2/60 p-3"
                style={{ borderTopColor: identity.bright }}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <TeamLogo abbreviation={candidate.counterparty.abbreviation} size={22} decorative />
                    <span className="numeral whitespace-nowrap text-base">
                      {candidate.counterparty.abbreviation}
                    </span>
                  </span>
                  <Badge status={candidate.legality_status}>
                    {LEGALITY_SHORT[candidate.legality_status]}
                  </Badge>
                </div>
                <dl className="mt-2 space-y-1 text-[12px]">
                  <div className="flex gap-1.5">
                    <dt className="eyebrow shrink-0 pt-0.5 text-[0.625rem] text-illegal">Out</dt>
                    <dd className="min-w-0 text-muted">
                      {candidate.outgoing.map((p) => p.name).join(", ") || "—"}
                    </dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt className="eyebrow shrink-0 pt-0.5 text-[0.625rem] text-legal">In</dt>
                    <dd className="min-w-0 text-muted">
                      {candidate.incoming.map((p) => p.name).join(", ") || "—"}
                    </dd>
                  </div>
                </dl>
                <p className="mt-2 text-[11px] leading-snug text-faint">{candidate.rationale}</p>
                <Button
                  size="sm"
                  variant="secondary"
                  className="mt-2.5 w-full"
                  disabled={!focalTeamId}
                  onClick={() => onLoad(candidate)}
                >
                  Load into the builder
                </Button>
              </li>
            );
          })}
        </ul>
      )}
      <SourceRail source="Pivot candidate search over ingested rosters and impact estimates" />
    </Panel>
  );
}
