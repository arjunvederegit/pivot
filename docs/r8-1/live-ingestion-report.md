# R8.1 — Live NBA ingestion report

*Run 2026-10-18 ≈ 19:00–19:30 UTC, from this machine, against NBA.com through `nba_api 1.11.4`, into a
**fresh isolated SQLite database** created for the purpose (migrated to head, then
`sync-teams`/`sync-players`). The owner's `tradelab.db` was never opened for writing (a read-only
`sqlite3` backup copy was used for comparison; its mtime, 2026-10-18 05:57, predates this session).*

## 0. Authorization, terms, and what this is and is not

The task authorised "a limited, representative" live test; R8's research records that NBA.com's
Terms of Use (§9) restrict statistics to private non-commercial use, so this is **private development
testing only** — about **113 requests** in total (counted from the calls made: 2 probes, 3 × 30
roster fetches, 2 standings, 12 player-stat calls (incl. two re-runs), 3 team-stat, 3 game-log + 1
neutral-site inspection) at the client's 0.75 s minimum interval, one concurrent pair at most. No endpoint was
blocked, throttled or retried; no anti-bot measure was met or bypassed. The `proxy`/header settings
were not used. Nothing from NBA.com was committed or redistributed; the database lives in the session's
scratch directory.

**This is real evidence about NBA.com on 2026-10-18 and about the canonical ingestion path. It is not
evidence about a provider that is down, rate-limiting or blocking** — none of those happened, so those
protections are fixture-verified only (§4).

## 1. What was run

All through the shipped CLI (`python -m app.cli refresh …`), i.e. the same `sync_*` jobs the worker runs:

| Command | Request count | Result |
| --- | ---: | --- |
| `refresh rosters --season 2025-26` | 30 | succeeded, **530** roster rows, 30/30 teams |
| `refresh rosters --season 2026-27` | 30 | succeeded, **526** rows, 30/30 teams |
| `refresh rosters --season 2026-27` (again) | 30 | `added 0, confirmed 526, closed 0` — **idempotent** |
| `refresh standings --season 2025-26` | 1 | 30 rows, `finalized` |
| `refresh standings --season 2026-27` | 1 | 30 rows, all **0-0** (NBA.com returns zero-record rows for an unplayed season; these are observed zeros, not substitutions) |
| `refresh player-stats --season 2025-26` | 3 (base, advanced, estimated) | 1,722 rows (574 × 3), `finalized`; two re-runs: `unchanged 1722` |
| `refresh player-stats --season 2026-27` | 1 | **empty answer → `succeeded`, `outcome: expected_empty`, 0 rows** |
| `refresh player-stats --season 2026-27 --season-type preseason` | 2 | 1,064 rows (532 × base/advanced) under `season_type='preseason'` |
| `refresh team-stats --season 2025-26` / `2026-27` | 2 / 1 | 60 rows `finalized` / **expected_empty** |
| `refresh games --season 2025-26` (×2) / `2026-27` | 1 / 1 | 1,230 games `finalized` / **expected_empty** |

## 2. What the live data showed

**Datasets after the run** (`make season-status`, isolated DB): rosters `fresh`; standings, player
stats, team stats, games for 2025-26 `finalized`; 2026-27 player/team statistics and games
`not_yet_available` with the reason *"2026-27 regular-season games begin 2026-10-20; no figures exist
yet, and none are substituted"*. Cross-tool consistency on this database: **1,705 checks, 0
disagreements** (see regression-report).

**Multiple teams / current rosters / movers / rookies (2025-26 → 2026-27 rosters, same database):**

| Fact | Count |
| --- | ---: |
| players on a 2026-27 roster who were not on a 2025-26 roster | 83 (72 with `years_experience = 0`) |
| players on a 2025-26 roster and no 2026-27 roster | 87 |
| players on **different teams** in the two seasons | **108** (e.g. Aaron Wiggins OKC→ATL, Andre Drummond PHI→NYK, Bobby Portis MIL→MIA) |
| 2026-27 rookies with a 2025-26 stat row | 4 of 83 new faces; the rest correctly absent — `missing`, not zero |
| new `players` rows created from roster rows (not in the static package) | 91 (5,103 → 5,194) |
| duplicate (player, team, season) roster rows | 0; players on two current teams: 0 |

**A completed season (2025-26) vs. the owner's earlier snapshot.** The owner's database holds 2025-26
base statistics retrieved 2026-08-24. Re-fetched live on 2026-10-18: **573 of 573 shared player rows are
identical** (the live set has one additional player). This is the empirical basis for calling a completed
season `finalized` — and for the re-verify-not-immutable stance: a correction *would* have shown up as
`rows.changed` in the run detail, and none did.

**The upcoming season.** NBA.com publishes 2026-27 rosters (526 players) and 2026-27 *preseason*
statistics (532 players) but **no 2026-27 regular-season statistics or games** — exactly the calendar's
expectation. This is the first time that distinction has been observed against the provider.

## 3. Defects the live run found (and what was done)

