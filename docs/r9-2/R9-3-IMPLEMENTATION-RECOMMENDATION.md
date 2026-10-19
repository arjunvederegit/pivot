# R9.3 — Connected Player Investigation: implementation recommendation

Not implemented in R9.2. This is the plan that follows from what R9.2 built and what it found. Every item names the backend contract it relies on.

## 1. What R9.2 leaves R9.3 to build on

* **One retrieval path.** `PlayerIntelligenceService.get / get_many / catalog` returns typed records that already carry the context a connected
  investigation must preserve: canonical player id, `period` (season, season type, information cutoff), `target_period` and `conditioning`
  (projections), `sample`, `model` (name, version, status, evaluation status and reference), `provenance` (sources, snapshot id, code commit,
  reproducibility), `assumptions`, `limitations`, `interval` with its kind and calibration, and `reliability`.
* **Honest refusals as data** (`Unavailable`), so a tool can show "Pivot cannot see this" rather than an empty cell.
* **A preview route, off by default** (`PLAYER_INTELLIGENCE_PREVIEW`): `GET /players/{id}/intelligence`, `GET /player-intelligence/catalog`.
* **What is and is not trustworthy today:** measured values for eleven quantities; three-point and free-throw *projections* confirmed on an
  unread holdout (experimental); ability estimates (experimental, `development_only`); true-shooting projection = a benchmark; defence, shot
  profile and availability models **unavailable**; the Trade Evaluator's deal-level 10–90% bands inherit the TEI band's under-coverage and
  have **not** been corrected (R9.2 corrected only the player-level label).

## 2. The first connected workflow (highest value): Player Explorer → investigate → carry the context

**Player Explorer → "Investigate" panel → Team Outlook / Trade Evaluator / Strategy Lab / Ask Pivot**, in that order of delivery, because each step
reuses the previous step's contract and none needs a new statistic.

### 2.1 A shared investigation context (the thing that must not be lost between tools)

```
InvestigationContext {
  player_id            canonical Pivot id (never a provider id)
  as_of                information cutoff (season label)   ← the selected statistical period
  season_type          regular (the only type the engine reads today)
  roster_season / team_id    where he plays now, and where the stat row says he played then
  quantities[]         what the user is investigating
  kinds[]              measured | inferred | projected
  include_experimental bool — and the UI must keep showing the "experimental" label whenever true
  model_versions{ quantity → version }   pinned, so a shared link reproduces the numbers
  scenario_id?         the Strategy Lab / Trade Evaluator scenario this investigation belongs to
  evidence_refs[]      experiment ids from the records' ModelRef.evaluation_ref
}
```

Carried in the URL (`?player=…&as_of=…&q=…&mv=…`) and returned by the backend as one object so no tool reconstructs it. **Pinning model versions
is the point:** R9.2's versions are hash-named and append-only, so a link opened next month shows the same numbers, and a newer model shows up as
a new version the user can switch to deliberately.

### 2.2 Backend work (small, all on top of R9.2)

