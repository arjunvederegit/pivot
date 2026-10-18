# R8.1 — Regression report

*Final numbers, 2026-10-18, branch `feat/r8-1-operational-validation`. Baseline = R8 `5cada84`.*

## 1. Backend

| Check | Baseline (R8) | R8.1 |
| --- | --- | --- |
| `pytest -q --cov=app --cov-fail-under=85` (SQLite) | 2,610 passed, 5 skipped, 92.07 % | **2,717 passed, 19 skipped, 92.69 %** — the 14 extra skips are the PostgreSQL-only tests, skipped unless `PIVOT_TEST_POSTGRES_URL` is set; the 5 are the same environmental skips |
| same suite on **PostgreSQL 16.2** (`PIVOT_TEST_DB_URL`) | not run | **2,731 passed, 5 skipped** (14 PG compat tests included) |
| `ruff check app tests scripts` | clean | clean |
| `mypy app` | clean (159 files) | clean (161 files) |
| `alembic upgrade head && alembic check` | clean | clean on SQLite (empty DB and a copy of the real DB) **and PostgreSQL** |
| new tests | 2,615 collected | **2,736 collected (+121 named below, +5 parametrised CLI-reachability cases for the new commands)**: `test_season_readiness.py` 66, `test_nba_api_canonical.py` 19, `test_postgres_compat.py` 14, 5 rollover tests in the cross-tool suite, 16 in the BALLDONTLIE harness suite, 1 label-length test; the data-health fixture was updated (below) |
| coverage of new/changed modules | — | `calendar` 96 %, `provenance` 98 %, `rosters` 100 %, `data_provenance` 98 %, `data_health` 99 %, `nba_api/canonical` 96 %, `ingestion/jobs` 86 %, `balldontlie/evaluation` 63 % → 75 % |

**Mutation check:** 11 deliberate regressions, one at a time, each caught by a named test (list in
season-readiness.md §4). Two first escaped (a harness mutation with no test, and a bad anchor) and were
fixed; the final run caught all 11.

## 2. Analytical behaviour — unchanged

All against **copies of the real database** (71 MB, 5,121 players, 530 rostered), R8 code vs R8.1 code:

| Comparison | Result |
| --- | --- |
| `comparable-validation` | **byte-identical** (8,619 B) |
| `acquisition-validation` | **byte-identical** (31,143 B) |
| `adversarial-validation` | **byte-identical** (5,209 B) |
| 423 API responses, R8 vs R8.1 (`scripts/r8_1_api_snapshot.py compare`, `data_status` ignored) | **0 differences**, 0 non-200 |
| same, R8.1 on **PostgreSQL** vs R8 on SQLite (`--unordered`) | values equal; only provenance-timestamp selection (≤ 1 s) and list order within a position differ — see postgres-validation.md |
| `data_health` latency | 20 ms → 27 ms (+7 ms: the lifecycle assessment adds ~20 small aggregate queries; the query-budget tests pass) |

No model, threshold, coefficient or analytical output moved. (`current_season`, `HISTORY_SEASONS` and
`CORPUS_SEASONS` now derive from the calendar; on 2026-10-18 they evaluate to exactly the previous
hard-coded values, which is why the batteries are identical.)

## 3. Intentional output changes

Everything else is identical; these are deliberate, with a reason:

