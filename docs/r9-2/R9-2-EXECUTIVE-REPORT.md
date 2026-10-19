# R9.2 — Reusable Player Intelligence & Skill Engine: executive report

**Status: {{STATUS}}** · Branch `feat/r9-2-player-skill-engine` (from R9.1 `40f8602`) · Not merged · Nothing live was changed: no TEI rating, no stored estimate, no active model, no existing route.
Start here; the evidence is in the other files in `docs/r9-2/`. What "validated" means in that status line is spelled out in §6: the engineering and the *testable* statistical gates are met, every model-derived output is a
labelled **candidate**, and nothing was promoted.

## 1. In one paragraph

Pivot now has a backend **Player Intelligence Engine**: one service that returns typed records separating what a player *did* (measured), what we can *infer* about his underlying ability (inferred, with a reliability) and what
we can *project* (next season, with its condition stated) — each carrying its sample, model version, data provenance, uncertainty and limitations, and *declining*, with a reason, whatever the evidence does not support.
Shooting (true shooting, three-point accuracy, free-throw accuracy) is built end to end. For three-point and free-throw accuracy a sampling-aware state-space model beats the regression the protocol named by **3–6% out of time,
confirmed on seasons no protocol had read** — but an independent review showed that regression ignores how much evidence each player has, and against one that does the margin is **about 1.5%** (development only), below the
bar R9.2 set. For true shooting the same idea helps in development but is **not confirmed**, so the served true-shooting projection is the baseline, labelled a benchmark. Playmaking, rebounding, usage/role and games played are
available as measured values only, on purpose. Defence, shot profile and an overall rating are *unavailable*, and say why. Everything model-derived is a labelled **candidate**: nothing is promoted.

## 2. What the engine actually does

| | |
| --- | --- |
| **One retrieval path** | `PlayerIntelligenceService.get(player_id, quantities, as_of, kinds, model_version, include_experimental)` (+ `get_many`, `catalog`) → typed `PlayerIntelligenceRecord`s + `Unavailable` reasons + context. Read-only. |
| **Measured** (11 quantities) | true shooting, three-point %, free-throw %, assist %, assist/turnover, total/offensive/defensive rebound %, usage %, minutes per game, games played — with the attempts/makes/minutes/games behind each, the source, and integrity checks. Integer makes/attempts are **recovered from NBA.com's rounded per-game figures** by constraint reconstruction (exact for 82% of player-seasons; 0 unconstrained; validated against 573 exact totals). Where the rounding leaves the attempt count undetermined the record says so and gives the range; no attempts ⇒ *no rate* (never 0%). |
| **Inferred** (shooting) | a Gaussian state-space model with a known, attempts-based sampling variance: ability + credible interval + **reliability** + the weight on the player's own record + effective attempts. Withheld below reliability 0.30. Experimental, development-only evidence. |
| **Projected** (shooting) | next-season rate for a player who plays ≥ 500 minutes (and ≥ 50 attempts) — *conditional on playing*, said on every record. Three-point and free-throw: the state-space model (experimental). True shooting: `ols_history`, a **benchmark** that claims no improvement. Interval with its measured out-of-time coverage. |
| **Not offered** | inferred/projected playmaking, rebounding, usage, availability (measured is already close to the best estimate, or the quantity is a role, not a skill); defence, shot profile, shot creation, overall rating — with reasons. |
| **Safety** | `train_candidate` stays the only write path; candidates cannot appear as validated, cannot borrow evidence they were not evaluated on, cannot be served when their evaluation failed or the registry withholds them; a read never writes; existing routes unchanged; the preview route is off by default. |

## 3. What was demonstrated, and what was not

Out-of-time mean absolute error (MAE), shooting rates; relative = against `ols_history`, the regression the protocol pre-registered as the reference (it does not use attempts). Folds: five development origins
2018-19…2022-23, two locked holdout origins 2023-24 and 2024-25.

| Quantity | Served projection | Development (vs `ols_history`) | Holdout (vs `ols_history`) | vs a regression that knows attempts (development, exploratory) | Status |
| --- | --- | --- | --- | --- | --- |
| Three-point accuracy | state-space | −4.4%, 5/5 folds | **−3.2%**, CI [−0.00195, −0.00010], first read | −1.5% — below the 2% floor | experimental |
| Free-throw accuracy | state-space + role | −5.3%, 4/5 folds | **−6.5%**, CI [−0.00446, −0.00130], first read | −1.4%, interval includes 0 — below the floor | experimental |
| True shooting | `ols_history` | candidates −2.6% / −2.8% | −1.9%, CI [−0.00130, +0.00018] — **not confirmed** (and a re-read) | −2.1% (best of the three) | benchmark; no improvement claimed |
| Inferred ability (all three) | state-space | forecasts non-inferior to the reference; the model's own 80% predictive interval covered 78–84% of development outcomes | not claimed | — | experimental, development-only |
| Availability (appears next season / share of games) | **not served** | AUC 0.92; two-part model beats persistence | — | — | studied, not built |

Honest limits, in plain words:

