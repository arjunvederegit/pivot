# R9.2 — Model safety, serving rules and the (future) promotion procedure

R9.2 **promotes nothing**. Every inferred or projected value the engine can return comes from a model whose status is `candidate`
and `is_active = false`, labelled as such on every record. This document states the guarantees, where each is enforced and tested, and
what a future, reviewed promotion would have to do.

## 1. Guarantees

| # | Guarantee | Enforced by | Tested by |
| --- | --- | --- | --- |
| 1 | Existing TEI ratings, estimates and model versions are untouched | no R9.2 code writes to `player_impact_estimates`, retires a model or sets `is_active`; the only write path is R9.1's `train_candidate` | `test_modeling_registry` (byte-identical active state), `test_pi_mutations::test_a_candidate_cannot_overwrite_an_active_model`; verified on copies of the real database: **19 model versions and 512 estimates hash-identical** before/after migration and candidate training, on SQLite and PostgreSQL |
| 2 | Candidate training is non-destructive and proves it | `registry.train_candidate`: read-only computation guard, protected-state fingerprint of **every table** (incl. every other candidate) before and after, isolation assertions, rollback on any difference | R9.1's six sabotage tests still pass; R9.2 adds ability rows under the same checks |
| 3 | A candidate cannot appear as validated, and cannot borrow evidence it does not have | `service.serving_decision` (one rule); `ModelRef.status` and `evaluation_status` on every record; the registry's evidence attaches only to a stored model whose **own recorded configuration and data fingerprint** equal those the registry's experiments ran under (`ValidationRegistry.applies_to`: same method, same artifact schema as today's code, the registry's run ids and data fingerprint — both the experiment's and the model's own at training time — and the features / seed / interval level the experiments ran under; the controlled training path writes those fields and a caller-supplied `evaluation` can neither assert provenance nor claim a status, only record a failure or "unevaluated"; an experiment that evaluated a different configuration confirms nothing for this model); `promotion_readiness` uses the same check; an `active` status needs `is_active` too and an unknown status string is unservable; the registry can *withhold* (for everyone, even when a version is named); a failed forecast evaluation blocks that model's projections and a *stale* failure stays failed | `test_pi_service::TestServingDecision`, `test_pi_review_fixes` (evidence not borrowed; stale cannot lift a failure; withheld means nobody), `test_pi_mutations::test_serving_a_candidate_without_the_opt_in_is_detected` |
| 4 | Inferred/projected outputs are opt-in and labelled | `kinds=` default is `measured`; `include_experimental` required for any candidate; a notice is attached | `test_pi_service::TestModelOutputs`, `test_player_intelligence_api` |
| 5 | A read never writes | the service issues only SELECTs; candidate outputs are *read from the store*, never computed in a request | `test_reading_never_writes` (fingerprint of all tables unchanged) |
| 6 | Historical outputs are reproducible and never overwritten | version id = hash(configuration, data fingerprint); append-only; idempotent re-training and re-materialisation (forecast and ability rows compared separately); the version id carries an artifact-schema tag and reuse compares **every** stored column (estimate, interval, level, exposure, each `inputs` key, and what the row means: output kind, season, target season), refusing without touching the evaluation record if anything differs; concurrent materialisations resolve by reuse | `test_modeling_registry::TestReuse`, `test_pi_review_fixes::TestIdempotence`; clean-environment re-run and a PostgreSQL run, see [qa-and-regression-report.md](qa-and-regression-report.md) |
| 7 | The holdout is not re-read to tune anything | hard-coded holdout origins; mandatory ledger; R9.1's ledger is loaded as a *prior* ledger, so a target read there needs an explicit `--acknowledge-reread` and is recorded as a re-read | `test_modeling_experiment`; the ledger in `results/holdout_ledger.json` shows `ts_pct` flagged `reread: true` |
| 8 | The live TEI and existing consumers are unchanged | no consumer imports the new service; the route is off by default and absent from the OpenAPI schema; the TEI formula is untouched | `test_player_intelligence_api::test_the_preview_is_off_by_default…`, the three existing validation batteries (see QA report) |
| 9 | No secrets, no raw licensed data | no provider call was made in R9.2; aggregates only are committed; databases are git-ignored | gitleaks allow-list unchanged; `git diff` reviewed |

## 2. Serving tiers

