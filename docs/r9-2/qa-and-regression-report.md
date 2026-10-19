# R9.2 — QA and regression report

Three independent QA rounds were run against the implementation, each followed by fixes, regression tests and a retest; the retest of Round 3 found a high-severity defect, so a fourth, final verification pass was run. This report records, for each round, who reviewed what, what they found
(with severity), what was fixed and by which test, what was *not* fixed and why, and the final numbers. It is the evidence behind [FINAL-QA-SIGNOFF.md](FINAL-QA-SIGNOFF.md).

## 1. How the reviews were run, and what that does and does not prove

* **Reviewers.** Each round was a separate, fresh sub-agent (an AI reviewer with no access to the author's reasoning, started with a brief that named the target commit, the documents to read and the
  things to attack, and told to *report, not fix*). They were not people. They share a model family with the author, so shared blind spots are possible; that is a limit of this evidence, not something the
  numbers below can remove.
* **They inspected and ran the code.** The reports name the files read, the commands run, the databases (always copies), versions and counts. Examples that cannot be produced by reading documents alone: an independent
  scalar Kalman recursion that matched `filter_histories` to 0.0 and the stored weights to 4.4e-16 (Round 1, Round 3); a Monte-Carlo check of the true-shooting variance formula (Round 1); four stronger baselines fitted through the
  R9.1 harness (Round 1); a deliberate panel-construction leak that the leakage audit *passed* (Round 1); materialisation, failure injection and two concurrent runs on SQLite *and* PostgreSQL (Rounds 2, 3); 9 and 26
  source-level mutants run against the test suite (Rounds 2, 3); a clean-clone re-run of a full development experiment that reproduced the committed run id and artifacts byte for byte (Round 3).
* **The full reports** are not committed (they contain scratch paths and probe databases); their findings, severities, dispositions and tests are reproduced below, finding by finding, without softening.
* **Retest.** Every round's findings were fixed and then re-tested. The fixes for Rounds 1 and 2 were verified by Round 3's reviewer (who re-ran the earlier reproductions). The fixes for Round 3 were verified by a further
  independent reviewer ("Round 3 retest", §6), whose fixes were in turn verified by a final independent pass (§6b). A fix verified only by the author's own tests is stated as such.

## 2. Final test and verification numbers

| Check | Result |
| --- | --- |
| Full backend suite, SQLite, at the final commit's code (`pytest tests`) | **3,188 passed, 19 skipped, 0 failed** (3,207 collected; 401 s). The 19 skips are environmental and pre-existing: no live Ollama, no local RealGM snapshot, no `tei_to_net_rating` model in the test database, … |
| R9.1 baseline for comparison | 2,970 passed at `40f8602` → R9.2 adds ≈ 218 tests (215 in nine new/extended R9.2 files, plus contract and compatibility additions) and changes no existing assertion's meaning |
| R9.2 files + `test_postgres_compat.py` + modeling tests, **PostgreSQL 16** (`PIVOT_TEST_DB_URL`, `PIVOT_TEST_POSTGRES_URL`) | **393 passed, 1 skipped** (the one skip is a foreign-key-dependent case that the PostgreSQL schema makes impossible); the module fixtures build the league in the real PostgreSQL schema |
| Frontend (vitest / `tsc --noEmit` / eslint) | **311 passed** (22 files) / clean / clean |
| `ruff check` on every R9.2 file; `mypy app` | clean; 188 source files, no issues. (`ruff check .` over the whole backend reports the same 17 pre-existing findings in migrations and R9.1 scripts with and without this branch.) |
| Per-file R9.2 test counts | components 9 · diagnostics 3 · mutations 20 · rate models 11 · registry builder 7 · review fixes 118 · service 37 · API 7 · TEI band 3 |
| Migration | **none** (`git diff 40f8602..HEAD -- backend/alembic` is empty); `alembic upgrade head` + `alembic check` clean on a fresh PostgreSQL database and on a copy of the real SQLite database |
| Existing validation batteries (comparable-, acquisition-, adversarial-validation) | the three JSON outputs are **byte-identical** between the R9.1 baseline worktree and the R9.2 final tree, run on identical copies of the same database (re-run at the final code) |
| Protected state | on a copy of the real database, candidate materialisation changed only `model_versions` (+4 candidate rows) and `candidate_estimates` (+4,424 rows); the 19 non-candidate model versions hash to `16a96351ab50fc1b` before and after, and the 512 `player_impact_estimates` rows to `04677fdff5754c5f` before and after, on SQLite and PostgreSQL (the protected-state fingerprint covers all 38 tables) |
| Idempotence | `materialise-skills --origin 2025-26` run twice on a fresh migrated copy of the real database, SQLite and PostgreSQL: first run writes 4 versions (632 estimates each, forecast + ability rows); second run `reused_existing: true` for all 4 |
| SQLite vs PostgreSQL | the same 4,424 candidate rows; version ids identical; **maximum absolute difference of any estimate or interval bound: 0.0** (bit-identical) |
| A store written by the *pre-fix* code | re-materialising it creates **new** versions (artifact-schema tag in the id) and leaves the old rows untouched; the default read then serves the new, registry-applicable versions |
| Reproducibility of the experiments | a clean-clone re-run of all three development experiments from the same seed on a freshly migrated copy: run ids equal the committed ones; `metrics.json`, `decisions.json`, `attrition.json`, `leakage_audit.json`, `data_snapshot.json` **byte-identical**; `fitted_parameters.json` identical except for the later-added `at_bound` field (now committed); data fingerprint `56bff0c2…` equal to the registry's |

