# R8.3 — Live BALLDONTLIE evaluation (observed 2026-10-18/19)

Everything in this file was **observed against `api.balldontlie.io`** unless it is labelled otherwise.
Evidence (raw responses, request ledger) lives in `data/imports/balldontlie/` — gitignored, not
redistributed. Re-analyse it with no request: `make balldontlie-evaluate REPLAY=<dir>`.

Status labels used throughout: **Live-tested** · **Fixture-tested** (behaviour of Pivot's code against
recorded/synthetic shapes) · **Unverified** (implemented or documented, never observed) · **Blocked** ·
**Out of scope**.

## 1. Authentication, entitlement and rate limit

| Check | Result | Label |
| --- | --- | --- |
| Key authenticates | `GET /teams` → 200 (89 rows) | Live-tested |
| Free-tier endpoints | `/teams`, `/players/active`, `/games` → 200 | Live-tested |
| ALL-STAR endpoint | `/player_injuries` → 200 (92 rows) | Live-tested |
| **GOAT-restricted endpoints** | `/season_averages/{general,clutch,defense,shooting,tracking,playtype,hustle,shotdashboard}`, `/team_season_averages/*`, `/standings`, `/contracts/teams`, `/contracts/players/aggregate`, `/lineups`, `/plays`, `/v2/stats/advanced`, `/stats` → all 200 | Live-tested |
| Invalid key | → 401 (`unauthorized`) | Live-tested |
| Malformed parameter | → 400 with a JSON error body (`schema_error`) | Live-tested |
| Rate limit | every 2xx response carries `x-ratelimit-limit: 600`; 84 requests ran in 16 s (~315/min) with **no 429** | Provider-reported + consistent with observation |

**Tier conclusion.** GOAT-class access is *established by observation*: every GOAT-gated endpoint tried
answered 200, and a 5-requests/minute trial could not have served 84 requests in 16 s. What was **not**
inspected is the account's billing state (no dashboard access); "600/min" is the provider's own header, not a
measurement. The client therefore paced at 600/min × 0.8 and never needed a retry. No 429 and no 5xx occurred.

The documentation's `401` ambiguity (bad key vs. endpoint outside the plan) never arose: the key succeeded on a
free endpoint first, so a `401` would have been classified `TIER_RESTRICTED` — none occurred.

