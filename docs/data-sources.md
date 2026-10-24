# Data sources

## Primary (required): NBA.com via swar/nba_api

Package `nba_api==1.11.4` (pinned). Upstream services: `stats.nba.com` and
`cdn.nba.com`. All access flows through `backend/app/integrations/nba_api/` with
rate limiting (0.75 s min interval, concurrency 2), bounded retries with jitter, a
per-endpoint circuit breaker, response caching, and schema validation. Respectful
usage is a design constraint: no aggressive parallel scraping, polling only for
data the product displays.

### Endpoints in production use

| Endpoint | Purpose | Cadence |
| --- | --- | --- |
| `stats.static.teams` / `players` | identity records (package-bundled, no network) | each sync |
| `CommonTeamRoster` | current rosters + bio enrichment | 6 h (worker) |
| `LeagueStandingsV3` | standings (current + history seasons) | 6 h |
| `LeagueDashPlayerStats` (Base, Advanced) | player season stats — 3 modelled seasons, 10 for the comparable-trade corpus (R7) | daily |
| `PlayerEstimatedMetrics` | optional enrichment (documented fallback) | daily |
| `LeagueDashTeamStats` (Base, Advanced) | team season stats | daily |
| `LeagueGameLog` (team) | completed games; also the first and last regular-season game of each season, stored in `season_calendar` so a trade is described by a season that had actually been played (R7) | daily |
| `live.scoreboard/boxscore/playbyplay` | live data — **disabled by default** (`LIVE_DATA_ENABLED`); this build's network is edge-blocked by cdn.nba.com, which surfaces as a classified `blocked` provider error, honestly shown | in-season only |

Every ingested record stores: provider, upstream, endpoint class, source record ID,
request/retrieval timestamps, season, package version, and ingestion run ID.

### Contract tests

`integrations/nba_api/schemas.py` declares required datasets/columns per endpoint;
violations raise `ProviderSchemaError` before anything reaches the database. Live
contract verification is manual/scheduled only — ordinary CI never depends on
NBA.com availability (unit tests use recorded/synthetic fixtures).

## Secondary (optional): contracts

`nba_api` provides no contract data. Interface: `ContractProvider`
(`integrations/contracts/`). Bundled implementation: `file` — a user-imported CSV
the user lawfully possesses (schema + legal notes in
[data/contracts/README.md](../data/contracts/README.md)); rows failing validation
are rejected into `data_quality_issues`, never corrected. Contract-derived values
always display their own `source_name`/`source_date` and are never described as
NBA.com data. **No provider configured ⇒ salary features are unavailable, and
legality caps at `conditionally_valid`.**

## Secondary (optional): completed trades

`nba_api` publishes no transaction history. Source: **Basketball-Reference season
transaction pages**, `/leagues/NBA_<year>_transactions.html`, one page per season.

`make fetch-transactions FROM=2017 TO=2026` is the only fetcher in this repository, and
it exists because ten pages is too many to save by hand and they change as a season
advances. It reads its constraints from the source's own published policy rather than
assuming them: `robots.txt` allows `/leagues/` for `User-agent: *` and publishes
`Crawl-delay: 3`, so requests are **3.5 seconds apart**, one per season page, following
no links, with a user agent that names the project. A `provenance.json` sidecar records
each page's URL, HTTP status, byte count, SHA-256 and retrieval timestamp, so any parsed
row can be traced to the exact bytes it came from. Pages already present are not
re-requested.

Raw pages land in `data/imports/transactions/`, which is **gitignored in full** and never
redistributed. `make import-transactions` parses them into `historical_trades` /
`historical_trade_assets`; only normalized, attributable rows enter the database. On the
2016-17 … 2025-26 corpus that is 565 trades, 2,568 asset legs, 89.4 % of player legs
resolved, five unreadable asset phrases kept verbatim and filed as warnings, and nothing
fuzzy-matched.

The same `robots.txt` **disallows** `*/on-off/` and `*/lineups/`, which is one of the
measurements behind R6's decision not to build a lineup-aware fit model
(see [limitations.md](limitations.md)).

## Measured, never stored: lineup availability

`make lineup-availability` asks NBA.com `LeagueDashLineups` how many minutes two-, three-
and five-man groups actually played, and prints the standard error a net-rating estimate
would carry at each. It reads sample sizes and throws the rows away — there is no table
behind it, no ingestion path, and no NBA.com payload is retained. It exists so a
deferral made on measurement can be re-checked rather than believed.

## Not sourced (by design)

- **Injuries** — no provider bundled; availability uses historical games played
  only and is labeled as such.
- **Verified draft-pick ownership** — no authoritative provider; picks in trades
  are labeled hypotheticals; Stepien compliance is never certified.
- **Cap parameters** are not scraped: version-controlled YAML in
  `backend/app/config/cap_rules/`, each value carrying league year, effective date,
  source name/URL, and verification timestamp (2025-26 and 2026-27 verified against
  the NBA's official June announcements).

## Licensing posture

No NBA data is committed to this repository; everything is fetched at runtime by the
operator under NBA.com's terms. Test fixtures are small synthetic records clearly
marked as fixtures. Logs avoid wholesale provider payloads.

## User-imported and enrichment sources (RosterLab, July 2026)

| Source | Domain | Join key | Import |
| --- | --- | --- | --- |
| `data/imports/nba_player_stats_2026.csv` | 2025-26 season totals (573/582 imported; 9 unmatched ids recorded) | `PLAYER_ID` exact | `make import-stats-csv` |
| Kaggle `wyattowalsh/basketball` (nba.sqlite via kagglehub) | historical bio/draft enrichment — NULL-fill only, 273 conflicts preserved | `person_id == nba_player_id` | `make import-kaggle` ([setup](kaggle-setup.md)) |
| Basketball-Reference contracts page (user-downloaded snapshot) | salaries by league year + option markers | name (+ mapped team code), unmatched preserved | `CONTRACT_DATA_PROVIDER=bbref_snapshot` + `make sync-data` |
| `./nbalogos` + `./nbaplayerimages` (local, gitignored) | presentation only — logos + photos | abbreviation / resolved name→`PLAYER_ID` (confidence recorded) | `make index-assets` |

Precedence and conflict policy: see [identity-resolution.md](identity-resolution.md).
Nothing in these directories is committed or redistributed; the backend serves images
locally and Data Status reports coverage (30/30 logos, ~84% of rostered players with
photos as of the July 2026 index run).

## R8 additions (October 2026)

Provider-independent records, season/statistic definitions, identity crosswalk, freshness metadata and
the optional BALLDONTLIE adapter are described in [r8/architecture.md](r8/architecture.md). **Licensing:**
NBA.com's Terms of Use §9 restrict NBA statistics to private, non-commercial use absent consent — see
[r8/resource-research.md](r8/resource-research.md) before any public launch.

**R8.3 (October 2026):** BALLDONTLIE GOAT was exercised live (148 requests) and integrated behind the canonical
layer for 2026-27 contracts, injuries and the clutch/defence/shot-zone/tracking/hustle families. Where it overlaps
NBA.com it is numerically identical. What it covers, what it does not, and which provider serves which category:
[r8-3/data-coverage-matrix.md](r8-3/data-coverage-matrix.md), [r8-3/provider-strategy.md](r8-3/provider-strategy.md).