### Phase 0 (database verification) in brief
The embedded PostgreSQL 16 used throughout is a scratch server on `127.0.0.1:54329`; the owner's primary database was never opened for writing (hash unchanged). Alembic upgrades from the R8-era revision to head, and a
migrated PostgreSQL database accepts the whole R9.2 read/write path (the compat and R9.2 tests above, and a full materialisation). One incompatibility found on the way — loading real R8-era rows into the head schema fails
on `algorithm varchar(50)` — is a property of the loader order, not of the code: the rows are loaded at revision `c5e2b7d91f34` and then upgraded.

## 3. Round 1 — statistical review (target `526aacd`)

Reviewer brief: falsify optimistic statistical conclusions. Ran: an independent Kalman filter, profile likelihoods over ρ, a selection analysis, a Monte-Carlo check of the TS variance, four stronger baselines and a
fixed-variance state-space model in the R9.1 harness (development folds only — no holdout was run), a deliberate leak through `leakage_audit`, ~40 documented numbers checked against the committed JSON,
and the pre-registration against commit `fdd7425`.

| # | Severity | Finding | Disposition | Regression test / artifact |
| --- | --- | --- | --- | --- |
| F1 | HIGH | Inferred ability is biased upward and over-confident for low-evidence players; the serving rule could not see it | **Mitigated, root cause open.** Reliability floor 0.30 (`INFERRED_MIN_RELIABILITY`), measured value still returned; selection analysis committed; limitations text states the curve is too high for rare shooters. The missing volume term is an R9.3 item; players selected on *not* playing next season remain over-predicted above the floor (+3.0…+3.8 pts 3P; stated in the docs) | `TestInferredFloor`, `results/selection_check.json` |
| F2 | HIGH | `ols_history` is not the strongest simple baseline; against attempts-aware regressions the 3P/FT gains fall below the 2% floor | **Fixed (claim restated).** Headline now "beats a regression that ignores attempts by 4–5%; beats an attempts-aware one by about 1.5%, below the pre-registered floor"; "strongest baseline" retracted; baseline challenge committed | `results/baseline_challenge.json`; `test_pi_diagnostics` |
| F3 | MED-HIGH | 3P evidence marginal and fragile (fails the rule at the 100-attempt cut; would not survive a multiplicity correction) | **Fixed (docs).** Stated plainly in the validation document; 3P described as probable and small | `shooting-validation-results.md` §3, §6, §8 |
| F4 | MEDIUM | Plug-in sampling variance uses the observed rate and biases levels | **Disclosed, not changed** (MAE unchanged; fixed-variance diagnostics added and committed) | `StateSpaceFixedVariance` diagnostics |
| F5 | MEDIUM | φ not identified as sampling dispersion; ρ sits on its bound while `converged=True` | **Fixed (reporting).** `StateSpaceParams.at_bound`; φ described as a nuisance factor | `TestReliabilityFieldsMeanWhatTheyDo::test_a_parameter_on_its_bound_is_reported` |
| F6 | MEDIUM | Circular TS-agreement claim; FT holdout coverage 0.853 outside the band, unstated | **Fixed (docs)** | validation §5; methodology |
| F7 | MED-LOW | `weight_on_player_record` and `effective_attempts` did not mean what the records said | **Fixed (code).** True filter weight; effective attempts with the player's own variance (1.000 for one-season players) | `test_the_weight_is_the_filters_response_to_the_evidence`, `…effective_attempts_equal_the_attempts_he_took` |
| F8 | MED-LOW | Leakage audit blind to panel-construction leaks and to the ability path | **Fixed (structural guards + test).** Outcome columns hidden from predictors (`features_only`); panel-invariance test kills the wrong-key merge | `test_a_predictor_cannot_see_the_outcome_when_forecasting`, `test_the_panel_build_is_invariant_to_later_seasons` |
| F9 | MED-LOW | Inferred and projected serving bars inconsistent | **Fixed (docs)** | `multidimensional-skill-coverage.md` §1 |
| F10 | LOW | Coverage criterion in the advance rule nearly vacuous | **Fixed (docs)** | validation §5 |
| F11 | LOW | No league-environment term in any mean curve | **Disclosed** (methodology §7) | — |
| F12 | LOW | Role covariates imputed as league average / cutoff-season role applied to all history | **Not fixed; disclosed** (added after Round 3 found it undocumented) | methodology §7.2 |
| F13 | LOW | Documentation inconsistencies (amendment count, rounding MAE, mixed models in a table, …) | **Fixed** | five amendments everywhere; numbers re-checked |

