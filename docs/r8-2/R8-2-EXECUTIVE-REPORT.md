# R8.2 — Current-season operational readiness: executive report

*For the product owner. 2026-10-18. Branch `feat/r8-2-current-season-readiness` (from R8.1 `2b0a627`); nothing merged
to `main`. Detail: [architecture](season-context-architecture.md) · [validation](validation-report.md) ·
[runbook](operational-runbook.md) · [remaining work](remaining-work.md).*

## Status: **Partially complete** — the engineering is done and verified; four things need you (below)

Every acceptance criterion that can be met in code and on live data was demonstrated by running something. The
milestone is *partial*, not *complete*, for three reasons stated plainly rather than hidden: (1) your **real
database has not been refreshed** — I worked on a copy, so *your* app will show last season's roster, labelled as
a fallback, until you run one command; (2) the **2026-27 roster is provisional** — NBA.com cannot say which list it is; (3) **no
2026-27 salary data exists anywhere**, so payroll, cap, matching and the contract/assets components remain
unavailable (honestly, not as zeros). A fourth: the worker was not run across a real date boundary.

## Does Pivot now show real 2026-27 rosters?

**Yes.** `make refresh DATASETS=rosters` now targets the league year (2026-27) instead of the statistics season.
Live from NBA.com today: **526 players, 30/30 teams, 0 duplicates, 0 players on two rosters**, repeat run
`added 0 / confirmed 526`. Pick any team and the tools show its 2026-27 roster; Jaylen Brown is on Philadelphia,
his 28.7-point line is labelled **2025-26 with Boston**, and a rookie is on his team with *no history* rather than zeros.

## How roster and statistics coexist

They were one setting (`current_season`); they are now two clocks (architecture §2):

| | Season today | Turns over |
| --- | --- | --- |
| Roster | **2026-27** | 1 July |
| Statistics / model estimates / team needs | **2025-26** (labelled "latest completed") | 3 Nov (14 days into the season) |
| Contracts | 2026-27 (cap year) | by hand |

Every tool payload carries a `season_context` block; the UI says "2026-27 roster [provisional] · statistics:
2025-26 regular season". A stale `CURRENT_SEASON=2025-26` in your `.env` can no longer hold rosters back.

## Tools tested

| Tool | API | Browser |
| --- | --- | --- |
| Workspace home | ✔ | ✔ (record labelled "2025-26 record") |
| Team Outlook | ✔ | ✔ (context line; "Limited analysis" notice on needs) |
| Player Explorer / directory | ✔ | ✔ (8 of PHI's 15 cards say "totals with BOS/LAL/…") |
| Player page | ✔ | ✔ |
| Trade Evaluator | ✔ | ✔ end-to-end on live 2026-27 rosters |
| Strategy Lab (saved deals) | ✔ | ✔ renders (see validation §6 for an environment caveat) |
| Cap Center / payroll | ✔ | ✔ ("0 of 17 salaries on file", not zeros) |
| Ask Pivot | feature flag off by default | ✔ e2e: `/assistant` is not found without the flag |

## Models: do they safely support the new context?

* **Player impact, skills, roles — yes.** They describe players, not teams, so they follow a player to his new
  team; labelled with the season they came from. Rookies are excluded and named, never zero.
* **Team needs / roster fit — only with a label.** Needs were measured on the 2025-26 *team*; for a rebuilt roster
  they may not apply. Computed unchanged, labelled, confidence capped at *medium*, **not recomputed** (that would be
  a new model — R11). No coefficient, weight or threshold changed.
* **After 3 Nov, before retraining:** evaluations say the model has no estimates for 2026-27 and name the season they
  still read; `fit` is withheld (no 2026-27 needs exist).
* **A correction to R8.1 that matters:** `make train && make score` follow `current_season`, so running them on 2 Nov
  retrains **2025-26**, not 2026-27. A rehearsal on a copy also showed `train` **drops 2023-24 from the model window and
  retires all five active models**, and `score` refuses until 2026-27 team statistics exist. The safe procedure is in
  runbook §5. R8.2 ran neither on any real database.

## Money

Salaries are looked up for `CAP_LEAGUE_YEAR` (2026-27) only; an earlier season's salary is never shown as 2026-27's
(tested), a missing one is never zero, and payroll/cap/apron are withheld. **No legitimate 2026-27 salary source exists
on disk** (0 contracts, empty import folder, no provider), so nothing could be connected. The fields a verified source
must fill are listed in remaining-work §C; the next step is the *planned* BALLDONTLIE GOAT evaluation — not started,
nothing purchased.

## Evidence (short)

* Backend **2,730 passed** (SQLite, 91.7 % coverage); **2,744 passed on PostgreSQL 16.2**; `ruff`/`mypy` clean.
* Frontend 308 tests, `tsc`, `eslint`, production build clean; Playwright e2e 8 passed / 1 skipped (flag).
* **Analytics byte-identical** to R8.1 on a copy of your database where no 2026-27 roster exists (three batteries);
  cross-tool consistency **1,705 checks / 0 disagreements** (same as R8.1) and **1,613 / 0** on the live 2026-27 rosters.
* 396 API responses, R8.1 vs R8.2 code: **only additive fields** plus one explicit fallback sentence; every trade
  evaluation identical.
* **14/14 mutation checks caught** (one first missed and the test strengthened).
* 60 NBA.com requests, no failures; your real `tradelab.db` read three times through the SQLite read-only backup API (to scratch copies) and
  never written — size and mtime identical at the end (71,131,136 B, 05:57).

## What I found that you did not ask about

1. Saved deals **across the flip**: a deal built on 2025-26 rosters can contain a player now on no roster; he was being
   scored as available. Now labelled (`membership_warnings`), not refused (validation §6a).
2. Your database copy holds two **"E2E comparison baseline"** saved deals from an earlier e2e run against the dev
   database (`make purge-fixtures` lists such leftovers).
3. The sidebar can read "Data aging — NBA.com data is 0 day(s) old": the stale part is the identity tables
   (teams/players, 30-day window, R8.1 C5), not rosters or statistics. Not changed.

## Do this (in order)

1. `make refresh DATASETS=rosters` against your real database; read `roster_verification` (runbook §1).
2. After opening night (20 Oct) and after the cut-down, run it again — the snapshot is `provisional_preseason` until then.
3. Blank `CURRENT_SEASON` in `backend/.env` if it is pinned (no longer urgent for rosters; still needed for the 3 Nov roll-over).
4. On/after 3 Nov follow runbook §5 for 2026-27 estimates — **rehearse on a copy**, do not run `make train` on 2 Nov.

## Is Pivot ready for current-season usability testing?

**Yes for exploring rosters, players and the trade tools — with the labels it now shows**; **no for anything that
depends on money** (cap, matching, contract value) or on 2026-27 results (none exist). Ordinary users can open an
organisation and work with its real roster today. What stands between this and confident use: your refresh (1), the
provisional-roster caveat, the 3 Nov retraining, and contract data.

## Git

Branch `feat/r8-2-current-season-readiness` · worktree `/Users/arjunvedere/pivot-r8-2-worktree` · commit and push status in the
final handoff message.
