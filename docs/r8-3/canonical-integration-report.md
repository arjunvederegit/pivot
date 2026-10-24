# R8.3 — Canonical integration report

Scope: make BALLDONTLIE a *provider behind the canonical layer*, not a second data path. No tool imports the
provider; every tool reads Pivot's tables. Nothing was written to the owner's database (§6).

## 1. Architecture as built

```
BALLDONTLIE GOAT ─► BalldontlieClient (auth, pacing, retry, cursor pagination, ledger)
                     └► BalldontlieProvider (typed fetchers, published category/type pairings checked locally)
                         └► normalizers (live-confirmed row models → canonical records, missing = None)
                             └► crosswalk (team + player identity, quarantine of the unresolved)
                                 └► app/ingestion/balldontlie_jobs.py (idempotent, non-destructive, run-logged)
                                     └► contracts / contract_years · injuries · provider_stat_snapshots · provider_entity_ids
                                         └► shared readers: cba.resolver.salaries · payroll · cap-outlook · roster · data-health
                                            · canonical.provider_stats.provider_stat_rows
NBA.com (nba_api) ─► unchanged ─► rosters · games · standings · player/team statistics
```

## 2. Code changes (branch `feat/r8-3-goat-live-integration`)

| Area | Change |
| --- | --- |
| Client | `x-ratelimit-limit` captured (provider-reported); optional JSONL **request ledger** (endpoint, parameter *names*, status, latency — never key, values or bodies; `BALLDONTLIE_REQUEST_LOG`); `sample=True` for deliberate page-1 probes |
| Normalizers | nested `player` in season averages; `contract_notes` list/dict/null; `team_id`-only players; embedded bios kept for identity linking; contract rows keep the provider's player name (reporting only) |
| Provider | category→type pairings from the published reference (player *and* team), validated **before** any request; `player_ids[]`; `sample` pass-through |
| Crosswalk | active-franchise filter (59 historical teams counted, not failed); `LA`/`Los Angeles` alias; two strict fallbacks for nicknames — **draft slot + surname + height** and **surname + same roster team + height (±1 in)** — each requiring *exactly one* candidate and recorded as `medium` confidence with its method; everything else stays quarantined |
| Jobs (`balldontlie_jobs.py`) | `sync_identities`, `sync_contracts_from_balldontlie`, `sync_injuries_from_balldontlie`, `sync_stats_from_balldontlie`, `link_embedded_players` |
| CLI / Make | `refresh-balldontlie <identities|contracts|injuries|stats|all>` with `--cache DIR`, `--replay`, `--dry-run`, `--season`, `--stat-season`, `--groups`; `balldontlie-evaluate --resume DIR` |
| Schema | one additive migration `e8b3f5c2a914` (below) |
| Readers | `canonical/provider_stats.provider_stat_rows` (provider-, scope- and group-explicit; missing players returned, never zero); `data/providers` reports `live_verified` from what actually ran plus stored-row counts; data-health contracts card reports roster-side coverage; `REFRESH_JOB` now includes injuries |
| Opt-in | `CONTRACT_DATA_PROVIDER=balldontlie` makes every "is a contract provider configured?" reader truthful and routes `sync_contracts` to the BALLDONTLIE job. **Off by default.** |
| Two defects in *existing* code found by the cross-tool run | see §5 |

### Schema (additive; downgrade is a plain drop; tested with pre-existing rows)

| Table | Change | Why |
| --- | --- | --- |
| `contract_years` | `+base_salary`, `+total_cash`, `+salary_basis` (all nullable) | the provider states three different dollar figures (they differ for 11 % of rows); `salary` keeps its meaning for every reader and now says it is the **cap hit** |
| `injuries` | `+return_date` (date) | the provider's expected return, as data not prose |
| `provider_stat_snapshots` (new) | provider · entity · season · season_type · stat_group · aggregation · basis · games_played · `values` JSON · retrieved_at · run id; unique per scope | `player_season_stats` has NBA.com's stat-type vocabulary and **no provider in its unique key**: a BALLDONTLIE row there would collide with or silently replace an NBA.com row |

