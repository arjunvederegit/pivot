# Model versioning and the safety guarantee

## 1. The problem R9.1 removes

`make train` (`analytics/train.py::train_all`) does three things an analytical research platform cannot tolerate:

1. it **retires every active model** of each name (`_register_model` sets `is_active = False` on the previous row);
2. it **permanently deletes the previous version's impact estimates** (`_gc_superseded_estimates`), and `make score`
   deletes stale rows of its own, so the only copy of a past rating is a database backup;
3. it makes the new model **active immediately**, in the same transaction, with no evaluation step between "trained" and
   "serving".

Training, evaluation and promotion were one act. R9.1 separates them. The legacy command still exists and, by default,
still behaves exactly as before (a test documents that); it is not what experiments should use.

## 2. The lifecycle

```
train candidate ──► evaluate (backtest, locked holdout) ──► review ──► [future] audited promotion
      │                                                         │
      └── status = candidate, is_active = false                 └── reject_candidate(): status = rejected
```

| `model_versions.status` | meaning | read by serving paths? |
| --- | --- | --- |
| `active` | exactly the rows with `is_active = true` | yes |
| `retired` | was active, superseded | no |
| `candidate` | trained for evaluation | **no** — and cannot be by accident (below) |
| `rejected` | a candidate a reviewer declined | no |

`is_active ⇔ status = 'active'` is maintained by the legacy writer (`_register_model` now sets both) and by a
context-sensitive column default for any other writer; a test asserts it after a full legacy training run.

**Promotion is not implemented.** `registry.promotion_blockers()` lists what a future promotion would have to establish
(an audited procedure, locked-holdout confirmation, a mapping from a forecast onto the serving table's TEI-scale columns,
re-running the comparable/acquisition/adversarial batteries). Nothing in R9.1 can set `is_active`.

## 3. Isolation by construction, not by filter

Candidate rows live in:

* `model_versions` — with `model_name = "player_forecast_<target>"`. Every serving query filters
  `model_name == "player_impact"` (or another serving name) **and** `is_active`; a candidate matches neither.
* **`candidate_estimates`** — a *new table* that no serving path reads. Candidate estimates are deliberately **not** written
  to `player_impact_estimates`, which is read by the player, team and evaluation APIs and counted by the data-health pages;
  a candidate row placed there would be one forgotten filter away from reaching a user. The table is generic over `target`
  and `output_kind`, so R9.2's skill estimates use the same store.

## 4. The guard: prove nothing else changed

`registry.train_candidate` runs this protocol:

1. **Refuse a dirty session** (uncommitted ORM changes would be discarded by a rollback).
2. **Compute under a read-only guard.** Reading the statistics, fitting and forecasting happen with a `before_cursor_execute` hook on
   the session's connection that raises `ProtectedStateViolation` for any `INSERT/UPDATE/DELETE/REPLACE/DROP/ALTER/CREATE/TRUNCATE`.
   The computation is read-only by construction and is enforced as such; other connections (a concurrent `make score`) are not
   affected, so a legitimate writer cannot cause a false alarm during the minutes-long computation.
3. **Finite-ness**: NaN/inf estimates or intervals are refused before anything is stored (PostgreSQL would store a NaN silently).
4. **Fingerprint immediately before the write**: `protected_fingerprint(db)` — row count and SHA-256 of the content of **every table**,
   *including every existing candidate* (`model_versions` of every status, `candidate_estimates`).
5. Insert the candidate `model_versions` row and its `candidate_estimates`; flush.
6. **Post-write invariants** (`_assert_isolated`): the new model is `status = candidate`, `is_active = false`, named
   `player_forecast_*`, and no `player_impact_estimates` row references it.
7. **Fingerprint again**, excluding only the new model's own rows. **Any difference ⇒ `ProtectedStateViolation`.** Any exception
   at all, violation or not, ⇒ `rollback()` (the session stays usable). Otherwise commit.

The same specification on the same data yields the same version string (a hash of spec + config + data fingerprint). A repeat is a
no-op **provided it still produces the same numbers** — if the code or a library has changed so that it would not, it refuses
(`CandidateError`) rather than return stale estimates under an unchanged id. The one thing a repeat may do is record a newer
*evaluation* on the candidate's own row, under the same fingerprint guard.

### What the tests prove (`tests/unit/test_modeling_registry.py`, `test_modeling_migration.py`)

| Test | Failure it would catch |
| --- | --- |
| snapshot active versions + all estimates, train two candidates, compare byte-for-byte | candidate training changing active state |
| mutation: a sabotaged computation **writes** to any of players, player_season_stats, teams, player_archetypes, an active model's metrics, or deletes an active estimate (parametrised, 6 statements) | the read-only guard / fingerprint not firing — each must raise `ProtectedStateViolation` with the session left unchanged |
| mutation: a sabotaged run deletes all of an **earlier candidate's** estimates and blanks its lineage | candidates being outside the protection (a defect in the first version of the guard, found by review) |
| `_assert_isolated` refuses an active candidate, a candidate named `player_impact`, a `retired`-status row, and a serving estimate referencing the candidate | a candidate becoming reachable by a serving reader |
| an error after the write rolls back and the session still works; NaN estimates; a dirty session; too little data | partial writes; a session stuck in a failed transaction |
| reuse refuses when retraining would give different numbers; the version changes when the data changes; an evaluation can be attached later without touching anything else | stale estimates under an unchanged id; silent reuse |
| `evaluation_from_experiment`: development-advanced → `development_only`, failed → `failed_development_rule`, a baseline → `not_evaluated_as_candidate`, holdout confirmed/not, other data → `stale:*`, other target → refusal | a candidate being labelled better evaluated than it was |
| the incumbent run through the candidate path equals the stored active estimates to 1e-9; a candidate forecast at a non-final cutoff equals the backtest's, bit for bit | the evaluation pipeline drifting from production, or from the backtest |
| legacy `train` default still deletes superseded estimates (documented); `--preserve-history` keeps them; GC cannot touch candidates | an unannounced change to legacy behaviour; a missing escape hatch |
| every candidate version string fits `model_versions.version` (64) | PostgreSQL rejecting the insert (SQLite never checks) |
| migration: status backfilled from `is_active`; upgrade/downgrade/upgrade and `alembic check`; **downgrade refuses while candidates exist** | data-orphaning downgrades; model/migration drift |

