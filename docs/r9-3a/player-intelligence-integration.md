# Player intelligence integration — how Player Explorer uses the R9.2 service

R9.2 built `PlayerIntelligenceService`; nothing consumed it. R9.3A makes Player Explorer its first consumer, **without** computing, caching, or re-deriving anything in the
browser, and without weakening a single R9.2 safeguard.

## 1. The rule

> The backend decides what may be served and on what terms. The browser decides only the words — and is built so the wrong words are unreachable.

* Player Explorer calls `GET /players/{id}/intelligence` (the shared service route) and `GET /players/{id}/shot-zones`, passing the page's own **performance season** as the cutoff (`as_of`), so the evidence
  agrees with the header and the TEI card instead of following "the latest ingested season". No React component computes a statistic.
* **Read-only.** A request never trains, materialises or publishes. Inferred and projected values are read from the stored candidate estimates that
  `make materialise-skills` wrote; a cutoff with no stored model is *unavailable*, never computed on the fly.
* The route is `404` unless `PLAYER_INTELLIGENCE_PREVIEW=true`. With it off the Player Explorer hides the two panels (a 404 is "not enabled here", not an error). The isolated
  preview turns it on deliberately; no default changes.

## 2. What Player Explorer shows (Advanced tab; the Overview and Contract tabs carry injury and contract)

| Layer | Source | Always shown with |
| --- | --- | --- |
| **Observed performance** | `kinds=measured`: true shooting, 3P%, FT%, assist rate, assist-to-turnover, rebound rates, usage, minutes, games | the named season and season type, makes/attempts (or the bounded range when counts are reconstructed) or the sample it rests on, a "thin season" flag when the season has < 20 games or < 300 minutes, a "few attempts" flag below 30, the server's own per-record notes (two-team totals, unstable shares in few minutes), source, data snapshot id and retrieval time |
| **Research preview — experimental estimates** | `kinds=inferred,projected&include_experimental=true`, **fetched only when the reader opens it** | the word *experimental*, model name and version, evaluation status in words, interval (kind and level), **how much of an inferred value is the player's own record versus the league curve** (the record's `reliability.interpretation`), the record's own reliability sentence, and the condition a projection holds under |
| **Shot profile** | BALLDONTLIE `shooting/by_zone` via `/shot-zones` | season, units, per-zone share and FG% against the league, "thin" flags, unit reconciliation, limits |
| **Unavailable dimensions** | the service's `unavailable` list | the reason, verbatim (e.g. "usage is a role a coach assigns, not an ability") |

### Experimental gating — three independent barriers

1. **Server:** `serving_decision` returns a candidate model's output only with `include_experimental=true`, never for a `rejected`/`retired` model, never when evaluation `failed`. The route
   defaults to measured-only.
2. **Request:** the browser asks for experimental kinds only after an explicit click ("Show experimental estimates"); the panel is closed on load.
3. **Words:** `lib/skillEvidence.ts::evidenceTier` returns `validated` **only** for a record whose own model fields say `status = active` *and* `evaluation_status = holdout_confirmed`; every
   other inferred or projected record renders as *experimental*. Nothing in R9.2 meets the bar (promotion is manual), a modelled record with no model never qualifies, and
   `tests/unit/skill-evidence.test.ts` fails if a candidate with a confirmed holdout reads as validated.

### Honest wording rules (each backed by a record field or a test)

* An **inferred** value is "an estimate of underlying ability at the end of *season*" with a *credible* range — not an observed rate.
* A **projected** value always carries "conditional on the player playing a rotation role" (the record's own `interval.interpretation`). It is not a forecast of total contribution and says
  nothing about whether he will play. The R9.2 study showed participation is forecastable but unvalidated, so no availability-adjusted number is shown.
* **True shooting** shows the R9.2 outcome unchanged: the improvement was *not confirmed*; the served method is a benchmark and the card says so ("a benchmark method, not a claim of skill").
* The model's **evaluation status** is translated, not hidden: `development_only` → "development evidence only — not confirmed on held-out data"; `holdout_confirmed` → "confirmed on a
  held-out test, still a candidate".
* **TEI is untouched.** The Player Explorer's existing TEI card and its relabelled "uncalibrated" band are unchanged, and nothing here replaces or feeds it.
* Defence, shot-location *impact*, and an overall rating are listed as not measured; descriptive tracking figures are never presented as causal impact.

## 3. Shot profile — descriptive, and unit-checked at read time

BALLDONTLIE's shot-zone figures were stored in R8.3 with `aggregation = unspecified` (unreconciled). `app/services/shot_zones.py` reconciles the unit **per player, per request**: the six
non-backcourt zones' attempts per game plus backcourt must equal the season's per-game field-goal attempts (`general/base`, verified against NBA.com in R8.3) within 0.4. If they do not,
the zones are **withheld with the reason** — never shown on an unverified unit. Measured on the preview data: **522 of 570 players reconcile; 48 are withheld** (the cause is not established: 44 of the withheld
are single-team players, and 69 of 72 multi-team players reconcile, so it is not simply a multi-team artefact). The tolerance (0.4 per game) is the theoretical maximum of the provider's
tenth-rounding, compared in tenths; the observed gaps are skewed positive (mean +0.09), which independent rounding would not produce, so the check proves the *unit* is attempts per game,
not that every individual zone count is exact — the panel says so.

Figures are per game, rounded to a tenth, so a zone with fewer than 25 approximate season attempts is flagged "thin" and its percentage greyed. The league line is Σ makes / Σ attempts across
every player with a row, weighted by games. The panel states that it is descriptive: role, teammates and scheme choose shots as much as ability does.

## 4. Provenance and shared records

Every record carries `provenance.sources`, `data_snapshot_id` (a hash of the data it was computed from), `code_commit` and, for models, `evaluation_ref` (the experiment id). The "How this was
computed" drawer shows them. Because versions are hash-named and append-only, two tools that show the same version and snapshot id showed the same number; R9.3B can pin them in a link
(see `connected-workflows.md` §4).

## 5. What the materialised preview holds

`make materialise-skills` was run on the **isolated preview copy only** (origin 2025-26): four candidate versions (`fg3_pct` state-space, `ft_pct` state-space with role, `ts_pct` `ols_history`
benchmark and `state_space_role`), 632 players each, `status = candidate`, `is_active = 0`. The active TEI model (`20260825-0c89cb6a46a8`, 512 estimates) and its history are byte-identical to the
owner's database (checked by row fingerprint; see `qa-and-regression-report.md`).

## 6. Not done (deliberately)

No new statistic; no availability model; no change to the Trade Evaluator's deal-level bands (R9.2 carry-over: they inherit the TEI band's under-coverage, still labelled uncalibrated); no promotion;
no team-level aggregation of projections; no use of GOAT advanced statistics as a model input.
