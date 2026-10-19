# R9.2 — Shooting: validation results

Everything here is read from the committed artifacts in [`results/`](results/) (aggregates only: no per-player rows). The protocol was written and
committed before the first development run ([shooting-intelligence-methodology.md](shooting-intelligence-methodology.md) §6); numbers below are
**out-of-time**: each fold fits only on seasons at or before its cutoff and forecasts the next, and a falsification audit (later seasons destroyed:
statistics permuted, identities and teams re-assigned, a third of rows deleted) left every fold's scored players, inputs, forecasts and intervals
**bit-identical** at every one of the 5 development and 2 holdout origins, for all three targets (21 audits, all passed).

## 1. Headline

| Quantity | Recommended projection | vs the strongest simple baseline (`ols_history`) | Holdout | Status served |
| --- | --- | --- | --- | --- |
| Three-point accuracy | `state_space` | **−4.4%** MAE (development, 5/5 folds) | **−3.2%**, 95% CI [−0.00195, −0.00010], both folds improve — first read of this holdout | experimental |
| Free-throw accuracy | `state_space_role` | **−5.3%** (4 of 5 folds improve; §3) | **−6.5%**, CI [−0.00446, −0.00130], both folds — first read | experimental |
| True shooting | **`ols_history` (a benchmark)** | candidates −2.6% / −2.8% in development (5/5 folds) | **−1.9%**, CI [−0.00130, +0.00018] — **not confirmed**; and a *re-read* of a holdout R9.1 had read | benchmarked — **no improvement claimed** |

Three-point and free-throw accuracy: a method that weights each season by the attempts behind it beats the best simple regression by about 3–6%, and
the improvement holds on seasons that were reserved and never read. True shooting: the same idea helps in development but is **not** confirmed
out of time at a convention-sized bar; R9.2 therefore does not claim it, and serves the regression. Neither of R9.1's ridge candidates (`ridge_history`, `ridge_boxscore`) cleared the rule for any of the three targets (the best, `ridge_boxscore` on free throws, is −2.6% with a season-level
interval that includes zero); R9.1's empirical-Bayes shrinkage advanced only for three-point accuracy, where attempts vary most. The existing TEI, recalibrated onto each target's scale, is the
*weakest* sensible forecaster of all three (8–65% worse than `ols_history`): TEI says little about these skills.

## 2. Setup (what each number is)

* **Target**: the next season's *observed* rate — true shooting %, three-point %, free-throw % — for players in the **evaluated population**: scored at
  the cutoff (≥ 200 minutes over three seasons), and in the target season ≥ 500 minutes **and**, for three-point and free-throw accuracy, ≥ 50 attempts.
  Same rows for every model. **This is a forecast conditional on playing** (§7).
* **Folds**: development origins 2018-19…2022-23 (training uses ≥ 2 earlier transitions); holdout origins 2023-24 and 2024-25.
* **Metric**: MAE in the target's units (a 0.001 difference = 0.1 percentage point). Paired differences on identical rows with a player-cluster
  bootstrap interval **and** a fold-level t-interval (seasons are a sample too).
* **Rule** (unchanged from R9.1, pre-registered): advance iff both intervals are below 0, ≥ 4 of 5 folds improve, MAE ≥ 2% lower, and the nominal-80%
  interval's pooled coverage lies in [0.75, 0.85].

## 3. Development folds (n = 1,367 three-point, 1,294 free-throw, 1,591 true-shooting evaluations)

MAE (lower is better); relative = vs `ols_history`; ✔ = advanced under the rule.

