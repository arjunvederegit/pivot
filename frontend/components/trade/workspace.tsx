"use client";

/**
 * The Trade Evaluator's builder: one workspace per team, a transaction lane between each
 * pair, and the boards used to put teams on the table.
 *
 * A workspace reads top to bottom the way a front office reads its own side of a call —
 * who we are (crest, record, the live legality chip), what the deal does to our books
 * (roster count before → after, salary out and in, payroll), what is on the move, then the
 * roster to pick from. Every roster card carries the league-impact cell, a drag handle and
 * an explicit "Send … to …" button, so nothing depends on dragging.
 *
 * All state lives in the page; these components only render it and report intent.
 */

import { useDraggable, useDroppable } from "@dnd-kit/core";
import clsx from "clsx";
import { useState } from "react";
import { LEGALITY_SHORT, money, ordinal, payrollDisclosure } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type { LegalityResponse, PickMove, RosterPlayer, RosterResponse, Team } from "@/lib/types";
import { HalfCourt, TransactionLane } from "@/components/court";
import { FitChip } from "@/components/fit";
import { IconClose, IconInfo } from "@/components/icons";
import { ImpactCell, ImpactInline } from "@/components/impact";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { Badge, Button, ButtonLink, Panel, Skeleton, SkeletonRows, SourceRail } from "@/components/ui";
import { useTeamDetail } from "@/components/trade/sheet";
import { CONTRACT_TOOLTIP } from "@/components/trade/verdict";

export interface Destination {
  id: string;
  abbr: string;
  name: string;
}

const FIELD =
  "rounded-lg border border-line bg-panel2 px-2.5 py-1.5 text-[13px] text-foreground transition-colors placeholder:text-faint hover:border-[#3a433f] focus:border-signal/60";

const HOW_IT_WORKS: { step: string; title: string; body: string }[] = [
  {
    step: "1",
    title: "Pick two teams",
    body: "Choose the sides of the deal — your team is preselected if you set one.",
  },
  {
    step: "2",
    title: "Move the assets",
    body: "Drag a player across the lane, or use the send button on any roster card.",
  },
  {
    step: "3",
    title: "Read the consequences",
    body: "The rules check runs live; Evaluate shows every team before and after, with the uncertainty.",
  },
];

/* ---------------------------------------------------------- workspace + lane */

export function TeamWorkspaceCell(props: {
  index: number;
  threeWay: boolean;
  teamIds: string[];
  teams: Team[];
  roster?: RosterResponse;
  rosterLoading: boolean;
  playerIndex: Record<string, RosterPlayer>;
  moves: Record<string, string>;
  originOf: Record<string, string>;
  validation: LegalityResponse | null;
  picks: PickMove[];
  setPicks: React.Dispatch<React.SetStateAction<PickMove[]>>;
  onRemove: () => void;
  onMove: (playerId: string, toTeamId: string) => void;
  onInspect: (player: RosterPlayer) => void;
}) {
  const { index, threeWay, teamIds, teams, moves, originOf } = props;
  const teamId = teamIds[index];
  const previousId = index > 0 ? teamIds[index - 1] : null;
  const laneActive = previousId
    ? Object.entries(moves).some(
        ([pid, to]) =>
          (originOf[pid] === previousId && to === teamId) ||
          (originOf[pid] === teamId && to === previousId),
      )
    : false;

  return (
    <>
      {previousId && (
        <LaneCell
          threeWay={threeWay}
          active={laneActive}
          left={teamIdentity(teams.find((t) => t.id === previousId)?.abbreviation).bright}
          right={teamIdentity(teams.find((t) => t.id === teamId)?.abbreviation).bright}
        />
      )}
      <TeamWorkspace {...props} teamId={teamId} />
    </>
  );
}

