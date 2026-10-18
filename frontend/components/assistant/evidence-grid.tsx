"use client";

/**
 * The evidence grid: players down the side, the GM's priorities across the top, and in
 * each cell what a fictional scout *wrote* — their letter grade as written, a one-line
 * summary, and the source it came from.
 *
 * It is built deterministically by the backend from the corpus manifest, not by the model,
 * and it is deliberately not a ranking: no totals, no weights applied, no colour scale
 * implying better or worse. The caption says so in as many words. A grade is the analyst's
 * opinion in one dated report; where reports disagree the cell says that too.
 */

import type { AssistantConflict, AssistantSource, EvidenceGrid } from "@/lib/assistant-types";
import { CitationChip } from "@/components/assistant/citation";

export function EvidenceGridTable({
  grid,
  sources,
  conflicts,
  dimensionLabel,
  onOpen,
}: {
  grid: EvidenceGrid;
  sources: AssistantSource[];
  conflicts: AssistantConflict[];
  dimensionLabel: (key: string) => string;
  onOpen?: (sourceId: string, opener: HTMLElement) => void;
}) {
  if (grid.rows.length === 0 || grid.dimensions.length === 0) return null;
  const minWidth = 180 + grid.dimensions.length * 190;

  return (
    <figure className="min-w-0">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="title-sm text-foreground">{grid.label}</span>
        <span className="text-[11.5px] text-faint">Scroll sideways for every priority</span>
      </div>
      <div
        className="scroll-thin overflow-x-auto rounded-lg border border-hairline"
        role="region"
        aria-label={`${grid.label} — documented observations, scrollable`}
        tabIndex={0}
      >
        <table className="w-full border-collapse text-left" style={{ minWidth }}>
          <caption className="border-b border-hairline px-3 py-2 text-left text-[12px] font-medium text-conditional">
            <span aria-hidden className="mr-1.5">
              !
            </span>
            Documented observations — not a fit score
          </caption>
          <thead>
            <tr className="border-b border-hairline bg-panel2">
              <th scope="col" className="eyebrow sticky left-0 z-[1] bg-panel2 px-3 py-2.5">
                Player
              </th>
              {grid.dimensions.map((dim) => (
                <th key={dim} scope="col" className="eyebrow px-3 py-2.5 align-bottom">
                  {dimensionLabel(dim)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row) => (
              <tr key={row.player_id} className="border-b border-hairline last:border-b-0">
                <th
                  scope="row"
                  className="sticky left-0 z-[1] bg-panel px-3 py-3 align-top text-[13px] font-medium text-foreground"
                >
                  {row.player_name}
                  <span className="mt-0.5 block text-[11.5px] font-normal text-faint">
                    {row.current_club}
                  </span>
                </th>
                {grid.dimensions.map((dim) => {
                  const cell = row.cells.find((c) => c.dimension === dim);
                  if (!cell) {
                    return (
                      <td key={dim} className="px-3 py-3 align-top text-[12px] text-faint">
                        <span aria-hidden>— </span>Not documented
                      </td>
                    );
                  }
                  const conflict = cell.conflict_id
                    ? conflicts.find((c) => c.id === cell.conflict_id)
                    : undefined;
                  return (
                    <td key={dim} className="px-3 py-3 align-top">
                      <div className="flex items-baseline gap-2">
                        <span className="numeral text-[15px] text-foreground">
                          {cell.grade ?? (
                            <span className="text-faint" title="No letter grade in the source">
                              —
                            </span>
                          )}
                        </span>
                        {cell.source_id && (
                          <CitationChip
                            id={cell.source_id}
                            source={sources.find((s) => s.source_id === cell.source_id)}
                            onOpen={onOpen}
                            inAnswer={false}
                          />
                        )}
                      </div>
                      <p className="mt-1 text-[12.5px] leading-snug text-muted">{cell.summary}</p>
                      {cell.stat_line && (
                        <p className="mt-1 text-[11.5px] leading-snug text-faint">{cell.stat_line}</p>
                      )}
                      {cell.report_date && (
                        <p className="mt-1 text-[11px] text-faint">Report dated {cell.report_date}</p>
                      )}
                      {cell.conflict_id && (
                        <p
                          className="mt-1.5 text-[11.5px] font-medium text-conditional"
                          title={conflict?.summary}
                        >
                          <span aria-hidden>~ </span>Sources disagree
                        </p>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {grid.note && (
        <figcaption className="mt-2 text-[12px] leading-relaxed text-faint">{grid.note}</figcaption>
      )}
    </figure>
  );
}
