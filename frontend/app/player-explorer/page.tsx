"use client";

/**
 * Player Explorer — the scouting library over the imported season directory.
 *
 * One request loads every imported player; search, filters, sorting, league percentile
 * context and the 2–4 player comparison all run client-side. Totals mode and per-game
 * mode stay strictly separated: the two scales are never mixed inside a single view, and
 * the provenance rail says which one you're reading.
 *
 * Layout, top to bottom: the comparison bench (one quiet line until someone is picked,
 * then four shaped slots that open into small multiples), then the library with its query
 * bar. The library has two views over the same filtered, sorted list: cards (the default —
 * portrait, team, position, games and a three-stat line) and the full table, where every
 * cell carries its league-percentile micro-bar. Both read percentiles from the same
 * qualified population, so switching views never changes a number.
 */

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useHydrated } from "@/lib/hydrated";
import {
  ALL_COLUMNS,
  COUNTING_COLUMNS,
  QUALIFY_MIN,
  qualifies,
  statValue,
} from "@/lib/playerStats";
import type { Mode } from "@/lib/playerStats";
import type { SeasonTotalsResponse, Team } from "@/lib/types";
import { PlayerPhoto } from "@/components/media";
import {
  Badge,
  Button,
  ButtonLink,
  EmptyState,
  ErrorState,
  Explainer,
  PageHeader,
  SegmentedControl,
  Skeleton,
  SourceRail,
} from "@/components/ui";
import { ComparisonBench, SlotMarker } from "@/components/players/compare";
import { DirectoryHeader, DirectoryRow } from "@/components/players/directory";
import { LIBRARY_GRID, LibraryCard, LibrarySkeleton } from "@/components/players/library";
import { leaguePopulations, qualifierText } from "@/components/players/stats";

const SEASON = "2025-26";

type PositionGroup = "all" | "Guards" | "Wings" | "Bigs";

/** Cards (the library) or the full table — two views over the same list. */
type View = "library" | "table";

function positionGroup(position: string | null): PositionGroup {
  const p = (position ?? "").toUpperCase();
  if (!p) return "all";
  if (p.includes("C")) return "Bigs";
  if (p.includes("G") && !p.includes("F")) return "Guards";
  return "Wings";
}

const PAGE_SIZE = 60;
const COMPARE_MAX = 4;
const COMPARE_MIN = 2;

const CONTROL =
  "h-9 w-full rounded-lg border border-line bg-panel2 px-3 text-[13.5px] text-foreground placeholder:text-faint transition-colors hover:border-[#3a433f] focus:border-signal/60";

export default function PlayerExplorerPage() {
  return (
    <Suspense fallback={<ExplorerSkeleton />}>
      <PlayerExplorer />
    </Suspense>
  );
}

