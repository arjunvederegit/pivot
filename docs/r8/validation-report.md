# R8 — Validation report

**Completion status: R8's foundation is complete against all ten acceptance criteria as written, with
three stated exceptions that are *not* hidden inside "complete":**

1. **No live provider call of any kind was made** — BALLDONTLIE has never been exercised against the
   real API (no key; no trial started), and NBA.com was not contacted. Everything about BALLDONTLIE
   is verified against its *documentation* and *synthetic fixtures* only.
2. **`nba_api` is not yet routed through the canonical records.** It keeps its existing dict protocol and
   `sync_*` jobs (hardened, but not re-expressed as `ProviderResult`). Only the BALLDONTLIE adapter
   emits canonical records; interchangeability is demonstrated by that adapter, not by two live ones.
3. **The new `data_status` fields are not shown in the UI** (backend support was the stated sufficient bar).

Commands and results below are from this session (2026-10-18).

## 1. Acceptance criteria

| # | Criterion | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Canonical team and player identities implemented, or existing equivalents validated and strengthened | **Met** | NBA.com keys retained and validated; `provider_entity_ids` + `ProviderIdMap` added; hint-assisted resolution; 38 real duplicate names; `test_canonical_foundation.py::TestNameResolutionWithHints`, `TestProviderIdMap` |
| 2 | Consumers use consistent season, identity and statistical definitions | **Met** | one roster predicate at all 20 sites; scope-explicit stat reads; `scripts/r8_consistency.py` 1,705 comparisons / 0 disagreements on the real DB |
| 3 | Reproducible ingestion pathway for the supported sources | **Met (not run live)** | `make refresh DATASETS=…` with plausibility guards; exercised with fake providers in `test_ingestion_jobs.py` and `test_cli.py::TestR8Commands`; **not executed against NBA.com** |
| 4 | Missing and outdated information handled explicitly | **Met** | `fresh/stale/missing/unknown`; `missing_player_ids`; `unlisted`; `None` not 0 — tests below |
| 5 | Provider failures do not silently corrupt analytical results | **Met** | empty-answer guards; `ProviderResult` status classes; 11 failure-mode tests in the adapter suite; mutation check M3/M5 |
| 6 | ≥ 2 existing consumers share a compatible data pathway | **Met** | Team Overview, Player Evaluation, Strategy Lab/evaluator, Cap Lab payroll, CBA resolver |
| 7 | Source provenance and freshness inspectable | **Met** | `data_status` on 6 endpoints, `GET /data/freshness`, `GET /data/providers`, `make data-status` |
| 8 | Representative identity, roster, season and data-quality tests pass | **Met** | §2 |
| 9 | Existing frontend and backend functionality regression-tested | **Met** | §3, §4 |
| 10 | Architecture supports adding BALLDONTLIE or another provider without coupling tools to it | **Met (one adapter)** | the BALLDONTLIE adapter was added with **zero changes to any tool**; consumers import only `app.canonical` |

## 2. New and changed tests

| Suite | Tests | Covers |
| --- | ---: | --- |
| `tests/unit/test_canonical_foundation.py` | 62 | seasons (century boundary, malformed labels), season-type maps, league-calendar dates, stat definitions, minutes-unit normalisation, missing ≠ 0, freshness (oldest decides, naive SQLite timestamps, completeness only against an expectation), `ProviderResult` semantics, team aliases (no alias shadows a real abbreviation; the three formerly duplicated maps are one), franchise eras, hint-assisted resolution (including contradiction → refuse), crosswalk idempotency/conflict/replace, roster membership (stale, partial league, conflict, unlisted, season-scoped), scope-explicit stat reads (playoff row never returned for a regular question; both coexist under the new key) |
| `tests/unit/test_balldontlie_adapter.py` | 35 | key hygiene, pagination, normalisation, every error class, 429 `Retry-After`, 5xx, timeout, HTML error page, empty vs failed, 401 inference, pacing (15 s trial / ≈0.125 s GOAT), cursor loop, truncation disclosure, namesakes separated by draft year, idempotent linking, conflict filing |
| `tests/unit/test_balldontlie_harness.py` | 18 | probe wiring, per-game vs totals inference, contract coverage/agreement arithmetic, tier skipping, budget refusal, **replay with zero requests**, key absent from evidence files |
| `tests/integration/test_r8_cross_tool_consistency.py` | 15 | three tools list the same people, show the same figures, declare the same definitions, ignore a playoff row; missing rookie listed not zeroed; prior-season row not current performance; traded player's totals name both teams; stale snapshot reported on every reader; demo rows never called NBA.com; credential never in provider registry; namesakes get distinct pages |
| `tests/unit/test_r8_quality_media_migration.py` | 11 | four new quality checks; media rights default/clear/re-index-preserves-licence; migration backfill, widened unique key, clean reversal, no model/migration drift |
| `tests/unit/test_ingestion_jobs.py` (changed, 21 → 34) | 34 | idempotent roster diff, movement, drop, empty-answer rejection, partial-league rejection, history idempotency, empty-answer rejection for 4 jobs, explicit season type |
| `tests/unit/test_cli.py` (changed +6) | 28 | `data-status`, `refresh` (failed dataset fails the command), plan, key-less refusal |

