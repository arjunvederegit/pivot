# R9.2 — Multidimensional coverage: every dimension, its status, and what it would take

A dimension is a kind of basketball **evidence**, not a rating. There is no overall score and no shared 0–100 scale: each quantity is in its
natural unit (fractions of 1, counts, minutes), and a dimension is offered at exactly the level its evidence supports. Where a quantity cannot be
estimated reliably the engine says so (`Unavailable`) instead of producing a number.

Evidence legend: **M** measured · **I** inferred (ability estimate) · **P** projected (next-season forecast).
Tiers: *experimental* = a candidate that met its rule · *benchmark* = best simple method, no improvement claimed · *withheld* · *not offered*.

## 1. Summary

| Dimension | Quantities | M | I | P | Data | Status |
| --- | --- | --- | --- | --- | --- | --- |
| **Shooting** | `ts_pct`, `fg3_pct`, `ft_pct` | ✔ | ✔ experimental, **withheld below reliability 0.30** | `fg3_pct`, `ft_pct`: ✔ experimental (confirmed against `ols_history` on an unread holdout; ~1.5% better than an attempts-aware regression on development) · `ts_pct`: benchmark | NBA.com, 10 seasons | **ready, with the caveats in [shooting-validation-results.md](shooting-validation-results.md)** |
| **Playmaking** | `ast_pct`, `ast_to_ratio` | ✔ | not offered | not offered | NBA.com | **measured only** |
| **Rebounding** | `reb_pct`, `oreb_pct`, `dreb_pct` | ✔ | not offered | not offered | NBA.com | **measured only** |
| **Offensive role / usage** | `usg_pct`, `minutes_per_game` (+ a rule-based role descriptor as context) | ✔ | **never** (a role, not an ability) | not offered | NBA.com | **measured only** |
| **Availability** | `games_played` | ✔ | not offered | not offered — a study is reported | NBA.com GP, standings | **measured; model not served** |
| **Defensive contribution** | — | — | — | — | one GOAT season | **unavailable** |
| Shot profile (zones) | — | — | — | — | one GOAT season | unavailable |
| Shot creation / off-ball | — | — | — | — | one GOAT season | unavailable |
| Overall skill rating | — | — | — | — | — | **deliberately not offered** |

**An asymmetry to know about.** Ability estimates are served on a *development-only* non-inferiority-and-calibration rule, projections need holdout confirmation. For true shooting that means Pivot serves an ability
estimate from `state_space_role`, whose own forecasts were **not** confirmed against the baseline on the holdout, while declining its projection. The rule was pre-registered; the record's limitations now say it in so
many words, and the estimate is withheld below reliability 0.30. Treat true-shooting ability as the least-validated of the served outputs.

## 2. Why measured-only for playmaking, rebounding and role

Year-over-year correlation among players with ≥ 500 minutes in both seasons (n = 2,575 pairs; `results/shooting_analysis.json`):

| Quantity | Stability (Pearson) | Reading |
| --- | --- | --- |
| `reb_pct` | 0.946 | the measurement *is* the estimate; also strongly position-dependent |
| `oreb_pct` / `dreb_pct` | 0.934 / 0.918 | same |
| `ast_pct` | 0.900 | same; assists depend on role and teammates as much as skill |
| `usg_pct` | 0.878 | a role a coach assigns |
| `ast_to_ratio` | 0.776 | a ratio of two counts with unmodelled sampling error |
| `minutes_per_game` | 0.756 | a coaching decision |
| `games_played` | **0.226** | availability is *not* a stable trait — see §4 |
| (for comparison) `ft_pct` / `ts_pct` / `fg3_pct` | 0.82 / 0.64 / 0.35 | the shooting rates are the ones where noise is large enough for an estimate to add information |

R9.1's backtests already showed that for the stable rates the best simple forecast improves on raw persistence by only 5–6%. A latent playmaking
or rebounding model would be decoration, so none was built. Each measured record carries its minutes, games and source, and a short-minutes
limitation (< 200 minutes: share statistics are unstable). The rebound and assist shares are NBA.com's published fractions; a published value
above 1 (percentage points) is *refused*, not rescaled.

**Usage is context, not skill.** `usg_pct` and minutes are exposed so other tools can see *how a player was used*; the catalog states that an
inferred or projected usage is not offered because "a high-usage player is not thereby a better player". The rule-based role descriptor (from the
existing archetype table) is attached to the response `context`, labelled "a rule-based descriptor of how the player was used, not a measure of
ability".

## 3. Shooting (the primary target)

