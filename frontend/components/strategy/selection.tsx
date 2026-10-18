"use client";

/**
 * Step one of the decision board: choosing the saved deals.
 *
 * Each card says who receives what, and from which team's side the deal would be scored
 * under the scenario currently chosen — the backend scores every deal from the scenario's
 * team, and a deal that does not involve that team comes back with no evaluation at all.
 * Saying so on the card, before the comparison runs, is what keeps that from being a
 * silent disappearance.
 *
 * The card reads like a trade sheet: the deal's name, then each team's side of the
 * exchange next to each other, then the side the board will score it from.
 */

import clsx from "clsx";
import Link from "next/link";
import { asUtc } from "@/lib/format";
import type { TradeDetail, TradeSummary } from "@/lib/types";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { Button, Panel, Skeleton } from "@/components/ui";
import { MAX_SELECTED, MIN_SELECTED, receivedByTeam } from "./model";
import { AVATAR_SIZE, Dot } from "./parts";

/** The side a deal is scored from under the current scenario choice. */
export interface ScoredSide {
  /** Abbreviation of the team whose evaluation the board will read. */
  abbr: string | null;
  /** True when a scenario is chosen and its team is not part of this deal. */
  missing: boolean;
}

function shortDate(iso: string): string {
  const d = new Date(asUtc(iso));
  if (Number.isNaN(d.getTime())) return "date unknown";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** A check that reads as a checkbox: square-ish, filled when the deal is on the board. */
function CheckMark({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden
      className={clsx(
        "flex h-5 w-5 shrink-0 items-center justify-center rounded-[6px] border transition-colors",
        selected ? "border-signal bg-signal text-ink" : "border-line bg-panel2 text-transparent group-hover:border-signal/60",
      )}
    >
      <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
      </svg>
    </span>
  );
}

/** The inside of a deal card. Phrasing content only, so it can live inside a button. */
function DealCardContent({
  trade,
  detail,
  side,
  nbaIdByPlayerId,
  control,
  footerExtra,
}: {
  trade: TradeSummary;
  detail: TradeDetail | undefined;
  side: ScoredSide;
  nbaIdByPlayerId: Record<string, number>;
  control?: React.ReactNode;
  footerExtra?: React.ReactNode;
}) {
  const groups = detail ? receivedByTeam(detail) : [];
  return (
    <>
      <span className="flex items-start gap-3 px-4 pt-4">
        <span className="mt-0.5 flex shrink-0 items-center -space-x-1.5">
          {trade.teams.map((abbr) => (
            <TeamLogo key={abbr} abbreviation={abbr} size={26} decorative />
          ))}
        </span>
        <span className="min-w-0 flex-1">
          {/* The saved name is the card's identity: always visible text, wrapped, never cut to one line. */}
          <span className="title-md line-clamp-2 block break-words text-foreground">{trade.name}</span>
          <span className="mt-1 block text-[12px] leading-snug text-faint">
            {trade.teams.join(" ↔ ")} · {trade.n_players} player{trade.n_players === 1 ? "" : "s"}
            {trade.n_picks ? ` · ${trade.n_picks} pick${trade.n_picks === 1 ? "" : "s"}` : ""}
          </span>
        </span>
        {control}
      </span>

      <span className="@container block flex-1 px-4 pb-4 pt-3.5">
        {!detail ? (
          <span className="grid grid-cols-2 gap-2">
            <Skeleton className="h-[74px]" />
            <Skeleton className="h-[74px]" />
          </span>
        ) : groups.length === 0 ? (
          <span className="block text-[12.5px] text-faint">No assets recorded on this deal.</span>
        ) : (
          // Each team's side of the exchange, next to each other once the card has room for two
          // readable columns of names; stacked below that, so no name is cut to fit.
          <span className="grid gap-2 @min-[20rem]:grid-cols-2">
            {groups.map((group) => (
              <span
                key={group.abbreviation}
                className="block min-w-0 rounded-lg border border-hairline bg-panel2/70 px-2.5 py-2.5"
              >
                <span className="flex items-center gap-1.5">
                  <TeamLogo abbreviation={group.abbreviation} size={16} decorative />
                  <span className="numeral text-[12px] leading-none text-foreground">{group.abbreviation}</span>
                  <span className="text-[11.5px] leading-none text-faint">receives</span>
                </span>
                <span className="mt-2 block space-y-1.5">
                  {group.items.slice(0, 3).map((item) => (
                    <span key={item.key} className="flex min-w-0 items-center gap-2">
                      {item.playerName ? (
                        <PlayerPhoto
                          nbaPlayerId={item.playerId ? (nbaIdByPlayerId[item.playerId] ?? null) : null}
                          name={item.playerName}
                          size={AVATAR_SIZE}
                        />
                      ) : (
                        <span
                          aria-hidden
                          className="flex shrink-0 items-center justify-center rounded-full border border-dashed border-line text-[10px] text-muted"
                          style={{ width: AVATAR_SIZE, height: AVATAR_SIZE }}
                        >
                          Pick
                        </span>
                      )}
                      <span className="line-clamp-3 min-w-0 break-words text-[13px] leading-tight text-foreground">
                        {item.label}
                      </span>
                    </span>
                  ))}
                  {group.items.length > 3 && (
                    <span className="block pl-[38px] text-[11.5px] text-faint">+{group.items.length - 3} more</span>
                  )}
                </span>
              </span>
            ))}
          </span>
        )}
      </span>

      <span className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-hairline px-4 py-2.5 text-[12px]">
        <span className="text-faint">Saved {shortDate(trade.created_at)}</span>
        {side.missing ? (
          <span className="flex items-center gap-1.5 text-conditional">
            <span aria-hidden>~</span>
            {side.abbr} not in this deal
          </span>
        ) : side.abbr ? (
          <span className="text-muted">
            Scored for <span className="font-medium text-foreground">{side.abbr}</span>
          </span>
        ) : null}
        {footerExtra}
      </span>
    </>
  );
}

/** One selectable saved deal. Everything inside stays phrasing-level (button content). */
export function DealCard({
  trade,
  detail,
  selected,
  side,
  onToggle,
  nbaIdByPlayerId,
}: {
  trade: TradeSummary;
  detail: TradeDetail | undefined;
  selected: boolean;
  side: ScoredSide;
  onToggle: () => void;
  nbaIdByPlayerId: Record<string, number>;
}) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={selected}
      aria-label={`${selected ? "Deselect" : "Select"} deal ${trade.name}, ${trade.teams.join(" and ")}`}
      onClick={onToggle}
      className={clsx(
        "panel group flex min-w-0 flex-col text-left transition-[border-color,background-color] duration-200",
        selected ? "border-signal/55" : "hover:border-line hover:bg-panel2/40",
      )}
      style={
        selected
          ? { background: "color-mix(in srgb, var(--signal) 5%, var(--arena))" }
          : undefined
      }
    >
      <DealCardContent
        trade={trade}
        detail={detail}
        side={side}
        nbaIdByPlayerId={nbaIdByPlayerId}
        control={<CheckMark selected={selected} />}
      />
    </button>
  );
}

