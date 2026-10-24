# R8 — Implementation report

Branch `feat/r8-canonical-data-foundation` (from `main` @ `ccaf0b8`). Nothing merged. Design is in
[architecture.md](architecture.md); evidence in [validation-report.md](validation-report.md).

## 1. What changed, by area

### New: `backend/app/canonical/` (the provider-independent layer)

| Module | What it is |
| --- | --- |
| `seasons.py` | `Season` (strict `YYYY-YY` parse, provider-year mapping, arithmetic), `SeasonType` (+ NBA.com and BALLDONTLIE label maps), `StatScope`, `game_date_from_utc` |
| `stats.py` | stat-type registry (aggregation, basis, minutes unit), `require_comparable`, `numeric`/`per_game_from_total`/`minutes_per_game` (missing is `None`, never 0) |
| `records.py` | provider-independent records; `ProviderResult` with `ok/empty/unavailable/unauthorized/tier_restricted/rate_limited/schema_error/not_configured` |
| `identity.py` | name tiers + `PlayerHints`, `ProviderIdMap` crosswalk, `TeamIndex`, one team-alias table, `FRANCHISE_ERAS` |
| `rosters.py` | `current_roster_clause` (the one predicate), `current_roster`, `current_team_of` (member / unlisted / conflict) |
| `stat_access.py` | `player_stat_rows` (scope-explicit; missing players listed, not zeroed), `team_stat_rows` |
| `provenance.py` | `SourceMeta`: freshness by oldest row, completeness against a stated expectation, observed/estimated basis |

### New: `backend/app/integrations/balldontlie/` (optional)

`client.py`, `normalizers.py`, `provider.py`, `crosswalk.py`, `evaluation.py` — see
[balldontlie-goat-evaluation.md](balldontlie-goat-evaluation.md). Server-side key (`SecretStr`),
off unless `BALLDONTLIE_API_KEY` is set, never exposed to the browser.

### Changed: ingestion (`ingestion/jobs.py`)

* `sync_rosters` is an **idempotent diff**: unchanged membership is confirmed (`source_retrieved_at`
  moves, `valid_from` does not), a new team opens a row, a dropped player's row is closed
  (`is_current=false`, `valid_to`), and `player_team_history` is upserted on (player, team, season).
  Run detail records `added / confirmed / closed`.
* **Plausibility guard** before any row is touched: no usable rows, < 90 % of the previously covered
  teams, or < 50 % of the previous player count → `SnapshotRejectedError`, run recorded as failed,
  stored data untouched.
* `sync_standings / player_stats / team_stats / games` reject an empty answer the same way instead of
  recording a green run that wrote nothing.
* Statistics are written and looked up with an explicit `season_type='regular'`.

### Changed: schema (migration `a9d4c6e18b53`, additive, reversible)

* `season_type` on `player_season_stats` and `team_season_stats` (backfilled `regular`); unique keys
  widened to include it.
* `provider_entity_ids` table.
* `media_assets`: `usage_status` (default `unverified`), `license_ref`, `source_url`, `width`, `height`.

### Changed: read paths (additive JSON only; no existing key changed)

| Endpoint | Added |
| --- | --- |
| `GET /teams/{id}/roster` | `data_status.roster` |
| `GET /teams/{id}/season-stats` | `data_status.{player_stats,roster}`, `missing_player_ids`, `stat_definition` |
| `GET /teams/{id}` | `data_status.team_stats` (and an explicit regular-season filter) |
| `GET /players/{id}` | `membership_status`, `data_status.roster` |
| `GET /players/{id}/stats` | `season_type`, `stat_definitions`, `data_status`; regular-season only |
| `GET /players/season-totals/{season}` | per row `season_team_abbr`; `season_type`, `data_status` |
| `GET /assets/coverage` | `usage_status`, `assets_cleared_for_public_display`, `rights_note` |
| **new** `GET /data/freshness`, `GET /data/providers` | dataset-level `SourceMeta`; provider registry (no credentials) |

### Changed: consumers moved onto the shared definition

