# R8.2 — Activation checkpoint report

*2026-10-18. Branch `feat/r8-2-activation-checkpoint` (from verified R8.2 `5bc31f8`; code commit `ecbc912`). Nothing merged to
`main`. Every number below was produced in this session; "not verified" says what was not.*

## A. Activation status — **Engineering and data validated; owner activation of the running app pending**

| Layer | State |
| --- | --- |
| **Code readiness** | Ready. R8.1 → R8.2 → checkpoint chain verified (`2b0a627` → `5bc31f8` → `ecbc912`); the R8.2 code reads your *unmigrated* (`a9d4c6e18b53`) database. |
| **Data readiness** | **Done.** The one authorized roster-only refresh was applied to your real `tradelab.db` (details B). |
| **Running-app readiness** | **Not activated, and I did not claim it.** The shared checkout `/Users/arjunvedere/pivot-work/pivot` is on `feat/r8-canonical-data-foundation` (`5cada84`, pre-R8.1), and its one running server (`:8010`) reads a *different*, disposable QA database — nothing is serving `tradelab.db` right now. To use the refreshed rosters the app must be started from R8.2 code (exact commands in G). Under R8 code the new data is inert (below). |

Git baseline: R8.1 `2b0a627` ← R8.2 `5bc31f8` (pushed, `origin` equal) ← `ecbc912`. Worktrees: shared checkout (R8), `pivot-r8-1-worktree`,
`pivot-r8-2-worktree` (clean before this session). **No `.env` exists** in the shared checkout or backend, so nothing pins `CURRENT_SEASON`
(the earlier worry about a stale pin is moot), and `DATABASE_URL` defaults to `backend/tradelab.db`. Your servers were not touched.

## B. Database results (real development DB: `/Users/arjunvedere/pivot-work/pivot/backend/tradelab.db`)

How it was identified: the default `sqlite:///./tradelab.db` with no override; local file; real NBA.com-sourced data (5,121 players, 573 stat
rows/season, `nba_api` provenance, synced 2026-08-24); no process held it open (`lsof`: 0 handles; the running `:8010` server uses a QA copy).

**Backup:** `/Users/arjunvedere/pivot-backups/tradelab-pre-r82-activation-2026-10-18.db` (+ `.meta.json`), made with the SQLite backup API
(read-only source), 71,131,136 B, sha256 `2c1f63ec…`, outside every repo. Verified: `quick_check ok`, identical row counts **and** a logical
hash over every row of all 37 tables, source mtime/size unchanged, restored copy opened and read by R8.2 code.
**Rollback drill (on a scratch copy):** overwrite the file with the backup → sha256 equals the recorded value; rosters back to 2025-26 only.

| | Before | After |
| --- | --- | --- |
| Roster seasons | 2025-26 only (530 rows) | 2025-26 (530, **byte-identical**) + **2026-27 (526 rows)** |
| Teams with a 2026-27 roster | — | **30 / 30**, sizes 16 / 18 / 20 (min/median/max) |
| Players | 5,121 (5,121 distinct NBA ids) | 5,194 (+73 new identities, 5,194 distinct) |
| Players on two current rosters / duplicate ids | 0 / 0 | **0 / 0** |
| Existing players' bio fields modified | — | **0** (`--roster-only`) |
| `player_team_history` | 530 | 1,056 |
| `data_sync_runs` | 41 | 43 (two `sync_rosters` runs) |
| Roster freshness | `fallback`/stale (89 days; 2025-26) | **fresh**, 0 h, `provisional_preseason`, `complete: true`, `unresolved: []` |
| Schema revision | `a9d4c6e18b53` | unchanged (no migration run) |
| `PRAGMA quick_check` | ok | ok |

**Only four tables changed** (`rosters`, `player_team_history`, `players` — new rows only — and `data_sync_runs`). The other **33 of 37 are
byte-identical**, including every protected one: `model_versions`, `player_impact_estimates`, `player_archetypes`, `team_needs`, `contracts` (0),
`contract_years` (0), `player_season_stats`, `team_season_stats`, `standings`, `games`, `trade_*`, `scenarios`, `draft_picks`, `media_assets`.
Idempotency: second live run `added 0 / confirmed 526 / closed 0`.
Observed counts today equal this morning's (526, 108 changed team, 83 new to the roster listing, 73 rookies by experience); no cuts have
happened yet. NBA.com gives **no roster-type field**, so camp / two-way / Exhibit-10 members are not distinguishable — hence `provisional`.