Pre-registration and holdout integrity were also reviewed: the embedded pre-registration matched `fdd7425` byte for byte; the ledger times showed amendments 1–2 were committed before the first holdout read and 3–4 after
(each only lowers a claim or adds a diagnostic); `ts_pct` is recorded as a **re-read**. The reviewer found no use of holdout information in design. Limits it noted: the order of development runs relative to the
pre-registration commit cannot be externally proven from committed files (mitigated: `run.json` files are now committed).

## 4. Round 2 — engineering and data review (target `526aacd`)

Reviewer brief: engineering/data integrity of everything the service reads and writes. Ran: 103 R9.2 tests on SQLite and 106 on PostgreSQL, the 145 R9.1 modeling tests, the R9.2 tests in reverse order, alembic
checks, read-only real-data probes, a mutation run (9 mutants: 4 killed, **5 survived**), mid-write failure on PostgreSQL, and 21 documentation claims checked.

| # | Severity | Finding | Disposition | Regression test |
| --- | --- | --- | --- | --- |
| F1 | HIGH | Re-training / re-materialising any ability-carrying model failed (ability rows pooled with forecasts); the idempotence guarantee was false | **Fixed.** Forecast and ability rows compared separately (later: every column, see R3-N1) | `TestIdempotence` (3 tests) |
| F2 | CRITICAL | The registry's evidence label attached to any stored model with a matching method name | **Fixed** (then hardened in Round 3, N2–N4) | `TestRegistryEvidenceIsNotBorrowed` |
| F3 | CRITICAL (preconditioned) | A model that *failed* its evaluation was served once its evidence went stale | **Fixed.** Stale can only lower a status | `TestStaleCannotLiftAFailure` |
| F4 | HIGH | Naming `model_version` bypassed the registry's `withheld` tier | **Fixed** | `TestWithheldMeansNobody` |
| F5 | MEDIUM | `weight_on_player_record` wrong when the latest evidence is not in the cutoff season | **Fixed** (= R1-F7) | as R1-F7 |
| F6 | MEDIUM | Measured path said "no attempts" for players who did attempt (rounding hides 1–3 attempts) | **Fixed.** "undetermined", never "no attempts" (0 false claims against 573 exact totals) | `test_attempts_the_rounding_hides_are_undetermined_not_none` |
| F7 | MEDIUM | OpenAPI-schema test order-dependent | **Fixed** (fresh-process test) | `test_the_openapi_schema_reflects_the_flag_at_process_start_in_a_fresh_process` |
| F8 | MEDIUM | Inferred sample block misleading (stored 3-season window, used 10 seasons) | **Fixed.** History exposure stored and served | `test_stored_ability_exposure_covers_the_whole_history…`, `…served_sample_block…` |
| F9 | MEDIUM | Guard tests partly tautological; 5 mutants survived (incl. ability intervals at 50% labelled 80%) | **Fixed.** Guards plant real defects; tests for each survivor (`test_pi_mutations`, 20 tests) | `test_pi_mutations.py` |
| F10 | MEDIUM | Trade evaluator treats the TEI band as a calibrated 80% interval | **Partly fixed.** Calibration note added to the trade payload and band texts derived from `TEI_BAND_CALIBRATION`; σ **not** recalibrated (R9.3) | `test_tei_band_calibration.py`, `tei-band-honesty.test.ts` |
| F11 | MEDIUM | Missing prior ledger silently disabled the re-read guard | **Fixed** | `test_a_missing_prior_ledger_refuses_a_holdout_read` |
| F12 | LOW-MED | N+1 queries | **Partly fixed:** roster of 15 all kinds 271 → 146 statements (one estimate query per player/quantity/kind remains; 49 ms) | `TestBatch` |
| F13 | LOW | Refusal-text inaccuracies (TS > 1, GP = 0, unknown version, interval above 1) | **Fixed** | `TestMeasuredEdgeCases`, `TestIntervalsStayInTheFeasibleRange` |
| F14 | LOW | Duplication/hygiene (docstring, unused constant, missing make target, `*.pkl`) | **Fixed** except the three-place percentage computation (disclosed, unchanged) | — |

