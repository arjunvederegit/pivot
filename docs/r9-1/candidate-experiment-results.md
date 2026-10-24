# Candidate experiment results

Everything here is produced by `make backtest` on a scratch copy of the development database (10 NBA.com regular
seasons, 2016-17 → 2025-26), by the code in this branch. Aggregate artifacts (no per-player rows) are in
[results/](results/); the protocol is [preregistration.json](preregistration.json) and
[evaluation-architecture.md](evaluation-architecture.md). **Read §0 and §6 before quoting any number.**

## 0. Result in one paragraph

**No candidate approach improved on the best simple baseline under the pre-registered rule, on any of the six targets (0 of 18
candidate–target pairs advanced), so none was taken to the holdout.** The best simple baseline everywhere was a four-parameter
least-squares regression on the player's own recent history (`ols_history`). The candidates (empirical-Bayes shrinkage; ridge on
history; ridge on history plus the incumbent's box-score features) forecast about as well as that baseline or marginally better. The
one case worth a second look is the primary target, where ridge on box-score features is 2.8 % better than `ols_history` (MAE 0.379
vs 0.389) with a player-level interval entirely below zero, but a season-level interval that includes zero, and an advantage that
appears at a 250-minute evaluation cut and vanishes at 1,000 — it is a weak, fragile signal, not a finding. The **incumbent TEI** forecasts
its own documented target no better than "regress last season's value toward the mean with an age curve", is 10.8 % worse than the history
regression on the holdout (interval entirely above zero), and its shipped likely-range band contained 57 % of holdout outcomes against a
nominal 80 %.

## 1. Protocol in force, amendments and disclosures

The rule was committed (`preregistration.json`, v1) before the first official run. After that run an independent methodology review found
defects; the protocol was amended **before any holdout read**, in the *more conservative* direction each time (full list in
`PREREGISTRATION["amendments"]`):

1. Added the baseline `ols_history` (the baselines had lacked the candidates' own information) and let the recalibrated incumbent apply to every target.
2. Added a season-level interval, a 2 % practical-significance floor and a fail-closed, reference-baseline team check.
3. Hard-coded the holdout, made the read ledger mandatory, verified the development run a holdout confirms, strengthened the leakage audit
   (it now also deletes and re-assigns later rows) and ran it at every development origin of the primary target.

**What v1 would have concluded.** Under the original rule and baseline set, 14 of 18 candidate–target pairs advanced (ridge variants on
every target; shrinkage on true shooting and rebounding), and "ridge beats the best baseline by 7 %" was the headline. That conclusion
was an artefact of comparing a model that sees several seasons of history with baselines that saw one. The amendment is why the headline
is now different; it is reported here because a reader should know the first answer existed and why it was withdrawn.

**Disclosures.** (a) Before the development/holdout split was enforced in code, an engineering smoke test scored all folds for
`impact_proxy` with the then-registered models. Nothing was changed in response to it, but the `impact_proxy` holdout is not pristine.
(b) The incumbent's own stored validation used the 2024-25 → 2025-26 transition, one of the two holdout folds. (c) The holdout was read
once per target (ledger in `results/holdout_ledger.json`), for the baselines and the incumbent only, because no candidate advanced.

Design in one line: forecast season *t+1* from information through *t*; 5 development folds (cutoffs 2018-19…2022-23, targets
2019-20…2023-24) and 2 locked holdout folds (cutoffs 2023-24 and 2024-25); evaluated population = scored players with ≥ 500 minutes in the
target season (**about 54 % of the players scored at each cutoff** — the rest left, missed the season or played little); identical rows for
every model; MAE headline; 95 % player-cluster bootstrap and a fold-level t-interval for differences.

## 2. Primary target — `impact_proxy` (the incumbent's own documented objective)

`0.6·z(PIE) + 0.4·z(on-court NET_RATING)`. See [current-model-assessment.md](current-model-assessment.md) for why this is a proxy and
what it can and cannot support.

### Development folds

#### impact_proxy — exp-development-impact_proxy-2d96be1a-46bd0edc

n = 1591 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.3893)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.6003 | +54.2 % | 0.7708 | — | 0.798 | 1.898 |
| `persistence` | 0.4456 | +14.5 % | 0.5648 | 0.692 | 0.784 | 1.388 |
| `regressed_persistence` | 0.4122 | +5.9 % | 0.5231 | 0.692 | 0.793 | 1.319 |
| `regressed_persistence_age` | 0.4074 | +4.6 % | 0.5204 | 0.695 | 0.785 | 1.286 |
| `incumbent_tei` | 0.4128 | +6.0 % | 0.5186 | 0.705 | 0.795 | 1.314 |
| `incumbent_tei_recalibrated` | 0.4074 | +4.7 % | 0.5107 | 0.705 | 0.798 | 1.286 |
| `eb_shrinkage` | 0.3989 | +2.5 % | 0.5060 | 0.701 | 0.803 | 1.280 |
| `ridge_history` | 0.3899 | +0.2 % | 0.4932 | 0.725 | 0.799 | 1.229 |
| `ridge_boxscore` | 0.3785 | -2.8 % | 0.4784 | 0.738 | 0.798 | 1.221 |

