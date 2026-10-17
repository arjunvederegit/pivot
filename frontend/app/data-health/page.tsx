"use client";

/**
 * Data Health — what is powering Pivot right now, and what to plug in next.
 *
 * A status board, read top to bottom: one sentence that says how the estate stands, the
 * counts behind it, then every source — those that need a person first, each with the
 * backend's own coverage sentence and next step, then the healthy ones.
 *
 * The honesty rule is structural. Every card status is either healthy (fresh, derived)
 * or needs attention (stale, incomplete, failed, not configured — and anything the
 * backend adds later), so no status can fall between the two counts and leave the
 * headline reading "All sources healthy" over a source that is not. Critical sources
 * (contracts) are counted separately, because without them whole rules go dark.
 *
 * Refresh re-reads the endpoint. It never starts a sync.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { dataHealthSchema } from "@/lib/schemas";
import { count, formatDate } from "@/lib/format";
import { useHydrated } from "@/lib/hydrated";
import type { DataHealth, SourceCard } from "@/lib/types";
import { CommandText, daysBetween } from "@/components/reference/format";
import { ScrollCue } from "@/components/reference/scroll-cue";
import { RefSectionHead } from "@/components/reference/section-head";
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  MeterBar,
  PageHeader,
  Panel,
  Skeleton,
  SkeletonRows,
  SourceRail,
  Tabs,
  Td,
  Th,
} from "@/components/ui";

const CARD_BADGE_STATUS: Record<SourceCard["status"], string> = {
  fresh: "pass",
  derived: "derived",
  stale: "stale",
  incomplete: "incomplete",
  unavailable: "unavailable",
  failed: "fail",
};

const CARD_BADGE_LABEL: Record<SourceCard["status"], string> = {
  fresh: "fresh",
  derived: "derived",
  stale: "stale",
  incomplete: "incomplete",
  unavailable: "not configured",
  failed: "failed",
};

const CARD_TONE: Record<SourceCard["status"], string> = {
  fresh: "var(--legal)",
  derived: "var(--signal)",
  stale: "var(--conditional)",
  incomplete: "var(--conditional)",
  unavailable: "var(--unknown)",
  failed: "var(--illegal)",
};

/** Lower sorts first: what needs a person comes before what is fine. */
const SEVERITY: Record<SourceCard["status"], number> = {
  failed: 0,
  unavailable: 1,
  incomplete: 2,
  stale: 3,
  derived: 4,
  fresh: 5,
};

/** The only statuses that count as healthy. Everything else needs attention. */
const HEALTHY = new Set<string>(["fresh", "derived"]);

/** Sources the product refuses to work around: without them, whole rules go dark. */
const CRITICAL_KEYS = new Set(["contracts"]);

/** Pull a leading percentage out of the backend's own coverage sentence, if present. */
function coveragePercent(coverage: string): number | null {
  const match = coverage.match(/(\d+(?:\.\d+)?)\s*%/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
}

/** "30/30 teams" style ratios also deserve a meter. */
function coverageRatio(coverage: string): number | null {
  const match = coverage.match(/\b(\d+)\s*\/\s*(\d+)\b/);
  if (!match) return null;
  const [, a, b] = match;
  const denominator = Number(b);
  if (!denominator) return null;
  return Math.max(0, Math.min(100, (Number(a) / denominator) * 100));
}

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];

/** "One", "Two" … for a sentence; figures past ten. */
function spell(n: number) {
  return WORDS[n] ?? n.toLocaleString();
}

function isCritical(card: SourceCard) {
  return CRITICAL_KEYS.has(card.key) && (card.status === "unavailable" || card.status === "failed");
}