**A finding that changed the procedure.** The stock refresh also rewrites the bio fields of players Pivot already knows. On your data it modified
448 players (experience, 108 weights, 86 heights, 16 positions, 6 birth dates). Isolating the effect column by column on the acquisition battery: position and experience change nothing, heights move 72 output lines (teams answered unchanged), but
**six newly filled birth dates alone moved it materially (teams answered 29 → 19, 606 diff lines)**.
That is an analytical change, not a roster change, so the activation used a new **`--roster-only`** mode (`update_existing_bio=False`, committed
and tested): memberships and brand-new identities only. Cost: known players' `years_experience` stays last season's (so "rookie by experience"
counts include some second-year players); refresh bio fields later, deliberately, with the batteries re-run.

**Rehearsal (copy of the real DB, unmigrated) and rejected inputs.** Live refresh ×2 as above. Fed the rehearsal copy: an empty answer, 3 teams, half the
league, unknown team ids → all `SnapshotRejectedError` ("previous snapshot kept"); rows with no ids → `KeyError` (also rejected, rolled back).
Current rows stayed 526 in every case; failed runs are recorded.

**Compatibility with the code you actually run (R8, `5cada84`):** its API outputs over the pre/post databases are identical for every team, player,
player-stats, trade-evaluation, season-totals and freshness response (396 compared, volatile timestamps ignored); the only differences are the
player list (73 new active players shift pagination) and the data-health run list. Its three batteries are byte-identical pre/post. So the new rows are inert
under R8 code, and R8.2 reads them as the 2026-27 roster.

## C. Cross-tool validation (actual results)

Environment: R8.2 backend + production frontend build on isolated ports (8043/3043) over the roster-only rehearsal copy, with **your logo/image
directories mounted read-only** (so no 404s); Chromium via Playwright (+ Browser pane earlier). **0 page errors, 0 HTTP ≥ 400** in the smoke run.

| Tool | Result |
| --- | --- |
| Workspace home (PHI selected) | "Overview · 2026-27 rosters · 2025-26 statistics"; "2025-26 record"; "17 rostered players" ✔ |
| Team Outlook | roster/statistics context line, "provisional", "Limited analysis · 2026-27 roster" on needs ✔ |
| Player Explorer `?team=PHI` | "2025-26 totals" + "2026-27 rosters" badges; "totals with BOS/LAL/…" markers ✔ |
| Player pages | mover (Randle → Brooklyn, line "with MIN"), rookie (Kingston Flemings, Atlanta, no stat line invented), **unlisted** (Bruce Brown: "no current team on file", `membership_status: unlisted`) ✔ |
| Trade Evaluator | full flow on live rosters: limitation notice; "Input data: 2025-26 season stats, 2026-27 roster snapshot…, cap year 2026-27" ✔ |
| Strategy Lab | renders 3 saved deals; compare 2 deals works; the deal containing a player on no current roster now shows **"Unverified roster membership: Christian Koloko — scored as if the move were possible"** ✔ |
| Cap Center | "0 of 17 salaries on file", every cell "not on file", no payroll/cap/apron number ✔ |
| Ask Pivot | flag off by default: `/assistant` is 404, nav does not offer it ✔ (e2e) |

Required examples (API, same copy): **stayer** Amen Thompson (HOU→HOU, history 2023-26 intact); **mover** Julius Randle (line attributed to MIN, `with_current_team: false`);
**rookie** (`rookie_no_nba_history`, absent not zero); **no verified membership** Bruce Brown (a trade that moves him is scored, with `membership_warnings` on **both** teams and
confidence *low* — it is not presented as verified); **high turnover** LAL (11 of 19 players new vs 2025-26; 8 stat lines belong to other teams; needs flagged as describing the old team);
**saved deal with an absent player** (Koloko) ✔. 2026-27 regular-season rows: **0** — nothing substituted, nothing zero.

