# Methodology

Everything below is computed from real NBA.com data ingested via `nba_api`; validation
numbers are from the actual trained models on this repository's July 2026 snapshot
(also visible at `/data-health`).

### Evidence vocabulary

The words below are not interchangeable, and a passing test suite is none of them.

* **Legacy services** (`app/domain/evidence.py`): `validated` = checked against a stated
  threshold by a command that exits non-zero when it fails (the comparable, acquisition and
  adversarial batteries) — a consistency check, not proof that a quantity measures what it
  names. `measured` = computed from real data by a documented method with nothing to check it
  against. `heuristic` = a transparent rule chosen by construct, defensible but not validated.
  `unavailable` = no value offered.
* **R9 player-intelligence service** (stricter, separate): `measured`, `validated` (an active
  model whose forecasts were confirmed on a first-read holdout and promoted by a reviewed
  decision — promotion is designed but not built, and as of R9.2 **nothing had reached this tier**), `experimental`, `benchmarked`, `withheld`.
  See [R9.2 model safety](r9-2/model-safety-and-promotion.md) §2.
* The two vocabularies are not harmonised: the legacy `validated` is weaker than the R9 one.
  Read any "validated" in this document as the check it names, not as proof of accuracy.

## 1. Composite utility — not a magic score

For focal team *t* and trade *d*:

```
U(t,d) = w_P·P + w_F·F + w_C·C + w_T·T + w_A·A + w_R·R        Σ w_k = 1
```

- Components are normalized to **0–100 with 50 = neutral** so the composite is
  readable at a glance; every component publishes its raw calculation alongside.
- **A component is squashed, never truncated (R5-1a).** Components built on a naturally
  bounded quantity — availability is a share of games — map affinely and can genuinely
  reach 0 or 100. Components built on an unbounded one — projected wins, cap-share
  surplus, fit deltas, asset counts — pass through
  `50 + 50·tanh((linear − 50)/50)`, which is strictly increasing and has slope exactly 1
  at 50, so every documented scale constant is unchanged and only the tail bends.
  Truncation was an information choice, not a scale one: measured over 800 evaluations of
  the 30 ingested rosters, **24.1 % of fit scores, 9.5 % of contract scores and 4.1 % of
  timeline scores sat exactly on 0 or 100**, so the component stopped ordering deals at
  precisely the point where they were most extreme. After the change the same sample ties
  0.5 % of fit scores and none of the others.
- Weights are user-controlled per scenario (strategy presets provided) and always
  renormalized.
- **Missing data shrinks scope, never fakes a number**: an unavailable component
  (e.g. contract value with no contract provider) is excluded and remaining weights
  renormalized; the exclusion is surfaced in the API (`excluded_components`) and UI.
  Driver contributions use the **renormalized** weights, so they sum to `U − 50`.
- **`U` can be absent, and absence has two meanings.** The response carries
  `decision_status`:
  - `scored` — `composite_utility` is a number;
  - `suppressed_illegal` — the deal fails a verified CBA rule, so it cannot be
    executed. No score is reported and `suppression.failing_rules` says why. A number
    here would invite comparing a deal that cannot happen against deals that can.
  - `insufficient_data` — no component could be scored. `null`, never 0.0: on a 0–100
    scale a zero reads as a catastrophic verdict rather than a refusal.
- **Verdict labels are monotone in the score**: ≥58 Clear win · 48–58 Roughly neutral ·
  40–48 Net negative · <40 Clear loss · low confidence ⇒ "Cannot fully evaluate". The
  thresholds are unchanged from the original design; the labels were not monotone before
  R1-9 (46 read "High-risk upside" while 52 read "Mixed outcome").
- **Zeroing every weight yields no score.** A weight vector summing to zero is a
  deliberate statement; substituting a uniform prior silently re-enabled components the
  user had switched off.

### Players the model has not scored

43 of 530 rostered players (8.1 %) have no impact estimate — mostly two-way and
late-signing players below the 200-minute window threshold the feature pipeline already
applies. They are **excluded from the projection and named**, never averaged in:
`has_unmodeled_players` and `unmodeled_players` appear on every evaluation, confidence
drops to low when one is inside the deal, and they remain in the roster count so roster
limits are unaffected.

Before R1-4 they carried `tei = 0.0`. That is the **63rd percentile** of rostered
players (306 of 487 sit below zero), so a player with no data was scored above the
median — a silent default considerably more favourable than the −0.293 league mean
suggests, and one that contradicted this document's own model card.

## 2. TEI — RosterLab Estimated Impact