export function LaneCell({
  threeWay,
  active,
  left,
  right,
}: {
  threeWay: boolean;
  active: boolean;
  left: string;
  right: string;
}) {
  return (
    <div
      className={
        threeWay
          ? "flex items-center justify-center gap-3 py-1 xl:min-h-[27rem] xl:flex-col xl:px-1 xl:py-0"
          : "flex items-center justify-center gap-3 py-1 lg:min-h-[27rem] lg:flex-col lg:px-1 lg:py-0"
      }
    >
      <TransactionLane
        orientation="vertical"
        active={active}
        leftColor={left}
        rightColor={right}
        className={threeWay ? "h-16 w-9 xl:hidden" : "h-16 w-9 lg:hidden"}
      />
      <TransactionLane
        orientation="horizontal"
        active={active}
        leftColor={left}
        rightColor={right}
        className={threeWay ? "hidden h-11 w-24 xl:block" : "hidden h-11 w-24 lg:block"}
      />
      <span className={clsx("whitespace-nowrap text-[11.5px]", active ? "text-signal" : "text-faint")}>
        {active ? "Assets moving" : "No assets yet"}
      </span>
    </div>
  );
}

function LedgerCell({
  label,
  value,
  note,
  title,
}: {
  label: string;
  value: React.ReactNode;
  note: React.ReactNode;
  title?: string;
}) {
  return (
    <div className="min-w-0 bg-panel px-4 py-3" title={title}>
      <dt className="eyebrow truncate">{label}</dt>
      <dd className="numeral mt-1.5 truncate text-[1.2rem] leading-none text-foreground">{value}</dd>
      <dd className="mt-1.5 truncate text-[11.5px] text-faint">{note}</dd>
    </div>
  );
}

