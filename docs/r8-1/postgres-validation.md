# R8.1 — PostgreSQL validation

**Status: verified, in isolation.** One real defect found and fixed (§3). Everything below ran against a
disposable PostgreSQL server; no persistent or shared database was touched.

## 1. Environment

| | |
| --- | --- |
| Server | PostgreSQL **16.2** (aarch64-apple-darwin), the binaries bundled in the `pgserver` wheel, installed in a **throw-away venv in the session scratch directory** — not added to `pyproject.toml`, no production dependency change |
| Isolation | `initdb` into a scratch data directory, listening on **127.0.0.1:55432 only** (no unix socket, no other interface; 5432 untouched), `trust` auth, started and stopped by this session (PID 99859) |
| Databases | `r8_manual`, `r8_real`, `r8suite`, and one `r8test_<random>` per compat-test run (created by the test, dropped by the test with `WITH (FORCE)`; it only ever drops the database it made) |
| Client | the project's own `psycopg[binary]` 3 + SQLAlchemy 2; Alembic from the repo |
| Docker | not used (the daemon was not running; starting Docker Desktop was unnecessary) |

## 2. What was verified

**A. Migration chain.** `alembic upgrade head` on an empty PostgreSQL: all 10 revisions apply;
`alembic check` → *No new upgrade operations detected* (models and migrations agree). Applying twice
is a no-op.

**B. The R8 migration from the pre-R8 revision, with data** (`f2c8b41d6a05 → a9d4c6e18b53`,
`tests/integration/test_postgres_compat.py::TestR8MigrationOnPostgres`):

| Check | Result |
| --- | --- |
| existing stat rows survive; `season_type` backfilled to `regular` on all (player and team tables) | ✔ |
| `season_type` is `NOT NULL` with default `regular`; old `uq_pss`/`uq_tss` gone; `uq_pss_scope (player, season, season_type, stat_type)` and `uq_tss_scope` exist | ✔ (read back through the inspector) |
| `provider_entity_ids`: `uq_provider_entity (entity_type, provider, provider_id)`, index `ix_provider_entity_internal` | ✔ |
| `media_assets` gains `usage_status` (NOT NULL, default) + four nullable columns | ✔ |
| regular + preseason + playoffs rows coexist for one (player, season, stat_type); a duplicate scope raises `IntegrityError` | ✔ |
| a second BALLDONTLIE binding for one provider id is refused by the constraint | ✔ |
| downgrade → upgrade on disposable data loses nothing | ✔ |
| a downgrade that *would* destroy data (a playoff row exists) is refused, and because PostgreSQL DDL is transactional the schema is left at head, intact | ✔ |

**C. The canonical layer on PostgreSQL** (same tests, real Postgres semantics — `timestamptz`, strict
types, enforced foreign keys): idempotent roster and statistics jobs, regular/preseason separation, a
rejected snapshot leaves the data untouched and the failed run recorded, freshness reads aware
timestamps and `last_refresh`, `roster_as_of` reconstruction.

**D. The whole backend suite on PostgreSQL.** `tests/conftest.py` now honours an optional
`PIVOT_TEST_DB_URL` (drop-and-recreate the schema per test on that database; unset = SQLite, as before):
**2,731 passed, 5 skipped** (the same 5 environmental skips) on PG 16.2, including the 14 PG-only
compat tests. The first PG run found 10 failures (§3) and one fixture that inserted a roster row for a
player that did not exist — SQLite does not enforce foreign keys, PostgreSQL does; the fixture now
creates the player.

**E. A copy of the real 71 MB development database, loaded into PostgreSQL** (migrated to head; 29
non-empty tables, row counts equal in every table: 17,022 player-season stat rows, 5,121 players,
2,519 media rows, 512 impact estimates, …):

