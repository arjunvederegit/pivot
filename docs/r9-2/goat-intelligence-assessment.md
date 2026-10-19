# R9.2 — BALLDONTLIE GOAT: what contributed, what was learned, what is next

**Bottom line.** GOAT did not feed any R9.2 model, and no new historical data were acquired (**0 additional requests**). Every served quantity
comes from ten seasons of NBA.com history, because GOAT's per-game statistics are numerically identical to NBA.com's (R8.3) and its
newer families exist for one season only (R9.1). What R9.2 *did* do with GOAT is (a) use its one ingested shot-zone season to ask the
questions a single season can answer, (b) test an explicit, auditable rule for promoting the medium-confidence identity links, and
(c) establish — before spending a single request — that the backfill R9.1 proposed would be **biased by survivorship** unless an identity
problem is solved first, and specify how to solve it.

## 1. What GOAT contributed to R9.2

| Use | Result | Evidence |
| --- | --- | --- |
| Training or serving any quantity | **None.** `general/base` per-game = NBA.com's (R8.3: numerically identical); the other six families have one season | `docs/r8-3/`, `docs/r9-1/goat-feature-readiness.md` |
| An independent check of the shot-count reconstruction | **Not available from GOAT** (its `general/base` is per-game, one decimal, like NBA.com). The check used an independent CSV import of exact 2025-26 totals instead: 573 player-seasons; counts recovered exactly in 476 of 476 rows the reconstruction called *exact*, and within 5 attempts everywhere | `tests/unit/test_pi_components.py`, [shooting-validation-results.md](shooting-validation-results.md) |
| Is shooting one skill or several? (cross-sectional, 2025-26 `shooting/by_zone`) | See §2 — an informative but imprecise answer | `scripts/r9_2_goat_assessment.py` → `results/goat_assessment.json` |
| Identity: can the 14–15 medium-confidence links be trusted? | **14 of 15 corroborate** exactly (games played equal, minutes per game equal to the provider's rounding); one has no 2025-26 row to compare. A promotion rule is specified and **not applied** | §3 |

## 2. What one season of shot-zone data can say about the *structure* of shooting

`shooting/by_zone` publishes per-game attempts and makes by zone, with the accuracy to three decimals. Two zones of one player-season are
**independent binomial measurements**, so the covariance of the *observed* rates equals the covariance of the *true* rates; subtracting
each rate's expected sampling variance from its variance gives a noise-corrected correlation. (Rates use the published percentage — no
per-game rounding; the rounded attempts enter the sampling variance only. Intervals are player bootstraps; > 1 values occur when a noisy
variance estimate is small, and mean "not distinguishable from 1".)

| Pair (trusted, high-confidence population; 2025-26) | n | Observed r | Noise-corrected r | 95% interval | Reading |
| --- | --- | --- | --- | --- | --- |
| Corner-3 % vs above-the-break-3 % (≥ 40 attempts in each) | 237 | 0.29 | **0.87** | 0.50 – 1.69 | consistent with **one shared three-point skill**; zone-specific skill is not ruled out |
| Free-throw % vs above-the-break-3 % (≥ 100 attempts in each) | 151 | 0.26 | **0.48** | 0.18 – 0.84 | free-throw and three-point accuracy are related but clearly **not the same quantity** — the engine keeps them separate |
| Free-throw % vs mid-range % (≥ 40 mid-range, ≥ 100 FT) | 117 | 0.16 | 0.29 | −0.09 – 0.65 | not distinguishable from unrelated |

Median season attempts by zone (trusted population): above the break 77, corner 33, restricted area 78, in the paint (non-restricted) 45,
**mid-range 14**. A zone-accuracy "skill" for most players rests on a few dozen attempts a year; mid-range is hopeless at that size.

What this does *not* show: that zone accuracies are validated skills (no stability or out-of-time test is possible with one season), or
that GOAT zone data would improve R9.2's forecasts. It supports two design decisions: keep three-point accuracy as **one** quantity (not
split by zone) until multi-season zone data can show otherwise, and keep free-throw accuracy **separate** from it.

Also measured, as a caution for R10: restricted-area accuracy has a "true" spread (SD 0.050) comparable to its observed spread (0.061) —
but that spread is dominated by *which* shots a player takes at the rim (dunks vs floaters), not by finishing skill; accuracy by zone is
not a skill without a shot-quality adjustment the data cannot provide.

## 3. Identity: trusted population and the medium-link rule

* Trusted modeling population = **high-confidence** links only. In the one ingested season: 556 of 570 shot-zone rows are high-confidence
  (555 of them also have an NBA.com row), 14 are medium. No medium link entered any model or analysis here.
