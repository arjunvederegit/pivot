# data/

This directory intentionally ships **no NBA data**.

All NBA basketball data (teams, players, rosters, standings, statistics, games) is
retrieved at runtime from NBA.com via the [`nba_api`](https://github.com/swar/nba_api)
package and stored in your local database with full provenance
(provider, endpoint, parameters, retrieval timestamps, package version, ingestion run ID).

Raw provider payloads are **not** committed to this repository because NBA.com's terms
do not clearly permit redistribution. To populate your own **disposable** database:

```bash
make setup
make migrate       # changes the schema of the database DATABASE_URL names
make seed-config   # load salary-cap parameters (version-controlled YAML, officially sourced)
make sync-data     # fetch current NBA data from NBA.com via nba_api (network required); writes that database
```

Every command after `make setup` writes to the database `DATABASE_URL` names (by default
`backend/tradelab.db`, which on a long-lived checkout may be hand-built and irreplaceable).
Work on a copy. **Training and scoring (`make train`, `make score`) are not setup steps** and
are deliberately not listed here: on a database that already holds impact estimates they retire
the active models and permanently delete superseded estimates. Which command does what, the
season-setting checks and the 2026-11-03 statistics rollover are in
[Operating safely](../README.md#operating-safely-data-training-and-models) and the
[R8.2 operational runbook](../docs/r8-2/operational-runbook.md) §5; the project rules are in
[CLAUDE.md](../CLAUDE.md) and [CONTRIBUTING.md](../CONTRIBUTING.md).

## Subdirectories

- `cba/` — **committed**. League-wide published cap/tax/apron/exception figures with an
  explicit `confirmed` / `nba_estimate` / `projected` status per season. League-wide
  money, not per-player contract data; see `cba/README.md`.
- `contracts/` — optional user-imported contract/salary file. `nba_api` does not provide
  contract data; see `contracts/README.md` for the expected schema and legal notes.
  Files placed here are gitignored and never redistributed.
- `imports/` — raw provider pages you download yourself (Basketball-Reference contracts,
  RealGM future drafts, stats CSVs). **Gitignored in full.** They are third-party pages
  whose redistribution terms are not clear, so they stay on the machine that fetched
  them; only normalized, attributable data derived from them is ever committed.
- `snapshots/` — local database snapshots (gitignored).

## Test fixtures are not data

`backend/tests/fixtures/` contains small, sanitized, recorded response excerpts used only
by deterministic tests. They are clearly marked as fixtures, are never loaded by
production configuration, and are not presented in the application.

### Your development database may still hold test entities

The end-to-end suite saves real scenarios, trade proposals and comparison sets through
the API. It now runs against its **own** database (`make e2e`, which builds one via
`make seed-demo`), but a database used before that change — or used for manual
exploration — can hold rows like `E2E RosterLab deal` or `Smoke test deal`. They are
indistinguishable from your own saved work in the UI, which is the point of this note.

```bash
make purge-fixtures            # list what looks automated (dry run)
make purge-fixtures APPLY=1    # delete it — irreversible; back up the database first
```

The match is deliberately narrow — `e2e`, `smoke`, `test`, `fixture`, `probe` as whole
words — so a name you chose is never removed. Anything it does not match is yours, and
you delete it yourself.

### The demo league is not NBA data

`python -m app.cli seed-demo` builds a synthetic league for the end-to-end suite. Team
identity comes from `nba_api`'s bundled static table; every other row is generated and
stamped `source_provider="demo_seed"`. The seeder refuses to run against a database that
already holds `nba_api` rows, so it cannot mix with real data.
