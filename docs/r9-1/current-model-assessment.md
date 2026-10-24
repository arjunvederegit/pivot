# Formal assessment of the existing Player Impact model (TEI)

Method: read the code (`analytics/impact.py`, `features.py`, `train.py`, `score.py`, `uncertainty.py`, `projection.py`),
the model card, and the stored model rows; reproduce the stored validation; then evaluate the incumbent under the R9.1
rolling-origin design. **Dev** numbers are from the five development folds (targets 2019-20 → 2023-24); **holdout** numbers from the
two locked folds (targets 2024-25 and 2025-26), read once ([candidate-experiment-results.md](candidate-experiment-results.md)). Facts script:
`backend/scripts/r9_1_model_assessment.py` ([results/model_assessment.json](results/model_assessment.json)).

## 1. What TEI is

**A hand-weighted linear composite of ten within-season z-scores of box-score production. Nothing is fitted.**

```
TEI = (2.5 / 1.16) · Σ wᵢ · zᵢ        (Σ|wᵢ| = 1.16; so ≈ 2.155 index points per weighted z-unit)
```

| input zᵢ (minutes-weighted z within season) | weight |
| --- | --- |
| points per 75 possessions | +0.22 |
| true shooting % | +0.18 |
| assist % | +0.14 |
| PIE | +0.14 |
| steals per minute | +0.12 |
| blocks per minute | +0.10 |
| turnover % | −0.08 |
| defensive rebound % | +0.08 |
| usage % | +0.06 |
| offensive rebound % | +0.04 |

* **Units:** a unitless index (≈ ±5 for elite seasons). The model card is explicit that it is *not* points per 100 possessions;
  a team's minutes-weighted index converts to net rating through a separately fitted coefficient ≈ 15 (`tei_to_net_rating`).
* **Serving construction:** the last three seasons (2023-24 → 2025-26), recency-weighted (λ = 0.7, times minutes), then z-scored
  against the **latest season's** league moments (so train and serve share a scale). Players with < 200 window minutes get no
  estimate ("unscored", never zero). A missing input becomes z = 0, i.e. "league average".
* **Training population / objective / regularisation:** none, because there is no fitting. "Training" (`make train`) recomputes
  the index, runs one validation, and registers the model. The weights are constants chosen by judgement (documented in
  `docs/methodology.md`); no sensitivity analysis of them exists in the repository.
* **Uncertainty:** a per-player band `tei ± 1.2816·σ·2.5` with `σ² = 0.0326 + 240.9 / window_minutes`, estimated from 921
  same-player consecutive-season pairs. The model card calls it season-to-season variability, "the right input for a
  forward-looking interval". It is a *normal-theory* band with no tested coverage.

## 2. Is it descriptive, inferential, predictive or a mixture?

**Descriptive**, with a predictive *validation claim*. It summarises a player's box-score production on a common scale; it makes
no attempt to separate ability from context or luck (no sample-size correction beyond the minutes gate and the band; no
teammate adjustment; no age or role term). It is not inferential — nothing estimates an underlying parameter — and it is not
a causal impact estimate. The code and card describe it correctly as a proxy; the risk is in how a *percentile of an index* is
read downstream as skill.

## 3. What it has been validated against, and why that is thin

The stored validation (reproduced exactly: n = 464, index MAE 0.6454 vs persistence 0.7169) is **one transition**
(2024-25 → 2025-26), pooled over all 464 players with window features — of whom **27.8 % played under 500 minutes** in the target
season, so their target is mostly noise. The target is the incumbent's own: `0.6·z(PIE) + 0.4·z(on-court NET_RATING)`.

| Property | Finding | Consequence |
| --- | --- | --- |
| **Overlap of predictor and target** | The index correlates **0.958** with same-season z(PIE) (rotation players), and PIE carries 60 % of the target. | The index is, to a first approximation, a re-expression of PIE; "predicting next season's PIE-heavy target from this season's PIE-like index" mostly tests how persistent box-score share is. It is not independent evidence about impact. |
| **On-court net rating in the target** | NET_RATING (40 % of the target) is a **team result while the player is on the floor**, confounded by teammates; the index gives it zero weight. | The target rewards "plays for good teams again next year". A better forecast of it is not necessarily a better measure of the player. |
| **Scale** | SD of index prediction 0.68 vs SD of target 1.09; regression slope of target on prediction 0.66. | Not on the target's scale; but a linear recalibration changes MAE by < 0.001 (0.6458 vs 0.6454), so the mismatch is not what limits it. |
| **The "persistence" baseline** | Raw last-season proxy, uncorrected. | A straw man: a regression toward the mean already removes most of the gap. The right comparator is *regressed* persistence (below). |
| **Single fold** | n = 1 transition, selected as the most recent. | No estimate of variability across seasons. |
| **Team-level evidence** | R² 0.7505 (concurrent, 90 team-seasons) and 0.6236 (change-on-change) vs **on-court net rating** (documented). | Concurrent, not out-of-time; and on-court rating is built from the same players. R9.1's check against **win %** gives pooled concurrent R² **0.63 (dev)** and **0.36 (dev)** when last season's scores are applied to next season's roster. Real, moderate, and expected for any metric that tracks quality. |

## 4. How it performs under the R9.1 design

Same rows and cutoffs for every method; target = `impact_proxy`. **Dev** = 5 development folds (1,591 evaluated player-seasons);
**holdout** = 2 locked folds (654), read once. Full tables: [candidate-experiment-results.md](candidate-experiment-results.md).

