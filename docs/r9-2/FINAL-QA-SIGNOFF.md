# R9.2 — Final QA sign-off

**Final status: R9.2 COMPLETE — VALIDATED**

Branch `feat/r9-2-player-skill-engine`, built from the verified R9.1 commit `40f8602`; not merged. Executive summary: [R9-2-EXECUTIVE-REPORT.md](R9-2-EXECUTIVE-REPORT.md). Detailed findings, fixes and tests per round:
[qa-and-regression-report.md](qa-and-regression-report.md).

## 1. Did independent reviewers genuinely inspect the implementation?

**Yes — with stated limits.** Three QA rounds, a retest and a final verification pass were performed by **separate sub-agents started fresh for each task** (they had no access to the author's reasoning, only a brief naming the commit, the documents and what to
attack, and an instruction to report rather than fix). None of the reviews was performed by the primary development agent. Each reviewer wrote and ran its own scripts and reported the commands, versions, databases (copies) and counts; the
reports contain results that cannot be produced by reading: an independent Kalman recursion matching the stored weights to 4.4e-16, a deliberate leak that the existing audit passed, 26 source-level mutants (6 survived and were
then killed with new tests), failure injection and concurrent materialisation on SQLite and PostgreSQL, a clean-clone re-run of a full development experiment that reproduced the committed artifacts byte for byte, and a
read-only survey of all 573 current players. The reviewers were AI agents of the same model family as the author — no human statistician reviewed this — and no round was permitted to run a holdout evaluation. The
author's own checks (regression tests, mutation, PostgreSQL and clean-environment runs) are listed separately from the reviewers' in the report.

## 2. Final numbers

| | |
| --- | --- |
| Backend suite (SQLite), final code | **3,188 passed · 19 skipped (environmental, pre-existing) · 0 failed** (3,207 collected). R9.1 baseline: 2,970 passed |
| R9.2 + compat + modeling tests on PostgreSQL 16 | **393 passed, 1 skipped** |
| R9.2 service / mutation / review / API tests in reverse order | **182 passed** (order-independence) |
| Frontend | **311 passed** (22 files) · `tsc` clean · eslint clean |
| `ruff` (R9.2 files) / `mypy app` | clean / clean (188 files) |
| Existing validation batteries | **byte-identical** on R9.1 baseline vs R9.2 (identical database copies) |
| Protected state | 19 existing model versions (`16a96351ab50fc1b`) and 512 TEI estimates (`04677fdff5754c5f`) hash-identical before/after materialisation, SQLite and PostgreSQL |
| Materialisation | 4 candidate versions × 632 estimates; second run `reused_existing: true` ×4 on both backends; SQLite vs PostgreSQL max abs difference **0.0** over 4,424 rows |
| Experiment reproduction | all 3 development experiments re-run from a clean clone, same seed: run ids, `metrics.json`, `decisions.json`, `attrition.json`, `leakage_audit.json`, `data_snapshot.json` byte-identical; data fingerprint `56bff0c2…` |
| Mutation testing | Round 2: 9 mutants (5 survived) → tests added; Round 3: 26 mutants (20 killed, 6 survived) → tests added; retest: retest of Round 3's fixes: 57 mutants (6 old all killed; 30 of 51 new killed) → tests added, the author's 18 re-derived mutants then all killed; final pass: 46 more mutants, 37 killed, 8 survivors (+1 equivalent), all test gaps (two closed, the rest listed in the QA report) |
| QA findings | Round 1: 13 (2 high, 1 medium-high); Round 2: 14 (2 critical, 2 high); Round 3: 14 (1 high, 1 medium-high); retest: retest: 1 high (wrong-unit attempt range on bounded true-shooting records, fixed), 2 medium, 6 low; final verification: **0 critical, 0 high**, 1 medium (inferred label cap, fixed), 7 low. Every critical/high finding fixed with a regression test |
| Headline numbers | three-point MAE −4.4% dev / −3.2% holdout; free-throw −5.3% dev / −6.5% holdout; true shooting −2.6% dev / −1.9% holdout (**not confirmed**) — all vs `ols_history`; vs an attempts-aware regression ≈ −1.5% / −1.4% / −2.1% (development) |

## 3. The three QA rounds

