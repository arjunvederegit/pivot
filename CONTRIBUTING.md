# Contributing

Thanks for your interest! Pivot is a portfolio project, but issues and PRs are
welcome.

## Ground rules (the honesty bar)

1. **No synthetic NBA data in production paths.** Fixtures live only under
   `backend/tests/fixtures/` / `frontend/tests/` and must be clearly marked.
2. **Missing data becomes an explicit unavailable state** — never an estimate, a
   default, or a hidden branch.
3. **Provenance or it didn't happen**: provider-derived rows populate the
   ProvenanceMixin columns; UI surfaces source + timestamp.
4. New CBA rules require: plain-English description, formula, source reference,
   coverage entry in `docs/cba-rule-coverage.md`, and unit tests for pass, fail,
   and unavailable paths.
5. Model changes require updated validation metrics in the model card and a new
   `model_versions` entry.
6. **Database and model safety** (below) is part of the bar: no training, scoring,
   activation or reset against a database whose contents you cannot afford to lose.

## Dev workflow

```bash
make setup && make migrate && make seed-config   # schema + cap parameters in the DATABASE_URL database
make sync-data                                   # optional: real data from NBA.com (network); writes that database
make dev                                         # :8000 + :3000
make lint && make test                           # must pass before a PR
```

Do the above against a **copy** of the data (or a fresh disposable database), not against
a database someone built by hand. `make train` and `make score` are intentionally absent:
they are not setup steps. On a fresh disposable database they populate impact estimates and
team needs; on a database that already holds them they are destructive (see below).

CI runs ruff, mypy, pytest, eslint, tsc, vitest, next build, migration check, and
Docker builds — none of which may call NBA.com.

## Database and model safety

Verified against the `Makefile` and `backend/app/analytics/`; the full tables and the
reasons are in [Operating safely](README.md#operating-safely-data-training-and-models).

- **Never train or score against the owner's real database** without a separately approved
  operational procedure and a verified backup. `make train` retires the active model
  versions and, unless `ARGS=--preserve-history`, permanently deletes the superseded impact
  estimates; `make score` rewrites and deletes `team_needs`. Re-activating an old version
  does not undo either — only a backup does.
- **Prefer database copies** for development and validation:
  `DATABASE_URL=sqlite:///<copy> make …`. Never point `E2E_DB` at a real database;
  `make seed-demo` deletes and recreates it.
- **Candidate training is not activation.** Use `make train-candidate` for experiments; it
  never changes an active model. Promotion is designed, not built
  ([R9.1](docs/r9-1/model-versioning-and-safety.md),
  [R9.2](docs/r9-2/model-safety-and-promotion.md)). Do not mark a model active by hand.
- **Holdout evaluation follows the preregistered protocol**
  ([evaluation architecture](docs/r9-1/evaluation-architecture.md)): development stage
  first, one holdout read per target recorded in the ledger, an explicit
  `--acknowledge-reread` for any re-read, and no tuning on the holdout.
- **Verify season settings first.** `make season-status` (read-only) shows which season each
  kind of data is read from and whether `CURRENT_SEASON` is pinned behind the calendar.
  `train` and `score` use that implicit setting. **2026-11-03 is the documented statistics
  rollover checkpoint**; confirm what is configured instead of assuming it is correct, and
  follow the [R8.2 operational runbook](docs/r8-2/operational-runbook.md) §5.
- **Model mathematics is not a drive-by change.** A change to a model, a feature or a
  calibration needs its own approval and updated validation evidence (rule 5).

## Commit style

Conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `chore:`); explain
*why* in the body when the change is behavioral.
