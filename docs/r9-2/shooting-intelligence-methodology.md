# R9.2 — Shooting intelligence: methodology

Three **separate** quantities — true shooting (`ts_pct`), three-point accuracy (`fg3_pct`), free-throw accuracy (`ft_pct`) — each with up to three
kinds of statement. They are never combined into a shooting score: R9.2's own data say they are different things (free-throw and
three-point accuracy correlate about 0.48 after removing noise, [goat-intelligence-assessment.md](goat-intelligence-assessment.md) §2).

| Kind | Question | Method | Validated by |
| --- | --- | --- | --- |
| **measured** | what did he shoot, on how many attempts? | integer counts recovered from NBA.com; ratio of counts | data integrity + reconciliation |
| **inferred** | what is his underlying ability, given how little or much we saw? | Gaussian state-space model with a known, attempts-based sampling variance (empirical Bayes) | its own forecasts and predictive calibration out of time — *never* directly (ability is latent) |
| **projected** | what will he shoot next season, if he plays a rotation role? | the posterior forecast of next season's ability, or the best baseline | rolling-origin backtest against `ols_history`, then a first-read holdout |

## 1. Measured: recovering the counts

NBA.com's season tables publish per-game averages rounded to one decimal (`FG3A = 5.4`), a few exact totals in the advanced table (`FGM`, `FGA`),
and rates to three decimals. Multiplying a rounded average by games played gives attempts wrong by up to `0.05 × GP` (±4 for a full season;
mean absolute error 1.14 three-point and 1.10 free-throw attempts on real data), and treating `0.355` as data is worse. The engine recovers **integer season counts by
constraint satisfaction** (`components.reconstruct`): a count tuple `(FG3M, FG3A, FTM, FTA)` is *feasible* iff

* each rounds to its published per-game figure (`|T/GP − per_game| ≤ 0.05`),
* `round(makes/attempts, 3)` equals the published rate (for three-pointers and free throws),
* `(FGM + 0.5·FG3M)/FGA` rounds to the published `EFG_PCT` (exact `FGM`, `FGA` from the advanced table),
* `PTS = 2·FGM + FG3M + FTM` is within the per-game rounding of the published `PTS`, and `PTS / (2(FGA + 0.44·FTA))` rounds to the published `TS_PCT`.

One feasible tuple ⇒ the counts are **exact**; several ⇒ the nearest to the rounded point estimate is used and the *range* of attempts over all
feasible tuples is returned (`bounded`); none ⇒ `unconstrained` (never trusted; the measured record is refused). Nothing is fitted.

**Validation (real data):** for 2025-26, 573 player-seasons also carry exact totals from an independent CSV import. The reconstruction recovered
all four counts exactly in **all 476 rows it called exact**, and in 45 of 97 `bounded` rows; mean absolute error 0.10 attempts (vs 1.15 for
rounded-times-games), maximum 5, and the bounded range contained the truth in every row. 4,487 of 5,483 player-seasons are exact (82%); the
rest carry a median attempt-range of 2. True shooting recomputed from the recovered counts agrees with the published value to 0.0005 for all 5,455 player-seasons with attempts — but note this is a *consistency check, not independent
validation*: agreement with the published `TS_PCT` is one of the feasibility constraints, so it holds by construction. The independent evidence is the comparison with exact totals above. (`results/reconstruction_validation.json`; synthetic-truth tests in `test_pi_components.py`.)

**Rules:** no attempts ⇒ *unavailable* (never 0%); rates are fractions of 1 and a published value outside [0, 1] is refused, not rescaled; regular season only;
a traded player's row is season totals across teams and says so; a missing season is reported, never filled.

## 2. Sampling noise: what a single season's number is

A player-season rate `y` measures his ability `θ` with error whose variance depends on the attempts actually taken.

* **Three-point and free-throw accuracy** are made/attempted: `Var(y) = p(1−p)/n`, with `p` smoothed by half a count (`(m + ½)/(n + 1)`) so a 3-for-3
  season does not claim certainty. Rounded percentages are never used: the counts are.
* **True shooting is not a binomial rate.** `TS = PTS/(2·TSA)`, `TSA = FGA + 0.44·FTA`, and points per attempt take values 0/1/2/3. Treating
  attempts as given and each as an independent trial, the delta-method variance of the *ratio estimator* is
  `Var(TS) = [4·n₂q₂(1−q₂) + 9·n₃q₃(1−q₃) + n_f q_f(1−q_f)] / (2·TSA)²`, where `n₂, n₃, n_f` are two-point, three-point and free-throw attempts
  and `q` their smoothed rates. A beta-binomial on true shooting would be wrong on its face; the engine refuses it (`BetaBinomialRate.applies_to`
  admits only `fg3_pct` and `ft_pct`, and a planted-defect test fails if that changes).