| Comparison (new code on PostgreSQL vs **R8 code on SQLite**) | Result |
| --- | --- |
| `acquisition-validation` | **byte-identical** (31,143 bytes) |
| `adversarial-validation` | **byte-identical** (5,209 bytes) |
| `comparable-validation` | identical except **one** informational value: `leave_one_dimension_out.overlap_without.draft_capital` **0.2541 (SQLite) vs 0.254 (PostgreSQL)**; every pass/fail identical; PostgreSQL is run-to-run deterministic. Likely a floating-point summation-order effect of unordered query results — **not confirmed** |
| `scripts/r8_consistency` | **1,705 checks, 0 disagreements** |
| 423 API responses (`scripts/r8_1_api_snapshot.py compare --unordered`) | every value equal; the only differences are provenance timestamps ≤ 1 s apart on 525 responses (below) |

## 3. Defects and differences found

**P1 — `model_versions.algorithm` was `VARCHAR(50)`; real labels are 51 and 97 characters.
`make train` could not register a model on PostgreSQL.** SQLite does not enforce declared lengths, so the
project's real database holds `weighted z-score index (transparent, fixed weights)` (51 — the production
impact metric) and the 97-character pick-value-curve label, and 10 tests failed on PG with
`StringDataRightTruncation`. A scan of every `String(n)` column against the real data found this one
and no other overflow. Not caused by R8 (the column predates it), but it means **no PostgreSQL
deployment of this codebase could have trained**. Fixed by migration `c5e2b7d91f34` (widen to 200),
model updated, regression test (`test_every_registered_algorithm_label_fits_its_column`) and a PG test
that demonstrates the failure at the previous revision and the fix at head. Safe both ways round: an
un-migrated SQLite database keeps working with the new code (the length was never enforced), and the
migration was verified on a copy of the real SQLite database (labels preserved, `alembic check` clean).
**The owner needs to run `make migrate` on their own database only if they want it to match a PG
deployment; nothing breaks if they do not.**

**P2 — API list order within a position is the database's.** `/teams/{id}/roster` orders by `position`
only, so players sharing a position come back in insertion order on SQLite and heap order on
PostgreSQL (the same 30 rosters, same players, different order). Values are identical; consumers that
index by id are unaffected; consumers that display in response order will differ by engine. Not
changed (it would change SQLite output order too); a tie-breaker is a one-line follow-up.

**P3 — "last row wins" provenance timestamps.** `/players/{id}/stats` (495 values) and `/players/{id}`
(`stats_retrieved_at`, 30) report the retrieval time of whichever stat row the database returns last
among base/advanced/estimated — up to ~1 s apart. Cosmetic; same cause as P2.

**P4 — Timestamp serialisation.** SQLite returns timestamps naive (stored as UTC), so most API
timestamps serialise as `2026-07-21T01:53:25` (no zone); PostgreSQL returns aware values and they carry
a zone. A browser parses the zone-less form as *local* time. This pre-dates R8.1 and is a display
concern on SQLite, not a data difference (`data-health` already normalises its own).

## 4. Not tested / limits

* One server version (16.2) and one client driver. No other PostgreSQL major version, no extensions,
  no connection-pool or concurrency behaviour (the worker, the API and `make train` against one
  database at once), no `pg_dump`/restore, no performance comparison.
* The data load used SQLAlchemy Core inserts of a SQLite copy — it proves the *schema and data* are PG
  compatible and the analytics produce the same answers; it is not a documented migration procedure
  from SQLite to PostgreSQL (none exists in the repo).
* `docker-compose.yml`'s Postgres service was not started.

## 5. Reproduce

```bash
# any disposable PostgreSQL you can create/drop databases on:
cd backend
PIVOT_TEST_POSTGRES_URL=postgresql+psycopg://user@127.0.0.1:55432/postgres \
  .venv/bin/pytest tests/integration/test_postgres_compat.py -q          # 14 tests; skipped when unset
PIVOT_TEST_DB_URL=postgresql+psycopg://user@127.0.0.1:55432/scratch \
  .venv/bin/pytest -q                                                    # the whole suite on Postgres
```
