# R8 — Current data infrastructure audit

*Written 2026-10-18 against `main` @ `ccaf0b8` ("pre-R8 baseline"). Every finding is tagged **CONFIRMED**
(reproduced by running code or querying the real development database), **CODE-VERIFIED** (read in
the source, behaviour certain, not observed to misbehave on the data in hand) or **SUSPECTED**
(plausible, not demonstrated). The audit changed nothing; fixes are in
[implementation-report.md](implementation-report.md).*

## 1. Scope and method

Traced every data dependency of the intelligence tools — Team Outlook, Player Explorer / player page,
Strategy Lab and the trade evaluator, Cap Lab, Ask Pivot — from provider to endpoint. Evidence came
from (a) reading `backend/app`, (b) querying `backend/tradelab.db` (a 40 MB SQLite copy of real
NBA.com-derived data: 5,121 players, 530 rostered, 30 teams, 17,022 stat rows), (c) small
experiments run against the ingestion code with a fake provider, and (d) the project's own
`docs/pivot/*` R8 material. The existing R8 readiness note
([docs/pivot/r8-readiness.md](../pivot/r8-readiness.md)) lists derived-table work (skill vectors,
multi-membership archetypes, an evaluation entry point). This assignment defines R8 as the
*canonical data foundation*; the two overlap only in provenance and constraints, and the derived-table
items are carried forward in [next-steps-roadmap.md](next-steps-roadmap.md), not done here.

## 2. Architecture inventory

```
 providers                     ingestion                 storage (SQLAlchemy 2)        read paths
 ─────────                     ─────────                 ──────────────────────        ──────────
 nba_api  ──► integrations/    ingestion/jobs.py   ──►   teams, players,               api/v1/teams.py
 (NBA.com)    nba_api/         sync_* (idempotent        rosters, player_team_history  api/v1/players.py
              client.py        upserts, DataSyncRun)     player_season_stats           services/evaluation.py
              (rate limit,                               team_season_stats             services/payroll.py
              retry, breaker,                            standings, games              cba/resolver.py
              schema check)                              season_calendar               analytics/{features,train,score}
 BBRef HTML ─► integrations/   sync_contracts       ──►  contracts, contract_years     services/data_health.py
 (user file)  contracts/                                 (empty on the dev DB)
 BBRef trans.─► ingestion/     import-transactions  ──►  historical_trades(+assets)    services/comparables.py
 (user files) transactions/                                                            services/acquisition.py
 user CSV  ──► ingestion/      import-stats-csv     ──►  player_season_stats           api/v1/players.py
              stats_csv.py                               (stat_type="totals")           /season-totals
 Kaggle    ──► integrations/   import-kaggle        ──►  players (NULL-fill only)
 local dirs ─► assets/indexer  index-assets         ──►  media_assets                  api/v1/assets.py
 YAML      ──► config/cap_rules seed-config         ──►  league_cap_parameters         cba/*
 (Ask Pivot reads none of the above: it runs on a fictional corpus, `assistant_rag/`.)
```

* **Backend:** FastAPI, SQLAlchemy 2, Alembic (8 migrations before R8), APScheduler worker
  (rosters/standings every 6 h; stats, games, contracts daily), optional Redis, in-process TTL cache
  keyed by a data version bumped after each successful ingest.
* **Frontend:** Next.js 16 App Router, TanStack Query. Talks only to `/api/v1`. No frontend file
  changed in R8.
* **Reliability boundary for NBA.com** (`integrations/nba_api/client.py`): rate limiter (0.75 s,
  concurrency 2), bounded retries with jitter, per-endpoint circuit breaker, response cache, required-
  column schema contracts that also reject a zero-row response, classified errors, health metrics.
  This is good and R8 left it alone.
* **Provenance:** a `ProvenanceMixin` (provider, source record id, retrieved-at, valid-from/to,
  ingestion run) on 17 of 35 tables, and a single provider→upstream-name map (`core/provenance.py`)
  that already fixed the earlier "demo data labelled NBA.com" defect.

### Data sources actually connected