On a copy of the development database (38 tables, 71 MB), training two candidates left **every one of the 38 protected tables
byte-identical** under an independent `sqlite3` hash that does not use the code under test, and added 1,264 `candidate_estimates`
and two `candidate` model rows. One fingerprint pass took ≈ 0.6 s on that copy (measured before the candidate tables were included in it), so the guard is
cheap relative to a fit.

## 5. What a candidate records (reproducibility)

`model_versions` gained `status`, `config` and `lineage` (migration `a7d3e1f9c204`, additive; existing rows get
`status` from `is_active` and empty config/lineage — nothing is invented for models trained before R9.1).

* **config** — model spec, target, origin season, target season, seed, interval level, feature configuration.
* **lineage** — data-snapshot fingerprint (SHA-256 of the exact `player_season_stats` rows read, rounded to 1e-9, row-order
  independent), seasons and row counts, library versions (numpy/pandas/scipy/scikit-learn/SQLAlchemy), Python and platform,
  code commit and dirty flag, the fitted parameters (ridge coefficients, shrinkage constant, age curve, interval model),
  training-pair counts, and the **active version it should be compared against**.
* **validation_metrics** — what the experiment's artifacts say about *this model* (`evaluation_status`: `development_only`,
  `failed_development_rule`, `holdout_confirmed`, `holdout_not_confirmed`, `not_evaluated_as_candidate`, `stale:*`, or
  `unevaluated`), the experiment id, and the decision record. Read from the files, never assumed.
* **feature_list** — the columns the predictor actually reads (`Predictor.inputs`), not a guess from column names.
* **data_snapshot_id** — `ds-<16 hex>` (the column existed since R1 and was unused).

Random seeds: no model in R9.1 uses randomness in fitting (closed-form least squares and ridge); the seed governs only the
bootstrap resampling used to put intervals on *comparisons*.

## 6. Experiment artifacts (`models/experiments/<experiment-id>/`, git-ignored)

`config.json` (with the pre-registration) · `environment.json` · `data_snapshot.json` · `target.json` ·
`feature_manifest.json` · `folds.json` · `models.json` · `metrics.json` · `fitted_parameters.json` ·
`attrition.json` · `leakage_audit.json` · `decisions.json` · `predictions.csv.gz` · `run.json`. The experiment id is
`exp-<stage>-<target>-<config hash>-<data hash>`: the same inputs give the same id, and a changed byte of input gives a new
one. Only `run.json` (timestamp, runtime) differs between identical runs; a test asserts the rest is byte-identical.

Per-player predictions are not committed (derived from licensed statistics); aggregate results are in
`docs/r9-1/results/`.

## 7. Operating it

```bash
make migrate                                       # on a COPY of the database first (adds status/config/lineage, widens version, adds candidate_estimates)
make backtest BT_STAGE=development BT_TARGET=all   # writes models/experiments/exp-development-*/
make backtest BT_STAGE=holdout BT_TARGET=all       # needs exactly one development run per target in the same directory; recorded in holdout_ledger.json
make train-candidate CAND_MODEL=ridge_boxscore CAND_TARGET=impact_proxy CAND_EXPERIMENT=models/experiments/exp-development-impact_proxy-<id>
make candidates                                    # list candidate / rejected models
```

`make train` is labelled LEGACY in the Makefile. It is unchanged except for the opt-in `ARGS=--preserve-history`; note that flag is
undone by the next plain `make train`, and that the data-provenance page's impact-estimate count then includes every kept version.
The Makefile variables are prefixed (`BT_*`, `CAND_*`) so that an exported `TARGET` or `MODEL` in the environment cannot bind silently.

## 8. Known limits of the guarantee

* The guard protects *database* state. It does not protect the owner's file system or a process that bypasses the registry; it is a
  property of `train_candidate`, not of the database.
* The fingerprint covers table content, not schema. A candidate cannot alter a schema, but this check would not notice one.
* A legitimate writer **on another connection** that commits between the two fingerprints (milliseconds) would be reported as a
  violation and the candidate rolled back — a safe failure, not a data loss. The read-only guard only constrains this session's connection.
* The holdout ledger lives in the experiments directory; a different directory has a different ledger. It is discipline, not
  access control.
* `status` is advisory for any writer that bypasses both the ORM default and `_register_model` (none exists today); a raw-SQL insert
  with `is_active = 1` would be `retired`.
* PostgreSQL has not been exercised: it was not available. The static length check, the `String(64)` widening and the existing
  PG-compat pattern cover the known class of failure, but a PG run of `make migrate` + `make train-candidate` is still owed.
