# Recommendations after R9.3A: R9.3B, then R10

**Short answer.** Do **R9.3B (interconnected experience)** next, preceded by a small **contract-completeness sprint** that costs no new engineering, and keep historical GOAT coverage and R10 behind it.
The reason is what R9.3A measured, not a preference: the two things that most limit what Pivot can *conclude* today are (a) no team has a complete payroll, and (b) the Trade Evaluator's
deal-level uncertainty is still the uncalibrated TEI band. Neither is solved by a new model; both are solved by finishing what the foundation already contains.

## 0. Do first (days, not weeks; no new modelling)

1. **Spot-check 10 contracts** against a source you trust (15 minutes). It is the only check on any *individual* amount and the gate R8.3 set for league-wide cap views.
2. **Classify the 92 unpriced rostered players.** 53 have provider cash equal to the sourced two-way salary (a hint, never assumed). A labelled source — the NBA transactions ledger or a roster-type feed
   — turns "probably non-counting" into a fact. If they are non-counting, cap-relevant coverage rises from 82.5 % to ~92 % and several teams become complete; after the 15-man cut-down the
   preseason camp noise disappears too. This is the single highest-value data task because it unlocks verified payroll, cap space and apron position (currently withheld for all 30 teams).
3. **Link *Yang Hansen*** (provider id 1057275769) by hand — the name-order inversion the crosswalk correctly refused.
4. **Refresh 2026-27** after the cut-down (30 requests) so the horizon, roster and cap hits are from the same week.
5. Decide on **activation** (`local-preview-and-activation.md` §4). Until the data is in the primary database none of this is in your daily tool.

## 1. R9.3B — deeper connected player investigation (recommended next)

What R9.3A built is the first link. What it did not build, in order of value:

| Item | Why it is next | Contract it relies on |
| --- | --- | --- |
| **`InvestigationContext` as one backend object** (player, `as_of`, quantities, **pinned model versions**, scenario id) | A shared link must reproduce the same experimental numbers; versions are hash-named and append-only, so pinning is cheap and makes "two tools showed the same number" provable | the four URL fields shipped in R9.3A are its first members (`connected-workflows.md` §4) |
| **Batch route** `POST /player-intelligence/batch` over a roster / a trade's assets | Team Outlook and the Trade Evaluator need many players at one cutoff without N requests; R9.2 measured 146 statements for a 15-player roster | `PlayerIntelligenceService.get_many` |
| **Availability model** (pre-registered, fresh holdout) | Every projection is *conditional on playing*; Team Outlook and the Trade Evaluator are the tools most likely to mistake that for a total. R9.2 showed participation is forecastable (AUC 0.92) but unvalidated | R9.2 availability study |
| **Calibrate the Trade Evaluator's deal bands** on R9.1's held-out residuals (or label them as the player band is labelled) | R9.2 carry-over #8: the deal-level 10–90 % bands inherit the TEI band's 57–61 % coverage for a nominal 80 %. R9.3A left them untouched and labelled | R9.1 backtest residuals |
| **Strategy Lab and Team Outlook consume the same context** | the point of "one environment" | the shared financial service (`financials.py`) is already the single read path for money |
| **Ask Pivot grounding** from record qualifiers (`reliability_statement`, `conditioning`, `limitations`) and the assessment's unchecked list | an answer about legality or an ability must quote the same qualifiers the UI shows | R9.2 recommendation §2.3 |

Not in R9.3B: any new statistic (a new dimension is a new pre-registered R9.x), promotion of the shooting projections (a reviewed decision per `model-safety-and-promotion.md` §4), a generalized workspace system.

## 2. Historical GOAT identity mapping (parallel, bounded)

R9.2 deferred the ~260-request advanced-statistics backfill because the crosswalk covers **active** players only (survivor bias: it would silently train on players who stayed in the league).
Two prerequisites, neither needing new subscriptions: a **historical identity pass** (link retired/inactive players from the biographies embedded in stat rows, as `link_embedded_players` already
does for active ones) and an **insert-only snapshot history** (so a provider restating a figure does not overwrite what a model was trained on). Only then is defence, zone and tracking
modelling honest. R9.3A added a descriptive shot-zone panel on the *current* season only for exactly this reason.

## 3. Advanced player intelligence coverage

Defence, shot-location *impact* and an overall rating remain explicitly unavailable. The attempts-aware regression baseline and the other R9.2 carry-overs (volume in the ability prior,
model-based sampling variance, role covariates, a league-environment term, exact measured counts where `totals` exist) should land *before* any shooting candidate is re-confirmed for promotion.

## 4. R10 — validated player archetypes (after R9.3B's context object)

Cluster on the low-noise dimensions R9.2 identified (usage, rebounding, playmaking) with **membership uncertainty**, and treat `usg_pct` as role, not skill. R9.3A's shot-zone distribution is a natural
*descriptive* feature (where a player takes his shots) but must enter as description, not as an impact estimate. Validation first (stability under perturbation, held-out recoverability, agreement with
the existing rule-based role descriptor), labels second.

## 5. R11 → R14, in order, and why not earlier

* **R11 roster-needs intelligence** needs R10 archetypes plus *complete* payroll/roster-type data (cap-aware needs).
* **R12 explicit player–team fit** (counterfactual) needs R11 and the availability model; it is the first place a causal claim appears and must be pre-registered.
* **R13 comparable trades** reuses the existing comparables service; its value rises with R12.
* **R14 transaction optimizer** is last by design: it needs verified legality inputs (options, guarantees, no-trade clauses, signing dates, exceptions) that no current provider supplies in structured form. Pivot's philosophy —
  evaluate the decision the user is considering rather than choose one — means this should assist a search the analyst steers, not replace it.

## 6. What would change this recommendation

If the 10-contract spot-check **fails** (the provider's cap hits are wrong), stop and fix data trust before any of the above. If the owner decides the hosted/shared deployment matters sooner than analytical
depth, the provider-licensing question (open since R8.3) moves to the front.
