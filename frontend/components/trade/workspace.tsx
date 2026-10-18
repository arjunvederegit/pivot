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
import { LEGALITY_SHORT, height, money, ordinal, payrollDisclosure } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type { LegalityResponse, PickMove, RosterPlayer, RosterResponse, Team } from "@/lib/types";
import { FitChip } from "@/components/fit";
import { IconClose, IconSwap } from "@/components/icons";
import { ImpactInline } from "@/components/impact";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { Badge, Button, ButtonLink, Panel, SkeletonRows, SourceRail } from "@/components/ui";
import { TeamGrid } from "@/components/team-card";
import { useTeamDetail } from "@/components/trade/sheet";
import { CONTRACT_TOOLTIP } from "@/components/trade/verdict";

export interface Destination {
  id: string;
  abbr: string;
  name: string;
}

const FIELD =
  "rounded-md border border-line bg-panel px-2.5 py-1.5 text-[13px] text-foreground transition-colors placeholder:text-faint hover:border-[#4a4a50] focus:border-[#6a6a70]";

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
}: {
  threeWay: boolean;
  active: boolean;
  /** Retained for call-site compatibility — the lane no longer draws team colours. */
  left?: string;
  right?: string;
}) {
  return (
    <div
      className={
        threeWay
          ? "flex items-center justify-center py-1 xl:min-h-[8rem] xl:px-1 xl:py-0"
          : "flex items-center justify-center py-1 lg:min-h-[8rem] lg:px-1 lg:py-0"
      }
    >
      <span
        className={clsx(
          "grid h-10 w-10 place-items-center rounded-full border transition-colors",
          active ? "border-foreground text-foreground" : "border-line text-faint",
        )}
        title={active ? "Assets moving" : "No assets yet"}
      >
        <IconSwap size={20} className={threeWay ? "rotate-90 xl:rotate-0" : "rotate-90 lg:rotate-0"} />
      </span>
      <span className="sr-only">{active ? "Assets moving" : "No assets yet"}</span>
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
    <div className="min-w-0 bg-panel px-4 py-2.5" title={title}>
      <dt className="eyebrow truncate">{label}</dt>
      <dd className="numeral mt-1 truncate text-[1.05rem] leading-none text-foreground">{value}</dd>
      <dd className="mt-1 truncate text-[11px] text-faint">{note}</dd>
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

  const incomingSalary = legality?.incoming_salary;

  return (
    <Panel as="section" padded={false} className={clsx("@container", isOver && "ring-1 ring-signal")}>
      <div ref={setNodeRef}>
        {/* ---------------------------------------------------------- header */}
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-5 py-4">
          <div className="flex min-w-0 items-center gap-4">
            <TeamLogo abbreviation={team?.abbreviation} name={team?.full_name} size={52} />
            <div className="min-w-0">
              <h2 className="truncate text-[1.25rem] font-semibold leading-tight tracking-[-0.01em] text-foreground">
                {team?.full_name ?? "Loading…"}
              </h2>
              <p className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[13.5px] text-muted">
                <span>
                  Receives {incoming.length} player{incoming.length === 1 ? "" : "s"}
                </span>
                <span aria-hidden>·</span>
                <span
                  className="numeral font-normal text-foreground"
                  title={incomingSalary != null ? undefined : CONTRACT_TOOLTIP}
                >
                  {incoming.length === 0 ? "$0.0M" : incomingSalary != null ? money(incomingSalary) : "salary —"}
                </span>
                {standing && (
                  <>
                    <span aria-hidden className="text-faint">
                      ·
                    </span>
                    <span className="text-faint">
                      {standing.wins}–{standing.losses}
                      {standing.playoff_rank
                        ? ` · ${ordinal(standing.playoff_rank)} ${standing.conference ?? ""}`
                        : ""}
                      {detail?.season ? ` · ${detail.season}` : ""}
                    </span>
                  </>
                )}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {legality && <Badge status={legality.status}>{LEGALITY_SHORT[legality.status]}</Badge>}
            <button
              type="button"
              onClick={onRemove}
              aria-label={`Remove ${team?.full_name ?? "team"} from the trade`}
              title="Remove this team"
              className="grid h-8 w-8 place-items-center rounded-md border border-transparent text-muted transition-colors hover:border-illegal/40 hover:bg-illegal/10 hover:text-illegal"
            >
              <IconClose size={16} />
            </button>
          </div>
        </header>

        {/* --------------------------------------------------- moving assets */}
        {(incoming.length > 0 || outgoing.length > 0) && (
          <div className="border-b border-hairline">
            {incoming.length > 0 && (
              <MovingTable
                direction="in"
                players={incoming}
                teamId={teamId}
                teamAbbr={team?.abbreviation ?? "?"}
                onUndo={(pid) => onMove(pid, originOf[pid])}
                counterpartOf={(pid) => teams.find((t) => t.id === originOf[pid])?.abbreviation ?? "?"}
              />
            )}
            {outgoing.length > 0 && (
              <MovingTable
                direction="out"
                players={outgoing}
                teamId={teamId}
                teamAbbr={team?.abbreviation ?? "?"}
                onUndo={(pid) => onMove(pid, teamId)}
                counterpartOf={(pid) => teams.find((t) => t.id === moves[pid])?.abbreviation ?? "?"}
              />
            )}
          </div>
        )}

        {/* ------------------------------------------------------ ledger row */}
        <dl className="grid grid-cols-2 gap-px border-b border-hairline bg-hairline @md:grid-cols-4">
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

        {/* --------------------------------------------------- roster filters */}
        <div className="flex flex-wrap items-center gap-2 px-4 pb-2 pt-3.5">
          <h3 className="mr-auto flex items-baseline gap-2 text-[13px] font-semibold text-foreground">
            Roster
            <span className="numeral font-normal text-muted">{stayingTotal}</span>
            {all.length > 0 && all.every((p) => p.salary === null) && (
              <span className="hidden text-[12px] font-normal text-faint @xl:inline" title={CONTRACT_TOOLTIP}>
                no contracts on file
              </span>
            )}
          </h3>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter roster…"
            aria-label={`Filter the ${team?.full_name ?? "team"} roster by name`}
            className={clsx(FIELD, "w-full min-w-0 @xs:w-36")}
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

        {/* ------------------------------------------------------ roster table */}
        <div role="table" aria-label={`${team?.full_name ?? "Team"} roster`}>
          <TableHead
            impactTitle="League impact percentile: where each player's Pivot Estimated Impact sits among every player the model scored this season. Hover a figure for the model index and rank."
            trailing="Send"
            actWidth={`${destinations.length * 3.9 + (destinations.length - 1) * 0.25}rem`}
          />
          {/* `relative`, so anything absolutely positioned inside a row (screen-reader text)
              is clipped by this scroller instead of stretching the page. */}
          <div className="relative">
            <div role="rowgroup" className="scroll-thin relative max-h-[26rem] overflow-y-auto">
              {!roster && rosterLoading ? (
                <div className="px-4 pb-4">
                  <SkeletonRows rows={6} height="h-[3rem]" />
                </div>
              ) : staying.length === 0 ? (
                <div className="mx-4 mb-4 rounded-md border border-dashed border-line px-4 py-8 text-center">
                  <p className="text-[15px] font-semibold text-foreground">
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
                  <RosterRow
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
                className="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-panel to-transparent"
              />
            )}
          </div>
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
              <ul className="mt-2.5 divide-y divide-hairline rounded-md border border-hairline">
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

/* ----------------------------------------------------------- player tables */

/**
 * Column template shared by the head and every row, so a column lines up down the panel.
 * The workspace is a container: below ~32rem the contract-years column drops away, below
 * ~28rem age does too — the name, salary, impact and action always remain.
 */
const ROW_GRID =
  "grid items-center gap-x-2.5 px-4 grid-cols-[minmax(0,1fr)_4rem_3.5rem_var(--act-w,2rem)] @md:grid-cols-[minmax(0,1fr)_1.75rem_4rem_3.5rem_var(--act-w,2rem)] @xl:grid-cols-[minmax(0,1fr)_1.75rem_4rem_1.75rem_3.5rem_var(--act-w,2rem)]";

function TableHead({
  impactTitle,
  trailing,
  label,
  actWidth = "2rem",
}: {
  impactTitle?: string;
  trailing?: string;
  label?: string;
  actWidth?: string;
}) {
  return (
    <div
      role="row"
      style={{ ["--act-w" as string]: actWidth }}
      className={clsx(ROW_GRID, "border-y border-hairline py-2 text-[11px] font-medium uppercase tracking-[0.08em] text-faint")}
    >
      <span role="columnheader">{label ?? "Player"}</span>
      <span role="columnheader" className="hidden text-right @md:block">
        Age
      </span>
      <span role="columnheader" className="text-right">
        Salary
      </span>
      <span role="columnheader" className="hidden text-right @xl:block">
        Yrs
      </span>
      <span role="columnheader" className="text-right" title={impactTitle}>
        Impact
      </span>
      <span role="columnheader" className="text-right">
        {trailing}
      </span>
    </div>
  );
}

/** Identity + figures shared by roster and moving rows. */
function PlayerCells({
  player,
  showContract,
  leading,
  onInspect,
  extra,
}: {
  player: RosterPlayer;
  showContract: boolean;
  leading?: React.ReactNode;
  onInspect?: () => void;
  /** Trailing text on the second line ("from CHI"). */
  extra?: React.ReactNode;
}) {
  const identity = (
    <>
      <PlayerPhoto nbaPlayerId={player.nba_player_id} name={player.name} size={38} square />
      <span className="min-w-0">
        <span
          className="block truncate text-[14px] font-semibold leading-tight text-foreground group-hover/id:underline"
          title={player.name}
        >
          {player.name}
        </span>
        <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[12px] leading-tight text-muted">
          <span className="truncate">
            {player.position ?? "—"}
            {player.height_inches ? ` · ${height(player.height_inches)}` : ""}
            {player.archetype && <span className="hidden text-faint @2xl:inline"> · {player.archetype}</span>}
          </span>
          {extra}
        </span>
      </span>
    </>
  );
  return (
    <>
      <div role="cell" className="flex min-w-0 items-center gap-2.5">
        {leading}
        {onInspect ? (
          <button
            type="button"
            onClick={onInspect}
            aria-label={`Open details for ${player.name}`}
            className="group/id flex min-w-0 flex-1 items-center gap-2.5 rounded-md text-left"
          >
            {identity}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2.5">{identity}</div>
        )}
      </div>
      <span role="cell" className="numeral hidden text-right text-[13px] font-normal text-foreground @md:block">
        {player.age ? player.age.toFixed(0) : "—"}
      </span>
      <span
        role="cell"
        className="numeral text-right text-[13px] font-normal text-foreground"
        title={player.salary === null ? CONTRACT_TOOLTIP : undefined}
      >
        {player.salary === null ? (showContract ? "—" : "") : money(player.salary)}
      </span>
      <span role="cell" className="numeral hidden text-right text-[13px] font-normal text-foreground @xl:block">
        {player.contract_years_remaining ?? (showContract ? "—" : "")}
      </span>
      <span role="cell" className="text-right">
        <ImpactInline tei={player.tei} />
      </span>
    </>
  );
}

/** A player in the deal — receiving or sending — with the control that takes him back out. */
function MovingTable({
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
    <div role="table" aria-label={isIn ? "Receiving" : "Sending"}>
      <div role="row" className="flex items-center gap-2 px-5 pb-1.5 pt-3.5 text-[13px] font-semibold text-foreground">
        {/* Direction is carried by the arrow AND the word, never by colour alone. */}
        <span aria-hidden className="text-muted">
          {isIn ? "←" : "→"}
        </span>
        {isIn ? "Receiving" : "Sending"}
        <span className="numeral font-normal text-muted">{players.length}</span>
      </div>
      <TableHead trailing="" actWidth="1.75rem" />
      <div role="rowgroup">
        {players.map((p) => (
          <div
            key={p.player_id}
            role="row"
            className={clsx(ROW_GRID, "lane-in border-b border-hairline py-2 last:border-b-0")}
            style={{ ["--lane-from" as string]: isIn ? "-14px" : "14px", ["--act-w" as string]: "1.75rem" }}
          >
            <PlayerCells
              player={p}
              showContract
              extra={
                <>
                  <span className="shrink-0 whitespace-nowrap text-faint">
                    {isIn ? "from" : "to"} {counterpartOf(p.player_id)}
                  </span>
                  {isIn && (
                    <span className="hidden shrink-0 @xl:inline">
                      <FitChip playerId={p.player_id} teamId={teamId} teamAbbr={teamAbbr} />
                    </span>
                  )}
                </>
              }
            />
            <span role="cell" className="flex items-center justify-end">
              <button
                type="button"
                onClick={() => onUndo(p.player_id)}
                aria-label={`Undo moving ${p.name}`}
                title={`Undo moving ${p.name}`}
                className="grid h-7 w-7 place-items-center rounded-md text-muted transition-colors hover:bg-panel3 hover:text-foreground"
              >
                <IconClose size={15} />
              </button>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function RosterRow({
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

  return (
    <div
      ref={setNodeRef}
      role="row"
      style={{ ["--act-w" as string]: `${destinations.length * 3.9 + (destinations.length - 1) * 0.25}rem` }}
      className={clsx(
        ROW_GRID,
        "group border-b border-hairline py-2 transition-colors last:border-b-0 hover:bg-panel2/70",
        isDragging && "opacity-30",
      )}
    >
      <PlayerCells
        player={player}
        showContract={rosterHasContracts}
        onInspect={onInspect}
        leading={
          <button
            type="button"
            {...listeners}
            {...attributes}
            aria-label={`Drag ${player.name} to another team`}
            className="-ml-2 flex h-9 w-4 shrink-0 cursor-grab touch-none items-center justify-center rounded-md text-faint transition-colors hover:text-foreground"
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
        }
      />
      <span role="cell" className="flex shrink-0 items-center justify-end gap-1">
        {destinations.map((d) => (
          <button
            key={d.id}
            type="button"
            onClick={() => onMove(player.player_id, d.id)}
            aria-label={`Send ${player.name} to ${d.abbr}`}
            title={`Send ${player.name} to the ${d.name}`}
            className="flex items-center gap-1 whitespace-nowrap rounded-md border border-line bg-panel px-2 py-1 text-[12px] font-medium text-muted transition-colors hover:border-foreground hover:bg-foreground hover:text-ink"
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
  columns = "grid-cols-5 sm:grid-cols-6",
  grouped = true,
}: {
  teams: Team[];
  selected: string[];
  onAdd: (id: string) => void;
  columns?: string;
  grouped?: boolean;
}) {
  return (
    <TeamGrid
      teams={teams.length ? teams : undefined}
      grouped={grouped}
      columns={columns}
      size="sm"
      selectedIds={selected}
      disabledIds={selected}
      onSelect={(team) => onAdd(team.id)}
      ariaLabelFor={(team, { disabled }) =>
        disabled ? `${team.full_name} is already in this trade` : `Add ${team.full_name} to the trade`
      }
    />
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
    <section aria-labelledby="start-here-title" className="panel overflow-hidden">
      <div className="grid gap-6 p-5 sm:p-6 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)] lg:gap-10">
        <div className="min-w-0">
          <div className="eyebrow text-faint">Start here</div>
          <h2 id="start-here-title" className="mt-2 text-[1.25rem] font-semibold leading-tight text-foreground">
            Put two teams on the board
          </h2>
          <p className="mt-2 text-[13.5px] leading-relaxed text-muted">
            A deal needs both sides. Pick the franchises to trade between — the rules check runs as
            soon as an asset crosses over.
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Button variant="primary" onClick={onPick}>
              Search for a team
            </Button>
            <ButtonLink href="/team-outlook" variant="secondary">
              Browse team outlooks
            </ButtonLink>
          </div>
        </div>
        <div className="min-w-0">
          <TeamBoard teams={teams} selected={[]} onAdd={onAdd} columns="grid-cols-5 xl:grid-cols-5" />
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
    <div className="flex min-h-[260px] flex-col rounded-md border border-dashed border-line bg-panel/60 p-5">
      <div>
        <div className="eyebrow text-faint">The other side</div>
        <p className="mt-2 text-[1.25rem] font-semibold leading-tight text-foreground">Add a trade partner</p>
        <p className="mt-2 max-w-sm text-[13.5px] leading-relaxed text-muted">
          One side is set. Choose who&apos;s on the other end of the call and the live rules check
          turns on.
        </p>
      </div>
      <div className="mt-5">
        <TeamBoard
          teams={teams}
          selected={selected}
          onAdd={onAdd}
          grouped={false}
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
      className={rail ? "rounded-md border border-hairline px-4 py-4" : "rounded-md border border-hairline px-5 py-4"}
    >
      <div className="eyebrow mb-3.5">How the evaluator works</div>
      <ol className={rail ? "space-y-3.5" : "grid gap-x-8 gap-y-4 sm:grid-cols-3"}>
        {HOW_IT_WORKS.map((item) => (
          <li key={item.step} className="flex gap-3">
            <span className="numeral grid h-6 w-6 shrink-0 place-items-center rounded-full border border-line text-[12px] text-muted">
              {item.step}
            </span>
            <span className="min-w-0">
              <span className="block text-[13.5px] font-semibold text-foreground">{item.title}</span>
              <span className="mt-0.5 block text-[12.5px] leading-snug text-muted">{item.body}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
