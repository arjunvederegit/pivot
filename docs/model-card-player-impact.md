# Model card — RosterLab Estimated Impact (TEI)

**Model:** a transparent weighted z-score index with documented fixed weights
(`impact.py::INDEX_WEIGHTS`). Version metadata in `model_versions` (algorithm, features,
target, metrics, commit); there is no artifact to load, because the model *is* its
coefficients.

**Retired in R3-1:** a ridge regression (α=10) served this metric until R3-1. It won the
player-level held-out MAE comparison (0.637 vs the index's 0.645) and lost the one the
product depends on — at team level it explained **R² = 0.0039** of net rating against the
index's **0.7505** (change-on-change: 0.0030 vs 0.6236). It is also a volume metric
(corr 0.716 with usage, 0.100 with net rating) and is not computable per season, so the
R3-2 conversion could only have been fitted on n = 30 of a metric with no signal.

## Intended use

Comparative impact estimates **on an index scale** for exploratory trade analysis in
RosterLab: rotation-weighted team projections, player comparisons, candidate filtering.
Always displayed with uncertainty bands.

The scale is deliberately **not** described as per-100-possession points. A team's
minutes-weighted index converts to net-rating points through a fitted coefficient of
≈15 (R3-2); calling the raw index "points per 100" asserts that coefficient is 1.0, and
it is not.

## How the product presents it (terminal redesign)

The raw index is not shown on its own. It is placed in the **scored league**: every estimate
for the current season under the active version (n = 512 on the 2025-26 snapshot), served by
`GET /players/impact-distribution`.

- **League impact percentile:** a mid-rank percentile, where tied players count as half.
  It is never displayed as 0 or 100.
- **Competition rank:** for example "T-#11" when tied.
- **Likely range:** the 10–90% band placed in the same population.
- **Position descriptor:** a plain fraction such as "Top 5%". It is not a tier.

The raw index is shown in each player readout and roster row and beside each percentile
on the saved-deal report; every compact percentile carries it in its tooltip. The
population definition, method, model version and limitations travel with the readouts. Unscored players are shown as unscored and never
ranked.

These are positions among measured players, not calibrated ratings. Because the likely range
is shown with them, they are not presented as authoritative rankings. Precedent `tei` is a
different, single-season quantity and is never placed in this population. Details and the
modeling follow-ups are in `docs/pivot/impact-metric-findings.md`.

## Excluded uses

Player valuation for real transactions or contracts · performance management ·
public player rankings presented as authoritative · any use that strips the
uncertainty bands · claiming equivalence to RAPTOR/EPM/LEBRON/BPM (it is none of
these).

## Training data

NBA.com per-season Base + Advanced + Estimated player statistics (2023-24 →
2025-26) ingested via `nba_api`; players with ≥200 minutes in the window. No
synthetic data.

## Target

Next-season `0.6·z(PIE) + 0.4·z(NET_RATING)`, minutes-weighted z-scores within each
season — a box-derived impact **proxy** (documented as such; see ADR-10).

## Features

Recency-weighted (λ=0.7, minutes-weighted) z-scores of: pts/75, TS%, USG%, AST%,
TOV%, OREB%, DREB%, steals/min, blocks/min, 3PA rate, FTA rate, minutes, PIE,
on-court net rating; plus age.

**Unchanged by R4, and that was a measured decision rather than an omission.** R4 added
columns to the feature path (fouls per minute, 3PA per minute, a team-relative defensive
differential, shrunk 3P%) for the *skill* vectors, but none of them entered `INDEX_WEIGHTS`,
so TEI is the same quantity it was at R3 and the fitted net-rating conversion (14.977)
remains valid for it. Feeding the new defensive term into the index was tested and
**rejected**: team-level R² fell from **0.7505** to 0.5655 (replacing the event terms),
0.7263 (adding it at 0.10) or 0.6753 (at 0.20). `test_r3_gate_after_r4.py` fails the suite
if a future change moves an R4 column into the index without refitting the conversion.

## Validation (actual, from this snapshot)

- Split: validate on the 2024-25→2025-26 transition (n=464). No random row splits
  across seasons.
- Held-out player MAE (z-units): **index 0.645** · persistence 0.717 · (retired ridge
  0.637).
- Team-level validity, 90 team-seasons: **index R² 0.7505** vs retired ridge 0.0039.
  Change-on-change over 60 transitions: **0.6236** vs 0.0030.
- Serving scale (C5): served rows are z-scored against the reference season's
  minutes-weighted moments, so train and serve share one scale. Team-level served TEI
  regresses on season TEI with slope **1.015** (r = 0.911); before the fix, r = 0.387.
- Uncertainty bands are per player: **σ² = 0.0326 + 240.9 / total_minutes**, estimated
  from 921 same-player consecutive-season pairs. σ runs 0.72 at 500 minutes to 0.36 at
  2,500, replacing a constant 2.462 taken from the retired model's residual spread.
  Bands are narrower for rotation players and **wider** below ~257 minutes. This σ is
  season-to-season variability — the right input for a forward-looking interval, and the
  wrong thing to call "measurement error".

## Limitations

Box-score-only: no tracking, matchup, or lineup-context data; defense is
under-measured (stocks + DREB + net-rating echo). One validation transition (three
seasons ingested); bands understate tail risk for role changes, injuries, and aging
outliers. The index→net-rating coefficient is fitted on 60 team transitions from three
ingested seasons and is valid only for the regressor construction recorded beside it in
`model_versions`. Minutes threshold excludes fringe players (shown as
"no estimate", not zero).

## Fairness considerations

The model scores on-court production only, is not used for any employment or
compensation decision, and inherits any measurement biases present in NBA.com box
statistics (e.g., defensive contributions that don't appear in counting stats are
under-credited).

## Update cadence

Retrain weekly or after each data refresh (`make train`); every run writes a new
version and deactivates the old one; the UI's /data-health lists the active version
and metrics.
