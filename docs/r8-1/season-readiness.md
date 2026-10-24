# R8.1 — Season readiness: lifecycle semantics, freshness policy, the 2026-27 rollover

*Date of validation: 2026-10-18 (preseason; 2026-27 regular season opens 2026-10-20). Code:
`backend/app/canonical/calendar.py`, `provenance.py`, `services/data_provenance.py`,
`ingestion/jobs.py`. Tests: `tests/unit/test_season_readiness.py` (66), plus rollover cases in
`tests/integration/test_r8_cross_tool_consistency.py`.*

## 1. What was wrong

R8 reported every dataset stale (rosters 89 d, standings/stats 55 d). That was a clock applied to
everything. Two different things were being conflated:

* **Rosters** describe *now*. 89 days old is genuinely stale.
* **2025-26 statistics, standings, games** describe a season that ended 2026-04-12. Retrieved
  2026-08-24, they are *complete*; their age says nothing about quality.

And one thing could not be said at all: **2026-27 regular-season statistics do not exist before
2026-10-20.** An empty provider answer for them was a failed run; a missing dataset was "missing".
The "current season" was a hand-edited string (`CURRENT_SEASON=2025-26`) read in ~60 places.

## 2. The model

### Season calendar (`app.canonical.calendar`)
Pure date arithmetic; callers pass `as_of`. Dates come from, in order: the league's published
schedule (`KNOWN_SCHEDULES`, one line per season), the ingested `season_calendar` table, then an
**estimate** (opening night = first Tuesday on/after 18 Oct; 173-day season) that labels itself
`basis: "estimated"`. The estimate reproduces every known opening night (2022, 2024, 2025, 2026).
2026-27 is entered from the NBA's schedule release: **20 Oct 2026 – 11 Apr 2027**. Preseason dates
were not published in the release, so the preseason window is estimated (18 days before opening).

| Concept | Definition | On 2026-10-18 |
| --- | --- | --- |
| `league_season` | the league year (turns over **1 July**) | 2026-27 |
| `phase` | preseason / regular / play-in / playoffs / offseason | preseason |
| `analysis_season` | the season every tool reads: league season once its regular season is **14 days** old, else the last completed one | **2025-26** |
| `analysis_is_baseline` | analysis season is behind the league season → prior-season data is *context* | true |
| `regular_stats_state` | upcoming / in_progress / completed | upcoming |
| `rollover_date` | first day the tools read the league season | 2026-11-03 |

`Settings.current_season` is now a **property**: the pinned `CURRENT_SEASON` env var if set,
otherwise `analysis_season`. A long-running server or worker therefore crosses the rollover with no
restart and no edit. `HISTORY_SEASONS` / `CORPUS_SEASONS` default blank = windows ending at the analysis
season (today: identical to the previous hard-coded values — the analytical batteries are
byte-identical). Explicit values still win. `.env.example` is blank by default; the owner's own
`.env` (not touched) pins `2025-26` if it was copied from the old example — `make season-status`
reports `pinned_behind_calendar` when a pin has fallen behind.

**Why 14 days, not opening night.** Every tool keys roster, statistics and model estimates on one
season. One night of games is not a sample, and the impact estimates for 2026-27 do not exist until
`make train && make score` is run. The grace period is configuration (`SEASON_ROLLOVER_GRACE_DAYS`).

### Dataset lifecycle and freshness
`Freshness` gains two states; the existing four are unchanged.

| State | Meaning |
| --- | --- |
| `fresh` | every row inside the dataset's window |
| `stale` | at least one row older than the window (oldest decides) — **or a completed season retrieved before it settled** |
| `missing` | no rows, and rows were expected |
| `unknown` | rows with no retrieval time |
| **`finalized`** | the season is over and every row was retrieved after it settled (end + 3 days). Not "immutable": a retrieval older than a year earns a note, not a warning |
| **`not_yet_available`** | the games have not been played; no rows is the right answer |

Per-dataset policy (`DATASET_POLICIES`): **rosters** never finalize for the live season (membership
is now; window 24 h; refreshed every 6 h); **standings / player stats / team stats / games** are
time-sensitive in season (24–48 h) and finalize when complete; **injuries** 6 h and never finalize;
**contracts** 30 days (a user-supplied snapshot); **historical stats** finalize season by season.
A past season's roster is history and finalizes.

Every `SourceMeta` now also carries `scope_state`, `status_reason` (one sentence saying *why*),
`cadence`, `observed_through` (date of the newest game, for games — basketball time, not retrieval
time), `is_baseline`, `last_refresh` (job, status, outcome, error, last success) and
`refresh_failing`. A failed refresh is visible **even while the stored rows still read `fresh`**.

### Source failure vs. legitimate empty
`ProviderEmptyError` (a well-formed zero-row answer) is distinct from every failure class. Before it,
an empty NBA.com response was a `ProviderSchemaError`: **retried four times (~22 s of backoff),
counted toward the circuit breaker (three strikes opened it for five minutes), and recorded as a
schema failure.** Now it is raised after the retry loop and never touches the breaker. The jobs
decide whether empty is *expected* from the calendar: before the first game it is a successful run
with `detail.outcome = "expected_empty"`; afterwards, or for any completed season, it is a rejected
snapshot. A **blocked** provider is never read as "expected empty" (tested).

