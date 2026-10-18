"use client";

/**
 * Strategy Lab — the decision board over 2–5 saved deals.
 *
 * The backend computes component scores, Pareto dominance and weight-sampling
 * sensitivity once per comparison. On top of that this page adds an exploratory
 * "Adjust priorities" control surface that re-weights the STORED component
 * scores live on the client (same renormalization rule as the backend: missing
 * components drop out and the remaining weights renormalize) — it never re-runs
 * a model, and it is labeled as exploratory next to the saved backend analysis.
 *
 * Reading order, top to bottom: who leads and why → the challengers read against it →
 * every deal's projected-wins band on one axis and where each deal wins → the seven
 * detailed views. The maths lives in `components/strategy/model.ts`.
 *
 * DATA HONESTY: a null component is rendered as "n/a" everywhere and excluded
 * from the score; nothing on this page is estimated to fill a gap. A deal the backend
 * returns no evaluation for is named as left off the board, never silently dropped.
 */

import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { dataHealthSchema, tradeDetailSchema } from "@/lib/schemas";
import { asUtc, count } from "@/lib/format";
import { useHydrated } from "@/lib/hydrated";
import { scenarioOptionLabels } from "@/lib/scenarioLabels";
import type {
  DataHealth,
  RosterPlayer,
  RosterResponse,
  Scenario,
  SourceCard,
  TradeDetail,
  TradeSummary,
} from "@/lib/types";
import { useToast } from "@/components/toast";
import {
  Badge,
  ButtonLink,
  EmptyState,
  ErrorState,
  PageHeader,
  Panel,
  Skeleton,
  SourceRail,
  Tabs,
  UnavailableNotice,
} from "@/components/ui";
import {
  ChallengerList,
  DroppedNotice,
  LeaderPanel,
  NotRankedList,
  OutcomeLedger,
  PriorityRail,
  TradeoffMatrix,
} from "@/components/strategy/board";
import {
  ENGINE_SOURCE,
  MAX_SELECTED,
  MIN_SELECTED,
  explainLeader,
  leaderUnder,
  rankBoard,
  sharedWinsAxis,
  toSliderWeights,
  type Board,
  type BoardAlternative,
  type BoardComparison,
  type BoardPlayer,
} from "@/components/strategy/model";
import { StepHeading, type PlayerLookup } from "@/components/strategy/parts";
import {
  DealCard,
  MethodRail,
  SavedDealPreview,
  SecondSeat,
  SelectionBar,
  type ScoredSide,
} from "@/components/strategy/selection";
import {
  FinancialView,
  FitView,
  ImpactView,
  RiskView,
  SensitivityView,
  SummaryView,
  TimelineView,
} from "@/components/strategy/views";

const TABS = [
  { id: "summary", label: "Summary" },
  { id: "impact", label: "Team impact" },
  { id: "fit", label: "Basketball fit" },
  { id: "financial", label: "Financial" },
  { id: "timeline", label: "Timeline" },
  { id: "risk", label: "Risk" },
  { id: "sensitivity", label: "Sensitivity" },
] as const;

type TabId = (typeof TABS)[number]["id"];

/* ------------------------------------------------------- header badges */

/**
 * Contract coverage, read from the Data Health source card — not from whether a provider
 * is configured. A configured provider with zero rows imported is not "imported".
 */
const CONTRACT_WORDS: Record<SourceCard["status"], string> = {
  fresh: "contracts imported",
  stale: "contracts stale",
  derived: "contracts derived",
  incomplete: "contracts partial",
  unavailable: "contracts not imported",
  failed: "contracts import failed",
};

function shortDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(asUtc(iso));
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/* -------------------------------------------------------------------- page */

