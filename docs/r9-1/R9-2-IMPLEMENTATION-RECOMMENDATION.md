# R9.2 — Reusable Player Skill Engine: implementation recommendation

Evidence base: R9.1's backtests ([candidate-experiment-results.md](candidate-experiment-results.md)), the incumbent
assessment ([current-model-assessment.md](current-model-assessment.md)) and the GOAT audit
([goat-feature-readiness.md](goat-feature-readiness.md)). Everything below that is a *result* cites one of them; everything
else is a recommendation and is labelled so.

## 1. What R9.1 teaches R9.2

1. **Most box-score rates are so stable that the measured value is already close to the best estimate.** Year-over-year correlation
   among rotation players: rebound share 0.95, assist share 0.90, usage 0.88, PIE 0.85 — and 0.59 for true shooting. For the stable
   ones the best simple forecast improves on raw persistence by only 5–6 % (0.0006–0.0016 in the rate's own units); manufacturing an
   "inferred skill" for them would be decoration.
2. **Where sampling noise matters, correction helps — and simple correction is enough.** For true shooting, regressing last
   season's value toward the mean cuts forecast error 11 % against raw persistence, and a four-parameter regression on the player's
   own recent history takes a further 5 % (MAE 0.0366 → 0.0326 → 0.0310). **None of the more elaborate candidates (empirical-Bayes
   shrinkage, ridge on history, ridge on history plus box-score features) beat that regression by a margin the pre-registered rule
   accepts — 0 of 18 candidate–target pairs.** R9.2 should start from the regression-with-history baseline, treat it as the bar, and
   adopt added structure only where it demonstrably clears it. Empirical-Bayes *inference* (as opposed to forecasting) is still the
   right framework for reporting an ability estimate with a reliability, because it makes the sample-size dependence explicit — but
   R9.1 provides no evidence that it forecasts better than the plain regression, and says so.
3. **The incumbent TEI is not a base to build skills on.** On its own documented target it forecasts no better than "regress last
   season's proxy toward the mean with an age curve" and is 11 % worse than a regression on the player's own history (holdout); it
   correlates 0.96 with same-season PIE; it says little about individual rates (19–202 % worse than the history regression as a
   forecaster of true shooting, assist, rebound and usage); and its shipped likely-range covered 57 % of holdout outcomes against a
   nominal 80 %. R9.2 should treat TEI as a consumer-facing summary *derived from* skills, not as their source — and R9.2's first
   task should make the likely-range honest.
4. **Availability is the largest unmodelled uncertainty.** About 46 % of players scored at a cutoff are not in the next season's
   rotation population (a third do not appear at all). A skill engine that forecasts only "given they play" omits the thing a trade
   decision hinges on.
5. **Defence is the highest-value gap and cannot be tested yet.** The only candidate inputs (`defense/overall`, `hustle`) exist for
   one season. No stability, no out-of-time test is possible until a bounded backfill is approved. Cross-sectionally, 72–79 % of
   hustle counts per 36 minutes are already explained by the box score; defended-FG% plus-minus is less redundant (R² 0.27–0.43).
6. **The infrastructure can say yes and no.** Any new skill is a `TargetSpec` + a `Predictor`; it inherits the as-of discipline, the
   folds, the baselines (including `ols_history`), the paired comparisons, the interval calibration, the candidate lifecycle and the
   pre-registered holdout protocol. A planted-signal test shows a real effect advances and is confirmed; the real data showed none
   did.
7. **Be wary of conclusions that depend on the cut.** The one marginal result (box-score ridge on the impact proxy, −2.8 %) advances
   at a 250-minute evaluation cut but not at 500 or 1,000 target-season minutes. R9.2 should pre-register its evaluation
   population and report sensitivity, not choose it afterwards.

## 2. Candidate skill dimensions (recommendation)

Dimensions are named by **what is measured**, not by a rating. Status = what the evidence supports *today*.

| # | Dimension | Measured from | Inferred ability? | Projected? | Data & history | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **Shooting efficiency** (true shooting) | `TS_PCT`, attempts | **yes** — EB shrinkage on attempts (reliability by attempts) | yes (backtested: ≈ the history regression, which is the bar; −11 % vs raw persistence) | NBA.com, 10 seasons | **ready to build** |
| 2 | **Three-point accuracy** and **free-throw accuracy** | makes/attempts | **yes** — beta-binomial; Pivot already ships an ad-hoc shrunk `fg3_pct` (constant 300 attempts) | yes | NBA.com, 10 seasons | **ready** (replace the constant with an estimated prior) |
| 3 | **Shot profile** (rim / mid / corner / above-break shares) | `shooting/by_zone` | shares persist; accuracy needs shrinkage | later | **1 season only** | needs backfill |
| 4 | **Playmaking volume** | `AST_PCT` | measured ≈ inferred (r 0.90) | marginal gain | NBA.com | build as *measured + role-adjusted*, no model needed |
| 5 | **Rebounding** | `REB_PCT` (off/def) | measured ≈ inferred (r 0.95) | marginal | NBA.com | as above |
| 6 | **Offensive role / usage** | `USG_PCT`, minutes | measured; role is a choice, not a skill | yes (r 0.88) | NBA.com | measured + projected only; **never an "ability"** |
| 7 | **Availability / durability** | games played, minutes per game | inferred (games-played propensity) | **yes — highest value** | NBA.com GP, 10 seasons; injury **current only** | build next |
| 8 | **Defensive contribution** | `defense/overall` (defended FG% vs expected), `hustle`, rim protection | unknown | unknown | **1 season** | needs backfill; may fail validation — report if so |
| 9 | Shot creation / off-ball | `tracking/possessions`, play types | unknown | unknown | 1 season; 77–90 % redundant with usage/assists/minutes | descriptive features for R10, not a skill, until stability is shown |

Explicitly **not** skill dimensions: clutch performance (median 46 clutch minutes a season), contract value (R13),
on-court net rating (team-confounded), a single overall rating.

## 3. Measured / inferred / projected for each dimension

Every record carries `output_kind` (R9.1 `PlayerIntelligenceRecord`):

* **measured** — the observed value with period, sample (games, minutes, attempts) and source. Needs only data integrity.
* **inferred** — an estimate of underlying ability at a stated information cutoff, e.g. posterior mean and interval of
  a player's true-shooting ability after pooling toward an age/role-adjusted league prior. Needs: (a) a **reliability
  statement** (the share of between-player variance that is signal at this sample size — computed from the fitted variance
  components, and reported as a function of attempts), (b) a stability check, (c) interval calibration evidence.
* **projected** — the forecast of next season's observed value. Needs a rolling-origin backtest that beats the baselines.

A dimension may omit a kind; the record then says so (`limitations`). A dimension whose reliability at a typical player's
sample is below an agreed floor is shown **as a measurement with its sample size, not as an attribute of the player.**

## 4. Methods to implement, in order of evidence

1. **Empirical-Bayes / hierarchical shrinkage** (already validated as `eb_shrinkage`; generalise): estimate the league prior
   *and its variance components* from the data (method of moments, checked against marginal likelihood); beta-binomial for
   accuracy rates, normal–normal for per-minute rates; partial pooling by age and by role strata derived from stats (not from
   labels that are only available for current players). Output: posterior mean, posterior SD, reliability. Report the shrinkage
   constant with its own uncertainty — R4's ad-hoc 300-attempt constant was estimated three ways (272–326) and should be
   replaced by the estimate and recorded.
2. **Age curves** estimated from the 10-season panel (a mixture of delta-method and fixed-effect approaches; survivorship bias
   from players leaving the league must be handled or stated).
3. **Multi-season windows with learned decay** instead of the fixed 0.7 used by TEI; choose by out-of-time error.
4. **Availability model**: games-played share as a function of age, history and minutes, as a count model with overdispersion;
   explicitly model "does not appear" (the 30 %+ attrition).
5. **Defence (conditional on the backfill):** shrink defended-FG% plus-minus on defended attempts, adjust for shot-mix, test
   stability across seasons, and test whether it adds out-of-time information about *team* defensive rating beyond what
   blocks/steals/rebounds provide — using the team-level gate, with the circularity caveats in the evaluation doc.
6. **Do not** begin with gradient boosting, neural networks or learned embeddings. R9.1 found that a ridge on 20 features did not
   clear a four-parameter regression on the same history (2.8 % at best, fragile); there is no evidence that more flexibility adds
   more than noise, and the product needs each number to be explainable.

## 5. Shared backend data contract (proposal)

Implement the R9.1 contract as the one return type for every consumer.

```
PlayerIntelligenceRecord {
  player_id            canonical Pivot id (never a provider id)
  quantity             "ts_pct" | "three_point_accuracy" | "availability" | …
  unit, value, output_kind ∈ {measured, inferred, projected}
  period               {season, season_type, as_of_season}      # information cutoff
  target_period        projected only
  interval             {low, high, nominal_level, method, calibration?{folds,n,empirical_coverage,evaluation_ref}}
  sample               {games, minutes, attempts, unit}
  model                {name, version, status, evaluation_status, evaluation_ref}   # absent for measured
  provenance           {sources[{provider, dataset, retrieved_at, identity_confidence}], data_snapshot_id, code_commit}
  assumptions[], limitations[]
}
```

Persistence: an **append-only** table (`player_intelligence`) keyed by `(player_id, quantity, output_kind, period, model_version)`,
fed by a promoted model version; `candidate_estimates` is the staging area for non-promoted versions and already has the
columns. Retrieval **as of a past date** is a requirement (R9.3 and reproducibility). Service: `PlayerIntelligenceService.get(
player_id, quantities, as_of, kinds, model_version=None)`; one endpoint family under `/api/v1/players/{id}/intelligence`; no
frontend change in R9.2 beyond what a consumer chooses to adopt.

## 6. Validation requirements per kind (the acceptance bar)

| Kind | Required before a quantity is served |
| --- | --- |
| measured | integrity checks pass (R9.1 `validate_panel` + provider reconciliation); reproducible from a data snapshot |
| inferred | year-over-year stability reported; reliability vs sample size reported; interval coverage measured out of time; no claim stronger than "estimate of ability from box-score evidence" |
| projected | rolling-origin backtest beats the best baseline per the pre-registered rule on development folds **and** is confirmed on a locked holdout; interval coverage within the pre-registered band |

## 7. Dependencies on GOAT data

| Dependency | Needed for | Blocking? |
| --- | --- | --- |
| Backfill of `defense/overall`, `hustle`, `shooting/by_zone`, `tracking/possessions` for 2015-16 → 2025-26 (≈ 260 requests) with an **append-only** retrieval history | defence, shot profile, any new-family stability test | for dimensions 3, 8, 9 only |
| High-confidence identity links (+ the explicit data-corroboration rule for the 14 medium links) | any use of provider rows in training | yes, for those dimensions |
| Nothing from GOAT | dimensions 1, 2, 4–7 (NBA.com series) | **no** |

Do **not** start the backfill until the owner approves the request count; it is cheap (≈ 30 s at 600/min) but must be
frozen once and recorded with its own provenance.

## 8. Connections to R10–R14

* **R10 archetypes** consume the *measured/inferred vector with reliabilities* — clusters should be formed on low-noise
  dimensions (usage, rebounding, playmaking, shot profile), with each player's cluster membership carrying its uncertainty.
* **R11 roster needs** consume skills aggregated to a roster, with the aggregate's uncertainty propagated (R9.1's cluster
  bootstrap and the interval model are the pattern).