* **The approximation's limits, and what `φ` is.** The variance formula is exact for independent attempts (checked by Monte-Carlo in the review: empirical/formula = 0.998); shot-to-shot variation in make
  probability *lowers* the variance slightly (ratio 0.97) and paired free throws change it by under 1%. The model nevertheless carries a **dispersion** `φ`, estimated from data: observation variance =
  `φ · s²`. Fitted `φ` is 1.05–1.15 (three-point `state_space`), 1.18–1.33 (free-throw `state_space_role`; 1.12–1.36 across models and folds), 1.12–1.23 (true shooting). It is **a nuisance inflation factor, not a
  measured property of shooting**: the review showed it is confounded with the persistence `ρ` and the drift variance (profile likelihood: for three-point accuracy `φ` moves from 0.78 to 1.05 along the plausible range
  of `ρ`; for free throws from 0.3 to 1.8), and it rises as the training window grows. It absorbs whatever the model misses between seasons — role change, in-season change, drift that scales with 1/attempts —
  not "free throws come in pairs".
* **The plug-in variance depends on the observed rate.** `p` in `p(1−p)/n` is the season's own (smoothed) rate, so a lucky high-rate season gets a smaller variance and more weight — which pushes levels
  **up for a high-rate statistic such as free throws** and down for three-point accuracy. Forecast error is unaffected (a variant with a rate-independent variance, `state_space_fixedvar`, has the same MAE and
  removes the bias: free-throw bias +0.0036 → −0.0013 in development; `results/baseline_challenge.json`), but the served *free-throw ability level* is shifted up by roughly 0.3–0.5 points
  (+0.9 on the holdout). Fixing it is the first change recommended for the next iteration, to be evaluated with fresh data.

## 3. Inferred ability: the model

```
y_t = θ_t + e_t,              e_t ~ N(0, φ · s²_t)            (sampling noise; s²_t known from the attempts)
θ_t = m(age_t, role_t) + u_t                                   (a league curve + the player's deviation)
u_{t+1} = ρ · u_t + η_t,      η_t ~ N(0, ω²),  ω² = τ²(1 − ρ²)  (stationary: Var(u) = τ² across players)
```

`m` is quadratic in age (plus, in `state_space_role`, the player's rebounding, three-point rate, free-throw rate and usage z-scores *at the
cutoff*). `τ` is the between-player spread of true ability at a given age; `ρ` the season-to-season persistence of a player's *true* deviation (1 = fixed
talent); `φ` the dispersion. A Kalman filter over all of a player's seasons (up to ten; a missed or zero-attempt season is *missing evidence*, and
the state still ages) gives the **posterior of ability now** `N(θ̂, P)` and, one step on, the forecast of ability **next season**
`N(m' + ρû, ρ²P + ω²)`.

**Estimation.** `(β, ρ, τ², φ)` are fitted by maximising the **one-step-ahead predictive likelihood** of the next-season observation over training
pairs — each pair's history is filtered, the filter's predictive distribution for the following season (including that season's own sampling
variance) is formed, and the realised value is scored under it. Only pairs whose outcome is observable at the cutoff are used (the same
as-of discipline as every R9.1 model); hyperparameters are *estimated* (L-BFGS, two starts), not fixed — in particular not R4's constant 300
attempts. This is **empirical Bayes**: the fitted hyperparameters are then treated as known.

**What the reported numbers mean.**

* `value` = posterior mean ability at the end of the cutoff season. `interval` = a **credible** interval (`kind = "credible"`, nominal 80%) — a
  range for the *unobserved* ability under the model, *not* a frequentist confidence interval, with hyperparameter uncertainty ignored. It carries
  **no coverage claim**; the validation of the machinery behind it is the posterior-predictive check in §6.
* `reliability = 1 − P/τ²`: the share of between-player ability variance this player's evidence has resolved (0 = nothing beyond the league curve).
  It is a **model quantity**, not a probability and not a test-retest correlation; the measured analogue is year-over-year stability (§6).
* `weight_on_player_record` = how far the estimate sits between the league curve (0) and his own record (1), computed as the filter's **actual response to his observations** (the change in the posterior mean when
  every observation moves by one unit). It equals the reliability only when the cutoff season itself is observed; with older evidence the state has decayed and the weight is *higher* than the reliability
  (the review measured up to +0.17 for free throws).
