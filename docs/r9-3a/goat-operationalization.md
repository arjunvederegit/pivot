# GOAT operationalization — what data Pivot can now use, how it got there, and what it cost

R9.3A turns BALLDONTLIE GOAT data from "ingested into a scratch copy" into data the shared backend services — and through them Cap Center,
the Trade Evaluator and Player Explorer — read. This page documents what is live versus cached, how identities and seasons are handled,
what each refresh cost, and what the provider does *not* give.

## 1. Where GOAT data enters, and where it is read

```
BALLDONTLIE API ──(server-side client, key never leaves the backend)──▶ app/integrations/balldontlie/*      adapter
                                                                       ▶ app/ingestion/balldontlie_jobs.py   canonical, idempotent jobs
                                                                       ▶ canonical tables (contracts, contract_years, injuries,
                                                                         provider_entity_ids, provider_stat_snapshots, data_sync_runs)
                                                                       ▶ app/services/financials.py          ONE read path for contracts
                                                                       ▶ Cap Center · Trade Evaluator · Player Explorer
```

* **No tool talks to BALLDONTLIE.** No request is made on a page view, on a render, or by a test. The browser never sees a credential: the key is read
  by the backend from its environment (`BALLDONTLIE_API_KEY`), wrapped as a `SecretStr`, sent only as the `Authorization` header, and redacted from every
  error message (`client._redact`). Nothing in R9.3A adds a client-side integration.
* **Reads are database reads.** `GET /teams/{id}/cap-outlook`, `/teams/{id}/roster`, `/players/{id}/contract`, `/players/{id}/shot-zones`,
  `/players/{id}/injury` and `POST /trades/validate|evaluate` read stored rows only. `tests/integration/test_r93a_api.py::test_no_provider_is_called_by_a_read`
  fails the build if a read reaches the network.
* **Refreshing is a deliberate operator action** (`make refresh-balldontlie WHAT=...`), never a side effect of using the product.

## 2. What was loaded in R9.3A (and what it cost)

| Dataset | Source endpoint | Rows | Requests | Notes |
| --- | --- | ---: | ---: | --- |
| 2026-27 contract years | `/contracts/teams?season=2026` | 453 | 0 this session (30 in R8.3) | Re-used from the R8.3 ingest; re-observed 2026-10-19 03:13 UTC |
| 2027-28 contract years | `/contracts/teams?season=2027` | 334 | 30 | all 30 teams; outcome `complete` |
| 2028-29 contract years | `/contracts/teams?season=2028` | 198 | 30 | complete |
| 2029-30 contract years | `/contracts/teams?season=2029` | 98 | 30 | complete |
| 2030-31 contract years | `/contracts/teams?season=2030` | 19 | 30 | complete |
| Contract-length validation | `/contracts/players/aggregate` | 19 players | 19 | see §4 |
| Identity links, injuries, 2025-26 statistics snapshots | — | 691 / 92 / 4,055 | 0 | R8.3 ingest, activated into the preview |
| **Total new requests this session** | | | **139** | cap was ≈150; no 429, no 5xx, no retries |

The 120 future-season requests are the same canonical job R8.3 used (`sync_contracts_from_balldontlie`), run once per season with `--season`. Raw
responses were recorded to a local cache directory (outside Git) so any step can be re-run with `--replay` at zero cost. **No raw provider payload is
committed**; only derived rows live in the (uncommitted) databases.

The deferred historical advanced-statistics backfill (~260 requests, needs historical identity and survivor-bias handling) was **not** started.

## 3. Live versus cached, and how fresh each thing is