* **R12 fit** needs *projected* skills for the destination context; the availability model matters as much as the skills.
* **R13 comparable trades** needs skills *as of the trade date* — the as-of retrieval requirement above is what makes that
  possible without hindsight.
* **R14 optimiser** needs calibrated uncertainty, not point estimates; the interval-coverage discipline is the prerequisite.

## 9. Recommended sequence and exit criteria

| Step | Deliverable | Exit criterion |
| --- | --- | --- |
| **1. First substantive task: shooting efficiency end-to-end** | `ts_pct` and three-point accuracy as measured + inferred + projected, served through the contract, with reliability-vs-attempts, stability, calibrated intervals | the projected value is no worse than the `ols_history` bar (and any structure added beyond it clears the pre-registered rule and the holdout); the reliability curve is published; the interval's out-of-time coverage is stated beside it; `PlayerIntelligenceRecord` returned for every rotation player |
| 2. Estimate variance components properly | prior and shrinkage constants fitted, not fixed | shrinkage constants recorded with uncertainty |
| 3. Availability model | games-played propensity + "does not appear" | out-of-time error beats persistence of last season's GP; calibration of the appearance probability measured |
| 4. Append-only provider snapshot history (R8.3 table change) + owner-approved backfill | frozen, hashed historical families | fingerprint recorded; no overwrite on refresh |
| 5. Defence feasibility study | stability and incremental-information test | a written go/no-go with numbers; "no-go" is an acceptable outcome |
| 6. Promotion procedure | audited, reversible switch; rollback = switch back | re-runs the comparable / acquisition / adversarial batteries and the R3-gate test before any serving change |
| 7. Role-aware modelling and aging | optional improvements | only if they pass the same rule |

## 10. What not to do in R9.2

Build a single composite rating; show an interval without its coverage; use contract values or current injury status as skill
inputs; train on medium-confidence provider links silently; or replace the live TEI before the promotion procedure and the
existing validation batteries have been re-run on the replacement.