| Source | Used for | Notes |
| --- | --- | --- |
| NBA.com via `nba_api==1.11.4` | identity, rosters, standings, player/team season stats (Base, Advanced, estimated metrics), game log | The only live provider. Regular season only (the endpoints are called without `SeasonType`). |
| Basketball-Reference snapshot (user file) | contracts (per-year salary, option colours) | **Not loaded** on the dev DB (`contracts` = 0 rows). |
| Basketball-Reference transaction pages | 565 historical trades | Fetched politely by the repo's own fetcher; gitignored. |
| User CSV | 2025-26 season totals (573 rows) | `stat_type="totals"`, `source_provider="user_import_csv"`. |
| Kaggle `wyattowalsh/basketball` | bio/draft enrichment, NULL-fill | 273 conflicts preserved, not overwritten. |
| Local folders `nbalogos/`, `nbaplayerimages/` | logos and headshots | `nbaplayerimages` is a **symlink into `~/Downloads`**; provenance and licence unknown. |
| YAML | cap parameters 2025-26, 2026-27 | Values match the NBA's own June releases (checked in the research). |
| Static / mocked | demo league (`demo_seed`), Ask Pivot's fictional corpus | Quarantined behind dedicated databases and guarded provenance labels. |

Independent re-fetching of the same data by different tools: none found — every consumer reads the
database. The duplication is in *how each consumer asks* (§4), not in what it fetches.

## 3. Analytical dependencies (what R8 must support, not rebuild)

| Tool | Reads | Kind of computation |
| --- | --- | --- |
| Team Outlook | roster, standings, team stats, TEI, archetypes, needs | transformations + percentile rules |
| Player Explorer / page | season totals CSV, base/advanced stats, TEI | percentile ranks, per-game derivation |
| TEI (player impact) | `features.py` over every player-season row | fixed-weight z-score composite, a regression for the net-rating mapping |
| Roles / archetypes | features | deterministic rule chain |
| Needs / fit | team + roster aggregates | percentile rules, one-way fit |
| Trade evaluator / Strategy Lab | rosters, skills, contracts, CBA rules | heuristic composite, Monte-Carlo uncertainty, 4-state legality engine |
| Comparable trades, acquisition targets | historical trades + corpus-season stats | grouped distance retrieval |
| Ask Pivot | its own fictional corpus | LLM over retrieved claims |

None of these needed new data in R8; they need *the same facts* to mean the same thing everywhere.

## 4. Findings

### 4.1 Confirmed