| Data | Live or cached | Refresh | Freshness shown to the user |
| --- | --- | --- | --- |
| Contract years (cap hit, base salary, total cash) | cached in `contract_years` | operator: `make refresh-balldontlie WHAT=contracts SEASON=<season>` | per-contract retrieval time (the financial season's own), Cap Center / Player contract / Trade panel source rails |
| Contract horizon (which seasons were loaded league-wide) | derived from `data_sync_runs` | automatic with each run | Cap Center "Contract years loaded", Data Health |
| Injuries | cached in `injuries` | operator: `WHAT=injuries` | "current as of retrieval" only within 24 h, otherwise "last reported — may no longer be current" |
| 2025-26 statistics snapshots (7 groups) | cached in `provider_stat_snapshots` | operator: `WHAT=stats` | season, season type and retrieval time on every panel |
| Rosters | cached from NBA.com (not GOAT) | `make refresh DATASETS=rosters` | "provisional preseason snapshot" until the cut-down |

The provider gives **no update time** on any payload, so Pivot's own retrieval time is the only freshness signal and is the one displayed.

## 4. How contract length is derived — and checked

The per-season endpoint returns one row per contract year. A player's final season is therefore the last season he has a row — *if* every season was
read for the whole league. `app/services/financials.py::horizon` reads which seasons were (successful `sync_contracts` runs with `outcome: complete` and no
failed team) and keeps the **contiguous** chain from the financial season. Then:

* final row **before** the last loaded season → `ends` (exact);
* final row **is** the last loaded season → `through_horizon` — "at least this long" (a contract can run past what was loaded);
* only the financial season loaded → length **unknown** (every priced contract has a row in the only season read, which proves nothing);
* a partial read (one failed team) is **not** counted.

**Validation.** 19 players were sampled, stratified by derived final season (5 / 4 / 4 / 3 / 3 across 2026-27 … 2030-31), and compared with the provider's own
contract-aggregate `free_agent_year` (final season = `free_agent_year − 1`):

| Result | Players |
| --- | ---: |
| derived final season equals `free_agent_year − 1` | 18 |
| derived `through_horizon`, provider says the contract runs to 2031-32 (Amen Thompson, extension) | 1 — correctly flagged "at least", not "ends" |
| disagreements | 0 |

Sample size is small (19 of 458) and is a *check of the method*, not an audit of every contract. Options, guarantees and no-trade clauses are **not** loaded: the
provider gives options only as free text and Pivot does not parse prose into structured fields.

## 5. Identity and season semantics

* **Canonical ids only.** Every row is keyed by Pivot's canonical player/team ids through `provider_entity_ids` (691 links: 30 teams, 661 players;
  510 rostered links `high`, 14 `medium`). A contract row whose provider player has no verified link is **quarantined in the run detail and written nowhere**.
  This session's future-season loads quarantined one: *Hansen Yang* (provider id 1057275769) — the provider spells the rookie *Yang Hansen* the other way
  round, so the name crosswalk correctly refused to guess. He has no cap hit in Pivot until an operator links him by hand.
* **Three seasons, never blended.** Every financial record names its **financial season** (`CAP_LEAGUE_YEAR`, 2026-27); the roster is the 2026-27 roster
  snapshot (provisional); performance evidence is the **2025-26 regular season**. Where they differ the UI says so (Trade Evaluator staged-player notice,
  Contract tab, "Seasons differ" line).
* **Cap thresholds are per season.** `league_cap_parameters` holds 2025-26 and 2026-27 only; thresholds are never extrapolated to later seasons, and the Cap
  Center says so.
* **Cap hit ≠ base salary ≠ cash.** All three are stored (`salary_basis = cap_hit`) and shown side by side; they differ for ~11 % of rows. Only the cap hit
  feeds payroll and trade checks.
* **Missing is NULL.** A provider row with no cap hit is never written as $0 or as its base salary. 92 rostered players have no 2026-27 cap hit:
  53 have a provider row whose total cash equals the sourced 2026-27 two-way salary ($678,882 — probably non-counting two-way deals, **reported as a hint and
  never assumed**), 7 have another provider row without a cap hit, 32 have no contract row at all, and the 2 unlinked identities are among them.

## 6. Idempotency and failure behaviour (as re-verified in R9.3A)

* Re-running a season changes nothing (`years_unchanged` counts); a changed figure is an `updated` row; nothing is deleted (`not_reseen` is reported).
* A team that fails to answer makes the run `partial`; a partial season is **not** counted in the horizon and no length is derived from it.
* A provider outage during a *read* is impossible by construction (reads never call the provider); during a *refresh* the run fails or is partial and is
  recorded, and the stored data is untouched.
* `tests/unit/test_r93a_financials.py::TestHorizon` covers: no runs, contiguous chain, a gap, a missing financial season, a partial read, a failed run.

## 7. Provider terms and redistribution

BALLDONTLIE's published terms were reviewed in R8.3 (`docs/r8-3/provider-strategy.md`). R9.3A displays **only categories R8.3 already persisted** (contracts,
injuries, statistics) to the single local user; it adds no export, no redistribution endpoint, and no raw-response storage in Git. The licensing question for a
*shared or hosted* deployment remains open and is listed as a limitation.

## 8. What the provider does not give (so Pivot cannot show it)

Option flags and deadlines (free text only), guaranteed amounts without one request per player, no-trade clauses, signing dates, a structured contract type,
cap holds, dead money, trade exceptions, and any update timestamp. See `financial-intelligence.md` for what each absence prevents.
