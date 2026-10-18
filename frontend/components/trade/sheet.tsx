"use client";

/**
 * The consequence sheet — what a deal does to every team, read left to right:
 *
 *   BEFORE  →  PROPOSED MOVE  →  AFTER
 *
 * One row per team. BEFORE is where the team stands today (its record, its roster count,
 * its cap position — or that contracts are not imported). PROPOSED MOVE is what it gives
 * and gets, every player placed in the league and every incoming player read for fit
 * against this roster. AFTER is what the model says comes out the other side: the verdict
 * (or the refusal), the projected wins change with its 10th–90th percentile band, the
 * roster count after, the legality state and the fit component.
 *
 * The AFTER column of every row is drawn on ONE wins axis, so a two- or three-team deal
 * reads as a comparison rather than as unrelated numbers. Nothing here is computed on the
 * client: every figure is what the evaluation (or team detail) returned, and anything it
 * did not return is said to be missing.
 */

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import Link from "next/link";
import { api } from "@/lib/api";
import { LEGALITY_SHORT, VERDICT_LABEL, fanVerdict, ordinal, payrollDisclosure } from "@/lib/format";
import { teamIdentity } from "@/lib/teamIdentity";
import type { Team, TeamEvaluation, TeamLegality } from "@/lib/types";
import { FitChip } from "@/components/fit";
import { ImpactInline } from "@/components/impact";
import { PlayerPhoto, TeamLogo } from "@/components/media";
import { Badge, Skeleton } from "@/components/ui";
import {
  CONTRACT_TOOLTIP,
  WinsBand,
  hasOutcomeBand,
  sharedWinsAxis,
  signedWins,
} from "@/components/trade/verdict";

/** `GET /teams/{id}` — the slice of it the trade pages read. */
export interface TeamDetailResponse {
  team: Team;
  season: string;
  standing: {
    wins: number;
    losses: number;
    win_pct: number | null;
    conference: string | null;
    playoff_rank: number | null;
  } | null;
  /** Measured season ratings by group ("advanced", "base"), as the backend served them. */
  stats?: Record<string, Record<string, number>>;
}

/** Shared with the builder's workspaces, so a record is fetched once per team. */
export function useTeamDetail(teamId: string) {
  return useQuery({
    queryKey: ["team-detail", teamId],
    queryFn: () => api.get<TeamDetailResponse>(`/teams/${teamId}`),
    staleTime: 300_000,
  });
}

export interface SheetAsset {
  key: string;
  kind: "player" | "pick";
  playerId?: string;
  nbaPlayerId?: number | null;
  /** Player name, or "2027 round 1". */
  label: string;
  /** Position · role, protections — whatever the page knows. */
  detail?: string | null;
  /** The served model index. `undefined` = this asset was not evaluated (nothing shown);
   *  `null` = evaluated and unscored. */
  tei?: number | null;
  /** The other team in this leg of the deal. */
  counterpart: string;
  hypothetical?: boolean;
  /** Pick round, for the token beside a pick. */
  round?: number | null;
  href?: string;
}

export interface SheetSide {
  teamId: string;
  abbreviation: string;
  name: string;
  evaluation?: TeamEvaluation;
  /** The team's legality slice — from the evaluation, or the deal's rules check. */
  legality?: TeamLegality;
  receives: SheetAsset[];
  sends: SheetAsset[];
}