* The three-point result is **probable and small**: at the 100-attempt sensitivity cut it fails the pre-registered rule, and it would not survive a multiplicity correction across the five holdout comparisons. Free throws are
  stronger (z ≈ 3). Neither is promoted.
* Every projection is a forecast **conditional on playing a rotation role next season**: only 46% (3P), 44% (FT), 54% (TS) of the scored players qualify, and 31% do not appear at all.
* The 80% prediction interval's measured out-of-time coverage is reported with each projection; the **ability** interval is a *credible* interval from the model (no coverage claim). The free-throw holdout coverage (0.853) lies
  outside the pre-registered 0.75–0.85 band; no decision depends on it.
* Inferred ability over-predicts rare shooters; below reliability 0.30 it is withheld, and players selected on *not* playing next season stay over-predicted above that line (stated in the docs).
* The existing TEI **uncertainty band**, nominally 80%, covers **60.8% (development) / 57.2% (holdout)** of outcomes. R9.2 labels it everywhere it is shown as an uncalibrated model range and does not recalibrate it.

## 4. Which data

* **NBA.com history (10 seasons)** feeds everything served.
* **BALLDONTLIE GOAT** fed **no model** and **no new requests were made** (0). It was used for cross-sectional questions on its one ingested shot-zone season (corner vs above-the-break three-point accuracy: noise-corrected correlation 0.87 [0.50, 1.69]; free throws vs three-point 0.48 [0.18, 0.84]) and to corroborate 14 of 15 medium-confidence identity links (which stay out of training). The ≈ 260-request historical backfill was re-examined and **deferred**: the identity crosswalk covers *active* players only, so a backfill would be survivor-biased until historical identity is solved (plan in `goat-intelligence-assessment.md`). **Which GOAT data improves analytical capability today: none; what would: per-zone and tracking families over several seasons, once identity is solved.**

## 5. Existing behaviour

* **No migration**; the three existing validation batteries (comparable, acquisition, adversarial) produce **byte-identical** output on the R9.1 baseline and on R9.2, on identical copies of the same database.
* **Protected state:** on a copy of the real database, materialising the candidate models changed only the candidate tables; the 19 existing model versions and 512 TEI estimates hash identically before and after, on SQLite and PostgreSQL.
* **Frontend:** wording only (the TEI band is called a "model range", with its measured coverage); 311 frontend tests, `tsc` and eslint pass. No redesign.
* **Tests:** the full backend suite passes (3,180 passed, 19 environmental skips; R9.1 had 2,970 passing); the R9.2 and PostgreSQL-compat tests pass on **PostgreSQL 16** as well (386). SQLite and PostgreSQL produce **bit-identical** candidate rows.
* **Secrets / data:** no credentials or raw licensed rows are committed (aggregates only); no provider request was made.

## 6. Is R9.2 complete? What remains

{{COMPLETE}}

## 7. Decisions for the owner

1. **Activation checkpoint (yours).** Run `make materialise-skills` on a *copy* of your real database (PostgreSQL if that is the target) before pointing anything at the preview route; it writes only labelled candidates and refuses to overwrite. The embedded-PostgreSQL run here used a copy of the real SQLite data.
2. **Preview flag.** `player_intelligence_preview` is off; turn it on only where you want to look at the records. Nothing consumes the service yet (R9.3).
3. **GOAT backfill.** Approve only after historical identity is solved; the bounded plan and its request cap are in `goat-intelligence-assessment.md`. Medium-confidence links must not enter training until their promotion rule is applied.
4. **R9.3 reference baseline.** I recommend pre-registering an attempts-aware regression as the reference (the review showed the named baseline was too easy).
5. **TEI band / trade evaluator.** The trade Monte Carlo derives σ from the TEI band, which is ≈ 1.5× too narrow in practice. Recalibrate (R9.3) or accept the labelled limitation.
6. **Merge.** The branch is pushed and unmerged; merge timing is yours.

## 8. Where things are

* Code: `backend/app/player_intelligence/`, `backend/app/modeling/` (R9.1 harness, extended), `backend/app/api/v1/player_intelligence.py`; scripts `backend/scripts/r9_2_*.py`; tests `backend/tests/unit/test_pi_*.py`, `backend/tests/integration/test_player_intelligence_api.py`.
* Documents (`docs/r9-2/`): [skill-engine-architecture.md](skill-engine-architecture.md) · [shooting-intelligence-methodology.md](shooting-intelligence-methodology.md) · [shooting-validation-results.md](shooting-validation-results.md) · [multidimensional-skill-coverage.md](multidimensional-skill-coverage.md) · [goat-intelligence-assessment.md](goat-intelligence-assessment.md) · [model-safety-and-promotion.md](model-safety-and-promotion.md) · [qa-and-regression-report.md](qa-and-regression-report.md) · [FINAL-QA-SIGNOFF.md](FINAL-QA-SIGNOFF.md) · [R9-3-IMPLEMENTATION-RECOMMENDATION.md](R9-3-IMPLEMENTATION-RECOMMENDATION.md) · committed aggregates in [results/](results/).
