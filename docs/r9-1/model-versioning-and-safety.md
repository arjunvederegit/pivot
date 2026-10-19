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

1. `protected_fingerprint(db)` — row count and SHA-256 of the content of **every table except `candidate_estimates`**, with
   `model_versions` restricted to non-candidate rows. (Every table: models, estimates, archetypes, needs, scenarios, saved
   trades, statistics, rosters, identity, contracts, provenance.)
2. Read the statistics, fit on training pairs only, forecast.
3. Insert the candidate `model_versions` row and its `candidate_estimates`.
4. Fingerprint again. **Any difference ⇒ `rollback()` and `ProtectedStateViolation`.** Otherwise commit.

The same specification on the same data yields the same version string (a hash of spec + config + data fingerprint), so a
repeat is a no-op that returns the existing candidate; nothing is rewritten or deleted.

### What the tests prove (`tests/unit/test_modeling_registry.py`)

| Test | Failure it would catch |
| --- | --- |
| snapshot active versions + all estimates, train two candidates, compare byte-for-byte | candidate training changing active state |
| mutation: a sabotaged run **deletes** an active estimate | the guard not firing — expects `ProtectedStateViolation`, a rollback, and the estimate restored |
| mutation: a sabotaged run **edits** an active `tei` by 0.001 | silent value drift |
| mutation: a sabotaged run **deactivates** the active model | an `is_active` flip |
| candidate is `is_active = false`, absent from `player_impact_estimates`, named `player_forecast_*` | accidental serving |
| the incumbent run through the candidate path equals the stored active estimates to 1e-9 | the evaluation pipeline drifting from production |
| legacy `train` default still deletes superseded estimates (documented); `--preserve-history` keeps them | an unannounced change to legacy behaviour; a missing escape hatch |
| legacy garbage collection cannot touch candidates | cross-contamination the other way |
| unknown model / season / inapplicable target are refused **before any write** | partial writes on bad input |

## 5. What a candidate records (reproducibility)

`model_versions` gained `status`, `config` and `lineage` (migration `a7d3e1f9c204`, additive; existing rows get
`status` from `is_active` and empty config/lineage — nothing is invented for models trained before R9.1).

* **config** — model spec, target, origin season, target season, seed, interval level, feature configuration.
* **lineage** — data-snapshot fingerprint (SHA-256 of the exact `player_season_stats` rows read, rounded to 1e-9, row-order
  independent), seasons and row counts, library versions (numpy/pandas/scipy/scikit-learn/SQLAlchemy), Python and platform,
  code commit and dirty flag, the fitted parameters (ridge coefficients, shrinkage constant, age curve, interval model),
  training-pair counts, and the **active version it should be compared against**.
* **validation_metrics** — the experiment's decision (or `evaluation_status: unevaluated`).
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
make migrate                       # on a COPY of the database first
make backtest STAGE=development TARGET=impact_proxy
make train-candidate MODEL=ridge_boxscore TARGET=impact_proxy EXPERIMENT=models/experiments/<dir>
make candidates                    # list candidates / rejected
```

`make train` is labelled LEGACY in the Makefile. It is unchanged except for the opt-in `ARGS=--preserve-history`.

## 8. Known limits of the guarantee

* The guard protects *database* state. It does not protect the owner's file system or a process that bypasses the
  registry; it is a property of `train_candidate`, not of the database.
* The fingerprint covers table content, not schema. A candidate cannot alter a schema, but this check would not notice one.
* The fingerprint reads every protected table twice; on the 71 MB development database that is a few seconds, which is the
  price of the guarantee.
* `status` is advisory for any writer that bypasses both the ORM default and `_register_model` (none exists today).