/** The single saved deal, shown read-only while there is nothing to compare it with. */
export function SavedDealPreview({
  trade,
  detail,
  side,
  nbaIdByPlayerId,
}: {
  trade: TradeSummary;
  detail: TradeDetail | undefined;
  side: ScoredSide;
  nbaIdByPlayerId: Record<string, number>;
}) {
  return (
    <article className="panel flex min-w-0 flex-col" aria-label={`Saved deal ${trade.name}`}>
      <DealCardContent
        trade={trade}
        detail={detail}
        side={side}
        nbaIdByPlayerId={nbaIdByPlayerId}
        control={
          <span className="shrink-0 rounded-full border border-hairline px-2 py-0.5 text-[11px] text-faint">
            1 of 2 needed
          </span>
        }
        footerExtra={
          <Link href={`/trades/${trade.id}`} className="font-medium text-signal hover:text-foreground">
            Open the report
          </Link>
        }
      />
    </article>
  );
}

/** The empty second seat beside a lone deal — the comparison it is waiting for. */
export function SecondSeat({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-w-0 flex-col justify-center overflow-hidden rounded-xl border border-dashed border-line bg-panel/40">
      {children}
    </div>
  );
}

const METHOD_STEPS = [
  {
    title: "Put deals on the board",
    body: `Select ${MIN_SELECTED}–${MAX_SELECTED} saved deals. Each one keeps the component scores the backend computed when it was evaluated.`,
  },
  {
    title: "Weight what you care about",
    body: "Drag the priority sliders. The ranking recomputes instantly from the stored scores — no model is re-run, so the numbers never drift.",
  },
  {
    title: "Read it seven ways",
    body: "Summary, team impact, basketball fit, financial, timeline, risk and sensitivity — the same comparison, sliced by the question you are actually asking.",
  },
];