## 3. The 2026-27 rollover, end to end

| Question | Answer (live, 2026-10-18) |
| --- | --- |
| Which season is analysed? | 2025-26, labelled `is_baseline`; league season 2026-27 reported beside it (`/data/freshness.league_season_datasets`) |
| Phase? | preseason |
| Are 2026-27 statistics available? | `not_yet_available` — NBA.com answered "no rows"; no value is substituted, none is zero |
| Preseason vs regular | stored under different `season_type` keys (1,064 preseason rows written live; 0 regular); a preseason refresh cannot overwrite regular rows |
| Roster for the date? | `current_roster(season)` for "now"; **`roster_as_of(date)`** reconstructs from first-seen/last-listed times and says *unknown, not empty* before Pivot's first snapshot |
| Confirmed current or snapshot? | always "observed snapshot" (`basis`, notes), with age |
| Rookies | on the 2026-27 roster with no 2025-26 row → `missing_player_ids`, value `None` (72 rookies in the live data) |
| Traded players | 108 players changed team 2025-26 → 2026-27 in the live data; history keeps both seasons, each under its own season key |
| Prior-season as baseline | labelled in `notes` and `is_baseline`; never presented as 2026-27 |

**Staging.** While the tools read 2025-26, `prepare_league_season` (worker, every 6 h; also in
`sync_all`) keeps the incoming season's rosters current under their own key, with its own job name
(`prepare_league_rosters`) so a roster NBA.com has not published yet cannot mark the dataset the
tools are *reading* as failing. `Settings.sync_season_list` adds the league season to routine
statistics refreshes the day its regular season starts, so the new figures are already stored when
the tools move.

**What must happen before 2026-11-03** (surfaced by `make season-status` /
`GET /data/rollover`, read-only):

1. 2026-27 rosters staged — `make refresh DATASETS=rosters ARGS='--season 2026-27'` (or the worker).
2. 2026-27 regular-season statistics (after 2026-10-20; the worker's daily job does this).
3. **Impact estimates for 2026-27: `make train && make score`.** Manual; the readiness report lists
   it as the one blocker the system cannot clear itself. Until it is done the impact-dependent views
   have no estimates for the new season (they say so; they do not invent any).
4. `CAP_LEAGUE_YEAR` is still a hand edit (it turns over 1 July, not in October). Readiness flags a
   cap year that has fallen behind the league year — tested for 2027-07-15.

## 4. Tests that prove it (all date-injected; none depends on the day the suite runs)

| # | Required case | Test |
| --- | --- | --- |
| 1 | completed historical season | `test_1_a_completed_season_is_finalized…` (+1b partial season is stale, 1c oldest row decides, 1d old verification → note) |
| 2 | active regular season | `test_2_an_active_season_ages_on_the_dataset_window` |
| 3 | preseason data | `test_3_preseason_rows_are_judged_as_preseason`; `TestSeasonTypesStaySeparate` |
| 4 | future season before first game | `test_4_…not_yet_available…` (+4b same answer after opening night = missing) |
| 5 | delayed statistics update | `test_5_a_delayed_update_in_season_goes_stale` |
| 6 | stale current roster | `test_6_a_live_roster_goes_stale_in_the_offseason_too` |
| 7 | historical correction | `test_7_a_corrected_season_is_updated_counted_and_still_finalized` (`rows.changed == 1`) |
| 8 | missing player statistics | `TestMissingIsNotZero` (rookie, upcoming season, preseason ≠ regular) |
| 9 | upstream provider failure | `test_9_a_failed_refresh_is_visible…` (stored rows untouched, `refresh_failing`); blocked ≠ expected-empty |
| 10 | refresh changes status | `test_10_a_successful_refresh_moves_the_roster_from_stale_to_fresh` |

Plus rollover tests: phases across a year, 1 July league-year turnover, opening night ≠ rollover,
grace period configurable, pinned override validated, history window follows the analysis season,
routine refreshes include the new season during the grace period, trade/rookie/roster-as-of, and the
three-tool agreement after the flip (below). **Mutation check:** 11 deliberate regressions (finalization
off, expected-empty as failure, empty inside the retry loop, dedupe off, errors swallowed, neutral-site
fix off, unscoped roster in the harness, first-snapshot guard off, failed refresh hidden, season type
ignored, rollover on opening night) — **all 11 caught** by named tests (two were first missed or
mis-anchored and the tests/anchors were fixed before the final run).

## 5. Limits

* Preseason *dates* for 2026-27 are estimated; the regular-season dates are the league's. Only the
  `PRESEASON` window depends on the estimate.
* Playoff/play-in windows are estimated from the regular-season end (play-in +3 d, playoffs +7 d,
  playoffs end 20 June); nothing reads them except the phase label.
* The NBA Cup / `ist` season type has a window of `None`: NBA.com folds those games into the regular
  season, and Pivot stores no `ist` rows.
* `observed_through` is only known for games. Standings/statistics do not record the date of the last
  game they include; for completed seasons "retrieved after the season settled" is the proxy.
* Preseason *games* are not stored (the `games` table has no season type); preseason **statistics**
  are. Adding a type to `games` is a migration deliberately not made here.