TEI is this project's original **unitless index of box-score production**: a hand-weighted
composite of ten within-season z-scores, scaled ×2.5 (nothing is fitted). It is **not**
points per 100 possessions — a team's minutes-weighted index reaches net-rating points only
through a fitted coefficient of ≈15 (below), not 1 — and it is not a causal estimate of a
player's incremental impact: it is descriptive, makes no teammate or context adjustment, and
sees box scores only. It is **not** RAPTOR, EPM, LEBRON, BPM, or any proprietary metric, and it
is documented as a portfolio-model estimate ([model card](model-card-player-impact.md);
[formal assessment](r9-1/current-model-assessment.md)).

### Features

Per player-season from `LeagueDashPlayerStats` (Base + Advanced) and
`PlayerEstimatedMetrics` where available: pts/75 (via individual possessions), TS%,
USG%, AST%, TOV%, OREB%/DREB%, steals & blocks per minute, 3PA and FTA rates,
minutes, GP, age, PIE, on-court NET_RATING. Multi-season aggregation uses minutes ×
recency weights:

```
X̄ᵢ = Σₛ λ^(s−1)·mᵢ,ₛ·Xᵢ,ₛ / Σₛ λ^(s−1)·mᵢ,ₛ        λ = 0.7, s=1 most recent
```

Players under 200 window minutes are excluded (insufficient evidence), not imputed.
Cross-season comparability comes from minutes-weighted z-scores within each season.

**Known limitation — missing individual inputs.** For a player who *is* scored, a missing
input is not excluded: `baseline_index` (and the z-scoring helpers in `features.py`) fill a
missing z-score with 0.0, i.e. "league average"
([assessment §1](r9-1/current-model-assessment.md)). This is legacy behaviour, unchanged here;
it contradicts the missing-data standard applied elsewhere in Pivot and is flagged for an
owner decision.

### Candidates and validation

| Candidate | Description |
| --- | --- |
| Persistence baseline | this season's target repeats next season |
| **Transparent index (production)** | documented weighted z-score blend (weights in `impact.py::INDEX_WEIGHTS`) |
| ~~Ridge~~ | α=10 on next-season `0.6·z(PIE) + 0.4·z(NET_RATING)` — **retired in R3-1** |

Validation is **time-aware**: validate on the 2024-25→2025-26 transition (n=464).
Individual rows are never randomly split across seasons (leakage).

| Model | Held-out player MAE | Team-level R² (net rating) | Change-on-change R² |
| --- | --- | --- | --- |
| Transparent index | 0.645 | **0.7505** | **0.6236** |
| Persistence | 0.717 | — | — |
| ~~Ridge~~ (retired) | 0.637 | **0.0039** | 0.0030 |

The ridge won the player-level comparison and lost the one that matters. Held-out MAE
scores a *next-season player proxy*; the product uses TEI to project *team* impact, and
measured there the ridge explained essentially nothing. It is also a volume metric
(corr 0.716 with usage, 0.100 with net rating) and is not computable per season from
stored artifacts, so the R3-2 conversion could only ever have been fitted on n = 30 of
a metric carrying no signal.

**What this validation does and does not show.** It is one forward transition, pooled over all
464 players (27.8 % played under 500 minutes in the target season), scored against the
incumbent's own proxy, which is 60 % PIE — and the index correlates 0.96 with same-season PIE.
The team-level R² values are concurrent, against on-court net rating built from the same
players. They show the index tracks quality; they are not out-of-time evidence of impact, and
a hand-weighted composite with no fitted parameters has no held-out *fit* to report.

### Out-of-time evaluation (R9.1, 2026-10-19)

Evaluated without changing it, under a rolling-origin backtest with two locked holdout
seasons, against the same proxy target: TEI beats raw "last season" (MAE 0.413 development /
0.436 holdout vs 0.446 / 0.452), is **not distinguishable from** regressing last season toward
the mean with an age curve, and is **10.8 % worse** than a four-parameter regression on the
player's own recent history (holdout MAE 0.436 vs 0.394). It forecasts the individual
statistics it is built from poorly (recalibrated TEI is 19–202 % worse than that regression
at forecasting true shooting, assist share, rebound share and usage on the holdout), so its
components should not be read as skill estimates. These are statements about forecasting a
box-score proxy, not about impact. Full tables and caveats:
[current-model-assessment.md](r9-1/current-model-assessment.md),
[candidate-experiment-results.md](r9-1/candidate-experiment-results.md).

### Serving scale (C5)

Served rows are z-scored against the **reference season's** minutes-weighted moments,
not against the recency-weighted window's own distribution. Before this fix the two
constructions correlated only **r = 0.387** at team level, and the two rescalings that
implied disagreed by 2.6× — which is the proof that no single transfer factor existed
and that train and serve had to share one reference instead. After it, team-level
served TEI regresses on season TEI with slope **1.015** (r = 0.911).