**Strategy Lab "loading hang" — resolved as a Browser-pane artifact, no app change.** Reproduced only in Browser-pane tabs that were not focused
(`document.hasFocus() === false`); there the page stayed on `app/loading.tsx` and never issued `/trades`/`/scenarios`. Ruled out: missing logos (reproduced with
logos present and zero 404s), network (all requests 200, ms-fast), app logic and stale state (headless Chromium, fresh contexts: **12/12 loads in 76–367 ms** across Strategy Lab, Home, Cap Center and
Trade Evaluator, `still loading: false`; the pane rendered it correctly in a tab opened in the foreground). The remaining cause is the pane's unfocused-tab throttling; I did not prove it at engine level.
Missing logos in the earlier isolated run caused the 404 storm and slow dev-mode paints only. **No defect found; nothing to fix.**

## D. Financial limitations (unchanged, verified)

No 2026-27 contract or salary record exists anywhere (0 contracts, empty import folder, no provider). Verified on real rosters: every salary `None`; `payroll: None`, `payroll_available: false`,
`players_with_salary 0`, `payroll_known 0` (a labelled lower bound, never zero); no `cap_context`/apron conclusion; `cap-outlook.available: false`; trade legality is `conditionally_valid` on every evaluated deal
(a verified verdict requires verified contracts); the contract and assets components are excluded and named. 2026-27 cap *parameters* exist. A 2025-26 salary is never shown as 2026-27's (tested).

## E. Model safety and the November rollover

Today: five models trained 2026-08-25 on 2023-24..2025-26, 512 impact estimates and 279 team needs, all `2025-26`, **untouched** by activation. Nothing in the worker trains; **3 Nov flips the statistics season but
retrains nothing.** After it, evaluations say the model has no 2026-27 estimates, name the season they still read, and withhold `fit` (no 2026-27 needs). **3 Nov ≠ models ready.**

What `make train && make score` actually does (rehearsed on a disposable copy; not run on your database): `train` follows `current_season`, moves the window to `2024-25..2026-27` (**drops 2023-24**), deactivates all five active
models, and **permanently deletes the previous model's impact estimates** (`_gc_superseded_estimates`) — so re-activating the old version does *not* restore them; **only a backup does** (this corrects the earlier runbook). The only sample gate is ≥200 minutes per player across the window; there is no
current-season, team-coverage or benchmark gate, and with no 2026-27 rows it still wrote 500 "2026-27" estimates. `score` refuses until 2026-27 team statistics exist.

Required before a 2026-27 model update (a later phase; none implemented): (1) 2026-27 regular-season player **and team** stats ingested and `make data-status` clean; a minimum sample (suggest ≥ 20 games per team and ≥ 30 qualifying players per team-window — to be set deliberately);
(2) full backup immediately before; run on a **copy** first; (3) pin the three-season window explicitly and decide whether dropping 2023-24 is intended; (4) reproducibility: two runs on the same copy give identical `version` content hashes; (5) benchmarks: the three batteries, per-player TEI rank correlation old vs new (and the biggest movers reviewed), `r2_loo`/`slope_t` not below the current ones; (6) keep the old model versions *and* their estimates (disable the GC for that run) so rollback is a flag, not a restore; (7) promote only on a written comparison.

## F. BALLDONTLIE GOAT trial readiness — **READY WITH LIMITATIONS**

Reviewed the adapter and the 13-probe harness (P1-P2 identity, P3 games, P4 standings, P5/P5b player and team stats, P6-P7 advanced, P8 history, P9-P10 contracts, P11 injuries, P12 raw feeds, P13 error behaviour). `make balldontlie-preflight` on the activated data: **no blocker except the key**;
the 2026-27 roster warning is gone (rosters are staged and fresh); estimated **103 requests ≈ 26 min** at the trial's 5/min; all probes cached/replayable; no key is set in the environment and none was used. Not re-checked this session: the provider's *current* pricing/trial terms — confirm at sign-up.

