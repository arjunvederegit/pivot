"use client";

/**
 * Trade Evaluator — the deal desk.
 *
 * Two or three team workspaces sit side by side with a transaction lane running between
 * them, so the page reads as assets moving rather than a form being filled in. Under the
 * builder, the live rules check and the controls that run the deal. Once evaluated, the
 * result leads with what matters most: every team BEFORE the deal, the PROPOSED MOVE, and
 * AFTER — the verdict, the projected wins change with its band on one shared axis, the
 * roster after, legality and fit — then one team's full analysis at a time.
 *
 * Everything is backend-authoritative: legality comes from POST /trades/validate, impact
 * from POST /trades/evaluate, and anything the backend cannot compute is shown as missing
 * rather than guessed.
 */

import { DndContext, DragOverlay } from "@dnd-kit/core";
import type { DragEndEvent, DragStartEvent } from "@dnd-kit/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "@/lib/api";
import { useHydrated } from "@/lib/hydrated";
import { tradeDetailSchema } from "@/lib/schemas";
import { decodeShareState, encodeShareState } from "@/lib/shareState";
import { LEGALITY_LABEL, LEGALITY_SHORT, formatDate } from "@/lib/format";
import { getFavoriteTeam } from "@/lib/favoriteTeam";
import { teamIdentity } from "@/lib/teamIdentity";
import type {
  EvaluatedPlayer,
  LegalityResponse,
  PickMove,
  PlayerMove,
  RosterPlayer,
  RosterResponse,
  Scenario,
  Team,
  TeamEvaluation,
  TradeDetail,
} from "@/lib/types";
import { TransactionLane } from "@/components/court";
import { PrecedentTab } from "@/components/evaluation-tabs";
import { TeamLogo } from "@/components/media";
import { useToast } from "@/components/toast";
import { DealLedger, RulesCheck, RunIt } from "@/components/trade/desk";
import { PlayerDrawer } from "@/components/trade/drawer";
import { TeamPerspective } from "@/components/trade/perspective";
import { SectionHead } from "@/components/trade/section-head";
import { ConsequenceSheet, type SheetAsset, type SheetSide } from "@/components/trade/sheet";
import { LEGALITY_VISUAL } from "@/components/trade/verdict";
import {
  AddPartnerSlot,
  HowItWorks,
  LaneCell,
  StartHere,
  TeamWorkspaceCell,
} from "@/components/trade/workspace";
import { Badge, Button, PageHeader, Skeleton, SourceRail, Tabs } from "@/components/ui";

const LEDE =
  "Build a two- or three-team deal, watch the rules check run live on the backend, then see what it does to every side — before, the move, and after.";

/** After an evaluation lands, bring its first line into view (instantly under reduced motion). */
function revealResults() {
  window.setTimeout(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById("deal-evaluation")
      ?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, 80);
}

/* --------------------------------------------------------------------- page */

export default function TradeEvaluatorPage() {
  return (
    <Suspense fallback={<BuilderSkeleton />}>
      <TradeEvaluator />
    </Suspense>
  );
}

function BuilderSkeleton() {
  return (
    <div role="status" aria-label="Loading the Trade Evaluator">
      <PageHeader eyebrow="Test the move" title="Trade Evaluator" lede={LEDE} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <Skeleton className="h-[420px] rounded-xl" />
        <div className="flex items-center justify-center py-1 lg:min-h-[27rem] lg:px-1">
          <TransactionLane className="h-11 w-24" />
        </div>
        <Skeleton className="h-[420px] rounded-xl" />
      </div>
    </div>
  );
}