* **Proposed corroboration rule** (documented, auditable, *not applied*): a medium link may be promoted to a verified status only if, in a
  season where both providers carry the player, **games played agree exactly and minutes per game agree to the provider's rounding
  (0.05)**. Applied as a read-only check it corroborates 14 of 15 medium links. Promotion would be an explicit, owner-reviewed write of
  that evidence into `provider_entity_ids.evidence`; nothing was written.
* **The crosswalk covers active players only** (BALLDONTLIE's payload carries no NBA.com id; links are made on name, team, draft slot and
  height among the 526 *active* players). That is the decisive fact for any historical backfill — §4.

## 4. The proposed backfill, re-examined (not executed)

R9.1 proposed ≈ 260 read-only requests (four families × ten more seasons) for a defence-feasibility study. R9.2's authorization allowed up to
300 and required the endpoints, access, request count and a safe storage design to be settled first. They are *not* all settled:

| Requirement | Status |
| --- | --- |
| Exact endpoints and count | `defense/overall`, `hustle`, `shooting/by_zone`, `tracking/possessions` for 2015-16 → 2024-25 (2025-26 is already stored and **must not be refetched**: the store upserts on `(provider, entity, player, season, season_type, stat_group)`). Page size 100 → ≈ 6–7 pages × 4 families × 10 seasons ≈ **260 requests**, ~30 s at the provider's stated 600/min. Plus ≈ 60 for `general/base` per season (see below) |
| Append-only storage | The existing table is *unique-keyed*, so a new season is a new key and cannot overwrite another — but a re-run of a stored season **would** overwrite. The backfill must be insert-only (refuse on conflict) and record `retrieved_at`; a separate history table would make later re-retrievals auditable. Needs a small additive migration (not written) |
| **Identity for historical seasons** | **Unsolved, and it decides the study's validity.** Only active players are linked, so a historical backfill would contain only players still in the league in 2025-26: every stability or predictive result would be computed on **survivors** — the group least likely to show regression. Fixing it needs links for retired players, and name matching (the only available method) yields `medium` confidence, which R9.2 forbids as training input |
| A way to make historical links trustworthy | The corroboration rule of §3 generalizes: retrieve `general/base` per season (≈ 60 requests), match each GOAT row to NBA.com's row for the same season by name **and** require games played and minutes per game to agree exactly. Agreement on two exact, independent statistics makes a name coincidence implausible, and the evidence is stored per link |
| Credentials | Would use the existing key through runtime configuration only; never printed, logged or committed. Not needed for what was done |

**Decision.** Deferred. Doing it now would spend requests to produce a survivor-biased defence study, and the core shooting engine does not
depend on it. **Sequence for the owner:** (1) additive migration for insert-only snapshot history; (2) historical identity by season via the
corroboration rule, reviewed; (3) the ≈ 260-request backfill, once, frozen, fingerprinted; (4) the defence feasibility study with a
pre-registered stability and incremental-information test, where "no-go" is an acceptable outcome.

## 5. What each GOAT family could improve, and what is demonstrated today

| Question | Family | Information gain over NBA.com history | Reliability | Demonstrated model improvement |
| --- | --- | --- | --- | --- |
| Shooting profile | `shooting/by_zone` | new (zone split) | median 14–77 attempts/zone/season; corner-vs-ATB corr. 0.87 [0.50, 1.69] | **none** (one season) |
| Offensive role / creation | `tracking/possessions`, touches | mostly redundant: 77–90% explained by usage, assists, minutes (R9.1) | high for volumes | none |
| Defensive evidence | `defense/overall`, `hustle` | defended-FG% vs expected is the least redundant (R² 0.27–0.43 on existing features); hustle counts 72–79% redundant | defended-FG%: median 488 attempts; stability untested | **none** — untestable until a backfill |
| Clutch | `clutch/base` | — | median 46 minutes: too small to be a skill | not a skill; excluded |
| Contract value | (contracts) | not a skill | — | excluded by design |

A metric that box-score statistics do not explain is not thereby a skill: the information gain, the reliability and the demonstrated
improvement are separate columns on purpose, and only the first is non-empty for most families today.

## 6. Future analytical opportunities (ordered)

1. Historical identity by corroboration → makes any GOAT history usable without survivorship (unblocks everything below).
2. Defence feasibility: stability of defended-FG% vs expected over ≥ 3 seasons, then incremental information about *team* defensive rating
   beyond blocks/steals/rebounds, using R9.1's team gate and its circularity caveats.
3. Multi-season zone accuracy: test whether a corner/above-the-break split improves three-point forecasts beyond the single quantity.
4. Shot-quality context (tracking) as a covariate for finishing — only if the above show stable signal.
