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

A reviewer drove the running app in the in-app browser at 100 % zoom (1440 px), 768 and 390 px, on real data: all 30 Cap Center teams, two- and three-team deals, the Wilson (no contract) deal, the connected flow, every Player Explorer tab, keyboard/contrast/heading basics, and Home, Team Outlook, Strategy Lab, Data Health and About for regressions. **No critical defects; 13 findings (mostly medium/low).**

| ID | Finding | Resolution |
| --- | --- | --- |
| F1 | the trade lane card called a floor "PAYROLL" and clipped its own caveat | the cell reads **"Known salary"** (and "Payroll" only when every contract is priced) |
| F2 | after a no-contract player moved, payroll read "— not imported" though contracts were imported | falls back to the roster floor; "a salary is unknown" instead of "no salaries" |
| F3 | "BOS's side fails a **verified** rule" named the wrong team and contradicted the lowered-confidence note | reworded: the deal fails an implemented rule for at least one team; a roster-count failure is measured on a snapshot that can be provisional (the gate itself is unchanged by design) |
| F4 | "Back to your trade" depended on browser history | the deal travels in the link (`&deal=`), so Back is a plain link that works in a new tab; history is the fallback |
| F6 | inconsistent thin-season handling; "equivalent to about 1,359 attempts" read as one season | the evidence is stated as pooled across the seasons used; the observed panel's flags are consistent (see round 2) |
| F7 | rule evidence printed `payroll coverage [object Object]` (renderer pre-dates R9.3A) | coverage objects render as "14 of 17 priced (floor)" |
| F9 | the "contract years loaded" tile is league-wide but sat beside a per-team chart | relabelled "League-wide years loaded" |
| F11 | projection sentence capitalisation, `aria-controls` | fixed (the toggle keeps `aria-expanded`) |
| F5, F8, F10, F12, F13 | clipped 3-team lane names (pre-existing), scrolling-table names, a bogus team id silently showing ATL (pre-existing), a conservative "not stated" subtotal, an extra pre-existing intelligence fetch | **not changed** — recorded as limitations |

