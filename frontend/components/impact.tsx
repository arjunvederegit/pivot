"use client";

/**
 * On-court impact, placed in the league.
 *
 * Every component here shows the same thing at a different size: where a player's
 * Pivot Estimated Impact sits among all the players the model scored this season, with
 * the estimate's 10–90% band placed in the same population and the raw index kept
 * visible beside it. The league itself is drawn behind the marker, so "96th" is never a
 * number floating free of the distribution it came from.
 *
 * Missing estimates are shown as unscored — never as zero, never at the median.
 */

import clsx from "clsx";
import Link from "next/link";
import { useMemo } from "react";
import {
  histogram,
  percentileDisplay,
  placeImpact,
  useImpactReference,
  type AvailableImpactReference,
  type ImpactPlacement,
} from "@/lib/impact";
import { tei as fmtTei } from "@/lib/format";
import { Explainer } from "@/components/ui";

/* ------------------------------------------------------------------ hooks */

export function useImpactPlacement(
  value: number | null | undefined,
  band?: { low: number | null | undefined; high: number | null | undefined } | null,
) {
  const { data: reference, isError } = useImpactReference();
  const low = band?.low;
  const high = band?.high;
  const placement = useMemo(
    () => placeImpact(value, reference, low !== undefined || high !== undefined ? { low, high } : null),
    [value, reference, low, high],
  );
  return {
    placement,
    reference: reference?.available ? reference : undefined,
    unavailableReason: reference && !reference.available ? reference.reason : undefined,
    loading: !reference && !isError,
    failed: isError,
  };
}

/* ---------------------------------------------------------------- pieces */

/** The index at served precision (2 dp) — ranks and ties are computed at that precision, so
 *  a 1-dp figure beside them could show two players "equal" with different ranks. */
function fmtIndex(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const r = Number(value.toFixed(2));
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r).toFixed(2)}`;
}

function suffixOf(n: number) {
  const lastTwo = n % 100;
  const lastOne = n % 10;
  if (lastTwo >= 11 && lastTwo <= 13) return "th";
  if (lastOne === 1) return "st";
  if (lastOne === 2) return "nd";
  if (lastOne === 3) return "rd";
  return "th";
}

/** "96th", with the suffix set small. */
export function PercentileFigure({
  percentile,
  className,
  suffixClassName,
  serif = false,
}: {
  percentile: number;
  className?: string;
  suffixClassName?: string;
  /** Set the figure in the editorial serif — for headline readouts, not table cells. */
  serif?: boolean;
}) {
  const n = percentileDisplay(percentile);
  return (
    <span className={clsx(serif ? "stat-serif" : "numeral", "leading-none", className)}>
      {n}
      {/* Half the figure's size, but never below the 10px floor (inline figures are 13px). */}
      <span
        className={clsx("ml-[1px] align-top font-medium [font-size:max(0.5em,10px)]", suffixClassName)}
      >
        {suffixOf(n)}
      </span>
    </span>
  );
}

function rankText(p: ImpactPlacement) {
  return p.tied > 1 ? `T-#${p.rank}` : `#${p.rank}`;
}

/**
 * The league as a histogram, the player as a marker, the band as a bracket.
 * `micro` fits a table row; `full` adds percentile ticks under the axis.
 */
