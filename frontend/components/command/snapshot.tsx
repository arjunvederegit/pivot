"use client";

/**
 * Your work — what has been saved in this workspace, newest first.
 *
 * Two cards side by side (deals, strategies), each its own height. They used to share
 * one instrument, which stretched a one-deal list to the height of five strategies and
 * left a block of empty ink; now each card ends where its content does. Each opens on
 * its count — the figure that says what can be done next — and the deals card closes on
 * what the Decision Board needs and how to give it that.
 *
 * Saved strategies are generated with the same name every time ("BOS — Contend now"), so
 * a list of names alone reads as one row repeated. Each row therefore leads with what
 * does differ — the moment it was saved — then horizon and risk, and the card opens with
 * the strategy mix rather than the list.
 */

import clsx from "clsx";
import Link from "next/link";
import { count } from "@/lib/format";
import type { Scenario, TradeSummary } from "@/lib/types";
import { asUtc } from "@/components/command/time";
import { IconArrowRight } from "@/components/icons";
import { TeamLogo } from "@/components/media";
import { Badge, ButtonLink, EmptyState, ErrorState, Skeleton } from "@/components/ui";

const SHOWN = 5;

function stamp(iso: string): string {
  const d = new Date(asUtc(iso));
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} · ${d.toLocaleTimeString(
    "en-US",
    { hour: "numeric", minute: "2-digit" },
  )}`;
}

const newestFirst = <T extends { created_at: string }>(rows: T[]) =>
  [...rows].sort((a, b) => Date.parse(asUtc(b.created_at)) - Date.parse(asUtc(a.created_at)));

function RowsSkeleton() {
  return (
    <div className="space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-11" />
      ))}
    </div>
  );
}

/** Card head: the label, the count as the headline figure, and where the list leads. */
function CardHead({
  id,
  title,
  figure,
  sub,
  action,
}: {
  id: string;
  title: string;
  /** The count, or null while it is being read. */
  figure: string | null;
  sub: string;
  action: React.ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-4 px-5 pt-4">
      <div className="min-w-0 flex-1">
        <h3 id={id} className="eyebrow">
          {title}
        </h3>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          {figure === null ? (
            <Skeleton className="h-8 w-10" />
          ) : (
            <span
              className={clsx(
                "stat-serif text-[2.2rem] leading-none",
                figure === "—" ? "text-faint" : "text-foreground",
              )}
            >
              {figure}
            </span>
          )}
          <span className="text-[12.5px] text-muted">{sub}</span>
        </div>
      </div>
      <div className="shrink-0 pt-0.5">{action}</div>
    </header>
  );
}

function HeadLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="group inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] font-medium text-signal transition-colors hover:text-foreground"
    >
      {children}
      <IconArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
    </Link>
  );
}

/* ------------------------------------------------------------- saved deals */

/** The Decision Board (`/strategy-lab`) compares two to five saved deals. */
const BOARD_MIN = 2;

/**
 * What the Decision Board does and whether it has enough to work with — read off the
 * saved-deal count alone, the same reading the workflow's "Decide" step shows.
 */
function BoardReadout({ saved }: { saved: number }) {
  const short = saved < BOARD_MIN;
  return (
    <div className="mt-4 flex flex-col gap-3 rounded-lg border border-hairline bg-panel2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="title-sm text-foreground">Decision Board</span>
          {short ? (
            <span className="flex items-center gap-1.5 text-[12px] text-muted">
              <span aria-hidden className="flex gap-1">
                {Array.from({ length: BOARD_MIN }, (_, i) => (
                  <span
                    key={i}
                    className={
                      i < saved
                        ? "h-2 w-2 rounded-full bg-signal"
                        : "h-2 w-2 rounded-full border border-faint"
                    }
                  />
                ))}
              </span>
              {saved} of {BOARD_MIN} deals
            </span>
          ) : (
            <Badge status="pass">ready</Badge>
          )}
        </div>
        <p className="mt-1 text-pretty text-[12.5px] leading-snug text-muted">
          {saved === 1
            ? "One deal isn't a comparison — the board ranks two to five saved deals against the priorities you set. Save another deal to put them side by side."
            : "Weighs two to five saved deals against the priorities you set and shows which one holds up."}
        </p>
      </div>
      <ButtonLink
        href="/trade-evaluator"
        size="sm"
        variant={short ? "primary" : "secondary"}
        className="shrink-0 self-start sm:self-center"
      >
        Build another deal
      </ButtonLink>
    </div>
  );
}

function SavedDeals({ trades, error }: { trades: TradeSummary[] | undefined; error: unknown }) {
  return (
    <section aria-labelledby="saved-deals-heading" className="panel flex min-w-0 flex-col">
      <CardHead
        id="saved-deals-heading"
        title="Saved deals"
        figure={error ? "—" : !trades ? null : String(trades.length)}
        sub={
          error
            ? "not loaded"
            : !trades
              ? "reading saved deals…"
              : trades.length === 0
                ? "nothing saved yet"
                : `${trades.length === 1 ? "deal" : "deals"} saved · newest first`
        }
        action={<HeadLink href="/strategy-lab">Decision Board</HeadLink>}
      />
      <div className="flex-1 px-5 pb-5 pt-4">
        {error ? (
          <ErrorState message={`Could not load saved deals: ${String(error)}`} />
        ) : !trades ? (
          <RowsSkeleton />
        ) : trades.length === 0 ? (
          <EmptyState
            className="py-7"
            title="No deals saved yet"
            hint="Build one in the Trade Evaluator and save it — it shows up here, ready to compare."
            action={
              <ButtonLink href="/trade-evaluator" size="sm" variant="primary">
                Build a first deal
              </ButtonLink>
            }
          />
        ) : (
          <>
            <ul className="divide-y divide-hairline border-y border-hairline">
              {newestFirst(trades)
                .slice(0, SHOWN)
                .map((trade) => (
                  <li key={trade.id}>
                    <Link
                      href={`/trades/${trade.id}`}
                      className="group -mx-2 flex items-center gap-3 rounded-md px-2 py-2.5 transition-colors hover:bg-panel2/70"
                    >
                      <span className="flex shrink-0 items-center -space-x-1.5" aria-hidden>
                        {trade.teams.map((abbr) => (
                          <span key={abbr} className="rounded-[5px] bg-panel p-0.5">
                            <TeamLogo abbreviation={abbr} size={24} decorative />
                          </span>
                        ))}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[14px] font-medium text-foreground transition-colors group-hover:text-signal">
                          {trade.name}
                        </span>
                        <span className="mt-0.5 block truncate text-[12px] text-muted">
                          {trade.teams.join(" ⇄ ")} · {count(trade.n_players, "player")} ·{" "}
                          {count(trade.n_picks, "pick")}
                        </span>
                      </span>
                      <span className="hidden shrink-0 text-[12px] tabular-nums text-faint sm:inline">
                        {stamp(trade.created_at)}
                      </span>
                      <IconArrowRight
                        size={15}
                        className="shrink-0 text-faint transition-colors group-hover:text-signal"
                      />
                    </Link>
                  </li>
                ))}
            </ul>
            {trades.length > SHOWN && (
              <p className="mt-2.5 text-[12px] text-faint">
                Showing {SHOWN} of {trades.length} · the Decision Board lists every one
              </p>
            )}
            <BoardReadout saved={trades.length} />
          </>
        )}
      </div>
    </section>
  );
}

/* --------------------------------------------------------- saved strategies */

function SavedStrategies({ scenarios, error }: { scenarios: Scenario[] | undefined; error: unknown }) {
  const mix = new Map<string, number>();
  const teams = new Set<string>();
  for (const s of scenarios ?? []) {
    mix.set(s.strategy, (mix.get(s.strategy) ?? 0) + 1);
    teams.add(s.focal_team.abbreviation);
  }
  const total = scenarios?.length ?? 0;

  return (
    <section aria-labelledby="saved-strategies-heading" className="panel flex min-w-0 flex-col">
      <CardHead
        id="saved-strategies-heading"
        title="Saved strategies"
        figure={error ? "—" : !scenarios ? null : String(total)}
        sub={
          error
            ? "not loaded"
            : !scenarios
              ? "reading saved strategies…"
              : total === 0
                ? "nothing saved yet"
                : `${total === 1 ? "strategy" : "strategies"} · ${count(teams.size, "team")} · newest first`
        }
        action={<HeadLink href="/team-outlook">Teams</HeadLink>}
      />
      <div className="flex-1 px-5 pb-5 pt-4">
        {error ? (
          <ErrorState message={`Could not load saved strategies: ${String(error)}`} />
        ) : !scenarios ? (
          <RowsSkeleton />
        ) : scenarios.length === 0 ? (
          <EmptyState
            className="py-7"
            title="No strategies yet"
            hint="Choose how a team should build on its roster page — it sets how every deal is scored."
            action={
              <ButtonLink href="/team-outlook" size="sm">
                Choose a strategy
              </ButtonLink>
            }
          />
        ) : (
          <>
            {/* The mix first: what the saved strategies say, as shares of the total. A
                neutral series — a strategy mix is a composition, not a judgement. */}
            <div className="flex h-1 w-full gap-[3px] overflow-hidden rounded-full" aria-hidden>
              {[...mix.entries()].map(([strategy, n], i) => (
                <span
                  key={strategy}
                  className="h-full rounded-full"
                  style={{
                    width: `${(n / total) * 100}%`,
                    background: "var(--series-b)",
                    opacity: Math.max(0.2, 0.5 - i * 0.12),
                  }}
                />
              ))}
            </div>
            <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
              {[...mix.entries()].map(([strategy, n]) => (
                <span key={strategy}>
                  <span className="capitalize text-foreground">{strategy.replaceAll("_", " ")}</span>{" "}
                  <span className="tabular-nums">× {n}</span>
                </span>
              ))}
            </p>

            <ul className="mt-3 divide-y divide-hairline border-y border-hairline">
              {newestFirst(scenarios)
                .slice(0, SHOWN)
                .map((scenario) => (
                  <li key={scenario.id} className="flex items-center gap-3 py-2.5">
                    <TeamLogo abbreviation={scenario.focal_team.abbreviation} size={24} decorative />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-medium text-foreground">
                        {scenario.name}
                      </span>
                      {/* Wraps rather than truncates: the save time is what tells these rows
                          apart, and at 390px a truncated line cut the risk off. */}
                      <span className="mt-0.5 flex flex-wrap gap-x-1.5 text-[12px] leading-snug text-faint">
                        <span className="tabular-nums text-muted">{stamp(scenario.created_at)}</span>
                        <span>
                          {/* At phone width this half wraps under the time; a separator
                              would then open the line, so it only shows where it sits inline. */}
                          <span aria-hidden className="hidden sm:inline">
                            ·{" "}
                          </span>
                          {scenario.horizon_years}y · {scenario.risk_tolerance.replaceAll("_", " ")} risk
                        </span>
                      </span>
                    </span>
                    <span className="hidden shrink-0 sm:block">
                      <Badge status="info" glyph={false}>
                        {scenario.strategy.replaceAll("_", " ")}
                      </Badge>
                    </span>
                    <Link
                      href={`/trade-evaluator?scenario=${scenario.id}`}
                      aria-label={`Build a deal under ${scenario.name}, saved ${stamp(scenario.created_at)}`}
                      className="group -my-1.5 inline-flex shrink-0 items-center gap-1 rounded-md py-1.5 pl-2 text-[13px] font-medium text-signal transition-colors hover:text-foreground"
                    >
                      Build
                      <IconArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
                    </Link>
                  </li>
                ))}
            </ul>
            {total > SHOWN && (
              <p className="mt-2.5 text-[12px] text-faint">Showing the {SHOWN} newest of {total}</p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ the instrument */

export function YourWork({
  trades,
  tradesError,
  scenarios,
  scenariosError,
}: {
  trades: TradeSummary[] | undefined;
  tradesError: unknown;
  scenarios: Scenario[] | undefined;
  scenariosError: unknown;
}) {
  return (
    // items-start: each card is as tall as what it holds, so a short deals list never
    // stretches to the strategies list's height.
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
      <SavedDeals trades={trades} error={tradesError} />
      <SavedStrategies scenarios={scenarios} error={scenariosError} />
    </div>
  );
}