/** Explains the board while the user is still choosing — and fills the column. */
export function MethodRail({ selectedCount }: { selectedCount: number | null }) {
  return (
    <Panel title="How the board works">
      <ol className="space-y-4">
        {METHOD_STEPS.map((step, index) => (
          <li key={step.title} className="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-3">
            <span aria-hidden className="stat-serif text-[1.45rem] leading-[1.05] text-signal">
              {index + 1}
            </span>
            <span className="min-w-0">
              <span className="block text-[13.5px] font-semibold text-foreground">{step.title}</span>
              <span className="mt-1 block text-[12.5px] leading-snug text-muted">{step.body}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-5 border-t border-hairline pt-4 text-[12px] leading-relaxed text-muted">
        A component the backend could not score shows as{" "}
        <span className="data text-unavail">n/a</span> and is dropped from the maths rather than
        filled in with a guess. That is why a deal missing contract data can still rank — it is
        simply being judged on less.
      </p>
      {selectedCount !== null && (
        <p className="mt-3 flex items-center gap-2 text-[12px] text-faint" aria-live="polite">
          <Dot tone={selectedCount >= MIN_SELECTED ? "signal" : "faint"} />
          {selectedCount === 0
            ? `Nothing selected yet — pick ${MIN_SELECTED} deals to start.`
            : selectedCount < MIN_SELECTED
              ? `${selectedCount} selected — ${MIN_SELECTED - selectedCount} more to compare.`
              : `${selectedCount} selected and ready to compare.`}
        </p>
      )}
    </Panel>
  );
}

/**
 * The sticky control bar under the cards: how many are on the board, where the starting
 * weights come from, whose side every deal is scored from, and the one action.
 */
export function SelectionBar({
  selectedCount,
  scenarioId,
  onScenario,
  scenarioOptions,
  sideLabel,
  missingNames,
  missingTeam,
  pending,
  onCompare,
}: {
  selectedCount: number;
  scenarioId: string;
  onScenario: (id: string) => void;
  scenarioOptions: { id: string; label: string }[];
  sideLabel: string;
  missingNames: string[];
  missingTeam: string | null;
  pending: boolean;
  onCompare: () => void;
}) {
  return (
    <div className="sticky bottom-3 z-30 mt-4">
      <div
        className="panel px-4 py-3.5 backdrop-blur-md sm:px-5"
        style={{
          background: "color-mix(in srgb, var(--arena) 94%, transparent)",
          boxShadow: "var(--shadow-pop)",
        }}
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <p className="flex items-baseline gap-2" aria-live="polite">
            <span className="stat-serif text-[2.1rem] leading-none text-foreground">{selectedCount}</span>
            <span className="whitespace-nowrap text-[12.5px] text-muted">of {MAX_SELECTED} selected</span>
          </p>

          {/* Label and select stay on one line once there is room; the scored side always gets
              its own line under them, so the select is never squeezed to a sliver. */}
          <div className="flex min-w-0 flex-[1_1_16rem] flex-wrap items-center gap-x-3 gap-y-1.5">
            <label htmlFor="scenario-weights" className="shrink-0 whitespace-nowrap text-[12.5px] font-medium text-muted">
              Starting weights
            </label>
            <select
              id="scenario-weights"
              aria-label="Scenario providing the starting weights"
              aria-describedby="scored-side"
              value={scenarioId}
              onChange={(event) => onScenario(event.target.value)}
              className="min-w-[12rem] flex-1 rounded-lg border border-line bg-panel2 px-3 py-2 text-[13px] text-foreground transition-colors hover:border-[#4a4a50] md:max-w-80"
            >
              <option value="">League default weights</option>
              {/* Name alone is not an identity: Team Outlook generates "BOS — Contend now"
                  every time the button is pressed. Labels are built for the whole list at
                  once so uniqueness is a property of the output. */}
              {scenarioOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
            <span id="scored-side" className="flex basis-full items-center gap-2 text-[12px] text-muted">
              <Dot />
              {sideLabel}
            </span>
          </div>

          <Button
            variant="primary"
            className="w-full shrink-0 sm:ml-auto sm:w-auto"
            disabled={selectedCount < MIN_SELECTED || pending}
            onClick={onCompare}
          >
            {pending
              ? "Comparing…"
              : selectedCount < MIN_SELECTED
                ? `Select ${MIN_SELECTED - selectedCount} more`
                : `Compare ${selectedCount} deals`}
          </Button>
        </div>
        {missingNames.length > 0 && missingTeam && (
          <p className="mt-3 flex gap-2 border-t border-hairline pt-2.5 text-[12px] leading-snug text-conditional">
            <span aria-hidden>~</span>
            <span className="min-w-0">
              {missingNames.length === 1 ? "1 selected deal does" : `${missingNames.length} selected deals do`} not
              involve {missingTeam}, so the backend has no {missingTeam} evaluation to put on the board:{" "}
              {missingNames.join(", ")}. {missingNames.length === 1 ? "It" : "They"} will be listed as left
              off, not scored.
            </span>
          </p>
        )}
      </div>
    </div>
  );
}