export default function DataHealthPage() {
  const query = useQuery({
    queryKey: ["data-health"],
    queryFn: () => api.get<DataHealth>("/data-health", dataHealthSchema),
  });
  // The shell warms this same ["data-health"] cache before the page hydrates, so reading it
  // during the hydration render would draw the board where the server drew the skeleton.
  // Until hydration completes the page renders exactly what the server did.
  const hydrated = useHydrated();
  const data = hydrated ? query.data : undefined;
  const error = hydrated ? query.error : null;
  const busy = hydrated && query.isFetching;

  const refresh = (
    <Button variant="secondary" onClick={() => query.refetch()} disabled={busy}>
      {busy ? "Refreshing…" : "Refresh"}
    </Button>
  );
  const header = (meta?: React.ReactNode) => (
    <PageHeader
      eyebrow="Provenance"
      title="Data Health"
      lede="Every screen in Pivot traces to one of these sources. When a source is missing, the product says so instead of estimating around it."
      actions={refresh}
      meta={meta}
    />
  );

  if (error) {
    return (
      <div>
        {header()}
        <ErrorState message={`Could not load data health: ${String(error)}`} />
      </div>
    );
  }

  if (!data) {
    return (
      <div role="status" aria-label="Loading data health">
        {header()}
        <div className="panel overflow-hidden">
          <div className="grid gap-6 border-b border-hairline p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:p-6">
            <div className="space-y-3">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-9 w-72 max-w-full" />
              <Skeleton className="h-3.5 w-full max-w-md" />
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16" />
              ))}
            </div>
          </div>
          <div className="p-5">
            <SkeletonRows rows={6} height="h-16" />
          </div>
        </div>
      </div>
    );
  }

  const cards = data.source_cards ?? [];
  const criticalMissing = cards.filter(isCritical);
  const healthy = cards.filter((card) => HEALTHY.has(card.status));
  // Not "stale, failed or unavailable" — a list like that is how `incomplete` once fell
  // through both counts. Attention is everything that is not healthy.
  const attention = cards.filter((card) => !HEALTHY.has(card.status));
  const ordered = [...cards].sort(
    (a, b) =>
      Number(isCritical(b)) - Number(isCritical(a)) ||
      (SEVERITY[a.status] ?? -1) - (SEVERITY[b.status] ?? -1),
  );
  const orderedAttention = ordered.filter((card) => !HEALTHY.has(card.status));
  const orderedHealthy = ordered.filter((card) => HEALTHY.has(card.status));

  const headline =
    cards.length === 0
      ? { status: "unavailable", text: "No source summary" }
      : criticalMissing.length > 0
        ? {
            status: "warning",
            text: `${criticalMissing.length} critical source${
              criticalMissing.length === 1 ? "" : "s"
            } missing`,
          }
        : attention.length > 0
          ? {
              status: "warning",
              text: `${attention.length} source${attention.length === 1 ? "" : "s"} need${
                attention.length === 1 ? "s" : ""
              } attention`,
            }
          : { status: "pass", text: "All sources healthy" };

  // The board's headline sentence spells small counts out: in the display serif a lone
  // "1" reads as a letter ("l critical source missing"). The badge above keeps figures.
  const statement =
    headline.text === "All sources healthy" || cards.length === 0
      ? headline.text
      : criticalMissing.length > 0
        ? `${spell(criticalMissing.length)} critical source${criticalMissing.length === 1 ? " is" : "s are"} missing`
        : `${spell(attention.length)} source${attention.length === 1 ? " needs" : "s need"} attention`;

  const summary =
    cards.length === 0
      ? "The backend returned no source cards. The technical details below still report tables, providers and sync runs."
      : attention.length > 0
        ? `${attention.length} of ${count(cards.length, "source")} need a person — they are listed first, each with its next step.`
        : "Every source is fresh, or derived from one that is.";

  const tone =
    criticalMissing.length > 0 || attention.some((c) => c.status === "failed")
      ? "var(--illegal)"
      : attention.length > 0
        ? "var(--conditional)"
        : "var(--legal)";

  return (
    <div>
      {header(
        <>
          <Badge status={headline.status}>{headline.text}</Badge>
          {/* Badges, not adjacent eyebrows: two caps runs side by side read as one phrase
              ("2025-26 SEASON CAP YEAR 2026-27"). */}
          <Badge status="info">{data.current_season} season</Badge>
          <Badge status="info">cap year {data.cap_league_year}</Badge>
          <span className="text-[12px] text-faint">generated {formatDate(data.generated_at)}</span>
        </>,
      )}

      <div className="space-y-14">
        {/* ------------------------------------------------------- status board */}
        <section aria-labelledby="board-heading" className="panel overflow-hidden" style={{ "--edge": tone } as React.CSSProperties}>
          <div className="border-b border-hairline px-5 pb-5 pt-6 lg:px-6">
            <div className="eyebrow flex items-center gap-2">
              <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: tone }} />
              Source status
            </div>
            <h2 id="board-heading" className="title-lg mt-2.5 text-foreground">
              {statement}
            </h2>
            <p className="mt-2.5 max-w-2xl text-[14px] leading-relaxed text-muted">
              {summary} Last successful sync {formatDate(data.last_successful_sync)}.
            </p>
          </div>

          <dl className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline sm:grid-cols-4">
            <Counter label="Healthy" value={healthy.length} note="fresh, or derived from a live source" tone="var(--legal)" />
            <Counter
              label="Need attention"
              value={attention.length}
              note="stale, incomplete, failed or not configured"
              tone={attention.length > 0 ? "var(--conditional)" : undefined}
            />
            <Counter
              label="Critical missing"
              value={criticalMissing.length}
              note={criticalMissing.length > 0 ? "salary rules report unavailable until fixed" : "no blocking gaps"}
              tone={criticalMissing.length > 0 ? "var(--illegal)" : "var(--legal)"}
            />
            <Counter label="Sources" value={cards.length} note="each listed below with coverage" />
          </dl>

          {criticalMissing.length > 0 && (
            <div className="flex gap-3 border-b border-hairline bg-illegal/[0.07] px-5 py-4 lg:px-6">
              <span aria-hidden className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-illegal" />
              <p className="min-w-0 text-[14px] leading-relaxed text-muted">
                <span className="font-semibold text-foreground">
                  {/* Critical covers `failed` as well as `unavailable`; a failed import is
                      configured, so it must not be reported as "not configured". */}
                  {criticalMissing
                    .map((card) => `${card.title} ${card.status === "failed" ? "failed" : "not configured"}`)
                    .join("; ")}
                  .
                </span>{" "}
                Salary matching, apron limits and payroll stay <em className="text-foreground">unavailable</em>{" "}
                product-wide — no trade will ever be reported as legal from partial checks.{" "}
                <Link
                  href="/salary-cap-center"
                  className="whitespace-nowrap font-medium text-signal underline decoration-signal/40 underline-offset-4 hover:decoration-signal"
                >
                  Import steps <span aria-hidden>→</span>
                </Link>
              </p>
            </div>
          )}

          {cards.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title="No source summary available"
                hint="The backend did not return source cards. The technical details below still show table counts, sync runs and provider configuration."
              />
            </div>
          ) : (
            <>
              {orderedAttention.length > 0 && (
                <SourceGroup id="sources-attention" label="Needs attention" cards={orderedAttention} generatedAt={data.generated_at} />
              )}
              {orderedHealthy.length > 0 && (
                <SourceGroup id="sources-healthy" label="Healthy" cards={orderedHealthy} generatedAt={data.generated_at} />
              )}
            </>
          )}
        </section>

        {/* -------------------------------------------------- under the hood */}
        <TechnicalDetail data={data} />
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- board */