1. `GET /player-intelligence/context/{player_id}` → `InvestigationContext` defaults + the catalog slice that applies (what is offered for *this* player at *this* cutoff, including the `Unavailable` list).
2. `POST /player-intelligence/batch` (a roster, a trade's assets): wraps `get_many`; resolves one shared cutoff.
3. A **versioned response schema** (`schema_version`) and generated TypeScript types from the record models, so the frontend cannot drift from the contract.
4. Cache keyed by `(data fingerprint, as_of, model versions)`; the stored-model reads are cheap, the measured path re-reconstructs counts per request (≈ 10 rows per player).
5. Materialise on activation (`make materialise-skills` on the 2025-26 cutoff, after the owner's activation checkpoint) so inferred/projected exist for the current season.
6. Fix the Trade Evaluator's deal bands (same under-coverage as the TEI band): either calibrate on R9.1's held-out residuals or label them as R9.2 labelled the player-level band.

### 2.3 Per-tool plan

| Tool | What it consumes | What it must show / preserve |
| --- | --- | --- |
| **Player Explorer** | `get` for the selected player and period; catalog for what exists | measured value **with attempts and sample**; for shooting, ability estimate with credible interval **and reliability sentence**; the "conditional on playing" line on any projection; model version, evaluation status, data snapshot behind a "How this was computed" drawer; unavailable dimensions listed by name |
| **Team Outlook** | `get_many` over the roster at one cutoff | aggregates must carry uncertainty (R9.1's cluster bootstrap is the pattern) and **must not present conditional forecasts as team totals**: pair every shooting projection with participation (availability). Do not average percentiles; aggregate counts (makes/attempts) |
| **Roster Intelligence** | measured + ability vectors with reliabilities | feed R10/R11: cluster on low-noise dimensions (usage, rebounding, playmaking) with membership uncertainty; treat `usg_pct` as role, never skill |
| **Trade Evaluator** | the same records for each asset, at the trade's information cutoff (`as_of`) | **as-of retrieval is the guard against hindsight**: a trade is described by what was known then; show the experimental label; do not let the engine's numbers silently replace TEI in a verdict |
| **Strategy Lab** | scenario assumptions as explicit inputs | the cutoff, the condition ("plays ≥ 500 minutes") and the model version are scenario parameters, not hidden defaults |
| **Ask Pivot** | `reliability_statement()`, `limitations`, `conditioning` verbatim | grounded claims must quote the record's own qualifiers; a claim about an ability must say "estimate"; refuse to answer from an `Unavailable` quantity |

### 2.4 Preserving analytical continuity

* Every number a tool shows carries `model.version` + `provenance.data_snapshot_id`; moving between tools passes them along, so two tools can be
  proven to have shown the same number.
* One `serving_decision`. Tools never decide what is "good enough" to show; they ask the service.
* Legacy percentile skills (`IntelligenceService`) keep working; migrate consumers one at a time, shooting first (it is the only dimension with an engine-grade estimate).

## 3. Prerequisites R9.3 should not skip

1. **Availability model** (pre-registered, mean-forecast scoring, fresh holdout) — R9.2's study shows participation is forecastable (AUC 0.92) but unvalidated; Team Outlook and Trade Evaluator are the tools most likely to mistake a conditional forecast for a total.
2. **PostgreSQL on a copy of the owner's real database** — R9.2 verified the migration chain and candidate training on a PostgreSQL copy of the real data; the activation checkpoint (R8.1/R8.3/R9.1 migrations on the real file) is still the owner's step.
3. **A reviewed promotion decision** for the two shooting projections (see model-safety-and-promotion.md §4); until then R9.3 UI must keep the *experimental* label.
4. **Historical identity + insert-only snapshot history** if defence/zone work is to continue ([goat-intelligence-assessment.md](goat-intelligence-assessment.md) §4).

## 4. Acceptance for R9.3 (proposal)

* Opening a player from any tool preserves player, period, scenario and model versions; a shared link reproduces the numbers byte-for-byte.
* Every shown inferred or projected value displays its kind, interval kind, reliability or conditioning, model version and evaluation status.
* No frontend code computes a statistic; no tool shows a candidate output without the experimental label.
* Existing tools' behaviour is unchanged unless a documented decision says otherwise; the three validation batteries are identical.

## 5. Risks

* **Over-reading experimental values.** Mitigation: labels carried by the record, not the UI; tests that fail if a candidate is shown unlabelled.
* **Reliability misread as probability.** Mitigation: use `reliability_statement()` text, never a bare percentage.
* **Cost of recomputation.** Mitigation: cache by fingerprint; batch route.
* **Scope creep into new statistics.** R9.3 connects what exists; a new dimension is a new R9.x with its own pre-registration.

## Carry-overs from the R9.2 independent reviews (modelling and engineering items deliberately not done in R9.2)

1. **Reference baseline.** Pre-register an attempts-aware regression (`ols_eb` / `ols_attempts` style, see `results/baseline_challenge.json`) as the reference, not `ols_history`; re-confirm any shooting candidate against it on a fresh holdout.
2. **Volume in the ability prior.** Let attempts-per-minute shift the prior mean (coaches let good shooters shoot); this is the root cause of the over-prediction for rare shooters that the 0.30 reliability floor only masks.
3. **Sampling variance from the model, not the observed rate** (iterate the plug-in), which removes the free-throw level bias seen in the fixed-variance diagnostics.
4. **Role covariates:** carry each returner's last observed role instead of the cutoff-season role / league average.
5. **League-environment term** in the mean curves (the TS and FT intercepts drift upward across folds).
6. **Measured counts:** where exact `totals` rows exist, use them instead of reconstruction; keep the range for seasons that have none.
7. **Query efficiency:** batch the per-(player, quantity, kind) estimate lookups in `get_many` (146 statements for a 15-player roster today).
8. **Trade Evaluator σ** derived from the TEI band is ≈ 1.5× too narrow (band coverage 57–61% for a nominal 80%): recalibrate on R9.1's held-out residuals.
