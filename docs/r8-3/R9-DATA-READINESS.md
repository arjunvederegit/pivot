# R9 data readiness — what can be built next, and what must be proven first

R8.3 does **not** build R9. It establishes what data exists, what it can and cannot support, and what the first
R9 session should do so that it starts from evidence instead of another exploration. Nothing here changes a
model, a coefficient or an estimate.

## 1. Starting point (facts from the repository, not from R8.3)

* The current impact metric (TEI, `docs/model-card-player-impact.md`) is a **fixed-weight z-score index**
  (`INDEX_WEIGHTS`), predicting next-season `0.6·z(PIE) + 0.4·z(NET_RATING)` from recency-weighted per-75 scoring,
  TS%, USG%, AST%, TOV%, OREB%, DREB%, steals/blocks per minute, 3PA/FTA rates, minutes, PIE, on-court net rating
  and age, trained on **three seasons** (2023-24 → 2025-26). It reaches team-level R² = 0.75 against net rating.
* A ridge regression that won player-level MAE (0.637 vs 0.645) was **retired** because its team-level R² was
  0.004. Adding R4's defensive differential to the index cut team R² from 0.75 to 0.57–0.73 and was **rejected**.
  Lesson to carry: *player-level error is not the product's outcome; team-level explanatory power is the gate.*
* 10 corpus seasons (2016-17 →) of NBA.com base/advanced/estimated per-player rows already exist.
* `make train` **permanently deletes the previous version's impact estimates** (`train.py::_gc_superseded_estimates`;
  `score.py` also deletes stale rows) and retires every active model. Not reversible without a database backup.

## 2. What R8.3 adds to R9's raw material

Verified complete for **2025-26 regular season only** (full league, 570–582 players, 0 fields > 5 % null):
`general/advanced` (39 fields), `clutch/base`, `defense/overall`, `shooting/by_zone`, `tracking/possessions`,
`hustle`, plus 2026-27 injuries (current) and contract rows. Everything else, including *all earlier seasons* of
these families, is **observed on one page or not at all** (see [data-coverage-matrix.md](data-coverage-matrix.md)).
**General/base and general/advanced are numerically identical to NBA.com's**, so they add no information beyond a
second copy; the new information is the *families*.

## 3. Candidate variables

Legend — **Obs**: directly observed · **Der**: derived by Pivot. **Hist**: depth confirmed by a sample ·
**Missing**: share null in the 2025-26 league table. All "relationship to impact" statements are *hypotheses to
test*, none is a finding.