function TradeEvaluator() {
  const hydrated = useHydrated();
  const searchParams = useSearchParams();
  const router = useRouter();
  const toast = useToast();
  const scenarioId = searchParams.get("scenario");
  const loadTradeId = searchParams.get("load");
  const initialTeamId = searchParams.get("team");
  const sharedState = searchParams.get("state");

  const { data: teams } = useQuery({ queryKey: ["teams"], queryFn: () => api.get<Team[]>("/teams") });
  const { data: scenario } = useQuery({
    queryKey: ["scenario", scenarioId],
    queryFn: () => api.get<Scenario>(`/scenarios/${scenarioId}`),
    enabled: !!scenarioId,
  });

  const [teamIds, setTeamIds] = useState<string[]>([]);
  const [moves, setMoves] = useState<Record<string, string>>({});
  const [picks, setPicks] = useState<PickMove[]>([]);
  const [tradeName, setTradeName] = useState("");
  const [activeDrag, setActiveDrag] = useState<{ id: string; name: string } | null>(null);
  const [drawerPlayer, setDrawerPlayer] = useState<{ id: string; name: string } | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [evaluation, setEvaluation] = useState<{
    legality: LegalityResponse;
    evaluations: Record<string, TeamEvaluation>;
  } | null>(null);

  // ---- state seeding (render-phase adjustments, no setState-in-effect) ----
  // Gated on hydration: the shell above these pages hydrates first and warms the shared
  // `["teams"]` query cache, so `teams` can already be present on this page's very first
  // client render. Seeding state from it during that render makes the client tree differ
  // from the server HTML and React throws a hydration error. `useHydrated()` is false for
  // the hydration render, so the seed happens in the commit straight after it instead.
  const [seeded, setSeeded] = useState(false);
  if (hydrated && !seeded && teams) {
    setSeeded(true);
    const shared = sharedState ? decodeShareState(sharedState) : null;
    if (shared) {
      setTeamIds(shared.teamIds.filter((id) => teams.some((t) => t.id === id)).slice(0, 3));
      setMoves(shared.moves);
      setPicks(shared.picks ?? []);
      if (shared.name) setTradeName(shared.name);
    } else if (initialTeamId && teams.some((t) => t.id === initialTeamId)) {
      setTeamIds([initialTeamId]);
    } else {
      const favorite = getFavoriteTeam();
      if (favorite && teams.some((t) => t.id === favorite.id)) setTeamIds([favorite.id]);
    }
  }

  const { data: loadedTrade } = useQuery({
    queryKey: ["trade", loadTradeId],
    queryFn: () => api.get<TradeDetail>(`/trades/${loadTradeId}`, tradeDetailSchema),
    enabled: !!loadTradeId,
  });
  const [appliedTradeId, setAppliedTradeId] = useState<string | null>(null);
  if (loadedTrade && loadedTrade.id !== appliedTradeId) {
    setAppliedTradeId(loadedTrade.id);
    setTeamIds(loadedTrade.teams.map((t) => t.team_id));
    const nextMoves: Record<string, string> = {};
    const nextPicks: PickMove[] = [];
    for (const asset of loadedTrade.assets) {
      if (asset.asset_type === "player" && asset.player_id) nextMoves[asset.player_id] = asset.to_team_id;
      else if (asset.asset_type === "pick" && asset.draft_year && asset.round_number)
        nextPicks.push({
          from_team_id: asset.from_team_id,
          to_team_id: asset.to_team_id,
          draft_year: asset.draft_year,
          round_number: asset.round_number,
          protections: asset.protections,
          is_hypothetical: true,
        });
    }
    setMoves(nextMoves);
    setPicks(nextPicks);
    setTradeName(`${loadedTrade.name} (copy)`);
  }

  const [appliedScenario, setAppliedScenario] = useState<string | null>(null);
  if (scenario && teamIds.length === 0 && appliedScenario !== scenario.id) {
    setAppliedScenario(scenario.id);
    setTeamIds([scenario.focal_team_id]);
  }

  // ---- rosters ----
  const rosters = useQuery({
    queryKey: ["rosters", teamIds],
    queryFn: async () => {
      const result: Record<string, RosterResponse> = {};
      await Promise.all(
        teamIds.map(async (id) => {
          result[id] = await api.get<RosterResponse>(`/teams/${id}/roster`);
        }),
      );
      return result;
    },
    enabled: teamIds.length > 0,
  });

  const originOf = useMemo(() => {
    const map: Record<string, string> = {};
    if (rosters.data)
      for (const [tid, r] of Object.entries(rosters.data))
        for (const p of r.roster) map[p.player_id] = tid;
    return map;
  }, [rosters.data]);

  /** Every rostered player in the builder, so incoming cards need no extra fetch. */
  const playerIndex = useMemo(() => {
    const map: Record<string, RosterPlayer> = {};
    if (rosters.data)
      for (const r of Object.values(rosters.data)) for (const p of r.roster) map[p.player_id] = p;
    return map;
  }, [rosters.data]);

  const playerMoves: PlayerMove[] = useMemo(
    () =>
      Object.entries(moves)
        .filter(([pid, to]) => originOf[pid] && originOf[pid] !== to)
        .map(([pid, to]) => ({ player_id: pid, from_team_id: originOf[pid], to_team_id: to })),
    [moves, originOf],
  );

  // ---- live rules check (backend-authoritative; debounced) ----
  const [validation, setValidation] = useState<LegalityResponse | null>(null);
  const [validating, setValidating] = useState(false);
  useEffect(() => {
    const handle = setTimeout(async () => {
      if (teamIds.length < 2 || playerMoves.length === 0) {
        setValidation(null);
        return;
      }
      setValidating(true);
      try {
        const result = await api.post<LegalityResponse>("/trades/validate", {
          team_ids: teamIds,
          player_moves: playerMoves,
          pick_moves: picks,
        });
        setValidation(result);
      } catch {
        setValidation(null);
      } finally {
        setValidating(false);
      }
    }, 450);
    return () => clearTimeout(handle);
  }, [teamIds, playerMoves, picks]);

  const evaluate = useMutation({
    mutationFn: () =>
      api.post<{ legality: LegalityResponse; evaluations: Record<string, TeamEvaluation> }>(
        "/trades/evaluate",
        {
          team_ids: teamIds,
          player_moves: playerMoves,
          pick_moves: picks,
          scenario_id: scenarioId,
          strategy: scenario?.strategy ?? "custom",
        },
      ),
    onSuccess: setEvaluation,
    onError: (e) => toast("error", `Evaluation failed: ${String(e)}`),
  });

  /**
   * The memo is the artifact a front office actually circulates, and it used to be
   * reachable only after saving a deal — which meant the document existed only for
   * trades someone had already committed to keeping.
   *
   * The response is markdown; it opens in a new tab rather than downloading, because a
   * download the browser cannot preview is a worse default for a document a person is
   * about to read.
   */
  const memo = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/v1/trades/memo", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: tradeName || "Untitled deal",
          team_ids: teamIds,
          focal_team_id: teamIds[0],
          player_moves: playerMoves,
          pick_moves: picks,
          scenario_id: scenarioId,
          strategy: scenario?.strategy ?? "custom",
          format: "html",
        }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.text();
    },
    onSuccess: (html) => {
      const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
    onError: (e) => toast("error", `Could not build the memo: ${String(e)}`),
  });

  const saveTrade = useMutation({
    mutationFn: () =>
      api.post<TradeDetail>("/trades", {
        name: tradeName || "Untitled deal",
        scenario_id: scenarioId,
        team_ids: teamIds,
        player_moves: playerMoves,
        pick_moves: picks,
      }),
    onSuccess: (trade) => {
      toast("success", `Saved "${trade.name}" — opening the full report.`);
      router.push(`/trades/${trade.id}`);
    },
    onError: (e) => toast("error", `Could not save: ${String(e)}`),
  });

  const addTeam = useCallback(
    (id: string) => {
      setTeamIds((current) =>
        id && !current.includes(id) && current.length < 3 ? [...current, id] : current,
      );
    },
    [setTeamIds],
  );

  function removeTeam(id: string) {
    setTeamIds(teamIds.filter((t) => t !== id));
    setMoves((m) =>
      Object.fromEntries(Object.entries(m).filter(([pid, to]) => to !== id && originOf[pid] !== id)),
    );
    setPicks((p) => p.filter((x) => x.from_team_id !== id && x.to_team_id !== id));
    setEvaluation(null);
  }

  // A pick changes the deal, so an evaluation of the deal without it no longer describes
  // what is on the sheet — clear it, exactly as moving a player does.
  function updatePicks(next: React.SetStateAction<PickMove[]>) {
    setEvaluation(null);
    setPicks(next);
  }

  function movePlayer(playerId: string, toTeamId: string) {
    setEvaluation(null);
    setMoves((m) => {
      if (originOf[playerId] === toTeamId) {
        const rest = { ...m };
        delete rest[playerId];
        return rest;
      }
      return { ...m, [playerId]: toTeamId };
    });
  }

  function copyShareLink() {
    const state = encodeShareState({ teamIds, moves, picks, name: tradeName || undefined });
    const url = `${window.location.origin}/trade-evaluator?state=${state}`;
    navigator.clipboard
      .writeText(url)
      .then(() => toast("success", "Share link copied — it reopens this exact deal."))
      .catch(() => toast("error", "Could not copy the link."));
  }

  function onDragStart(event: DragStartEvent) {
    const data = event.active.data.current as { name?: string } | undefined;
    setActiveDrag({ id: String(event.active.id), name: data?.name ?? "" });
  }
  function onDragEnd(event: DragEndEvent) {
    setActiveDrag(null);
    if (event.over) movePlayer(String(event.active.id), String(event.over.id));
  }

  // R1-8 — the candidate generator is hidden pending a constrained rebuild in R5.
  //
  // Measured, it reaches **13.8 %** of counterparties (4 of 29): `evaluations_run`
  // hits the 400-evaluation budget after roughly six teams, in unordered insertion
  // order, and says nothing about the truncation. It has no salary matching, so it
  // proposed Donovan Mitchell *and* James Harden for Jordan Walsh at a counterparty
  // utility of 52.0 — above the 42.0 acceptance floor (QA-10).
  //
  // `services/candidates.py` and `POST /trades/generate` remain; only the entry point
  // is removed, so the R5 rebuild starts from working scaffolding rather than a blank
  // file.

  const loaded = teams ?? [];
  const threeWay = teamIds.length === 3;
  const overall = validation?.overall_status;
  const hasMoves = playerMoves.length > 0;

  if (!hydrated) return <BuilderSkeleton />;

  return (
    <div className="pb-24 lg:pb-0">
      <PageHeader
        eyebrow="Test the move"
        title="Trade Evaluator"
        lede={LEDE}
        actions={
          <TeamPicker
            teams={loaded}
            selected={teamIds}
            onAdd={addTeam}
            open={pickerOpen}
            setOpen={setPickerOpen}
          />
        }
        meta={
          <>
            {validation && <Badge status="info">Cap year {validation.league_year}</Badge>}
            {validating && (
              <span className="flex items-center gap-1.5 text-[12px] font-medium text-signal">
                <span className="pulse-live h-1.5 w-1.5 rounded-full bg-signal" aria-hidden />
                Checking rules
              </span>
            )}
            {!validating && overall && (
              <Badge status={overall}>{LEGALITY_LABEL[overall]}</Badge>
            )}
            {scenario && (
              <Badge status="info">Strategy: {scenario.strategy.replaceAll("_", " ")}</Badge>
            )}
            <span className="text-[12px] text-faint">
              {teamIds.length}/3 teams · {playerMoves.length} player
              {playerMoves.length === 1 ? "" : "s"} moving
              {picks.length > 0 ? ` · ${picks.length} pick${picks.length === 1 ? "" : "s"}` : ""}
            </span>
          </>
        }
      />

      <div className="space-y-12">
        {teamIds.length === 0 ? (
          <StartHere teams={loaded} onAdd={addTeam} onPick={() => setPickerOpen(true)} />
        ) : (
          <DndContext onDragStart={onDragStart} onDragEnd={onDragEnd}>
            <div
              className={
                threeWay
                  ? "grid grid-cols-[minmax(0,1fr)] items-start gap-4 xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] xl:gap-3"
                  : teamIds.length === 1
                    ? "grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] lg:gap-3"
                    : "grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] lg:gap-3"
              }
            >
              {teamIds.map((tid, index) => (
                <TeamWorkspaceCell
                  key={tid}
                  index={index}
                  threeWay={threeWay}
                  teamIds={teamIds}
                  teams={loaded}
                  roster={rosters.data?.[tid]}
                  rosterLoading={rosters.isPending}
                  playerIndex={playerIndex}
                  moves={moves}
                  originOf={originOf}
                  validation={validation}
                  picks={picks}
                  setPicks={updatePicks}
                  onRemove={() => removeTeam(tid)}
                  onMove={movePlayer}
                  onInspect={(p) => setDrawerPlayer({ id: p.player_id, name: p.name })}
                />
              ))}
              {teamIds.length === 1 && (
                <>
                  <LaneCell threeWay={false} active={false} left="var(--signal)" right="var(--signal)" />
                  <AddPartnerSlot teams={loaded} selected={teamIds} onAdd={addTeam} />
                </>
              )}
            </div>
            <DragOverlay dropAnimation={null}>
              {activeDrag && (
                <div className="flex items-center gap-2 rounded-lg border border-signal/60 bg-panel2 px-3 py-2 text-[14px] font-medium text-foreground shadow-[var(--shadow-pop)]">
                  <span aria-hidden className="text-signal">
                    →
                  </span>
                  {activeDrag.name}
                </div>
              )}
            </DragOverlay>
          </DndContext>
        )}

        {teamIds.length < 2 && <HowItWorks />}

        {teamIds.length >= 2 && (
          <section aria-labelledby="desk-heading">
            <SectionHead
              id="desk-heading"
              eyebrow="Rules and actions"
              title="Can this deal happen?"
              aside="The rules engine re-checks the deal every time an asset moves. When it is settled, evaluate it for every side."
            />
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(300px,360px)]">
              <RulesCheck
                className="self-start"
                validation={validation}
                validating={validating}
                hasMoves={hasMoves}
              />
              <div>
                <div className="space-y-4 lg:sticky lg:top-6">
                  <RunIt
                    tradeName={tradeName}
                    setTradeName={setTradeName}
                    canAct={hasMoves}
                    evaluating={evaluate.isPending}
                    saving={saveTrade.isPending}
                    onEvaluate={() => evaluate.mutate(undefined, { onSuccess: revealResults })}
                    onSave={() => saveTrade.mutate()}
                    onCopy={copyShareLink}
                    onMemo={() => memo.mutate()}
                    memoPending={memo.isPending}
                    evaluated={Boolean(evaluation)}
                  />
                  <DealLedger
                    teams={loaded}
                    playerMoves={playerMoves}
                    picks={picks}
                    playerIndex={playerIndex}
                  />
                  <HowItWorks variant="rail" />
                </div>
              </div>
            </div>
          </section>
        )}

        {evaluation && teams && (
          <EvaluationSection
            evaluation={evaluation}
            teams={teams}
            teamIds={teamIds}
            playerMoves={playerMoves}
            pickMoves={picks}
            playerIndex={playerIndex}
          />
        )}
      </div>

      {/* Mobile action rail — the rules state and the primary action stay reachable. */}
      {teamIds.length >= 2 && hasMoves && (
        <div
          data-bottom-bar
          className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-arena px-4 py-3 shadow-[0_-16px_32px_-24px_rgb(0_0_0/0.9)] lg:hidden"
        >
          <div className="flex items-center gap-3">
            <span className="min-w-0 flex-1">
              {validating ? (
                <span className="flex items-center gap-1.5 text-[13px] font-medium text-signal">
                  <span className="pulse-live h-1.5 w-1.5 rounded-full bg-signal" aria-hidden />
                  Checking rules…
                </span>
              ) : overall ? (
                <span className={`flex min-w-0 items-center gap-2 ${LEGALITY_VISUAL[overall].text}`}>
                  <span aria-hidden className="stat-serif text-[1.35rem] leading-none">
                    {LEGALITY_VISUAL[overall].glyph}
                  </span>
                  <span className="truncate text-[14px] font-semibold">{LEGALITY_SHORT[overall]}</span>
                  <span className="hidden truncate text-[12px] font-normal text-muted sm:inline">
                    rules check
                  </span>
                </span>
              ) : (
                <span className="text-[13px] text-muted">No check yet</span>
              )}
            </span>
            {evaluation && (
              <a
                href="#deal-evaluation"
                className="shrink-0 rounded-lg px-2 py-1.5 text-[13px] font-medium text-signal"
              >
                Results <span aria-hidden>↓</span>
              </a>
            )}
            <Button
              variant="primary"
              size="sm"
              disabled={evaluate.isPending}
              onClick={() => evaluate.mutate(undefined, { onSuccess: revealResults })}
            >
              {evaluate.isPending ? "Evaluating…" : "Evaluate"}
            </Button>
          </div>
        </div>
      )}

      {drawerPlayer && (
        <PlayerDrawer
          player={drawerPlayer}
          rosterRow={playerIndex[drawerPlayer.id]}
          fitTeams={teamIds
            .filter((t) => t !== originOf[drawerPlayer.id])
            .map((t) => {
              const other = loaded.find((x) => x.id === t);
              return { id: t, abbr: other?.abbreviation ?? "?", name: other?.full_name ?? "team" };
            })}
          onClose={() => setDrawerPlayer(null)}
        />
      )}
    </div>
  );
}