| Round | Reviewer's task | Target | Findings (severity) | Resolution | Retested by |
| --- | --- | --- | --- | --- | --- |
| **1 — statistical** | falsify the statistical conclusions: independent filter, profile likelihoods, selection analysis, stronger baselines, leakage demo, number checks, pre-registration/holdout integrity | `526aacd` | 13: 2 high (inferred bias for low-evidence players; baseline not the strongest), 1 medium-high (3P fragile), 3 medium, 3 medium-low, 4 low | headline restated; reliability floor; true weight and effective attempts; structural outcome-hiding + invariance test; baseline-challenge, selection and ablation artifacts; docs corrected | Round 3 (verification table) |
| **2 — engineering/data** | integrity of every read/write path: tests on SQLite and PostgreSQL, reverse order, mutation, failure injection, docs claims | `526aacd` | 14: 2 critical (evidence borrowed by method name; stale failure served), 2 high (non-idempotent re-materialisation; withheld bypass), 7 medium, 3 low | all fixed with regression tests; evidence gated on the model's own configuration; failed/stale/withheld enforced; idempotence | Round 3 (verification table) |
| **3 — adversarial/reproducibility** | clean-clone reproduction, adversarial integration cases, concurrent runs, API abuse, 26 mutants, docs vs behaviour | `f02948d` | 14: 1 high (old stores "reused"), 1 medium-high, 5 medium, 7 low | all fixed with regression tests; the 6 surviving mutants given killing tests | Retest reviewer, then the final verification pass |
| **Retest of round 3's fixes** | re-run each reproduction, re-derive the 6 old mutants, plant new ones aimed at the new code, hunt regressions | `ada003c` | 9: 1 high (bounded true-shooting range in the wrong units), 2 medium, 6 low; plus 21 surviving mutants | all fixed with regression tests (`206672b`); re-verified by the final pass (§ below) | — |
| **Final verification pass** | re-verify the retest fixes on all 573 exact-totals players, on SQLite and PostgreSQL, tamper tests, 46 more mutants, regression hunt | `206672b` | 8: **no critical/high**; 1 medium (inferred label could read `holdout_confirmed` for a pre-schema version named explicitly), 7 low | the medium and the testable lows fixed with tests; the rest disclosed (QA report §6b) | — |

## 4. Acceptance table

Statuses: ✅ met · 🟡 met with a stated limitation · ➖ explicitly unavailable/not built (permitted by the spec) .

