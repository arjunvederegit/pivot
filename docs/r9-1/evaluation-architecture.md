# Evaluation architecture: the rolling-origin backtest

Code: `backend/app/modeling/`. Operate it with `make backtest`, `make train-candidate`, `make candidates`. Tests:
`backend/tests/unit/test_modeling_*.py`.

## 1. The question the harness answers

> At the end of season *t*, using only what was known then, how well does a method forecast a player's statistic in season
> *t+1* — and is that better than something much simpler?

It answers a **projection** question. It does not, and cannot, say that a method measures underlying ability
(see [R9-VISION-AND-ROADMAP.md](R9-VISION-AND-ROADMAP.md) §3).

## 2. Information cutoffs and the as-of discipline

| Rule | Mechanism |
| --- | --- |
| Features for a cutoff *t* see only seasons ≤ *t* | `PlayerSeasonPanel.as_of(t)` returns an `AsOfPanel` that **cannot be constructed** containing a later season (`LeakageError`). Feature code accepts only an `AsOfPanel`. |
| Outcomes are attached separately, after prediction | `pairs.attach_outcomes` takes the *target season's rows* and returns only outcome columns; predictors read feature columns by name. |
| Standardisation uses only data available at the cutoff | z-scores are computed **within a season, from that season's own league distribution**; the incumbent TEI is z-scored against the cutoff season's moments, exactly as production does. No moment is ever computed across a boundary. |
| Training pairs are complete at the cutoff | the fold for origin *t* trains on pairs *(s → s+1)* with *s+1 ≤ t*; an assertion in `run_backtest` checks every training target season ≤ *t*. |
| Hyper-parameters and interval calibration use only the training window | ridge penalty and shrinkage constant: leave-one-transition-out over the *training* origins; interval spread: residuals from the same leave-one-out fits. The held-out fold never touches either. |
| Present-state attributes cannot leak | `is_active`, `position`, `height_inches` (properties of *today*) are removed from every panel; team membership is the season's own `player_season_stats.team_id`, never `players`/`rosters`. |
| Injuries, contracts, provider "rank" fields | not features. (Injury data is current-state only; contract cap hit is endogenous.) |

### The leakage audit (run on official experiments)

`backtest.leakage_audit` is a **falsification test**: it re-runs a fold on a copy of the panel in which everything after the cutoff has
been destroyed — later numeric statistics permuted and rescaled, later rows **re-assigned to other players and other teams**, and **a
third of them deleted** — and requires the fold's **scored population, eligibility inputs, forecasts and intervals to match the real run
bit for bit** (and no forecast to be NaN). For the development stage the destroyed seasons include the locked holdout, so the audit also
shows that development cannot depend on them. It runs at **every development origin of the primary target** and at the last origin of
each other target.

Tested by injection (each must make the audit fail): a feature that reads next season's value; a scoring population restricted to
players who appear next season (survivorship); a feature shifted by next season's team. A clean run passes at every origin.

*What it cannot detect:* a leak that survives the destruction — information in the cutoff season or earlier, or in row *order*. The
structural guards above (`AsOfPanel`, the removal of present-state columns) are what cover those. Passing is evidence, not proof.

## 3. Folds and the locked holdout

10 seasons (2016-17 → 2025-26) give 9 transitions. A fold needs ≥ 2 training transitions, so there are 7 folds, with test
targets 2019-20 → 2025-26:

| | origin (cutoff) | forecasts | training transitions |
| --- | --- | --- | --- |
| dev 1–5 | 2018-19 … 2022-23 | 2019-20 … 2023-24 | 2 … 6 |
| **holdout 1–2** | 2023-24, 2024-25 | 2024-25, 2025-26 | 7, 8 |

Development may read only the first five. The holdout origins are **hard-coded** in the protocol (`experiment.HOLDOUT_ORIGINS`), so
ingesting 2026-27 cannot turn them into development data (a fold beyond the holdout belongs to a new protocol). The holdout is read by
`run_stage("holdout", …)`, which **requires the development run it confirms** and verifies it (same protocol version, same target,
same data fingerprint), evaluates only the baselines and the candidates that advanced — against the baseline that was best *in
development* — and **must** write to `holdout_ledger.json` (keyed by target; a re-read raises `HoldoutReadRefused` unless
acknowledged, and is then recorded as a re-read). The ledger lives in the experiments directory: it is discipline, not access
control — another output directory has another ledger.