Three separate quantities — never combined. Measured value with attempts and makes; inferred underlying ability with a credible interval,
reliability and effective attempts; projected next-season value with an interval whose out-of-time coverage is stated beside it. Methodology:
[shooting-intelligence-methodology.md](shooting-intelligence-methodology.md); evidence: [shooting-validation-results.md](shooting-validation-results.md).

## 4. Availability — measured, studied, not served

*Measured*: `games_played` per season, with the caveat that seasons differ in length (the denominator for a share is the team's own games from
the standings; wins + losses equal `team_season_stats.GP` for all 90 team-seasons that carry both).

*Studied* (`scripts/r9_2_availability_study.py` → `results/availability_study.json`; development folds only, an analysis rather than a served model;
injury records are never used because they exist only for the present and would reconstruct knowledge that did not exist at the cutoff):

| Question (pooled over five development folds) | Result |
| --- | --- |
| Does a scored player appear next season? (≥ 1 game; base rate ≈ 69%) | logistic regression on his own history: Brier **0.105** vs 0.218 (base rate) and 0.128 ("played last season" rule); AUC 0.92. Calibration by decile (last development fold) is within ≈ 0.06 except the third decile, where 42.5% was forecast and 61% appeared (under-forecast by 0.19) |
| Given he appears, what share of his team's games? | the existing decayed-GP availability (Pivot's `analytics.availability`) is **the best of the four methods** (MAE 0.189) vs OLS on history 0.197, persistence 0.202, league mean 0.218 — a new model was **not** adopted |
| Total future availability (zero if absent) | two-part model RMSE 0.260 vs persistence 0.282 vs league mean 0.382 (−8% vs persistence); in **MAE** persistence is better (0.191 vs 0.198) because a mean forecast is the wrong object for an absolute-error score of a zero-heavy outcome |

What this establishes: participation is *forecastable* (and a lot of the 46% attrition R9.1 reported is predictable from his own history), and the
existing availability measure is a reasonable conditional estimate. What it does not: a validated, pre-registered availability model. That is the
**recommended next modelling step** (R9.3 prerequisite for Team Outlook and Trade Evaluator): promote this study to a harness target with its own
pre-registration, scoring the *mean* forecast with squared error and the appearance probability with proper scoring rules, on the same folds, with a
holdout it has never touched. Until then `Unavailable` is returned for inferred/projected availability.

## 5. Defence — unavailable, and why that is the right answer

| Candidate evidence | What it is | Why it is not a skill rating |
| --- | --- | --- |
| blocks, steals, rebounds | recorded events | events are not ability; they depend on role and scheme |
| `defense/overall` (defended FG% vs expected) | tracking-based opponent shooting outcomes on a player's assignments | one ingested season: no stability, no out-of-time test; shot-mix and assignment dependent |
| `hustle` | counted actions | 72–79% explained by minutes and box-score rates (R9.1); one season |
| team defensive rating | team context | confounded with teammates; including a player on both sides of a subtraction produces opposite signs (documented in `analytics.features`) |
| player-specific causal defensive impact | what a GM actually wants | not identifiable from box-score or on/off data |

`defensive_contribution` therefore returns an explicit `Unavailable` naming the requirement (a bounded multi-season backfill with insert-only
storage and a historical identity method, then a stability and incremental-information study — see
[goat-intelligence-assessment.md](goat-intelligence-assessment.md)). The existing `domain.skills` vocabulary already lists the finer-grained
defensive dimensions (switchability, help defence, …) as unavailable with their own reasons; R9.2 does not contradict any of them.

## 6. Relationship to the existing skill vocabulary

`domain.skills` / `analytics.archetypes.player_skill_vector` (the nine percentile-based "measured" dimensions, e.g. `shooting_accuracy`) are
**unchanged** and still serve the Intelligence endpoints, archetypes and fit. They are league *percentiles*, recomputed each ingestion, with no
sampling model. R9.2's quantities are natural-unit, versioned and sample-aware; they are the intended inputs for R10 (archetypes should be formed
on the low-noise dimensions with their reliabilities) and R11–R12. Migrating consumers from percentiles to the engine is an R9.3/R10 decision,
made one consumer at a time.

## 7. What each missing piece would take

| Missing | Requirement | Where it is prioritised |
| --- | --- | --- |
| Availability model | pre-registered harness target; mean-forecast scoring; fresh holdout | next (R9.3 prerequisite) |
| Defence | historical identity + insert-only backfill; stability + incremental-information study | after the identity work |
| Shot profile | multi-season zone data; test of zone split vs single quantity | after the backfill |
| Role-aware ability | the `state_space_role` variant is in; broader role models only if they pass the same rule | optional |
| Ageing curves | the quadratic age curve is a fitted part of every shooting model; a richer curve only if it passes the rule | optional |
