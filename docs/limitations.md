# Limitations

Candid scope statement — these are design boundaries, surfaced in the product
itself, not fine print.

## Data

- **No contract provider is bundled.** Payroll, tax/apron status, salary matching,
  and contract value are unavailable until the operator supplies lawful contract
  data; the app says so everywhere it matters and never estimates a salary.
- **No injury feed.** Availability is historical games played — not health status,
  not a medical prediction.
- **No verified draft-pick ownership.** Picks in trades are labeled hypotheticals;
  Stepien compliance is never certified.
- **Live game data is disabled by default** (offseason at build time; cdn.nba.com is
  also edge-blocked from some networks, which the app reports as a classified
  provider error rather than hiding).
- NBA.com may change or throttle endpoints without notice; the client fails to
  classified errors and retains the last valid snapshot with stale badges.
- **No historical salaries.** The comparable-trade corpus knows what moved in 565
  completed trades and nothing about what any of it was paid. Salary is therefore
  excluded from the similarity, and the response says so rather than scoring the
  query's money against nothing (R6-2).

## Modeling

- **A player the model has never scored is excluded, not averaged.** 43 of 530
  rostered players (8.1 %) have no impact estimate. They are left out of the
  projection and named in the response, and confidence drops to low when one is in
  the deal. They still count against roster limits. Before R1-4 they arrived at
  `tei = 0.0`, which is the **63rd percentile** of rostered players — a silent
  default more favourable than the −0.293 league mean suggests.
- **Roster fit needs players on both sides.** It compares the arriving package with
  the departing one, so a deal with nothing arriving (or nothing leaving) has no
  comparison to make and the component is withheld rather than scored against an
  invented median player. R5 introduces a measured replacement baseline.