export function ConsequenceSheet({
  sides,
  isRefused,
  active,
  onSelect,
  showIndex = false,
  linkPlayers = false,
}: {
  sides: SheetSide[];
  /** Which evaluations get "No decision score" instead of a verdict — the page's rule. */
  isRefused: (evaluation: TeamEvaluation) => boolean;
  active?: string;
  onSelect?: (teamId: string) => void;
  /** Print each player's raw index beside his percentile. */
  showIndex?: boolean;
  linkPlayers?: boolean;
}) {
  const bands = sides
    .filter((s) => s.evaluation && !isRefused(s.evaluation) && hasOutcomeBand(s.evaluation.uncertainty))
    .map((s) => s.evaluation!.uncertainty);
  const axis = sharedWinsAxis(bands);

  return (
    <div className="panel overflow-hidden">
      <ol className="divide-y divide-hairline">
        {sides.map((side) => (
          <SheetRow
            key={side.teamId}
            side={side}
            axis={axis}
            refused={side.evaluation ? isRefused(side.evaluation) : false}
            active={active === side.teamId}
            onSelect={onSelect}
            showIndex={showIndex}
            linkPlayers={linkPlayers}
          />
        ))}
      </ol>
      <div className="grid gap-x-8 gap-y-1.5 border-t border-hairline bg-court/40 px-5 py-3.5 text-[12px] leading-snug text-faint lg:grid-cols-3">
        <p>
          <span className="font-medium text-muted">Projected wins</span> — change over a full
          82-game season. Bar = 10th–90th percentile of the simulation, tick = median,{" "}
          {bands.length > 0 ? `same ±${axis} scale for every team.` : "drawn only where the simulation ran."}
        </p>
        <p>
          <span className="font-medium text-muted">Fit</span>
          {" "}— 0–100 against the receiving roster&apos;s measured needs; 50 = no change.
          Direction, not size, and not a talent rating.
        </p>
        <p>
          <span className="font-medium text-muted">Percentiles</span> place each player among
          everyone the impact model scored. They are not added up; the wins projection weighs
          the same players by the minutes they would play.
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- row */

function SheetRow({
  side,
  axis,
  refused,
  active,
  onSelect,
  showIndex,
  linkPlayers,
}: {
  side: SheetSide;
  axis: number;
  refused: boolean;
  active: boolean;
  onSelect?: (teamId: string) => void;
  showIndex: boolean;
  linkPlayers: boolean;
}) {
  const identity = teamIdentity(side.abbreviation);
  const headingId = `sheet-team-${side.teamId}`;

  return (
    <li aria-labelledby={headingId} className="relative">
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 pt-5">
        <div className="flex min-w-0 items-center gap-3">
          <TeamLogo abbreviation={side.abbreviation} name={side.name} size={34} decorative />
          <div className="min-w-0">
            <h3 id={headingId} className="truncate font-serif text-[1.6rem] leading-none text-foreground">
              {side.name}
            </h3>
            <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-muted">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: identity.bright }} />
              {side.abbreviation} · receives {countWord(side.receives.length)} · sends{" "}
              {countWord(side.sends.length)}
            </p>
          </div>
        </div>
        {onSelect && (
          <button
            type="button"
            aria-pressed={active}
            onClick={() => onSelect(side.teamId)}
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] transition-colors",
              active
                ? "border-signal/45 bg-signal/10 text-signal"
                : "border-line bg-panel2 text-muted hover:bg-panel3 hover:text-foreground",
            )}
          >
            {side.abbreviation} in full
            <span aria-hidden className="text-[11px]">
              ↓
            </span>
          </button>
        )}
      </header>

      <div className="grid gap-y-6 px-5 pb-6 pt-5 md:grid-cols-2 xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.3fr)_minmax(0,1.15fr)]">
        <Column label="Before" className="md:pr-6">
          <BeforeColumn side={side} />
        </Column>
        <Column label="Proposed move" arrow className="md:border-l md:border-hairline md:pl-6 xl:pr-6">
          <MoveColumn side={side} showIndex={showIndex} linkPlayers={linkPlayers} />
        </Column>
        <Column
          label="After"
          arrow
          tone="signal"
          className="md:col-span-2 md:border-t md:border-hairline md:pt-5 xl:col-span-1 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0"
          aside={
            side.legality ? (
              <Badge status={side.legality.status}>{LEGALITY_SHORT[side.legality.status]}</Badge>
            ) : undefined
          }
        >
          <AfterColumn side={side} axis={axis} refused={refused} />
        </Column>
      </div>
    </li>
  );
}

function countWord(n: number) {
  return n === 0 ? "nothing" : `${n} asset${n === 1 ? "" : "s"}`;
}

