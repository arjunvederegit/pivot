# R8.1 — Operational data reliability & season readiness: executive report

*For the product owner. 2026-10-18. Branch `feat/r8-1-operational-validation` (from R8 `5cada84`); nothing
merged to `main`. Detail: [baseline](baseline-verification.md) · [season readiness](season-readiness.md) ·
[live ingestion](live-ingestion-report.md) · [PostgreSQL](postgres-validation.md) ·
[regression](regression-report.md) · [BALLDONTLIE readiness](balldontlie-trial-readiness.md) ·
[remaining work](remaining-r8-work.md).*

## Status: **Complete**, with the limitations below stated rather than hidden

Every acceptance criterion in the brief was demonstrated by running something, not by describing it.
Nothing is marked complete on fixtures alone: where only fixtures exist the report says so.

## What improved

1. **"Stale" now means something.** R8 reported every dataset stale. Judged by the NBA calendar: on your
   real database **one** dataset needs attention — the rosters (89 days old, genuinely stale). The
   2025-26 statistics, standings, games and team stats are `finalized`: the season ended 12 April and
   they were retrieved after it settled. (A *completed season retrieved before it ended* still reads
   stale. A completed season is *finalized*, not *immutable*: it is re-verified, and a correction
   shows up in the refresh report.)
2. **2026-27 is understood.** The system knows the league is in the 2026-27 preseason, that
   regular-season statistics are `not_yet_available` until 20 October (not a failure, not a zero, never
   last season's numbers), that preseason and regular-season rows are separate, and which season the tools
   read — and it **moves the tools to 2026-27 itself on 3 November** (two weeks of games), instead of waiting
   for someone to edit `CURRENT_SEASON`.
3. **A provider that has nothing to say is no longer a provider that failed.** An empty NBA.com answer was
   retried four times, tripped the circuit breaker and counted as a schema error — which would have made
   every 2026-27 statistics call before opening night look like an outage. It is now a distinct,
   calendar-judged outcome; a *blocked* provider is never mistaken for it.
4. **nba_api speaks the canonical contract.** Calls return `ProviderResult` statuses, season type is passed
   through (preseason statistics are fetched and stored apart from regular-season ones), and rows
   convert to the same record types as BALLDONTLIE. What is *stored* did not change by a byte.
5. **PostgreSQL works** — and it did not, before: the model registry could not be written there
   (`VARCHAR(50)` against 51- and 97-character labels). One migration fixes it.
6. **The BALLDONTLIE trial is cheaper to get right**: a preflight, two new probes (rosters, team
   statistics), a harness defect fixed, and a free-key dry run that verifies the integration at no cost.

## What was actually tested

| | |
| --- | --- |
| **Live NBA.com** (isolated database, ≈113 requests, no failures) | 30 teams × 2 seasons of rosters (530 / 526 players), standings, 1,722 player-stat rows, 60 team-stat rows, 1,230 games, 1,064 preseason rows; three correct "expected empty" answers for 2026-27. Re-runs idempotent. 2025-26 statistics re-fetched equal your 2026-08-24 snapshot on 573 of 573 players. |
| **PostgreSQL 16.2** (disposable server) | whole migration chain; the R8 migration with data, constraints, downgrade/upgrade and a refused destructive downgrade; **the entire backend suite: 2,731 passed**; your real database loaded into it: two validation batteries byte-identical, 1,705/1,705 consistency checks. |
| **Regression** | backend 2,717 passed / 19 skipped (92.7 % coverage), ruff and mypy clean; the three analytical batteries **byte-identical** to R8 on a copy of your real data; 423 API responses with **zero** differences; cross-tool consistency **1,705 checks, 0 disagreements** on your data, on live-fetched data and on PostgreSQL; frontend 301 tests, `tsc` and production build clean (no frontend change). |
| **Mutation checks** | 11 deliberate regressions, 11 caught. |

## Does 2026-27 work?

Yes, for what can be tested before the season exists. Verified live and by date-injected tests: the
phase, the baseline labelling, the empty statistics, preseason/regular separation, the staged 2026-27
rosters (108 players changed team, 72 rookies with no history), historical rosters intact, and three-tool
agreement on both sides of the flip. **Not verified until it happens:** the worker across the real
2026-10-20 and 2026-11-03 boundaries. **One thing still needs a human:** `make train && make score` for
2026-27 before the flip (the readiness report, `make season-status`, says so).

## Does live ingestion work?

Yes, today, against a healthy NBA.com, for the datasets above. It found two real defects you did not
know about: **five neutral-site games (Mexico City, Paris, Berlin, the Cup semifinals) had lost a team and
a score** — in your database too — and eight 2025-26 players have statistics but no identity in Pivot.
The first is fixed (re-run `make refresh DATASETS=games`); the second is disclosed and counted. Blocked,
throttled or failing NBA.com was not observed; those protections are fixture-verified.

## Can BALLDONTLIE testing begin?

**Ready with limitations.** The code and the evaluation workflow are as prepared as they can be without
the live API, which they have never touched. Do the free-key dry run first (§6 of the readiness doc): it
verifies the key, the path prefix and pagination for free, and is what stops a wrong `/v1` vs `/nba/v1`
from spending your 48 hours. No trial, account or request was made in this session.

## Limitations that remain

* The tools read one season at a time; in the summer they show last season's rosters although 2026-27's
  are staged and fresh (remaining-work B1 — a real design change, deliberately not made here).
* No contract or injury data; NBA.com's terms still limit everything to private development.
* The new states (`finalized`, `not_yet_available`, `is_baseline`) are served but not shown in the UI.
* Not run: Playwright e2e, load/concurrency, the scheduler across a real date boundary.

## Things I did that you should know about

* **Isolation held.** A separate git worktree outside your checkout; a venv inside it; your
  `tradelab.db` only read (SQLite backup API, to a scratch copy; mtime unchanged); your `.env` never
  opened; no process killed except three I started myself, by PID (a Postgres on 55432, a backend on 8041,
  a frontend on 3041). The other session's backend on :8010 was left alone.
* I made ≈113 requests to NBA.com for the live test, within R8's note that this is private development.
* A Postgres server (the `pgserver` wheel, in a scratch venv) was used and stopped; no project dependency
  was added.
* One migration was added, `c5e2b7d91f34` (widens `model_versions.algorithm`). It is needed on PostgreSQL;
  your SQLite app keeps working without it, and I did not run it on your database.

## Acceptance criteria

| Area | Criterion | Status |
| --- | --- | --- |
| Lifecycle | active vs completed distinguished | ✔ demonstrated (tests, live, real DB) |
| | 2026-27 rollover safe | ✔ tested with injected dates and live data; **worker across the real boundary unverified** |
| | preseason ≠ regular | ✔ live (1,064 preseason rows, 0 regular) |
| | missing future data handled | ✔ live (3 expected-empty runs) |
| | dataset-specific freshness defined and tested | ✔ |
| Canonical ingestion | existing paths canonical-compatible | ✔ nba_api; CSV/Kaggle/transactions deliberately not moved |
| | identity mapping consistent | ✔ (24 unmatched stat rows disclosed) |
| | refresh idempotent | ✔ live |
| | bad upstream cannot destroy valid records | ✔ fixtures; **not observed live** |
| | refresh updates provenance/status | ✔ |
| Operational | real NBA ingestion tested | ✔ |
| | PostgreSQL verified in isolation | ✔ (found and fixed a defect) |
| | cross-tool consistency intact | ✔ 1,705/0 ×4 |
| | analytics regression-tested | ✔ byte-identical |
| | frontend intact | ✔ unchanged; 301 tests/tsc/build; live render; **e2e not re-run** |
| Providers | BALLDONTLIE reviewed; prerequisites documented; verdict | ✔ READY WITH LIMITATIONS |
| Safety | shared DB untouched; owner services uninterrupted; no secrets; isolated branch; nothing merged | ✔ |

## Recommendation

**B — the BALLDONTLIE live evaluation**, after the four owner steps below. The foundation held up against
real data, a second database engine and a season boundary; what is left that matters most is evidence from
the one component that has never run, and the trial is cheap to prepare now. Do **A (remediation)** items
A1–A4 of the remaining-work list first because they have a date on them. **C (R9)** can start once B1's
design is decided — R9's first task (persisted skill vectors + a backtest) does not need it, R11 does.

1. `make refresh DATASETS=rosters` and `make refresh DATASETS=rosters ARGS='--season 2026-27'`, then `make refresh DATASETS=games`.
2. Check `backend/.env` for a pinned `CURRENT_SEASON`; blank it.
3. Create a free BALLDONTLIE key and run `make link-balldontlie DRY=1`.
4. Between 2026-11-02 and 2026-11-03: `make train && make score`.