Incumbent's own shipped band: coverage 0.608, mean width 0.905 (nominal 0.80).

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |
| `eb_shrinkage` | +0.0096 [+0.0008, +0.0181] (+2.5 %) | 0/5 | no |
| `ridge_boxscore` | -0.0108 [-0.0188, -0.0032] (-2.8 %) | 4/5 | no |
| `ridge_history` | +0.0006 [-0.0035, +0.0048] (+0.2 %) | 2/5 | no |

Per-fold ΔMAE of `ridge_boxscore` vs `ols_history` (negative = better): 2018-19 −0.0132 · 2019-20 −0.0169 · 2020-21 −0.0041 ·
2021-22 −0.0232 · 2022-23 **+0.0034**. Four of five folds improve, but the last, cleanest-looking fold (no COVID season in its target)
does not, and **every development fold has a COVID-shortened season (2019-20, 2020-21) in its features or its target**.
Team check (prospective, roster-known win %): ΔR² of `ridge_boxscore` vs `ols_history` = −0.003, 95 % [−0.049, +0.037]
(no credible difference). Decisions: `ridge_boxscore` fails only the season-level interval ([−0.0239, +0.0023]); `ridge_history` and
`eb_shrinkage` fail nearly everything.

### Locked holdout (baselines and incumbent only)

#### impact_proxy — exp-holdout-impact_proxy-48d7807c-46bd0edc

n = 654 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.3939)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.6071 | +54.1 % | 0.7787 | — | 0.783 | 1.900 |
| `persistence` | 0.4522 | +14.8 % | 0.5855 | 0.704 | 0.815 | 1.416 |
| `regressed_persistence` | 0.4237 | +7.6 % | 0.5389 | 0.704 | 0.797 | 1.324 |
| `regressed_persistence_age` | 0.4181 | +6.1 % | 0.5335 | 0.713 | 0.795 | 1.314 |
| `incumbent_tei` | 0.4364 | +10.8 % | 0.5471 | 0.676 | 0.791 | 1.327 |
| `incumbent_tei_recalibrated` | 0.4294 | +9.0 % | 0.5424 | 0.676 | 0.778 | 1.307 |

Incumbent's own shipped band: coverage 0.572, mean width 0.916 (nominal 0.80).

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |

MAE by holdout fold (cutoff 2023-24 forecasting 2024-25 / cutoff 2024-25 forecasting 2025-26): `ols_history` 0.381 / 0.407,
regressed persistence + age 0.402 / 0.434, **incumbent TEI 0.418 / 0.454**. The incumbent is worse than `ols_history` in both folds. Relative to regressed persistence + age, TEI is
4.4 % worse (interval [−0.006, +0.041] — not separable from it). On the cleaner seasons the incumbent's gap to the best simple baseline is
*larger* than in development (10.8 % vs 6.0 %).