- **TEI is box-score-based.** No tracking, matchup, or lineup on/off data; defense
  is structurally under-measured. It is a unitless, hand-weighted composite (not points
  per 100 possessions) and a descriptive one: no teammate or context adjustment, and not a
  causal estimate of a player's incremental impact. What is and is not established, kept
  apart on purpose ([model card](model-card-player-impact.md),
  [R9.1 assessment](r9-1/current-model-assessment.md)):
  - *Functional implementation and testing:* the computation is deterministic and
    reproducible, and the test suite checks its code paths. That says nothing about accuracy.
  - *Predictive evaluation:* one stored forward transition (2024-25 → 2025-26, n = 464) has
    MAE 0.645 against 0.717 for raw persistence. The R9.1 rolling-origin backtest found it
    indistinguishable from regressed persistence with an age curve, and 10.8 % worse than a
    history regression on the locked holdout (MAE 0.436 vs 0.394). All of this is
    forecasting the index's own PIE-heavy proxy target.
  - *Calibration:* the "likely range" is **not calibrated** — the nominal 80 % band
    contained 57 % of holdout outcomes (61 % in development).
  - *Empirical validation:* none for "impact". No independent target with demonstrated
    construct validity exists in the repository; the available criteria share information
    with the index or are team-confounded. The team-level R² (0.75) is concurrent.
  - *Uncertainty limits:* the band is a normal-theory season-to-season σ, and still
    understates tail risk (role changes, injuries, aging outliers).
  - *Missing inputs:* a scored player's missing input is filled with a league-average
    z-score of 0.0 (legacy behaviour; see [methodology](methodology.md#2-tei--rosterlab-estimated-impact)).
- **The wins conversion is a cross-sectional fit** (same-season net rating → wins)
  applied to hypothetical roster changes; it assumes context stability.
- **Rotation reallocation is a model of coach behavior**, proportional to
  established minutes with caps — real rotations will differ; users can override
  minutes.
- **Roles are descriptive labels, not positions.** They come from a deterministic
  size-first rule chain over league percentiles (R4-3), not from clustering, so the same
  profile always yields the same label. Gating on size first means a tall high-assist
  player is named a big: Kevin Durant is a "playmaking big". **49 of 632 scored players
  (7.75 %) have no listed height** and are labelled `unclassified (no listed height)`
  rather than given a role from an imputed number.
- **No player skill claims to measure point-of-attack defence, on purpose.** A composite
  was built in R4-2 and withdrawn: on its pre-registered class — high-usage, high-assist,
  sub-6'8" players with real minutes — it scored **0.630 mean with 75 % above the median**
  against the steals proxy's 0.611 / 70 %, i.e. **worse than the thing it replaced**. A
  steals-led measure necessarily rates ball-dominant guards highly, because gambling into a
  passing lane appears in a box score and staying in front of a ball handler does not. The
  team-side need is still measured and shown, with the reason nothing is claimed about it.
  Measuring it needs matchup and tracking data this repository does not have.
- **`team_defense` is not a validated measure of defensive ability.** It is a construct:
  every term is a defensive act (blocks, steals, defensive rebounds, fouls as a cost, and
  points allowed on court). It is more stable than the steals proxy it replaced
  (year-over-year 0.838 vs 0.669) and imports less team quality (0.99 vs 1.51 of the decile
  gap), but every validation target available here derives from on-court `DEF_RATING`, so
  every available test is circular to some degree. Two specific consequences worth knowing:
  the composite is **big-biased** — blocks and defensive rebounding carry 0.58 of the weight
  — and its defensive-rebound term **overlaps the separate `rebounding` skill** at r = 0.58.
- **Three-point accuracy is shrunk, so extremes are pulled toward the league mean.** 37 % of
  player-seasons have under 50 attempts, so an unshrunk percentage ranks small-sample
  non-shooters at both extremes. The shrunk figure is deliberately conservative for
  low-volume shooters, and is **withheld entirely** for the 22 window players with no
  attempt record rather than defaulted to the league average.
- **Contract value (when enabled) is a documented heuristic**, not a fitted market
  model, until historical salary data exists.
- **The candidate generator is hidden and experimental** (R1-8). It has no UI entry
  point. Measured, it searches only about **14 % of counterparties** — it exhausts a
  400-evaluation budget after roughly six teams — and applies no salary matching, so it
  will propose packages that could never be executed. `POST /trades/generate` still
  responds, and its `coverage` block states exactly which teams were and were not
  searched. Its utility scores were never evidence that a real front office would accept
  a deal, and with that coverage they are not evidence of anything league-wide either.
  R5 rebuilds the search salary-matched and deterministic.

### Comparable trades stop where the player model does (R6-2, widened in R7-2)

Ten seasons of Basketball-Reference transaction pages are ingested — **565 trades,
2,568 asset legs, 69 of them involving three or more teams**. Ranking a side needs the
on-court value of every player in it, so how much of the corpus is *rankable* is set by
how many seasons of `player_season_stats` are held. R6 held three; R7 ingests ten:

| | R6 | R7 |
| --- | --- | --- |
| trades ingested | 565 | 565 |
| team-sides | 1,225 | 1,225 |
| seasons of player production | 3 | 10 |
| sides whose feature season has production | 352 | 1,186 |
| ...rankable | **337** | **1,151** |
| ...withheld because a player in them has no modelled production | 15 | 35 |
| distinct trades rankable | 154 | **535** |

The withheld sides are withheld rather than priced at zero: each contains a player who
had played in the NBA before the trade but recorded no minutes in its feature season, and
pricing him at zero would understate the package by an unknown amount. A player who had
recorded **no** NBA season before the trade — a draft right, a rookie moved on draft
night — contributes zero, which is a measurement rather than an imputation.

**Thirty trades still cannot be ranked at all**, and the reason has moved. It is no longer
the modelled window: it is that their feature season falls before 2016-17, or that a
player in every side of them has no production in a season the database holds.

The widening is **not** a widening of the modelling window. `HISTORY_SEASONS` is still
2023-24 … 2025-26, and every served estimate — player impact, skills, the rotation
allocation, the R3 conversion coefficient — is fitted and served on exactly those three.
The two windows are separate settings for that reason, and the isolation is measured: the
served window frame is byte-identical at 632 rows × 33 columns and every R3 calibration
figure reproduces to full float precision on a season frame that grows from 1,714 rows to
5,483.

**1,341 of 1,500 player legs (89.4 %) resolve to a player in this database.** The 159
that do not are almost entirely draft-rights players who never appeared in an NBA game;
each is filed as a data-quality warning and none is fuzzy-matched.

The restriction is not purely a loss. The 2023 CBA changed how trades are built, and
the corpus shows it: the share of moved **first-round picks** carrying a protection or a swap
rose from **0.379 to 0.600** across the rule change, picks per side from 1.317 to 1.581,
and the multi-team share from 0.163 to 0.247. A 2019 comparable is a comparable under
different rules.

### Lineup-aware fit is deferred, on measurement (R6-4)

Not "not built yet" — measured and refused. `nba_api`'s `LeagueDashLineups` is reachable
and returns real data; on 2024-25 totals, over the top 2,000 groups by minutes:

| group size | median minutes | share ≥ 200 min | implied sd of net rating |
| --- | --- | --- | --- |
| 2 | 376.9 | 88.4 % | 3.7 per 100 |
| 3 | 249.4 | 66.6 % | 4.6 per 100 |
| 5 | **20.2** | **1.6 %** | **16.1** per 100 |

At five-man level the estimate is noise: 16 points per 100 possessions against a league
team spread of roughly ±10, and that is the median of the *top* 2,000 groups. Two- and
three-man groups are estimable and still do not give a trade-fit model — a trade prices
combinations that have never played together, so observed groups can only support a
synergy model, and nothing here holds a held-out target to validate one against. Any
target built from on-court net rating is also the circularity R4-2 withdrew a claim
over.

Two independent confirmations: the local Kaggle `nbadb` play-by-play ends **2023-06-12**,
before the first season this product models, and Basketball-Reference's `robots.txt`
disallows `*/on-off/` and `*/lineups/` outright.

`make lineup-availability` re-runs the measurement, so the deferral can be overturned by
evidence rather than becoming folklore. What *is* built is **roster composition** —
minutes by player role before and after a trade, against the league's own distribution —
and it says in its own text that it is not lineup data and makes no claim about on-court
synergy.

## What the product refuses to answer

- **An illegal trade gets no decision score.** When any participating team's verdict is
  `verified_illegal` the composite and every component are withheld and the failing
  rules are shown instead. Scoring a deal that cannot be executed invites comparing it
  against deals that can. The comparison board lists such deals but never ranks them.
- **"Nothing could be scored" is not zero.** With no scorable component the composite is
  `null`, not 0.0 — on a 0–100 scale a zero reads as a catastrophic verdict.
- **A deal in which nothing moves has no probability.** `prob_positive` is `null`, not
  0 %, when no players move.
- **Zeroing every weight yields no score**, rather than silently restoring a uniform
  prior over components the user switched off.
- **A comparable trade is not a prediction.** Nothing in the retrieval reads what
  happened after the trades it returns. Resemblance is evidence about precedent, not
  about consequence, and a historical deal that worked is not an argument that yours
  will (R6-2).
- **A target list is not an offer.** Acquisition targets are filtered by a diagnosed
  need and ranked by projected wins, then put through the trade evaluator under the
  conditions the candidate generator applies. Surviving those is not evidence that the
  other front office would accept, and how central a player is to his own team is
  reported, never scored (R6-3).
- **A need no player skill addresses returns nothing, and says why.** San Antonio's only
  measured weakness on the ingested data is point-of-attack defence, which R4-2
  withdrew every player-side claim over; the response names the need and quotes the
  reason rather than returning an empty list.

## CBA coverage

A documented subset (see [cba-rule-coverage.md](cba-rule-coverage.md)): no
sign-and-trades, trade exceptions, cash, BYC/poison-pill, or hard-cap triggers;
trade salary equals contract salary (no incentive/guarantee adjustments); roster
counts can't distinguish two-ways without contract data (handled with widened
honest bounds). **RosterLab is not an official cap-management product.**

## Engineering

- Dev-mode cache is in-process (single instance); Redis semantics only in
  compose/production.
- Backend coverage is **88 %** overall, enforced by a `--cov-fail-under=85` floor in CI
  — core domain logic (CBA rules, analytics) is the tested surface; network-touching
  ingestion paths are exercised by probe scripts and classified-error tests rather
  than live CI calls. (`ingestion/jobs.py` remains at 0 %; R5 addresses it.)
- E2E tests run in CI against a **dedicated database** seeded with a synthetic demo
  league (`make e2e`), never NBA.com and never a developer's ingested data.
