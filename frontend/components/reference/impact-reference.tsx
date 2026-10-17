"use client";

/**
 * The league impact reference, live — for the Methodology page.
 *
 * Everything drawn here comes from `GET /players/impact-distribution` (via
 * `useImpactReference`) and the same arithmetic every percentile in the product uses
 * (`lib/impact.ts`). The worked example is a real index from the served population, run
 * through `percentileOf` / `rankOf`, so the explanation and the product cannot disagree.
 */

import { useMemo } from "react";
import {
  percentileDisplay,
  percentileOf,
  placeImpact,
  rankOf,
  useImpactReference,
  type AvailableImpactReference,
} from "@/lib/impact";
import { ordinal, tei } from "@/lib/format";
import { signed } from "@/components/reference/format";
import { ImpactStrip } from "@/components/impact";
import { Panel, Skeleton, SourceRail, StatBlock, UnavailableNotice } from "@/components/ui";

export function ImpactReferenceLive() {
  const { data, isError, isPending } = useImpactReference();

  if (isPending) {
    return (
      <div className="space-y-2" role="status" aria-label="Loading the league impact reference">
        <Skeleton className="h-24" />
        <Skeleton className="h-28" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <UnavailableNotice reason="The league impact reference did not load, so no live numbers are shown here. The rules above still describe exactly how every percentile in the product is computed." />
    );
  }
  if (!data.available) {
    return <UnavailableNotice reason={data.reason} />;
  }
  return <ReferenceCard reference={data} />;
}

function ReferenceCard({ reference }: { reference: AvailableImpactReference }) {
  const values = reference.values;
  const n = values.length;

  // A real index from the population: the one sitting at the 90 % position of the sorted
  // list. Nothing is invented — it is a served value, placed by the product's own code.
  const example = useMemo(() => {
    const value = values[Math.min(n - 1, Math.max(0, Math.round(0.9 * (n - 1))))];
    const { rank, tied } = rankOf(value, values);
    const above = rank - 1;
    const below = n - above - tied;
    const percentile = percentileOf(value, values);
    return { value, rank, tied, above, below, percentile };
  }, [values, n]);

  const placement = useMemo(() => placeImpact(example.value, reference), [example.value, reference]);
  const pop = reference.population;

  return (
    <Panel
      padded={false}
      title="The population, live"
      subtitle={`${reference.metric} · ${reference.season} · served by the API, not restated by hand`}
    >
      <div className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline sm:grid-cols-4">
        <div className="bg-panel px-4 py-3">
          <StatBlock size="md" label="Scored players" value={n.toLocaleString()} note={`the population · ${reference.season}`} />
        </div>
        <div className="bg-panel px-4 py-3">
          <StatBlock size="md" label="Minimum minutes" value={pop.min_window_minutes.toLocaleString()} note="across the model window" />
        </div>
        <div className="bg-panel px-4 py-3">
          <StatBlock
            size="md"
            label="Rostered, unscored"
            value={pop.rostered_unscored.toLocaleString()}
            note="never ranked — shown as unscored"
            accent="var(--unknown)"
          />
        </div>
        <div className="bg-panel px-4 py-3">
          <StatBlock
            size="md"
            label="Distinct indices"
            value={pop.distinct_values.toLocaleString()}
            note={`of ${n.toLocaleString()} · ties are common`}
          />
        </div>
      </div>

      <div className="px-4 pb-4 pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <span className="eyebrow">Every scored player&apos;s index</span>
          <span className="numeral text-[11.5px] font-normal tracking-normal text-faint">
            {tei(values[0])} to {tei(values[n - 1])} · the marker is the worked example
          </span>
        </div>
        <ImpactStrip reference={reference} placement={placement} variant="full" className="mt-3" />

        <div className="mt-4 grid gap-4 border-t border-hairline pt-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <div className="eyebrow">Worked example</div>
            <p className="mt-2 text-[13.5px] leading-relaxed text-muted">
              Take a scored player whose index is{" "}
              <span className="numeral text-foreground">{signed(example.value, 2)}</span>. Of the{" "}
              {n.toLocaleString()} scored players,{" "}
              <span className="numeral text-foreground">{example.below.toLocaleString()}</span> sit
              below it and{" "}
              <span className="numeral text-foreground">{example.above.toLocaleString()}</span> sit
              above it;{" "}
              {example.tied === 1 ? (
                "no one else shares the exact value"
              ) : (
                <>
                  <span className="numeral text-foreground">{example.tied}</span> players, him
                  included, share it exactly and each counts as half
                </>
              )}
              .
            </p>
          </div>
          {/* No font utility: <pre> takes the code face from the base layer, so the
              continuation lines align under the first on its fixed advance. */}
          <pre className="scroll-thin min-w-0 self-start overflow-x-auto rounded-lg border border-hairline bg-court px-4 py-3 text-[12px] leading-[1.7] text-foreground">
            {`percentile = 100 × (below + ½·tied) / N
           = 100 × (${example.below} + ½·${example.tied}) / ${n}
           = ${example.percentile.toFixed(1)}  →  ${ordinal(percentileDisplay(example.percentile))}
rank       = 1 + above = 1 + ${example.above}
           = ${example.tied > 1 ? "T-" : ""}#${example.rank}`}
          </pre>
        </div>

        {reference.limitations.length > 0 && (
          <ul className="mt-4 space-y-1.5 border-t border-hairline pt-3">
            {reference.limitations.map((line) => (
              <li key={line} className="flex gap-2.5 text-[12.5px] leading-snug text-muted">
                <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-unavail" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        )}

        <SourceRail
          source={`GET /players/impact-distribution · model ${reference.model.version} · window ${
            reference.model.training_period ?? "—"
          }`}
          retrievedAt={reference.model.trained_at}
        />
      </div>
    </Panel>
  );
}