| Tier | Meaning | Who may see it | Reached by |
| --- | --- | --- | --- |
| `measured` | an observation; data integrity only | any caller | integrity checks pass |
| `validated` | an `active` model whose forecasts were confirmed on a first-read holdout, promoted by a reviewed decision | any caller | **nothing in R9.2** |
| `experimental` | a `candidate` that advanced under the pre-registered rule and was confirmed; or an ability model that met the inferred serving rule | only `include_experimental=true`, always labelled | `fg3_pct` and `ft_pct` projections; the three ability estimates |
| `benchmarked` | the reference baseline (`ols_history`), whose out-of-time accuracy is recorded but which **claims no improvement** | only `include_experimental=true` (it is a stored candidate) | `ts_pct` projection |
| `withheld` | evidence does not support serving | nobody | any output whose method failed the rule |

The registry that assigns these (`app/player_intelligence/validation_registry.json`) is *generated from the experiment artifacts*; the rules
are written down in `app.modeling.experiment.PREREGISTRATION_R92` (with five dated amendments: #1 fixes a tie-break before any holdout read; #2 caps a re-read; #3–#5 were made after the evidence they respond to — the holdout reads, a self-found
history confound, and the independent statistical review — and each only lowers what is claimed or served, or adds a diagnostic).

## 3. What the failure modes look like

* A candidate is trained on bad data → `train_candidate` raises before any write (non-finite estimates, too few rows, no calibratable interval).
* A write goes wrong halfway → the whole session rolls back; the protected-state fingerprint refuses to commit if anything but the new
  candidate's own rows changed.
* A model fails its pre-registered evaluation as a forecaster → its status is stored as `failed` (or `holdout_not_confirmed`); the service never returns **that model's projections**,
  even with `include_experimental=true`, and a stale failure stays a failure. Its ability estimate, if any, is governed by its own pre-registered serving rule (non-inferior forecasts, a calibrated predictive
  interval, and — added by the review — a reliability floor of 0.30) and is served only if that rule is met, with the fact that its forecasts were not confirmed stated on the record.
* A model is rejected after the fact → `reject_candidate` marks that one row; the service stops returning it immediately.
* The registry does not know a quantity → *unevaluated*, withheld; absence of evidence is never "assumed fine".
* A stored model that merely shares a method name with a registry entry (trained on other data, under another configuration, or never evaluated) is **not** given the registry's evidence: it is shown
  with its own stored status (default `unevaluated`), no calibration and a limitation saying so.
* A holdout ledger declared as a prior ledger but missing refuses the holdout read (it cannot silently disable the re-read guard).

## 4. The reviewed promotion procedure (designed, not built, not run)

`player_intelligence.promotion.promotion_readiness(db, version)` reads a candidate and returns the **blockers** — it can never return
`eligible = True` in R9.2. A future promotion would, in order:

1. **Confirm the evidence.** Projected output: advanced in development *and* confirmed on a holdout that no protocol had read before.
   Inferred output: its model's forecasts meet the same bar; ability is never "confirmed" (status capped at `development_only`).
2. **Re-run the existing validation batteries** (comparables, acquisition, adversarial — plus the R3 gate test) with the model's outputs in
   the path, and require byte-identical results for every consumer that does not read them.
3. **Reproduce** the stored estimates from a clean environment and the recorded data snapshot; they must match within the documented tolerance.
4. **Record a human approval** naming the version, the evidence and the reviewer (no such record type exists yet).
5. **Publish append-only**: add a record that references the candidate version and the evidence; never mutate the candidate or any earlier
   record. Serving switches by reading the newest such record.
6. **Rollback** = add a later record that supersedes it. Nothing is deleted, so a past cutoff can always be re-served as it was.

Consumers are protected twice: `serving_decision` refuses a `candidate` as validated, so a published record that did not also change the
model's status would change nothing; and the TEI-based consumers never read this path at all.

## 5. Known limits of these guarantees

* The guarantees hold for the path the code defines. Someone with database access can still write rows by hand; the fingerprint detects
  changes made *during* a candidate operation, not at rest.
* The registry is only as honest as the artifacts it is built from; the builder is deterministic and tested, and the artifacts carry the data
  fingerprint they were computed on (the service reports a *stale* evaluation as unevaluated if the data later changed).
* `candidate_estimates` is keyed per model version; there is no cross-version uniqueness, so two versions trained on different data for the
  same cutoff can coexist (by design: older versions are kept). The service prefers the version the registry's evidence applies to, then the newest, and names its version on every record.
