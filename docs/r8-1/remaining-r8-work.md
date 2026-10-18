# R8.1 — Remaining work and technical debt

Ordered by when it bites. "Owner" = needs the owner's hands or decision; everything else is engineering.

## A. Before the tools roll over to 2026-27 (2026-11-03) — time-boxed

| # | Item | Why | Who |
| --- | --- | --- | --- |
| A1 | **`make train && make score` for 2026-27** (after ~2 weeks of games, i.e. between 2026-11-02 and the rollover) | Impact estimates, archetypes and team needs are keyed by season. The readiness report lists `impact_estimates` as the one blocker the system cannot clear itself. Without it the impact-dependent views say "no estimates for 2026-27" (honestly) after the flip. Candidate for automation (a worker job that trains when the league season has ≥ N games and no estimates exist). | engineering / owner |
| A2 | **Refresh your own data**: `make refresh DATASETS=rosters`, `make refresh DATASETS=rosters ARGS='--season 2026-27'`, `make refresh DATASETS=games` | rosters are 89 days old; 2026-27 rosters must be staged for the flip; `games` repairs 5 neutral-site rows | owner |
| A3 | **Check `backend/.env`** for a pinned `CURRENT_SEASON=2025-26` (the old `.env.example` set it). A pin overrides the calendar and the tools will stay on 2025-26 after 2026-11-03; `make season-status` reports `pinned_behind_calendar`. Blank it to let the calendar drive. | the owner's `.env` was deliberately not touched | owner |
| A4 | Run the worker (or cron `make refresh …`). The rollover assumes someone refreshes: staging, daily statistics for the league season, and rosters every 6 h | nothing refreshes by itself on a laptop | owner |

## B. Before or during R9

| # | Item | Impact | Effort |
| --- | --- | --- | --- |
| B1 | **Roster season ≠ statistics season in the tools.** One `current_season` governs rosters, stats and model estimates, so between 1 July and the rollover the tools show last season's rosters (stale by construction) although 2026-27 rosters are staged and fresh. `SeasonContext` already separates `league_season` from `analysis_season`; the tools do not yet use the split (about a dozen sites). Needed for R11 (roster needs) and R12 (fit) to reason about the *incoming* roster in the summer. Not done because it changes what every tool reads — an analytical-behaviour change R8.1 was told not to make. | **High** for R11–R14 | M |
| B2 | **8 players with 2025-26 statistics have no identity** (Tyler Burton, Lucas Williamson, Adama Bal, Darius Brown II, Kadary Richmond, Lawson Lovering, Andersson Garcia, Toby Okani); their 24 stat rows are skipped and counted. The static package and end-of-season rosters do not list them. Creating identities from stat rows would add them to every percentile population — decide deliberately, and re-run the batteries. | Low today; grows with two-way/10-day churn | S |
| B3 | A persisted skill vector with model provenance, and a rolling next-season backtest harness (R8's roadmap §4) — unchanged, still the right first R9 task | R9 | L |
| B4 | **Contract data: 0 rows; injuries: none.** The "missing" states are honest but the capability is absent. Owner decision (licence Spotrac/BDL contracts vs. build a transactions ledger) gates R13–R14. | **High** for R13–R14 | owner |
| B5 | **NBA data rights.** NBA.com ToU §9 limits statistics to private non-commercial use; nothing in R8.1 changes that. Everything in this report is private development. | blocks any public product | owner + counsel |

## C. Data-layer polish (verified defects, small)

| # | Item | Notes |
| --- | --- | --- |
| C1 | `/teams/{id}/roster` orders by `position` only: order within a position is the database's (SQLite insertion order vs PostgreSQL heap order). Add a tie-breaker (e.g. `player_id`) — changes SQLite output order, so do it deliberately. | postgres-validation P2 |
| C2 | "Last row wins" provenance timestamps in `/players/{id}/stats` and `/players/{id}` (≤ 1 s differences between engines). Pick a defined one (the oldest, to match "the oldest row decides"). | P3 |
| C3 | SQLite returns naive timestamps, PostgreSQL aware ones; many API timestamps serialise without a zone and a browser reads them as local time. Normalise in the response layer. | P4 |
| C4 | `games` has no `season_type`, so preseason games cannot be stored (preseason *statistics* can); no `neutral_site` flag (the home/away slot of such a game is arbitrary but stable). Both need a migration. | |
| C5 | `sync_teams`/`sync_players` are not scheduled by the worker; identity tables have their own 30-day window and the data-health card now names them when stale. Schedule them weekly, or fold into `sync_all` cadence. | |
| C6 | The unexplained 4th-decimal difference in one `comparable-validation` diagnostic between SQLite and PostgreSQL (0.2541 vs 0.254). Likely unordered-query float summation; add `ORDER BY` in the corpus query and re-check. Pass/fail identical. | |
| C7 | `observed_through` exists only for games; standings and statistics do not record the last game they include. Persist it (a `through_date` on the stat rows) to replace the "retrieved after the season settled" proxy. | |
| C8 | The estimated 2026-27 preseason window and the play-in/playoff windows should be replaced with the league's published dates (`KNOWN_SCHEDULES` takes one line). 2027-28 is *estimated* until the NBA publishes it; add it then. | yearly |
| C9 | `CAP_LEAGUE_YEAR` is the one season setting that is still a hand edit (it turns over 1 July). `GET /data/rollover` flags it; consider deriving it with a YAML-exists guard. | |
| C10 | The CSV importer's default season (`2025-26`) is a property of the file; it now accepts an explicit season. A future CSV needs `--season`. | |

## D. Frontend (not touched; deliberately)

* The `is_baseline` and `not_yet_available` states are served but not rendered anywhere. After 2026-10-20
  a user looking at "2025-26" statistics is told only by the small season label.
* League-state banner wording: when only the roster is stale it still says stats and projections
  "reflect <last sync>". Reword to name the stale dataset (`datasets_needing_attention` is in the payload).
* `league_season_datasets` (what exists for 2026-27) has no surface.

## E. Verification gaps

* BALLDONTLIE has never been called (the adapter, the 103-request plan and the new preflight are
  tested only against mocks). See balldontlie-trial-readiness.md §5.
* Failure handling of NBA.com (blocking, throttling) was not observed live; it is fixture-verified.
* Playwright e2e not re-run; no load or concurrency test; PostgreSQL tested at one version, with no
  documented SQLite → PostgreSQL migration procedure.
* The worker was not run across a real boundary (2026-10-20, 2026-11-03); the calendar and jobs are tested
  with injected dates.

## F. Documentation

R8's documents are historical and were not rewritten. Where R8.1 corrects them: the "everything is
stale" reading (executive report; validation §5), "≈130 requests / 35 minutes" for the BALLDONTLIE plan
(now 103 / 26 min), "2,610 tests" (correct only with the optional `.[assistant]` extra installed), and "the
harness reads current rosters" (it read every season's current rows).