**Credential handling.** The key was supplied as a gitignored `.env.local` (first in the shared project folder,
later inside the Pivot repository per the owner's instruction). It was read at runtime, one variable only, into
the process environment; it was never printed, logged, copied, or written to the R8.3 worktree. A search of the
Git history of the R8.2 and R8.3 worktrees found no `BALLDONTLIE_API_KEY=` line, and no env file is tracked in
either. (Mid-session the file moved between folders; authenticated work paused briefly and resumed after
the owner's clarification. No key was searched for outside the Pivot repository.)

## 2. Request budget (the guideline was ≈200)

| Segment | Requests | Notes |
| --- | --- | --- |
| Manual preflight (teams, injuries, contracts, season averages, standings) | 5 | before the harness |
| Harness run 1 (13 probes, as written in R8) | 84 | exposed the adapter defects of §3 |
| Everything after (ledger `request-ledger.jsonl`) | 59 | resumed harness, 5 extra stat families, depth probes, final pass |
| **Total** | **148** | 141 × 2xx, 7 × 4xx |

The seven non-2xx responses: 2 invalid category/type pairings (my adapter's fault, §3 #2), 1 single-game
`/lineups` call spelled without `[]` (my fault, §3 #7), and 4 *deliberate* error-behaviour probes (invalid key,
malformed parameter ×3). No response was a provider fault. Every response is cached by `(path, sent
parameters)`, so repeating any analysis costs nothing.

Pagination: cursor-based (`meta.next_cursor`), `per_page` ≤ 100, `prev_cursor` also returned. 13 pages of
regular-season `/games` and 6 of `/season_averages/general` were followed to exhaustion, with no repeated cursor and no
truncation. Row counts matched the documented totals (1,230 regular-season games; 582 player rows).

## 3. Adapter defects the live run exposed (all fixed; each has a test)

The R8 adapter was written from the OpenAPI document and passed 100 % of its synthetic fixtures. Against the
live API it failed 10 probes. None of these was visible without a live response.

| # | Fixture assumption (OpenAPI) | Live reality | Effect before the fix | Fix |
| --- | --- | --- | --- | --- |
| 1 | `/season_averages/*` rows have a flat `player_id` | rows nest a `player` object | every one of 582 rows rejected (P5, P6, P7, P8) | `BdlSeasonAverage` derives the id from `player.id` |
| 2 | `type=base` valid for every category | each category has its own types (`defense/overall`, `shooting/by_zone`, …); the OpenAPI leaves `type` a free string | HTTP 400 | published pairings encoded and checked **before** a request |
| 3 | `/teams` = the 30 franchises | 89 rows: 30 active + **59 defunct/historical** (blank conference) | team link rate read 29/89 | historical franchises skipped and counted separately |
| 4 | team name matches exactly | BDL "**LA** Clippers" vs NBA.com "**Los Angeles** Clippers" | LAC quarantined → *cascade*: 18 roster "disagreements", 87 unmatched games, standings 29/30, no LAC contracts | name key normalises the `LA`/`Los Angeles` spelling; abbreviation still required |
| 5 | `contract_notes` is an object | `null` or a **list of strings** | contract aggregates rejected | accepted verbatim as `{"lines": [...]}` |
| 6 | player carries a nested `team` everywhere | `/player_injuries`, `/lineups` carry only `team_id` | injuries lost their team | both accepted |
| 7 | arrays `player_ids`, `game_ids` | `player_ids[]`, `game_ids[]` (a single-id `game_ids` → 400 "must be an array") | wrong filters | brackets used; verified live |
| 8 | first-page samples are "truncated reads" | by design | 9 spurious "inconclusive" findings | `sample=True` is explicit; unintended caps still warn |
| 9 | (harness) cache keyed on `None` parameters | `None` is never sent | an unrelated refactor invalidated the cache | key ignores unsent parameters; old caches re-indexed |

A consequence worth stating plainly: **the first live run's headline (29/89 teams, 4 failures, 6 errors) was
almost entirely Pivot's own defects**, and after fixing them 16 probes pass, 1 fails, 14 are informational. A
vendor evaluation done only from the OpenAPI document would have blamed the vendor.

## 4. Final harness result (live responses, 2025-26 season, 2026-27 rosters)

`16 pass · 1 fail · 14 inconclusive (descriptive probes, by design) · 0 error`

| Probe | Result | Live evidence |
| --- | --- | --- |
| P1 team identity | **pass** | 30/30 active franchises → canonical teams (59 historical skipped, reported) |
| P2 active-player identity | **pass** | 524/526 linked (99.6 %); 0 ambiguous; **2 quarantined** (Hansen Yang — name order swapped; David Jones Garcia — undrafted, surname differs) |
| P2b roster agreement | **pass** | 524/524 linked players are on the same team in BALLDONTLIE and in Pivot's 2026-27 NBA.com snapshot (100 %) |
| P3 games | **pass** | 1,225/1,230 matched on (date, home, away), scores equal on all 1,225; the other 5 are **Pivot rows with no home team** (the known neutral-site/IST defect, R8.1 C4) |
| P3b season-type separation | **pass** | 85 playoff games, none also in the regular-season pull |
| P4 standings | **pass** | 30/30 W-L records equal |
| P5 season averages | **pass** | 392/392 players' PTS equal NBA.com per-game within 0.15 → aggregation is **per game** |
| P5b team averages | **pass** | 30/30 teams' PTS equal |
| P6 advanced fields | **pass** | 8/8 NBA.com advanced fields present; 436 players compared; **median and max absolute difference 0.0** on all eight |
| P7 other families (7 player, 5 team) | informational | see §6; all return full-league pages |
| P8 history | informational | rows for every sampled season back to 2000 (and 1995/1996, §6) |
| P9 contract coverage | **FAIL** | 434/526 (82.5 %) of 2026-27 roster players have a 2026-27 cap hit; threshold 90 % (§5) |
| P9b contract value agreement | inconclusive | there is no other contract source on file to compare with |
| P10 contract aggregates | informational | 16 contracts / 5 players sampled (§5) |
| P11 injuries | informational | 92 rows, 92/92 map to a 2026-27 roster player, 92/92 have a return date |
| P12 lineups / plays / v2 advanced | informational | §6 |
| P13/P13b error behaviour | **pass** | 401 / 400 as classified |

P9's threshold (0.90) was fixed before any live data was seen and is **not** adjusted here.

## 5. Contracts — what the provider actually returned (2026-27, all 30 teams)

`/contracts/teams` returns **one row per contract year** per team: `cap_hit`, `base_salary`, `total_cash`,
`rank`, nested `player`/`team`. Season encoding: **`season=2026` means 2026-27** (the season start year).

| Observation | Count |
| --- | --- |
| rows returned (30 requests, one per team) | 582 |
| rows with all three dollar figures | 454 (78 %) — every row with a cap hit also had base salary and cash |
| rows with **null `cap_hit`** | 128 (22 %): 70 carry only `total_cash` (**67 of them exactly $678,882**), 58 only `base_salary` ($1,357,763; $2,537,526; …) |
| rows where `cap_hit` ≠ `base_salary` (and ≠ `total_cash`) | 49 of 454 (11 %) |
| rows with `rank` 0 | 262 (the provider's salary rank is 0 for every low-salary row; not used) |
| team of contract row ≠ Pivot roster team | **0** |
| sum of cap hits | $5.857 B over 454 rows; per-team sums $132.9 M – $229.9 M (median $198.3 M) |
| roster players priced | **434 / 526 (82.5 %)**; no team is fully priced (best 16/18; worst 13/17) |

The 526-player roster, classified: **434** have a cap hit · **60** have a row but no cap hit · **30** are linked
but have no 2026-27 row at all · **2** have no identity link.

*Interpretation (a hypothesis, not a finding):* the rows without a cap hit are probably non-counting deals
(two-way, Exhibit 10, camp), which the provider prices in cash only — one figure, $678,882, accounts for 67 of the 128 null-cap-hit rows. Pivot's
roster is *provisional preseason* (16–20 per team), so some "uncovered" players may not be on a 15-man roster
at all. **The provider does not say, and nothing here infers it.** Until another source confirms, those players
stay *unpriced*, never $0 and never their cash figure.

`/contracts/players/aggregate` (one request **per player**): `contract_type` 100 % filled, `total_guaranteed`
100 %, `guaranteed_at_signing` and `signed_using` 94 %, `free_agent_year`/`_status` 81 %. Notes are **free text
lines** (`"2028-29: Player Option (deadline 6/29/28)"`, `"Trade Bonus: 10%"`, incentives, pay advancements);
options are *not* structured fields. **No no-trade clause, no guarantee dates, no cap-hold/exception detail.**
`contract_type` is an inconsistent free-text vocabulary (`Rookie`, `maximum`, `Maximum Veteran Extension`,
`rookie-maximum-extension`, …) — it is *not* Pivot's `standard/two_way/exhibit_10` and is not mapped to it.

Historical contract depth (1 team, 2 seasons): UTA 2018 → 15 rows, UTA 2024 → 15 rows, both with 0 null cap
hits. The null-cap-hit pattern is therefore a *preseason roster-boundary* phenomenon, not a general data hole.

## 6. Statistics

**Season averages, general/base** — per game, 582 rows (the whole 2025-26 league, including players no longer
on a roster), 32 fields after dropping provider ranks, 100 % filled. Equal to NBA.com's per-game numbers.
`team_count > 1` flags 72 players who played for several teams that season (aggregated across teams, as NBA.com
does).

**Families Pivot never had** (full-league pages for 2025-26 regular season; rows → fields kept; null share):

| Group | Rows | Fields | Fields > 5 % null | What it is | Aggregation |
| --- | --- | --- | --- | --- | --- |
| `general/advanced` | 582 | 39 | 0 | usage, TS%, eFG%, ratings (+ estimated ratings), PIE, pace, AST/REB %, possessions | season rates; **identical to NBA.com** where both exist |
| `clutch/base` | 492 | 32 | 0 | last-5-minutes production | unspecified |
| `defense/overall` | 582 | 7 | 0 | defended FGA/FGM, defended FG%, normal FG%, **% plus-minus** | unspecified |
| `shooting/by_zone` | 582 | 24 | 0 | FGA/FGM/% by zone (RA, paint, mid, corner 3, above-break 3, backcourt) | unspecified |
| `tracking/possessions` | 582 | 17 | 0 | touches, seconds/touch, dribbles/touch, paint/post/elbow touches and points per touch | unspecified |
| `hustle` | 581 | 24 | 0 | deflections, contested shots (2/3), charges, loose balls, box-outs | unspecified |
| team `tracking/passing`, `playtype/isolation`, `hustle`, `general/*`, `clutch/base` | 30 each | — | — | team-level families | unspecified |

"0 fields > 5 % null" is a statement about *presence*. **Sample size is a separate caveat**: 69 of the 570 mapped players
have fewer than 10 games; their per-game and rate figures are noisy and a model must weight or filter them.
`playtype` and `shotdashboard` (player level) returned a full first page but were not persisted.

**History (page-1 samples).** general/base: rows for 1995 (21 fields) and 1996+ (59 fields), 2000/2005/2010/
2013/2016/2019/2022/2024 all non-empty. general/advanced: 73 raw fields present in 2000, 2010, 2016. hustle:
**empty for 2014, present from 2015**. tracking/possessions: **empty for 2012, 2013, 2014** (its first
non-empty season was not located; 2025 works). shooting/by_zone: present in 2018. A first page proves
*existence*, not completeness — **verified complete coverage exists only for 2025-26 general/base, general/
advanced and the families in the table above.**

**Game level.** `/stats` (v1 box line) returns the documented fields. `/v2/stats/advanced` returns **121 fields
per player-game** (usage, four factors, tracking, hustle, matchup defence, contested-shot splits) for the one
game sampled. Not persisted in R8.3 (a season of games is ~25 k player-games ≈ 250 requests).

**Update timestamps: none.** No payload carries an `updated_at`; freshness can only be measured from Pivot's
own retrieval time. Latency (game end → data) was **not measured** — it needs a live game night.

## 7. Other intelligence data

* **Injuries** — 92 rows (64 *Day-To-Day*, 28 *Out*), nested player with `team_id`, free-text `description`,
  ISO `return_date` on all 92 (2026-10-20 … 2027-10-01). No report timestamp and no source, so staleness is
  unknowable. A player *absent* from the feed is not thereby healthy.
* **Standings** — 30/30 equal to NBA.com.
* **Lineups** — `starter: true/false` per player per game (25 rows; **two of the three requested games** had any — lineups exist only once a game has begun): *game* starters and bench, **not
  five-man combinations**. The documentation says 2025-season onward; the pre-2025 check was not completed (§2 #7).
* **Play-by-play** — 595 plays for one game; 258 shooting plays. **Event coordinates use a sentinel:**
  191 non-spatial events (substitutions, timeouts, free throws, …) carry `coordinate_x = -214748340`,
  `coordinate_y = -214748365` instead of null. All 202 field-goal attempts in the sample had plausible coordinates
  (small integers, e.g. `(7, 18)`, `(25, 2)`, `(48, 0)`). A consumer that does not filter the sentinel will compute nonsense — Pivot
  stores no play data in R8.3.
* **Box scores / leaders** — not exercised (no analytical need beyond `/stats`).
* **Betting, props, DFS** — Out of scope.

## 8. Limits of this evaluation

* One season (2025-26) was compared in depth; 2026-27 *statistics* do not exist yet (first game 2026-10-20).
* BALLDONTLIE's statistics are **not independent of NBA.com**: where both exist they are identical to the last
  digit (P5/P5b/P6). Agreement therefore validates the *pipe*, not the underlying numbers.
* The roster comparison is against Pivot's own *provisional* NBA.com snapshot; 100 % agreement says the two
  sources share a roster state today, not that either is the league's official roster.
* Contract *accuracy* was not checked against a third source (none is licensed or on file). Coverage and internal
  consistency were.
* Complete `/players` (historical identity) pagination (~50 pages) was **not** run; identity of non-active
  players was tested only through the biographies embedded in stat and contract rows (§ canonical-integration).