| Model | 3P MAE | rel. | FT MAE | rel. | TS MAE | rel. |
| --- | --- | --- | --- | --- | --- | --- |
| `state_space_role` | 0.03286 | −4.2% | **0.04191** | **−5.3% ✔** | **0.03014** | **−2.8% ✔** |
| `state_space` | **0.03279** | **−4.4% ✔** | 0.04242 | −4.1% ✔ | 0.03021 | −2.6% ✔ |
| `beta_binomial` | 0.03284 | −4.3% ✔ | 0.04302 | −2.8% | — | — |
| `eb_shrinkage` (R9.1) | 0.03310 | −3.5% ✔ | 0.04315 | −2.5% | 0.03130 | +1.0% |
| `ridge_boxscore` (R9.1) | 0.03403 | −0.8% | 0.04309 | −2.6% | 0.03095 | −0.2% |
| **`ols_history`** (reference) | 0.03430 | — | 0.04424 | — | 0.03100 | — |
| `ridge_history` (R9.1) | 0.03440 | +0.3% | 0.04363 | −1.4% | 0.03108 | +0.3% |
| regressed persistence (+ age) | 0.03505–0.03516 | +2.2–2.5% | 0.04828 | +9.1% | 0.03257–0.03262 | +5.1–5.2% |
| TEI, recalibrated to the target's scale | 0.03696 | +7.8% | 0.07278 | +64.5% | 0.03728 | +20.3% |
| league mean | 0.03698 | +7.8% | 0.07279 | +64.5% | 0.04010 | +29.4% |
| persistence (last season, raw) | 0.04423 | +29% | 0.05233 | +18% | 0.03664 | +18% |

(17 candidate–target pairs were examined: five candidates on each target plus `beta_binomial` on the two made/attempted rates. Seven advanced — three for
three-point accuracy, two each for free-throw and true shooting. No correction is applied for examining several candidates: see §8.)

**Fold stability.** Paired MAE difference vs `ols_history` (negative = better), five development folds:

| | 2018-19 | 2019-20 | 2020-21 | 2021-22 | 2022-23 | fold-level 95% t-interval |
| --- | --- | --- | --- | --- | --- | --- |
| 3P `state_space` | −0.00096 | −0.00285 | −0.00075 | −0.00201 | −0.00096 | [−0.00262, −0.00039] |
| FT `state_space_role` | −0.00349 | −0.00360 | −0.00162 | −0.00300 | **+0.00002** | [−0.00424, −0.00043] |
| TS `state_space_role` | −0.00101 | −0.00088 | −0.00096 | −0.00074 | −0.00072 | [−0.00102, −0.00070] |

The FT gain in the last development fold is nil; with `state_space` it is −0.0003. Improvement is not uniform over seasons, and the 2019-20 and 2020-21
(shortened) folds are in every column.

**Is the gain "attempts-aware weighting" or just "more seasons of history"?** The state-space candidates read up to ten seasons; `ols_history` reads a
three-season window — the asymmetry that made R9.1's first candidate rule advance 14 of 18 pairs it should not have. R9.2 checked it directly (development folds
only, two opt-in diagnostics outside the rule; amendment 4; `results/history_ablation.json`):

| MAE relative to `ols_history` (development) | `ols_history_long` (OLS given the full history) | `state_space` (10 seasons) | `state_space_h3` (the same 3 seasons) | share of the gain kept with 3 seasons |
| --- | --- | --- | --- | --- |
| Three-point | +0.01% | −4.4% | **−3.7%** | 83% |
| Free-throw | −0.16% | −4.1% | **−3.2%** | 78% |
| True shooting | +0.24% | −2.6% | **−2.6%** | 100% |

Giving the regression the whole history changes nothing (it cannot use it better), so the baseline was not handicapped; and a state-space model limited to the baseline's
three seasons keeps 78–100% of the gain. **The improvement comes from weighting each season by the evidence behind it**, with a little more from extra history for
three-point and free-throw accuracy. (The holdout was not re-read for these diagnostics; the confirmed holdout comparisons are against `ols_history`, which the
diagnostic shows to be within 0.2% of the full-history baseline.)

## 4. The locked holdout (first read for 3P and FT; a re-read for TS)

