# R8.2 — Validation report

*Date 2026-10-18 (preseason). Code `feat/r8-2-current-season-readiness` from R8.1 `2b0a627`. Everything below
was run in this session; "not verified" says what was not.*

## 1. Baseline (before any change)

`pytest` on the unmodified R8.1 code in the new worktree: **exit 0** (quiet mode, so no count was printed; R8.1 reported 2,717 passed / 19 skipped with the optional extras installed — this venv has none, hence more skips). Frontend baseline was R8.1's 301 tests.

## 2. Final results

| Check | Result |
| --- | --- |
| Backend, SQLite, CI parity (`--cov-fail-under=85`) | **2,730 passed, 57 skipped**, coverage **91.7 %** |
| Backend, **PostgreSQL 16.2** (disposable server, 127.0.0.1:55433, `pgserver` in a scratch venv) | **2,744 passed, 43 skipped** — includes the 14 PostgreSQL-only compat tests (migration chain, constraints, downgrade guard) |
| `ruff`, `mypy` (162 files) | clean |
| Frontend `vitest` / `tsc` / `eslint` / `next build` | **308 passed** (7 new) / clean / clean / build succeeds |
| Playwright e2e (`make e2e`, its own demo DB, ports 8000/3000 were free and released) | **8 passed, 1 skipped** (Ask Pivot *with* the build flag — flag is off by default; the "without the flag, `/assistant` is not found and the nav does not offer it" test **passed**) |
| New tests | 55 backend (`test_roster_season_split.py` 44, `test_roster_refresh_r82.py` 11) + 7 frontend |
| **Mutation checks** | 14 deliberate regressions (roster follows statistics; no visible fallback; stat attributed to the current team; refresh targets the statistics season; needs claimed current; payroll on the wrong roster; confidence cap removed; limitations hidden; movers uncounted; rookie reason lost; evaluator on the wrong roster; last season's salary stood in for 2026-27; player page on the wrong roster; estimate staleness unreported) — **14/14 caught** (one was first missed — the confidence cap was unobservable because the fixture's `assets` was always excluded — and the test was strengthened) |

Tests that changed, deliberately (the three that encoded the old coupling):

| Test | Why |
| --- | --- |
| `test_before_the_flip_nothing_about_2026_27_leaks_into_the_tools` → `…roster_is_already_2026_27_and_the_statistics_are_not` | it asserted B1 itself: that a staged 2026-27 roster is invisible before the flip |
| `test_a_failed_league_season_roster_does_not_mark_the_roster_being_read_as_failing` | now needs `ROSTER_SEASON` pinned apart from the league year to be true; a companion test asserts the new behaviour (a failed 2026-27 refresh *does* mark the roster the tools read) |
| `test_a_second_evaluation_reuses_the_role_reference` | `EvaluationService` now spends one query resolving the roster season (`≤ 1` instead of `0`); the reference itself is still not rebuilt |

## 3. Live ingestion (isolated)

Working database: a **read-only SQLite-backup copy of your `tradelab.db`** made in the session scratch dir
(source size and mtime unchanged — compared before/after), migrated to head. `.env` not opened. **60 NBA.com
requests** in total (two full 30-team roster passes, ~22 s each), no failures, no throttling.

| Run | Result |
| --- | --- |
| Baseline copy, `make season-status` | rosters: 530 players, season **2025-26** (retrieved 2026-07-21, 89 days), `fell_back: true` — i.e. the pre-R8.2 situation, now labelled |
| `refresh rosters` (default season) | targeted **2026-27**; **526 rows, 30/30 teams**; `added 526, confirmed 0, closed 0, duplicates 0, player_on_two_teams 0` |
| `refresh rosters` again | **`added 0, confirmed 526, closed 0`** — idempotent |

R8.1's **526 (2026-27)** reproduces today. R8.1's 530 (2025-26) was **not re-fetched live** here; it is the stored
2026-07-21 snapshot (30/30 teams, 530 rows).

What the 2026-27 snapshot contains (`roster_verification`): team sizes **16 / 18 / 20** (min/median/max);
**108** players on a different team than 2025-26; **83** not on any 2025-26 roster listing; **73** with
`years_experience = 0`; **85** with no 2025-26 base statistics row (rookies, new signings, non-players);
**0** players on two rosters. Status: **complete: true**; `unresolved: []`.
**The provider does not say which roster this is.** 16–20 per team is consistent with 15 standard + two-way
+ camp invitees and Exhibit 10s; NBA.com's roster endpoint carries no field to separate them, so Pivot labels
the snapshot `provisional_preseason` and says so. It is not assumed to equal the post-cut-down 15-man roster.

## 4. Regression — analytics byte-identical

Same code-vs-code design as R8.1: two identical copies of your database (no 2026-27 rosters), one run with the
R8.1 worktree, one with R8.2.

