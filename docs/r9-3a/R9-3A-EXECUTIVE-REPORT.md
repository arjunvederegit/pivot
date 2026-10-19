# R9.3A — GOAT operationalization and existing-tool integration: executive report

**Status: Complete for the isolated preview; activation on your primary development database is prepared, rehearsed on a disposable copy of it, and deliberately not performed.**
It is a product-integration release: no new page, no new statistic, no new model, no new migration. Branch `feat/r9-3a-goat-operational-tools` (from R9.2 `1c7e9bb`), pushed, not merged.

## What you can do now

```bash
cd /Users/arjunvedere/pivot-r9-3a-worktree && make preview-up      # Cap Center http://localhost:3093/salary-cap-center
```

1. **Cap Center** — pick a team and see its 2026-27 cap hits, contract length (`4 seasons · through 2029-30`, or `5+ · through at least 2030-31`), commitments through 2030-31, the players Pivot cannot price (with the provider's own figures and
   why), and an explicit "what this page can say / what it withholds" panel. A partial team shows a **Known salary subtotal (≥ $193.5M, 15 of 18 priced)**, never a payroll; apron position, cap space and the tax bill are withheld with reasons.
2. **Trade Evaluator** — build a real deal and get verified cap hit / base salary / cash and contract years per player, a salary comparison per team (net change only when every salary is known; "unknown" otherwise), commitments by season,
   and a legality **assessment** that says *prohibited under a named rule*, *indeterminate*, or *passes the rules Pivot checks* — with what passed, what could not be verified and why, and nine-plus CBA restriction categories Pivot does not check at all.
   The existing basketball evaluation is unchanged.
3. **Player Explorer** — Advanced tab: measured performance for the named season with its sample, a shot-zone profile (unit-reconciled or withheld), and a closed-by-default **research preview** of R9.2 experimental estimates (labelled
   experimental, model version, evaluation status in words, interval, how much is the player's own record, the condition a projection holds under). Overview: a dated injury status. Contract tab: the same record Cap Center and the Trade Evaluator read.
4. **Connected** — "Evaluate *Name* in a trade" carries the canonical player id, team and statistics season into the Trade Evaluator, which *verifies* it against its own roster and places him when you pick the partner team; every traded player links back to his file, and Back restores the deal.

## What is true about the data (recomputed, not copied)

| | |
| --- | --- |
| Rostered players (2026-27 provisional preseason snapshot) | 526 across 30 teams |
| With a 2026-27 cap hit | **434 (82.5 %)**; team range 76–89 % |
| Teams with a complete payroll | **0 of 30** (so payroll / cap space / apron are withheld for every team) |
| Not priced | 92 = 53 cash-equals-two-way-salary (hint only) + 7 other provider rows without a cap hit + 32 no contract row |
| Contract years loaded league-wide | 2026-27 → 2030-31 (453 / 334 / 198 / 98 / 19) |
| New GOAT requests this session | **139** (120 future-season contract loads + 19 length-validation probes); cap ≈150; no 429/5xx |
| Contract-length check vs the provider's own aggregate | 18 of 19 exact; 1 correctly flagged "at least"; 0 disagreements |
| Injuries on file | 92 (28 Out, 64 Day-To-Day), retrieved 2026-10-19 03:13 UTC; no report date from the provider |
| Shot zones | 522 of 570 players reconcile to their field-goal attempts; 48 withheld |
| R9.2 candidates materialised (preview copy only) | 4 versions × 632 players, all `candidate`, none promoted |

## What changed in the engine (each tested; see `financial-intelligence.md` §4)

* A team that ends at or under the cap needs no salary matching (removes a **false failure**).
* "Not a two-way contract" is **deduced** from a cap hit above the sourced two-way salary ($678,882) so matching can run; any failure that rests on that deduction, or on a payroll floor, is reported as an **indication, never a verified failure**.
* The verified-illegal gate that suppresses the basketball evaluation is **unchanged**: 300 random real-data trades have identical statuses in the R9.2 engine and this one, and the acquisition / adversarial / comparable validation batteries are **byte-identical** to R9.2 on the same data.

## What independent review found, and what it changed

Four structured review rounds by fresh agents (financial correctness, analytical integrity, frontend, adversarial end-to-end; final: backend 3,327 passed / 0 failed, frontend 345 passed, PostgreSQL subset 193 passed) plus mutation testing (35 own planted defects plus 44 by the round-4 reviewer; every one that would mislead a user is now caught). The headline defect, found by two reviewers independently,
was that the new two-way deduction would have made a floor-based second-apron failure reachable and **newly suppressed the basketball evaluation on ~5 % of real trades** — contradicting the release's own invariant; fixed and pinned. The rest (ingestion guards, honest freshness,
gap handling, phantom movers, one-contract-per-player, wording and date handling) are listed with evidence in `qa-and-regression-report.md`.

## Remaining limitations (what still prevents stronger conclusions)

No complete payroll for any team (needs two-way / Exhibit 10 / camp classification from a labelled source); no options, guarantees, no-trade clauses or signing dates (so recently-signed, consent and several matching checks stay unverified); the roster is provisional until the cut-down;
no second source has verified any individual contract amount; *Hansen Yang* is unlinked; trade-deal uncertainty bands are still the uncalibrated TEI band; the R9.2 projections are experimental and conditional on playing; provider licensing for a shared deployment is unresolved.

## Activation

Not done. `local-preview-and-activation.md` §4 is the reviewed procedure (backup → rehearse → migrate → dry run → apply → verify), with the rehearsal evidence from a copy of your real database.

## Completion criteria

See `FINAL-QA-SIGNOFF.md` — each acceptance criterion with its evidence and state.