| Variable | Basketball meaning | Definition / unit | Source | Hist | Missing | Sample-size caveat | Obs/Der | Hypothesised link | Duplication risk | Leakage risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ts_pct`, `efg_pct` | scoring efficiency | pts / (2·(FGA+0.44·FTA)); fraction | NBA.com = GOAT | 10 seasons | 0 | stabilises slowly (~1,000+ FGA) | Obs | strong, partly noise | TS vs eFG vs FG%: collinear | none |
| `usg_pct` (and `e_usg_pct`) | share of team possessions used | fraction | both | 10 seasons | 0 | needs ≥ 500 min | Obs | role, not quality | `e_*` is an estimate of the same | none |
| `ast_pct`, `ast_ratio`, `ast_to` | playmaking volume | % teammate FG assisted; ratio per 100 poss | both | 10 seasons | 0 | noisy < 500 min | Obs | creation | three views of one thing | none |
| `reb_pct`, `oreb_pct`, `dreb_pct` | rebounding | % available boards | both | 10 seasons | 0 | stable | Obs | role + size | position proxy | none |
| `pie` | box-score share of events | fraction | both | 10 seasons | 0 | — | Obs | **is a component of the target** | do not use same-season PIE to predict the next season's PIE without a guard | **yes (target)** |
| `off/def/net_rating` (on-court) | team results while on floor | pts per 100 poss | both | 10 seasons | 0 | **confounded by teammates**; net rating is the other target component | Obs | descriptive of context | high with team strength | **yes (target)** |
| `e_off/def/net_rating`, `e_pace` | provider's estimated ratings | per 100 poss | GOAT | 2025-26 only verified | 0 | same as above | Obs (modelled by provider) | as above | duplicates the on-court versions | yes |
| `pct_plusminus` (defense/overall) | defended FG% minus league-expected FG% on those shots | percentage points | GOAT | not tested | 0 | depends on assignments & shot mix; low reliability | Obs | **defensive contribution — the weakest area today** | `d_fg_pct`, `normal_fg_pct` are its parts | none |
| defended FG% < 6 ft / < 10 ft | rim protection | fraction | GOAT `defense/*` (not ingested) | not tested | — | only bigs; small attempts | Obs | rim deterrence | overlaps blocks | none |
| deflections, contested shots (2/3), charges drawn, loose balls, box-outs | defensive effort/events | per game | GOAT `hustle` | **2015-16 →** | 0 | counts, need per-minute | Obs | possibly role + defence | collinear with minutes | none |
| zone shares and zone FG% (RA, paint, mid, corner 3, above-break 3) | shot profile / shot quality | FGA share; FG% | GOAT `shooting/by_zone` | 2018 sampled | 0 | small cells per zone → **needs shrinkage** | Der (shares) | efficiency expectation; 3P/rim rate persists strongly | with 3PA rate, FTA rate already in TEI | none |
| touches, seconds & dribbles per touch, paint/post/elbow touches, pts per touch | ball dominance / creation | per game | GOAT `tracking/possessions` | **not 2012-14; start unlocated** | 0 | volume depends on team | Obs | creation burden | with usage, assists | none |
| clutch pts, FG%, +/- (last 5 min) | late-game role | per game | GOAT `clutch/base` | not tested | 0 (492 of 582 qualify) | **tiny samples; 197 of 485 under 10 games** | Obs | mostly **descriptive**, minutes-driven | with usage | none |
| `gp` / games-played share, minutes per game | availability and role stability | count; fraction of 82 | both | 10 seasons | 0 | — | Obs | predicts *playing time*, hence value | with minutes | none |
| injury status, return date | current availability | label; date | GOAT | **current only — no history** | n/a | — | Obs | forward-looking only | — | **would leak if applied to past seasons** |
| age, years of experience | development curve | years | NBA.com | 10 seasons | 0 | — | Obs | strongest known prior | — | none |
| team context: team net rating, pace, minutes share, teammate usage | what a player's numbers are conditional on | various | NBA.com team tables | 10 seasons | 0 | 30 teams/season | Der | adjustment term | with on-court ratings | none |
| contract cap hit | market value | USD | GOAT | 2026-27 (82.5 %) | 17.5 % | — | Obs | **endogenous to the same performance** | — | **do not use as an impact feature** (it belongs to R13 value-over-contract) |

## 4. Candidate feature groups — what could help, what only describes

| Group | Available now (verified complete 2025-26) | Likely to improve a *predictive* impact model (to be tested) | Probably descriptive only |
| --- | --- | --- | --- |
| Offensive efficiency | TS%, eFG%, pts/FGA, off rating | TS% **with shrinkage toward position/role mean** | raw ranks, fantasy points |
| Shooting ability | zone FG%, 3PA rate | zone *shares* and shrunk zone efficiency; 3P% shrunk by attempts | zone FG% unshrunk on small cells |
| Shot creation | usage, touches, time of possession, drives (untested) | touches × usage × efficiency (burden-adjusted efficiency) | clutch scoring |
| Playmaking | AST%, ast ratio, potential/secondary assists (team tracking) | AST% adjusted for usage | assist-to-turnover alone |
| Rebounding | REB%, OREB%, DREB% | rebounding *relative to position* | box-outs (hustle) |
| Defensive contribution | defended FG% plus-minus, rim FG%, deflections, contested shots | rim protection (bigs), steals/blocks per minute, **on/off with shrinkage** | deflection counts, hustle events (unproven), clutch defence |
| Role & usage | usage, minutes, starter status (lineups), touches | role *stability* (minutes variance) | positions as labelled |
| Age & development | age, experience | age curve (already the strongest prior) | — |
| Availability | games played (history), injury (current only) | games-played share as a minutes forecaster | injury status (no history) |
| Team context | team net rating, pace, teammate usage | context terms to separate player from team | on-court net rating un-adjusted |

The honest summary: **R9's most promising new signal is defence** (the one area Pivot cannot measure and R4's
attempt failed) **and shot-profile structure**; both come from the new families. Everything marked "descriptive"
is useful for *explanation* in Ask Pivot, not for prediction.

## 5. Baselines and validation

| ID | Model | Purpose |
| --- | --- | --- |
| B0 | persistence + age regression: next-season value = this season's value shrunk toward the position mean, adjusted by the age curve | the bar any model must clear (R8's roadmap names this "naive prior") |
| B1 | the **current TEI** as-is | the incumbent; no change allowed to pass without beating it |
| B2 | penalised linear (ridge / elastic net) on B1's features with empirical-Bayes shrinkage and an explicit minutes weight | tests whether *learning weights* helps at all (it did not at team level before) |
| B3 | B2 + new families (one group at a time, then together) | the question R8.3 exists to ask |
| B4 | hierarchical (partial-pooling) model or gradient-boosted trees | ceiling check only; adopt only if B3 shows structure B2 cannot hold |

**Validation design (fix before looking at results):**

1. **Rolling origin, never random splits:** train on seasons ≤ t, predict t+1; ≥ 8 folds where the family exists.
   Features only from seasons ≤ t; target season t+1 untouched.
2. **Two outcome levels, both gating:** player-level minutes-weighted MAE/RMSE and Spearman among rotation
   players (≥ 500 min); **team-level R² of the minutes-weighted aggregate against net rating**, which is what
   rejected the previous two attempts.
3. **Uncertainty:** empirical coverage of the nominal 80 % interval per fold and per subgroup; width *and*
   calibration reported (a narrower band that undercovers is a regression).
4. **Subgroups:** rookies, ≥ 31-year-olds, bigs, low-minute players, traded players (`team_count > 1`).
5. **Significance:** paired player-cluster bootstrap on ΔMAE; a family is kept only if the interval excludes 0 in
   a pre-declared majority of folds *and* team-level R² does not fall.
6. **Missingness is a feature of history:** hustle exists from 2015-16, tracking from an unlocated season ≥ 2015.
   Compare B2 and B3 **on the identical rows** (restrict to seasons where the family exists), and never impute a
   missing family as 0.
7. **Leakage checks:** no `*_rank` fields (dropped at ingestion: they are relative to the provider's population
   at retrieval time); no `pie`/`net_rating` of the target season as features; no current-injury information in a
   historical fold; no contract fields.
8. **Reproducibility:** each run records the code commit, the input fingerprint (row-hash of every feature table
   incl. `provider_stat_snapshots` retrieval times), seeds and fold definitions.

## 6. Prerequisites for non-destructive training

| # | Prerequisite | State |
| --- | --- | --- |
| 1 | A training run writes a **candidate** `model_versions` row and keeps its own estimates under that version; it never deletes another version's estimates or flips `is_active` | **missing** — `train.py` retires active models and `_gc_superseded_estimates` deletes old estimates permanently |
| 2 | Promotion is a separate, audited, reversible switch after the validation battery passes; rollback = switch back, no restore | missing |
| 3 | Training window and performance season are **explicit arguments**, not `Settings.current_season` (R8.2 runbook §5) | missing |
| 4 | Estimates keyed by (player, season, model_version) so versions coexist | schema supports it; code deletes |
| 5 | Provider data is **append-only with retrieval time** for any in-season use: `provider_stat_snapshots` currently holds *one row per scope* and overwrites it on refresh (fine for a finished season, **wrong for an in-season backtest**, where a model must see only what was known on a given date) | **missing** — add a history table or an `as_of` key before using in-season data |
| 6 | Historical backfill of the families for the corpus seasons, run once and frozen (§7) | not started |
| 7 | Rehearse on a disposable copy; compare batteries (`comparable-validation`, `acquisition-validation`, `adversarial-validation`) before promotion | procedure exists (R8.2 runbook §5) |
| 8 | `test_r3_gate_after_r4.py`-style gates stay green | exist |

## 7. Recommended first R9 task

**R9.1 — Rolling-origin backtest harness and a candidate-feature gate. No model is promoted.**

1. Build the harness on the data Pivot **already has** (10 NBA.com corpus seasons): B0, B1 and B2, measured at
   player and team level with interval coverage. This produces R9's first honest number — *how well does the
   current metric predict next season?* — which no document yet states.
2. In the same session, make training **candidate-only** (prerequisites 1–4): a new version row, its own estimates,
   no deletion, no activation.
3. Then — and only if the owner approves ≈ 260 requests (4 families × 6 pages × ~11 seasons; ≈ 30 s at
   600/min, above the ≈200 evaluation guideline) — backfill `hustle`, `defense/overall`, `shooting/by_zone` and
   `tracking/possessions` for 2015-16 → 2025-26, frozen once, with an append-only snapshot history, and run B3 one
   family at a time.

The first *substantive* improvement worth testing is **defensive contribution**: the only skill the product
cannot currently measure, the one area where the box-score index and R4's differential both failed, and the one
the new families speak to directly (defended-FG% plus-minus, rim protection, deflections/contests). The second is
**shot-profile structure with shrinkage** for offence. Neither is assumed to work; either result is publishable
(`docs/r8` already treats "this should not be trusted" as a valid engineering outcome).

Out of scope for R9.1: archetypes (R10), roster needs (R11), fit (R12), anything using contracts.