/** A column with its label; from the second on, a chalk arrow sits on the dividing line. */
function Column({
  label,
  arrow = false,
  tone,
  aside,
  className,
  children,
}: {
  label: string;
  arrow?: boolean;
  tone?: "signal";
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={clsx("relative min-w-0", className)}>
      {arrow && (
        <span
          aria-hidden
          className="absolute -left-[10px] top-0 hidden h-5 w-5 place-items-center rounded-full border border-line bg-panel text-[11px] leading-none text-muted xl:grid"
        >
          →
        </span>
      )}
      <div className="flex min-h-5 items-center justify-between gap-3">
        <h4 className={clsx("eyebrow", tone === "signal" ? "text-signal" : "text-muted")}>{label}</h4>
        {aside}
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- before */

function Fact({
  label,
  children,
  title,
}: {
  label: string;
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-t border-hairline py-2" title={title}>
      <dt className="text-[12.5px] text-muted">{label}</dt>
      <dd className="ml-auto text-right text-[13px] text-foreground">{children}</dd>
    </div>
  );
}

function BeforeColumn({ side }: { side: SheetSide }) {
  const { data: detail, isError } = useTeamDetail(side.teamId);
  const standing = detail?.standing ?? null;
  const legality = side.legality;
  const payroll = legality
    ? payrollDisclosure(
        legality.payroll_before,
        legality.payroll_known_before,
        legality.payroll_coverage_before,
      )
    : null;
  const apron =
    legality?.apron_status_before ??
    (legality?.apron_status_at_least_before ? `at least ${legality.apron_status_at_least_before}` : null);

  return (
    <div>
      {standing ? (
        <div>
          <p className="stat-serif text-[2.3rem] leading-none text-foreground">
            {standing.wins}–{standing.losses}
          </p>
          <p className="mt-1.5 text-[12px] text-muted">
            {standing.playoff_rank && standing.conference
              ? `${ordinal(standing.playoff_rank)} in the ${standing.conference} · `
              : ""}
            {detail?.season ?? ""} record
          </p>
        </div>
      ) : detail || isError ? (
        <div>
          <p className="stat-serif text-[2.3rem] leading-none text-faint">—</p>
          <p className="mt-1.5 text-[12px] text-muted">Record not available</p>
        </div>
      ) : (
        <Skeleton className="h-[3.35rem] w-32" />
      )}

      <dl className="mt-4">
        <Fact label="Roster">
          {legality ? (
            <span className="numeral">
              {legality.roster_before} <span className="font-normal text-muted">players</span>
            </span>
          ) : (
            "—"
          )}
        </Fact>
        {!payroll || payroll.kind === "unavailable" ? (
          <div className="border-t border-hairline py-2" title={CONTRACT_TOOLTIP}>
            <dt className="text-[12.5px] text-muted">Payroll · apron</dt>
            <dd className="mt-1.5">
              <Badge status="unavailable">contracts not imported</Badge>
            </dd>
          </div>
        ) : (
          <>
            <Fact
              label="Payroll"
              title={payroll.kind === "floor" ? (legality?.payroll_coverage_note ?? undefined) : undefined}
            >
              <span className="numeral">{payroll.value}</span>
              {payroll.kind === "floor" && (
                <span className="block text-[11.5px] text-faint">{payroll.note}</span>
              )}
            </Fact>
            <Fact label="Apron">
              {apron ?? <span className="text-muted">not proven</span>}
            </Fact>
          </>
        )}
      </dl>
    </div>
  );
}

/* ------------------------------------------------------------------ move */

function MoveColumn({
  side,
  showIndex,
  linkPlayers,
}: {
  side: SheetSide;
  showIndex: boolean;
  linkPlayers: boolean;
}) {
  return (
    <div className="space-y-4">
      <AssetList
        direction="in"
        side={side}
        assets={side.receives}
        showIndex={showIndex}
        linkPlayers={linkPlayers}
      />
      <AssetList
        direction="out"
        side={side}
        assets={side.sends}
        showIndex={showIndex}
        linkPlayers={linkPlayers}
      />
    </div>
  );
}

function AssetList({
  direction,
  side,
  assets,
  showIndex,
  linkPlayers,
}: {
  direction: "in" | "out";
  side: SheetSide;
  assets: SheetAsset[];
  showIndex: boolean;
  linkPlayers: boolean;
}) {
  const isIn = direction === "in";
  return (
    <div>
      <p className="flex items-center gap-2 text-[12.5px] font-medium text-foreground">
        {/* Direction is the arrow AND the word, never colour alone. */}
        <span aria-hidden className={isIn ? "text-signal" : "text-muted"}>
          {isIn ? "←" : "→"}
        </span>
        {isIn ? "Receives" : "Sends"}
        <span className="numeral font-normal text-faint">{assets.length}</span>
      </p>
      {assets.length === 0 ? (
        <p className="mt-2 rounded-lg border border-dashed border-hairline px-3 py-2.5 text-[12.5px] text-faint">
          Nothing {isIn ? "arrives" : "leaves"}.
        </p>
      ) : (
        <ul className="mt-1.5">
          {assets.map((asset) => (
            <li
              key={asset.key}
              className="flex items-center gap-3 border-t border-hairline py-2.5 first:border-t-0"
            >
              {asset.kind === "player" ? (
                <PlayerPhoto nbaPlayerId={asset.nbaPlayerId} name={asset.label} size={32} />
              ) : (
                <span
                  aria-hidden
                  className="numeral flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-line text-[11px] text-muted"
                >
                  R{asset.round ?? "?"}
                </span>
              )}
              <div className="min-w-0 flex-1">
                {asset.kind === "player" && linkPlayers && asset.href ? (
                  <Link
                    href={asset.href}
                    className="block truncate text-[14px] font-medium text-foreground transition-colors hover:text-signal"
                  >
                    {asset.label}
                  </Link>
                ) : (
                  <p className="truncate text-[14px] font-medium text-foreground">{asset.label}</p>
                )}
                <p className="mt-0.5 truncate text-[12px] text-faint">
                  <span className="text-muted">
                    {isIn ? "from" : "to"} {asset.counterpart}
                  </span>
                  {asset.detail ? ` · ${asset.detail}` : ""}
                </p>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1">
                {asset.kind === "player" ? (
                  <>
                    {asset.tei !== undefined && <ImpactInline tei={asset.tei} showIndex={showIndex} />}
                    {isIn && asset.playerId && (
                      <FitChip playerId={asset.playerId} teamId={side.teamId} teamAbbr={side.abbreviation} />
                    )}
                  </>
                ) : (
                  asset.hypothetical && <Badge status="unavailable">hypothetical</Badge>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- after */

function AfterColumn({ side, axis, refused }: { side: SheetSide; axis: number; refused: boolean }) {
  const ev = side.evaluation;
  if (!ev) {
    return (
      <div>
        <p className="font-serif text-[1.85rem] leading-[1.05] text-foreground">No decision score</p>
        <p className="mt-1.5 text-[12.5px] text-muted">No evaluation exists for this team on this deal.</p>
      </div>
    );
  }
  const suppressed = ev.decision_status === "suppressed_illegal";
  const verdict = fanVerdict(ev.composite_utility, ev.confidence);
  const u = ev.uncertainty;
  const band = !refused && hasOutcomeBand(u);
  const fit = ev.components?.fit ?? null;
  const failing = (ev.suppression?.failing_rules ?? [])
    .map((r) => r.rule_code)
    .filter((c, i, a) => a.indexOf(c) === i);

  return (
    <div>
      <p className="font-serif text-[1.85rem] leading-[1.05] text-foreground">
        {refused ? "No decision score" : VERDICT_LABEL[verdict]}
      </p>
      {refused ? (
        <p className="mt-1.5 text-[12.5px] leading-snug text-muted">
          {suppressed ? (
            failing.length > 0 ? (
              <>
                Fails{" "}
                <span className="font-medium text-illegal [overflow-wrap:anywhere]">{failing.join(" · ")}</span>
              </>
            ) : (
              "Fails a verified rule"
            )
          ) : (
            "Not enough data to score"
          )}{" "}
          — the deal is not scored.
        </p>
      ) : (
        <p className="mt-1.5 text-[12.5px] text-muted">
          Decision score{" "}
          <span className="numeral text-foreground">
            {ev.composite_utility != null ? ev.composite_utility.toFixed(0) : "—"}
          </span>{" "}
          of 100 · confidence {ev.confidence}
        </p>
      )}

      <div className="mt-5">
        <div className="flex items-baseline justify-between gap-3">
          <span className="eyebrow">Projected wins</span>
          <span className={clsx("stat-serif text-[1.65rem] leading-none", band ? "text-foreground" : "text-faint")}>
            {band ? signedWins(u.median) : "—"}
          </span>
        </div>
        {band ? (
          <WinsBand u={u} axis={axis} ticks className="mt-2.5" />
        ) : (
          <div className="mt-2.5 h-4 rounded-full border border-dashed border-unavail/45" aria-hidden />
        )}
        <p className="mt-1.5 text-[12px] leading-snug text-muted">
          {band ? (
            <>
              <span className="numeral text-foreground">
                {signedWins(u.p10)} to {signedWins(u.p90)}
              </span>{" "}
              wins · {((u.prob_positive ?? 0) * 100).toFixed(0)}% chance it helps
            </>
          ) : refused ? (
            suppressed ? "Not projected — the deal cannot happen." : "Not projected."
          ) : (
            (u?.unavailable ?? "No outcome distribution.")
          )}
        </p>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 border-t border-hairline pt-3">
        <div className="min-w-0">
          <dt className="eyebrow">Roster after</dt>
          <dd className="mt-1.5 text-[13px] text-muted">
            <span className="numeral text-[1.05rem] text-foreground">{ev.legality.roster_after}</span>{" "}
            from {ev.legality.roster_before}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="eyebrow">Fit</dt>
          <dd className="mt-1.5 text-[13px] text-muted">
            {fit != null ? (
              <>
                <span className="numeral text-[1.05rem] text-foreground">{fit.toFixed(0)}</span>{" "}
                <span className="whitespace-nowrap">· 50 = no change</span>
              </>
            ) : (
              <span className="text-unavail">not scored</span>
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}