## 5. Round 3 — adversarial integration and reproducibility (target `f02948d`)

Reviewer brief: act adversarially against a clean clone. Ran: 175 tests on SQLite and on PostgreSQL; `materialise-skills` twice per backend; a clean-clone re-run of a development experiment; gating, stale, withheld
and status probes; failure injection on both backends; two concurrent materialisations; API abuse (23 requests); the measured path against the 573 exact 2025-26 totals; a survey of all 573 current players; **26 mutants**
(20 killed, **6 survived**). It also verified every Round 1–2 fix (all verdicts above come from that table).

| # | Severity | Finding | Disposition | Regression test |
| --- | --- | --- | --- | --- |
| N1 | HIGH | Fixes do not reach a database materialised before them: re-materialisation "reused" defective rows (it compared only `estimate`) and upgraded their evidence label | **Fixed.** Artifact-schema tag in the stored configuration (so the id changes); reuse compares every stored column and refuses without rewriting the evaluation record | `TestReuseComparesEveryColumn` (6 cases) |
| N2 | MED-HIGH | Registry evidence borrowable via self-reported `validation_metrics` (a caller-supplied `evaluation=`) | **Fixed.** Provenance keys stripped; `applies_to` compares the model's own configuration and data fingerprint | `test_a_caller_supplied_evaluation_cannot_assert_provenance`, `test_the_registry_checks_the_models_own_configuration` |
| N3 | MEDIUM | `promotion_readiness` borrowed evidence by method name | **Fixed** (uses `applies_to`) | `test_promotion_readiness_does_not_credit_an_unevaluated_model` |
| N4 | MEDIUM | The newest candidate wins the default read regardless of configuration or evidence | **Fixed.** Prefer the version the registry applies to, then newest | `test_a_newer_unevaluated_candidate_does_not_become_the_default` |
| N5 | MEDIUM | Status alone decided "validated"; `is_active` ignored | **Fixed.** Both required; inconsistent pair and unknown statuses are unservable | `TestActiveRequiresBothFields` (10 cases) |
| N6 | MEDIUM | Measured counts served with false precision (87/1,666 records differ from exact totals; rates off by up to 0.00094) | **Fixed by disclosure and by serving the published rate.** Bounded rows serve the *published* rate (exact to its precision) and the attempt **range**; the nearest-tuple attempts remain as the central value, labelled bounded. Using the stored `totals` rows where they exist is possible but not done (they exist for one season only) | `test_a_bounded_row_shows_the_published_rate_and_the_attempt_range` |
| N7 | MEDIUM | No plausibility bounds (MPG 2289 served; AST/TO 400 served; negative attempts called "no attempts") | **Fixed** | `test_implausible_values_are_refused`, `test_a_negative_count_is_an_integrity_failure…` |
| N8 | LOW | `as_of` validated but not normalised | **Fixed** | `TestRequestsAreNormalised` |
| N9 | LOW | Concurrent materialisations end in a raw `IntegrityError` | **Fixed** (loser reuses) | `test_the_loser_of_a_creation_race_reuses_the_winner` |
| N10 | LOW | CLI ignored unknown flags; `materialise-skills --help` materialised | **Fixed** | `test_unknown_flags_write_nothing_and_help_prints_usage` |
| N11 | LOW | New order-dependent test | **Fixed** (module fixture creates the version) | reverse-order run of the R9.2 service/mutation/review/API files: 182 passed |
| N12 | LOW | `status_from_stored` failed open (`failed`, `STALE:…`, trailing spaces) | **Fixed** (unknown → failed) | `TestStatusParsingFailsClosed` (11 cases) |
| N13 | LOW | Wording (floor message rounding, "0 games played", doc range "+1.2 to +3.5") | **Fixed** | docs; `TestMeasuredHonesty` |
| N14 | LOW | Weak tests behind the six surviving mutants | **Fixed:** a test for each (below) | `TestSurvivingMutantsNowHaveTests` + the rewritten rounding guard in `test_pi_mutations.py` |

