# Player impact (TEI): audit findings and the league-percentile presentation

Written during the terminal redesign (`feat/pivot-terminal-redesign`). This document covers
two things: what the impact number actually is and what changed in how Pivot shows it, and
the modeling issues found along the way. The model itself was **not** retrained or changed.

## 1. What the number is

- **Pivot Estimated Impact (TEI)** is a fixed-weight, minutes-weighted z-score composite of
  box-score rates multiplied by 2.5 (`backend/app/analytics/impact.py`). It is a **unitless
  index**. It is not points per 100 possessions.
- The inputs are recency-weighted over three seasons (2023-24 → 2025-26, λ = 0.7), and players
  need at least 200 window minutes.
- Each estimate carries an analytic 10–90% band:
  σ_z = √(0.0326 + 240.9 / window minutes) × 2.5.
- A team's minutes-weighted TEI converts to net rating through a fitted coefficient of
  ≈ 14.98 (60 team transitions, R² 0.62). Projected wins come from that conversion and the
  `team_projection` slope, and are served only as a **change**, with p10–p90.

## 2. Why "+2.0, +1.8, +1.6" did not communicate anything

These figures come from the real dev database: 2025-26, active model `20260825-0c89cb6a46a8`.

- **512** players are scored. **487** are on current rosters. **43** rostered players are
  unscored because they fall below the minutes threshold. **25** scored players are active
  but not on a roster.
- The distribution is right-skewed: min −3.56, median −0.46, max +4.58. Only 36% of players
  are above 0, and an index of 0.0 sits at the **64th percentile**. A "+0.5" therefore
  reads as roughly average-plus when it is actually the top quarter.
- Rounding collapses the scale. At the one decimal the UI showed, 512 players share only
  **61 distinct values**, and the top 50 players span just 18 of them.
- A clear group stands at the top: six players above +3.3 (Jokić, Antetokounmpo,
  Gilgeous-Alexander, Wembanyama, Dončić, Embiid). Below that, ranks crowd together.
  Low-minute specialists sit level with All-Stars; for example, Ty Jerome (1,747 window
  minutes) is tied with Tatum at +2.11.
- The bands are wide in rank terms. Placed in the league, a typical player's 10–90% band
  covers a **median of 57 percentile points**. Differences of less than about 1.0 TEI are
  within band noise.

**Conclusion:** the model does separate players, though only coarsely below the top
group. Raw decimals with no reference population hid both the separation and the
uncertainty.

## 3. What changed

### Backend (additive, backward compatible)

- `GET /api/v1/players/impact-distribution` (`services/impact_reference.py`) returns the scored league:
  - Every served TEI for the current season under the active model, sorted, at the same
    two-decimal precision every endpoint serves.
  - The model version and training window.
  - The population definition and the 200-minute rule.
  - Scoring coverage: rostered scored, rostered unscored, and unrostered scored.
  - The percentile and rank methods, and the limitations.
- `GET /teams/{id}/roster` rows now carry `tei_low` / `tei_high`, the band. Before, the
  roster served the point estimate with its band stripped, which the model card lists as an
  excluded use.
- Tests: `backend/tests/unit/test_impact_reference.py`.
- Wording corrected where TEI was called "per-100": the served `method` string in
  `services/intelligence.py`, the column comment, and the module docstring.

### Frontend

**`lib/impact.ts`** places a served TEI in that population:

- **League impact percentile:** a mid-rank percentile, where tied players count as half.
  It is strictly between 0 and 100 and is never displayed as 0 or 100.
- **Competition rank**, shown as "T-#11" when tied. Tied players share a rank.
- **Likely range:** the 10–90% band placed in the same league.
- **Position descriptors** state plain fractions: "Top 5%", "Upper half", and so on. They
  are not tiers like "star" or "starter".
- Missing estimates return `null` and render as **unscored**, never as 0 or as the median.
- Tests: `frontend/tests/unit/impact.test.ts`.

**`components/impact.tsx`** draws the league as a histogram, the player as a marker, and the
band as a bracket. The raw index always stays visible in small print with its provenance.

**Three concepts are kept separate:**

1. **On-court impact.** The league percentile and rank above.
2. **Team-conditional fit.** From `/intelligence/fit` (`components/fit.tsx`). It runs
   0–100, where 50 means the move changes nothing. It shows direction, not size, and it can
   be withheld; the real reason comes from `detail.unavailable`.
3. **Projected trade impact.** The change in wins from `/trades/evaluate`, always shown
   with its p10–p90 band. Every team in a deal is plotted on one shared axis
   (`OutcomeLedger` in the Trade Evaluator). Player indices are **never summed** into a
   "net impact", because the projection already weights players by minutes and a raw sum
   would not.

## 4. Modeling issues for the next iteration (not addressed here)

1. **Rank precision.** Below the top group, neighbouring ranks are statistically
   indistinguishable. Options:
   - Publish rank intervals by simulating from each player's σ.
   - Coarsen the displayed rank outside the top ~50.
2. **The σ model may be optimistic for veterans.** It was fitted per single-season minutes
   but is applied to summed window minutes (`impact.py` around line 242). Refit it on window
   minutes, or apply it per season.
3. **The defense sub-index is noisy.** It has three terms, so it swings widely (for example,
   Wembanyama +6.64 defense against +3.98 overall), and offense + defense ≠ TEI because PIE
   sits in neither. Either document the sub-indices as non-additive in the UI, or rebuild
   them as an additive decomposition.
4. **Players are served on stale seasons.** Seven players with 0 games in 2025-26
   (Haliburton, Lillard, Irving, VanVleet, Niang, Exum, Rozier) are still scored and ranked
   from older seasons. Consider an availability-adjusted or "last observed" flag on the
   reference population.
5. **The conversion coefficient is not drawn in simulation.** The 14.977 TEI → net-rating
   coefficient has SE 1.53 (about 10%), but it is a constant in the Monte Carlo, so win
   bands understate conversion uncertainty. The `tei_to_net_rating` registry row is also
   not read at serve time; the constant is imported.
6. **The team-projection doc has drifted.** `docs/model-card-team-projection.md` claims
   "±15% noise" on the wins slope. The code uses slope_se ≈ 0.053 (about 2.4%).
7. **Precedent `tei` is a different quantity.** It is a single-season, within-season z
   score. It shares the name and must never be placed in the served population. The UI
   keeps it as a raw index.
8. **Validation is thin.** It rests on one validation transition (n = 464) and three
   ingested seasons. The model card says to retrain weekly; the active model was trained
   2026-08-25 on data that is now about 53 days stale.
9. **Team strengths ignore direction.** The strength rule in `analytics/needs.py` and
   `domain/needs.py` is "severity 0 and raw percentile ≥ 65". It ignores direction for the
   statistics where lower is better (`DEF_RATING`, `TM_TOV_PCT`). An elite defence or
   ball-security team can therefore never be listed as a strength. Boston's defence, for
   example, sits around the 90th league position but has a raw 10th percentile.

   The Team Outlook page now plots direction-corrected positions and footnotes the affected
   rows. The server should apply direction itself and expose a `lower_is_better` flag, which
   would remove the client's inference from the (severity, percentile) pair.
10. **Acquisition targets carry no band.** `projected_delta_wins` on acquisition targets is a
    point estimate with no p10–p90. The panel says so and sends the reader to the Trade
    Evaluator, which does serve the band.
11. **The e2e league differs.** The synthetic demo league's distribution (std 0.63, no
   unscored players) does not resemble the real one. Do not tune visual thresholds on it.