| # | Finding | Evidence | Disposition |
| --- | --- | --- | --- |
| L1 | **Neutral-site games lost a team.** NBA.com writes `"X @ Y"` on *both* rows of a neutral-site game (Mexico City, Paris, Berlin, NBA Cup semifinals). The normalizer's `"vs."` test found no home row, both teams landed in the *away* slot and the later row overwrote the earlier: one team vanished, the surviving score depended on row order, the home slot was empty. **5 of 1,230 games** in 2025-26 — and in the owner's existing database (and the surviving score is row-order dependent: the same game holds `away_score` 122 in the owner's database and 110 in the live one). | `games` rows with `home_team_id IS NULL`: 5 live, 5 owner copy | **Fixed** narrowly (`normalize_game_log`): both teams and each team's own score are kept; the "home" slot for such a game is the higher `TEAM_ID` (arbitrary but stable). Re-ran `refresh games`: 0 null-home, 0 null-score games. Test: `TestNeutralSiteGames`. Consumers of `games` are the data-quality check and the BALLDONTLIE game probe only. |
| L2 | A zero-row response was retried ×4, counted toward the circuit breaker, and recorded as a schema failure. Would have fired on every 2026-27 regular-season call before opening night. | read from `client.py`/`retry.py`; reproduced by test with 4 calls → 4 requests, circuit closed | **Fixed** (`ProviderEmptyError`, raised after the retry loop). Live: 3 expected-empty calls made one request each. |
| L3 | A roster response could contain the same player twice (two teams, if he moved during the 30 sequential fetches) and produce two current roster rows. | code reading + unit test (live data had none) | **Fixed** (`_dedupe_roster_rows`: later retrieval wins, counted in `run.detail`). |
| L4 | 24 stat rows (8 players × base/advanced/estimated) in 2025-26 have **no matching player** in Pivot's identity table and were silently skipped: Tyler Burton, Lucas Williamson, Adama Bal, Darius Brown II, Kadary Richmond, Lawson Lovering, Andersson Garcia, Toby Okani. | `run.detail.rows.skipped_unknown_player = 24` | **Made visible** (counted and named in the run detail). Not changed: creating identities from stat rows would change the player population every percentile is computed over — an analytical change out of scope. Listed in remaining-r8-work. |

## 4. Reliability protections — what is verified how

| Protection | Live | Fixture / unit | Notes |
| --- | :---: | :---: | --- |
| refresh is idempotent | ✔ rosters 2026-27 (`confirmed 526`), stats ×3 (`unchanged 1722`), games | ✔ | |
| empty answer for an unplayed season ≠ failure | ✔ (3 jobs) | ✔ | |
| empty answer for a played season is rejected, data kept | — | ✔ | needs a provider that misbehaves |
| failed request does not overwrite valid data | — | ✔ (`test_9…`, mutation M5) | no failure occurred live |
| implausible snapshot rejected (partial league, tiny first snapshot) | — | ✔ | first-snapshot guard is **new** in R8.1 (previously unchecked) |
| duplicate provider records handled | — | ✔ (same row twice; player on two teams) | none in the live responses |
| provider ids map consistently | ✔ | ✔ | NBA.com ids are native (`players.nba_player_id`, `teams.nba_team_id`); the crosswalk table is only for *other* providers (BALLDONTLIE). The same id served rosters, statistics and games with no unmatched team; 24 unmatched *player* rows in L4 |
| partial refresh failures stay visible | — | ✔ (`PartialRefreshError`, `refresh_failing`) | multi-season jobs keep what succeeded and fail the run naming both lists |
| success updates provenance and status | ✔ (`last_refresh`, `finalized`) | ✔ | |
| historical data preserved | ✔ (2025-26 untouched by the 2026-27 runs; roster history per season) | ✔ | |

## 5. Which pathways use the canonical layer now

| Path | Before R8.1 | R8.1 |
| --- | --- | --- |
| nba_api calls → jobs | raw dicts; every exception alike | `fetch_result()` → `ProviderResult` (`OK` / `EMPTY` / `UNAVAILABLE` / `RATE_LIMITED` / `SCHEMA_ERROR`) — the jobs act on the status; the original exception is re-raised unchanged |
| season and season type | strings; type implicit | `Season`/`SeasonType`/`StatScope` for state and calendar decisions; `season_type` passed to NBA.com for preseason |
| record types | BALLDONTLIE only | `canonical.py` converts nba_api rows to `TeamRecord`, `PlayerRecord` (roster hint), `StandingRecord`, `GameRecord`, `PlayerStatRecord`, `TeamStatRecord` (19 tests) — available to any consumer that wants one shape for both providers |
| what is *stored* | unchanged | **unchanged, byte-for-byte**: the raw `stats` JSON every validated analysis reads is not touched; the batteries are byte-identical |

Paths **not** moved: `stats_csv`, Kaggle, transactions and draft-pick importers (user-supplied or
scraped files with their own provenance), contracts. The CSV importer's default season (`2025-26`) is
deliberately a property of the file, now overridable on the command line.

## 6. Limitations

* One machine, one afternoon, one provider that was healthy. NBA.com was reported unreachable
  2026-10-07/10 (third-party report in R8); it answered every call here. Failure handling was not
  observed live.
* The roster fetch for 2025-26 still returns end-of-season rosters in October; whether NBA.com keeps
  that frozen or reflects summer moves under the old season key is unknown — the 2026-27 key is the
  reliable "now", which is why staging it matters.
* Trades *during* the 2026-27 season, 10-day/two-way churn, and a roster refreshed mid-trade were not
  exercised live.
* `PlayerEstimatedMetrics` (the third stat type) was fetched for 2025-26 only; its behaviour for an
  unplayed season is untested (the job treats it as optional enrichment).