export function TeamWorkspace({
  teamId,
  teamIds,
  teams,
  roster,
  rosterLoading,
  playerIndex,
  moves,
  originOf,
  validation,
  picks,
  setPicks,
  onRemove,
  onMove,
  onInspect,
}: {
  teamId: string;
  teamIds: string[];
  teams: Team[];
  roster?: RosterResponse;
  rosterLoading: boolean;
  playerIndex: Record<string, RosterPlayer>;
  moves: Record<string, string>;
  originOf: Record<string, string>;
  validation: LegalityResponse | null;
  picks: PickMove[];
  setPicks: React.Dispatch<React.SetStateAction<PickMove[]>>;
  onRemove: () => void;
  onMove: (playerId: string, toTeamId: string) => void;
  onInspect: (player: RosterPlayer) => void;
}) {
  const team = teams.find((t) => t.id === teamId);
  const identity = teamIdentity(team?.abbreviation);
  const { setNodeRef, isOver } = useDroppable({ id: teamId });
  const legality = validation?.teams[teamId];

  const { data: detail } = useTeamDetail(teamId);

  const [query, setQuery] = useState("");
  const [positionFilter, setPositionFilter] = useState("");
  const [sort, setSort] = useState<"tei" | "age" | "name">("tei");
  const [pickYear, setPickYear] = useState(2027);
  const [pickTo, setPickTo] = useState("");

  const destinations: Destination[] = teamIds
    .filter((t) => t !== teamId)
    .map((t) => {
      const other = teams.find((x) => x.id === t);
      return { id: t, abbr: other?.abbreviation ?? "?", name: other?.full_name ?? "the other team" };
    });

  const all = roster?.roster ?? [];
  const staying = all
    .filter((p) => (moves[p.player_id] ?? teamId) === teamId)
    .filter((p) => !query || p.name.toLowerCase().includes(query.toLowerCase()))
    .filter((p) => !positionFilter || (p.position ?? "").toUpperCase().includes(positionFilter))
    .sort((a, b) =>
      sort === "tei"
        ? (b.tei ?? -99) - (a.tei ?? -99)
        : sort === "age"
          ? (a.age ?? 99) - (b.age ?? 99)
          : a.name.localeCompare(b.name),
    );
  const stayingTotal = all.filter((p) => (moves[p.player_id] ?? teamId) === teamId).length;
  const incoming = Object.entries(moves)
    .filter(([pid, to]) => to === teamId && originOf[pid] && originOf[pid] !== teamId)
    .map(([pid]) => playerIndex[pid])
    .filter((p): p is RosterPlayer => Boolean(p));
  const outgoing = all.filter((p) => moves[p.player_id] && moves[p.player_id] !== teamId);

  const standing = detail?.standing;
  // R2c: with partial contract coverage the verified payroll is withheld but a lower
  // bound is publishable. `≥` and the coverage count travel with the number so the
  // reader can never mistake a floor for the payroll.
  const payrollShown = payrollDisclosure(
    legality?.payroll_after,
    legality?.payroll_known_after,
    legality?.payroll_coverage_after,
  );
  const ownPicks = picks.filter((p) => p.from_team_id === teamId);

  return (
    <Panel
      as="section"
      accent={identity.bright}
      padded={false}
      className={clsx("@container transition-shadow", isOver && "ring-1 ring-signal")}
    >
      <div ref={setNodeRef}>
        {/* ---------------------------------------------------------- header */}
        <header className="flex items-start justify-between gap-3 px-5 pb-4 pt-5">
          <div className="flex min-w-0 items-start gap-3.5">
            <TeamLogo abbreviation={team?.abbreviation} name={team?.full_name} size={44} />
            <div className="min-w-0">
              <h2 className="text-balance font-serif text-[1.5rem] leading-[1.02] text-foreground @sm:text-[1.75rem]">
                {team?.full_name ?? "Loading…"}
              </h2>
              <p className="mt-1.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
                <span className="flex items-center gap-1.5 whitespace-nowrap font-medium text-foreground">
                  <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: identity.bright }} />
                  {team?.abbreviation ?? "—"}
                </span>
                {standing ? (
                  <span>
                    <span className="numeral font-normal text-foreground">
                      {standing.wins}–{standing.losses}
                    </span>
                    {standing.playoff_rank
                      ? ` · ${ordinal(standing.playoff_rank)} ${standing.conference ?? ""}`
                      : ""}
                    {detail?.season ? ` · ${detail.season}` : ""}
                  </span>
                ) : detail ? (
                  <span className="text-faint">record not available</span>
                ) : (
                  <span className="text-faint">loading record…</span>
                )}
                {/* In a narrow workspace the legality chip drops under the name. */}
                {legality && (
                  <span className="basis-full @sm:hidden">
                    <Badge status={legality.status}>{LEGALITY_SHORT[legality.status]}</Badge>
                  </span>
                )}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {legality && (
              <span className="hidden @sm:block">
                <Badge status={legality.status}>{LEGALITY_SHORT[legality.status]}</Badge>
              </span>
            )}
            <button
              type="button"
              onClick={onRemove}
              aria-label={`Remove ${team?.full_name ?? "team"} from the trade`}
              title="Remove this team"
              className="grid h-8 w-8 place-items-center rounded-lg border border-transparent text-muted transition-colors hover:border-illegal/40 hover:bg-illegal/10 hover:text-illegal"
            >
              <IconClose size={16} />
            </button>
          </div>
        </header>

        {/* ------------------------------------------------------ ledger row */}
        <dl className="grid grid-cols-2 gap-px border-y border-hairline bg-hairline @md:grid-cols-4">
          <LedgerCell
            label="Roster"
            value={
              legality ? (
                <span>
                  {legality.roster_before}
                  <span aria-hidden className="mx-1 font-normal text-faint">
                    →
                  </span>
                  {legality.roster_after}
                </span>
              ) : (
                all.length || "—"
              )
            }
            note={legality ? "before → after" : "players listed"}
          />
          <LedgerCell
            label="Salary out"
            value={legality?.outgoing_salary != null ? money(legality.outgoing_salary) : "—"}
            note={legality?.outgoing_salary != null ? "leaving" : "no salaries"}
            title={legality?.outgoing_salary != null ? undefined : CONTRACT_TOOLTIP}
          />
          <LedgerCell
            label="Salary in"
            value={legality?.incoming_salary != null ? money(legality.incoming_salary) : "—"}
            note={legality?.incoming_salary != null ? "arriving" : "no salaries"}
            title={legality?.incoming_salary != null ? undefined : CONTRACT_TOOLTIP}
          />
          <LedgerCell
            label="Payroll"
            value={payrollShown.value}
            note={
              payrollShown.kind === "verified"
                ? `from ${money(legality?.payroll_before)}`
                : payrollShown.note
            }
            title={
              payrollShown.kind === "verified"
                ? undefined
                : payrollShown.kind === "floor"
                  ? (legality?.payroll_coverage_note ?? CONTRACT_TOOLTIP)
                  : CONTRACT_TOOLTIP
            }
          />
        </dl>

        {/* --------------------------------------------------- moving assets */}
        {(incoming.length > 0 || outgoing.length > 0) && (
          <div className="space-y-4 border-b border-hairline bg-court/50 px-5 py-4">
            {incoming.length > 0 && (
              <AssetGroup
                direction="in"
                players={incoming}
                teamId={teamId}
                teamAbbr={team?.abbreviation ?? "?"}
                onUndo={(pid) => onMove(pid, originOf[pid])}
                counterpartOf={(pid) =>
                  teams.find((t) => t.id === originOf[pid])?.abbreviation ?? "?"
                }
              />
            )}
            {outgoing.length > 0 && (
              <AssetGroup
                direction="out"
                players={outgoing}
                teamId={teamId}
                teamAbbr={team?.abbreviation ?? "?"}
                onUndo={(pid) => onMove(pid, teamId)}
                counterpartOf={(pid) =>
                  teams.find((t) => t.id === moves[pid])?.abbreviation ?? "?"
                }
              />
            )}
          </div>
        )}

        {/* --------------------------------------------------- roster filters */}
        <div className="flex flex-wrap items-center gap-2 px-4 pb-2 pt-4">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter roster…"
            aria-label={`Filter the ${team?.full_name ?? "team"} roster by name`}
            className={clsx(FIELD, "w-full min-w-0 @xs:w-auto @xs:flex-1")}
          />
          <select
            value={positionFilter}
            onChange={(e) => setPositionFilter(e.target.value)}
            aria-label={`Filter the ${team?.abbreviation ?? "team"} roster by position`}
            className={clsx(FIELD, "flex-1 @xs:flex-none")}
          >
            <option value="">All</option>
            <option value="G">Guards</option>
            <option value="F">Forwards</option>
            <option value="C">Centers</option>
          </select>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as "tei" | "age" | "name")}
            aria-label={`Sort the ${team?.abbreviation ?? "team"} roster`}
            className={clsx(FIELD, "flex-1 @xs:flex-none")}
          >
            <option value="tei">Impact</option>
            <option value="age">Age</option>
            <option value="name">Name</option>
          </select>
        </div>

        {/* ---------------------------------------------------- list legend */}
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-5 pb-1.5 pt-2">
          <span className="min-w-0 text-[12px] text-muted">
            <span className="eyebrow">Roster</span>{" "}
            <span className="numeral text-foreground">{stayingTotal}</span>
            {all.length > 0 && all.every((p) => p.salary === null) && (
              <span className="text-faint" title={CONTRACT_TOOLTIP}>
                {" "}
                · no contracts on file
              </span>
            )}
          </span>
          <span
            className="eyebrow flex shrink-0 items-center gap-1 whitespace-nowrap"
            title="League impact percentile: where each player's Pivot Estimated Impact sits among every player the model scored this season. The strip is the league; the green line is the player; the shaded bracket is the estimate's 10–90% band."
          >
            League impact
            <IconInfo size={13} className="text-faint" />
          </span>
        </div>

        {/* ------------------------------------------------------ roster list */}
        {/* `relative`, so anything absolutely positioned inside a row (screen-reader text)
            is clipped by this scroller instead of stretching the page. */}
        <div className="relative">
          <div className="scroll-thin relative max-h-[28rem] space-y-1.5 overflow-y-auto px-3 pb-5 pt-1">
            {!roster && rosterLoading ? (
              <SkeletonRows rows={6} height="h-[3.5rem]" />
            ) : staying.length === 0 ? (
              <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center">
                <p className="font-serif text-[1.35rem] leading-tight text-foreground">
                  {stayingTotal === 0 ? "Everyone is on the move" : "No player matches"}
                </p>
                <p className="mx-auto mt-1.5 max-w-xs text-[13px] leading-relaxed text-muted">
                  {stayingTotal === 0
                    ? "Every listed player has been sent out. Undo one above to bring them back."
                    : "Clear the filter or choose a different position to see the rest of the roster."}
                </p>
                {stayingTotal > 0 && (
                  <Button
                    size="sm"
                    className="mt-3"
                    onClick={() => {
                      setQuery("");
                      setPositionFilter("");
                    }}
                  >
                    Clear filters
                  </Button>
                )}
              </div>
            ) : (
              staying.map((player) => (
                <RosterCard
                  key={player.player_id}
                  player={player}
                  rosterHasContracts={all.some(
                    (p) =>
                      p.salary !== null || p.contract_years_remaining !== null || p.contract_type,
                  )}
                  destinations={destinations}
                  onMove={onMove}
                  onInspect={() => onInspect(player)}
                />
              ))
            )}
          </div>
          {/* The list scrolls; a soft fade at its foot says there is more below. */}
          {staying.length > 6 && (
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-3 bottom-0 h-8 bg-gradient-to-t from-panel to-transparent"
            />
          )}
        </div>

        {/* ------------------------------------------------------------ picks */}
        <div className="border-t border-hairline px-5 py-3.5">
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-[13px] font-medium text-muted transition-colors hover:text-foreground">
              <span aria-hidden className="inline-block w-3 text-center transition-transform group-open:rotate-90">
                ›
              </span>
              Draft picks
              <span className="font-normal text-faint">· ownership unverified</span>
              {ownPicks.length > 0 && (
                <span className="numeral ml-auto text-[12px] text-foreground">{ownPicks.length} added</span>
              )}
            </summary>
            <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px]">
              <label className="sr-only" htmlFor={`pick-year-${teamId}`}>
                Draft year to send from {team?.abbreviation}
              </label>
              <select
                id={`pick-year-${teamId}`}
                value={pickYear}
                onChange={(e) => setPickYear(Number(e.target.value))}
                className={FIELD}
              >
                {[2027, 2028, 2029, 2030, 2031].map((y) => (
                  <option key={y}>{y}</option>
                ))}
              </select>
              <span className="whitespace-nowrap text-muted">1st round to</span>
              <label className="sr-only" htmlFor={`pick-to-${teamId}`}>
                Team receiving the pick
              </label>
              <select
                id={`pick-to-${teamId}`}
                value={pickTo}
                onChange={(e) => setPickTo(e.target.value)}
                className={FIELD}
              >
                <option value="">team…</option>
                {destinations.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.abbr}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                disabled={!pickTo}
                onClick={() =>
                  setPicks((prev) => [
                    ...prev,
                    {
                      from_team_id: teamId,
                      to_team_id: pickTo,
                      draft_year: pickYear,
                      round_number: 1,
                      is_hypothetical: true,
                    },
                  ])
                }
              >
                Add pick
              </Button>
            </div>
            <p className="mt-2.5 text-[12px] leading-snug text-faint">
              Pick ownership isn&apos;t imported, so every pick added here is labeled hypothetical
              and never counted as verified capital.
            </p>
            {ownPicks.length > 0 && (
              <ul className="mt-2.5 divide-y divide-hairline rounded-lg border border-hairline">
                {ownPicks.map((p, i) => (
                  <li
                    key={`${p.draft_year}-${p.to_team_id}-${i}`}
                    className="flex items-center justify-between gap-2 px-3 py-2 text-[13px]"
                  >
                    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="numeral whitespace-nowrap">
                        {p.draft_year} R{p.round_number}
                      </span>
                      <span className="whitespace-nowrap text-muted">
                        <span aria-hidden>→ </span>
                        {teams.find((x) => x.id === p.to_team_id)?.abbreviation}
                      </span>
                      <Badge status="unavailable">hypothetical</Badge>
                    </span>
                    <button
                      type="button"
                      className="shrink-0 rounded-md px-1.5 py-0.5 text-[12px] text-muted underline-offset-2 transition-colors hover:text-illegal hover:underline"
                      aria-label={`Remove the ${p.draft_year} first-round pick from this trade`}
                      onClick={() => setPicks((prev) => prev.filter((x) => x !== p))}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </details>
        </div>

        <div className="px-5 pb-4">
          <SourceRail
            source={roster?.source ?? "unknown source"}
            retrievedAt={roster?.source_retrieved_at ?? null}
          />
        </div>
      </div>
    </Panel>
  );
}

function AssetGroup({
  direction,
  players,
  onUndo,
  counterpartOf,
  teamId,
  teamAbbr,
}: {
  direction: "in" | "out";
  players: RosterPlayer[];
  onUndo: (playerId: string) => void;
  counterpartOf: (playerId: string) => string;
  /** The workspace's own team — incoming players are read for fit against it. */
  teamId: string;
  teamAbbr: string;
}) {
  const isIn = direction === "in";
  return (
    <div>
      {/* Direction is carried by the arrow AND the word, never by colour alone. */}
      <p className="flex items-center gap-2 text-[12.5px] font-medium text-foreground">
        <span aria-hidden className={isIn ? "text-signal" : "text-muted"}>
          {isIn ? "←" : "→"}
        </span>
        {isIn ? "Receiving" : "Sending"}
        <span className="numeral font-normal text-faint">{players.length}</span>
      </p>
      <ul className="mt-2 space-y-1.5">
        {players.map((p) => (
          <li
            key={p.player_id}
            className={clsx(
              "lane-in flex flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-lg border bg-panel px-2.5 py-2",
              isIn ? "border-signal/30" : "border-hairline",
            )}
            style={{
              // Arriving assets travel in from the lane; departing assets lean out.
              ["--lane-from" as string]: isIn ? "-14px" : "14px",
            }}
          >
            <PlayerPhoto nbaPlayerId={p.nba_player_id} name={p.name} size={28} />
            <span className="min-w-[7rem] flex-1 basis-[7rem] truncate text-[13.5px] font-medium text-foreground">
              {p.name}
            </span>
            <ImpactInline tei={p.tei} />
            {isIn && <FitChip playerId={p.player_id} teamId={teamId} teamAbbr={teamAbbr} />}
            <span className="whitespace-nowrap text-[12px] text-muted">
              {isIn ? "from" : "to"} {counterpartOf(p.player_id)}
            </span>
            <button
              type="button"
              onClick={() => onUndo(p.player_id)}
              aria-label={`Undo moving ${p.name}`}
              className="shrink-0 rounded-md px-1.5 py-0.5 text-[12px] font-medium text-signal underline-offset-2 hover:underline"
            >
              Undo
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RosterCard({
  player,
  destinations,
  onMove,
  onInspect,
  rosterHasContracts,
}: {
  player: RosterPlayer;
  /** Some player on this roster has contract data on file. */
  rosterHasContracts: boolean;
  destinations: Destination[];
  onMove: (playerId: string, toTeamId: string) => void;
  onInspect: () => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: player.player_id,
    data: { name: player.name },
  });

  const hasContract =
    player.salary !== null || player.contract_years_remaining !== null || player.contract_type;
  // With partial coverage, a player with no contract on file still says so on his own row;
  // only when the whole roster has none does the single legend note above the list cover it.
  const showContractLine = hasContract || rosterHasContracts;

  return (
    <div
      ref={setNodeRef}
      className={clsx(
        "group flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-hairline bg-panel2/50 py-2 pl-1 pr-2.5 transition-colors hover:border-line hover:bg-panel2",
        isDragging && "opacity-30",
      )}
    >
      <button
        type="button"
        {...listeners}
        {...attributes}
        aria-label={`Drag ${player.name} to another team`}
        className="flex h-9 w-5 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-faint transition-colors hover:text-signal"
      >
        <svg aria-hidden viewBox="0 0 8 14" className="h-3.5 w-2" fill="currentColor">
          <circle cx="2" cy="2" r="1.1" />
          <circle cx="6" cy="2" r="1.1" />
          <circle cx="2" cy="7" r="1.1" />
          <circle cx="6" cy="7" r="1.1" />
          <circle cx="2" cy="12" r="1.1" />
          <circle cx="6" cy="12" r="1.1" />
        </svg>
      </button>
      <button
        type="button"
        onClick={onInspect}
        className="flex min-w-0 flex-[1_1_9rem] items-center gap-3 rounded-md text-left"
        aria-label={`Open details for ${player.name}`}
      >
        <PlayerPhoto nbaPlayerId={player.nba_player_id} name={player.name} size={38} square />
        <span className="min-w-0">
          <span
            className="block truncate text-[14px] font-semibold leading-tight text-foreground decoration-line underline-offset-2 group-hover:underline"
            title={player.name}
          >
            {player.name}
          </span>
          <span className="mt-1 block truncate text-[12px] leading-tight text-muted">
            {player.position ?? "—"} · {player.age ? `${player.age.toFixed(0)} yrs` : "age —"}
            {player.archetype ? (
              <span className="hidden text-faint sm:inline"> · {player.archetype}</span>
            ) : (
              ""
            )}
          </span>
          {/* On a phone the role gets its own line rather than truncating to a few letters. */}
          {player.archetype && (
            <span className="mt-0.5 block text-[11.5px] leading-tight text-faint sm:hidden">
              {player.archetype}
            </span>
          )}
          {/* Real contract data, shown when the snapshot has any. When the whole roster
              has none, the workspace says so once above the list instead of repeating
              "salary —" on every row. */}
          {showContractLine && (
            <span
              className="mt-0.5 block text-[11.5px] leading-tight text-faint"
              title={player.salary === null ? CONTRACT_TOOLTIP : undefined}
            >
              {player.salary === null ? "salary —" : money(player.salary)}
              {player.contract_years_remaining !== null &&
                ` · ${player.contract_years_remaining}y left`}
              {player.contract_type && ` · ${player.contract_type}`}
            </span>
          )}
        </span>
      </button>
      <ImpactCell
        tei={player.tei}
        band={{ low: player.tei_low, high: player.tei_high }}
        name={player.name}
      />
      <span className="ml-auto flex shrink-0 gap-1.5">
        {destinations.map((d) => (
          <button
            key={d.id}
            type="button"
            onClick={() => onMove(player.player_id, d.id)}
            aria-label={`Send ${player.name} to ${d.abbr}`}
            title={`Send ${player.name} to the ${d.name}`}
            className="flex items-center gap-1 whitespace-nowrap rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12px] font-medium text-muted transition-colors hover:border-signal/60 hover:bg-signal/10 hover:text-signal"
          >
            <span aria-hidden>→</span>
            {d.abbr}
          </button>
        ))}
      </span>
    </div>
  );
}

/* --------------------------------------------------------------- empty flow */

/** The 30-franchise board — the fastest way into a deal, reused by both empty states. */
export function TeamBoard({
  teams,
  selected,
  onAdd,
  columns = "grid-cols-5 sm:grid-cols-8 lg:grid-cols-10",
}: {
  teams: Team[];
  selected: string[];
  onAdd: (id: string) => void;
  columns?: string;
}) {
  if (teams.length === 0) {
    return (
      <div className={`grid gap-1.5 ${columns}`}>
        {Array.from({ length: 30 }).map((_, i) => (
          <Skeleton key={i} className="h-[62px]" />
        ))}
      </div>
    );
  }
  return (
    <ul className={`grid gap-1.5 ${columns}`}>
      {teams.map((team) => {
        const taken = selected.includes(team.id);
        return (
          <li key={team.id}>
            <button
              type="button"
              disabled={taken}
              aria-label={
                taken ? `${team.full_name} is already in this trade` : `Add ${team.full_name} to the trade`
              }
              title={team.full_name}
              onClick={() => onAdd(team.id)}
              className="hover-frame flex w-full flex-col items-center gap-1.5 rounded-lg border border-hairline bg-panel2/60 px-1 py-2.5 disabled:pointer-events-none disabled:opacity-30"
            >
              <TeamLogo abbreviation={team.abbreviation} size={28} decorative />
              <span className="numeral text-[12px] leading-none text-muted">{team.abbreviation}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function StartHere({
  teams,
  onAdd,
  onPick,
}: {
  teams: Team[];
  onAdd: (id: string) => void;
  onPick: () => void;
}) {
  return (
    <section aria-labelledby="start-here-title" className="panel relative overflow-hidden">
      {/* The floor the deal is drawn on: a chalk half-court under the opening copy. */}
      <HalfCourt className="pointer-events-none absolute inset-y-0 left-0 hidden h-full w-[44%] text-foreground/[0.08] lg:block" />
      <div className="relative grid gap-8 p-5 sm:p-7 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:gap-12 lg:p-9">
        <div className="min-w-0 lg:pt-4">
          <div className="eyebrow flex items-center gap-2">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />
            Start here
          </div>
          <h2 id="start-here-title" className="title-lg mt-3 text-foreground">
            Put two teams on the board
          </h2>
          <p className="mt-3 max-w-md text-[15px] leading-relaxed text-muted">
            A deal needs both sides. Pick the franchises you want to trade between — the rules
            check starts running the moment an asset crosses the lane.
          </p>
          <div className="mt-6 flex flex-wrap gap-2.5">
            <Button variant="primary" onClick={onPick}>
              Search for a team
            </Button>
            <ButtonLink href="/team-outlook" variant="secondary">
              Browse team outlooks
            </ButtonLink>
          </div>
        </div>
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="eyebrow">Or start from a franchise</span>
            <span className="text-[12px] text-faint">One click puts them on the board</span>
          </div>
          <TeamBoard teams={teams} selected={[]} onAdd={onAdd} columns="grid-cols-5 sm:grid-cols-6" />
        </div>
      </div>
    </section>
  );
}

export function AddPartnerSlot({
  teams,
  selected,
  onAdd,
}: {
  teams: Team[];
  selected: string[];
  onAdd: (id: string) => void;
}) {
  return (
    <div className="relative flex min-h-[260px] flex-col overflow-hidden rounded-xl border border-dashed border-line bg-panel/60 p-5 sm:p-6">
      <div className="relative">
        <div className="eyebrow">The other side</div>
        <p className="mt-2 font-serif text-[1.75rem] leading-none text-foreground">Add a trade partner</p>
        <p className="mt-2.5 max-w-sm text-[13.5px] leading-relaxed text-muted">
          One side is set. Choose who&apos;s on the other end of the call and the live rules check
          turns on.
        </p>
      </div>
      <div className="relative mt-5">
        <TeamBoard
          teams={teams}
          selected={selected}
          onAdd={onAdd}
          columns="grid-cols-5 sm:grid-cols-6"
        />
      </div>
    </div>
  );
}

export function HowItWorks({ variant = "strip" }: { variant?: "strip" | "rail" }) {
  const rail = variant === "rail";
  return (
    <section
      aria-label="How the evaluator works"
      className={rail ? "rounded-xl border border-hairline px-4 py-4" : "rounded-xl border border-hairline px-5 py-5"}
    >
      <div className="eyebrow mb-3.5">How the evaluator works</div>
      <ol className={rail ? "space-y-3.5" : "grid gap-x-8 gap-y-4 sm:grid-cols-3"}>
        {HOW_IT_WORKS.map((item) => (
          <li key={item.step} className="flex gap-3">
            <span className="numeral grid h-6 w-6 shrink-0 place-items-center rounded-full border border-signal/40 text-[12px] text-signal">
              {item.step}
            </span>
            <span className="min-w-0">
              <span className="block text-[14px] font-semibold text-foreground">{item.title}</span>
              <span className="mt-0.5 block text-[12.5px] leading-snug text-muted">{item.body}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