Surviving mutants and the tests that now kill them: **M02** (fingerprint comparison dropped) → `test_the_registry_checks_the_models_own_configuration`; **M18** (no-evidence rule keyed on the window) →
`test_no_evidence_is_judged_on_the_history_the_model_used_not_the_window`; **M19** (league variance in effective attempts) → `test_a_one_season_players_effective_attempts_equal_the_attempts_he_took`;
**M20** (batch cache never cleared) → `test_the_batch_cache_is_cleared_…`; **M22** (stored exposure = window) → `test_stored_ability_exposure_covers_the_whole_history_…`; **M25** (rounding note never emitted) →
the rebuilt `guard_rounding_ambiguity_is_disclosed` with an example that is genuinely bounded and has a nonzero minimum attempt count.

Not changed after Round 3 (disclosed): the plug-in variance bias interacts with the reliability floor (a low-reliability poor shooter can pass the floor at ≈ 109 attempts); the trade evaluator's σ is not recalibrated;
N+1 query pattern (146 statements for a 15-player roster, all kinds).

## 6. Round 3 retest (independent re-verification of the Round 3 fixes, target `ada003c`)

Reviewer: a fresh independent sub-agent, given the Round 3 report and scripts and told to try to show the fixes do **not** work and to hunt regressions. It worked from a clean clone of `ada003c`, read the diff
`f02948d..ada003c`, re-ran every Round 3 reproduction on SQLite and PostgreSQL (materialisation twice per backend; a forced creation race; two concurrent runs; failure probes; 26 hand-edit tamper cases; 573-totals check; a
54-player real-data sample, all kinds), ran 215 tests, and planted **57 mutants** (the 6 old survivors + 51 new ones aimed at the new code). Disclosure from the reviewer: the disk filled during its run, so it could
not run the PostgreSQL mutation runs or the PostgreSQL suites after that point, and it did not repeat rollback injection, the full suite, the frontend tests or any experiment.

**Verdicts on the Round 3 findings:** fixed — N3, N4, N5, N7, N8, N9, N11, N12, N13; mostly fixed — N10 (a valueless `--origin` was ignored); **partly fixed — N1, N2, N6**. All six old surviving mutants were **killed**; of 51 new
mutants 30 were killed and **21 survived**.

**New findings and what was done** (all fixed in the commit after `ada003c`, with regression tests):