| Method | MAE (dev) | MAE (holdout) | holdout vs best simple baseline |
| --- | --- | --- | --- |
| league mean | 0.600 | 0.607 | +54 % |
| raw persistence (the incumbent's own comparator) | 0.446 | 0.452 | +15 % |
| regressed persistence (last season, shrunk) | 0.412 | 0.424 | +8 % |
| regressed persistence + age curve | 0.407 | 0.418 | +6 % |
| **history regression** (`ols_history`: last season, windowed history, age) — **best simple baseline** | **0.389** | **0.394** | — |
| **incumbent TEI, as shipped** (TEI ÷ 2.5) | **0.413** | **0.436** | **+10.8 %** [+0.021, +0.062] |
| incumbent TEI, linearly recalibrated | 0.407 | 0.429 | +9.0 % |
| candidates (ridge on history / on history + box score / empirical Bayes) | 0.390 / 0.378 / 0.399 | not read (none advanced) | — |

* **TEI beats raw persistence (0.413 vs 0.446), as its model card says — and that is all.** It is statistically indistinguishable from
  "regress last season's proxy toward the mean with an age curve" (holdout +4.4 %, interval [−0.006, +0.041]), and it is worse than
  a four-parameter regression on the player's own recent history in both holdout folds (+10.8 %; development +6.0 %). On this target
  the index adds no measurable forecasting value over statistics the product already has. It ranks no better than the simple
  methods either (Spearman 0.68 on the holdout against 0.71–0.73).
* **Where it is weakest** (holdout MAE, TEI vs history regression): rebound-heavy players 0.501 vs 0.418, age ≤ 22 0.443 vs 0.399,
  high-minute 0.441 vs 0.384; roughly level for age ≥ 31 (0.367 vs 0.355) and low-minute players (0.450 vs 0.439). Development shows
  the same ordering with smaller gaps.
* **Its shipped uncertainty band under-covers.** Evaluated against the incumbent's own target, `tei ± 1.2816σ` (nominal 80 %)
  contained **60.8 %** of development outcomes and **57.2 %** of holdout outcomes, at a mean width of 0.92 against 1.31 for an
  interval calibrated on the same training windows. Caveat: the band was designed around the season-to-season variability of
  the index rather than of the proxy; but the product presents it as the plausible range of a player's impact, and as such it is
  roughly a third too narrow.
* **What it knows about individual statistics is limited.** As a predictor of next season's true shooting, assist share, rebound
  share or usage, recalibrated TEI is 19–202 % worse than the history regression on the holdout (rank correlation 0.28–0.77). A
  composite is not supposed to forecast each of its parts, but it does mean TEI's components cannot be read as skill estimates.
* **Candidates did not change the picture.** The best candidate, a ridge on history plus the incumbent's box-score features, is 2.8 %
  better than the history regression on the primary target (player-level interval below zero, season-level interval including zero,
  not robust to the evaluation cut) and is **not** a replacement case. Most of its edge over TEI (8 %) comes from using
  *more history* and previous on-court net rating (which TEI ignores — and which, §3, is partly team context), not from a better
  measure of the player.
* **Team level.** Applying last season's scores to next season's roster (prospective, roster-known) and comparing with next season's
  win %: pooled R² development 0.36 (TEI) against 0.39–0.43 for the simple baselines; holdout 0.20 against 0.36–0.38. The
  concurrent R² against same-season win % is 0.63 (development) and 0.72 (holdout) for TEI; for the persistence baselines it is
  0.88–0.95 *because the proxy contains on-court net rating, itself a team result* — see `team_gate.py`; this comparison cannot
  reject anything for this target.

## 5. What the model reports but should not be read as

* A percentile of TEI is a position among *measured* players, not a rating. The product says so; the risk is in its use by
  Ask Pivot and the trade evaluator, where a TEI difference becomes "wins".
* TEI differences below roughly the forecast error (MAE ≈ 0.41 target z-units ≈ 1 index point, since the shipped TEI is 2.5 per
  unit of the scale its own validation used) are within noise for next-season value.

## 6. Output classification

| Output | Kind | How it should be validated |
| --- | --- | --- |
| `tei` (as served) | a **measured composite** of the (up to) 3-season window, with an implicit projection claim | measured: data integrity + reproducibility (met). Projection claim: out-of-time forecast error (R9.1 provides it: ≈ baseline). |
| `tei_offense`, `tei_defense` | measured sub-composites; `tei_defense` is box-score-only (blocks, steals, defensive boards) | construct validity unproven (R4 documented that every available criterion is circular); do not present as defensive ability |
| `tei_low/high` | a nominal 80 % forecast band | out-of-time coverage — **fails** (61 % development, 57 % holdout) |
| `availability` | games-played share of the window | not validated; separate model needed |

## 7. Validation gaps, ranked

1. **No independent target with demonstrated construct validity** exists in the repository for "impact". Every available outcome
   (PIE, on-court rating) shares information with the index or is team-confounded. This cannot be fixed with this data; it can
   be reduced with lineup/RAPM-style data (R6 deferred) or tracking-based defence.
2. **Uncertainty bands untested until now; they under-cover** (61 % / 57 % vs 80 %).
3. The persistence comparator was uncorrected; against fair simple baselines the index adds nothing measurable (and is 11 % worse than a history regression on the holdout).
4. One validation fold; low-minute targets included.
5. Hand-set weights never tested for sensitivity.
6. The team-level gate is concurrent and circular for any metric that includes on-court rating.

## 8. Reusable parts

The z-score and window machinery (`features.py`), the production scoring path (`score_players`, used unchanged as the
incumbent baseline), the model/estimate tables, the `ModelVersion` content-hash identity, and `uncertainty.py` for propagation
are all reused. What R9.1 added instead of replacing: an as-of view, a harness, candidate storage, a contract. **TEI itself is
untouched.**
