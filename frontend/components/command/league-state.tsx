"use client";

/**
 * League state — what the models were fed and what they scored.
 *
 * Everything in this card is read off two payloads and nothing else: the data-health
 * report (`GET /data-health`) and the league impact reference
 * (`GET /players/impact-distribution`). A reading that is missing is shown as missing:
 * contracts that were never imported read "—", not "0", and the freshness verdict is the
 * backend's own (`source_cards.current_nba_data.status`), never recomputed here against a
 * second threshold. When that verdict is "stale", the consequence leads the card, ahead
 * of the figures it qualifies.
 */

import Link from "next/link";
import { useState } from "react";
import type { ImpactReference } from "@/lib/impact";
import { formatDate, count, tei as fmtTei } from "@/lib/format";
import type { DataHealth, SourceCard } from "@/lib/types";
import { CommandLine } from "@/components/command/command-line";
import { IconArrowRight } from "@/components/icons";
import { ImpactStrip } from "@/components/impact";
import { Badge, ErrorState, Explainer, Skeleton, SourceRail } from "@/components/ui";

const CARD_BADGE: Record<SourceCard["status"], string> = {
  fresh: "fresh",
  stale: "stale",
  derived: "derived",
  incomplete: "incomplete",
  unavailable: "unavailable",
  failed: "failed",
};

function age(iso: string | null | undefined, now: number): { value: string; unit: string } | null {
  if (!iso) return null;
  const ms = now - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const hours = ms / 3_600_000;
  if (hours < 1) return { value: "<1", unit: "hour" };
  if (hours < 48) return { value: String(Math.floor(hours)), unit: "hours" };
  return { value: String(Math.floor(hours / 24)), unit: "days" };
}

const humanCheck = (code: string) => code.replaceAll("_", " ");

/* ---------------------------------------------------------------- a figure */

function Figure({
  label,
  value,
  unit,
  note,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  unit?: React.ReactNode;
  note?: React.ReactNode;
  tone?: string;
}) {
  return (
    <div className="min-w-0 border-t border-hairline pt-3">
      <dt className="eyebrow truncate">{label}</dt>
      <dd className="mt-2">
        <span className="flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
          <span
            className="stat-serif text-[2.15rem] leading-none text-foreground"
            style={tone ? { color: tone } : undefined}
          >
            {value}
          </span>
          {unit && <span className="text-[12px] text-muted">{unit}</span>}
        </span>
        {note && <span className="mt-1.5 block text-[12px] leading-snug text-muted">{note}</span>}
      </dd>
    </div>
  );
}

function FigureSkeleton() {
  return (
    <div className="min-w-0 border-t border-hairline pt-3" aria-hidden>
      <dt>
        <Skeleton className="h-2.5 w-20" />
      </dt>
      <dd>
        <Skeleton className="mt-3 h-7 w-16" />
        <Skeleton className="mt-2 h-2.5 w-28 max-w-full" />
      </dd>
    </div>
  );
}

/* ------------------------------------------------------------- the impact */

