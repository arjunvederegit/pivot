# R8 — Canonical data architecture

*Design principle: **external providers supply information; Pivot owns its definitions, validation
rules, provenance and analytical interfaces.** No provider's schema is a Pivot contract.*

## 1. Shape

```
 provider adapters (thin, replaceable)          Pivot-owned canonical layer  app/canonical/         consumers
 ─────────────────────────────────────          ────────────────────────────────────────────         ─────────
 integrations/nba_api      ─┐  dicts            seasons.py   Season · SeasonType · StatScope          api/v1/teams.py
 integrations/balldontlie  ─┼─►records.py  ───► stats.py     what a stored figure IS                  api/v1/players.py
   client · normalizers     │  ProviderResult   identity.py  resolve · crosswalk · aliases            services/evaluation
   provider · crosswalk     │                   rosters.py   "on a team now" + its age                (trade evaluator /
 integrations/contracts    ─┘                   stat_access  scope-explicit reads                     Strategy Lab)
                                                provenance.py SourceMeta: fresh? complete? observed?  services/payroll, cba/*
        │                                              ▲                                              analytics/*
        ▼                                              │                                              /data/freshness
 ingestion/jobs.py  (idempotent, guarded) ────────────►│ SQLAlchemy models (existing tables, additive)
```

Nothing in the canonical layer imports a provider. Providers import the canonical layer (to emit
its records) and nothing else of Pivot's. Consumers import the canonical layer and never a provider.
Ask Pivot is untouched: it reads a fictional corpus and no table in this document.

## 2. Canonical models

Where an adequate representation existed it was **strengthened, not replaced**.

| Entity | Representation | R8 change |
| --- | --- | --- |
| Player | `players` (UUID pk; `nba_player_id`; `is_active`; bio incl. draft year/round/number; provenance) | + `provider_entity_ids` crosswalk for every other provider; name resolution moved to `canonical/identity.py` with biography hints |
| Team | `teams` (UUID pk; `nba_team_id`; abbreviation; conference/division) | + one alias table (`BRK→BKN`, …) and a curated `FRANCHISE_ERAS` list (renames since 1995) |
| Season | label `"YYYY-YY"` on 12 tables (kept, deliberately) | `Season` value object: strict parse/validate, provider-year mapping, arithmetic. **Decision: no `seasons` table** — see §6 |
| Season type | none | `SeasonType` enum + `season_type` column on both statistics tables |
| Game | `games` (`game_date` = league calendar date) | convention documented; `game_date_from_utc()` converts a UTC instant to the Eastern league date |
| Roster membership | `rosters` (+ `valid_from/valid_to`, `is_current`), `player_team_history` | now an idempotent diff: `valid_from` = first observed on that team, `source_retrieved_at` = last confirmed, `valid_to` set when dropped; `current_roster_clause()` is the one definition |
| Player / team statistics | `player_season_stats`, `team_season_stats` (+ JSON `stats`) | + `season_type`; unique key `(entity, season, season_type, stat_type)`; `stat_type` now has a **registry** (aggregation, basis, minutes unit) |
| Contracts | `contracts` + `contract_years` (salary, guaranteed, options as the provider states) | schema unchanged. **No guarantees, options, cap holds or legality are inferred** (see §5) |
| Media | `media_assets` (match method + confidence) | + `usage_status` (default `unverified`), `license_ref`, `source_url`, `width`, `height` |
| Provenance | `ProvenanceMixin` on provider tables | + `SourceMeta` computed over any set of rows |

### Statistical definitions (`canonical/stats.py`)

| `stat_type` | Measure | Aggregation | Basis | `minutes` column is |
| --- | --- | --- | --- | --- |
| `base` | NBA.com Base | per game | observed | minutes **per game** |
| `advanced` | NBA.com Advanced | per game (ratings per 100 poss.) | observed | per game |
| `estimated` | NBA PlayerEstimatedMetrics | per game | **estimated** | per game |
| `totals` | user CSV | season totals | observed | minutes **for the season** |
| `csv_totals` | demo seed | season totals | observed, *synthetic* | season |

`spec_for()` raises for an unregistered type; `require_comparable()` refuses to combine per-game with
totals; `numeric()` makes absent/NaN/non-numeric `None`, **never 0**; `per_game_from_total(x, 0)` is
`None`, not 0; `minutes_per_game()` normalises the shared column.

### Seasons and dates (`canonical/seasons.py`)

A season is identified by the year it **starts** in and labelled `YYYY-YY`: 2025-26 is
`Season(2025)`. Providers' integer seasons (BALLDONTLIE `2025`) are start years. `SeasonType` is
`preseason | regular | play_in | playoffs | ist` with explicit NBA.com and BALLDONTLIE label maps.
`StatScope(season, season_type)` has no default for the type. Game dates are the Eastern-calendar
dates the league publishes (a 22:00 Pacific tip-off is 05:00 UTC next day).

## 3. Provider contract

`canonical/records.py` defines provider-independent records (`TeamRecord`, `PlayerRecord`,
`GameRecord`, `StandingRecord`, `PlayerStatRecord`, `TeamStatRecord`, `ContractYearRecord`,
`ContractAggregateRecord`, `InjuryRecord`). They carry the **provider's** id, never a Pivot id —
attaching one is the identity layer's job and can fail.

Every provider call returns a `ProviderResult`:

| Status | Meaning | Treated as data? |
| --- | --- | --- |
| `ok` | rows returned | yes |
| `empty` | the provider answered that nothing matches (also an HTTP 404) | **yes** — a real answer |
| `unavailable` | timeout, 5xx after retries, transport failure | **no** |
| `unauthorized` | no/invalid key | no |
| `tier_restricted` | key valid, endpoint outside the plan | no |
| `rate_limited` | 429 after bounded retries | no |
| `schema_error` | non-JSON, wrong shape, or every row rejected | no |
| `not_configured` | no credential; no request was made | no |