### Subgroups (development, MAE)

| subgroup | n | `ols_history` | `ridge_boxscore` | incumbent TEI | regressed persistence + age |
| --- | --- | --- | --- | --- | --- |
| all | 1,591 | 0.389 | 0.378 | 0.413 | 0.407 |
| age ≤ 22 | 324 | 0.419 | 0.403 | 0.442 | 0.425 |
| age ≥ 31 | 259 | 0.409 | 0.400 | 0.417 | 0.458 |
| new to the corpus (rookie proxy) | 189 | 0.453 | 0.419 | 0.433 | 0.447 |
| window minutes < 1,000 | 140 | 0.464 | 0.428 | 0.436 | 0.467 |
| window minutes ≥ 3,000 | 1,046 | 0.376 | 0.367 | 0.408 | 0.396 |
| changed team (outcome-side stratum) | 559 | 0.404 | 0.391 | 0.407 | 0.431 |
| big-man proxy (top-quartile rebound share) | 393 | 0.404 | 0.389 | 0.440 | 0.427 |

Holdout incumbent vs `ols_history`: all 0.436 vs 0.394; ≥ 31: 0.367 vs 0.355; ≤ 22: 0.443 vs 0.399; rebound-heavy: 0.501 vs 0.418;
high-minute: 0.441 vs 0.384. The incumbent's deficit is concentrated in the rebound-heavy group (the big-man proxy), where the
index's box-score composition fits least well.

### Uncertainty

The incumbent's shipped band (`tei ± 1.2816σ`, nominal 80 %) covered **60.8 %** of development outcomes and **57.2 %** of holdout
outcomes, with mean width 0.91 against 1.31 for the calibrated interval. Calibrated intervals (one shared method, fitted on training
transitions only) covered 0.78–0.80 for every model on development and 0.78–0.82 on the holdout. Per-fold coverage ranged
0.72–0.89 (independent review), so the pooled figure should be read as ±0.05.

## 3. Exploratory targets

No multiplicity correction is applied; these are descriptive. In every one, the best baseline is `ols_history`; **no candidate
advanced**. Development folds:

#### pie — exp-development-pie-5009b0c9-46bd0edc

n = 1591 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0122)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0246 | +101.3 % | 0.0315 | — | 0.816 | 0.079 |
| `persistence` | 0.0138 | +13.3 % | 0.0176 | 0.814 | 0.818 | 0.045 |
| `regressed_persistence` | 0.0133 | +8.9 % | 0.0168 | 0.814 | 0.822 | 0.045 |
| `regressed_persistence_age` | 0.0129 | +5.9 % | 0.0164 | 0.824 | 0.827 | 0.044 |
| `incumbent_tei_recalibrated` | 0.0146 | +19.3 % | 0.0182 | 0.783 | 0.818 | 0.049 |
| `eb_shrinkage` | 0.0132 | +8.0 % | 0.0167 | 0.813 | 0.819 | 0.045 |
| `ridge_history` | 0.0123 | +0.9 % | 0.0158 | 0.836 | 0.834 | 0.043 |
| `ridge_boxscore` | 0.0122 | -0.2 % | 0.0157 | 0.839 | 0.827 | 0.042 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |
| `eb_shrinkage` | +0.0010 [+0.0006, +0.0013] (+8.0 %) | 0/5 | no |
| `ridge_boxscore` | -0.0000 [-0.0002, +0.0002] (-0.2 %) | 2/5 | no |
| `ridge_history` | +0.0001 [-0.0000, +0.0003] (+0.9 %) | 1/5 | no |

#### ts_pct — exp-development-ts_pct-80563995-46bd0edc

