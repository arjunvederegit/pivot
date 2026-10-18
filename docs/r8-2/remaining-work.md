# R8.2 — Remaining work

Split by what is blocking. "Owner" = needs the owner's hands or a decision. Item numbers refer to
`docs/r8-1/remaining-r8-work.md` where they continue one.

## A. Dated, before ordinary users rely on the 2026-27 season

| # | Item | Why / evidence | Who |
| --- | --- | --- | --- |
| A1 | **Refresh your own data**: `make refresh DATASETS=rosters` (and `games`, per R8.1 A2) against the real database | your database still holds only the 2025-26 roster; until then the tools show it **labelled as a fallback** | owner |
| A2 | **Re-check the roster after opening night (20 Oct) and after the cut-down** | the 2026-27 snapshot is `provisional_preseason`: 16–20 per team, camp/two-way/Exhibit-10 not distinguishable | owner / worker |
| A3 | **`make train && make score` for 2026-27 — correct procedure is in the runbook §5** | R8.1's "run it 2–3 Nov" was wrong: both commands follow `current_season`, which is 2025-26 until 3 Nov. A rehearsal also showed `train` drops 2023-24 from the window and retires all five active models **and permanently deletes the previous model's impact estimates** (rollback = restore a backup), and `score` stops until 2026-27 team statistics exist. Until done, impact-based views say so | owner / engineering |
| A4 | `.env`: blank `CURRENT_SEASON` (R8.1 A3). **No longer urgent for rosters** (a pin cannot hold them back), still needed for statistics to roll over | `pinned_behind_calendar` | owner |
| A5 | Run the worker or cron (R8.1 A4) | nothing refreshes by itself | owner |

## B. Remaining R8 infrastructure gaps

| # | Item | Notes |
| --- | --- | --- |
| B1 | **Team needs for the new roster** | `fit` uses needs measured on the previous team. R8.2 labels this and caps confidence; it does not recompute. A roster-aware needs model (needs of *this* roster's skill profile) is R11 work, and the summer is exactly when it matters |
| B2 | 8 players with 2025-26 stats but no identity (R8.1 B2) | unchanged; 24 stat rows still skipped and counted |
| B3 | **Roster type** | NBA.com gives no standard / two-way / Exhibit-10 / camp flag. If Pivot needs the 15-man roster it must come from another source (BALLDONTLIE has not been evaluated for this, and its roster probe is in the R8.1 plan) or a transactions ledger |
| B4 | `team_win_pct` (`EvaluationService`) reads the latest standing; with no 2026-27 games that is 2025-26 | labelled by `season` on the team page; not changed here |
| B5 | R8.1 data-layer polish **C1–C10** (roster ordering tie-break, timestamp normalisation, `games.season_type`, scheduling identity syncs, `observed_through` for stats, 2026-27 preseason dates, `CAP_LEAGUE_YEAR` derivation) | unchanged; none is blocking |
| B6 | Frontend: `is_baseline` / `not_yet_available` states still not rendered as badges; home "league-state" banner wording; `league_season_datasets` has no surface (R8.1 §D) | R8.2 added only the season-context line, the limitation notice and the "totals with TEAM" marker |
| B7 | Worker not run across a real boundary; no load/concurrency test; blocked-provider behaviour not observed live | validation-report §10 |
| B8 | Provisional-roster cadence: the worker refreshes every 6 h; trade/waiver churn during the season is untested live | |

## C. Financial / contract gaps (no work started, by instruction)

**Verified 2026-27 salary data on disk: none.** The `contracts` table has 0 rows, `data/imports/contracts/` is
empty, no contract provider is configured. Cap parameters for 2026-27 *are* present (the legality engine runs).

What R8.2 guarantees meanwhile: salaries are looked up for `CAP_LEAGUE_YEAR` only; an earlier season's salary
is never shown as 2026-27's (tested: a 2025-26 `ContractYear` leaves the 2026-27 salary `None`); a missing salary
is never 0 (`payroll_available: false`, `payroll_known` is a labelled lower bound); `contract` and `assets`
components are excluded from the composite and named; legality stays `conditionally_valid` with its disclosure.

**Fields to populate when verified live contract data exists** (`sync_contracts` writes these from whatever contract provider
is configured — bbref snapshot today, a BALLDONTLIE-backed one later; nothing else needs to change):

| Table.field | Needed for |
| --- | --- |
| `contracts.player_id` (via the canonical identity crosswalk), `contract_type` (`standard`/`two_way`/`exhibit_10`; **NULL if unknown, never defaulted**), `signed_date`, `no_trade_clause`, `source_name`, `source_date`, provenance (`source_provider`, `source_retrieved_at`) | roster-size rule, NTC, provenance |
| `contract_years.contract_id`, **`season = '2026-27'`** (one row per season on the deal), `salary` (integer USD), `guaranteed`, `player_option`, `team_option` | payroll, matching, cap/apron, Cap Center |
| `league_cap_parameters` for `2026-27` (already present) | thresholds |

Done = `make contract-coverage` shows the 2026-27 roster side matched (`players_with_salary / roster_size`).
The next milestone is a **controlled BALLDONTLIE GOAT evaluation** (R8.1 plan: 103 requests / ~26 min, free-key
dry run first) — not an unplanned subscription, and not started here.

## D. Future analytical work (R9–R14), what R8.2 changes for it

* **R9** (persisted skill vectors + next-season backtest) is unaffected and can start; it should key its
  evaluation on *performance season* and *roster season* separately, as `season_basis` now does.
* **R11** (roster needs) now has the incoming roster to reason about — B1 above is its first task.
* **R12** (fit) / **R13** (contracts) / **R14** (strategy): blocked on B1, B3 and §C respectively.
* Not built, per scope: Historical Replay, MyGM, the modular Whiteboard.

## E. Licensing and deployment

* NBA.com ToU §9 still limits statistics to private, non-commercial use; the 60 requests in this milestone were
  private development. Nothing here changes that.
* Deployment: no change; the one new migration-free behaviour is that `data_sync_runs.detail` now carries `season`
  for roster runs. PostgreSQL 16.2 suite passes; SQLite→PostgreSQL migration procedure is still undocumented (R8.1 §E).