`result.require_data()` raises for the "no" rows and returns `()` for `empty`. That single
distinction — *no rows* vs *no answer* — is what keeps an outage from being written down as a fact.

The existing nba_api provider keeps its `NBADataProvider` protocol and `sync_*` jobs; its reliability
boundary (rate limit, retry, circuit breaker, schema contracts) is unchanged. Adding BALLDONTLIE or
any other provider means writing an adapter that emits canonical records — no tool changes.

## 4. Identity (`canonical/identity.py`)

1. **Canonical keys** — `nba_player_id` / `nba_team_id` (NBA.com), UUIDs internally.
2. **Provider crosswalk** — `provider_entity_ids(entity_type, provider, provider_id → internal_id,
   match_method, confidence, evidence)`, unique per provider id. `ProviderIdMap.link()` is idempotent;
   a link that disagrees with an existing one is a **conflict**: nothing is overwritten, a
   `provider_id_conflict` finding is filed, and `replace=True` is a deliberate manual act.
3. **Name resolution** — tiers `provider_id → nba_player_id → exact → unaccented → suffix-insensitive`,
   an ambiguity returned as an ambiguity (`status: resolved | ambiguous | unresolved`).
   `PlayerHints` (draft year/number, birth date, height) can *exclude* a namesake whose recorded
   facts contradict the source, or *confirm* one whose draft year/birth date agrees. If every
   candidate is contradicted the result is `conflicting_bio` (unresolved), never a pick. Height only
   conflicts at ≥ 3 inches (providers list heights up to two inches apart).

## 5. What Pivot deliberately does not infer

* No guarantee, option, no-trade clause, cap hold, exception or trade legality is ever derived from
  a salary row. `ContractYearRecord` keeps `cap_hit`, `base_salary` and `total_cash` apart and
  chooses none of them for a rule.
* Roster membership dates are *observation* dates (`basis: observed`), never transaction dates.
* A player on no roster is `unlisted`, not "free agent".
* A statistic with no row is `missing`, not zero.

## 6. Decisions and their reasons

| Decision | Reason |
| --- | --- |
| **No `seasons` table** (kept the label) | The R8 readiness note reached the same conclusion: a 12-table migration is real risk with no consumer that needs it. The `Season` value object gives the missing validation and conventions; a table can follow when R13 needs per-season attributes. |
| **`season_type` is a column, not a new `stat_type` value** | Playoff and regular-season figures have the *same* measure and aggregation; encoding the type in `stat_type` would multiply the registry and make "all regular-season rows" a pattern match. |
| **Roster sync diffs** instead of snapshot-per-run | Unchanged membership kept its identity and its first-seen date; growth went from O(runs) to O(changes). It changes a previously *tested* behaviour (`test_it_supersedes_the_previous_snapshot`), updated deliberately and recorded in the implementation report. |
| **BALLDONTLIE is an adapter + harness, not an ingestion path** | No live response has ever been observed by this code. Writing its rows into `player_season_stats` before the trial would be inventing a data path on faith. The adapter emits records; the harness compares them with Pivot's own data; persistence is a follow-up gated on the trial (see roadmap). |
| **Left the two name normalisers alone** (`ingestion` vs `assets`) | They differ on purpose (the asset matcher drops generational suffixes anywhere; the data matcher only trailing, ≥ 3 tokens). Merging them would change which photo folders match which players on real data; the benefit is cosmetic. Documented, not merged. |
| **No new infrastructure** | No queue, no service, no dependency. The new tables and columns live in the existing database via one additive Alembic revision. |

## 7. Freshness policy (`canonical/provenance.py`)

`fresh | stale | missing | unknown`, decided by the **oldest** contributing row against a per-dataset
window: rosters and standings `NBA_API_STALE_AFTER_SECONDS` (24 h, matching the 6-hour worker),
player/team stats and games `STATS_STALE_AFTER_SECONDS` (48 h — two missed daily refreshes, not one
late job), contracts `CONTRACTS_STALE_AFTER_SECONDS` (30 days, a user-supplied file). No rows →
`missing`. `completeness` is reported only against a stated expectation (30 teams; rostered players
with a stats row); otherwise `null`, never `1.0`.

## 8. Storage and caching

Unchanged engines (SQLite dev / Postgres prod, optional Redis, in-process TTL cache keyed by a data
version bumped on successful ingest). New: `provider_entity_ids`; `season_type` ×2; media rights ×5.
Migration `a9d4c6e18b53` is additive, backfills `season_type='regular'`, reverses cleanly, and was run
on a copy of the real 40 MB database (17,022 stat rows preserved) before it touched anything else.

## 9. Where it plugs in

| Consumer | Uses |
| --- | --- |
| `GET /teams/{id}/roster`, `/season-stats`, `/teams/{id}` | `current_roster`, `player_stat_rows`, `build_source_meta` |
| `GET /players`, `/{id}`, `/{id}/stats`, `/season-totals/{season}` | `current_team_of`, stat registry, `current_roster_clause` |
| Trade evaluator / Strategy Lab (`EvaluationService`) | `current_roster_clause` (query count unchanged: it is a tested budget) |
| Cap Lab payroll, CBA resolver, contract coverage, needs scorer, impact reference, training | `current_roster_clause` |
| `analytics/features.py`, `score.py`, `train.py` | explicit `season_type == 'regular'` |
| `GET /data/freshness`, `/data/providers`, `make data-status` | `dataset_freshness`, `provider_registry` |