| # | Finding | Evidence |
| --- | --- | --- |
| **C1** | **Staleness is invisible on the data-bearing endpoints.** The dev database's rosters were 89 days old, standings and player statistics 55 days, team stats and games 89 days — *every* dataset stale — yet `/teams/{id}/roster`, `/season-stats` and `/players/{id}` returned only a timestamp string. The only freshness logic was a 24 h check on standings in `validate_data`, and `/data-health` judged a table by its *newest* row, so a table that was 29 teams current and one team months old read as fresh. | `GET /api/v1/data/freshness` on the migrated dev DB: all five NBA datasets `stale`, contracts `missing`. |
| **C2** | **A statistic's identity omitted what it aggregates.** `player_season_stats.stat_type` mixed measure (`base`/`advanced`), provenance (`estimated` is NBA's model, not a count), aggregation (`totals` is a season sum) and source (only the CSV writes `totals`). Concretely the `minutes` column holds **34.8** for one player's `base` row and **2,265.0** for his `totals` row — minutes per game and minutes for the season in one column with no marker. The unique key `(player, season, stat_type)` also made it impossible to store a playoff row beside a regular-season one, and nothing recorded which a row was. | `select … where full_name='Nikola Jokić'`; `models.py` unique constraint `uq_pss`. |
| **C3** | **Roster sync is not idempotent.** Three syncs of an unchanged two-player roster left **6** `player_team_history` rows instead of 2, and superseded-and-reinserted every `rosters` row each run. At the worker's six-hour cadence over 530 players that is ≈1.5 million rows a year of identical membership. `valid_from` was also reset every run, so it could never mean "first seen on this team". | experiment with the repo's own `FakeProvider`; worker schedule in `app/worker.py`. |
| **C4** | **An empty provider answer erases the league.** `sync_rosters` marked every current row superseded *before* looking at the response, so a provider that answered `[]` left zero current rosters. nba_api itself rejects a zero-row response, which hid this for the one provider that exists; any other provider would not be protected. The other season-scoped jobs likewise recorded an empty answer as a *successful* run that wrote nothing, so a dataset could go stale with every run green. | experiment: `sync_rosters(FakeProvider(rosters=[]))` → 0 current rows. |
| **C5** | **"Who is on a team now" was written 20 times.** `RosterEntry.season == <season> AND is_current` was hand-copied across 11 modules (team page ×3, player directory ×3, trade evaluator ×2, payroll, CBA resolver ×2, needs scorer, impact reference, data quality ×2, contract coverage ×2, asset coverage ×2). Two copies had **no season filter** (`analytics/train.py`, `assets/indexer.py::coverage`), correct only while exactly one season has ever been synced — which stops being true when 2026-27 rosters are synced. | `grep -rn is_current backend/app`. |
| **C6** | **No crosswalk exists for any provider but NBA.com.** The only external ids are `players.nba_player_id` / `teams.nba_team_id`. BALLDONTLIE's player payload carries **no NBA.com id** (read from its OpenAPI document), so integrating it needs an identity link that can be ambiguous — 38 lowercase player names are duplicated in the 5,121-player table. | `nba.yml` `NBAPlayer` schema; duplicate-name query. |
| **C7** | **Team-code aliases were copy-pasted three times** (`BRK→BKN, CHO→CHA, PHO→PHX` in the transaction importer *and* the contract provider; `phl/uth` in the logo indexer), and player-name normalisation exists in three differently-behaving forms (`ingestion/identity.py`, `assets/indexer.py`, Ask Pivot's own). | grep. Aliases consolidated; the name normalisers were deliberately left (§5). |
| **C8** | **Media files carry no rights state.** `media_assets` records *how a file was matched to a player* (method, confidence) but nothing about whether Pivot may show it; the asset routes serve any indexed file. The photo folder is a symlink into `~/Downloads`; provenance is unrecorded. | `MediaAsset` model; `ls -l pivot/nbaplayerimages`. |
| **C9** | **Naming drift between real and demo data**: the demo seeder writes `stat_type="csv_totals"`, the real importer `"totals"`. Harmless today because the totals endpoint hardcodes its filter, but an unregistered type is exactly what a future consumer mishandles. | `demo_seed.py:288` vs `stats_csv.py:279`. |

### 4.2 Code-verified, not observed misbehaving

| # | Finding | Why it did not bite yet |
| --- | --- | --- |
| **V1** | `/players/season-totals/{season}` labelled each row with the player's **current** roster team (`team_abbr`), not the team the totals were accumulated with. For a past season or a player traded since, production is attributed to the wrong team. | On the dev DB all 525 comparable base rows agree with the July roster snapshot, because the snapshot predates the moves. After the next roster sync it will not. |
| **V2** | `contracts` has no natural key (nothing stops two headers for one player and source); payroll reads the first. | `contracts` is empty on the dev DB. |
| **V3** | The current season is a single environment string (`CURRENT_SEASON`) edited by hand, and `is_current` means "latest snapshot *of its season*", so after the 2026-27 sync two seasons both have `is_current` rows. Anything that reads `is_current` without a season (C5) then mixes them. | The 2026-27 tip-off is 2026-10-20 (third-party schedule); no 2026-27 roster has been synced. |

### 4.3 Suspected

* **S1 — upstream fragility.** `nba_api` issue tracker reports every stats.nba.com endpoint returning an
  Akamai HTML page 2026-10-07/10 and recurring cloud-IP blocks. Pivot's classified-error handling
  turns this into a visible failure (good) but there is no second provider to fall back on.
* **S2 — licence exposure.** Not a code defect, but the audit's most consequential finding: NBA.com's
  Terms of Use §9 (read in full from the primary page, last updated 2026-07-13) limit NBA statistics to
  "legitimate news reporting or private, non-commercial purposes" and forbid use with a "database …
  of comprehensive, regularly updated statistics" without consent. Basketball-Reference's terms
  prohibit AI use of their content. See [resource-research.md](resource-research.md).
* **S3** — `Standing.conference_rank` and `playoff_rank` are both written from NBA.com's `PlayoffRank`
  (which *is* a conference rank), so they are the same number under two names. Not a defect; noted so
  nobody builds on a distinction that does not exist.

### 4.4 Cross-tool agreement on today's data

The three tools in the Team Overview → Player Evaluation → Strategy Lab workflow **did agree** on the
real snapshot: 1,705 membership/statistics comparisons across all 30 teams and 530 players found no
disagreement (`scripts/r8_consistency.py`). The audit's honest conclusion is therefore *not* "the tools
contradict each other" — it is that they agreed **by coincidence of one snapshot**: the same predicate
copied twenty times, no statement of what the numbers are, and no sign of age. C1, C2, C5 and V1 are
the ways that agreement would have broken.

## 5. Identity-integrity observations (real cases)

* **Traded players** — a player's season row carries the *last* team (`TEAM_ID`); the roster carries the
  current team. They match for all 525 rostered players with rows today; V1 is the exposure.
* **Rookies** — 5 rostered players have no `base` row. Correctly absent; the endpoints now say so
  (`missing_player_ids`) instead of leaving the caller to infer it.
* **Inactive / unrostered** — 48 players have 2025-26 `base` rows and no current roster entry.
* **Duplicate names** — 38 duplicated lowercase names (e.g. two Brandon Williamses, ids 1585 and
  1630314). The resolver already refuses to guess (R2a); R8 extends it with biography hints.
* **Regular season vs playoffs** — only regular-season data exists today (game log runs 2025-10-21 →
  2026-04-12; no playoff rows). The risk was structural (C2), not a present contamination.
* **Season labels** — `YYYY-YY` throughout, validated only by a SQL `LIKE`.
* **Source timestamps** — present on every provider row; interpreted nowhere (C1).

## 6. Baseline (before any edit)

| Check | Result |
| --- | --- |
| Backend `pytest --cov=app --cov-fail-under=85` | **2,453 passed, 5 skipped**, coverage 92 %, 50.6 s |
| Skips | 2 live-Ollama, 1 Ollama retrieval, 1 RealGM snapshot absent, 1 no `tei_to_net_rating` model — all environmental |
| Frontend `vitest --run` | 301 passed (20 files) |
| Frontend `tsc --noEmit` | clean |
| Branch/worktree | clean `main`; branch `feat/r8-canonical-data-foundation` created from it |
| Preexisting failures | none |

## 7. Prioritised remediation (what R8 did about each)

| Priority | Finding | R8 action |
| --- | --- | --- |
| 1 | C4 empty answer erases data | plausibility guard + `SnapshotRejectedError`; empty season answers fail the run |
| 1 | C1 hidden staleness | `SourceMeta` freshness on every data-bearing payload; `/data/freshness`; stale-dataset quality check |
| 1 | C2 statistic identity | `season_type` column, widened unique key, stat-definition registry, scope-explicit accessor |
| 2 | C3 non-idempotent roster sync | diff-based sync, history upsert |
| 2 | C5 / V3 duplicated roster definition | one predicate (`current_roster_clause`) used by all 20 sites; `train.py` and asset coverage fixed to be season-aware |
| 2 | C6 identity crosswalk | `provider_entity_ids`, hint-assisted resolution |
| 3 | V1 totals team attribution | additive `season_team_abbr` beside `team_abbr` |
| 3 | C8 media rights | `usage_status` (default `unverified`), licence/source/dimension columns, coverage warning |
| 3 | C7, C9 | team aliases consolidated; `csv_totals` registered; name normalisers left, see implementation report |
| 4 | V2 contract natural key | data-quality check only (no constraint on data not yet loaded) |
| 4 | S1, S2 | resource research and owner checklist; no code answer exists for a licence |