| Battery | R8.1 vs R8.2 (no 2026-27 roster → fallback → 2025-26) | R8.2 on the live 2026-27 rosters |
| --- | --- | --- |
| `comparable-validation` | **byte-identical** (8,619 B) | identical (it does not read rosters) |
| `acquisition-validation` | **byte-identical** (31,143 B) | differs (30,548 B) — expected, it samples current rosters; exit 0 |
| `adversarial-validation` | **byte-identical** (5,209 B) | differs — expected; exit 0, no honesty violation |
| `scripts.r8_consistency` (overview == directory == strategy lab == payroll == player page) | **1,705 checks, 0 disagreements** (same count as R8.1) | **1,613 checks, 0 disagreements** (526 players) |

## 5. API compatibility

396 responses (30 teams × 7 endpoints, 80 players × 2, directory, season totals, impact distribution, 10 two-team
`/trades/evaluate` + `/trades/validate`, freshness, data-health) from R8.1 code and R8.2 code over identical
databases. With the new keys ignored, **the only differences are 62**: the roster `notes` array gaining the explicit
fallback sentence (60), and the wording of the player-stats completeness note (2). Every trade evaluation —
composite, components, confidence — is identical. Additive keys: `roster_season`, `season_context`,
`season_basis`, `stat_attribution`, `missing_reasons`, `needs_basis`, `estimate_season`, `membership_warnings` and three fields
inside `detail.fit`.

## 6. Acceptance workflow — a 2026-27 roster with 2025-26 evidence (live data, API and browser)

On the isolated copy with the live 2026-27 rosters, served by the R8.2 backend (:8042) and frontend (:3042):