function PlayerExplorer() {
  const searchParams = useSearchParams();
  const teamParam = searchParams.get("team");
  // `?focus=search` lands the cursor in the name search (the player not-found page links
  // here as its "search" action). Read once on mount by `autoFocus`, never re-applied.
  const focusSearch = searchParams.get("focus") === "search";

  const { data, isLoading, error } = useQuery({
    queryKey: ["season-totals", SEASON],
    queryFn: () => api.get<SeasonTotalsResponse>(`/players/season-totals/${SEASON}`),
    staleTime: 300_000,
  });
  const { data: teams } = useQuery({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    staleTime: 300_000,
  });

  const [search, setSearch] = useState("");
  const [teamAbbr, setTeamAbbr] = useState<string>("all");
  const [position, setPosition] = useState<PositionGroup>("all");
  const [minGames, setMinGames] = useState(0);
  const [mode, setMode] = useState<Mode>("per_game");
  const [sortKey, setSortKey] = useState<string>("PTS");
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [view, setView] = useState<View>("library");

  // Preselect the team filter from ?team=<team_id> once teams are loaded.
  // Gated on hydration: the shell above these pages hydrates first and warms the shared
  // `["teams"]` query cache, so `teams` can already be present on this page's very first
  // client render. Seeding state from it during that render makes the client tree differ
  // from the server HTML and React throws a hydration error. `useHydrated()` is false for
  // the hydration render, so the seed happens in the commit straight after it instead.
  const hydrated = useHydrated();
  const [seededTeamParam, setSeededTeamParam] = useState<string | null>(null);
  if (hydrated && teamParam && teams && seededTeamParam !== teamParam) {
    setSeededTeamParam(teamParam);
    const match = teams.find((t) => t.id === teamParam);
    if (match) setTeamAbbr(match.abbreviation);
  }

  // The bench is observed so the floating tray only appears once it has scrolled away —
  // the two never show the same picks at the same time.
  const [benchNode, setBenchNode] = useState<HTMLElement | null>(null);
  const [benchVisible, setBenchVisible] = useState(true);
  useEffect(() => {
    if (!benchNode || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(([entry]) => setBenchVisible(entry.isIntersecting), {
      rootMargin: "-57px 0px 0px 0px",
    });
    observer.observe(benchNode);
    return () => observer.disconnect();
  }, [benchNode]);

  const players = useMemo(() => data?.players ?? [], [data]);
  const sortColumn = ALL_COLUMNS.find((c) => c.key === sortKey) ?? COUNTING_COLUMNS[0];

  /**
   * The population every percentile on the page is a percentile *of*, per column.
   *
   * Deliberately not every loaded player. A percentile answers "how many of the league
   * is this player above", and a player with two three-point attempts is not evidence
   * about the league — 67 such players sat at exactly 0 % or 100 % from three, pinning
   * both ends of the scale that everyone else was read against. The population is the
   * players whose sample reaches `QUALIFY_MIN` of the column's own denominator, and it
   * ignores the team/position/search filters on purpose: narrowing the *view* must not
   * silently redefine what "league percentile" means.
   */
  const populations = useMemo(() => leaguePopulations(players, ALL_COLUMNS, mode), [players, mode]);

  const belowQualifier = useMemo(
    () => players.filter((p) => !qualifies(p, sortColumn)).length,
    [players, sortColumn],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = players.filter(
      (p) =>
        (teamAbbr === "all" || p.team_abbr === teamAbbr) &&
        (position === "all" || positionGroup(p.position) === position) &&
        (p.gp ?? 0) >= minGames &&
        (q.length === 0 || p.name.toLowerCase().includes(q)),
    );
    rows.sort((a, b) => {
      const av = statValue(a, sortColumn, mode);
      const bv = statValue(b, sortColumn, mode);
      if (av === null && bv === null) return a.name.localeCompare(b.name);
      if (av === null) return 1;
      if (bv === null) return -1;
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return rows;
  }, [players, search, teamAbbr, position, minGames, sortColumn, sortDir, mode]);

  const selectedPlayers = useMemo(
    () =>
      selected
        .map((id) => players.find((p) => p.player_id === id))
        .filter((p): p is NonNullable<typeof p> => Boolean(p)),
    [selected, players],
  );

  function toggleSelected(playerId: string) {
    setSelected((prev) => {
      if (prev.includes(playerId)) return prev.filter((id) => id !== playerId);
      if (prev.length >= COMPARE_MAX) return prev;
      return [...prev, playerId];
    });
  }

  function clearSelection() {
    setSelected([]);
    setCompareOpen(false);
  }

  /**
   * Opens the bench and takes the reader to it. The button that was pressed (the tray's,
   * or the bench's own) disappears as the bench opens, so focus is moved to the bench
   * heading rather than dropping to <body>; the heading's new name ("Comparing N players")
   * is committed first so that is what a screen reader announces.
   */
  function openComparison() {
    flushSync(() => setCompareOpen(true));
    const reduceMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    benchNode?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    benchNode?.querySelector<HTMLElement>("#compare-title")?.focus({ preventScroll: true });
  }

  function setSort(key: string) {
    if (key === sortKey) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  function resetFilters() {
    setSearch("");
    setTeamAbbr("all");
    setPosition("all");
    setMinGames(0);
    setVisibleCount(PAGE_SIZE);
  }

  const modeSuffix = mode === "per_game" ? "/g" : " tot";
  const visible = filtered.slice(0, visibleCount);
  const filtersActive =
    search.trim() !== "" || teamAbbr !== "all" || position !== "all" || minGames > 0;
  const sortedLabel = `${sortColumn.label}${sortColumn.kind === "counting" ? modeSuffix : ""}`;

  if (isLoading) return <ExplorerSkeleton />;

  if (error) {
    return (
      <div className="space-y-5">
        <ExplorerHeader count={null} season={SEASON} />
        <ErrorState message={(error as Error).message} />
      </div>
    );
  }

  if (!data || !data.available) {
    return (
      <div className="space-y-5">
        <ExplorerHeader count={null} season={SEASON} />
        <EmptyState
          title="No season totals imported"
          hint={
            data?.note ??
            "No season-totals file has been imported for this season, so there is nothing to explore yet."
          }
          action={
            <ButtonLink href="/data-health" variant="primary">
              See what&apos;s missing
            </ButtonLink>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-8 pb-24">
      <ExplorerHeader count={data.count} season={data.season} />

      {/* ------------------------------------------------- comparison bench */}
      <ComparisonBench
        players={selectedPlayers}
        league={players}
        open={compareOpen}
        max={COMPARE_MAX}
        min={COMPARE_MIN}
        onOpen={openComparison}
        onClose={() => setCompareOpen(false)}
        onRemove={toggleSelected}
        onClear={clearSelection}
        benchRef={setBenchNode}
      />

      {/* ------------------------------------------------------------ library */}
      <section aria-labelledby="directory-title">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <div className="eyebrow">{data.season} · imported season</div>
            <h2 id="directory-title" className="title-lg mt-2 text-foreground">
              Player library
            </h2>
          </div>
          {/* Display settings — how the list reads, not what is in it. */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex items-center gap-2.5">
              <span className="eyebrow min-w-[2.75rem] sm:min-w-0">Scale</span>
              <SegmentedControl
                ariaLabel="Stat display scale"
                value={mode}
                onChange={(value) => setMode(value as Mode)}
                options={[
                  { value: "per_game", label: "Per game" },
                  { value: "totals", label: "Season totals" },
                ]}
              />
            </div>
            <div className="flex items-center gap-2.5">
              <span className="eyebrow min-w-[2.75rem] sm:min-w-0">View</span>
              <SegmentedControl
                ariaLabel="Player list view"
                value={view}
                onChange={(value) => setView(value as View)}
                options={[
                  { value: "library", label: "Library" },
                  { value: "table", label: "Table" },
                ]}
              />
            </div>
          </div>
        </div>

        {/* query bar */}
        <div className="panel p-4 sm:p-5">
          <div className="grid grid-cols-2 gap-x-3 gap-y-3.5 md:grid-cols-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1.15fr)_minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1fr)]">
            <Field
              label="Search"
              htmlFor="explorer-search"
              className="col-span-2 md:col-span-4 xl:col-span-1"
            >
              <input
                id="explorer-search"
                type="search"
                placeholder="Player name…"
                autoFocus={focusSearch}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setVisibleCount(PAGE_SIZE);
                }}
                className={CONTROL}
              />
            </Field>

            <Field label="Team" htmlFor="explorer-team">
              <select
                id="explorer-team"
                value={teamAbbr}
                onChange={(event) => {
                  setTeamAbbr(event.target.value);
                  setVisibleCount(PAGE_SIZE);
                }}
                className={CONTROL}
              >
                <option value="all">All teams</option>
                {(teams ?? []).map((team) => (
                  <option key={team.id} value={team.abbreviation}>
                    {team.abbreviation} — {team.full_name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Position group" htmlFor="explorer-position">
              <select
                id="explorer-position"
                value={position}
                onChange={(event) => {
                  setPosition(event.target.value as PositionGroup);
                  setVisibleCount(PAGE_SIZE);
                }}
                className={CONTROL}
              >
                <option value="all">All positions</option>
                <option value="Guards">Guards</option>
                <option value="Wings">Wings</option>
                <option value="Bigs">Bigs</option>
              </select>
            </Field>

            <Field label="Minimum games" htmlFor="explorer-min-games">
              <select
                id="explorer-min-games"
                value={String(minGames)}
                onChange={(event) => {
                  setMinGames(Number(event.target.value));
                  setVisibleCount(PAGE_SIZE);
                }}
                className={CONTROL}
              >
                <option value="0">All players</option>
                <option value="5">5+ games</option>
                <option value="15">15+ games</option>
                <option value="30">30+ games</option>
                <option value="58">58+ games (league-leader rule)</option>
              </select>
            </Field>

            <Field label="Sort by" htmlFor="explorer-sort">
              <div className="flex gap-1.5">
                <select
                  id="explorer-sort"
                  value={sortKey}
                  onChange={(event) => {
                    setSortKey(event.target.value);
                    setSortDir("desc");
                  }}
                  className={clsx(CONTROL, "min-w-0 flex-1")}
                >
                  {ALL_COLUMNS.map((column) => (
                    <option key={column.key} value={column.key}>
                      {column.label}
                      {column.kind === "counting" ? modeSuffix : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => setSortDir((d) => (d === "desc" ? "asc" : "desc"))}
                  aria-label={`Sort direction: ${sortDir === "desc" ? "high to low" : "low to high"}`}
                  title={sortDir === "desc" ? "High → low" : "Low → high"}
                  className="h-9 w-9 shrink-0 rounded-lg border border-line bg-panel2 text-sm text-muted transition-colors hover:border-signal/50 hover:text-foreground"
                >
                  <span aria-hidden>{sortDir === "desc" ? "↓" : "↑"}</span>
                </button>
              </div>
            </Field>

          </div>

          {/* readout of the current query, and what the bars mean */}
          <div className="mt-4 space-y-3 border-t border-hairline pt-3.5">
            <p className="min-w-0 text-[12.5px] leading-relaxed text-muted">
              <span className="numeral text-foreground">{filtered.length.toLocaleString()}</span> of{" "}
              {data.count.toLocaleString()} imported players{" · "}
              sorted by <span className="font-semibold text-signal">{sortedLabel}</span>{" "}
              <span className="text-faint">
                ({sortDir === "desc" ? "high → low" : "low → high"})
              </span>
              {" · "}
              {mode === "per_game" ? (
                <>
                  showing <span className="text-foreground">per-game values</span> (raw totals ÷
                  games played)
                </>
              ) : (
                <>
                  showing <span className="text-foreground">raw season totals</span> exactly as
                  imported
                </>
              )}
              {belowQualifier > 0 && (
                <>
                  {" · "}
                  <span className="text-faint">
                    {belowQualifier.toLocaleString()} listed without a percentile on{" "}
                    {sortColumn.label} (under {QUALIFY_MIN}{" "}
                    {sortColumn.kind === "rate" ? "attempts" : "games"})
                  </span>
                </>
              )}
              {filtersActive && (
                <>
                  {" "}
                  <button
                    type="button"
                    onClick={resetFilters}
                    className="ml-1 font-medium text-signal underline underline-offset-2 hover:text-foreground"
                  >
                    Reset filters
                  </button>
                </>
              )}
            </p>
            <BarLegend
              population={populations[sortColumn.key]?.length ?? 0}
              qualifier={qualifierText(sortColumn)}
            />
          </div>
        </div>

        {/* results */}
        <div className="mt-4">
          {filtered.length === 0 ? (
            <EmptyState
              title="No players match those filters"
              hint="Widen the search, clear the team filter, or switch the position group."
              action={
                <Button size="sm" onClick={resetFilters}>
                  Reset filters
                </Button>
              }
            />
          ) : view === "library" ? (
            <ul aria-label="Players" className={clsx(LIBRARY_GRID, "fade-in")}>
              {visible.map((player) => {
                const slot = selected.indexOf(player.player_id);
                return (
                  <LibraryCard
                    key={player.player_id}
                    player={player}
                    mode={mode}
                    sortColumn={sortColumn}
                    populations={populations}
                    slot={slot === -1 ? null : slot}
                    selectionFull={selected.length >= COMPARE_MAX}
                    onToggle={() => toggleSelected(player.player_id)}
                  />
                );
              })}
            </ul>
          ) : (
            <div className="panel fade-in p-3">
              <DirectoryHeader
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={setSort}
                mode={mode}
                populations={populations}
              />
              <ul aria-label="Players" className="space-y-1">
                {visible.map((player, index) => {
                  const slot = selected.indexOf(player.player_id);
                  return (
                    <DirectoryRow
                      key={player.player_id}
                      player={player}
                      rank={index + 1}
                      mode={mode}
                      sortKey={sortKey}
                      populations={populations}
                      slot={slot === -1 ? null : slot}
                      selectionFull={selected.length >= COMPARE_MAX}
                      onToggle={() => toggleSelected(player.player_id)}
                    />
                  );
                })}
              </ul>
            </div>
          )}

          {filtered.length > visibleCount && (
            <div className="mt-5 flex justify-center">
              <Button onClick={() => setVisibleCount((count) => count + PAGE_SIZE * 3)}>
                Show more · {(filtered.length - visibleCount).toLocaleString()} remaining
              </Button>
            </div>
          )}

          <SourceRail
            className="mt-6"
            source={data.source}
            retrievedAt={data.imported_at}
            extra={
              <span>· totals are raw as imported; per-game values are derived (totals ÷ GP)</span>
            }
          />
        </div>
      </section>

      {/* --------------------------------------------------- selection tray */}
      {selected.length > 0 && (
        <div
          inert={benchVisible}
          // Only while it is on screen: globals.css then pads phone scrolling so a
          // focused card is never parked behind the tray.
          data-bottom-bar={benchVisible ? undefined : ""}
          className={clsx(
            "fixed inset-x-0 bottom-0 z-40 border-t border-line bg-ink shadow-[0_-16px_40px_-24px_rgb(0_0_0/0.8)] transition-[transform,opacity] duration-200 lg:left-[var(--sidebar-w)]",
            benchVisible
              ? "pointer-events-none translate-y-full opacity-0"
              : "translate-y-0 opacity-100",
          )}
        >
          <div className="mx-auto flex max-w-[1480px] flex-wrap items-center gap-3 px-4 py-2.5 sm:px-6 lg:px-10">
            <div className="flex min-w-0 items-center gap-2.5" aria-live="polite">
              <span aria-hidden className="flex shrink-0 items-center gap-1.5">
                {selectedPlayers.map((player, index) => (
                  <span key={player.player_id} className="relative">
                    {/* 30, not 28: the no-photo fallback sets its initials at 0.34 × size,
                        and 28 put them under the 10px floor. */}
                    <PlayerPhoto nbaPlayerId={player.nba_player_id} name={player.name} size={30} />
                    <span className="absolute -bottom-0.5 -right-0.5 rounded-full bg-ink p-[2px]">
                      <SlotMarker index={index} size={8} />
                    </span>
                  </span>
                ))}
              </span>
              <span className="min-w-0 truncate text-[12px] text-muted">
                {selected.length} of {COMPARE_MAX} selected
                {selected.length < COMPARE_MIN ? " — pick at least 2 to compare" : ""}
              </span>
            </div>
            <div className="ml-auto flex shrink-0 items-center gap-2">
              <Button size="sm" variant="ghost" onClick={clearSelection}>
                Clear
              </Button>
              <Button
                size="sm"
                variant="signal"
                disabled={selected.length < COMPARE_MIN}
                onClick={openComparison}
              >
                Compare {selected.length}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ header */

function ExplorerHeader({ count, season }: { count: number | null; season: string }) {
  return (
    <PageHeader
      eyebrow="Scouting"
      title="Player Explorer"
      lede="Every player's imported season totals, searchable and sortable, with each stat set against the qualified league. Pick two to four names to put their seasons on the same percentile axes."
      meta={
        <>
          <Badge status="info">{season}</Badge>
          {count !== null && (
            <span className="text-[12px] text-muted">
              <span className="numeral text-foreground">{count.toLocaleString()}</span> players
              loaded
            </span>
          )}
          {/* R7: "league percentiles from this same set" used to sit here and is now
              false — the percentile population is the qualified subset, not everyone
              loaded. The legend under the query bar says what it is, with the live count. */}
        </>
      }
    />
  );
}

function Field({
  label,
  htmlFor,
  children,
  className,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("min-w-0", className)}>
      <label htmlFor={htmlFor} className="eyebrow mb-1.5 block">
        {label}
      </label>
      {children}
    </div>
  );
}

/** What a micro-bar is, drawn with the same marks the cards and rows use. */
function BarLegend({ population, qualifier }: { population: number; qualifier: string }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-muted">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="relative block h-[3px] w-9 overflow-hidden rounded-full bg-panel3">
            <span className="absolute inset-y-0 left-0 w-[70%] rounded-full bg-foreground/45" />
          </span>
          league percentile
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="relative block h-[3px] w-9 overflow-hidden rounded-full bg-panel3">
            <span className="absolute inset-y-0 left-0 w-[70%] rounded-full bg-signal" />
          </span>
          sorted stat
        </span>
        <span>
          <span className="text-unavail">n/a · sample</span> = under {QUALIFY_MIN}
        </span>
      </div>
      <Explainer summary="Who the percentiles are measured against" className="mt-2">
        Each bar is the share of qualified players at or below this value on that stat — qualified
        meaning {QUALIFY_MIN}+ of what the stat divides by: games for a per-game stat, attempts
        for a percentage. On the sorted stat that is {population.toLocaleString()} players with{" "}
        {qualifier}. The population is the whole imported league and ignores the filters above, so
        narrowing the list never changes a percentile. Totals and per-game are ranked separately;
        shooting percentages read the same in both.
      </Explainer>
    </div>
  );
}

/* --------------------------------------------------------------- loading */

function ExplorerSkeleton() {
  return (
    <div className="space-y-8" role="status" aria-label="Loading Player Explorer">
      <div className="space-y-3 border-b border-hairline pb-6">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-12 w-80 max-w-full" />
        <Skeleton className="h-4 w-full max-w-2xl" />
      </div>
      <Skeleton className="h-[74px] rounded-xl" />
      <div className="space-y-4">
        <Skeleton className="h-9 w-56" />
        <Skeleton className="h-40 rounded-xl" />
        <LibrarySkeleton />
      </div>
    </div>
  );
}