## 3. What the jobs guarantee (each is a test)

| Property | Evidence |
| --- | --- |
| **Idempotent** | second run: 0 created, 0 updated, N `unchanged` (contracts, injuries, stats, identities) |
| **Non-destructive** | a contract the provider stops listing is kept (`not_reseen`); a delisted injury is *closed* (`valid_to`), never deleted and never read as "healthy" |
| **Corrections propagate** | a changed cap hit updates the row in place; no duplicate contract/year |
| **No silent cross-provider overwrite** | another provider's salary for the same player-season is kept and a `contract_provider_conflict` finding is filed |
| **Missing is not zero** | a null cap hit is *unpriced* (listed with the figures given), never 0, never `base_salary`/`total_cash`; null stat values stay JSON null; absent players come back in `missing_ids` |
| **Quarantine** | unmapped provider players are listed in the run detail and written nowhere |
| **Scope purity** | regular/playoff keys never collide; a row whose `season_type` contradicts the request is rejected, not relabelled |
| **Partial is labelled** | one failed team ⇒ `outcome: partial` and the rest is kept; a capped/incomplete statistic read is **not persisted** |
| **Provenance** | every written row carries provider, retrieval time and run id; every run records requests, counts, quarantines |
| **Secrets** | run details and the ledger contain no key; asserted by test |

## 4. Result of the live-data ingestion (isolated copy of the 2026-10-18 development database)

| Job | Outcome |
| --- | --- |
| identities | 30/30 teams; 524/526 active players; +137 more players linked from embedded bios (43 via contracts, 94 via statistics); 691 links total; **0 ambiguous, 0 conflicts**; 646 players linked at *high* confidence (316 exact name, 316 exact name + biography, 14 accent-folded + biography) and 15 at *medium* (6 accent-folded, 4 suffix-folded, 3 surname + team + height, 2 draft slot + surname) |
| contracts | 453 players priced for 2026-27 (434 on the 2026-27 rosters), 97 unpriced rows listed, 32 contract rows for players with no Pivot identity quarantined; 0 cross-provider conflicts |
| injuries | 92 opened (92/92 mapped to rostered players) |
| statistics (2025-26 regular season) | 7 groups × players (570 mapped of 582; 12 unmapped = players with no Pivot identity, R8.1 B2) + team rows for the 5 groups the team endpoint publishes → **4,055 snapshot rows**; re-running changes nothing |
| Requests | the live calls made while recording are inside the evaluation's 148; every ingestion re-run used the recorded responses (0 requests) |

All 15 `medium`-confidence links were audited by hand and are the same person: *Nah'Shon→Bones Hyland,
Nicolas→Nic Claxton, Alexandre→Alex Sarr, Carlton→Bub Carrington, Airious→Ace Bailey*, the suffix cases (*Craig
Porter Jr., Morez Johnson Jr., Trey Jemison III, Jimmy Butler III*) and six accent cases (*Dëmin, Topić, Traoré,
Dadiet, Salaün, Konan Niederhäuser*). The 2 quarantined (*Hansen Yang*, *David Jones Garcia*) would be wrong to
guess and are reported.

## 5. Cross-tool validation (isolated API + production frontend build, ports 8042/8043/8044/3042)

Method: three copies of the 2026-10-18 development database — **baseline** (migrated only), **ingested**, and
**ingested + `CONTRACT_DATA_PROVIDER=balldontlie`** — served by separate API processes; 218 read endpoints
(30 teams × roster/needs/season-stats/payroll/cap-outlook/profile + player, scenario, trade, readiness and data
lists) compared with volatile fields stripped.

* **Row-hash check** (sha-256 of every row, 22 tables, excluding timestamps) between the owner's database
  (opened read-only), the baseline copy and the ingested copy: **identical for all 22** — `model_versions` (19),
  `player_impact_estimates` (512), `player_archetypes` (632), `team_needs` (279), `scenarios` (20),
  `scenario_weights`, `trade_proposals`/`trade_teams`/`trade_assets`/`trade_evaluations`/`trade_rule_results`
  (saved scenarios), `player_season_stats` (17,022), `team_season_stats`, `standings`, `games`, `rosters`
  (1,056, incl. 526 of 2026-27), `players`, `teams`, `historical_trades` (565), `league_cap_parameters`,
  `comparison_sets`, `generated_reports`. **Existing models, estimates, history and saved scenarios were not
  touched.**
