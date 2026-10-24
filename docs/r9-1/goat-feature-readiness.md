# GOAT feature readiness for the Player Skill Engine (R9.2)

**Question:** which BALLDONTLIE GOAT variables can reliably support R9.2, and what must be proven before they do?

**Short answer.** *Nothing from the new families can be validated for prediction today*, because R8.3 ingested exactly one
retrieval of one season (2025-26 regular season) and a model can only be tested over time. What can be said now is
what is *complete*, what is *redundant with data Pivot already has*, what is *too thin to be a player attribute*, and how
sure we are of the identity of the player each row belongs to. The evidence is `backend/scripts/r9_1_goat_readiness.py`
(aggregates only; no per-player value is committed — the snapshots are licensed provider data), run on a scratch copy of the
database holding R8.3's 4,055 snapshots. Raw output: [results/goat_readiness.json](results/goat_readiness.json).

No backfill was run. No provider request was made in R9.1.

## 1. What exists

| Fact | Value |
| --- | --- |
| Snapshots | 3,905 player rows in 7 groups, plus 150 team rows; all **2025-26 regular season**, retrieved in a single run (2026-10-19 03:13 UTC) |
| History of any new family inside Pivot | **none** — `provider_stat_snapshots` holds one row per scope and overwrites it on refresh |
| Provider-reported history (R8.3 samples, not verified complete) | `hustle` from 2015-16 (2014 empty); `tracking/possessions` empty for 2012–14, first non-empty season not located; `shooting/by_zone`, `defense/overall`, `clutch/base`: sampled in a few seasons only; `general/*`: 1995-96 onward |
| Request cost to backfill the four new families for 2015-16 → 2025-26 | ≈ 260 requests (R8.3 estimate), ~30 s at 600/min — **needs the owner's approval; not done** |
| Reference population used below | NBA.com players with ≥ 500 minutes in 2025-26: **379** |

Every group covers **all 379** reference players (100 %), including `clutch/base` (which has fewer rows overall because
players with no clutch minutes have none).

## 2. Identity: who is each row?

The crosswalk (`provider_entity_ids`) links BALLDONTLIE ids to Pivot's canonical players. In the reference population:

| Confidence | Players | Share of players | Share of minutes |
| --- | --- | --- | --- |
| high | 369 | 97.4 % | 97.4 % |
| medium | 10 | 2.6 % | 2.55 % |
| manual / unlinked | 0 | 0 % | 0 % |

(Across all 570 linked players with a 2025-26 row: 555 high, 14 medium.) Medium links were made by weaker rules
(`unaccented`, `suffix_insensitive`, `surname+roster_team+height`, `draft_slot+surname`).

**The data corroborates the links.** For `general/base`, GOAT reports the same games played and minutes per game as NBA.com.
Those two numbers identify a player almost uniquely, so agreement is evidence the link is correct:

| Link confidence | Players | Games played equal | Minutes/game equal |
| --- | --- | --- | --- |
| high | 555 | 555 | 555 |
| medium | 14 | 14 | 14 |

