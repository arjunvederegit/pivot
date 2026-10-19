# QA and regression report

How R9.3A was verified. Reviews were done by **fresh AI reviewer agents** with no prior context who read the real source, ran the real tests and probed the running application against real data; there was **no human QA**. Reviewers were told to find defects, not to approve, and
were forbidden from modifying the repository, calling the provider or reading credentials. Every finding below was reproduced by its reviewer; every fix has a regression test; every claim of "fixed" was re-checked.

## 1. Before the reviews: self-checks that shaped the design

* **Real-data rehearsal of the activation** on a disposable copy of your real database (`local-preview-and-activation.md` §4.1). It found a real bug (a second SQLite connection inside the write transaction → "database is locked" at 71 MB) that every small fixture had hidden; fixed with a call-shape regression test.
* **Validation batteries, R9.2 vs R9.3A on identical data**: `adversarial-validation`, `acquisition-validation`, `comparable-validation` — outputs **byte-identical** (timestamps masked) after the round-2 fix. The first comparison *was not* identical (three teams' acquisition-rejection counts moved by 1–2) — that difference was the early warning for the round-2 headline defect.
* **Performance**: R9.2 baseline vs R9.3A on the same database (median ms): roster 11 → 13, cap-outlook 4 → 7, player contract 1 → 3, validate 11 → 15, evaluate 28 → 31, generate 422 → 494. Query budgets are pinned (`test_query_budget.py`, `TestQueryBudgets`); the cap-outlook N+1 (one query per contract) was found by a new budget test and fixed.
* **Controlled defects (mutation testing)** of the new financial logic: 35 planted defects (a one-season horizon claims a length; a floor labelled "Payroll"; unknown salary counted as zero; "legal" worded for a pass; a partial read in the horizon; an old injury read as current; an unreconciled shot-zone unit shown; …). 33 were caught; 2 are equivalent mutants (a redundant guard / arithmetic identical when the guard holds). Three initially survived and each gained a test.

## 2. Round 1 — data integrity and financial correctness (independent reviewer)

Attempted to make Pivot produce an incorrect financial conclusion. **1 high, 5 medium, 5 low.**

| ID | Sev | Finding | Resolution |
| --- | --- | --- | --- |
| F1 | high | the new two-way deduction made a floor-based second-apron failure reachable → a verified failure that suppresses the basketball evaluation (GSW/CHA example: scored at R9.2, suppressed on HEAD) | A failure resting on a deduced type is an `unavailable` **indication** (`_indication_if_deduced`); 300 random real-data trades now have identical statuses to R9.2; the roster-count downgrade that also moved the gate was **reverted** and replaced by a lowered-confidence qualifier |
| F2 | med | "complete" season / "ends" length not sound: rejected rows, empty responses and later unpriced rows were invisible | job returns `partial` on any rejected/refused row or empty season; partial seasons never count in the horizon; a later unpriced season makes the length unknown |
| F3 | med | no sanity checks on what the job writes (season mismatch stored as 2026-27; zero/negative cap hits priced; duplicate rows last-wins; non-cap-hit basis shown as "cap-hit basis") | all refused and listed (`refused_rows`); UI basis label comes from the row |
| F4 | med | cap-room pass `high` confidence; cap holds and dead money unmentioned | pass is `medium`; "Cap holds and dead money" added to the unchecked list and the not-on-file list; position carries a caveat |
| F5 | med | freshness showed another season's retrieval time | an unpriced record has none; a team shows its oldest priced figure |
| F6 | low-med | gaps and horizon limits counted as known $0 in commitments | only "contract ended" is a known zero |
| F7 | low-med | duplicate `Contract` rows handled inconsistently (chart summed both) | lowest id everywhere; cap-outlook counts one; **activation refuses** to create a second |
| F8 | low | a free agent named in a deal, or an empty roster, manufactured a verified after-trade payroll | only on-roster players come off a team's books; no roster → no after-trade coverage |
| F9 | low | legacy `players_with_contracts` disagreed with the financials block | equals the priced count; chart and columns use the financial-season-onward commitments |
| F10–F11 | low | misleading unpriced reasons (two-way hint beside later standard cap hits), gap wording, a tiny net rounding to "$0.0M" | reasons nuanced; "with a gap"; "<$0.1M" |

Reviewer's *verified-OK* list (could not break): unknown ≠ $0 in side totals, net, ratio and subtotal-after (hand-checked live arithmetic, 3-team, picks-only, empty, same-player-both-sides); floor-based matching indications sound in both directions; cap-room math; the deduction is one-directional (minimum priced 2026-27 cap hit is $1,357,763, above the $678,882 two-way figure); `verified_legal` unreachable on this data; thresholds per season; base/cash never promoted to cap hit.

## 3. Round 2 — analytical integrity and model safety (independent reviewer)

Attempted to expose experimental estimates as approved results. **Could not.** Scanned 573 players × {ts, fg3, ft} × {inferred, projected} with experimental on: only `candidate`, never `active`, never validated; tried every `serving_decision` combination; `evidenceTier` fail-safe for a missing model, unknown kind, differently-cased status and a candidate with a confirmed holdout; no R9.3A code path reads or writes TEI; the activation tool cannot overwrite a serving model.
Defects found (1 high — the F1 above, found independently — and 6 medium, 5 low), all fixed:

| Finding | Resolution |
| --- | --- |
| high: new evaluation suppression (as F1) | fixed + pinned (`TestADeducedTypeNeverCreatesAVerifiedFailure`) |
| Observed panel dropped the server's per-record limitations (two-team totals, unstable shares, recomputed-vs-published TS, bounded counts) | notes disclosure per panel; bounded counts shown as a range; "few attempts" flag |
| Inferred rows hid how much is the player's own record | the record's `reliability.interpretation` shown on every inferred row (the type was wrong in the first draft: it is an object) |
| "confirmed on a held-out test" overstated the small 3P/FT effect | wording now R9.2's own: passed the pre-registered check — a small effect, not a claim of certainty; still a candidate |
| Injury return date shown a day early with an invented time; `return_date_passed` judged on the UTC date | date-only formatter; league-timezone "today" |
| Default cutoff = "latest ingested season", not the page's performance season; false "rookie" message | page passes its performance season as `as_of`; the server's own unavailable reason is shown |
| Shot-zone tolerance hid a systematic positive skew; float boundary rejected valid rows; doc blamed multi-team seasons wrongly | rounding in tenths; limitation stated (mean gap +0.09; the unit is verified, not each zone); doc corrected (44 of 48 withheld are single-team) |
| `injury_status` 500 on a NULL retrieval time; readiness statics | guarded; computed from data |

## 4. Round 3 — frontend and user experience

__ROUND3__

## 5. Round 4 — adversarial end-to-end

__ROUND4__

## 6. Round 5 — release-candidate verification

__ROUND5__

## 7. Browser verification at 100 % zoom (author)

Run against the live preview on real data at 1440×1000 (CSS pixels = 100 % zoom), plus 390×844:

* **A — player investigation:** Jayson Tatum → Advanced (observed 2025-26: 16 games flagged "thin season", TS 54.1 %, 3P 32.9 % = 47 of 143, FT 82.3 % = 65 of 79) → research preview opened on request (experimental, `cand-state_space-fg3_pct-…`, 80 % credible 34.9–37.8 %) → "Evaluate in a trade" → Trade Evaluator opened on BOS with the carried-player notice naming the season difference.
* **B — trade finances:** ATL↔BOS (Jalen Johnson $30.0M ↔ Jayson Tatum $58.5M): net +$28.5M / −$28.5M, ratios 195 % / 51 %, subtotals shown as `≥` floors, commitments through 2029-30, verdict *Indeterminate — the known salaries alone already indicate a salary matching problem for ATL (not verified)*. With Jalen Wilson (no contract) the net is "not stated".
* **C/D — Cap Center / partial coverage:** ATL 15 of 18 priced, "Known salary subtotal ≥ $193.5M", three unpriced players with reasons, thresholds for 2026-27 only; no payroll, cap space or apron claim.
* **E — cross-tool:** player, team, season, cap hit and source agree across the three tools (`test_the_three_surfaces_agree_on_a_salary` and by eye).
* **F — seasons:** 2025-26 statistics are labelled 2025-26 everywhere; the 2026-27 roster is "provisional"; the Contract tab says "Seasons differ".
* Connected flow incl. browser Back restoring a 2-player deal; mobile 390 px no horizontal overflow.