export function ImpactStrip({
  reference,
  placement,
  variant = "micro",
  className,
}: {
  reference: AvailableImpactReference;
  placement: ImpactPlacement | null;
  variant?: "micro" | "full";
  className?: string;
}) {
  const hist = useMemo(
    () => histogram(reference.values, variant === "micro" ? 28 : 44),
    [reference.values, variant],
  );
  const span = hist.max - hist.min || 1;
  const x = (v: number) => ((Math.min(hist.max, Math.max(hist.min, v)) - hist.min) / span) * 100;
  const H = variant === "micro" ? 16 : 40;
  const barW = 100 / hist.counts.length;

  // Index values at the 10th / 50th / 90th percentile, to tie the axis back to rank.
  const ticks = useMemo(() => {
    const v = reference.values;
    const at = (q: number) => v[Math.min(v.length - 1, Math.max(0, Math.round(q * (v.length - 1))))];
    return [
      { label: "10th", value: at(0.1) },
      { label: "50th", value: at(0.5) },
      { label: "90th", value: at(0.9) },
    ];
  }, [reference.values]);

  return (
    <div className={clsx("min-w-0", className)}>
      <svg
        viewBox={`0 0 100 ${H}`}
        preserveAspectRatio="none"
        className="block w-full"
        style={{ height: H }}
        aria-hidden
      >
        {hist.counts.map((c, i) => {
          const h = hist.peak ? Math.max(c ? 1.2 : 0, (c / hist.peak) * (H - 3)) : 0;
          return (
            <rect
              key={i}
              x={i * barW + barW * 0.12}
              width={barW * 0.76}
              y={H - h}
              height={h}
              fill="var(--chalk-faint)"
              opacity={0.42}
            />
          );
        })}
        <line x1="0" x2="100" y1={H - 0.5} y2={H - 0.5} stroke="var(--hairline)" vectorEffect="non-scaling-stroke" />
        {placement?.band && (
          <rect
            x={x(placement.band.low)}
            width={Math.max(0.6, x(placement.band.high) - x(placement.band.low))}
            y={H * 0.35}
            height={H * 0.3}
            fill="var(--signal)"
            opacity={0.28}
          />
        )}
        {placement && (
          <line
            x1={x(placement.value)}
            x2={x(placement.value)}
            y1="0"
            y2={H}
            stroke="var(--signal)"
            strokeWidth={variant === "micro" ? 2 : 2.5}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      {variant === "full" && (
        <div className="relative mt-1 h-4 font-mono text-[10px] text-faint">
          {ticks.map((t) => (
            <span
              key={t.label}
              className="absolute -translate-x-1/2 whitespace-nowrap"
              style={{ left: `${x(t.value)}%` }}
            >
              {t.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- row cell */

/**
 * The compact cell for a player row: league percentile first, the league strip under it,
 * rank and the raw index in small print. Fixed width so a column of these lines up and
 * the markers can be compared down the list at a glance.
 */
export function ImpactCell({
  tei,
  band,
  className,
  name,
}: {
  tei: number | null | undefined;
  band?: { low: number | null | undefined; high: number | null | undefined } | null;
  className?: string;
  /** For the accessible description. */
  name?: string;
}) {
  const { placement, reference, loading, failed, unavailableReason } = useImpactPlacement(tei, band);
  const who = name ?? "This player";
  // Not loading and still no reference: say so, instead of shimmering forever.
  const noReference = !reference && !loading;
  const noReferenceReason = failed
    ? "The league reference did not load, so this player is not placed."
    : (unavailableReason ?? "No league reference is available, so this player is not placed.");

  if (tei === null || tei === undefined) {
    return (
      <div
        className={clsx("relative w-[7.25rem] shrink-0", className)}
        title="No impact estimate — the model has not scored this player (usually fewer than 200 minutes across its three-season window). Pivot does not rank a player it has not measured."
      >
        <div className="flex items-baseline justify-between gap-1">
          <span className="font-mono text-[12px] text-unavail">unscored</span>
        </div>
        <div className="mt-1 h-4 rounded-[1px] border border-dashed border-unavail/50" aria-hidden />
        <p className="mt-1 font-mono text-[10px] text-faint">no estimate</p>
        <span className="sr-only">{who} has no impact estimate and is not ranked.</span>
      </div>
    );
  }

  return (
    <div
      className={clsx("relative w-[7.25rem] shrink-0", className)}
      title={
        placement
          ? `League impact percentile: higher than about ${percentileDisplay(placement.percentile)}% of the ${placement.size} players Pivot scored. Model index ${fmtIndex(placement.value)} (TEI).`
          : `Model index ${fmtTei(tei)} (TEI)`
      }
    >
      <div className="flex items-baseline justify-between gap-1">
        {placement ? (
          <span className="flex items-baseline gap-1">
            <PercentileFigure percentile={placement.percentile} className="text-[1.3rem] text-foreground" />
            <span className="font-mono text-[10px] text-faint">pctl</span>
          </span>
        ) : (
          <span className="numeral text-[1.3rem] leading-none text-faint">{loading ? "··" : "—"}</span>
        )}
        <span className="font-mono text-[10.5px] text-muted">
          {placement ? rankText(placement) : ""}
        </span>
      </div>
      {reference ? (
        <ImpactStrip reference={reference} placement={placement} className="mt-1" />
      ) : noReference ? (
        <div
          className="mt-1 h-4 rounded-[1px] border border-dashed border-line"
          title={noReferenceReason}
          aria-hidden
        />
      ) : (
        <div className="skeleton mt-1 h-4" aria-hidden />
      )}
      {noReference && <span className="sr-only">{noReferenceReason}</span>}
      <p className="mt-1 font-mono text-[10px] text-faint">
        TEI idx <span className="text-muted">{fmtIndex(tei)}</span>
      </p>
      {placement && (
        <span className="sr-only">
          {who}: {percentileDisplay(placement.percentile)}
          {suffixOf(percentileDisplay(placement.percentile))} league impact percentile, ranked{" "}
          {placement.tied > 1 ? "tied " : ""}
          {placement.rank} of {placement.size} scored players.
        </span>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- readout */

/**
 * The full on-court impact block for a player drawer or page: percentile, rank, the
 * league strip with the band, the raw model index, and how all of it was produced.
 */
export function ImpactReadout({
  tei,
  band,
  offense,
  defense,
  className,
}: {
  tei: number | null | undefined;
  band?: { low: number | null | undefined; high: number | null | undefined } | null;
  offense?: number | null;
  defense?: number | null;
  className?: string;
}) {
  const { placement, reference, unavailableReason, loading, failed } = useImpactPlacement(tei, band);

  if (tei === null || tei === undefined) {
    return (
      <div className={clsx("rounded-md border border-dashed border-unavail/55 px-3.5 py-3", className)}>
        <div className="eyebrow">On-court impact</div>
        <p className="mt-2 text-sm text-muted">
          <span className="font-semibold text-foreground">Unscored.</span>{" "}The impact model has not
          scored this player — usually because he has fewer than 200 minutes across its
          three-season window, or is neither on a current roster nor listed as active. Pivot does
          not rank a player it has not measured, and never places him at zero or at the median.
        </p>
      </div>
    );
  }

  return (
    <div className={clsx("min-w-0", className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="eyebrow">On-court impact · league position</span>
        {reference && (
          <span className="font-mono text-[10.5px] text-faint">
            {reference.season} · {reference.population.size} scored
          </span>
        )}
      </div>

      {placement && reference ? (
        <>
          <div className="mt-2.5 flex flex-wrap items-end gap-x-4 gap-y-1">
            <span className="flex items-baseline gap-2">
              <PercentileFigure
                percentile={placement.percentile}
                serif
                className="text-[3.4rem] text-foreground"
              />
              <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted">
                percentile
              </span>
            </span>
            <span className="mb-1 rounded-sm border border-line px-1.5 py-0.5 font-mono text-[11px] font-semibold text-foreground">
              {placement.position}
            </span>
          </div>
          <p className="mt-1.5 text-[13px] text-muted">
            Ranked{" "}
            <span className="numeral text-foreground">
              {placement.tied > 1 ? "tied " : ""}#{placement.rank}
            </span>{" "}
            of <span className="numeral text-foreground">{placement.size}</span> scored players
            {placement.tied > 1 && (
              <span className="text-faint"> · {placement.tied} share this index</span>
            )}
          </p>
          <ImpactStrip
            reference={reference}
            placement={placement}
            variant="full"
            className="mt-3"
          />
          <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-hairline pt-3 sm:grid-cols-3">
            <div>
              <dt className="eyebrow text-[0.625rem]">Likely range</dt>
              <dd className="mt-1 font-mono text-[13px] text-foreground">
                {placement.band ? (
                  <>
                    {percentileDisplay(placement.band.percentileLow)}–
                    {percentileDisplay(placement.band.percentileHigh)}
                    <span className="text-faint"> pctl</span>
                  </>
                ) : (
                  <span className="text-unavail">band not served</span>
                )}
              </dd>
            </div>
            <div>
              <dt className="eyebrow text-[0.625rem]">Model index</dt>
              <dd className="mt-1 font-mono text-[13px] text-foreground">
                {fmtIndex(placement.value)} <span className="text-faint">TEI</span>
              </dd>
            </div>
            {(offense !== undefined || defense !== undefined) && (
              <div>
                <dt className="eyebrow text-[0.625rem]">Off · Def index</dt>
                <dd className="mt-1 font-mono text-[13px] text-foreground">
                  {fmtTei(offense)} <span className="text-faint">·</span> {fmtTei(defense)}
                </dd>
              </div>
            )}
          </dl>
          <Explainer summary="How this is placed" className="mt-3">
            <p>
              The percentile is this player&apos;s position among{" "}
              {reference.population.definition.charAt(0).toLowerCase() +
                reference.population.definition.slice(1)}{" "}
              It is a {reference.percentile_method}. Rank is {reference.rank_method}.
            </p>
            <p className="mt-1.5">
              The likely range places the estimate&apos;s 10–90% band in the same league: a wide
              range means the box score cannot pin the position down. {reference.scale}
            </p>
            <p className="mt-1.5">
              A percentile is not a calibrated 0–100 rating. {reference.population.rostered_unscored}{" "}
              rostered players are below the minutes threshold and unscored.
            </p>
            <p className="mt-1.5 font-mono text-[10.5px] text-faint">
              model {reference.model.version} · window {reference.model.training_period ?? "—"} ·{" "}
              <Link href="/methodology#tei" className="underline underline-offset-2">
                methodology
              </Link>
            </p>
          </Explainer>
        </>
      ) : (
        <div className="mt-2.5">
          <p className="font-mono text-[13px] text-foreground">
            Model index {fmtTei(tei)} <span className="text-faint">TEI</span>
          </p>
          <p className="mt-1 text-[12px] text-muted">
            {loading
              ? "Placing this player in the league…"
              : failed
                ? "The league reference did not load, so no percentile is shown."
                : (unavailableReason ?? "No league reference is available.")}
          </p>
        </div>
      )}
    </div>
  );
}

/** Small inline percentile for lists: "96th · #21". Null renders "unscored". */
export function ImpactInline({
  tei,
  className,
  showIndex = false,
}: {
  tei: number | null | undefined;
  className?: string;
  /** Print the raw model index beside the percentile, not only in the tooltip. */
  showIndex?: boolean;
}) {
  const { placement } = useImpactPlacement(tei);
  if (tei === null || tei === undefined) {
    return <span className={clsx("font-mono text-[11px] text-unavail", className)}>unscored</span>;
  }
  if (!placement) {
    return <span className={clsx("font-mono text-[11px] text-muted", className)}>{fmtTei(tei)}</span>;
  }
  return (
    <span
      className={clsx("inline-flex items-baseline gap-1 whitespace-nowrap", className)}
      title={`Model index ${fmtIndex(tei)} (TEI) · ranked ${placement.tied > 1 ? "tied " : ""}${placement.rank} of ${placement.size}`}
    >
      <PercentileFigure percentile={placement.percentile} className="text-[13px] text-foreground" />
      <span className="font-mono text-[10px] text-faint">pctl</span>
      {showIndex && (
        <span className="font-mono text-[10px] text-faint">· idx {fmtIndex(tei)}</span>
      )}
    </span>
  );
}