* `effective_attempts` = attempts of current-season-quality evidence the posterior is equivalent to: `φ·v_i·(1/P − 1/τ²)` with `v_i` the player's *own* per-attempt variance, so a one-season player's evidence is
  worth about the attempts he took. (With the league-average variance, elite free-throw shooters were told their record was worth up to 1.6× the attempts they took.)
* `sample.attempts` on an inferred record is the attempts across **every season the filter used** (up to ten), not the three-season window that decides who is scored.

**The example the spec asks about.** A player at 42% on 30 attempts and one at 39% on 500: the first has reliability ≈ 0.07 (three-point, one
season), the second ≈ 0.55; the 42% player is estimated at roughly the league curve plus a few tenths of a point, and the 39% player near
his own record — the 500-attempt player is the better-supported shooter (unit test `test_few_attempts_means_more_shrinkage_than_many`).

## 4. Projected: what is forecast, and what is not

The projection is the posterior forecast of **next season's observed rate**: `E[y_{t+1}] = E[θ_{t+1}]`. Its **interval** is R9.1's
residual-calibrated forecast interval (leave-one-transition-out forecast errors; spread modelled as `a + b/attempts`; empirical quantile). It
includes ability uncertainty, drift **and** next season's sampling noise; its empirical coverage is *measured out of time* and stored beside
it. (The model's own predictive distribution is separately checked, conditional on the attempts actually taken, in §6.)

**Conditional on playing — said on every record.** A projection is *conditional on* the player playing ≥ 500 minutes and (for three-point
and free-throw accuracy) taking ≥ 50 attempts in the target season. Of the players scored at a cutoff, only **46%** (three-point), **44%**
(free-throw) and **54%** (true shooting) meet that condition the following season, and **31%** do not appear at all (development folds). The
record's `conditioning` field (required by the contract) says this; the forecast is not a forecast of whether he will play or of his total
contribution. The availability study ([multidimensional-skill-coverage.md](multidimensional-skill-coverage.md) §4) shows participation is
itself forecastable.

**Which method is served.** The one the pre-registered rule supports. Where a candidate advanced *and* was confirmed on the holdout, it; otherwise
the reference baseline (`ols_history`: least squares on last season, the windowed history and age), labelled a benchmark. See the validation report — in particular its §3a:
**`ols_history` is the pre-registered reference, not the strongest simple baseline.** A regression that also knows each player's attempts captures most of the three-point and free-throw gain
(development: the served models are only −1.4% to −1.5% against it, below the 2% floor), and the "confirmed on the holdout" statements below mean *against `ols_history`*.

## 5. The R9.1 comparison set

Every candidate is scored on **identical rows** against: league mean; last season's value; regression toward the mean (± age curve);
`ols_history` (**the pre-registered reference every candidate must beat** — it does not use attempts, so it is the strongest *attempts-blind* baseline, not the strongest simple one; see the validation report §3a); the existing TEI recalibrated onto the target's scale;
and R9.1's own candidates (`eb_shrinkage`, `ridge_history`, `ridge_boxscore`). The R9.2 candidates: `state_space`, `state_space_role`, and (three-
point and free-throw only) a discounted `beta_binomial`: a Beta prior (mean = age curve on the logit scale, strength κ) updated with the player's
makes and attempts, each older season counted at a fitted fraction of the next.

## 6. Evaluation protocol (pre-registered)

Defined in `PREREGISTRATION_R92`, committed **before** any R9.2 development run. Population and folds unchanged from R9.1: players with ≥ 200
minutes over the three seasons ending at the cutoff are scored; the **evaluated** population is those who play ≥ 500 minutes the next season
and, for three-point and free-throw accuracy, take ≥ 50 attempts; development origins 2018-19…2022-23, holdout origins 2023-24 and 2024-25
(hard-coded). The advance rule is R9.1's, applied unchanged: the 95% player-cluster bootstrap interval of the MAE difference vs the reference lies
below 0, **and** the fold-level t-interval lies below 0, **and** ≥ 4 of 5 folds improve, **and** ≥ 2% lower MAE, **and** pooled interval
coverage in [0.75, 0.85]. Sensitivity: attempt cuts 25/100 (evaluation only). `fg3_pct` and `ft_pct` are confirmatory (their holdout had never
been read by any protocol); `ts_pct` is exploratory (R9.1 read its holdout: a re-read, recorded, status capped). Five dated amendments are
recorded (a tie-break rule before any holdout read; the re-read cap; the cap on inferred status; the history-ablation diagnostics; and the post-review baseline challenge and reliability floor), each made before the
evidence it affects was used or only lowering what is claimed or served.