* **API check:** 37 of 218 endpoints byte-identical; the 181 that differ differ **only** in contract-derived
  fields (`salary`, `payroll_*`, `cap_context_partial`, `contract` state/rows in `season_context`, cap-outlook
  seasons/players). No impact, need, archetype, profile, roster-membership, scenario or trade field changed.
* **Trade legality unchanged:** a two-team ATL↔BOS trade between two priced players returns the identical
  rule-result list on the baseline, ingested, and opt-in servers (salary-dependent rules stay `unavailable`;
  roster rules unchanged).
* **No team can claim a verified payroll:** 0/30 (best team 16/18 priced). Payroll is served as a *lower bound
  — N salaries missing*; tax/apron position is withheld.
* **Browser (production build):** Cap Center, Team Outlook, Trade Evaluator, Data Health render; team page shows
  "lower bound — 3 salaries missing"; no layout, branding or control was changed.
* **Not exercised in a browser:** Player Explorer, Strategy Lab, Workspace, a player page, Ask Pivot (off by
  default). Their data endpoints are covered by the API comparison; no frontend code changed except one TypeScript
  type (`expiring: boolean | null`).

### Two defects in code that already existed, exposed by real contract data

1. `contract_years_remaining` counted contract *rows on file*. With a per-season source that is 1 for everyone, so a
   five-year deal read as one season. It is now `null` for contracts from a per-season source.
2. Cap Center's `expiring` flag was "the last season on file is this season" — every BALLDONTLIE contract rendered
   as **expiring** (found in the first browser pass: Dyson Daniels, $25 M). It is now `null` (unknown), the Cap Center
   shows "—", and the cap-outlook `note` says: *"N contract(s) come from a per-season source: only 2026-27 is
   loaded, so contract length, options and expiry are not known."*

Both were invisible while the `contracts` table was empty. They are fixed at the source, with tests, and are the
reason any future contract source needs a multi-year path before Cap Center's multi-season view means anything.

## 6. Isolation record

* The owner's `tradelab.db` was only opened **read-only** (copied for the working databases; mtime unchanged
  `17:21`, row hashes identical). No migration, ingest, training or scoring touched it.
* Ports used: 8042, 8043, 8044 (API) and 3042 (frontend), all started and stopped by this session. The owner's
  server on 8010 was neither touched nor probed.
* `make train` / `make score`: **not run**. No model, coefficient, estimate or version was changed.
* No file in the shared checkout or the R8.2 worktree was modified (the `.env.local` there was read for one
  variable only).

## 7. Tests (counts are from the final runs)

| Suite | Result |
| --- | --- |
| Backend, full (`pytest --cov=app --cov-fail-under=85`) | **2,765 passed · 57 skipped · 0 failed**; coverage **91.7 %** (floor 85) |
| of which R8.3 | adapter 35 · harness 34 · new adapter/crosswalk behaviours 13 · ingestion & consumers 18 · additive-migration 1 (plus the CLI declaration test updated) |
| `ruff check app tests`, `mypy app` | clean (164 source files) |
| Frontend `vitest run` | **308 passed** (21 files); `tsc --noEmit` clean; production `next build` succeeds |
| Alembic drift check (`alembic check`, models vs migrations) | passes |
| Live smoke | 148 requests, §2 of the evaluation |
| Skips | 57 = optional assistant dependencies (`langchain_core`, `langsmith`) not installed, plus PostgreSQL-only tests |

**Not verified:** PostgreSQL (the Docker daemon was not running; `PIVOT_TEST_DB_URL`/`…POSTGRES_URL` tests
skipped — the new columns/table use only portable types, but this is unproven); Playwright end-to-end; Ask Pivot;
behaviour across a real game night or the 2026-10-20 / 2026-11-03 boundaries; concurrency.