n = 1591 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0310)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0401 | +29.4 % | 0.0523 | — | 0.780 | 0.118 |
| `persistence` | 0.0366 | +18.2 % | 0.0502 | 0.540 | 0.735 | 0.103 |
| `regressed_persistence` | 0.0326 | +5.2 % | 0.0429 | 0.540 | 0.755 | 0.095 |
| `regressed_persistence_age` | 0.0326 | +5.1 % | 0.0429 | 0.535 | 0.766 | 0.095 |
| `incumbent_tei_recalibrated` | 0.0373 | +20.2 % | 0.0487 | 0.353 | 0.767 | 0.108 |
| `eb_shrinkage` | 0.0313 | +1.0 % | 0.0406 | 0.590 | 0.757 | 0.092 |
| `ridge_history` | 0.0311 | +0.3 % | 0.0403 | 0.583 | 0.769 | 0.092 |
| `ridge_boxscore` | 0.0310 | -0.2 % | 0.0398 | 0.590 | 0.761 | 0.089 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |
| `eb_shrinkage` | +0.0003 [-0.0003, +0.0009] (+1.0 %) | 1/5 | no |
| `ridge_boxscore` | -0.0000 [-0.0006, +0.0005] (-0.2 %) | 3/5 | no |
| `ridge_history` | +0.0001 [-0.0002, +0.0004] (+0.3 %) | 2/5 | no |

#### ast_pct — exp-development-ast_pct-619e077c-46bd0edc

n = 1591 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0279)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0698 | +150.2 % | 0.0871 | — | 0.800 | 0.193 |
| `persistence` | 0.0295 | +5.8 % | 0.0403 | 0.872 | 0.794 | 0.091 |
| `regressed_persistence` | 0.0291 | +4.2 % | 0.0392 | 0.872 | 0.783 | 0.090 |
| `regressed_persistence_age` | 0.0289 | +3.6 % | 0.0389 | 0.874 | 0.788 | 0.089 |
| `incumbent_tei_recalibrated` | 0.0625 | +123.8 % | 0.0791 | 0.337 | 0.794 | 0.195 |
| `eb_shrinkage` | 0.0284 | +1.6 % | 0.0379 | 0.874 | 0.787 | 0.090 |
| `ridge_history` | 0.0278 | -0.5 % | 0.0368 | 0.882 | 0.787 | 0.085 |
| `ridge_boxscore` | 0.0276 | -1.1 % | 0.0367 | 0.884 | 0.788 | 0.086 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |
| `eb_shrinkage` | +0.0005 [-0.0000, +0.0010] (+1.6 %) | 1/5 | no |
| `ridge_boxscore` | -0.0003 [-0.0008, +0.0002] (-1.1 %) | 4/5 | no |
| `ridge_history` | -0.0001 [-0.0004, +0.0002] (-0.5 %) | 4/5 | no |

#### reb_pct — exp-development-reb_pct-4428b04d-46bd0edc

n = 1591 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0095)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0324 | +241.4 % | 0.0403 | — | 0.818 | 0.096 |
| `persistence` | 0.0101 | +6.7 % | 0.0135 | 0.928 | 0.822 | 0.034 |
| `regressed_persistence` | 0.0101 | +6.7 % | 0.0134 | 0.928 | 0.826 | 0.034 |
| `regressed_persistence_age` | 0.0102 | +6.9 % | 0.0134 | 0.927 | 0.817 | 0.034 |
| `incumbent_tei_recalibrated` | 0.0292 | +207.7 % | 0.0361 | 0.407 | 0.825 | 0.093 |
| `eb_shrinkage` | 0.0096 | +1.3 % | 0.0128 | 0.933 | 0.838 | 0.034 |
| `ridge_history` | 0.0096 | +1.0 % | 0.0127 | 0.936 | 0.828 | 0.033 |
| `ridge_boxscore` | 0.0097 | +2.0 % | 0.0128 | 0.933 | 0.826 | 0.032 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |
| `eb_shrinkage` | +0.0001 [-0.0000, +0.0003] (+1.3 %) | 2/5 | no |
| `ridge_boxscore` | +0.0002 [+0.0000, +0.0004] (+2.0 %) | 2/5 | no |
| `ridge_history` | +0.0001 [+0.0000, +0.0002] (+1.0 %) | 0/5 | no |