**Policy applied in R9.1 and recommended for R9.2.** Training uses `high` links only. Excluding the `medium` links removes
2.6 % of the reference players (2.55 % of minutes) — a small, quantified cost. Because all 14 medium links pass the data-based
check, R9.2 may promote a medium link to *verified* by an **explicit, audited rule** ("GP and MPG equal to NBA.com for the same
season"), recorded on the link — but it must not be done silently, and this is a check that holds only while GOAT and NBA.com
publish the same figures. A link that fails the check is untrusted regardless of its labelled confidence.

## 3. Feature inventory

"Existing" = already in Pivot from NBA.com. "New" = only GOAT. *R² on existing* = adjusted R² of the field (per-36 minutes where
it is a count) regressed on 13 existing NBA.com box-score features, rotation players, 2025-26 only. **A low value means "not
explained by what Pivot already has" — which is new information *or* noise; with one season there is no reliability estimate
to tell them apart.**

| Category | Source (BALLDONTLIE) | Key fields | Unit | Sample size & missingness (reference players) | Existing equivalent / R² on existing | Leakage risk | R9.2 use | Validation required before use |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Scoring efficiency** | `general/advanced` | `ts_pct`, `efg_pct` | fraction | complete; stable ≈ 1,000+ attempts | existing (identical to NBA.com, max diff 0.0) | none | **core** — use NBA.com series (10 seasons) | backtest R9.1 already shows persistence r = 0.59 (the noisiest rate; benefits most from shrinkage) |
| **Shooting (location)** | `shooting/by_zone` | zone FGA/FGM/FG% (6 zones) | per game, rounded to 0.1 | complete; **small cells**: median season attempts 127 (restricted area), 81 (paint non-RA), **31 (mid-range)**, 52 (corner 3), 138 (above-break 3); 64 % of players under 50 mid-range attempts | new; zone FG%: R² 0.12–0.39 | none | **shot-profile shares** (where a player shoots persists strongly) and shrunk zone accuracy | needs a ≥ 2-season history; shrinkage constants estimated per zone; rounding to 0.1/game adds ±3 attempts of error to season counts |
| **Shot creation / role** | `tracking/possessions` | touches, seconds & dribbles per touch, points per touch, paint touches | per game / seconds | complete | new in name, but R² 0.77–0.90: **largely a re-expression of usage, assists and minutes** | none | role descriptors (R10 archetypes) more than skill | decompose into "burden" vs "efficiency per burden"; history unlocated before ~2015 |
| **Playmaking** | `general/advanced` (existing); tracking passing (**untested**) | `ast_pct`, `ast_ratio`, `ast_to` | fraction / ratio | complete | existing | none | **core** — NBA.com `ast_pct` (stable r = 0.90) | adjust for usage and role; potential/secondary assists are a team-tracking family not ingested |
| **Rebounding** | `general/advanced` | `reb_pct`, `oreb_pct`, `dreb_pct` | fraction | complete | existing | none | **core**; position-dependent | evaluate relative to role (very stable r = 0.95, so little to gain from modelling) |
| **Defensive contribution** | `defense/overall` | `pct_plusminus`, `d_fg_pct` (defended FG% vs expected) | fraction / pp | complete; **median 488 defended attempts, 16 % under 300** | new; R² 0.27 (`d_fg_pct`), 0.43 (`pct_plusminus`) | none | **highest-value new signal** (the one area Pivot cannot measure) — and the hardest to validate | assignment-dependent and shot-mix-dependent; reliability must be shown by year-over-year stability, which needs backfill |
| **Hustle / effort** | `hustle` | deflections, contested shots (2/3), charges drawn, loose balls, box-outs, screen assists | per game (counts) | complete (games in payload) | **72–79 % redundant** with box score per 36 (deflections .79, contested shots .75, box-outs .75, screen assists .72); `charges_drawn` .08 and `loose_balls_recovered` .30 are mostly new | none | treat as *descriptive* until stability is shown; counts correlate with minutes (ρ 0.43–0.62 for contested shots, deflections, loose balls) | per-36 normalisation mandatory; reliability unknown |
| **Clutch** | `clutch/base` | clutch pts, FG%, +/- (last 5 min, ≤ 5 pts) | per game | 485 rows; **median 46 clutch minutes/season; 35 % of reference players under 30; 24 % under 10 clutch games** | new | none | **descriptive only** — sample far too small to be a player attribute | do not model as a skill; if shown, show the sample |
| **Offensive role / usage** | `general/advanced` | `usg_pct`, `e_usg_pct`, minutes | fraction | complete | existing (`e_usg_pct` clusters with `usg_pct`, ρ ≥ 0.9) | none | **core** (stable r = 0.88) | role is a choice as much as an ability: never interpret usage as skill |
| **Team context** | `team_season_averages/*` (30 rows each); NBA.com team tables | pace, ratings, tracking, hustle | various | 30 teams per season | team stats existing for 3 ingested seasons (`team_season_stats`); standings for 10 | none | context terms to separate player from team | the team table covers only 2023-24 → 2025-26 today |
| **Availability** | NBA.com `GP`, minutes (10 seasons); injuries (GOAT) | games played, minutes per game; injury status & return date | count / label / date | GP complete; **injuries are current-state only (92 rows), no history** | GP existing | **injuries would leak if applied to past seasons** (return dates are future information) | GP-based availability model (R9.2); injuries for *current* projection only | an injury history cannot be backfilled — Pivot would have to accumulate its own, append-only |
| **Standard counting stats** | `general/base` | pts, reb, ast, stl, blk, … | per game | complete | identical to NBA.com (392/392 points per game equal) | none | none new | — |

**Effective dimensionality** (fields left after merging those with |Spearman| ≥ 0.9): hustle 24 → 16, tracking 17 → 13,
shooting zones 24 → 14 (attempts and makes of one zone are the same thing twice), `defense/overall` 7 → 4 (one constant),
`general/advanced` 39 → 23, `clutch/base` 32 → 25. The new families are *not* equally informative: most of what looks like
breadth is duplication.

## 4. What this means for R9.2

1. **Build R9.2's first slice on data that has history today** — the NBA.com series (10 seasons): scoring efficiency, usage,
   playmaking, rebounding, availability, and the defensive proxies already in the TEI. Each can be backtested now with the R9.1
   harness (see the candidate results: shrinkage helps the noisy ones, little helps the stable ones).
2. **Defence is the prize, and it needs a backfill first.** `defense/overall` and `hustle` are the only candidate inputs for
   defensive skill. Without ≥ 3 seasons per family there is no stability estimate and no out-of-time test. The backfill is
   ≈ 260 requests for four families × 11 seasons; R9.1 recommends doing it **once, frozen, with an append-only retrieval
   history** (`provider_stat_snapshots` must stop overwriting), as part of R9.2's first task — not before the owner approves.
3. **Shot location is second.** Zone *shares* are mostly new information and persist; zone *accuracy* needs shrinkage because
   the cells are tiny (see the table) — the same empirical-Bayes machinery R9.1 validated for true shooting.
4. **Hustle and tracking** should enter R9.2 as *descriptive* and role-indicating features until their stability is measured.
   72–90 % of their variance is already in the box score.
5. **Clutch is not a skill dimension.** Median 46 minutes a season cannot separate players.
6. **Injuries and contracts are out of scope for player skill.** Injury status is forward-looking only; contract cap hit is
   endogenous to performance (it belongs in R13 value-over-contract).

## 5. Limits of this analysis

* One season, one retrieval: reliability, stability, drift and predictive value are **unmeasured** for every GOAT family.
* The redundancy numbers are cross-sectional associations among 379 players; a high R² shows duplication, a low R² shows only
  that the field is not explained by the box score.
* Provider values are per-game and rounded (shot zones to 0.1/game), so season totals reconstructed from them carry rounding
  error that matters for rare zones.
* GOAT and NBA.com statistics are numerically identical wherever both exist; agreement validates the pipe, not the numbers
  (R8.3). A shared upstream error would be invisible to the identity check above.
* Licensing: whether derived model artifacts built on the provider's data may be redistributed is an open question from R8.3.