### Uncertainty bands (R3-4)

Per player, from that player's own playing time:

    σ² = 0.0326 + 240.9 / total_minutes

estimated from **921** same-player consecutive-season pairs. σ runs 0.72 at 500 minutes
to 0.36 at 2,500, replacing a single **2.462** inherited from the retired ridge's
residual spread — a band that was identical for a 2,800-minute starter and a 300-minute
rookie. Most bands are now narrower, which reads as overconfidence and is the opposite;
the thinnest-evidence players' bands are now **wider**. σ here is season-to-season
variability, which is the right input for a forward-looking interval and the wrong thing
to call "measurement error".

**The bands are not calibrated.** Evaluated out of time against the same proxy target, the
nominal 80 % band (`tei ± 1.2816·σ`, ×2.5) contained **60.8 %** of development and **57.2 %**
of holdout outcomes — roughly a third too narrow
([assessment §4, §6](r9-1/current-model-assessment.md)). It is a normal-theory band whose σ was
built around the index's season-to-season variability rather than the proxy's, so this is a
mismatch of design as well as a miss; it should be read as an indication of relative evidence
(wider for thin samples), not as an 80 % interval.

Scores are scaled ×2.5 to index points (elite ≈ +5).

### Index → net rating (R3-2)

A team's minutes-weighted index does **not** arrive in net-rating points. The conversion
is fitted change-on-change over 60 team transitions:

    Δnet = 14.977 · Δ(team TEI)      R² 0.624 · SE 1.528 · t 9.80 · n 60
    per-fold slopes 14.716 / 15.276  (within ±2% of pooled)
    leave-one-transition-out RMSE 2.944 / 3.773 vs 5.201 / 5.805 predicting zero

Change-on-change rather than levels because a team's *level* carries everything the
roster does not — coaching, health, schedule — and differencing removes the team fixed
effect. **Falsification note:** if TEI were already in additive per-player net-rating
units this fit would return ≈ 5. It returns ≈ 15, which is the quantitative statement of
how far the raw index scale is from net-rating points. The coefficient is valid only for
the regressor construction recorded alongside it in `model_versions`.

### Team minutes and replacement level (R3-3)

Team impact is normalised by the **240 minutes a team must field**, not by the minutes a
roster happens to fill; minutes it cannot fill are charged to a replacement-level player.
Replacement level is derived, not assumed: the old hardcoded −2.0 sat at the **14.1st
percentile** of player-season TEI. The rule is the mean TEI of player-seasons outside
their team's top 10 by minutes — **−1.214**.

## 3. Roles

A **deterministic size-first rule chain** over league percentile cut points — 14 roles,
three height tiers, then four or five branches within each tier
(`archetypes.py::assign_role`). It is a total function of one player's row plus the cut
points, so nothing is fitted, nothing is random, and the same profile always produces the
same label. There is no silhouette to report because there is no clustering.

K-means (k=8) did this until R4-3 and was retired for being unstable rather than merely
imprecise. Measured on the 632-player frame: only **5 of its 10** label branches were ever
reached, **217 of 632 rows (34.3 %)** carried a disambiguating numeric suffix, the
silhouette was **0.154** — no separated structure exists in this space, so no k finds one —
and, decisively, **dropping a random 10 % of players rewrote 65.7 % of the surviving
players' labels**. It also filled the league-median height for the 49 players (7.75 %)
whose height is not recorded, and gave them a confident role.

The rule chain moves **1.77 %** of labels under the same resampling, fires all 14 roles
(largest 12.2 %, smallest 3.5 %), carries no numeric suffixes, and is byte-identical
across processes and BLAS thread counts. Players with no listed height are labelled
`unclassified (no listed height)` rather than assigned a role from a fabricated number.

Roles remain descriptive labels and comparable-player groupings, not hard class
boundaries. Gating on size first is deliberate: a creation-first chain was measured to
label a 7'4" centre a "secondary creator".

## 4. Team needs