Limitations that would reduce, not prevent, a useful evaluation: (1) the adapter has **never touched the live API** (everything is "documented", not "observed") — the free-key dry run is mandatory first; (2) **P9b (salary agreement) is inconclusive** without a contract snapshot, so contracts can be judged on coverage only; (3) the roster and identity probes compare against a **provisional** 2026-27 roster — re-run the refresh after opening night/cut-down, or the probes will measure roster churn rather than BALLDONTLIE; (4) 73 new identities exist only from roster rows (complete bio, no stats) — expect a lower link rate there; (5) injuries and 2026-27 stats cannot be validated against Pivot (Pivot has neither).

Owner checklist (no credentials in chat): free key → `BALLDONTLIE_API_KEY` in `backend/.env` → `make link-balldontlie DRY=1` (30/30 teams, sane player link rate; if 404 set `BALLDONTLIE_API_PREFIX=/v1`) → `make balldontlie-preflight` must print `"ready": true` → start the 48 h trial (cancel by hour 40 unless subscribing) → `make balldontlie-evaluate` (~26 min). Run these from the R8.2 code (the shared checkout has none of them).

## Verification summary

| Check | Result |
| --- | --- |
| Backend (SQLite, coverage floor 85) | **2,732 passed**, 57 skipped (optional extras not installed), **91.7 %** |
| Backend on PostgreSQL 16.2 (disposable) | **2,746 passed**, 43 skipped |
| `ruff` / `mypy` | clean |
| Frontend | 308 tests, `tsc`, `eslint`, production build clean; **Playwright e2e 8 passed, 1 skipped** (Ask Pivot with flag) |
| Analytics vs R8.1 (checkpoint code, pre-activation data) | comparable / acquisition / adversarial batteries **byte-identical** |
| API vs R8.1 (396 responses, same data) | 62 non-additive differences, all the documented fallback/completeness note wording |
| Cross-tool consistency on the activated data | **1,613 checks, 0 disagreements** (526 players) |
| Intentional output changes | roster membership (the point), additive fields, unverified-membership notices; `acquisition-validation` differs **only** if bio fields are refreshed (not done) |
| Not verified | worker across real 20 Oct/3 Nov boundaries; blocked/throttled NBA.com live; the running owner app (not R8.2); engine-level cause of the pane hang |

Skips are environmental (LangChain/LangSmith extras, no RealGM snapshot, no `tei_to_net_rating` in a fresh DB); the PostgreSQL run executes the 14 PG-only tests the SQLite run skips.

## G. Owner next steps (ranked)

1. **Run R8.2 code against the refreshed database** (the shared checkout is still R8). From the worktree, without switching your checkout:
   ```bash
   cd /Users/arjunvedere/pivot-r8-2-worktree/backend && DATABASE_URL=sqlite:////Users/arjunvedere/pivot-work/pivot/backend/tradelab.db \
     ASSET_LOGOS_DIR=/Users/arjunvedere/pivot-work/pivot/nbalogos ASSET_PLAYER_IMAGES_DIR=/Users/arjunvedere/pivot-work/pivot/nbaplayerimages \
     .venv/bin/uvicorn app.main:app --port 8000      # frontend: cd ../frontend && npm run dev
   ```
   (or switch the shared checkout to `feat/r8-2-activation-checkpoint` yourself; no migration is required).
2. **Re-run the roster refresh after opening night (20 Oct) and after the cut-down:** same worktree/`DATABASE_URL`, `make refresh DATASETS=rosters ARGS=--roster-only`. Rollback of this activation, if ever wanted: stop anything using the DB, copy the backup over `tradelab.db`, check its sha256 against the `.meta.json`.
3. **BALLDONTLIE:** free key → dry run → preflight → trial (checklist in F).
4. **3 Nov:** expect "no 2026-27 estimates" labels, not retraining. Do **not** run `make train && make score` until section E's preconditions hold, and only on a backup-protected copy.
5. Optional hygiene: `make purge-fixtures` lists the two "E2E comparison baseline" deals saved in your DB by an earlier e2e run (not modified); decide separately whether and when to refresh known players' bio fields.