/* -------------------------------------------------------------- team picker */

function TeamPicker({
  teams,
  selected,
  onAdd,
  open,
  setOpen,
}: {
  teams: Team[];
  selected: string[];
  onAdd: (id: string) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const full = selected.length >= 3;

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    function onPointer(event: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, setOpen]);

  const q = query.trim().toLowerCase();
  const options = teams.filter(
    (t) =>
      !q ||
      t.full_name.toLowerCase().includes(q) ||
      t.abbreviation.toLowerCase().includes(q) ||
      (t.city ?? "").toLowerCase().includes(q),
  );

  return (
    <div ref={boxRef} className="relative">
      <Button
        ref={triggerRef}
        type="button"
        variant="secondary"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={full}
        title={full ? "A trade can involve at most three teams" : "Add a team to the trade"}
        onClick={() => setOpen(!open)}
      >
        <span aria-hidden className="text-[16px] leading-none text-signal">
          +
        </span>
        {full ? "Max 3 teams" : "Add team"}
      </Button>

      {open && (
        <div
          role="dialog"
          aria-label="Add a team to the trade"
          className="fade-in absolute left-0 top-full z-50 mt-2 w-[min(calc(100vw-2rem),27rem)] overflow-hidden rounded-xl border border-line bg-arena shadow-[var(--shadow-pop)] sm:left-auto sm:right-0"
        >
          <div className="border-b border-hairline p-4">
            <label htmlFor="team-picker-filter" className="eyebrow">
              Add a team
            </label>
            <input
              id="team-picker-filter"
              ref={inputRef}
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by city, name or abbreviation…"
              className="mt-2 w-full rounded-lg border border-line bg-panel2 px-3 py-2 text-[14px] text-foreground placeholder:text-faint focus:border-signal/60"
            />
          </div>
          <div className="scroll-thin max-h-[20rem] overflow-y-auto p-3">
            {options.length === 0 ? (
              <p className="px-2 py-6 text-center text-[13.5px] text-muted">
                No team matches “{query}”.
              </p>
            ) : (
              <ul className="grid grid-cols-4 gap-1.5 sm:grid-cols-5">
                {options.map((team) => {
                  const taken = selected.includes(team.id);
                  return (
                    <li key={team.id}>
                      <button
                        type="button"
                        disabled={taken}
                        aria-label={
                          taken
                            ? `${team.full_name} is already in this trade`
                            : `Add ${team.full_name} to the trade`
                        }
                        title={team.full_name}
                        onClick={() => {
                          onAdd(team.id);
                          setOpen(false);
                          setQuery("");
                        }}
                        className="hover-frame flex w-full flex-col items-center gap-1.5 rounded-lg border border-hairline bg-panel2/60 px-1 py-2.5 disabled:pointer-events-none disabled:opacity-30"
                      >
                        <TeamLogo abbreviation={team.abbreviation} size={26} decorative />
                        <span className="numeral text-[12px] leading-none text-muted">
                          {team.abbreviation}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <p className="border-t border-hairline px-4 py-2.5 text-[12px] text-faint">
            {selected.length}/3 selected · press Escape to close
          </p>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------- evaluation report */

function EvaluationSection({
  evaluation,
  teams,
  teamIds,
  playerMoves,
  pickMoves,
  playerIndex,
}: {
  evaluation: { legality: LegalityResponse; evaluations: Record<string, TeamEvaluation> };
  teams: Team[];
  teamIds: string[];
  playerMoves: PlayerMove[];
  pickMoves: PickMove[];
  playerIndex: Record<string, RosterPlayer>;
}) {
  const [activeTeam, setActiveTeam] = useState(teamIds[0]);
  const resolvedTeam = evaluation.evaluations[activeTeam] ? activeTeam : teamIds[0];
  const teamEval = evaluation.evaluations[resolvedTeam];
  const team = teams.find((t) => t.id === resolvedTeam);
  const identity = teamIdentity(team?.abbreviation);
  if (!teamEval) return null;

  const abbr = (id: string | undefined) => teams.find((t) => t.id === id)?.abbreviation ?? "—";
  const teamAbbreviations = Object.fromEntries(
    Object.entries(evaluation.legality.teams).map(([id, t]) => [id, t.abbreviation]),
  );

  const playerAsset = (p: EvaluatedPlayer, direction: "in" | "out"): SheetAsset => {
    const row = playerIndex[p.player_id];
    const move = playerMoves.find((m) => m.player_id === p.player_id);
    return {
      key: p.player_id,
      kind: "player",
      playerId: p.player_id,
      nbaPlayerId: row?.nba_player_id,
      label: p.name,
      detail: [row?.position, row?.archetype].filter(Boolean).join(" · ") || null,
      tei: p.tei,
      counterpart: abbr(direction === "in" ? move?.from_team_id : move?.to_team_id),
    };
  };
  const pickAsset = (p: PickMove, direction: "in" | "out", i: number): SheetAsset => ({
    key: `pick-${direction}-${p.draft_year}-${p.round_number}-${i}`,
    kind: "pick",
    label: `${p.draft_year} round ${p.round_number}`,
    detail: p.protections ?? null,
    counterpart: abbr(direction === "in" ? p.from_team_id : p.to_team_id),
    hypothetical: p.is_hypothetical,
    round: p.round_number,
  });

  const sides: SheetSide[] = teamIds
    .filter((tid) => evaluation.evaluations[tid])
    .map((tid) => {
      const ev = evaluation.evaluations[tid];
      const t = teams.find((x) => x.id === tid);
      return {
        teamId: tid,
        abbreviation: t?.abbreviation ?? ev.legality.abbreviation,
        name: t?.full_name ?? ev.legality.abbreviation,
        evaluation: ev,
        legality: ev.legality,
        receives: [
          ...ev.incoming.map((p) => playerAsset(p, "in")),
          ...pickMoves.filter((p) => p.to_team_id === tid).map((p, i) => pickAsset(p, "in", i)),
        ],
        sends: [
          ...ev.outgoing.map((p) => playerAsset(p, "out")),
          ...pickMoves.filter((p) => p.from_team_id === tid).map((p, i) => pickAsset(p, "out", i)),
        ],
      };
    });

  function selectAndReveal(teamId: string) {
    setActiveTeam(teamId);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById("team-perspective")
      ?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  return (
    <section id="deal-evaluation" aria-labelledby="deal-evaluation-title" className="fade-in">
      <SectionHead
        id="deal-evaluation-title"
        eyebrow={`Deal evaluation · ${formatDate(teamEval.evaluated_at)}`}
        title="What the deal does to each team"
        aside="Where each team stands before the deal, what it gives and gets, and where the model projects it after — every side on the same wins scale."
      />

      <ConsequenceSheet
        sides={sides}
        isRefused={(ev) => ev.decision_status === "suppressed_illegal"}
        active={resolvedTeam}
        onSelect={selectAndReveal}
      />

      <section
        id="team-perspective"
        aria-labelledby="perspective-title"
        className="panel mt-6 overflow-hidden"
        style={{ "--edge": identity.bright } as React.CSSProperties}
      >
        <div className="px-5 pt-5">
          <div className="eyebrow flex items-center gap-2">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: identity.bright }} />
            Team perspective · every number scored from this side
          </div>
          <h3 id="perspective-title" className="mt-2 font-serif text-[2rem] leading-none text-foreground">
            {team?.full_name ?? identity.name}, in full
          </h3>
          <div className="mt-4">
            <Tabs
              ariaLabel="Team perspective"
              active={resolvedTeam}
              onChange={setActiveTeam}
              tabs={sides.map((s) => ({ id: s.teamId, label: s.name }))}
            />
          </div>
        </div>
        <div className="p-5">
          <TeamPerspective
            key={resolvedTeam}
            evaluation={teamEval}
            abbreviation={identity.abbreviation}
            refused={teamEval.decision_status === "suppressed_illegal"}
            teamAbbreviations={teamAbbreviations}
            refusalClosing="Fix the rule failures above and evaluate again."
            precedent={
              <PrecedentTab
                focalTeamId={resolvedTeam}
                teamIds={teamIds}
                playerMoves={playerMoves}
                pickMoves={pickMoves}
              />
            }
          />
          <div className="mt-2">
            <SourceRail
              source="Pivot evaluation model over ingested NBA data"
              retrievedAt={teamEval.evaluated_at}
            />
          </div>
        </div>
      </section>
    </section>
  );
}
