# R8.3 — 2026-27 contracts and cap readiness

**Verdict.** BALLDONTLIE gives Pivot its first 2026-27 salary data: **454 priced contract rows, 453 persisted,
434 of the 526 players on the provisional 2026-27 rosters (82.5 %)**. That is enough to show **every team's
payroll as a disclosed lower bound** (30/30) and **not** enough to verify any team's cap, tax or apron position
(**0/30**), to run salary-matching rules, or to say anything league-wide. The existing tools already behave that
way, and R8.3 kept them that way (§5). No trade-legality assumption was changed.

## 1. Coverage (live, 2026-10-18; all 30 teams; observed, not advertised)

| | Players | Share of 526 |
| --- | --- | --- |
| Cap hit present (priced) | 434 | 82.5 % |
| A contract row exists but **no cap hit** | 60 | 11.4 % |
| Linked to BALLDONTLIE but **no 2026-27 contract row** | 30 | 5.7 % |
| No BALLDONTLIE identity link (*Hansen Yang*, *David Jones Garcia*) | 2 | 0.4 % |

Per team: 76 %–89 % priced (median 82 %), e.g. DAL 16/18, MIL 16/18, MIN 14/16, NYK 13/17, POR 13/17.
**No team is complete**, so none can show a verified payroll. Another 19 contract rows (cap-hit-bearing) belong to
players not on Pivot's rosters (waived, camp, off-roster) and are stored but do not count toward coverage.

Missing seasons: only **2026-27** was loaded. Seasons 2027-28 onward are *not* in Pivot. (They are available:
`/contracts/teams?season=2027` is 30 requests per season; see §6.)

## 2. Data quality, as far as it could be checked

| Check | Result |
| --- | --- |
| Contract team = Pivot roster team | 454/454 (0 disagreements) |
| Duplicate or ambiguous player↔contract mapping | 0; the 32 contract rows for players with no Pivot identity are listed in the run, not written |
| Conflicts with another provider | none possible: Pivot has no other 2026-27 contract data (`contracts` was empty). The conflict path is tested, not exercised |
| Internal consistency | every row with a cap hit also had base salary and cash; they differ in 49/454 (11 %) — so **the three figures are genuinely different quantities** and are stored separately |
| Plausibility | per-team sums of known cap hits $132.9 M–$229.9 M against a $165.0 M cap, $200.4 M tax, $209.0 M first apron, $221.7 M second apron; the highest are floors (a team is missing 2–4 players). Not checked against a third source |
| Accuracy of any single contract | **not verified** — no second source is licensed or on file. A 10-contract human spot-check against a source the owner trusts (R8's M3) is the cheapest next step |
| Update time | no provider timestamp; Pivot records its own retrieval time |

### What the missing rows probably are (unverified)

128 returned rows have a null cap hit: 70 with only `total_cash` (67 of them exactly **$678,882**), 58 with only
`base_salary`. Pivot's roster is a *provisional preseason* snapshot of 16–20 players per team, and this is the
shape non-counting deals (two-way, Exhibit 10, camp) would take. **The provider does not label them and Pivot does
not infer it.** Consequences kept deliberately: those players are *unpriced*, they are never $0 and never their cash
or base figure, and each is listed by name in the run (`data_sync_runs.detail.unpriced_players`).

## 3. What a CBA-compliant trade evaluation needs, and who supplies it

| Input | GOAT | Pivot today |
| --- | --- | --- |
| Cap hit for the league year | **yes** (82.5 % of roster) | — |
| Base salary, cash | yes (kept separate) | — |
| Contract length, total value, guarantees | **yes, but one request per player** (not loaded) | — |
| Options (player/team/ETO) | **free text only** ("2028-29: Player Option (deadline 6/29/28)"); no structured flags | `contract_years.player_option/team_option` exist, **not filled** — nothing is parsed from prose |
| Trade bonus / incentives / pay advances | free text only | — |
| No-trade clause | **absent** | `contracts.no_trade_clause` stays NULL |
| Contract type (standard / two-way / Exhibit 10) | **absent** (provider's `contract_type` is a different, inconsistent vocabulary) | `contracts.contract_type` stays NULL |
| Signing/acquisition date, signed-using exception | `signed_using` (method) only; **no date** | recently-signed and aggregation rules stay `unavailable` |
| Cap holds, trade exceptions, dead money, stretch, waiver claims | **absent** | not modelled |
| Pick ownership and protections | absent | `draft_picks` (separate import) |
| Apron/tax status of the *other* team | derivable only from complete payrolls | withheld when coverage < 100 % |

GOAT supplies the **salary side**. It does **not** supply enough to validate a trade, and R8.3 does not claim it
does: legality stays `conditionally_valid`/`unavailable` with its disclosure.

## 4. What Pivot does with it (verified through the isolated API)

| Surface | Behaviour |
| --- | --- |
| `GET /teams/{id}/payroll` | `payroll_known` = sum of cap hits on file, `payroll_is_lower_bound: true`, `payroll: null`, the unpriced players named, `cap_context_partial` = only thresholds the *known* contracts already exceed |
| Cap Center | "≥ $193.5M — floor, contracts missing · 15/18 players covered"; unpriced players show "no salary on file"; "Cap/apron position is only computed when every rostered player has a known salary" |
| Roster payload | `salary` = cap hit; `contract_years_remaining` = `null` (length unknown); `contract_type` = `null` |
| Cap outlook | one season; `expiring: null`; note says length/options/expiry are unknown |
| Trade Evaluator | identical rule results to the baseline (salary rules `unavailable`) |
| Data Health | contracts card: *"453 contracts on file · 434/526 rostered players priced"*; status `incomplete` with the opt-in on, `unavailable` with it off — and the action text says which |

## 5. Not decided here (and why)

* **Whether to count the 60 "no cap hit" players as zero cap.** It is probably right for two-way/Exhibit 10
  deals, but that is a CBA fact, not a provider field. It needs a labelled source (roster-type data or the
  transactions ledger) before Pivot may use it; doing it now would manufacture a "verified" payroll.
* **Whether to make BALLDONTLIE the configured contract provider.** It is **opt-in**
  (`CONTRACT_DATA_PROVIDER=balldontlie`). With it *off*, the rows still feed payroll as a lower bound but several
  rules say "no contract provider is configured" while 453 contracts exist — Data Health now says exactly that
  and names the setting. With it *on*, rules say "this player has no salary on file". Recommended: turn it on
  after the owner's activation checkpoint, because the second message is the true one.

## 6. Path to a trustworthy cap picture (ordered; none done in R8.3)

1. **Spot-check 10 contracts** against a trusted source (owner, 15 minutes).
2. **Label the 90 unpriced players**: obtain roster type (two-way/Exhibit 10/camp) from the NBA transactions
   ledger or a licensed feed. If the rows are non-counting, coverage of *cap-relevant* players may already be ~100 %.
3. **Load contract length and guarantees**: `/contracts/players/aggregate`, one request per priced player
   (≈ 454 requests, ≈ 1 minute at 600/min; **exceeds the ≈200-request guideline — needs the owner's approval**).
   Write into `contract_years` for 2027-28+ only from *structured* fields; keep option text verbatim.
4. **Load future years** via `/contracts/teams?season=2027…2030` (30 requests/season) for the multi-season Cap
   Center view — but only together with step 3, or `contract_years_remaining` is still wrong.
5. **A structured option/NTC source** (R13/R14 prerequisite) — the part GOAT cannot give.
6. Re-run `make contract-coverage`; the gate for enabling league-wide cap/apron views is
   `teams_with_complete_payroll > 0` **and** the 10-contract spot-check passing.
