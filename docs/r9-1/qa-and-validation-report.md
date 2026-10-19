# QA and validation report

Two **independent review agents** (a statistician and a senior engineer, each briefed with the code and *not* with my conclusions)
attacked the work after the first complete implementation; I also reviewed it myself. This document lists every finding, what was
done, and what is left. Severity: HIGH = could invalidate a headline conclusion or break the safety guarantee.

## 1. Test results

| | Baseline (R8.3 `95c8d99`, before any change) | Final |
| --- | --- | --- |
| Backend tests | 2,808 passed · 19 skipped · 0 failed | **2,970 passed · 19 skipped · 0 failed** (+162, all new, in 9 files) |
| Coverage (floor 85 %) | — | **93.05 %**; `app/modeling/*` 90–100 % |
| `ruff check app tests` | clean | clean |
| `mypy app` | clean | clean (177 files) |
| `alembic check` (model/migration drift) | passes | passes |
| Frontend | not changed (`git diff 95c8d99 -- frontend` is empty), so not re-run | — |

The 19 skips are the same 19 as in the baseline run (PostgreSQL-only tests — no PostgreSQL was available — and optional
assistant/snapshot tests). **Not verified:** PostgreSQL, Playwright, Ask Pivot, a game-night refresh.

The suite's wall time grew (baseline 44 s without coverage; the final suite 7 min 44 s *with* coverage, against 5 min 03 s at the first
full run) because the backtest tests fit real models; each uses a small synthetic league and shares runs where it can. This is the
price of testing the harness end to end, and is worth trimming if CI time matters.

### Existing analytical outputs are unchanged

The three existing validation batteries (`adversarial-validation`, `comparable-validation`, `acquisition-validation`) were run on
(a) R8.3 code against the R8.3 scratch database and (b) R9.1 code against a migrated copy that had **three candidates trained into
it**. **All three outputs are byte-identical.** (`acquisition-validation` exits non-zero on `same_need_teams_hear_more_alike` in *both*
— a pre-existing failure, not caused by R9.1.)

## 2. Required boundary and defect tests

