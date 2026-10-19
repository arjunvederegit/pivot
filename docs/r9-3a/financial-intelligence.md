# Financial intelligence — contract semantics, what is calculated, what is withheld

One service assembles every contract fact the tools show: `backend/app/services/financials.py`. Cap Center, the roster payload, the player contract tab and the
Trade Evaluator all read it, so they cannot disagree about a salary, a season or a source (`TestRosterAndPlayerContract::test_the_three_surfaces_agree_on_a_salary`).
The trade-specific projection is `backend/app/services/trade_financials.py`. Nothing in either feeds a basketball evaluation or changes a model output.

## 1. Vocabulary (kept distinct everywhere)

| Term | Meaning | Where it comes from |
| --- | --- | --- |
| **Cap hit** | the amount counted against the cap for the season | `ContractYear.salary` (`salary_basis = "cap_hit"`) — the only figure payroll and trade checks use |
| **Base salary / total cash** | different quantities the provider reports | `ContractYear.base_salary / total_cash` — shown beside the cap hit, never substituted for it |
| **Financial season** | the league year salaries are read for (`CAP_LEAGUE_YEAR`) | settings; named on every record |
| **Known salary subtotal** | the sum of the cap hits on file for a roster | **a floor, never called payroll** unless every rostered player is priced |
| **Payroll** | a team's total cap hits | only when every rostered player has a priced cap hit |
| **Contract length** | derived from the loaded seasons | `ends` / `through_horizon` ("at least") / `unknown` — see `goat-operationalization.md` §4 |

## 2. Coverage today (preview database, 2026-10-19)

| Measure | Value |
| --- | --- |
| Rostered players on the 2026-27 provisional roster | 526 (30 teams, 16–20 each) |
| With a 2026-27 cap hit | **434 (82.5 %)** — team range 76 %–89 % |
| Teams with a complete payroll | **0 of 30** |
| Not priced | 92: 53 cash = two-way salary (hint only) · 7 other provider rows without a cap hit · 32 no contract row |
| Contract years loaded league-wide | 2026-27 through 2030-31 (453 / 334 / 198 / 98 / 19 rows) |
| Contract length derivable | every priced contract: `ends` (exact) or `through_horizon` (≥) — 19-player check against the provider: 18 exact, 1 correctly "at least" |

The 82.5 % is the R8.3 figure, **recomputed from the database** (`make r93a-readiness`), not copied. No refresh of the 2026-27 rows was made this session
(they are 15 hours old; the provider has no update time).

## 3. What the Cap Center now calculates and shows

* **Known salary subtotal** (labelled as such, with a leading `≥`) and **cap hits on file `n/m`**, per team, for the financial season.
* **Commitments by season**: the known cap hits of the *rostered* players for each loaded season, with the number of players summed — captioned "never a payroll"
  and excluding cap holds, draft picks, dead money and players not on the roster.
* **Per contract**: cap hit by season (hover for base salary and cash), contract length (`4 seasons · through 2029-30`, or `5+ seasons · through at least 2030-31`),
  source and retrieval time, a link to the player file.
