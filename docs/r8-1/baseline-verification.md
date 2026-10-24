# R8.1 — Baseline verification

*Taken 2026-10-18 before any change. Everything here was re-measured, not copied from R8's reports.*

## 1. Repository and isolation

| | |
| --- | --- |
| Source | `feat/r8-canonical-data-foundation` @ **`5cada84`** (local and `origin/` identical; `5cada84` is the tip, two commits above the pre-R8 merge `ccaf0b8`: `ad535cf` feat, `5cada84` docs) |
| New branch | `feat/r8-1-operational-validation`, created from `5cada84` with `git worktree add` |
| Worktree | `/Users/arjunvedere/pivot-r8-1-worktree` — **outside** the shared checkout (`/Users/arjunvedere/pivot-work/pivot`, left on its own branch with only its pre-existing untracked `docs/r8.zip`) |
| Python | a fresh venv **inside the worktree** (`pip install -e ".[dev,assistant]"`). The owner's venv was not reused: its editable install points at the shared checkout, so running tests with it would have imported the wrong code |
| Existing branches | none overwritten; no force-push; nothing merged to `main` |

**Services found running, not touched.** A `uvicorn app.main:app --port 8010` (PID 85520) serving a
*disposable QA database* from another Claude session's scratchpad, two other Claude Code sessions, Ollama.
Nothing was listening on 3000 or 8000. This session started and stopped only its own processes
(by PID): a Postgres on 55432, a backend on 8041 and a frontend dev server on 3041, all gone at the end.

**Databases.** Tests ran on `sqlite:///:memory:` (the suite's default; no `.env` exists in the worktree).
The owner's `tradelab.db` (71,131,136 bytes, mtime 2026-10-18 05:57 — before this session) was only
ever read through SQLite's online-backup API into the scratch directory (`mode=ro`); every migration,
refresh and test ran on copies or on databases created for the run. Verified after the fact: size and
mtime unchanged, `git status` in the shared checkout unchanged. The shared `.env` was not read or edited.

## 2. R8's claims, checked

| R8 claim | Re-measured | Verdict |
| --- | --- | --- |
| 2,610 backend tests passing, 5 skipped | **2,610 passed, 5 skipped** — *with the optional `.[assistant]` extra installed.* In a venv without it: 2,567 passed, **43 skipped** (38 Ask Pivot tests need `langchain-*`). The report's number is right but depends on an undisclosed install | ✔ with a caveat |
| 92 % backend coverage | 92.06 % (17,681 stmts, 1,404 missed) | ✔ |
| 1,705 cross-tool comparisons, 0 disagreements | `scripts/r8_consistency` on a copy of the real DB with **R8 code**: 1,705 / 0 | ✔ |
| Analytical batteries byte-identical | R8 code on a DB copy: `comparable` 8,619 B, `acquisition` 31,143 B, `adversarial` 5,209 B — sizes match the report; used here as the R8.1 baseline | ✔ |
| 421 API responses compatible | `scripts/r8_1_api_snapshot.py` (new) dumps 423 (teams, players, rosters, needs, payroll, season stats, cap outlook, 120 player pages and histories): all 200 | ✔ (a differently-composed set; see regression report) |
| Frontend 301 tests, `tsc` clean | `vitest`: 20 files, **301 passed**; `tsc --noEmit` clean; `next build` succeeds | ✔ |
| One roster predicate at all consumers | grep: tools use `current_roster_clause`; `identity.py` scopes by season explicitly; **`balldontlie/evaluation.py` read `RosterEntry.is_current` unscoped** (sampling, contracts) | ✘ one violation (fixed in R8.1) |
| Roster refresh is idempotent and cannot be wiped by an empty/implausible answer | unit-tested ✔; **no check on a first snapshot of a season**, and **duplicate rows inside one response were not collapsed** | partly (fixed) |
| Empty/zero-row provider answers are rejected | true — but an empty answer was also **retried 4×, opened the circuit breaker and was classed a schema failure**; the same code path would make 2026-27 look broken | ✘ semantics (fixed) |
| "Real data is stale" (rosters 89 d, others 55 d) | measured: rosters 89.7 d, standings/player stats 55.0 d, team stats/games 89.7 d — **accurate ages, but a completed season's 55 days is not staleness** | ✘ interpretation (fixed) |
| nba_api "not yet in canonical records" | confirmed; jobs spoke dicts and treated every exception alike | gap (addressed) |
| Migration verified on SQLite only | confirmed; PostgreSQL run in R8.1 found a real defect (VARCHAR(50)) | gap (addressed) |

## 3. Owner database state (read-only copy)

Alembic `a9d4c6e18b53` (R8). 30 teams, 5,121 players, **530 current roster rows** (all 2025-26,
retrieved 2026-07-21), 17,022 player-season stat rows (2025-26 `base/advanced/estimated` retrieved
2026-08-24, plus `totals` from the user CSV, plus 2016-17 … 2024-25), 300 standings, 180 team stats,
1,230 games (last 2026-04-12), `season_calendar` 10 seasons (2025-26: 2025-10-21 → 2026-04-12), 0
contracts, 0 injuries, 0 provider links, 41 sync runs (the latest NBA.com sync 2026-08-24), 512 impact
estimates, 632 archetypes. No 2026-27 rows of any kind.

## 4. Existing failures

None. No test failed at baseline; none was silently fixed. (The 5 skips: Ollama ×3, a RealGM snapshot,
a model not trained in the test database — all environmental.)

## 5. Reproduce the baseline

```bash
git worktree add ../pivot-r8-1-worktree 5cada84        # or: git archive 5cada84 backend | tar -x -C <dir>
cd backend && python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev,assistant]"
.venv/bin/pytest -q -p no:cacheprovider --cov=app                      # 2,610 passed, 5 skipped
DATABASE_URL=sqlite:///<copy>.db .venv/bin/python -m scripts.r8_consistency   # 1,705 / 0
DATABASE_URL=sqlite:///<copy>.db .venv/bin/python -m app.cli comparable-validation > before.out   # etc.
```