| Required case | Test (file → name) | Result |
| --- | --- | --- |
| Future statistics inserted into a historical set | `backtest` → leakage audit passes on clean data; **fails** when a feature reads next season, when the scoring population is restricted to next-season survivors, and when a feature depends on next season's team | detected |
| Candidate training overwriting / deleting / editing / deactivating an active model | `registry` → six sabotage statements (writes to players, season stats, teams, archetypes, an active model's metrics, a deleted estimate), plus edits and flips | each raises `ProtectedStateViolation`; session unchanged |
| Tampering with an earlier *candidate* | `registry` → delete all candidate estimates + blank lineage mid-run | detected (a gap the first version of the guard had) |
| Missing player identity | `panel` → `player_id` null | refused |
| Incorrect season type | `panel` → a playoff row for the same player-season | panel unchanged |
| Duplicate player-season records | `panel` → duplicated row | refused |
| Missing prediction target | `backtest` → players with no next-season row are scored, counted in attrition and never evaluated | covered |
| Incompatible units | `panel` → true-shooting ×100; turnover share ÷100 (NBA.com publishes it in percentage points) | refused |
| Partial historical data | `panel` → a season with 12 of ~100 players | refused |
| Non-finite forecasts | `registry` (candidate refused, nothing stored), `backtest` (raised) | covered |

## 3. QA Pass 1 — statistical and methodological review (independent reviewer)

| ID | Sev | Finding | Disposition |
| --- | --- | --- | --- |
| H1 | HIGH | The team check was **silently skipped for `pie`** (its comparator, the TEI, did not apply there), so candidates passed it vacuously | **Fixed**: the check now compares a candidate with the *reference baseline* by a team-season bootstrap and **fails closed** when it cannot be computed; test for the fail-closed path |
| H2 | HIGH | For `impact_proxy` the check is **circular** (the proxy contains on-court net rating, itself a team result) and used the wrong comparator, so it could not reject anything; it also scored a forecast of *t+1* against season-*t* wins, penalising regression to the mean | **Fixed/redefined**: prospective roster-known view vs the reference baseline with an interval; the concurrent view is reported but never gates; documented as incapable of rejecting for this target |
| H3 | HIGH | "Beats the best simple baseline" was overstated: **no baseline had the candidates' information** (windowed history). A 4-parameter OLS reproduced `ridge_history` | **Fixed**: `ols_history` added as a baseline. **This reversed the headline** (§5) |
| M1 | MED | The leakage audit scrambled only numbers; it could not see survivorship, identity or team leaks; ran at one origin; docstring said "proves" | **Fixed**: later rows are deleted and re-assigned to other players/teams, the scored population and eligibility inputs are compared, NaN is a failure, every development origin of the primary target is audited; reworded "falsification test"; three injection tests |
| M2 | MED | The holdout lock was policy: it moved when data was ingested; the ledger was skippable and keyed so a sensitivity run got a fresh read; `advanced_from` was an unverified dict | **Fixed**: holdout origins hard-coded; ledger mandatory and keyed by target; the development run is loaded from its artifacts and verified (protocol version, target, data fingerprint); tests. *Residual:* the ledger is per experiments directory (discipline, not access control). The smoke-test read of the primary holdout is disclosed |
| M3 | MED | Selection on target-season minutes (~46 % of scored players are not evaluated); sensitivity changed training too; reference flipped between near-ties | **Documented** beside every headline; sensitivity is now **evaluation-only**; the reference (`ols_history`) is stable across cuts |
| M4 | MED | The player-cluster bootstrap holds the evaluated *seasons* fixed; "≥ 4/5 folds" counts any negative difference | **Fixed**: a fold-level t-interval is required in addition; a 2 % practical floor added; claims reworded |
| M5 | MED | No multiplicity control; 14 of 18 pairs advanced under v1, so the rule discriminated little | **Declared**: one confirmatory target, the rest exploratory; the amended rule advanced 0 of 18 |
| M6 | MED | COVID seasons are in every development fold's features or target | **Documented** with per-fold results; the holdout is the clean regime; not correctable with 10 seasons |
| L1 | LOW | Early folds train on 2 transitions, so lags are missing for part of them | Documented; handicaps the candidates, does not inflate them |
| L2 | LOW | Interval wording ("calibrated out-of-time"); one shared calibrator so coverage ≈ 0.80 for every model; per-fold coverage 0.72–0.89 | Reworded (cross-validation, not forward chaining; the test-fold coverage is the evidence); width reported beside coverage |
| L3 | LOW | No vintage control | Documented |
| L4 | LOW | Squared-loss fitting judged on MAE; reference chosen by raw MAE; no Spearman uncertainty; multi-team players carry one row | Documented |

The reviewer also listed **checks that found nothing**: no temporal leak in the real code path (as-of discipline, training pairs,
fitted quantities, the incumbent adapter, age handling — `AGE` is season-specific, verified year-over-year); aligned rows; correct
bootstrap and metric code; development does not read holdout seasons.

## 4. QA Pass 2 — code and architecture review (independent reviewer)

| # | Sev | Finding | Disposition |
| --- | --- | --- | --- |
| 1 | HIGH | Team check skipped for `pie` | Fixed (see H1) |
| 2 | HIGH | The fingerprint excluded `candidate_estimates` and candidate `model_versions`, so tampering with an earlier candidate went undetected, and a new row that would be served was not checked | **Fixed**: every table is fingerprinted, excluding only the new model's own rows; post-write invariants (`_assert_isolated`); tests |
| 3 | HIGH | **Every candidate write would fail on PostgreSQL**: `model_versions.version` was `VARCHAR(30)`, candidate ids reach ~57 | **Fixed**: widened to 64 in the migration (downgrade restores 30); a static length test over every spec × target. *Not run on PostgreSQL* |
| 4 | HIGH | Holdout/CLI integrity: decisions carried no target or data identity; `--target all` with one decisions file; a different cut key bypassed the ledger; `train-candidate --experiment` could record another target's verdict, or `development_only` for a non-advancing model | **Fixed**: the CLI finds the single matching development run per target; staleness and target are verified; evaluation status is read from the artifacts (`failed_development_rule`, `holdout_confirmed`, `stale:*`, …) |
| 5 | MED | A concurrent legitimate writer during the minutes-long computation could raise a false alarm | **Fixed**: computation runs under a read-only connection guard; fingerprints sit immediately around the write |
| 6 | MED | No rollback on non-violation errors; NaN could be stored silently on PostgreSQL | **Fixed**: any exception rolls back; finiteness checks; a dirty session is refused |
| 7 | MED | Downgrade orphaned candidate rows | **Fixed**: downgrade refuses while candidates exist (R8 precedent); test |
| 8 | MED | Reuse ignored code changes | **Fixed**: reuse recomputes and refuses if the numbers differ |
| 9 | MED | CLI: blanket `except` appended a migration hint to unrelated errors; flag parsed as a value; tracebacks on bad input; no minimum training size | **Fixed**: specific errors → JSON, exit 2; strict option parsing; `InsufficientDataError` |
| 10 | MED | `feature_list` guessed from column-name prefixes | **Fixed**: `Predictor.inputs` is recorded |
| 11 | MED | `MIN_FOLDS_IMPROVED = 4` hard-coded | **Fixed**: 80 % of folds |
| — | LOW | `scipy` undeclared; contract accepted NaN/inf; scripts opened the database read-write; generic Makefile variable names; `from_db` twice | **Fixed** (declared; `allow_inf_nan=False`; `?mode=ro`; `BT_*`/`CAND_*`; single read) |
| — | LOW | `--preserve-history` undone by the next plain `make train`; inflates the provenance page count | **Documented** |
| — | LOW | registry duplicates the backtest's forecast path | **Pinned** by a bit-exact test at a non-final cutoff |

**Mutation run by the reviewer** (36 mutants of the guard, the lifecycle, the folds and the leakage guards): 24 caught (one only by
accident, with a `NameError`), 10 survived, 2 equivalent in isolation. Survivors were the gaps above — the guard on 8 unexercised tables, `reject_candidate` accepting an active model,
version ignoring data, swapped season fields, `min folds improved = 0`, always-true interval check, `_confirm` accepting
everything, the clustered-by-row bootstrap, inverted recency decay. **Each now has a test that fails against it**: parametrised
multi-table sabotage, load-bearing-check tests (all-good advances; breaking any one of the six conditions stops it), a
planted-signal test that advances and *confirms*, the cluster-count test, a recency-direction test, assertions on the stored
periods, a data-dependence test, and a negative test for `reject_candidate`. The equivalent-mutant pair (training origins `≤` and no
as-of truncation) is neutralised by the bit-exact forecast pin and the audit at non-final origins.

## 5. Findings from my own review and from running it

* **The first answer was wrong.** The first official development run advanced ridge models on 14 of 18 candidate–target pairs
  ("7 % better than the best simple baseline"). The reviewer's missing-baseline finding (H3) showed that was an information
  mismatch; with `ols_history` and the stricter rule it is 0 of 18. Both are recorded in the results document.
* **A vacuous verification, caught.** My first independent before/after hash of the database compared two empty files (a shell
  quoting error made the hashing script fail silently) and printed "identical". Caught because the output file was empty; redone with
  a valid script: 38 tables compared, 0 differences.
* **Pooled Spearman was contaminated by fold level** (a model forecasting a different constant in each fold showed a non-zero rank
  correlation). Now computed within each fold and averaged; `None` when undefined.
* **`TM_TOV_PCT` is in percentage points** while every other advanced rate is a fraction — found by the unit validator on real data.
  Harmless to TEI (it z-scores), a 100× error for any model reading it raw. The fringe-player rows (a legitimate 1.5 true-shooting %)
  are excluded from the unit check.
* **The smoke test read the holdout** before the split was enforced in code (disclosed in the protocol, the results and the report).
* **Tests that asserted nothing** (a tautological "advance == all(checks)", a holdout test that passed with both sets empty) were
  replaced by positive-path and load-bearing tests.

## 6. QA Pass 3 — regression and mutation (summary)

Full suite, lint, types, drift (§1); existing batteries byte-identical (§1); the sabotage, injection and load-bearing tests above;
legacy training behaviour pinned by test (default still deletes superseded estimates; `--preserve-history` keeps them; the legacy
garbage collector cannot touch candidates); API compatibility covered by the existing API suite (unchanged, passing).

## 7. QA Pass 4 — reproducibility

* Three development runs over the six targets — the first official run, the same code in a second process, and a **fresh process from a
  different working directory against a clean migrated database copy** (no candidates, none of the earlier run's state) — produced
  **the same experiment ids and byte-identical `metrics.json`, `decisions.json`, `fitted_parameters.json`, `folds.json`,
  `attrition.json`, `leakage_audit.json` and `data_snapshot.json`, and identical per-player predictions** (29,600 rows for the primary
  target). Only `run.json` (timestamp, runtime) differs.
* Nondeterminism: none found. Fits are closed-form; the bootstrap is seeded; the seed is recorded.
* A future developer needs only a database copy and `make backtest` (commands in [model-versioning-and-safety.md](model-versioning-and-safety.md) §7);
  the data fingerprint and library versions are recorded so a mismatch is visible. The experiment directory is git-ignored; aggregates
  are committed in [results/](results/).
* The holdout was deliberately **not** re-run (that would be a second read); its determinism is covered by the unit tests.

## 8. QA Pass 5 — product alignment

1. *More trustworthy analytics?* Yes: the incumbent now has an out-of-time error measurement, a calibrated-versus-shipped interval
   comparison and a defensible baseline set — none existed before — and an experiment can no longer destroy history.
2. *Can R9.2 reuse it?* Yes: a new skill is a `TargetSpec` plus a `Predictor`; candidates, folds, baselines, the contract and the protocol are generic.
3. *Can R9.3 consume versioned outputs?* The `PlayerIntelligenceRecord` contract and the `candidate_estimates` table carry period, model, evaluation status, sample and provenance; no serving path exists yet, by design.
4. *Scope creep?* None to R9.2/R9.3/R10+; no frontend change; no live rating changed; no promotion path built (`promotion_blockers` only lists what one would need).
5. *Depth or complexity?* The final design is smaller than the first: the candidates that did not earn their complexity are kept as
   documented negative results, not as features.

## 9. Remaining limitations (honest list)

* PostgreSQL was not run; the known failure class (column length) is closed by a static test and the widening, but a PG run is owed.
* The holdout ledger is per experiments directory.
* Only one provider snapshot exists (no vintage control); COVID seasons are in every development fold; ~46 % of scored players are not in
  the evaluation population; the primary target's holdout is not pristine (smoke test) and one of its folds was used by the incumbent's own
  stored validation.
* `acquisition-validation` has a pre-existing failing check (identical before and after R9.1).
* The `candidate-estimates` store and `PlayerIntelligenceRecord` have no consumer yet; they are exercised only by tests.
