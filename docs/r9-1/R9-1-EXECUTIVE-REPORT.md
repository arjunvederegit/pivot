# R9.1 — Evaluation & model infrastructure: executive report

**Status: Complete, with two verification items owed before this touches your real database** (a PostgreSQL run, and the migration
on a copy of your real database — both listed in §9). Nothing live was changed. Nothing was promoted. No candidate beat the baselines.

Branch `feat/r9-1-player-intelligence-foundation` (from the R8.3 branch `95c8d99`, which is pushed and clean). Start with this page;
the evidence is in the other files in `docs/r9-1/`.

## 1. What R9.1 built

1. **A way to test any analytical method honestly.** A rolling-origin backtest: stand at the end of season *t*, use only what was known
   then, forecast season *t+1*, compare with what happened, move forward. Leakage is structurally prevented (the feature code can only see
   seasons up to the cutoff) and tested by an audit that destroys the future and checks nothing changes.
2. **A fair set of opponents.** Simple baselines — league average, last season, regression toward the mean, with an age curve, a small
   regression on the player's own history — and the **existing TEI run through production's own code**. Differences are put in a
   paired interval, resampling players *and* seasons.
3. **Candidate-only training.** A candidate model is a new row marked `candidate`, with its own estimates in a table nothing else reads.
   It cannot retire or overwrite anything, and it **proves** that: every table is hashed before and after, and any difference rolls it back.
4. **A pre-registered protocol.** Rules for advancing a candidate, a locked holdout read once and recorded in a ledger, reproducible
   artifacts (data fingerprint, library versions, seeds, fitted parameters, fold definitions).
5. **A typed contract** (`PlayerIntelligenceRecord`) that keeps *measured*, *inferred* and *projected* apart and refuses an interval that
   pretends to be a probability — the envelope R9.2 and R9.3 will use.

Reused: production's feature and scoring code (the incumbent baseline *is* that code), the `model_versions` table (extended, additively),
the existing CLI/Makefile conventions, the R8 season and provenance concepts. Added: `backend/app/modeling/`, one additive migration,
three CLI commands, 162 tests. Not touched: the frontend, any live rating, any API response.

## 2. Are existing models preserved?

**Yes, verified three independent ways.**
* On a copy of your development database, training three candidates left **all 38 tables byte-identical** under a separate hash that
  does not use the new code, and added only the candidates' own rows. Your real `tradelab.db` was never opened for writing (its hash is
  unchanged) and no dev process was touched.
* The three existing validation batteries produce **byte-identical output** on the R8.3 code and on the R9.1 code with candidates in the database.
* Mutation tests sabotage candidate training six different ways and require the safety net to fire every time.

The legacy `make train` still deletes the previous version's impact estimates, exactly as before — I did not change what it does by
default (it is now labelled LEGACY, and `ARGS=--preserve-history` is available). Use `make train-candidate` for experiments.

## 3. How does today's Player Impact Engine perform?