| Change | Reason | Before → after (real data, a team season-stats response) |
| --- | --- | --- |
| `data_status.*.freshness` for completed-season statistics, standings, games, team stats | a 55-day-old completed season is not stale | `stale` → **`finalized`** |
| `data_status.*` gains `scope_state`, `status_reason`, `cadence`, `is_baseline`, `observed_through`, `last_refresh`, `refresh_failing`; `notes` gains the baseline note | say *why*, and label prior-season data | `scope_state: null` → `completed`; `status_reason` → "2025-26 is complete and every row was retrieved after it settled"; `is_baseline` → `true`; note "2025-26 is the most recent completed season, shown as context while the league is in 2026-27 (preseason); it is not 2026-27 performance" |
| roster `data_status` | unchanged verdict, now explained | `stale` → `stale`, reason "the oldest row is 89 days old; this dataset is considered stale after 24 hours" |
| `GET /data/freshness` | additive: `season` block, `league_season_datasets`, `injuries`, `historical_stats`; `attention` now lists datasets that *need* attention (stale/failing/unknown/missing-and-expected), not every non-fresh one | |
| `GET /data-health` card `current_nba_data` | judged by lifecycle; stays inside the frontend's status vocabulary (`fresh/stale/incomplete/unavailable`); gains `season`, `datasets`, `datasets_needing_attention`; `action` names the datasets to refresh | real data: `stale` → `stale` (the roster **is** 89 days old), but `stale_tables` is now `[rosters, teams, players]` instead of every table |
| `tables[*].stale` for rosters/standings/stats/games | lifecycle-aware | `true` → `false` for completed-season tables; gains `lifecycle`, `lifecycle_reason` |
| `teams`/`players` identity tables | their own 30-day window (they are identity, not a daily feed) | |
| `games` rows for 5 neutral-site games | a team and a score were being lost | repaired on the next `refresh games` (live-ingestion-report L1) |
| `model_versions.algorithm` column | PostgreSQL could not store the labels | `VARCHAR(50)` → `VARCHAR(200)` (migration `c5e2b7d91f34`; SQLite unaffected) |
| `.env.example`: `CURRENT_SEASON` and `HISTORY_SEASONS` blank | derive from the calendar | the owner's own `.env` was not touched; a pinned value keeps working |
| tests: `conftest` pins `CURRENT_SEASON=2025-26`; 3 data-health fixtures gained a live roster row | the suite's fixtures are 2025-26 data and must not change meaning when the calendar crosses 2026-10-27; staleness now comes from the live roster, not a completed season | |

## 4. Cross-tool consistency (Phase 6)

| Check | Result |
| --- | --- |
| `scripts/r8_consistency` on the real-data copy, R8 code | 1,705 / 0 disagreements |
| …R8.1 code | **1,705 / 0** |
| …R8.1 code on the **freshly live-ingested** isolated DB (NBA.com, 2026-10-18) | **1,705 / 0** |
| …R8.1 code on the real data **loaded into PostgreSQL** | **1,705 / 0** |
| after the season flip (new integration tests, `CURRENT_SEASON=2026-27`): Team Overview, Player directory and Strategy Lab list the same people for both teams | ✔ |
| a traded player has one current team on every page and keeps last season's line | ✔ |
| 2026-27 team page: `stats == {}`, every rostered player in `missing_player_ids`, `data_status` = `not_yet_available`/`upcoming` — no zero, no carry-over | ✔ |
| a rookie is a member with no history | ✔ |
| before the flip, a staged 2026-27 roster is invisible to every tool | ✔ |
| failed refresh leaves downstream records consistent; provenance says the refresh failed | ✔ (`test_9…`) |
| BALLDONTLIE harness reads season-scoped rosters (it did not) | ✔ (found and fixed) |

Cap Lab (payroll) and the CBA resolver are covered by the same three-way roster check in the existing
script (`overview == payroll`) — but the real database has **no contract data**, so that comparison
ran only where payroll rows exist (none); it is exercised by the fixture suites, not by real data.
Trade Evaluation and Ask Pivot data interfaces read through `EvaluationService`/`current_roster_clause`
and were exercised by the existing suites (all green) and the API snapshot; they were not given
bespoke rollover scenarios.

## 5. Frontend

| Check | Result |
| --- | --- |
| `git diff 5cada84 -- frontend` | **empty — no frontend change** |
| `vitest --run` | 20 files, **301 passed** (identical to baseline) |
| `tsc --noEmit` | clean |
| `next build` | succeeds, all 16 routes |
| Live check, isolated stack (backend :8041 on a copy of the real DB, `next dev` :3041) | Workspace, Data Health and Team Outlook render; header reads "2025-26 season", the side badge reads **"Data aging · 54d since sync"** and Data Health reports `current_nba_data: stale` because the **roster** is 89 days old — correct, and no longer implied for completed statistics. 33 console 404s are team-logo/player-photo requests: the worktree has no copy of the gitignored `nbalogos/`/`nbaplayerimages/` folders (environmental, not code). Playwright e2e was **not** re-run |

No frontend change was necessary: the new states are served inside the existing card vocabulary, and the
zod schemas are `passthrough`. One wording issue remains for a later UI pass: when only the roster is
stale, the banner still says "stats and projections … reflect <last sync>". (remaining-r8-work.md)

## 6. Not tested

* Playwright end-to-end; Ask Pivot with its flag on; load/concurrency; the worker scheduler against a
  real clock across 2026-10-20 / 2026-11-03 (calendar and jobs are tested with injected dates, not by
  waiting for the date).
* Trade Evaluation and Cap Lab under a rollover scenario beyond the shared-roster/consistency checks.
