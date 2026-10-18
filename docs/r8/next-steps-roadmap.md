# From R8 to R9 and beyond

*Principle carried from the brief: every addition must improve the intelligence available to more than one
tool; feature count is not progress.*

## 1. R8 remediation still owed (small; do first)

| # | Item | Why | Size |
| --- | --- | --- | --- |
| R8.1 | **Run the trial and record the verdict** ([criteria](balldontlie-goat-evaluation.md#5-objective-subscription-criteria)); then *persist* BALLDONTLIE records behind the canonical layer — statistics into `player_season_stats`/`team_season_stats` with `season_type`, contract years into `contract_years` with explicit `cap_hit`/`base_salary`/`total_cash` columns — **only if the trial passes** | the adapter emits records; nothing stores them, deliberately | M |
| R8.2 | Re-express the `nba_api` provider as `ProviderResult[...]` of canonical records (a thin wrapper over the existing normalizers) | makes the second provider a real swap, not a claim; validation-report exception 2 | S |
| R8.3 | **Season rollover procedure**: a tested `make rollover SEASON=2026-27` (sync that season's rosters, verify `current_roster_clause`, flip `CURRENT_SEASON`, assert no consumer still reads the old one); audit V3 | the 2026-27 tip-off is 2026-10-20 | S |
| R8.4 | Surface `data_status` in the UI where a decision is shown (team page, trade evaluator): a single "Data: 89 days old" chip driven by the field that exists | the backend support is done; the redesign was preserved on purpose | S (frontend) |
| R8.5 | Gate public asset serving on `usage_status` (serve only `licensed`/`dev_only` in non-production) and record `license_ref` as agreements are signed | converts the new column from a label into a control | S |
| R8.6 | Run the migration and the suite on **Postgres**; add the `contracts` natural key once a contract source is loaded | not exercised in R8 | S |
| R8.7 | Replace the 2026-27 cap YAML's `source_url` (a Spotrac article) with the NBA release | primary-source citation | trivial |
| R8.8 | Raise coverage of `balldontlie/evaluation.py` (63 %) when the first real recording exists — use it as a replay fixture | tests should follow reality | S |

## 2. The shared services R9–R14 need (build once, consumed by several tools)

| Service | Consumers | Status |
| --- | --- | --- |
| **Persisted skill vector** (`player_skills`, long form, `model_version_id`, schema fingerprint) | R9, R10, R11, R12 | specified in `docs/pivot/r8-readiness.md` §3; not built |
| **One evaluation entry point** (`evaluate_trade`) | R13, R14, Ask Pivot `simulate_trade` | specified there §2; not built |
| **Transaction ledger** — dated signings, extensions, options, waivers, trades with a rule era | R12 (matching), R13, R14 legality, CBA engine | partially exists (565 parsed trades); signings/waivers not parsed |
| **Roster-as-of-date reconstruction** from the ledger | R12, R13 | not built; R8 membership is observed-from-now only |
| **Canonical identity + provenance** | all | **done in R8** |
| **Constraint set for the CBA** (apron team salary, cap holds, exceptions with dates, roster counts, aggregation, recently-signed, Stepien/frozen picks) | R14, trade evaluator | 5 of ≈ 22 inputs exist (see [research §C](resource-research.md)) |

## 3. R9–R14: what each needs, in the order that pays

| Release | Question it answers | Data **already available** | Data **missing** | Method | Validation | Shared services | Difficulty |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **R9 Player Intelligence** | How good is this player, with what uncertainty, and how is it changing? | 10 corpus seasons of Base/Advanced/estimated per-player rows (2016-17→); TEI with bands; age curves; availability from games played | **injuries/availability**; **defensive, clutch and shot-zone families**; possession-level data for a regularised +/- (PBP/lineups — nothing licensed) | empirical-Bayes shrinkage of per-minute rates by minutes; a box-score impact model (reimplement BPM-style on *licensed* inputs); a ridge-regularised (RAPM-style) layer only if PBP is licensed | rolling out-of-sample backtest t→t+1 across 9 seasons: rank correlation, interval coverage/calibration, vs a naive prior and vs private benchmarks (EPM/LEBRON/DARKO as references, not inputs) | persisted skill vector; `SourceMeta`; stat registry | M |
| **R10 Archetypes** | What *kind* of player, with soft membership? | 9-dimension skill vector (frozen label map R4-3) | shot-zone, playtype and tracking families; role benchmark | multi-membership soft assignment (mixture model / NMF) over the skill vector; frozen, append-only labels | resampling stability; season-to-season persistence; label recovery on synthetic planted roles (existing pattern); relation to team results; compare to BBall-Index's 12 roles privately | skill vector; **multi-membership migration** (readiness §4) | M |
| **R11 Roster Needs** | Where is this roster actually thin, in marginal wins? | roster aggregates, rotation allocation, needs percentiles, standings | **availability/injury**, schedule context, lineup minutes with usable samples (five-man refused on sample size by R6; `make lineup-availability` re-measures) | marginal-win contribution by composition (regression with shrinkage), pair-level complementarity instead of five-man | does the need ranking predict which positions the best-performing teams addressed? shuffled-need null (existing battery pattern) | skill vector; roster-as-of-date | M |
| **R12 Player–Team Fit** | Does adding X improve *this* roster? | one-way fit baseline; 565 trades with outcomes | co-play evidence; contract/role at time of move | matched difference-in-differences on historical acquisitions; skill-complementarity score learned from them | placebo and shuffled nulls; temporal holdout; calibration of predicted vs realised change | transaction ledger; roster-as-of-date; `evaluate_trade` | L |
| **R13 Comparable Trades** | What happened in trades like this one? | 565 trades, grouped-distance retrieval, validation battery | **historical contract terms at trade date**; pick protections/values; pre-2016 (diminishing, era comparability worsens); 2017-CBA rule era | extend the existing distance with contract, age and team-state features; era-aware weighting | the existing perturbation/null/direction battery, extended | transaction ledger; contract history | M–L |
| **R14 Transaction Optimizer** | What is the best *legal* package? | 4-state legality engine; candidate generator; adversarial battery | **the legality inputs** above (apron team salary, cap holds, exceptions, pick ownership/protections, signing dates, guarantees/options) | search/MILP over packages with the legality engine as verifier; uncertainty-aware objective | replay known historical trades (must be accepted as feasible); adversarial battery; never trade a verdict for a score | everything above | XL, data-gated |

Sequencing logic: R9 and R10 *share a prerequisite* (the persisted skill vector) and need no new licence
to start; R11–R12 are limited by data that does not yet exist (availability, lineup samples); R13 and R14
are limited by **contract history and legality inputs**, which is why the ledger is built, not bought.

## 4. Recommended next technical session

**"R8.1 + R9 foundation"** — one branch, three steps, each independently shippable:

1. **R8.3 season rollover + R8.2 nba_api via canonical records** (tests first; the rollover is time-boxed
   by the 2026-10-20 tip-off).
2. **Persist the skill vector** (`player_skills` + `model_version_id`/`computed_at` on derived tables),
   read path switched only after a test asserts it agrees with the recomputed vector (readiness §3, §5).
3. **A TEI backtest harness** — rolling t→t+1 prediction over the 10 corpus seasons with interval
   coverage and a naive-prior baseline — so R9's first claim ("this model predicts next season") is
   measured before it is made.

Gate: if the trial passes, add R8.1 (persist BDL) before step 3 so R9's feature set can include the
defensive/clutch families; if it fails, R9 starts on the box-score base and says so.

## 5. Non-goals (unchanged)

Historical Replay, MyGM-style experiences, the modular Workspace and consumer features stay deferred.