Random train/test splits are not used anywhere.

## 4. Populations

| | rule | why |
| --- | --- | --- |
| **Scored at cutoff** | ≥ 200 minutes over the last 3 seasons ending at *t* (production's rule), including a player who **missed the cutoff season** but played earlier in the window | everyone Pivot would score; a forecaster blind to returners is not the product |
| **Evaluated** | scored players who played ≥ 500 minutes in the target season | the target is only measurable for players who played; below ~500 minutes a season's statistic is mostly noise |
| Training | the same rule applied to training pairs | one population, one selection |

**This is a conditional evaluation.** It says how well a method forecasts a player *given that they play a rotation role next
season*. Players who leave the league, are injured out, or fall below 500 minutes are not evaluated; the attrition table
reports how many: in the development folds roughly **46–49 % of scored players are not evaluated** (about a third have no
appearance in the next season at all; most of the rest play under 500 minutes). That is the single largest limit on what
these results say, and the reason availability needs its own model in R9.2. Every model is scored on **the identical rows**. Sensitivity to the cut (250 and 1,000 target-season minutes) is run as an
**evaluation-only** change — training keeps its own rule — and it matters: the one marginal candidate result flips with it
([candidate-experiment-results.md](candidate-experiment-results.md) §4).

Sample strata reported per model: age ≤ 22, age ≥ 31, new to the corpus (no earlier season in the data — a rookie proxy),
low-minute (window < 1,000), high-minute (≥ 3,000), changed team (outcome-side: a *reporting* stratum only), and a
stats-derived big-man proxy (top-quartile rebound share). Position labels are not used: they exist only for currently active
players in the database.

## 5. Targets

Defined once in `targets.py` with unit, exposure (the sample size behind the number) and interpretation.

| Target | Unit | Exposure | Notes |
| --- | --- | --- | --- |
| `impact_proxy` | z (season SDs) | minutes | **the incumbent's own documented objective**, 0.6·z(PIE) + 0.4·z(on-court NET_RATING). A proxy: PIE is a box-score share; NET_RATING is a team result confounded by teammates. Primary target. |
| `pie` | fraction | minutes | box-score share with no team result — the cleaner version of the above |
| `ts_pct` | fraction | true shooting attempts | the noisiest stable rate: where shrinkage should matter |
| `ast_pct`, `reb_pct`, `usg_pct` | fraction | minutes | very stable (year-over-year r 0.88–0.95): little room for any model to help |

All are *projected* quantities.

## 6. Models

Baselines (`predictors.py`): `league_mean`, `persistence`, `regressed_persistence` (OLS on last season),
`regressed_persistence_age` (+ age curve), **`ols_history`** (OLS on last season, the windowed history and age — the simple baseline
that has the candidates' own information; added after the review found the baselines lacked it), `incumbent_tei` (production TEI at the
cutoff, as shipped, ÷ 2.5 — its own validation's scale; `impact_proxy` only), `incumbent_tei_recalibrated` (linear map onto the target's
scale; every target: what does the existing estimate know about this quantity?). The incumbent is computed by
**production's own functions** (`recency_weighted_features` → `score_players`) applied to an as-of frame; a test shows the
candidate pipeline reproduces the stored active estimates to 1e-9.

Candidates: `eb_shrinkage` (exposure-aware empirical-Bayes shrinkage toward an age-adjusted league mean),
`ridge_history` (ridge on the player's own history), `ridge_boxscore` (ridge on history plus the incumbent's box-score
z-features). All are closed-form, deterministic, and small enough to read.

## 7. Metrics

| Metric | Why this one |
| --- | --- |
| MAE (headline) | robust to the heavy tails of low-minute players; in the target's own units |
| RMSE | punishes the large misses a trade decision cares about |
| Bias | systematic over/under-prediction |
| Spearman, **computed within each fold and averaged** | "does it order players correctly?"; undefined (`None`) for a constant forecast; pooling folds first lets between-fold level differences pose as ranking (a bug the tests caught) |
| Interval coverage + mean width, together | a narrower interval that under-covers is worse |
| Team-level R² vs win % (`team_gate.py`) | the gate that rejected two earlier proposals; see caveats |

**Paired comparison.** For two models on identical rows, the statistic is mean(|e_A| − |e_B|) with a 95 % **player-cluster
bootstrap** interval (2,000 draws; a player's rows across folds are resampled together because they are not independent).
Reported per fold as well. It is an interval for *this sample of players and seasons*; it is not a claim about future seasons
and no "significance" is asserted beyond the pre-registered rule.

**Intervals.** `intervals.py`: residuals from leave-one-transition-out fits on the training window, spread modelled as
`a + b/exposure`, half-width = empirical 80 % quantile of |r|/s times s. The 80 % is **nominal**; the backtest measures what
it delivers.

**Team gate.** Concurrent (players on the team in season *t*, weighted by season-*t* minutes, vs that season's win %) and
prospective-roster-known (the same scores aggregated over the players on the team's *t+1* roster — which assumes the next
roster is known, so it is optimistic and used only to compare models under the same assumption). **For `impact_proxy` it is
partly circular**: the proxy contains on-court net rating, which is itself a team result, so a metric that carries it will
correlate with wins by construction. It is a gate for `pie` and `impact_proxy` only and is a sanity check, not evidence.

## 8. Decision rule (pre-registered; v2 after three conservative amendments)

The reference is the baseline with the lowest pooled development MAE (it was `ols_history` for all six targets). A candidate advances
to the holdout iff, on the five development folds, **all** of:

1. the 95 % player-cluster bootstrap interval for MAE(candidate) − MAE(reference) is entirely below 0;
2. the 95 % **fold-level** t-interval (4 d.f.) of the five per-fold differences is entirely below 0 — the seasons are a dependent
   sample too, and a player-level interval treats them as fixed;
3. it has lower MAE than the reference in ≥ 80 % of folds (4 of 5);
4. its MAE is ≥ 2 % lower (a practical floor: a gain of 0.0003 on a rate that is forecast to within 0.03 is not a reason to ship a model);
5. pooled coverage of the nominal 80 % interval is in [0.75, 0.85];
6. (`pie`, `impact_proxy` only) the 95 % team-season bootstrap interval of R²(candidate) − R²(reference) for **prospective,
   roster-known** win % is not entirely below 0 — and **if it cannot be computed the check fails**, it is never skipped.

A candidate is *confirmed* iff on the two holdout folds the pooled player-cluster interval against the development-chosen reference
is entirely below 0 and it improves in both. The rule is statistical-plus-practical; judgement of whether a confirmed gain is worth
acting on belongs to the review, not the gate. A planted-signal test shows that the rule *can* say yes (a real effect advances in
development and is confirmed on the holdout), and a set of load-bearing-check tests show that each of the six conditions independently
stops an advance.

**Multiplicity.** 6 targets × 3 candidates are examined. Only `impact_proxy` is confirmatory; the other five are exploratory and no
correction is applied — their intervals are descriptive. Zero of eighteen advanced, so the question of the family-wise error rate of
a "yes" does not arise in R9.1; it will in R9.2, and the family should be declared before it is run.

**Amendment history** (all after the first development run, none after any holdout read, each more conservative): see
`PREREGISTRATION["amendments"]` and [candidate-experiment-results.md](candidate-experiment-results.md) §1.

## 9. Reproducibility

Experiment id = `exp-<stage>-<target>-<hash of config>-<hash of data>`. Re-running the same stage on the same data reproduces
`metrics.json`, `decisions.json`, `fitted_parameters.json` byte for byte (tested); only `run.json` (timestamp, runtime) differs.
Artifacts and what each records are listed in [model-versioning-and-safety.md](model-versioning-and-safety.md) §6.