#### usg_pct — exp-development-usg_pct-821f7d3d-46bd0edc

n = 1591 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0208)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0449 | +116.4 % | 0.0558 | — | 0.763 | 0.132 |
| `persistence` | 0.0219 | +5.3 % | 0.0278 | 0.841 | 0.799 | 0.068 |
| `regressed_persistence` | 0.0215 | +3.6 % | 0.0272 | 0.841 | 0.778 | 0.066 |
| `regressed_persistence_age` | 0.0211 | +1.8 % | 0.0267 | 0.847 | 0.788 | 0.066 |
| `incumbent_tei_recalibrated` | 0.0354 | +70.5 % | 0.0445 | 0.529 | 0.757 | 0.105 |
| `eb_shrinkage` | 0.0225 | +8.2 % | 0.0282 | 0.837 | 0.774 | 0.068 |
| `ridge_history` | 0.0206 | -0.9 % | 0.0260 | 0.856 | 0.787 | 0.063 |
| `ridge_boxscore` | 0.0202 | -2.8 % | 0.0256 | 0.861 | 0.796 | 0.064 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |
| `eb_shrinkage` | +0.0017 [+0.0011, +0.0022] (+8.2 %) | 0/5 | no |
| `ridge_boxscore` | -0.0006 [-0.0009, -0.0002] (-2.8 %) | 4/5 | no |
| `ridge_history` | -0.0002 [-0.0004, +0.0000] (-0.9 %) | 4/5 | no |

Reading across them:

* **Regression toward the mean is where the gain is.** For true shooting, raw persistence MAE 0.0366 → regressed persistence 0.0326
  (−11 %) → the four-parameter history regression 0.0310 (a further −5 %). Nothing beat that last step by a margin the rule accepts.
* **For the stable rates the room is tiny**: assist share, rebound share and usage are forecast to within ~0.01–0.03 of their scale by
  last season's value; the best simple model improves on raw persistence by 5–6 %, in absolute terms 0.0006–0.0016.
* **The incumbent knows little about individual rate statistics** (recalibrated TEI MAE on the holdout is 19–202 % above `ols_history` on pie, TS %,
  AST %, REB % and USG %, with rank correlations of 0.28–0.77). That is expected of a composite, and is why a skill engine cannot be
  built by reading TEI components.

### Holdout (baselines and incumbent), for reference

#### pie — exp-holdout-pie-ee5e4f86-46bd0edc

n = 654 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0123)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0238 | +94.5 % | 0.0302 | — | 0.821 | 0.077 |
| `persistence` | 0.0137 | +11.8 % | 0.0179 | 0.806 | 0.804 | 0.044 |
| `regressed_persistence` | 0.0130 | +6.2 % | 0.0168 | 0.806 | 0.815 | 0.043 |
| `regressed_persistence_age` | 0.0129 | +5.7 % | 0.0166 | 0.809 | 0.792 | 0.041 |
| `incumbent_tei_recalibrated` | 0.0145 | +18.5 % | 0.0182 | 0.766 | 0.801 | 0.047 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |

#### ts_pct — exp-holdout-ts_pct-7873ce08-46bd0edc

n = 654 evaluated player-seasons; reference baseline = `ols_history` (MAE 0.0297)

| model | MAE | vs reference | RMSE | Spearman (fold mean) | 80 % interval coverage | mean width |
| --- | --- | --- | --- | --- | --- | --- |
| `league_mean` | 0.0378 | +27.2 % | 0.0499 | — | 0.812 | 0.121 |
| `persistence` | 0.0362 | +21.9 % | 0.0482 | 0.514 | 0.784 | 0.112 |
| `regressed_persistence` | 0.0317 | +6.7 % | 0.0413 | 0.514 | 0.798 | 0.099 |
| `regressed_persistence_age` | 0.0317 | +6.7 % | 0.0412 | 0.513 | 0.798 | 0.100 |
| `incumbent_tei_recalibrated` | 0.0367 | +23.3 % | 0.0479 | 0.277 | 0.800 | 0.113 |