**Supplementary checks** (development folds only; `scripts/r9_2_analysis.py`):

* *posterior predictive check* — the standardised error of next season's rate under the model's predictive distribution **given the attempts
  actually taken**; coverage at 50/80/90%, by attempts bin and by fold;
* *reliability by attempts* — from the fitted variance components;
* *stability* — year-over-year correlation of the measured rates, and whether the ability estimate ranks next season's outcomes better than the raw rate;
* *participation* — who meets the evaluation condition next season, by how much evidence they had.

## 7. Assumptions and limitations (the list a reader should hold in mind)

1. **Ability is latent.** Nothing here measures it; the state-space posterior is an estimate from box-score evidence, validated only through what it predicts.
2. **Independent attempts**, with a fitted dispersion: shot quality, defence faced and role changes are not modelled (the `state_space_role` variant adds four role
   covariates at the cutoff and nothing else). In particular the role covariates are those of the **cutoff season**, applied to every historical season (a returner's earlier roles are ignored),
   and a player with no cutoff-season role row is imputed the league average (z = 0). Carrying the last observed role is an R9.3 refinement; the plain `state_space` model (the one served for three-point accuracy) does not use role.
3. **Hyperparameters are estimated and then treated as known** — credible intervals ignore that uncertainty.
4. **Selection and volume.** The league curve is fitted on players who *later qualify for evaluation*, and attempts are treated only as precision. Measured (`results/selection_check.json`, development folds): refitting
   on every observed player-season (≥ 10 attempts) moves the league mean by **−0.8 to −1.4 points and widens τ ×1.3–1.6 for three-point accuracy**, −0.4 to −0.6 points (×1.03–1.07) for free throws and −0.2 to −0.5 points
   (×1.02–1.05) for true shooting. More importantly, for *every* scored player who took any attempt the next season, the forecast over-predicts when evidence is thin: pooled bias +3.1 / +1.2 / +0.2 points
   for three-point accuracy in the reliability bins [0, 0.1) / [0.1, 0.2) / [0.2, 0.3), and +3.2 / +3.5 / +2.0 for free throws; every bin at or above reliability 0.3 is within 0.6 points *in this all-who-played view*. The floor is a uniform line, not a
   fix: among the players who did **not** later qualify for evaluation (they lost their minutes), the forecast stays over-predicted **above** the floor — +3.1 to +3.8 points for three-point accuracy and +2.5 to +3.3 for free throws
   in every bin from 0.3 up (`by_reliability_not_qualifying`). That subset is selected on *future* minutes, so it is not an unbiased test of ability, and nothing observable at the cutoff separates it; it is why every projection is stated as conditional on playing. Volume carries information about ability — coaches let good shooters shoot — that
   the model does not use. **Inferred ability is therefore withheld below reliability 0.30** (the measured value is still returned), and every inferred record says the curve is too high for rare shooters.
5. **Conditional on playing** (§4). 46–56% of scored players are not in the evaluated population the following season (31% do not appear at all).
6. **Ten seasons, one provider, COVID-shortened seasons in the folds.** Results are about 2016-17…2025-26 and make no claim about later seasons.
7. **Single-provider counts.** The reconstruction removes the rounding problem; it cannot detect an error NBA.com itself made.
8. **No age×role interactions, no league-environment (season) term, no injury information** (injury data exist for the present only), no team or opponent context. League true-shooting and free-throw levels drift upward
   across the development folds (TS 0.572 → 0.588), so ability is levelled against a pooled-era curve and every model under-predicts true shooting slightly (bias −0.003 to −0.004). Role covariates are the *cutoff season's*
   z-scores applied to every historical season and mean-filled for players with no cutoff-season row.
9. **Parameters on a bound.** The persistence `ρ` sits on its upper bound (0.999) for three-point accuracy in the early folds — the optimiser reports success because the logistic re-parametrisation flattens the gradient —
   and its profile interval is about [0.82, 0.999]. Fitted parameter sets now carry an `at_bound` list; ability estimates are not sensitive to it (every fold's forecasts are calibrated), but `ρ` itself is not well identified.
10. **Three-point accuracy is the noisiest quantity** (year-over-year r ≈ 0.35 at ≥ 100 attempts; one season at 100 attempts resolves about 20% of ability variance). The estimate's value is
   precisely that it refuses to over-read a hot season.