TEI is a **hand-weighted** composite of ten box-score z-scores; nothing is fitted. Measured as a forecaster of its own documented
target (next season's `0.6·PIE + 0.4·net rating`, a proxy — see the assessment for why it is not "impact"):

| | holdout error (MAE) | |
| --- | --- | --- |
| league average | 0.607 | |
| last season's value | 0.452 | the comparison in the model card |
| last season, regressed toward the mean, with an age curve | 0.418 | |
| regression on the player's own recent history | **0.394** | best simple method |
| **TEI, as shipped** | **0.436** | **10.8 % worse than the best simple method** |

* TEI beats raw "last season" (as its card says) but is **statistically no better than regressing last season toward the mean**, and
  worse than a four-parameter regression on the player's own history.
* It correlates **0.96** with same-season PIE, which is also 60 % of the target: it is largely a re-expression of one box-score
  statistic.
* Its **"likely range" under-covers**: it is meant to contain 80 % of outcomes; it contained **57 %** on the holdout (61 % in development).
* As a forecaster of individual statistics (true shooting, assist, rebound, usage) it is 19–202 % worse than the history regression — a
  composite is not meant to do that, but it means TEI's components cannot be read as skills.

None of this says TEI is useless as a descriptive summary, and the target is itself weak. It says TEI has **not been shown** to add
forecasting value over simple statistics the product already has, and its uncertainty band should not be trusted at its nominal level.

## 4. Did any candidate approach beat the baselines?

**No.** Three interpretable candidates — empirical-Bayes shrinkage, ridge on history, ridge on history plus box-score features — were
tested on six targets: **0 of 18** cleared the pre-registered rule, so none was taken to the holdout.

* The best case — the box-score ridge on the primary target — is 2.8 % better than the history regression, with a player-level interval
  below zero but a season-level interval that includes zero, and it only appears at a 250-minute evaluation cut. A weak signal, not a finding.
* Where the model-card-era approach (regress last season's rate toward the mean) matters most is **true shooting**: −11 % error against
  raw last-season, and a further −5 % from using several seasons. That is the evidence base for R9.2's first skill.
* **The first answer was different, and it was wrong.** Before an independent review, the rule and the baselines let 14 of 18 pairs
  advance ("7 % better than the best baseline"). That was an artefact: the candidates saw several seasons of history and the baselines
  saw one. The protocol was tightened **before any holdout read**, and the change is recorded openly. A negative result that survives a
  stricter test is more useful than a positive one that does not.

## 5. Is the historical evaluation reliable?

Reliable for what it claims, limited in what it can claim.
* **Reliable:** no temporal leakage found (a falsification audit passes at every development origin and fails when leaks are injected);
  every model is scored on identical rows; results are reproducible byte-for-byte from a clean database copy in a fresh process;
  coverage of the calibrated intervals is near nominal out of time (0.78–0.82).
* **Limits that matter:** (i) **46 % of the players Pivot scores are not in the evaluation** — they leave, miss the season or play little —
  so every figure is "given a rotation role next season"; (ii) COVID-shortened seasons are in every development fold; (iii) 10 seasons,
  one provider snapshot; (iv) no independent measure of "impact" exists in the data, so the evaluation measures *forecasting*, not
  *ability* — R9.1 makes no claim that any model measures ability; (v) the primary target's holdout was seen once in an engineering
  smoke test before the split was enforced in code (disclosed), and one holdout fold was the incumbent's own stored validation season.

## 6. GOAT advanced statistics: what can support R9.2?

Nothing new can be validated for prediction yet: R8.3 ingested **one season, one retrieval**, and a model can only be tested over time.
What the data does show (aggregates in [goat-feature-readiness.md](goat-feature-readiness.md)):
* **Complete and trustworthy identity:** all 7 families cover 100 % of the 379 rotation players; 97.4 % of them are high-confidence
  identity links, and all 14 medium-confidence links agree exactly with NBA.com on games and minutes (so they can be promoted to
  "verified" by an explicit rule — not silently).
* **Mostly redundant with what Pivot has:** most hustle counts (deflections, contested shots, box-outs, screen assists) and all the
  tracking/touch measures are 72–90 % explained by existing box-score features per 36 minutes (charges drawn and loose balls are the
  exceptions). **Shot-zone accuracy** is mostly new but sits on tiny samples (median 31 mid-range attempts a season). **Clutch** is far too
  small to be a skill (median 46 minutes). **Defended-FG% vs expected** is the most promising new signal (R² 0.27–0.43 on existing
  features; median 488 defended attempts), and the hardest to validate.
* **Needed first:** a bounded backfill (≈ 260 requests, ~30 s) with an **append-only** snapshot history — needs your approval.

## 7. What I recommend R9.2 do first

**Shooting efficiency, end to end** (true shooting, three-point and free-throw accuracy): measured value, an inferred ability with a
reliability that depends on attempts, a projected next-season value — served through `PlayerIntelligenceRecord`, with the interval's
measured coverage beside it, and with the bar set at the history regression R9.1 identified. It has ten seasons of data, the noisiest
stable rate (so correction matters), and a textbook sampling model. Then availability (the biggest unmodelled uncertainty), then the
backfill and a go/no-go on defence. Details, acceptance criteria and the R10–R14 connections:
[R9-2-IMPLEMENTATION-RECOMMENDATION.md](R9-2-IMPLEMENTATION-RECOMMENDATION.md). The three-milestone vision and later acceptance
criteria: [R9-VISION-AND-ROADMAP.md](R9-VISION-AND-ROADMAP.md).

## 8. Is R9.1 ready to close?

Against the completion criteria: model safety ✔ · evaluation infrastructure ✔ (targets, rolling origin, tested leakage guards,
baselines, populations documented, reproducible) · statistical credibility ✔ (three kinds of output distinguished in the contract and
docs; metrics matched to targets; sample-size limits stated; uncertainty measured, not asserted; **no improvement is claimed because
none was found**) · engineering ✔ (2,970 tests pass, 93 % coverage, ruff/mypy/alembic-check clean, existing batteries identical, no
frontend change, no secrets or raw provider data committed) · future readiness ✔ (reusable harness, a contract, a concrete R9.2 plan).
**Yes, with the two items below.**

## 9. Owed before activation, and decisions for you

1. **Run `make migrate` and `make train-candidate` against a PostgreSQL copy.** Not run here (none available). The known failure class
   (a 30-character version column) is fixed and statically tested, but only a real run closes it.
2. **Run the migration on a copy of your real database first.** It is additive (adds columns and one table, widens one column) and
   refuses to downgrade while candidates exist. Your real database is still at the R8-era migration and still needs the R8.1/R8.3
   migrations (see the R8.3 checkpoint).
3. **Approve (or not) the ≈ 260-request GOAT backfill** for R9.2's defence study. Not run.
4. **Decide when to merge.** Nothing is merged; the branch is pushed for review.
5. Do not run `make train` / `make score` on your real database before the 2026-27 statistics are ingested (R8.2's standing warning);
   `make train-candidate` is the safe way to experiment.

## 10. Where things are

`docs/r9-1/`: this report · [R9-VISION-AND-ROADMAP.md](R9-VISION-AND-ROADMAP.md) · [current-model-assessment.md](current-model-assessment.md) ·
[evaluation-architecture.md](evaluation-architecture.md) · [model-versioning-and-safety.md](model-versioning-and-safety.md) ·
[candidate-experiment-results.md](candidate-experiment-results.md) · [goat-feature-readiness.md](goat-feature-readiness.md) ·
[qa-and-validation-report.md](qa-and-validation-report.md) · [R9-2-IMPLEMENTATION-RECOMMENDATION.md](R9-2-IMPLEMENTATION-RECOMMENDATION.md) ·
[preregistration.json](preregistration.json) · [results/](results/) (aggregates only).
Code: `backend/app/modeling/`, `backend/alembic/versions/a7d3e1f9c204_r9_1_candidate_models.py`, `backend/scripts/r9_1_*.py`.