| # | Severity | Finding | Fix | Test |
| --- | --- | --- | --- | --- |
| R4-1 | **HIGH** (borderline critical) | A wrong number served as measured: bounded true-shooting records reused the *free-throw* attempt range (50 of 573 TS records showed `attempts_min/max ≈ 645–649` beside `attempts = 1,741`, with a limitation sentence in the wrong units). The rate itself was right | range computed in true-shooting attempts (FGA + 0.44·FTA); the makes of a bounded record are no longer served (they were the nearest-tuple figure, off by up to 1) | `TestBoundedTrueShootingIsServedInItsOwnUnits` (2) |
| R4-2 | MEDIUM | N1 not closed: a pre-fix version stayed nameable *with registry evidence attached* because `applies_to` ignored the artifact schema | `applies_to` requires today's `artifact_schema` and the model's *own* data fingerprint (not only the experiment's) | `TestEvidenceNeedsTheCurrentArtifactSchemaAndOwnData` (4) |
| R4-3 | MEDIUM | N2 residuals: a caller could assert `evaluation_status`; a model trained under another configuration but given an experiment dir was labelled `holdout_confirmed`; a caller `evaluation=` on an existing version overwrote its record | a caller can only record "failed" or "unevaluated"; a configuration mismatch downgrades a confirmation to unevaluated (with a note) and never lifts a failure; a reuse rewrites the evaluation only when it came from an experiment directory | `TestACallerCannotClaimAStatus` (4) |
| R4-4 | LOW | Reuse ignored `output_kind`, `season`, `target_season` (the docs said "every stored column") | compared (with level) | `TestReuseComparesTheRowsMeaning` (3), `TestReuseComparesEveryColumn` (6 columns) |
| R4-5 | LOW | The race handler's safety rested on one untested `if winner is None: raise` | test: a non-race `IntegrityError` is raised, nothing partial is returned | `TestRaceHandlerOnlyForRaces` |
| R4-6 | LOW | A valueless `--origin` / `--evidence-dir` was ignored and a full materialisation ran at the default | refused with usage (exit 2), nothing written | `test_valueless_cli_options_are_refused` |
| R4-7 | LOW | Served makes for bounded records carried no range or note | no makes are served for bounded records; the limitation says so | as R4-1 |
| R4-8 | LOW | 21 surviving mutants: bounded-TS/3P paths, several reuse/bound checks untested | tests added for each family (below) | |
| R4-9 | LOW (docs) | "No self-reported flag is trusted" overclaimed; the "own data fingerprint" named was the experiment's; the roster endpoint's additive key undocumented; the QA report was not in the reviewed commit | docs corrected; the key documented; the report is committed | |

**Author's re-test of the surviving-mutant families:** 18 mutants re-derived from the reviewer's survivors and aimed at the *fixed* code (schema tag dropped from the id; reuse skipping `interval_low`, `interval_level`, or the
text columns; `applies_to` ignoring the interval level, the artifact schema, the model's own fingerprint, or case-folding the stale marker; bounded TS in free-throw units; bounded rows serving makes; the MPG and AST/TO lower bounds
dropped; the race handler swallowing a non-race error; reuse rewriting a caller's evaluation; caller status not coerced; configuration mismatch keeping a confirmation; the floor message rounding up to the floor; default
resolution ignoring the registry) — **16 were killed at once, 2 survived** (`interval_low` and `interval_level` in the reuse comparison); the two missing test cases were added and both mutants then died, so **18 of 18 are now killed**.
Three more families got direct tests (a swapped player set of equal size, reuse counting only forecast rows, forecast rows carrying `history_exposure`). **Not re-run as mutants, stated plainly:** the reviewer's "default resolution
prefers the *oldest* applicable version" (two simultaneously applicable versions at one cutoff cannot be built with the synthetic league; the rule is covered only for applicable-vs-not), and its "`promotion_readiness` credits
non-applying evidence" mutant (the readiness tests assert nothing is credited for a non-applying model but were not re-mutated).

**After the fixes:** full backend suite on SQLite **3,188 passed, 19 skipped, 0 failed**; R9.2 + modeling + PostgreSQL-compatibility tests on PostgreSQL 16 (`tests/unit/test_pi_*`, the API and TEI-band tests, `test_modeling_*`,
`tests/integration/test_postgres_compat.py`) **393 passed, 1 skipped**; `ruff` and `mypy` clean; materialisation twice on a fresh copy of the real database: 4 versions, second run reused all 4, existing 19 model versions and 512 estimates
hash-identical; a 60-player real-data sample of every quantity and kind served with the registry evidence attached to the right models (3P/FT projections `holdout_confirmed`, TS projection `benchmarked`, all inferred `development_only`)
and no attempt range violating `min ≤ attempts ≤ max`.

## 6b. Final verification pass (Round 4, target `206672b`)

The Round 3 retest had found a high-severity defect, so the protocol required the relevant review to be repeated. A further fresh independent reviewer re-verified the fixes against a clean clone of `206672b`: all 573
players with exact 2025-26 totals, tamper tests on every stored column, materialisation twice on SQLite *and* PostgreSQL (same version ids, second run reuses all four; an `ada003c` store reuses identically), a 60-player sample of
every quantity and kind on both backends (947 records, no exceptions, no number contradicting its limitation text, registry evidence attached to the right models), and **46 more mutants**.
It could not run the full suite, the PostgreSQL suites, rollback injection or the frontend (the author ran those).

* **Result: no critical or high defect, no gating bypass, no wrong number served as measured.** R4-1, R4-3, R4-4 and R4-7 verified fixed; R4-2, R4-5, R4-6 verified with residuals (below).
* **Measured path against the exact totals (573 players):** attempt range always satisfies min ≤ attempts ≤ max (0 violations in 3P, FT, TS); the true attempts lie inside every served range (0 misses); every ranged record carries
  the limitation (0 without); no bounded record serves `makes` (0 of 104); maximum |served rate − true rate| 0.000500 (3P), 0.000490 (FT), 0.000534 (TS — one player whose provider-published FGA differs by one from the totals; pre-existing, no range or note).
* **Mutation:** 46 planted, 37 killed, 8 survivors + 1 equivalent. All survivors are test gaps, not defects in the shipped code.

| # | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| R5-1 | MEDIUM | When the registry evidence does not apply (a pre-schema store, named explicitly, with the opt-in), the inferred output was labelled with the version's stored status — which could read `holdout_confirmed`, stronger than the registry's own cap of `development_only`; `promotion_readiness` disagreed | **Fixed:** an inferred output is never labelled above `development_only` (test `test_an_inferred_label_is_capped…`) |
| R5-2 | LOW | A model stored `unevaluated` because its configuration did not match still carries the holdout run id and `confirmed` in its stored decision | Disclosed (the label and the served status are correct; the artifact fields are traceability) |
| R5-3 | LOW | Empty-string CLI values (`--origin ""`) not refused; `train-candidate --experiment` with no value silently ignored; `--evidence-dir /nonexistent` materialises without evidence | **Fixed for `materialise-skills` empty values** (test); the other two disclosed |
| R5-4 | LOW | 25 of 50 bounded true-shooting ranges narrower than one attempt printed as "between 563 and 563"; the TS text said "makes are not given" although TS has no makes | **Fixed** (one decimal when the range is sub-attempt; the makes clause only where makes exist) |
| R5-5 | LOW (pre-existing, safe direction) | `train-candidate --experiment <sensitivity directory>` rewrites a legitimate version's evidence record (e.g. to `failed_development_rule`, which *refuses* its projections); re-running with the holdout directory restores it | Disclosed; not a gating bypass |
| R5-6 | LOW | Test gaps behind the survivors: `applies_to` accepting a missing own fingerprint; caller status coercion letting `development_only` through; the race path rewriting a caller's evaluation; text-column comparison for ability rows only / skipped for falsy values; range-text precision | **Tests added** for the first two, the range text and the CLI; the rest are listed as remaining test gaps |
| R5-7 | LOW (pre-existing) | One TS record (provider FGA differs from the totals by one) is off by one attempt with no note | Disclosed |
| R5-8 | LOW (docs) | Placeholders still in the sign-off and executive report | Filled |

## 7. Things the reviews tried and failed to break

Kalman filter and TS variance formula (independent implementations agree); temporal discipline (as-of view, training pairs, `forward_fit`); reconstruction identities and the 573/573 range check; every status ×
`is_active` × opt-in combination for a candidate; withheld-means-nobody for every stored version; rollback after a DB error, an exception after all rows were flushed, and a `KeyboardInterrupt` at commit, on both
backends; SQLite-vs-PostgreSQL bit-identity; read-only service (run against `mode=ro` connections); API input validation; the wrong-key panel merge (caught by the invariance test); percentage-point inputs refused;
no secrets in the diff; committed results are aggregates only.

## 8. Residual limits of this QA

* The reviewers are AI agents with a shared model family; none of the rounds involved a human statistician.
* No round re-ran a holdout evaluation (by rule); the holdout numbers are the ones produced once by the author's run.
* The frontend was changed only in wording; it was verified by its own type/lint/unit tests, not by a visual review.
* Nothing was tested against the owner's production configuration or data volume; the owner-side activation checkpoint (a PostgreSQL run on a copy of the real database) remains the owner's step.