* **Not priced** list with the reason for each player (`no contract row`, or the provider row's figures and the two-way hint).
* **"What this page can say"**: the supported statements and the *withheld* conclusions with reasons (payroll, cap space, apron position, luxury-tax bill,
  seasons after the financial season), plus definitions and what is not on file.
* **Thresholds** for the financial season only, with their source and verification date.

### Why apron position, cap space and the tax bill are withheld

* *Payroll / cap space / apron* need **every** rostered salary. No team has them (0 of 30), so a floor is shown and the position is withheld. A floor can prove a team is
  *over* a line (shown, e.g. "already exceeds the salary cap") and can never prove it is *under* one.
* The roster is a **provisional preseason snapshot** (camp invitees, Exhibit 10 and two-way players cannot be told apart from the final 15), so even the floor describes the
  snapshot, not the final roster. The page says so.
* The **luxury-tax bill** is assessed on end-of-season payroll with graduated rates and depends on cap holds, dead money and in-season moves Pivot does not hold.
  It is never computed.
* Thresholds exist for 2025-26 and 2026-27 only; later seasons show commitments, not a cap or apron position. A 2025-26 threshold is never applied to 2026-27
  (`test_thresholds_are_for_the_financial_season_only`).

## 4. What the Trade Evaluator now shows

For every traded player: cap hit, base salary, total cash, contract length, source and freshness, and whether the contract counts toward salary matching
(`stated`, `deduced not-two-way`, or `unverified`). For every team: salary out and in (each side shows `unknown` plus the named players if any salary is missing),
the **net salary change only when every salary behind it is known** (otherwise "not stated"), the incoming-to-outgoing ratio, the subtotal before → after with its
floor label, and the contract commitments the deal moves in each loaded season (a season with an unknown player shows "not stated", never $0).

### The legality assessment (the part that must never overclaim)

`POST /trades/validate` and `/evaluate` return the engine result **unchanged** plus `financial_analysis.legality_assessment`:

| Verdict | Meaning |
| --- | --- |
| `prohibited` | at least one **implemented** rule fails with complete inputs — the rule is named |
| `indeterminate` | nothing fails, but at least one rule could not be verified; any known-salary **indication** of a failure is listed separately and labelled "not a verified failure" |
| `passes_checked_rules` | every implemented rule passed — **never** "legal"; the unchecked categories are attached to every answer |

Always attached: the *checked and passed*, *could not be verified (and why)* and *restrictions Pivot does not check* lists (existing trade exceptions, sign-and-trade,
cash, base-year compensation, hard-cap triggers, incentives/guarantees, aggregation timing, pick legality beyond Stepien, calendar restrictions). The short chip
wording was changed from "Legal / Illegal" to "Passes checks / Fails a check".

### Engine changes (each tested, none silent)

1. **Cap-room alternative.** A team whose post-trade payroll is at or under the cap needs no salary matching. The old rule applied a matching band to such a team and
   could report a *false failure* for a cap-space team taking back more than twice what it sends. Fixed; `TestUnderTheCapNeedsNoMatching`.
2. **Deduced "not a two-way contract".** `contract_type` is NULL for every GOAT contract, which kept `SALARY_DATA_AVAILABLE`, `TWO_WAY_EXCLUSION` and the matching sums
   `unavailable` for all 434 priced players. A cap hit **above the sourced league two-way salary** ($678,882 for 2026-27) proves the contract is not a two-way deal (a two-way
   contract is paid exactly that figure), and "not two-way" is all matching needs. It is deduced in one direction only, never written to `contract_type`, flagged
   `confidence: medium`, and a cap hit at or below the figure proves nothing. `TestNotTwoWayDeduction`.
3. **Floor-based failure indications.** When a payroll is a floor, matching can still be *indicated* to fail (the floor already exceeds the first apron so the standard
   exception binds; or incoming exceeds even the most generous limit and the floor exceeds the cap). The rule stays `unavailable` — the existing standard "partial data
   refutes but never passes" is kept, and an indication that rested on a roster snapshot must not suppress the basketball evaluation — and the assessment lists it as an
   **indication**. A deal within the strictest limit is reported as "every regime would accept it" and **still not a pass**. `TestPartialPayrollIndicationsNeverDecide`.
4. **Provisional roster counts.** A roster-count failure measured on the provisional preseason snapshot is downgraded to a low-confidence **warning** (the count may include
   camp deals that will be cut). Settled rosters still fail. `TestProvisionalRosterCountIsNotAVerifiedFailure`.

### What is not changed

The basketball evaluation (TEI, fit, simulation, drivers) is untouched: `evaluate_for_team` receives the same legality object it always did, and a trade is suppressed only
when an implemented rule fails on *complete* inputs, exactly as before. The R9.2 candidate models are not read by the Trade Evaluator.

## 5. Mathematics of the comparison

* `net = Σ incoming cap hits − Σ outgoing cap hits`, defined only if every traded salary on both sides of that team is known. `ratio = incoming / outgoing` when both are known and
  outgoing > 0.
* `known_subtotal_after = known_subtotal_before − Σ outgoing + Σ incoming` (a floor; it is a valid floor because each unknown salary is a non-negative amount that is in neither term).
* Matching-eligible sums exclude two-way contracts and are withheld when any traded contract's matching class is unknown.
* Per-season commitments: a player whose contract `ends` before a season contributes a known **zero** to it; a player with `unknown` length or no priced cap hit makes the season
  "not stated" for that side.

## 6. Failure modes the tests pin

Unknown ≠ $0 (financials, cap-outlook, trade comparison); a base salary or cash figure is never a cap hit; a lower-bound length is never "ends"; a one-season horizon proves no length;
a partial provider read is not a horizon; a 2025-26 threshold is not used for 2026-27; `verified_legal` is never produced from partial coverage; the assessment never says "legal"; a
write inside a session invalidates the memoized financials.

## 7. Remaining limitations

No complete payroll for any team (needs two-way / Exhibit 10 / camp classification from a labelled source — the transactions ledger or a roster-type feed); no options,
guarantees, no-trade clauses or signing dates (the checks that need them stay `unavailable`); the final 15-man rosters are not yet set; no second source verified any individual
contract amount (a 10-contract human spot-check against a trusted source is still the cheapest next step); *Hansen Yang* is unlinked.