`current_roster_clause` now replaces the hand-written predicate at **all 20 query sites** (team page,
player directory, trade evaluator, payroll, CBA resolver, needs scorer, impact reference, training,
data quality, contract coverage, asset coverage). The trade evaluator's query shape is unchanged because
its query budget is a tested property. `analytics/features.py`, `score.py`, `train.py` filter
`season_type == 'regular'` explicitly. `analytics/train.py` and `assets/indexer.py::coverage` — which
had no season filter — are now season-aware (identical on today's single-season data).

Team-code aliases (`BRK/CHO/PHO`, `phl/uth`) now come from one table; the three old constants are
aliases onto it (values asserted identical by a test).

### Changed: data quality (`validate_data`)

New checks: `stale_dataset` (per dataset, with age), `unknown_stat_type`, `duplicate_contract_header`,
`dangling_provider_link`.

### Operations

`make data-status`, `make refresh DATASETS=…`, `make link-balldontlie [DRY=1]`,
`make balldontlie-plan`, `make balldontlie-evaluate [REPLAY=…]`; `backend/scripts/r8_consistency.py`
(read-only cross-tool audit of any database). New settings: `BALLDONTLIE_*`,
`STATS_STALE_AFTER_SECONDS`, `CONTRACTS_STALE_AFTER_SECONDS` (documented in `.env.example`).

## 2. Deliberate behaviour changes

1. **Roster sync no longer supersedes every row each run.** The previously tested behaviour
   ("the previous snapshot is kept, marked, and dated") was replaced; its test was rewritten and new
   tests cover movement, drops, history idempotency and the guard. Rationale: audit C3/C4.
2. **Empty season answers now fail the run** rather than succeed with zero rows (audit C4).
3. **`/players/{id}/stats` and `/season-totals` filter to regular season explicitly.** Identical on
   today's data (no playoff rows exist); prevents a future playoff row overwriting regular-season
   figures under the same `(season, stat_type)` key.
4. **A player on two current rosters has no `current_team`** in the player directory (it returns
   `null`; the detail endpoint reports `membership_status: conflict`) instead of an arbitrary one.

No model coefficient, threshold, feature or scoring rule changed. The three validation batteries are
byte-identical before and after on the real database ([validation-report.md](validation-report.md) §4).

## 3. What was *not* done, and why

| Not done | Reason |
| --- | --- |
| Persist BALLDONTLIE data into statistics/contract tables | Never observed live; defining storage for unobserved shapes is guessing. Gated on the trial. |
| A `seasons` table | No consumer needs it; 12-table migration risk (architecture §6). |
| Merge the two name normalisers | They intentionally differ; merging changes real photo matches. |
| Unique constraint on `contracts(player_id, source)` | The table is empty on the dev DB and a user's may hold duplicates; a failing migration is worse than a finding. Quality check added instead. |
| Player/team images or any UI surface of `data_status` | Frontend redesign preserved; backend support is sufficient and the fields are ready for it. |
| Derived-table work from the earlier R8 note (persisted skill vector, multi-membership archetypes, `evaluate_trade` entry point) | R9–R12 scope; carried in the roadmap. |
| Live-API tests of anything | No key; none claimed. |
| `season_type` for the CSV importer's `totals` rows | They default to `regular` — correct (GP equals the regular-season row for all 573 players), recorded in the migration docstring. |

## 4. Known limitations

* Roster membership is observed-snapshot data, not transaction history; dates are first-seen times.
* The roster guard's thresholds (90 % / 50 %) are judgement calls, named constants in `ingestion/jobs.py`.
* `evaluation.py` (the trial harness) is 63 % line-covered — its probe bodies are exercised through
  synthetic worlds; several (P7, P8, P10–P12, P13) only partly.
* The consistency script and the tests agree because they share the new definitions. Independence
  comes from the before/after comparison in the validation report, not from the script.
* While working in the shared checkout the feature branch was live under the user's `--reload`
  backend; it returned HTTP 500 on `/season-stats` until `tradelab.db` was migrated. That migration was
  applied after a backup (`backend/tradelab.pre-r8.db`) — see the executive report.