export default function StrategyLabPage() {
  const toast = useToast();

  const { data: trades, error: tradesError } = useQuery({
    queryKey: ["trades"],
    queryFn: () => api.get<TradeSummary[]>("/trades"),
  });
  const { data: scenarios } = useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api.get<Scenario[]>("/scenarios"),
  });
  // Built for the whole list at once — see `lib/scenarioLabels.ts`.
  const scenarioLabels = useMemo(() => scenarioOptionLabels(scenarios ?? []), [scenarios]);
  const { data: healthData } = useQuery({
    queryKey: ["data-health"],
    queryFn: () => api.get<DataHealth>("/data-health", dataHealthSchema),
    staleTime: 120_000,
  });
  // The shell warms ["data-health"] before this page hydrates, so on a slow network the cache
  // is already full for the hydration render while the server HTML was rendered without it —
  // the conditional header badges then mismatch. `useHydrated()` is false for exactly that
  // first render, so every read below goes through `health`, never `healthData`.
  const hydrated = useHydrated();
  const health = hydrated ? healthData : undefined;

  const [selected, setSelected] = useState<string[]>([]);
  const [scenarioId, setScenarioId] = useState<string>("");
  const [board, setBoard] = useState<Board | null>(null);
  const [tab, setTab] = useState<TabId>("summary");
  /** Slider positions (0–100) for the exploratory re-weighting surface. */
  const [weights, setWeights] = useState<Record<string, number>>({});

  // Deal contents for every saved deal, so the selection cards can show who moves.
  const detailQueries = useQueries({
    queries: (trades ?? []).map((trade) => ({
      queryKey: ["trade", trade.id],
      queryFn: () => api.get<TradeDetail>(`/trades/${trade.id}`, tradeDetailSchema),
      staleTime: 300_000,
    })),
  });
  const detailById: Record<string, TradeDetail> = {};
  for (const query of detailQueries) if (query.data) detailById[query.data.id] = query.data;

  // Rosters of the involved teams resolve player_id -> headshot id and the impact band.
  const involvedTeamIds = Array.from(
    new Set(Object.values(detailById).flatMap((detail) => detail.teams.map((t) => t.team_id))),
  ).sort();
  const rosterQueries = useQueries({
    queries: involvedTeamIds.map((teamId) => ({
      queryKey: ["roster", teamId],
      queryFn: () => api.get<RosterResponse>(`/teams/${teamId}/roster`),
      staleTime: 300_000,
    })),
  });
  const rosterById: Record<string, RosterPlayer> = {};
  const nbaIdByPlayerId: Record<string, number> = {};
  const nbaIdByName: Record<string, number> = {};
  for (const query of rosterQueries) {
    for (const player of query.data?.roster ?? []) {
      rosterById[player.player_id] = player;
      nbaIdByPlayerId[player.player_id] = player.nba_player_id;
      nbaIdByName[player.name] = player.nba_player_id;
    }
  }

  /** Resolve a player inside a specific deal — by id when served, else by the deal's own asset (no guessing). */
  const lookup: PlayerLookup = (tradeId: string, player: BoardPlayer) => {
    const asset = detailById[tradeId]?.assets.find((a) => a.player_name === player.name);
    const playerId = player.player_id ?? asset?.player_id ?? null;
    const row = playerId ? (rosterById[playerId] ?? null) : null;
    return { nbaPlayerId: row?.nba_player_id ?? nbaIdByName[player.name] ?? null, row };
  };

  const compare = useMutation({
    mutationFn: (vars: { tradeIds: string[]; scenarioId: string }) =>
      api.post<BoardComparison>("/comparisons", {
        name: `Comparison of ${vars.tradeIds.length}`,
        scenario_id: vars.scenarioId || null,
        trade_ids: vars.tradeIds,
      }),
    onSuccess: (data, vars) => {
      setBoard({ comparison: data, sent: vars.tradeIds, scenarioId: vars.scenarioId });
      setWeights(toSliderWeights(data.weights));
      setTab("summary");
    },
    onError: (err) => toast("error", `Comparison failed: ${err.message}`),
  });

  function toggle(id: string) {
    if (selected.includes(id)) {
      setSelected(selected.filter((x) => x !== id));
    } else if (selected.length >= MAX_SELECTED) {
      toast("info", `You can compare at most ${MAX_SELECTED} deals — deselect one first.`);
    } else {
      setSelected([...selected, id]);
    }
  }

  const names: Record<string, string> = Object.fromEntries(
    (trades ?? []).map((trade) => [trade.id, trade.name]),
  );

  /* ------------------------------------------------ whose side is scored */

  const scenarioById = Object.fromEntries((scenarios ?? []).map((s) => [s.id, s]));
  const scenarioTeam = scenarioId ? (scenarioById[scenarioId]?.focal_team?.abbreviation ?? null) : null;

  /** The backend scores a deal from the scenario's team, or — with no scenario — its first team. */
  function sideFor(trade: TradeSummary, team: string | null): ScoredSide {
    if (team) return { abbr: team, missing: !trade.teams.includes(team) };
    const first = detailById[trade.id]?.teams[0]?.abbreviation ?? trade.teams[0] ?? null;
    return { abbr: first, missing: false };
  }

  const selectedTrades = selected
    .map((id) => (trades ?? []).find((t) => t.id === id))
    .filter((t): t is TradeSummary => Boolean(t));
  const missingFromScenario = scenarioTeam
    ? selectedTrades.filter((t) => !t.teams.includes(scenarioTeam)).map((t) => t.name)
    : [];

  /* -------------------------------------------------------- the board */

  const comparison = board?.comparison ?? null;
  const boardTeam = board?.scenarioId
    ? (scenarioById[board.scenarioId]?.focal_team?.abbreviation ??
      Object.values(detailById)
        .flatMap((d) => d.teams)
        .find((t) => t.team_id === comparison?.focal_team_id)?.abbreviation ??
      null)
    : null;

  /** Live re-ranking of the stored component scores under the slider weights. */
  const { ranked, unrankable } = useMemo(() => rankBoard(comparison, weights), [comparison, weights]);
  const leader = ranked[0] ?? null;

  // Only explained against a runner-up: with one ranked deal (the rest illegal, unscored or
  // weighted out) "leads on the weighted blend … the top spot is stable" would describe a lead
  // over nothing.
  const explanation = useMemo(() => {
    if (!comparison || !leader || ranked.length < 2) return null;
    return explainLeader(comparison, leader.alt, weights);
  }, [comparison, leader, ranked.length, weights]);

  const savedWeights = useMemo(
    () => (comparison ? toSliderWeights(comparison.weights) : {}),
    [comparison],
  );
  const savedLeaderAlt = comparison ? leaderUnder(savedWeights, comparison.alternatives) : null;
  const savedLeader = savedLeaderAlt ? { id: savedLeaderAlt.trade_id, name: savedLeaderAlt.name } : null;
  const liveLeader = leader ? { id: leader.alt.trade_id, name: leader.alt.name } : null;

  /** The board answers the selection it was built from; say so once that selection moves on. */
  const boardIsBehind =
    !!board &&
    !compare.isPending &&
    (board.scenarioId !== scenarioId ||
      board.sent.length !== selected.length ||
      board.sent.some((id) => !selected.includes(id)));

  const axis = comparison ? sharedWinsAxis(comparison.alternatives) : 1;
  const returnedIds = new Set(comparison?.alternatives.map((a) => a.trade_id) ?? []);
  const dropped = (board?.sent ?? [])
    .filter((id) => !returnedIds.has(id))
    .map((id) => {
      const trade = (trades ?? []).find((t) => t.id === id);
      return {
        id,
        name: names[id] ?? "an unnamed deal",
        involvesSide: boardTeam && trade ? trade.teams.includes(boardTeam) : null,
      };
    });

  /** The team a returned deal was scored for, for the row labels. */
  function sideOfAlt(alt: BoardAlternative): string | null {
    if (boardTeam) return boardTeam;
    const trade = (trades ?? []).find((t) => t.id === alt.trade_id);
    return detailById[alt.trade_id]?.teams[0]?.abbreviation ?? trade?.teams[0] ?? null;
  }

  const startingLabel = board?.scenarioId
    ? (scenarioLabels[(scenarios ?? []).findIndex((s) => s.id === board.scenarioId)] ?? "saved scenario")
    : "League default weights";

  /* ------------------------------------------------------- provenance */

  const retrievedAt = health?.last_successful_sync ?? null;
  const contractsNote = health?.providers?.contracts?.note ?? null;
  const contractsConfigured = health?.providers?.contracts?.configured ?? false;
  const contractsCard = health?.source_cards?.find((c) => c.key === "contracts") ?? null;
  const nbaCard = health?.source_cards?.find((c) => c.key === "current_nba_data") ?? null;

  const notEnoughDeals = !!trades && trades.length < MIN_SELECTED;
  const sideLabel = scenarioTeam
    ? `Scored from ${scenarioTeam}'s side`
    : "Scored from each deal's first team";

  return (
    <div className="space-y-12 lg:space-y-14">
      <PageHeader
        eyebrow="Strategy Lab"
        title="Decision board"
        lede="Line up two to five saved deals, weight them by what your front office actually cares about, and see which one survives the priorities you set."
        meta={
          <>
            <Badge status="info">{trades ? count(trades.length, "saved deal") : "loading deals"}</Badge>
            {health && (
              <Badge status={contractsCard?.status ?? "unavailable"}>
                {contractsCard ? CONTRACT_WORDS[contractsCard.status] : "contract status unknown"}
              </Badge>
            )}
            {nbaCard && (nbaCard.status === "stale" || nbaCard.status === "failed" || nbaCard.status === "unavailable") && (
              <Badge status={nbaCard.status}>
                NBA data {nbaCard.status}
                {shortDay(nbaCard.last_update) ? ` · ${shortDay(nbaCard.last_update)}` : ""}
              </Badge>
            )}
            {comparison && (
              <Badge status="running">{comparison.alternatives.length} on the board</Badge>
            )}
          </>
        }
        actions={
          <ButtonLink href="/trade-evaluator" variant="secondary" size="sm">
            Build a new deal
          </ButtonLink>
        }
      />

      {/* ------------------------------------------------------------ select */}
      <section aria-labelledby="select-heading">
        <StepHeading
          step="Step 1 of 4"
          title="Choose the deals"
          id="select-heading"
          aside={`Pick ${MIN_SELECTED}–${MAX_SELECTED} saved deals. Each card says who receives what and whose side it will be scored from.`}
        />

        {tradesError ? (
          <ErrorState
            message={`Could not load saved deals: ${(tradesError as Error).message}`}
            action={
              <ButtonLink href="/data-health" size="sm">
                Check Data Health
              </ButtonLink>
            }
          />
        ) : !trades ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px] xl:items-start">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 2xl:grid-cols-3" role="status" aria-label="Loading saved deals">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-[196px] rounded-xl" />
              ))}
            </div>
            <Skeleton className="h-[420px] rounded-xl" />
          </div>
        ) : notEnoughDeals ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px] xl:items-start">
            {trades.length === 0 ? (
              <EmptyState
                className="px-6 py-16"
                title="No saved deals yet"
                hint="The Strategy Lab ranks deals against each other, so it needs at least two saved deals. Build one in the Trade Evaluator, save it, and come back — every deal you save shows up here automatically."
                action={
                  <>
                    <ButtonLink href="/trade-evaluator" variant="primary">
                      Open the Trade Evaluator
                    </ButtonLink>
                    <ButtonLink href="/team-outlook" variant="secondary">
                      Set a team strategy first
                    </ButtonLink>
                  </>
                }
              />
            ) : (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 md:items-start">
                <SavedDealPreview
                  trade={trades[0]}
                  detail={detailById[trades[0].id]}
                  side={sideFor(trades[0], null)}
                  nbaIdByPlayerId={nbaIdByPlayerId}
                />
                <SecondSeat>
                  <EmptyState
                    className="border-0 px-5 py-8"
                    title="One deal isn't a comparison"
                    hint={`You have one saved deal. Save at least ${MIN_SELECTED} to put them side by side; nothing here is ever compared against an invented alternative.`}
                    action={
                      <>
                        <ButtonLink href="/trade-evaluator" variant="primary">
                          Open the Trade Evaluator
                        </ButtonLink>
                        <ButtonLink href="/team-outlook" variant="secondary">
                          Set a team strategy first
                        </ButtonLink>
                      </>
                    }
                  />
                </SecondSeat>
              </div>
            )}
            <MethodRail selectedCount={null} />
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px] xl:items-start">
            <div className="min-w-0">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                {trades.map((trade) => (
                  <DealCard
                    key={trade.id}
                    trade={trade}
                    detail={detailById[trade.id]}
                    selected={selected.includes(trade.id)}
                    side={sideFor(trade, scenarioTeam)}
                    onToggle={() => toggle(trade.id)}
                    nbaIdByPlayerId={nbaIdByPlayerId}
                  />
                ))}
              </div>

              <SelectionBar
                selectedCount={selected.length}
                scenarioId={scenarioId}
                onScenario={setScenarioId}
                scenarioOptions={(scenarios ?? []).map((scenario, index) => ({
                  id: scenario.id,
                  label: scenarioLabels[index],
                }))}
                sideLabel={sideLabel}
                missingNames={missingFromScenario}
                missingTeam={scenarioTeam}
                pending={compare.isPending}
                onCompare={() => compare.mutate({ tradeIds: selected, scenarioId })}
              />
            </div>

            <MethodRail selectedCount={selected.length} />
          </div>
        )}
      </section>

      {/* ----------------------------------------------------------- results */}
      {compare.isPending && !comparison && (
        <section aria-label="Building the comparison">
          <StepHeading step="Step 2 of 4" title="The board" aside="Scoring your deals" />
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <Skeleton className="h-[320px] rounded-xl" />
            <Skeleton className="h-[320px] rounded-xl" />
          </div>
        </section>
      )}

      {board && comparison && (
        <>
          <section aria-labelledby="board-heading">
            <StepHeading
              step="Step 2 of 4"
              title="The board"
              id="board-heading"
              aside={
                <>
                  Ranked live under your priorities ·{" "}
                  <span className="text-foreground">
                    {boardTeam ? `scored from ${boardTeam}'s side` : "each deal scored from its first team"}
                  </span>
                </>
              }
            />

            {boardIsBehind && (
              <p
                className="mb-4 flex gap-2.5 rounded-lg border border-conditional/45 bg-conditional/8 px-4 py-3 text-[13px] leading-snug text-muted"
                role="status"
              >
                <span aria-hidden className="text-conditional">
                  ~
                </span>
                <span className="min-w-0">
                  <span className="font-semibold text-foreground">Out of date. </span>
                  This board still shows the {board.sent.length} deal{board.sent.length === 1 ? "" : "s"} and
                  starting weights you compared; your selection or starting weights have changed since.
                  Compare again to update it.
                </span>
              </p>
            )}

            {dropped.length > 0 && (
              <div className="mb-4">
                <DroppedNotice dropped={dropped} sideAbbr={boardTeam} />
              </div>
            )}

            {comparison.alternatives.length === 0 ? (
              <UnavailableNotice reason="The backend returned none of the selected deals, so there is nothing to rank. Choose deals that involve the scenario's team, or use the league default weights." />
            ) : (
              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px] xl:items-start">
                <div className="min-w-0 space-y-4">
                  {ranked.length === 0 && (
                    <UnavailableNotice reason="None of the selected deals can be ranked — see the list below for why each one was excluded." />
                  )}
                  {leader && (
                    <LeaderPanel
                      alt={leader.alt}
                      score={leader.score}
                      runnerUp={ranked[1] ?? null}
                      explanation={explanation}
                      axis={axis}
                      sideAbbr={sideOfAlt(leader.alt)}
                      firstPlaceShare={comparison.sensitivity.first_place_share?.[leader.alt.trade_id]}
                      nSamples={comparison.sensitivity.n_samples}
                      lookup={lookup}
                      retrievedAt={retrievedAt}
                    />
                  )}
                  {leader && (
                    <ChallengerList
                      challengers={ranked.slice(1)}
                      leader={leader.alt}
                      leaderScore={leader.score}
                      names={names}
                    />
                  )}
                  <NotRankedList unrankable={unrankable} />
                </div>

                {/* sticky lives on the wrapper: `.panel` already sets position:relative */}
                <div className="xl:sticky xl:top-6">
                  <PriorityRail
                    weights={weights}
                    savedWeights={savedWeights}
                    onChange={(key, value) => setWeights({ ...weights, [key]: value })}
                    onReset={() => setWeights(toSliderWeights(comparison.weights))}
                    startingLabel={startingLabel}
                    savedLeader={savedLeader}
                    liveLeader={liveLeader}
                  />
                </div>
              </div>
            )}
          </section>

          {comparison.alternatives.length > 0 && (
            <section aria-labelledby="outcomes-heading">
              <StepHeading
                step="Step 3 of 4"
                title="Outcomes and trade-offs"
                id="outcomes-heading"
                aside="What each deal is projected to do, on one scale — and which components each one wins."
              />
              <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2 2xl:items-start">
                <OutcomeLedger ranked={ranked} unrankable={unrankable} axis={axis} sideFor={sideOfAlt} />
                {ranked.length > 0 && <TradeoffMatrix ranked={ranked} weights={weights} />}
              </div>
            </section>
          )}

          {/* ------------------------------------------------------ tabbed views */}
          {comparison.alternatives.length > 0 && (
            <section aria-labelledby="views-heading">
              <StepHeading
                step="Step 4 of 4"
                title="Read the difference"
                id="views-heading"
                aside="Same comparison, seven angles."
              />
              <Panel padded={false}>
                <div className="px-3 pt-2 sm:px-4">
                  <Tabs
                    tabs={TABS.map((t) => ({ id: t.id, label: t.label }))}
                    active={tab}
                    onChange={(id) => setTab(id as TabId)}
                    ariaLabel="Comparison views"
                  />
                </div>
                <div
                  role="tabpanel"
                  aria-label={`${TABS.find((t) => t.id === tab)?.label} view`}
                  className="p-4 sm:p-6"
                >
                  {tab === "summary" && (
                    <SummaryView comparison={comparison} ranked={ranked} weights={weights} />
                  )}
                  {tab === "impact" && <ImpactView ranked={ranked} axis={axis} lookup={lookup} />}
                  {tab === "fit" && <FitView ranked={ranked} lookup={lookup} />}
                  {tab === "financial" && (
                    <FinancialView
                      ranked={ranked}
                      contractsConfigured={contractsConfigured}
                      contractsNote={contractsNote}
                    />
                  )}
                  {tab === "timeline" && <TimelineView ranked={ranked} />}
                  {tab === "risk" && <RiskView ranked={ranked} axis={axis} />}
                  {tab === "sensitivity" && <SensitivityView comparison={comparison} names={names} />}
                  <SourceRail source={ENGINE_SOURCE} retrievedAt={retrievedAt} />
                </div>
              </Panel>
            </section>
          )}
        </>
      )}
    </div>
  );
}