| Quantity | n | `ols_history` MAE | candidate MAE | rel. | 95% player CI of the difference | folds improved |
| --- | --- | --- | --- | --- | --- | --- |
| 3P `state_space` | 569 | 0.03165 | 0.03062 | −3.2% | [−0.00195, −0.00010] ✔ | 2/2 |
| 3P `beta_binomial` | 569 | 0.03165 | 0.03056 | −3.4% | [−0.00192, −0.00023] ✔ | 2/2 |
| 3P `eb_shrinkage` | 569 | 0.03165 | 0.03092 | −2.3% | [−0.00149, +0.00006] ✗ | 2/2 |
| FT `state_space_role` | 544 | 0.04266 | 0.03989 | −6.5% | [−0.00446, −0.00130] ✔ | 2/2 |
| FT `state_space` | 544 | 0.04266 | 0.04058 | −4.9% | [−0.00356, −0.00077] ✔ | 2/2 |
| TS `state_space_role` | 654 | 0.02973 | 0.02916 | −1.9% | [−0.00143, +0.00035] ✗ | 2/2 |
| TS `state_space` | 654 | 0.02973 | 0.02916 | −1.9% | [−0.00130, +0.00018] ✗ | 2/2 |

The development *reference* (`ols_history`) was fixed from development; the holdout was not used to pick anything. Where two candidates are confirmed, the
pre-registered tie-break (lowest development MAE) selects the served one: `state_space` for three-point accuracy (0.03279 vs 0.03284 — `beta_binomial` is
statistically indistinguishable, and slightly better on the holdout by 0.00006), `state_space_role` for free throws.

**True shooting**: both folds improve and the point estimate is −1.9%, but the interval includes zero and the holdout is a re-read (R9.1 had read it for the
baselines; amendment 2 caps any confirmation at `development_only` regardless). The honest summary is *"probably a small improvement; not established"*.
The improvement also depends on who is evaluated: −1.7% at a 250-minute cut, −2.6% at 500, −4.4% at 1,000 minutes (sensitivity runs), and nil for players new
to the data (+0.1%).

## 5. Uncertainty: what was measured

| | Three-point | Free-throw | True shooting |
| --- | --- | --- | --- |
| **Forecast interval** (R9.1 residual method, nominal 80%) — development coverage / holdout coverage | 0.790 / 0.810 | 0.801 / 0.809 | 0.772 / 0.803 |
| **Model's own predictive interval** (nominal 80%, assumes recent-average attempts) — development / holdout | 0.794 / 0.803 | 0.842 / 0.853 | 0.780 / 0.813 |
| **Posterior predictive check**, realised attempts, development: share of outcomes inside the 50 / 80 / 90% predictive interval | 0.48 / 0.78 / 0.88 | 0.53 / 0.83 / 0.91 | 0.48 / 0.78 / 0.88 |
| SD of the standardised error (1.0 = calibrated spread) | 1.04 | 0.97 | 1.04 |
| Coverage of the 80% interval by fold (development) | 0.75–0.84 | 0.78–0.88 | 0.76–0.82 |

What these support: the **forecast intervals of three-point and free-throw accuracy cover about the nominal 80% out of time** (0.81 on the holdout), and the
state-space model's predictive distribution is well calibrated on the attempts actually taken (slightly conservative for free throws, slightly optimistic
for true shooting at 78%). They do **not** support any statement about the credible interval for *ability*, which is for an unobserved quantity and carries no coverage claim.

**Ability vs future-observation uncertainty are different things.** Of the variance of a next-season forecast, ability uncertainty (plus drift) is only 18% for
three-point accuracy, 49% for free throws and 41% for true shooting; the rest is the sampling noise of the season to be played. A good ability estimate cannot
make next season's percentage predictable: that is why the forecast interval is wide (±0.05 three-point, ±0.07 free-throw at 80%).

## 6. Sample-size effects

*Reliability of one season of evidence* (share of between-player ability variance resolved), from the fitted variance components at the last development cutoff:

| Attempts | 25 | 50 | 100 | 200 | 400 | 800 | 1,600 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Three-point | 0.06 | 0.11 | 0.20 | 0.33 | 0.49 | 0.66 | 0.80 |
| Free-throw | 0.40 | 0.58 | 0.73 | 0.84 | 0.92 | 0.96 | 0.98 |
| True shooting (TSA) | 0.10 | 0.19 | 0.31 | 0.48 | 0.65 | 0.79 | 0.88 |

The prior is worth about **412 three-point attempts**, 37 free-throw attempts and 220 true-shooting attempts: three-point accuracy needs ~400 attempts before a
player's own record outweighs the league curve (R4's ad-hoc constant of 300 was the right order of magnitude; the beta-binomial prior strength was 354–435 across development folds and R9.1's empirical-Bayes constant 277–422;
it is now estimated, not fixed). A player at 42% on 30 attempts is estimated at about the league curve; a 39% shooter on 500 attempts sits near his own record.

*Year-over-year stability of the measured rate* (≥ 100 attempts both seasons; true shooting ≥ 300): free-throw **r = 0.82**, true shooting 0.64, three-point **0.35**.
*Does the ability estimate rank next season better than last season's raw rate?* Yes, for all three quantities (mean over development folds): Spearman 0.41 vs 0.31 (3P), 0.78 vs 0.72 (FT), 0.60 vs 0.54 (TS).
*Accuracy by next-season attempts bin* (80% predictive coverage): 0.73 (< 100 attempts) against 0.77–0.81 (≥ 100) for three-point accuracy — the thinnest outcomes are
slightly under-covered. *Sensitivity to the attempt cut* (three-point / free-throw, evaluation only): 25 attempts −4.2% / −4.5%, 50 −4.4% / −4.1%, 100 −4.3% / −6.1% — the gain
does not depend on where the line is drawn.

## 7. The selection effect, stated plainly

Of the players Pivot scores at a cutoff: **69% appear at all** next season (31% do not), and only **46% (three-point), 44% (free-throw), 54% (true shooting)**
meet the evaluated population's condition. Every error figure above is for the evaluated population. Three separate questions, never to be conflated:

1. *Forecast of performance given a rotation role* — what this report validates.
2. *Forecast of whether he will play* — studied in [multidimensional-skill-coverage.md](multidimensional-skill-coverage.md) §4 (Brier 0.105 vs 0.218 base rate; AUC 0.92), not served.
3. *Forecast of total future contribution* — neither; requires both, with their dependence. Not offered. Every projected record says what it is conditional on.

By prior evidence, the share meeting the condition rises from 11% (< 100 prior three-point attempts) to 91% (≥ 800): the conditional forecast is for the players with
the evidence to be forecast.

## 8. Limits of this evidence

* **Multiplicity.** 17 candidate–target pairs were examined (three targets; five or six candidates). Seven advanced in development, four of those (two three-point,
  two free-throw) were confirmed on a holdout that no protocol had read. No multiple-comparison correction was applied (it was declared in advance). The two
  *confirmatory* targets are the ones whose holdout is clean; true shooting is exploratory.
* **The primary bar is `ols_history`.** A candidate that beats it by 3–6% beats a four-parameter regression, not the best conceivable model.
* **Ten seasons, one provider, two COVID-shortened seasons in the development folds.**
* **No improvement is claimed for true shooting.** If an owner needs a single recommendation: use `ols_history` for true-shooting projections until a later season
  can confirm the state-space candidate on data no one has read.
* **Hyperparameter estimation is inside each fold's training window** (no use of later data), but model *design* (the choice to try a state-space model) was informed by
  R9.1's results, which were read in full before R9.2 began. The pre-registration was committed before the first R9.2 run; its three amendments are recorded.
* **Absolute accuracy is modest.** A 3–4 point average miss on three-point accuracy and a 4–5 point miss on free-throw accuracy is mostly sampling noise of the season being forecast.