function ImpactSheet({
  reference,
  failed,
}: {
  reference: ImpactReference | undefined;
  failed: boolean;
}) {
  const frame = "sheet flex min-w-0 flex-col p-4 sm:p-5";
  if (failed || (reference && !reference.available)) {
    return (
      <div className={frame}>
        <span className="eyebrow">Impact model · scored</span>
        <span className="stat-serif mt-2 text-[2.6rem] leading-none text-unavail">—</span>
        <p className="mt-2 text-[12.5px] leading-snug text-muted">
          {failed || !reference || reference.available ? (
            "The league impact reference did not load, so no population is shown."
          ) : (
            <CommandLine text={reference.reason} />
          )}
        </p>
      </div>
    );
  }
  if (!reference) {
    return (
      <div className={frame} role="status" aria-label="Loading the impact model">
        <Skeleton className="h-2.5 w-28" />
        <Skeleton className="mt-3 h-9 w-24" />
        <Skeleton className="mt-2.5 h-3 w-full" />
        <Skeleton className="mt-6 h-10 w-full" />
      </div>
    );
  }
  const { population, model, values, season } = reference;
  return (
    <div className={frame}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="eyebrow">Impact model · scored</span>
        <span className="text-[11.5px] text-faint">{season}</span>
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="stat-serif text-[2.6rem] leading-none text-foreground">
          {population.size.toLocaleString()}
        </span>
        <span className="text-[12.5px] text-muted">players</span>
      </div>
      <p className="mt-1.5 text-[12px] leading-snug text-muted">
        <span className="text-foreground">{population.rostered_unscored}</span> rostered players
        unscored — under {population.min_window_minutes} minutes, so not ranked (never placed at
        zero)
      </p>
      <div className="mt-auto pt-5">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[11px] text-faint">
          <span>Every scored player · league TEI distribution</span>
          <span>ticks = percentile</span>
        </div>
        <ImpactStrip reference={reference} placement={null} variant="full" />
        <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-faint">
          <span>
            low <span className="tabular-nums text-muted">{fmtTei(values[0])}</span>
          </span>
          <span className="truncate">unitless index, not points</span>
          <span>
            high <span className="tabular-nums text-muted">{fmtTei(values[values.length - 1])}</span>
          </span>
        </div>
        <p
          className="mt-3 flex flex-wrap gap-x-1.5 border-t border-hairline pt-2.5 text-[11px] text-faint"
          title={model.algorithm}
        >
          <span>
            Model <span className="break-all text-muted">{model.version}</span>
          </span>
          <span aria-hidden>·</span>
          <span>
            window <span className="text-muted">{model.training_period ?? "—"}</span>
          </span>
        </p>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- the card */

export function LeagueState({
  health,
  healthError,
  reference,
  referenceFailed,
}: {
  health: DataHealth | undefined;
  healthError: unknown;
  reference: ImpactReference | undefined;
  referenceFailed: boolean;
}) {
  // Read once per mount: an age is "as of when you opened the page", and reading the clock
  // in render would make every re-render a different page.
  const [now] = useState(() => Date.now());

  const card = health?.source_cards?.find((c) => c.key === "current_nba_data");
  const freshness = card?.status;
  const synced = health?.last_successful_sync ?? null;
  const syncAge = age(synced, now);
  const contractsConfigured = health?.providers?.contracts?.configured === true;
  const coverage = health?.asset_coverage ?? {};
  const photoShare = coverage.player_photo_coverage;
  const issues = health?.open_quality_issue_total;
  const topIssue = Object.entries(health?.open_quality_issue_counts ?? {}).sort((a, b) => b[1] - a[1])[0];

  return (
    <section aria-labelledby="league-state-heading" className="panel">
      {/* ------------------------------------------------------------ status line */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 pt-4">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 id="league-state-heading" className="eyebrow flex items-center gap-2 self-center text-foreground">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />
            League state
          </h3>
          <span className="text-[12.5px] text-muted">
            {health ? (
              <>
                <span className="text-foreground">{health.current_season}</span> season · cap year{" "}
                {health.cap_league_year}
              </>
            ) : healthError ? (
              "report unavailable"
            ) : (
              "Reading the data-health report…"
            )}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {freshness && (
            <Badge status={freshness === "fresh" ? "fresh" : freshness === "stale" ? "stale" : "unavailable"}>
              {freshness === "fresh" ? "data fresh" : freshness === "stale" ? "data aging" : "no live data"}
            </Badge>
          )}
          <Link
            href="/data-health"
            className="group inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] font-medium text-signal transition-colors hover:text-foreground"
          >
            Data Health
            <IconArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      </div>

      {/* ------------------------------------------- what stale data means, first */}
      {health && freshness === "stale" && (
        <div className="mx-5 mt-4 flex items-start gap-3 rounded-lg border border-conditional/30 bg-conditional/[0.06] px-4 py-3">
          <span
            aria-hidden
            className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-conditional/50 text-[11px] font-semibold text-conditional"
          >
            !
          </span>
          <p className="text-[12.5px] leading-relaxed text-muted">
            <span className="font-semibold text-foreground">NBA.com data is aging.</span> Rosters,
            stats and projections on every page reflect {synced ? formatDate(synced) : "the last sync"}
            {syncAge ? ` (${syncAge.value} ${syncAge.unit} ago)` : ""}, not today.
            {/* The backend's next step, e.g. "Run `make sync-data` to refresh from
                NBA.com." — its command drawn as a chip, never with literal backticks. */}
            {card?.action && (
              <>
                {" "}
                <CommandLine text={card.action} />
              </>
            )}
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------ the readings */}
      <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)] lg:gap-6">
        {healthError && !health ? (
          // The impact reference is a separate request: a failed data-health report says
          // nothing about it, so it keeps its own card.
          <div className="min-w-0 self-start">
            <ErrorState message={`Could not load the data-health report: ${String(healthError)}`} />
          </div>
        ) : (
          <dl className="grid min-w-0 grid-cols-2 content-start gap-x-6 gap-y-5 sm:grid-cols-3" aria-busy={!health}>
            {!health ? (
              Array.from({ length: 6 }).map((_, i) => <FigureSkeleton key={i} />)
            ) : (
              <>
                <Figure
                  label="NBA.com sync"
                  value={syncAge?.value ?? "—"}
                  unit={syncAge ? `${syncAge.unit} old` : undefined}
                  tone={freshness === "stale" ? "var(--conditional)" : undefined}
                  note={synced ? <>retrieved {formatDate(synced)}</> : "never retrieved"}
                />
                <Figure
                  label="Roster spots"
                  value={health.tables?.rosters?.rows?.toLocaleString() ?? "—"}
                  note={
                    typeof health.tables?.teams?.rows === "number"
                      ? `${count(health.tables.teams.rows, "team")}${
                          typeof health.tables?.players?.rows === "number"
                            ? ` · ${count(health.tables.players.rows, "player")} on file`
                            : ""
                        }`
                      : undefined
                  }
                />
                <Figure
                  label="Contracts"
                  value={contractsConfigured ? (health.tables?.contracts?.rows?.toLocaleString() ?? "—") : "—"}
                  tone={contractsConfigured ? undefined : "var(--unknown)"}
                  unit={contractsConfigured ? "on file" : <Badge status="unavailable">not imported</Badge>}
                  note={
                    contractsConfigured
                      ? "Salary rules and payroll run on these"
                      : "No provider configured — salary rules and payroll stay unavailable, never estimated"
                  }
                />
                <Figure
                  label="Player photos"
                  value={typeof photoShare === "number" ? `${Math.round(photoShare * 100)}%` : "—"}
                  note={
                    typeof coverage.rostered_players === "number" &&
                    typeof coverage.rostered_players_with_photo === "number"
                      ? `${coverage.rostered_players_with_photo} of ${coverage.rostered_players} rostered matched`
                      : "coverage not reported"
                  }
                />
                <Figure
                  label="Active models"
                  value={health.active_models?.length ?? "—"}
                  note="Versions and validation in Data Health"
                />
                <Figure
                  label="Open data issues"
                  value={typeof issues === "number" ? issues.toLocaleString() : "—"}
                  tone={typeof issues === "number" && issues > 0 ? "var(--conditional)" : undefined}
                  note={
                    typeof issues !== "number"
                      ? "not reported"
                      : issues === 0
                        ? "none open"
                        : topIssue
                          ? `largest: ${humanCheck(topIssue[0])} (${topIssue[1]})`
                          : undefined
                  }
                />
              </>
            )}
          </dl>
        )}
        <ImpactSheet reference={reference} failed={referenceFailed} />
      </div>

      {/* ------------------------------------------------------- the honest footer */}
      {health && (
        <div className="border-t border-hairline px-5 pb-4 pt-3">
          <Explainer summary={`All ${health.source_cards?.length ?? 0} sources behind these readings`}>
            <ul className="space-y-2">
              {(health.source_cards ?? []).map((source) => (
                <li key={source.key} className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                  <Badge status={CARD_BADGE[source.status] ?? "info"}>{source.status}</Badge>
                  <span className="font-semibold text-foreground">{source.title}</span>
                  <span className="text-[11.5px] text-muted">{source.coverage}</span>
                </li>
              ))}
            </ul>
            {reference?.available && (
              <p className="mt-2.5">
                Impact is the {reference.metric}: {reference.scale}
              </p>
            )}
          </Explainer>
          <SourceRail
            className="mt-3"
            source={card?.source ?? "data-health report"}
            retrievedAt={synced}
            extra={
              <>
                <span aria-hidden>·</span>
                <span>report {formatDate(health.generated_at)}</span>
              </>
            }
          />
        </div>
      )}
    </section>
  );
}