**Mutation check.** Six deliberate regressions were introduced one at a time and each was caught by a
named test: stat reads ignoring season type (M1), roster predicate ignoring season (M2), roster guard
disabled (M3), namesake biography ignored (M4), `empty` treated as failure (M5), freshness judged by
newest row (M6). (M6 first appeared *not* caught — a stale `.pyc` of identical size from the mutation
run — cleared and re-run; the suite is green.)

## 3. Backend regression

| Command | Result |
| --- | --- |
| `pytest -q --cov=app --cov-fail-under=85` | **2,610 passed, 5 skipped**, coverage 92.07 % (baseline 2,453 passed, 5 skipped, 92 %) |
| skips | the same 5 as baseline (Ollama ×3, RealGM snapshot, missing `tei_to_net_rating`) |
| `ruff check app tests scripts` | clean |
| `mypy app` | clean (159 files) |
| `alembic upgrade head && alembic check` | clean on a fresh DB and on a copy of the real DB |
| migration on a **copy of the real 40 MB database** | 17,022 stat rows and 2,519 media rows preserved, `season_type='regular'` on all, 180 team rows preserved, downgrade → upgrade round-trip clean |
| coverage of new code | canonical 95–100 %, `balldontlie/` client 93 %, normalizers 93 %, provider 93 %, crosswalk 99 %, `data_provenance` 100 %; **`evaluation.py` (trial harness) 63 %** |

## 4. Before/after on real data (the "no silent change" test)

The same real database (40 MB; 5,121 players, 530 rostered) was read by `main` @ `ccaf0b8` (against a
backup taken before migration) and by this branch (against the migrated copy):

| Comparison | Result |
| --- | --- |
| `make comparable-validation` output | **byte-identical** (8,619 bytes) |
| `make acquisition-validation` output | **byte-identical** (31,143 bytes) |
| `make adversarial-validation` output | **byte-identical** (5,209 bytes) |
| 421 API responses (30 teams × roster/needs/payroll/season-stats/profile/detail; 120 player pages + stat histories; season totals), additive fields removed | **0 differ**, 0 non-200 |

No model, threshold, coefficient or analytical output moved.

## 5. Cross-tool consistency on the real snapshot

`scripts/r8_consistency.py` against the migrated copy: **1,705 checks, 0 disagreements** across 30 teams
and 530 players (Team Overview roster = player directory = Strategy Lab roster = Cap Lab payroll; team
page statistics = player page statistics; every player page names the team the overview lists).

It also reports what the new fields expose about that same data:

| Dataset | Freshness | Age | Completeness |
| --- | --- | ---: | --- |
| rosters | **stale** | 89.3 d | 30/30 teams |
| standings | **stale** | 54.6 d | 30/30 |
| player statistics (base) | **stale** | 54.6 d | 525/530 rostered players have a row (5 rookies absent, not zero) |
| team statistics | **stale** | 89.3 d | 30/30 |
| games | **stale** | 89.3 d | 1,230 ingested |
| contracts | **missing** | — | 0 rows |

The honest reading: the agreement was real and the data is old. The tools could not previously have
told a user that.

## 6. Frontend regression

| Check | Result |
| --- | --- |
| `vitest --run` | 301 passed (20 files), unchanged from baseline |
| `tsc --noEmit` | clean |
| Frontend source diff | **none** (`git diff --stat` shows no `frontend/` file) |
| Production build (`next build --webpack`, in an isolated copy) | succeeds, all routes. (Turbopack failed in the copy only because of a symlinked `node_modules`, not the code.) |
| Playwright e2e, isolated stack (own demo DB, ports 8031/3031) | **8 passed, 1 skipped** — including `team outlook → strategy → trade evaluator → rules → evaluate → save → compare`. The skipped test needs the Ask Pivot build flag. The suite's hard-coded `:8000` demo-guard was pointed at the isolated backend in the *copy*; the repo file is unchanged. |
| Live browser check of Team Outlook (BOS) and Player Explorer on the real data | render correctly, no console errors; every `/api/v1` call 200 |

## 7. What was not tested

* Any live provider (BALLDONTLIE, NBA.com). A real `make refresh` has not been run.
* BALLDONTLIE persistence (not built).
* Ask Pivot with its flag on (untouched by R8; its 160 backend tests ran in the suite).
* Postgres. All migration and test runs were SQLite; the migration uses `batch_alter_table`, which the
  project already relies on for Postgres, but this session did not run it there.
* Load/concurrency behaviour of the new endpoints.
* Season rollover: no 2026-27 data was synced; the rollover exposure (audit V3) is reduced, not
  exercised.

## 8. Reproduce

```bash
cd backend
.venv/bin/pytest -q --cov=app --cov-fail-under=85                      # 2,610 passed
.venv/bin/ruff check app tests scripts && .venv/bin/mypy app
.venv/bin/alembic upgrade head && .venv/bin/alembic check               # after `make migrate`
DATABASE_URL=sqlite:///./tradelab.db .venv/bin/python -m scripts.r8_consistency   # 0 disagreements
make comparable-validation acquisition-validation adversarial-validation
cd ../frontend && npx vitest --run && npx tsc --noEmit
```