| Step | Observed |
| --- | --- |
| `GET /teams/PHI/roster` | `roster_season 2026-27`, `season 2025-26`, 17 players, **Jaylen Brown on PHI** (not on BOS's 17); `provisional_preseason` |
| `GET /teams/PHI/season-stats` | Brown's **28.7 PTS** line is `season 2025-26`, `stat_team_abbreviation BOS`, **`with_current_team: false`**; 2 players missing — `rookie_no_nba_history` and `no_statistics_row_for_this_season` |
| rookie Kingston Flemings (ATL) | `membership_status: member`, no impact estimate, `/stats` → `[]`, listed in `missing_player_ids` — never a zero |
| `GET /teams/PHI/needs` | computed, `needs_basis.matches_current_roster: false`, note naming the 2025-26 team |
| `GET /teams/PHI/payroll` | `league_year 2026-27`, `roster_season 2026-27`, `payroll_available false`, 0 of 17 salaries (no contract data exists) |
| `GET /players/season-totals/2025-26` | Brown: `team_abbr PHI`, `season_team_abbr BOS` |
| `POST /trades/evaluate` PHI↔BOS | scored (52.96 / 48.86), `confidence medium`, `season_basis.limitations = [fit]`; a "move Brown out of BOS" request is **refused (422)** |
| `GET /data/freshness` | rosters `2026-27` fresh; standings/player/team stats/games `2025-26` finalized; contracts `2026-27` missing; **attention: contracts, injuries** |
| Browser — Team Outlook (PHI) | header "2026-27 roster · 2025-26 statistics"; line "2026-27 roster [provisional] · statistics: 2025-26 regular season (latest completed)"; "Limited analysis · 2026-27 roster" notice in Strengths & needs |
| Browser — Player Explorer `?team=PHI` | badges "2025-26 totals", "2026-27 rosters"; 8 of PHI's 15 cards read "totals with BOS/LAL/CHI/POR…" |
| Browser — Player page (Brown) | team Philadelphia 76ers; season-context line; games labelled "2025-26 season" |
| Browser — Trade Evaluator | roster list shows Brown on PHI (17); after Evaluate: the limitation notice, "Input data: 2025-26 season stats, 2026-27 roster snapshot Oct 18, 2026, 4:05 PM, cap year 2026-27"; record labelled "2025-26 record" |
| Browser — Workspace home (PHI chosen) | header "Overview · 2026-27 rosters · 2025-26 statistics"; team panel "**2025-26 record** 45–37", highest-impact players Embiid / LeBron James / Maxey from the 2026-27 roster, "17 rostered players · 15 scored · 2 unscored"; acquisition targets served |
| Browser — Cap Center (PHI) | "Contract ledger · 2026-27", "0 of 17 salaries on file", every salary cell "not on file" ("it is not a zero"), payroll / cap space / apron "withheld — never estimated" |
| Browser — Strategy Lab | renders the three saved deals (needs a fresh tab; see below) |
| Sidebar | "2026-27 rosters · 2025-26 stats · 0d since sync" |

Browser console: 404s for image assets, and one uncaught `getComputedStyle` TypeError whose stack is an anonymous injected script rather than app source; no application errors were otherwise seen.

Browser caveats: in this isolated environment the team-logo asset files are absent, so every logo requests
`/api/v1/assets/teams/XXX` and gets a 404 (hundreds per page). In a **dev** build that storm delayed first paint by
10–40 s on some pages; the production build (used for Home / Cap Center / Strategy Lab) was fast. Strategy Lab hung
on "Loading this view" in the tab that had already served the storm and rendered in a fresh tab — an environment
effect, not observed to depend on the R8.2 changes, but not isolated further. **Your database copy contains two
"E2E comparison baseline" saved deals** (created 2026-10-18 by an earlier end-to-end run against the dev database —
`make purge-fixtures` lists such leftovers); the copy was not modified.

## 6a. A saved deal across the roster flip (found while verifying)

Saved deals are re-evaluated on every read. The trade builder deliberately accepts a player who is on **no** current
roster (it must not block offseason free agents), so after the flip a deal built on 2025-26 rosters can contain a
player who is on none of the 2026-27 ones — Christian Koloko in your "E2E comparison baseline" — and was being scored
as simply available. R8.2 now returns `membership_warnings[]` on the evaluation ("… is on no 2026-27 roster in
Pivot's snapshot … scored as available to acquire; that is not confirmed"), caps a clean evaluation at *medium*, and
the Trade Evaluator shows the sentence. It does not refuse the move. (A player on a *different* team than the deal
says is still refused: 422.)

## 7. Season-boundary tests (injected dates; none depends on the day the suite runs)

| Date | Phase | Roster | Roster basis | Statistics | Baseline? | Regular-season stats |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-10-17 before opening night | preseason | 2026-27 | provisional | 2025-26 | yes | upcoming |
| 2026-10-18 today | preseason | 2026-27 | provisional | 2025-26 | yes | upcoming |
| 2026-10-20 opening night | regular | 2026-27 | regular-season snapshot | 2025-26 | yes | in progress |
| 2026-10-22 first games | regular | 2026-27 | regular-season snapshot | 2025-26 | yes | in progress |
| 2026-11-02 | regular | 2026-27 | regular-season snapshot | 2025-26 | yes | in progress |
| **2026-11-03 rollover** | regular | 2026-27 | regular-season snapshot | **2026-27** | no | in progress |
| 2026-11-20 | regular | 2026-27 | regular-season snapshot | 2026-27 | no | in progress |
| 2027-07-01 | offseason | **2027-28** | offseason snapshot | 2026-27 | yes | — |

API behaviour on 2026-11-04 with no 2026-27 statistics (`TestAfterTheStatisticsRollover`): rosters unchanged,
`/season-stats` returns `stats: {}` with every rostered player in `missing_player_ids` (no 2025-26 line is
relabelled), `projection.estimates_for_input_season: false`, evaluations carry
`limitations[performance]` ("no estimates for 2026-27; reading 2025-26's — run `make train && make score`") and
`fit` is withheld (no 2026-27 needs). A stale `CURRENT_SEASON=2025-26` pin after the rollover leaves
`roster_season_preferred = 2026-27`.

## 8. Edge cases (tests)

Traded player (Team A stats, Team B roster, no duplicate membership); rookie (on the roster, `rookie_no_nba_history`,
no estimate, no stats); returning player (one identity, 2024-25/2025-26 history kept); a player on two current
rosters → `conflict`, `team: null`, counted by the verification report; no 2026-27 snapshot → visible fallback,
`provisional`; empty database → not a fallback; historical roster lookups and `roster_as_of` still work; empty
provider answer → `SnapshotRejectedError`, rows survive, run attributed to 2026-27; provider outage → rows survive;
unknown player listed by NBA.com → identity created from the roster row; second refresh → `added 0`.

## 9. Intentional output changes

Only the additive fields in §5, the sentence on fallback rosters, the completeness-note wording, the
"Current record" label (now "2025-26 record"), and the roster **membership itself** on any database that holds a
2026-27 snapshot (that is the milestone). No coefficient, weight, threshold or model artifact changed.

## 10. Not verified

* The **worker across real 2026-10-20 / 2026-11-03** boundaries (dates are injected; the scheduler was not run).
* **Blocked/throttled NBA.com** behaviour live (fixture-verified only, as in R8.1).
* Your **real database and `.env`**: neither was modified or read (`.env`); the live run used a copy. Whether your
  `.env` pins `CURRENT_SEASON` is therefore unknown — R8.2 makes the pin harmless for rosters.
* PostgreSQL with a **copy of your real data** (R8.1 did this; R8.2 ran the suite and the new tests on PostgreSQL only).
* `make train && make score` for 2026-27 on real data (see the runbook: a rehearsal on a copy found hazards).