Transparent percentile rules over real team statistics — no LLM anywhere in the
calculation. Example: bottom-half FG3A → `three_point_volume` need with severity
`(50−pct)/50`. Proxies are labeled as proxies (blocks ≈ rim protection).
Point-of-attack defence is measured as a team **need** but has **no player skill** — see
`docs/limitations.md`; nothing in this repository measures on-ball defence, and the
steals-based proxy that used to stand in for it rated ball-dominant guards above the
defenders who guard them. Roster-composition rules add `lineup_size` (avg height <
77.5") and `secondary_creation` (< 2 players with AST% ≥ 25). Each need stores its
percentile and a plain-English explanation shown in the UI.

## 5. Roster fit

```
F = Σₖ n_k·Δs_k − γ·Σₖ max(0, r_k)        γ = 0.35
```

`n_k` = need severity; `Δs_k` = minutes-weighted change in skill *k* (incoming −
outgoing), where skills are percentiles of the scored population; `r_k` penalizes
adding to skills where the roster's top rotation is already above the 70th
percentile.

## 6. Team projection and rotation

Post-trade projection **reallocates the 240 regulation minutes** rather than summing
player values: proportional to established minutes (a proxy for coach trust), capped
at 36 min/player, user-overridable, allocated by water-filling so no player can exceed
his cap (R4-4); availability discounts expected minutes with replacement-level fill-in at
the **derived −1.214**, not the retired −2.0. Team ΔNetRating is the **fitted** conversion
of the change in minutes-weighted team TEI — `Δnet = 14.977 · Δ(team TEI)`, §2 above. It
is *not* "five players share the floor", which is the identity-scale claim R3-2 measured
and rejected.

Wins conversion is **fit on ingested data**, not hard-coded:

```
wins = 40.93 + 2.235 × net_rating     (90 team-seasons, R² = 0.953, σ_resid = 2.9)
```

`ΔW = slope·ΔNet·(games/82)`. The calibration, sample size, and fit quality are
stored with the model version.

## 7. Contract value (when data permits)

With a configured contract provider, surplus = market share − actual salary share of
cap, where the market share uses a documented cap-dollar-per-impact curve
(replacement ≈ 2.5% of cap, average rotation ≈ 8%, star ≈ 25%, ceiling 35%). This is
labeled a heuristic: a proper market-salary regression requires historical contract
data no bundled provider supplies ([model card](model-card-market-value.md)). With no
provider, the component is excluded entirely.

## 8. Availability

```
Availability_i = Σₛ λ^(s−1)·GPᵢ,ₛ / Σₛ λ^(s−1)·82
```

over the seasons the player was in the league. This is historical availability, not
a medical prediction — RosterLab does not model injuries.

### The risk component is availability exposure, and only that (R5-1b)

```
R = 50 + 50·( a_in − a_out )        a = minutes-weighted availability of a package
```

Until R5 the dominant risk term was `prob_positive`, the Monte Carlo's probability that
Δwins > 0 — which is the **performance component restated as a probability**. Measured
over 482 scored evaluations on the 30 ingested rosters:

| | |
| --- | --- |
| corr(`prob_positive`, performance) | **0.913** |
| corr(risk, performance) | **0.851** Pearson · **0.937** Spearman |
| risk's share of composite variance | **0.244** |

So a quarter of the composite's variance came from a component that was 85–94 % another
component, and `performance` carried roughly twice the weight the vector declared. C12
called folding risk *into* performance backwards; the fix is to take performance back out
of risk. Re-measured on the same sample after the change: **corr(risk, performance)
−0.022** Pearson, −0.048 Spearman.

Three properties of the replacement are deliberate:

- **A change, not a level.** "How durable are the arriving players" is not a question
  about the trade. "Is the team taking on more games-missed exposure than it is shedding"
  is.
- **Minutes-weighted.** Thirty minutes of a 60 %-available starter is far more exposure
  than eight minutes of one.
- **Where a side is empty the baseline is the roster's own measured availability**, not a
  default. The minutes an arriving player does not play are played by the roster that is
  already there, and that roster's availability is measured. When even that is
  unmeasurable the component is withheld.

Availability is a share of games, so its change is bounded on [−1, 1] and maps affinely
to the full 0–100 scale — this is the one component that is not squashed, because both
endpoints mean something.

**A legality-exposure term was built, measured and left unscored.** The share of
implemented CBA rules reaching a definite verdict runs **0.063 ± 0.071 with a ceiling of
0.143** across the same 482 evaluations, and what moves it is which contract fields the
configured provider supplies — a property of the dataset, not of the deal. Scoring it
would have added a near-constant offset, which is what `assets` already was. It is
published on every evaluation with `scored: false`.

### What the weights do and do not control

Component spreads are not comparable, and there is no measurement that says one standard
deviation of fit is worth one of performance — they are in different units, and only
`performance` has a fitted conversion to a real-world quantity. So the scale constants are
**not** re-anchored to equalise them, and the consequence is stated instead. Measured
share of composite variance on 168 fully-scored evaluations after R5:

| component | weight (`custom`) | variance share |
| --- | --- | --- |
| fit | 0.18 | 0.373 |
| contract | 0.14 | 0.243 |
| timeline | 0.16 | 0.209 |
| performance | 0.22 | 0.147 |
| risk | 0.15 | 0.025 |
| assets | 0.15 | 0.010 |

A slider set to 0.22 does not buy 22 % of the influence. It buys 22 % of the *weight*, on
a component whose spread is what it is.

## 9. Age curve and timeline

A conservative piecewise curve (+0.8 TEI/yr under 21 → −1.0 over 36) applied
cumulatively for multi-year horizons; timeline alignment maps age → fit with the
scenario strategy (contenders prize 25–33; rebuilds prize ≤ 25). Documented in
`age_curve.py`; deliberately modest to avoid precise long-range claims.

## 10. Uncertainty

2,000 Monte Carlo draws (fixed seed) per evaluation over: player TEI (the per-player
σ above — season-to-season variability, uncalibrated), availability (Beta concentrated at the historical rate),
minutes noise (±12%), and wins-conversion slope (±15%). Reported: median, p10, p90,
P(Δwins > 0), and top per-player uncertainty contributions. Small differences are
never presented as certainty.

## 11. Sensitivity

500 weight vectors sampled from a Dirichlet centered on the user's weights
(concentration 50) → share of samples in which each alternative ranks first, rank
volatility, and median rank; plus one-at-a-time ±50% tornado bars. A recommendation
is called robust only if it survives these perturbations.

## 12. Draft picks (R5-2)

A pick is four separate unknowns, and RosterLab keeps them separate: what the slot is
worth, where the pick will land, whether it conveys at all, and whether the team owns it.

### The value curve

Estimand: **the above-replacement value the player taken at slot *k* delivers, relative to
the average pick of his own draft class.**

```
player value  =  Σ_seasons total_minutes · (TEI_season − REPLACEMENT_TEI),  floored at 0
relative      =  player value ÷ mean player value of that draft class
rel(k)        =  3.3855 · exp(−0.08388·(k − 1)) + 0.2525      R² 0.7534 on slot means
```

Two construction choices carry the honesty:

- **Absence is a measured zero.** A drafted player who never appears in the window
  contributed nothing above replacement, and that is what a bust is worth. Averaging only
  over players you can see is the survivorship error that makes every late pick look
  useful. The estimation set is 448 drafted players from classes 2016–2023, of whom **323
  appeared** in the window.
- **Within-class normalisation removes the career-stage confound exactly.** The window is
  three seasons, so a 2016 draftee is observed in years 7–9 and a 2023 draftee in years
  0–2. Slot is orthogonal to class by construction — every class has one player at every
  slot — so dividing by the class mean removes the class effect and leaves the slot effect.
  The first-round grid is complete: 240 of 240 cells. The second round is missing 32 of
  240, which are excluded and reported, never imputed.

### What is established, and what is not

| Check (leave-one-class-out over 8 classes) | Result |
| --- | --- |
| Curve ranks the held-out class | **0.4624** Spearman, 8/8 positive, t = 15.36 |
| Within-class permutation null | 0.0588 |
| **Curve vs a two-band round-only rule** | **+0.0405, t = 1.33, p = 0.22 — not significant** |
| Four-band model | 0.4808 — indistinguishable from the curve |
| **Slot gradient inside the first round alone** | **0.3277, 8/8 positive, t = 4.67, p = 0.0023** |

The smooth curve is used because it is not worse than any alternative *and* because the
thing it adds is independently established: ordering inside the first round, which is where
trade currency actually lives. The claim that it beats a coarse round-only rule is **not**
made.

### Why nothing returns a bare number

Bootstrapping the 8 draft classes 2,000 times, the 90 % interval at slot 1 is [2.98, 5.64]
on a fitted 3.64 — **73 % of the value**; at slot 60 it is 150 %. And a single pick's
outcome is far more skewed than its mean: the median pick at slots 15–30 returns **0.150**
against a mean of 0.965, and **38 % return exactly zero**. In the second round 66 % return
exactly zero.

The landing slot compounds it. Measured on the ingested standings, one-year rank drift has
sd **8.53** against a no-information ceiling of **8.66** (rank correlation 0.602 and 0.509
across the two available transitions). A team's finish one year out is barely more
predictable than a coin flip on this data, and multi-year drift is extrapolated as a random
walk — **an assumption, labelled as one** — capped at the ceiling. From about four years
out the cap binds and the support is the whole round.

The **lottery is not modelled**. Only its structure is used: four selections are drawn, so
a lottery team can fall at most four places and any lottery team can rise to first. That is
a fact about the draw, not an estimate of its odds, and the odds table is not reproduced
here because this repository has no source for it. A lottery-exposed pick therefore gets a
support, never a distribution.

### Precision levels

| Level | When | Point estimate |
| --- | --- | --- |
| `interval` | unconditional, ownership verified | yes, with the class-bootstrap band |
| `range` | protected, swapped, or conditional | **no** |
| `unknown` | ownership unverified | **no** |

A protection whose text cannot be parsed into a selection range is treated as *conditional*,
not as unprotected — the dangerous failure mode is unreadable terms silently becoming clean
ones.

### Ownership

From a local RealGM future-drafts snapshot (`make import-draft-picks`; the file is
gitignored and never redistributed). Of 394 entries in the 2026-07-28 snapshot, **184 are
unconditional and become 92 verified picks**; 161 swaps, 33 protections and 16 conditional
conveyances are stored with their source sentence, `is_verified = false`, and a
`conveyance` class. Zero team names were unresolvable.

The Stepien rule now **certifies the teams the source resolves** and reports `unavailable`
— naming the specific clause — for the rest. One unresolved swap is enough: a team's
ownership picture is then genuinely uncertain, and a pass would be invented.

## 13. Comparable trades (R6-2)

### The unit is a side

"Boston traded Marcus Smart for Kristaps Porziņģis" and "Washington traded Kristaps
Porziņģis for Marcus Smart" are the same transaction and two different decisions. The
retrieval unit is therefore **one team's view of one trade**, and a three-team trade
contributes three of them. At most one side of any transaction is returned in a result
list; both remain in the corpus and both are ranked.

### The season a trade is described by

A comparable is only evidence if the numbers describing it existed when the trade was
made. Until R7 the feature season came from the calendar **month** — July, August and
September were offseason, everything else in-season — and the month is not enough to
decide it. Three groups of trades were being described by a season that had not started:

| the month rule said | what had actually been played |
| --- | --- |
| 33 trades in November 2020 are 2020-21 | the 2020-21 season began **22 December 2020** |
| 12 trades on 26–28 June 2024 are 2024-25 | Basketball-Reference files draft night under the season about to start; 2023-24 is the season that finished |
| 10 trades in early October are that season | first games fall 22–25 October |

**57 of 565 trades, 10.1 %.** The June-2024 group sits inside the three-season window R6
shipped, so this was live rather than latent. `is_in_season` — a scored feature in the
`timing` dimension — was wrong on 163 trades for the same reason: no games are played in
June or in early October.

R7 replaces the month with the boundary as **data**. `season_calendar` holds the first and
last regular-season game of each season, ingested from `LeagueGameLog`, and the rule reads
it: a trade between a season's first and last game is described by that season and is
in-season; otherwise it is described by the most recently completed one and is not. With
no calendar ingested the month rule still answers, and the response reports
`calendar_backed: false` rather than giving a confident answer with nothing behind it.

### The distance

Sixteen features in six dimensions. Each dimension's distance is the mean over the
features **both sides state**; the total is the weighted mean over the dimensions that
survive:

```
d(a, b) = Σ_g w_g · d_g(a, b) / Σ_g w_g          over dimensions g with data
d_g     = mean over f in g of  |a_f − b_f| / (|a_f − b_f| + scale_f)
```

| dimension | w | features |
| --- | --- | --- |
| `player_value` | 0.30 | value in, value out, best player in, best player out |
| `draft_capital` | 0.25 | net **unconditional** firsts, net **conditional** firsts, net seconds, picks in, picks out |
| `structure` | 0.20 | players in, players out, teams involved |
| `age_profile` | 0.10 | minutes-weighted age in, age out |
| `team_context` | 0.10 | win percentage, gap to the other side |
| `timing` | 0.05 | in-season or offseason |

**Nothing is truncated.** `|Δ| / (|Δ| + s)` is bounded, monotone and scale-free, so two
very different trades stay ordered rather than both landing on a ceiling — R5-1a's
finding applied again; a hard clip at one scale unit put 41 % of corpus pairs on the cap.

**Counts get a declared unit; continuous quantities get the corpus's interquartile
range.** One pick is one pick. Estimating the pick scales was tried first and
degenerates: 71 % of rankable sides receive no first-round pick, so the interquartile
range is zero, the median absolute deviation is zero, and the chain falls through to the
standard deviation — the tail-driven statistic this construction rejects for exactly
those columns.

**A feature enters only when both sides state it**, and a dimension neither side states
is dropped with its weight redistributed and its name returned.

**First-round picks are split by how they convey, and that split was forced by a
measurement.** The first construction carried one `firsts_net` plus a
`conditional_pick_share`, and protections then made no difference at all: attaching a
top-4 protection to a query's first-round pick returned the **identical** top five
(overlap 1.000), because a share diluted across five features can move the total distance
by at most 0.033 against a corpus whose pairwise similarity spans 0.51 to 0.87. A
protected first and an unconditional one are not the same asset — R5-2 refuses to price
the first at all — so the count itself is split. Measured after the change, on a
one-for-one player trade with picks attached:

| query change | top-5 overlap with the base query |
| --- | --- |
| +1 unconditional first vs no picks | 0.429 |
| +1 second-round pick vs no picks | **0.000** |
| +1 first vs +1 second | 0.250 |
| 1 first: unconditional vs **protected** | **0.667** (was 1.000) |
| 1 first: unconditional vs **swap** | 0.667 |
| 1 first: protected vs swap | 1.000 — deliberate; both are "does not convey unconditionally" |

### What the similarity deliberately excludes

- **Salary** — no source here carries a historical contract, so scoring the query's
  money would compare a number against nothing.
- **Cash and trade exceptions** — the corpus states both, a proposal states neither, so
  the feature could only ever penalize the 37 % of completed trades that include one.
  Both are reported as attributes of a neighbour.
- **What happened next.** Nothing reads the outcome.

### Validation

`make comparable-validation`, leave-one-out over **1,151 sides** at k = 5, about 70 s:

| check | measured | gate | |
| --- | --- | --- | --- |
| Perturbation rank displacement (±10 % of each feature's scale) | **4.33** | ≤ 10 | ✅ |
| Scale form — standard deviation instead of IQR | **0.761** | ≥ 0.50 | ✅ |
| Scale form — min-max | 0.448 | reported | null, see below |
| Distance form — hard clip instead of saturation | **0.717** | ≥ 0.50 | ✅ |
| Best single-dimension null | **0.060** | ≤ 0.75 | ✅ |
| Archetype recovery, lift over a shuffled-feature corpus | **0.493** | ≥ 0.10 | ✅ |
| Direction confusion (selling retrieved as buying) | **0.025** | ≤ 0.05 | ✅ |
| Perturbation stability (top-5 overlap) | 0.611 | reported | withdrawn as a gate, see below |

Archetype precision@5 is **0.917** against a 0.433 base rate; a random ranker scores
0.444 and a corpus whose feature vectors have been permuted between sides scores 0.424 —
both land on the base rate, which is what a null must do. Leave-one-dimension-out shows
draft capital drives the list hardest (0.254 overlap without it), then structure (0.317)
and player value (0.325); timing barely matters (0.707). Neighbours do not cluster in the
query's own season (0.123 against a 0.102 base rate), so this is not a date lookup.

**R7 widened the corpus and every one of these improved**, which is the evidence that the
R6 figures were partly a small-corpus artifact rather than a ceiling: precision@5
0.797 → 0.917, lift 0.392 → 0.493, best single-dimension null 0.089 → 0.060.

### Perturbation stability was withdrawn as a gate, and not because it failed

It measured the mean top-5 Jaccard overlap when the query is wobbled by 10 % of each
feature's scale, gated at 0.60. Widening the corpus took it to 0.595, and investigating
that condemned the criterion rather than the release. Two independent reasons:

**It is a statistic about corpus size.** Random subsamples of one corpus score 0.707 at
n = 200, 0.676 at n = 337, 0.651 at n = 600 and 0.611 at n = 1,151. Subsampling the
ten-season corpus back to the three-season corpus's 337 sides reproduces the three-season
number — 0.651 against 0.648 — so retrieval was unchanged and only the statistic moved. A
top-k overlap asks "are the same five still the five", which gets harder purely because a
larger corpus puts more candidates at a similar distance.

**Both nulls pass it**, which is disqualifying under the rule R4-2 established:

| ranking | perturbation stability |
| --- | --- |
| random-hash null (no information about any trade) | **1.0000** |
| shuffled-feature null | 0.6181 |
| the shipped distance | 0.6110 |

The random null scores *perfectly*, because a ranking keyed only on a side's identity
cannot move when its features do. A gate that a zero-information ranking wins by the
maximum possible margin is not a gate.

**What replaced it** is the same wobble read by **rank**: the five neighbours the
unperturbed query returned must still average inside the top ten of the perturbed one.
Measured 4.33, median 4.0, p95 7.0. It holds as the corpus grows — 4.72 at n = 200 against
5.32 at n = 1,151 — which is what the level could not do.

**Robustness is not validity, and R7 stopped conflating them.** R4-2's rule was written
about claims that a metric measures something. Applied literally to every check here it
would delete half of them, because a ranking that ignores the trades entirely is trivially
insensitive to how the scales were estimated. The battery now says which kind each check
is: `archetype_recovery` and `direction_confusion` are **validity** criteria and the nulls
fail both decisively; the form-sensitivity checks are **robustness** criteria — necessary,
not sufficient, and never evidence of validity.

**Min-max scaling is a null, not an alternative.** It takes every feature's unit from the
single most extreme trade in the corpus — a seven-team deal moving six first-round picks
— which is the failure the declared scales exist to avoid. Its 0.448 overlap is
reported, never gated: agreement with a scaling that is known to be wrong would be the
bad outcome.

**The weights are chosen by construct and they matter.** Uniform weights change 56 % of
the returned list (0.443 overlap). Nothing in this repository labels two trades as
similar, so there is no target to fit them against; what is established is that no
single dimension reproduces the shipped ranking and that the per-dimension decomposition
is returned with every result, so the choice is inspectable rather than hidden.

### One criterion was replaced rather than waived — and not because it failed

The battery originally asserted that a side whose two directions are genuinely different
must resemble its own mirror image **less** than two unrelated sides resemble each other.
It did fail when it was written, at 0.679 against a 0.672 median. It was replaced because
it does not test *retrieval*: similarity **levels** on this corpus compress into p05 0.521
… p95 0.873, so hundredths of level are not evidence about a list.

The clearest illustration is that the same statistic now reads **0.676 against a 0.685
median** — it would pass — purely as a side effect of splitting first-round picks by
conveyance, a change made for an unrelated reason. A criterion that flips on a change made
elsewhere is not measuring what it claims to.

By list, the mirror is far away: injected into the corpus it is the nearest neighbour for
5 of 515 asymmetric sides, reaches the top five for 10, and sits at **median rank 386 of
1,152**. Direction confusion measured on **real** trades replaces it, at 0.025 against a
0.05 ceiling. Both figures are still reported. (The R6 figures, on the 337-side corpus,
were 2 of 141, top-five for 11, median rank 89 of 338, and 0.019.)

## 14. Need-driven acquisition (R6-3)

Two rules, stated in every response and deliberately not merged:

- **Filter** — the player's percentile in the skill that addresses the chosen need must
  exceed the acquiring roster's own level in that skill (its third-best rotation value,
  the same definition §5 uses for "already strong here").
- **Rank** — the projected win change from adding him, from §6, before anything leaves.

Ranking on `fit` was rejected for a reason §5's own construction records: `fit_score`
normalises minutes within each side, so a package's size cancels and an 8-minute
specialist scores like a 32-minute starter. A single blended score was rejected because
the weight between need and impact cannot be fitted — nothing here labels a target as
good.

**Feasibility is what makes it a target list rather than a leaderboard.** Ranking by
projected wins alone gave all 30 teams the same names: 26 distinct players across every
team's top five. Each candidate is now put through the trade evaluator with a package
that balances its modelled value, and kept only if it clears the conditions the
candidate generator already applies — both sides above 50, neither worse than −2
projected wins, not verified illegal. Distinct players across the league's top fives:
**26 → 72**.

`make acquisition-validation`, over the 30 ingested rosters:

| check | measured | gate | |
| --- | --- | --- | --- |
| Need filter differentiates (distinct-player ratio) | **2.77** | ≥ 1.5 | ✅ |
| Same-need teams hear more alike than cross-need teams | **+0.090** | ≥ 0.05 | ✅ |
| ...but not identical (same-need overlap) | **0.113** | ≤ 0.9 | ✅ |
| Every returned target improves its need | **1.000** | = 1.0 | ✅ |
| Shuffled-need null (overlap with the team's own list) | 0.160 | reported | |

Cross-need overlap is 0.023 against same-need 0.113, and re-running a team with another
team's diagnosed need changes 84 % of its list.

## 15. Rotation shape (R6-4)

Minutes by player role before and after a trade, from §3's deterministic roles and the
**same** §6 allocation the projection used — never re-derived, because calling the
allocator twice is the defect R5.5 fixed. A role is congested when its post-trade minutes
exceed the 90th percentile of that role across the 30 ingested teams, computed from each
team's own allocated rotation; every team contributes a zero for a role it does not hold,
so a rare role's threshold is not high *because* it is rare.

This is roster composition, not lineup data. Nothing here knows who is on the floor
together. See [limitations.md](limitations.md) for the measurement behind that.

## Reproducibility

Fixed seed (20260720) for all stochastic steps; every model version records
algorithm, features, target, training window, validation metrics, artifact path,
code commit, and timestamp in `model_versions`. `make sync-data && make train &&
make score` reproduces the full pipeline **on a fresh, disposable database** — never on a
database whose model versions or estimates you need: `make train` retires active models and
deletes superseded impact estimates (see *Operating safely* in the
[README](../README.md#operating-safely-data-training-and-models)).