| # | Acceptance criterion | Status | Verification evidence | QA findings → fixes | Outstanding limitation |
| --- | --- | --- | --- | --- | --- |
| 1 | R9.1 model-preservation infrastructure intact | ✅ | R9.1 tests unchanged and passing (3,188 passed / 0 failed); protected-state fingerprint identical before/after; `train_candidate` still the only write path | R2-F1, R3-N1: ability rows initially broke reuse; fixed, every column compared | — |
| 2 | Migration compatibility verified | ✅ | no migration in the diff; `alembic upgrade head` + `check` on fresh PostgreSQL 16 and on a real-data copy; 393 PostgreSQL tests; materialisation on PostgreSQL | — | the owner's own PostgreSQL copy is the owner's activation step |
| 3 | One reusable player-intelligence service | ✅ | `PlayerIntelligenceService.get/get_many/catalog`; 37 service tests, 7 API tests; Round 3's survey of all 573 current players, all kinds | R2-F12 N+1 partly fixed (146 statements for a 15-player roster) | per-(player, quantity, kind) lookups remain |
| 4 | Typed records distinguish measured / inferred / projected | ✅ | contract validators (measured has no model; projection needs cutoff < target and `conditioning`; credible interval carries no coverage claim); contract tests | — | — |
| 5 | Model version and source provenance preserved | ✅ | every record carries `ModelRef` (version, method, status, evaluation, data fingerprint, seed) and source rows | R3-N2/N3/N4: provenance can no longer be asserted by a caller or borrowed | — |
| 6 | Historical as-of supported | ✅ | `as_of` filters measured rows and selects stored models by cutoff; panel-invariance test deletes later seasons and rebuilds | R3-N8 `as_of` normalised | only versions materialised at a cutoff are available for that cutoff |
| 7 | Measured shooting is reproducible | ✅ | integer counts reconstructed (4,487 exact / 996 bounded / 0 unconstrained of 5,483); vs 573 exact totals the truth lies in the reported range 573/573 and is never called "no attempts" | R2-F6, R3-N6/N7 honest ranges, plausibility bounds | bounded rows carry a range, not a point |
| 8 | 3P and FT accuracy account for attempts | ✅ | attempts-based sampling variance in the state-space model; reliability and effective attempts on each record | R1-F7 | plug-in variance bias disclosed (R1-F4) |
| 9 | True shooting uses appropriate inference | ✅ | delta-method ratio-estimator variance on TSA = FGA + 0.44·FTA (Monte-Carlo-checked: ratio 0.998) — not a binomial approximation | R1-F6 circular agreement claim relabelled | TS projection **not confirmed** → baseline served, labelled |
| 10 | Priors/shrinkage estimated from eligible historical data | ✅ | hyperparameters fitted per fold on training seasons only (one-step-ahead predictive likelihood); fitted parameters committed | R1-F5 `at_bound` reporting | ρ on its bound for some folds; φ is a nuisance factor |
| 11 | Inferred ability carries uncertainty | ✅ | credible interval (level checked), reliability, weight, effective attempts; floor at 0.30 | R1-F1 bias for rare shooters → floor + disclosure | over-prediction for players who later lose minutes remains above the floor |
| 12 | Projections historically validated before trusted | 🟡 | pre-registered protocol committed before the first run; 5 dev + 2 holdout folds; holdout first-read for 3P/FT (TS a declared re-read); 21 leakage audits | R1-F3 fragile 3P stated; nothing is "trusted": status experimental/benchmarked | 3P is "probable, small"; TS not confirmed |
| 13 | Comparison includes R9.1's strongest relevant baseline | ✅ | `ols_history` (pre-registered reference) **and**, after review, attempts-aware regressions | R1-F2 | against the stronger baselines the margin is below the 2% floor (stated as the headline) |
| 14 | Interval coverage evaluated and communicated accurately | ✅ | forecast-interval coverage by fold; native interval; FT holdout 0.853 stated as outside the band; TEI band measured 60.8% / 57.2% vs nominal 80% | R1-F6/F10, R2-F10 | trade evaluator σ not recalibrated |
| 15 | Playmaking, rebounding, usage/role have measured representations | ✅ | assist %, AST/TO, REB/OREB/DREB %, USG %, minutes per game, games played — measured only, with integrity checks | R3-N7 plausibility bounds | no inferred/projected versions (by design: see coverage document) |
| 16 | Unsupported dimensions explicitly unavailable | ✅ ➖ | defence, shot profile, shot creation, overall rating → `Unavailable` with reasons; unknown names 422 | — | — |
| 17 | Availability and defence requirements documented/prioritised | ✅ | availability study (AUC 0.92, two-part model beats persistence; development only) and requirements in the coverage and R9.3 documents | — | availability/defence **not built** (not required) |
| 18 | No arbitrary overall skill score | ✅ | `overall_skill_rating` is `Unavailable`; no composite exists | — | — |
| 19 | Architecture supports further validated dimensions | ✅ | quantity registry + validation registry + one gating rule; R9.3 recommendation | — | — |
| 20 | GOAT data evaluated and used where justified | ✅ | assessed: no model input (identical to NBA.com / single season); cross-sectional zone study; 14/15 medium links corroborated | — | none of GOAT's data improves capability today (stated) |
| 21 | Provider identities and confidence thresholds respected | ✅ | no provider ids/name matching in the service; medium links never enter training | — | — |
| 22 | New advanced features not treated as validated skills | ✅ | none added to a model or served as validated | — | — |
| 23 | Any backfill bounded, traceable, non-destructive | ✅ (none run) | 0 requests made; plan bounded ≤ 300 requests and deferred for survivor bias | — | backfill deferred, awaits owner and an identity fix |
| 24 | Final report states which GOAT data improves capability | ✅ | executive report §4, GOAT assessment | — | — |
| 25 | Candidate experiments do not modify active models | ✅ | fingerprint of all 38 tables; 19 versions / 512 estimates hash-identical; isolation/rollback tests; injected failures leave nothing behind | R3: failure injection ×3 on both backends held | — |
| 26 | Historical outputs remain reproducible | ✅ | clean-clone experiment reproduction; idempotent materialisation on both backends; SQLite = PostgreSQL bit for bit | R2-F1, R3-N1/N9 | — |
| 27 | Existing analytical outputs preserved | ✅ | three batteries byte-identical; TEI untouched | R2-F10: band texts relabelled (wording only) | — |
| 28 | Relevant regression tests pass | ✅ | 3,188 passed, 0 failed, SQLite; 393 on PostgreSQL; frontend 311 | — | 19 pre-existing environmental skips |
| 29 | Leakage and missing-data edge cases tested | ✅ | audit on every fold (21); outcome columns hidden from predictors; panel-invariance test; null / missing / percent-unit / negative / duplicate-season cases | R1-F8, R3 | the audit alone cannot see panel-construction bugs (the invariance test does) |
| 30 | QA findings addressed or documented | ✅ | all critical/high fixed; remaining items documented in the QA report §5 and §8 | see §3 | R1-F4, F11, F12, trade σ, N+1 remain (disclosed) |
| 31 | No secrets or licensed raw data committed | ✅ | aggregates only; databases and pickles git-ignored; no provider call; `git diff` reviewed | — | — |
| 32 | Existing frontend behaviour preserved | ✅ | wording-only change; 311 tests, `tsc`, eslint | — | not visually reviewed |
| 33 | No changes merged into main | ✅ | branch only | — | — |

## 5. Outstanding limitations (the list to hold in mind)

1. Three-point improvement is small and fragile; free-throw is stronger; true shooting is not confirmed — all labelled, nothing promoted.
2. Against an attempts-aware regression the state-space margin is ≈ 1.5%, below the 2% bar R9.2 set (development only; the holdout was not re-read).
3. Inferred ability is validated only through the forecasts it supports; it over-predicts rare shooters (floor 0.30), and the volume–ability relationship is not modelled.
4. Projections are conditional on playing a rotation role; availability is studied, not served.
5. Defence, shot profile, shot creation and an overall rating are unavailable; playmaking/rebounding/usage are measured only.
6. GOAT contributed no model input; the backfill is deferred.
7. The TEI band is miscalibrated (≈ 60% coverage for a nominal 80%) and is labelled, not fixed; the trade evaluator's σ inherits it.
8. All reviews were AI-performed; none by a human; no holdout re-evaluation by any reviewer.
9. The owner's activation checkpoint (a PostgreSQL run on a copy of the real database) is still the owner's step.