function Counter({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: number;
  note: string;
  tone?: string;
}) {
  return (
    <div className="min-w-0 bg-panel px-5 py-4 lg:px-6">
      <dt className="eyebrow">{label}</dt>
      {/* The UI face, not the serif: these are counts read as data, and the display serif's
          lone "1" reads as a letter. */}
      <dd className="numeral mt-2.5 text-[2rem] font-medium leading-none text-foreground" style={tone ? { color: tone } : undefined}>
        {value}
      </dd>
      <dd className="mt-2 text-[12px] leading-snug text-muted">{note}</dd>
    </div>
  );
}

function SourceGroup({
  id,
  label,
  cards,
  generatedAt,
}: {
  id: string;
  label: string;
  cards: SourceCard[];
  generatedAt: string;
}) {
  return (
    <div>
      <h3
        id={id}
        className="eyebrow flex items-center gap-2 border-b border-hairline bg-court/40 px-5 py-2.5 lg:px-6"
      >
        {label}
        <span className="numeral font-medium tracking-normal text-faint">{cards.length}</span>
      </h3>
      <ul aria-labelledby={id}>
        {cards.map((card) => (
          <SourceRow key={card.key} card={card} generatedAt={generatedAt} />
        ))}
      </ul>
    </div>
  );
}

function SourceRow({ card, generatedAt }: { card: SourceCard; generatedAt: string }) {
  const percent = coveragePercent(card.coverage) ?? coverageRatio(card.coverage);
  const missing = card.status === "unavailable" || card.status === "failed";
  const tone = CARD_TONE[card.status] ?? "var(--unknown)";
  const age = daysBetween(card.last_update, generatedAt);
  return (
    <li
      className="grid grid-cols-2 gap-x-6 gap-y-3 border-b border-hairline px-5 py-5 last:border-b-0 lg:grid-cols-[8.5rem_minmax(0,1.35fr)_minmax(0,1fr)_10rem] lg:px-6"
      aria-label={`${card.title}, ${CARD_BADGE_LABEL[card.status] ?? card.status}`}
    >
      <div className="col-span-2 flex items-start lg:col-span-1 lg:pt-0.5">
        <Badge status={CARD_BADGE_STATUS[card.status] ?? "info"}>
          {CARD_BADGE_LABEL[card.status] ?? card.status}
        </Badge>
      </div>
      <div className="col-span-2 min-w-0 lg:col-span-1">
        <h4 className="title-md text-foreground">{card.title}</h4>
        <p className="mt-1 text-[13.5px] leading-snug text-muted">{card.coverage}</p>
        {percent !== null && (
          <MeterBar
            value={percent}
            max={100}
            color={missing ? "var(--unknown)" : tone}
            className="mt-2.5 max-w-xs"
            label={`${card.title} coverage ${percent.toFixed(0)} percent`}
          />
        )}
      </div>
      <div className="min-w-0">
        <div className="eyebrow">Source</div>
        <p className="mt-1.5 break-words text-[12.5px] leading-snug text-muted">{card.source}</p>
      </div>
      <div className="min-w-0 lg:text-right">
        <div className="eyebrow">Last update</div>
        <p className="numeral mt-1.5 text-[12.5px] font-normal leading-snug tracking-normal text-muted">
          {formatDate(card.last_update)}
        </p>
        {age !== null && (
          <p
            className={clsx(
              "mt-0.5 text-[12px]",
              card.status === "stale" ? "text-conditional" : "text-faint",
            )}
          >
            {age === 0 ? "same day as this report" : `${count(age, "day")} before this report`}
          </p>
        )}
      </div>
      {card.action && (
        <div className="col-span-2 rounded-lg border border-hairline bg-panel2/70 px-3.5 py-2.5 lg:col-span-3 lg:col-start-2">
          <p className="min-w-0 text-[13px] leading-relaxed text-foreground">
            <span className="mr-1.5 font-semibold text-signal">Next step</span>
            <CommandText
              text={card.action}
              codeClassName="break-all rounded bg-signal/10 px-1 py-px text-[0.88em] text-signal"
            />
          </p>
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------- technical detail */

function TechnicalDetail({ data }: { data: DataHealth }) {
  const hasAssets = data.asset_coverage && Object.keys(data.asset_coverage).length > 0;
  const endpoints = data.providers.nba_api?.endpoints ?? [];
  const tabs = [
    { id: "tables", label: "Tables" },
    { id: "providers", label: "Providers" },
    { id: "runs", label: "Sync runs" },
    { id: "issues", label: `Quality issues · ${data.open_quality_issue_total.toLocaleString()}` },
    { id: "models", label: "Models" },
    ...(hasAssets ? [{ id: "assets", label: "Asset coverage" }] : []),
    ...(endpoints.length > 0 ? [{ id: "endpoints", label: "Endpoints" }] : []),
  ];
  const [tab, setTab] = useState("tables");

  return (
    <section aria-labelledby="under-the-hood">
      <RefSectionHead
        id="under-the-hood"
        eyebrow="Under the hood"
        title="Technical details"
        aside="Row counts, providers, sync runs, validation issues and the models the evaluation is running on — as the backend reports them."
      />
      <Panel padded={false}>
        <ScrollCue target='[role="tablist"]' label={null} className="px-4 pt-1">
          <Tabs ariaLabel="Technical details" active={tab} onChange={setTab} tabs={tabs} />
        </ScrollCue>
        <div className="p-5">
          {tab === "tables" && <TablesTab data={data} />}
          {tab === "providers" && <ProvidersTab data={data} />}
          {tab === "runs" && <RunsTab data={data} />}
          {tab === "issues" && <IssuesTab data={data} />}
          {tab === "models" && <ModelsTab data={data} />}
          {tab === "assets" && hasAssets && (
            <ul className="grid gap-x-8 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(data.asset_coverage).map(([key, value]) => (
                <li
                  key={key}
                  className="flex items-baseline justify-between gap-3 border-b border-hairline py-2"
                >
                  <span className="truncate text-[13px] text-muted">{key.replace(/_/g, " ")}</span>
                  <span className="numeral shrink-0 text-[13px] text-foreground">
                    {Number.isInteger(value) ? value : value.toFixed(3)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {tab === "endpoints" && endpoints.length > 0 && (
            <ScrollCue>
              <table className="w-full min-w-[640px]">
                <caption className="sr-only">nba_api endpoint health for this backend process</caption>
                <thead>
                  <tr className="border-b border-line">
                    <Th>Endpoint</Th>
                    <Th numeric>OK</Th>
                    <Th numeric>Failed</Th>
                    <Th numeric>Last latency</Th>
                    <Th>Last error</Th>
                  </tr>
                </thead>
                <tbody>
                  {endpoints.map((endpoint) => (
                    <tr key={endpoint.endpoint} className="border-b border-hairline">
                      <Td className="whitespace-nowrap text-[13px]">{endpoint.endpoint}</Td>
                      <Td numeric>{endpoint.successes}</Td>
                      <Td numeric>{endpoint.failures}</Td>
                      <Td numeric>
                        {endpoint.last_latency_ms ? `${endpoint.last_latency_ms.toFixed(0)}ms` : "—"}
                      </Td>
                      <Td className="text-[12px] text-illegal">{endpoint.last_error ?? "—"}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </ScrollCue>
          )}
        </div>
      </Panel>
    </section>
  );
}

function TablesTab({ data }: { data: DataHealth }) {
  return (
    <>
      {/* On a phone the freshness badge stacks under the table name, so name, rows and
          freshness read without scrolling; only the retrieval time can sit past the edge,
          and the cue says so. */}
      <ScrollCue>
        <table className="w-full sm:min-w-[520px]">
          <caption className="sr-only">Row counts and freshness for every ingested table</caption>
          <thead>
            <tr className="border-b border-line">
              <Th>Table</Th>
              <Th numeric>Rows</Th>
              <Th>Last retrieved</Th>
              <Th className="hidden sm:table-cell">Freshness</Th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.tables).map(([name, table]) => (
              <tr key={name} className="border-b border-hairline last:border-b-0">
                <Td className="py-2 text-[13px] text-foreground">
                  {/* A phone may break a long name after an underscore (never mid-word), which
                      lets the whole table fit at 390px; wider screens keep it on one line. */}
                  <span className="sm:whitespace-nowrap">
                    {name.split("_").map((part, i) => (
                      <span key={i}>
                        {i > 0 && (
                          <>
                            _<wbr />
                          </>
                        )}
                        {part}
                      </span>
                    ))}
                  </span>
                  <span className="mt-1 block sm:hidden">
                    <TableFreshness table={table} />
                  </span>
                </Td>
                <Td numeric className={clsx("align-top sm:align-middle", table.rows === 0 && "text-unavail")}>
                  {table.rows.toLocaleString()}
                </Td>
                <Td className="align-top text-[12.5px] text-muted sm:whitespace-nowrap sm:align-middle">
                  {formatDate(table.last_retrieved_at)}
                </Td>
                <Td className="hidden sm:table-cell">
                  <TableFreshness table={table} />
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollCue>
      <SourceRail
        source={`cap parameters loaded for ${data.cap_parameter_years.join(", ") || "—"}`}
        retrievedAt={data.last_successful_sync}
      />
    </>
  );
}

function TableFreshness({ table }: { table: DataHealth["tables"][string] }) {
  // Emptiness is checked first. An empty table has no maximum retrieval time, so
  // `stale === null`, and testing that first labelled every empty table "derived" —
  // `contracts` at 0 rows read as a derived source rather than an empty one.
  if (table.rows === 0) return <Badge status="unavailable">empty</Badge>;
  if (table.stale === null) return <Badge status="derived">derived</Badge>;
  if (table.stale) return <Badge status="stale">stale</Badge>;
  return <Badge status="pass">fresh</Badge>;
}

function ProvidersTab({ data }: { data: DataHealth }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {Object.entries(data.providers).map(([name, provider]) => {
        const ok = provider.configured ?? provider.enabled ?? false;
        return (
          <li key={name} className="sheet min-w-0 px-4 py-3.5">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <span className="title-md capitalize text-foreground">{name.replace(/_/g, " ")}</span>
              <Badge status={ok ? "pass" : "unavailable"}>{ok ? "configured" : "not configured"}</Badge>
            </div>
            {provider.package_version && (
              <p className="mt-2 text-[12.5px] text-muted">
                {provider.upstream} · v{provider.package_version}
              </p>
            )}
            {provider.provider && (
              <p className="mt-1 text-[12.5px] text-muted">Provider: {provider.provider}</p>
            )}
            {provider.note && (
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">{provider.note}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function RunsTab({ data }: { data: DataHealth }) {
  if (data.recent_sync_runs.length === 0) {
    return (
      <EmptyState
        title="No sync runs recorded"
        hint="Run `make sync-data` to pull a fresh snapshot from NBA.com."
      />
    );
  }
  return (
    <ul className="scroll-thin max-h-[28rem] divide-y divide-hairline overflow-y-auto pr-1">
      {data.recent_sync_runs.map((run, index) => (
        <li key={index} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
          <Badge status={run.status}>{run.status}</Badge>
          <span className="min-w-0 truncate text-[13px] text-foreground">{run.job}</span>
          <span className="text-[12px] text-muted">{count(run.rows, "row")}</span>
          <span className="ml-auto whitespace-nowrap text-[12px] text-faint">
            {formatDate(run.finished_at ?? run.started_at)}
          </span>
          {run.error && (
            <span className="w-full truncate text-[12px] text-illegal" title={run.error}>
              {run.error}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function IssuesTab({ data }: { data: DataHealth }) {
  if (data.open_quality_issues.length === 0) {
    return <p className="text-sm text-muted">No open issues from the latest validation pass.</p>;
  }
  return (
    <>
      {/* The list is capped, so without the totals the real backlog is unknowable from the
          page — 562 open rows rendered as 50 with no indication that 512 were missing. */}
      {Object.keys(data.open_quality_issue_counts ?? {}).length > 0 && (
        <ul className="mb-4 grid gap-2 border-b border-hairline pb-4 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(data.open_quality_issue_counts).map(([check, n]) => (
            <li key={check} className="flex items-baseline justify-between gap-3 rounded-lg bg-panel2 px-3 py-2">
              <span className="min-w-0 truncate text-[12.5px] text-muted">{check}</span>
              <span className="numeral shrink-0 text-[14px] text-foreground">{n.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="mb-2.5 text-[12px] text-faint">
        {data.open_quality_issue_total.toLocaleString()} open · showing the{" "}
        {data.open_quality_issues.length} most recent
      </p>
      <ul className="scroll-thin max-h-[26rem] space-y-2.5 overflow-y-auto pr-1">
        {data.open_quality_issues.map((issue, index) => (
          <li key={index} className="flex items-start gap-2.5 text-sm">
            <Badge status={issue.severity === "error" ? "fail" : "warning"} className="mt-0.5 shrink-0">
              {issue.severity}
            </Badge>
            <span className="min-w-0">
              <span className="block text-[12.5px] font-medium text-foreground">{issue.check}</span>
              <span className="block text-[12.5px] leading-relaxed text-muted">{issue.message}</span>
            </span>
          </li>
        ))}
      </ul>
      {data.open_quality_issues_truncated && (
        <p className="mt-3 border-t border-hairline pt-2.5 text-[12px] text-unavail">
          {(data.open_quality_issue_total - data.open_quality_issues.length).toLocaleString()} further
          open issues are not listed here.
        </p>
      )}
    </>
  );
}

function ModelsTab({ data }: { data: DataHealth }) {
  if (data.active_models.length === 0) {
    return (
      <EmptyState
        title="No active models"
        hint="Run `make train && make score` to fit and publish the impact model."
      />
    );
  }
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {data.active_models.map((model) => (
        <div key={model.name} className="sheet min-w-0 p-4">
          <div className="title-md truncate capitalize text-foreground">{model.name.replace(/_/g, " ")}</div>
          <div className="mt-1 text-[12.5px] leading-relaxed text-muted">{model.algorithm}</div>
          <div className="text-[12px] text-faint">
            {model.version} · trained {formatDate(model.trained_at)}
          </div>
          <details className="group mt-2.5">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12.5px] font-medium text-muted transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
              <span aria-hidden className="inline-block w-3 text-center transition-transform group-open:rotate-90">
                ›
              </span>
              Validation as reported
            </summary>
            <pre className="scroll-thin mt-2 max-h-56 overflow-auto rounded-lg border border-hairline bg-court p-2.5 text-[10.5px] leading-relaxed text-muted">
              {JSON.stringify(model.validation, null, 1)}
            </pre>
          </details>
        </div>
      ))}
    </div>
  );
}