Exercised and fine (reviewer): all 30 Cap Center teams (no payroll or apron claim anywhere, HOU's floor exactly at the tax line still not called payroll); the Wilson deal never shows $0, net "not stated", verdict *Indeterminate*, footer never says "legal"; arithmetic checked on three-team deals; the experimental endpoint is **not** requested until the toggle is opened (then once, with `include_experimental=true`); every estimate labelled; carried-player notice verified and a valid-player-on-the-wrong-team link gets a "not verified" notice; a `<script>` string in `player=` is dropped; muted-text contrast ≥ 5.6:1; no horizontal overflow at 390 px on any page; Data Health reads "434/526 rostered players priced".

## 5. Round 4 — adversarial end-to-end

A reviewer attacked the integration with 14 scenario families on real data and on controlled copies (incl. provider outages, partial/duplicate/stale ingestion in replay, an old schema, invalid flags, alternative seasons, mixed roster/financial seasons, two-roster players, kill -9 mid-activation, WAL/read-only/symlinked targets) and planted 44 defects. **13 findings (1 high, 7 medium, 5 low); all but two low items fixed, each with a regression test.**

| ID | Sev | Finding | Resolution |
| --- | --- | --- | --- |
| F1 | high | a team answering 200 with nothing (or a re-read that dropped teams) was recorded `complete`, making later-season contracts read "ends early" | a re-read that no longer lists ≥ 3 previously priced contracts is `partial` (one or two waivers tolerated; threshold documented); the horizon already ignores partial seasons |
| F2–F3 | med | the activation dry run missed unique-key collisions; several failure modes (read-only target, locked file, backup dir a file) ended in raw tracebacks and stray backups | dry run checks `uq_provider_entity` / `uq_contract_year` and the other unique keys; every failure is an `ActivationRefused` that names the kept verified backup |
| F4 | med | an estimate attached to an active (serving) model could be copied | estimates are copied only for the candidate models that are copied |
| F5 | low | two databases with no `alembic_version` passed the revision check | refused |
| F6 | med | `overall_status` could say `verified_legal` (only warnings) beside an `indeterminate` assessment | the chip is derived from the stricter assessment; the memo wording is "Passes the rules Pivot checks (not a statement that the trade is legal)" (latent: unreachable on today's data) |
| F7 | med | a database without the R9.1 migration 500'd on cap-outlook/validate (a regression: R9.2 answered 200) | the season basis falls back to "unavailable"; the rest still serves |
| F8 | med | `CAP_LEAGUE_YEAR=banana` produced 500s | validated at startup |
| F9 | med | a roster from another season was summed with this season's cap hits | withheld, with the reason; no "cleared thresholds" claim |
| F10 | low | a player on two current rosters was counted for both teams | surfaced as a roster conflict; payroll withheld |
| F11 | low | a contract that ended before the financial season read "no 2027-28 cap hit" / "no contract" | "ended before 2027-28 (last year on file: 2026-27)" |
| F12 | low | an empty injury feed closed every open injury | refused while injuries are open |
| F13 | low | future retrieval read as fresh; wording | stale; "never read an injury feed" |

**Mutation results.** Of 44 planted defects the named test files caught 35; the other 9 survivors, plus 2 more when the whole R9.3A suite was used, were reviewed one by one: the ones that would mislead a user (cap-room boundary, `apron_status_after` built from a floor, shot-zone tolerance, injury freshness window, warnings counted as passes, a zero-row/refused season entering the horizon, activation candidate filter and backup verification, the two-way salary accepting non-positive values) each gained a test (`test_r93a_round4_regressions.py`). Four survivors were **not** given a test and remain open, low impact: the calendar-based `roster_is_provisional` never returning true, the roster payload showing a length for an unpriced player, a two-way hint without the base-salary check, and commitments excluding an unpriced player's later seasons.

Held up under attack: all 30 teams are floors (never payroll); Wilson in 1-for-1, 2-for-1, three-team and no-return deals; 526 players × five endpoints with no unlabelled experimental value and **zero cross-endpoint disagreements** on cap hit / base / cash / length / source; 1,307 random trades with **no** `passes_checked_rules` or `verified_legal`; `CAP_LEAGUE_YEAR` of 2027-28 and 2031-32 give a 409 and apply no 2026-27 threshold; replay ingestion is idempotent and *Hansen Yang* stays quarantined; synthetic name collisions are refused; every injury edge reads stale, never "healthy"; backup/restore, kill -9 mid-apply, WAL and symlinked targets are safe.

## 6. Round 5 — release-candidate verification

Run on the final release candidate (after every fix above):

| Check | Result |
| --- | --- |
| Backend full suite (SQLite) | **3,327 passed, 19 skipped, 0 failed** (baseline 3,188 / 19) |
| PostgreSQL 16, new and related files | **193 passed** |
| Frontend vitest | **345 passed** (baseline 311) |
| `ruff check`, `mypy app` (194 files), `eslint`, `tsc` | clean |
| `next build --webpack` | succeeds |
| `alembic check`; up/down/up on a fresh DB; up→down on a copy of the real DB | no drift; round trip clean; **all 37 tables byte-identical** after |
| Validation batteries vs R9.2 | byte-identical (the acquisition battery's `same_need_teams_hear_more_alike` fails identically on R9.2: pre-existing) |
| 300 random real-data trades, R9.2 vs R9.3A engine | identical statuses |
| Replay re-ingestion of 2027-28 and 2030-31 on a copy of the preview | complete; 0 created, 0 updated, 334 / 19 unchanged; 0 requests |
| Preview snapshot vs your database | 27 core tables (players, teams, rosters, statistics, TEI estimates, archetypes, scenarios, saved trades, historical trades, picks, cap parameters …) row-identical; 19 non-candidate model rows unchanged |
| Real-data activation rehearsal | see `local-preview-and-activation.md` §4.1 |
| Not run | Playwright e2e; the full backend suite on PostgreSQL |
| One note | `test_worker_and_issue_growth.py::test_an_unknown_job_is_a_configuration_error_not_a_failure` failed once in a *combined* targeted run (a log-capture order sensitivity) and passes alone and in every full run; it is unrelated to R9.3A |

## 7. Browser verification at 100 % zoom (author)

Run against the live preview on real data at 1440×1000 (CSS pixels = 100 % zoom), plus 390×844:

* **A — player investigation:** Jayson Tatum → Advanced (observed 2025-26: 16 games flagged "thin season", TS 54.1 %, 3P 32.9 % = 47 of 143, FT 82.3 % = 65 of 79) → research preview opened on request (experimental, `cand-state_space-fg3_pct-…`, 80 % credible 34.9–37.8 %) → "Evaluate in a trade" → Trade Evaluator opened on BOS with the carried-player notice naming the season difference.
* **B — trade finances:** ATL↔BOS (Jalen Johnson $30.0M ↔ Jayson Tatum $58.5M): net +$28.5M / −$28.5M, ratios 195 % / 51 %, subtotals shown as `≥` floors, commitments through 2029-30, verdict *Indeterminate — the known salaries alone already indicate a salary matching problem for ATL (not verified)*. With Jalen Wilson (no contract) the net is "not stated".
* **C/D — Cap Center / partial coverage:** ATL 15 of 18 priced, "Known salary subtotal ≥ $193.5M", three unpriced players with reasons, thresholds for 2026-27 only; no payroll, cap space or apron claim.
* **E — cross-tool:** player, team, season, cap hit and source agree across the three tools (`test_the_three_surfaces_agree_on_a_salary` and by eye).
* **F — seasons:** 2025-26 statistics are labelled 2025-26 everywhere; the 2026-27 roster is "provisional"; the Contract tab says "Seasons differ".
* Connected flow incl. browser Back restoring a 2-player deal; mobile 390 px no horizontal overflow.