| candidate | Δ MAE vs reference [95 % cluster-bootstrap CI] | folds improved | advance / confirm |
| --- | --- | --- | --- |

(The other exploratory holdout tables are in `results/holdout-*`. The ordering of methods matches development in every target.)

## 4. Sensitivity of the primary conclusion to the evaluation cut (evaluation-only; training unchanged)

| target | evaluation cut (min) | n | persistence | incumbent TEI* | `ols_history` | `ridge_boxscore` | `ridge_boxscore` vs `ols_history`: relative [player CI] / [season CI] | advances? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| impact_proxy | 250 | 1786 | 0.4759 | 0.4323 | 0.4138 | 0.4048 | -2.2 % [-0.0163, -0.0016] / [-0.0173, -0.0008] | yes |
| impact_proxy | 500 | 1591 | 0.4456 | 0.4128 | 0.3893 | 0.3785 | -2.8 % [-0.0188, -0.0032] / [-0.0239, +0.0023] | no |
| impact_proxy | 1000 | 1177 | 0.4219 | 0.3911 | 0.3651 | 0.3560 | -2.5 % [-0.0181, +0.0001] / [-0.0234, +0.0052] | no |
| ts_pct | 250 | 1786 | 0.0394 | 0.0397 | 0.0334 | 0.0334 | +0.0 % [-0.0005, +0.0006] / [-0.0006, +0.0007] | no |
| ts_pct | 500 | 1591 | 0.0366 | 0.0373 | 0.0310 | 0.0310 | -0.2 % [-0.0006, +0.0005] / [-0.0008, +0.0007] | no |
| ts_pct | 1000 | 1177 | 0.0332 | 0.0345 | 0.0277 | 0.0278 | +0.4 % [-0.0006, +0.0008] / [-0.0011, +0.0014] | no |

\* TEI as shipped for `impact_proxy`; recalibrated TEI for `ts_pct`. The 500-minute row is the pre-registered, gating analysis; the others are
non-gating. At 250 minutes the box-score ridge would clear every check on the primary target; at 1,000 it would not clear the player
interval. **A conclusion that flips with the cut is not a conclusion.**

## 5. Boundary behaviour verified

* Candidate forecasts at a non-final cutoff are bit-identical to the backtest's forecasts for that fold (test).
* Returners (players who missed the cutoff season) are scored by every model with finite forecasts and intervals (test); they are 19.7 %
  of the scored population but 1.3 % (21 rows) of the evaluated rows on the development folds, so this path is validated for mechanics, not for accuracy.
* Constant forecasts have undefined (not zero) rank correlation; pooled cross-fold Spearman is replaced by the fold mean (a defect the tests caught).

## 6. What these results do and do not support

* They support: **a small set of transparent methods, given the same history, forecast these statistics about equally well; the
  incumbent TEI is not better than the simplest such method on its own target and its range is too narrow; correction for sampling
  noise (regression to the mean) is worth most for the noisiest rate.**
* They do not support: any claim that a model measures ability or impact; any claim for seasons after 2025-26; any claim about players
  who do not play a rotation role next season (46 % of those scored); any ranking of candidates beyond "no clear winner".
* Limits that bear on every number: single provider snapshot (no vintage control: "as of *t*" means "rows for seasons ≤ *t*", not "values
  as published at *t*"); COVID seasons in every development fold; first-fold training on only two transitions (so lag features are
  missing for part of the early folds); squared-error fitting judged on absolute error; multi-team players carry one end-of-season row
  (their minutes are attributed to the last team in the team check); 10 seasons is a small sample of league environments.
* **The negative result is the result.** It says the evaluation infrastructure can distinguish a real improvement from a plausible
  one — the planted-signal test shows it advances and confirms a candidate when a real effect exists — and that none of the
  first-generation candidates is one.
