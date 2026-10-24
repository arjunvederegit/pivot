# R8.3 — Data coverage matrix: Pivot's existing sources vs BALLDONTLIE GOAT

"Existing" = NBA.com via `nba_api` plus the user-supplied/Kaggle files already in Pivot. "GOAT" = what was
**observed** on 2026-10-18/19 (evidence in [live-api-evaluation.md](live-api-evaluation.md)).
Coverage vocabulary: **provider-advertised** (documentation says so) · **observed sample** (a page or a few
requests) · **verified complete** (the full league/season was read and checked) · **untested**.

| Category | Existing source | GOAT (observed) | Historical depth | Coverage status | Use for R9–R14 | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Franchise identity | `teams` (30) | 30 active + 59 historical rows, own ids | — | verified complete (30/30 linked) | crosswalk | Existing primary; GOAT cross-check |
| Active-player identity | `players` 5,194 | 526 active; **no NBA.com id in payload** | active only via `/players/active` | verified: 524/526 linked (2 quarantined) | crosswalk | Existing primary |
| Non-active identity | `players` | via bios embedded in stat/contract rows | — | linked +137 more; up to 44 left unresolved (mostly two-way/undrafted players Pivot has no identity for) | — | Existing primary |
| Current roster membership | NBA.com roster (526, provisional) | `/players/active` team = same team for 524/524 | none (current only) | verified agreement | R11, R12 | Existing primary; GOAT is a *second reading of the same state*, not independent |
| Roster **type** (standard / two-way / Exhibit 10) | not available | **not provided** | — | verified absent | CBA, roster rules | Unavailable from either |
| Games & scores | `games` 1,230 | 1,230 + 85 playoff, `ist_stage`, `postponed`, quarter scores | to 2000s (untested earlier) | verified for 2025-26: 1,225/1,230 equal; 5 Pivot rows lack a home team | R9 availability | Complementary; GOAT can repair Pivot's 5 neutral-site rows (not applied) |
| Standings | `standings` | 30/30 equal; adds home/road & conference records | 2025 tested | verified (one season) | R11 | Existing primary |
| Player season stats — base | `player_season_stats` base/totals (10 seasons) | per-game, 32 fields, 582 rows | **1995/96 →** (sample) | verified complete for 2025-26; equal to NBA.com | R9 | Existing primary (identical figures; GOAT adds nothing but a second copy) |
| Player season stats — advanced | advanced + estimated (8 fields compared) | 39 fields, 582 rows | 2000/2010/2016 sampled | verified complete 2025-26; **exactly equal** to NBA.com on 8 fields | R9 | Existing primary; GOAT adds ~30 more fields (ratios, `poss`, pace-per-40, estimated ratings) |
| Clutch | none | `clutch/base` 492 rows, 32 fields | unknown | verified complete 2025-26 | R9 (late-game role), R10 | **GOAT preferred** (only source) |
| Defensive tracking | none | `defense/overall` 582 rows: defended FG%, % plus-minus | unknown | verified complete 2025-26 | R9 defence | **GOAT preferred** — descriptive, not causal |
| Shooting zones | none | `shooting/by_zone` 582 rows, 24 fields | 2018 sampled | verified complete 2025-26 | R9, R10, R12 | **GOAT preferred** |
| Player tracking (touches, possessions) | none | `tracking/possessions` 582 rows, 17 fields; 11 other tracking types listed, untested | **not 2012–2014**; first non-empty season not located | verified complete 2025-26 | R10, R12 | **GOAT preferred** |
| Hustle | none | `hustle` 581 rows, 24 fields | **2015 →** (2014 empty) | verified complete 2025-26 | R9 defence/effort | **GOAT preferred** |
| Play types | none | player `playtype/isolation` page 1; team 30 rows; 10 other types untested | unknown | observed sample | R10 archetypes | Further validation |
| Shot dashboard | none | `shotdashboard/overall` page 1 | unknown | observed sample | R10 | Further validation |
| Team families (general, tracking, playtype, hustle, clutch) | `team_season_stats` base/advanced | 30 rows each; base PTS equal 30/30 | 2025 tested | verified for the families read | R11 | Existing primary + GOAT for tracking/playtype/hustle |
| Game-level player box | `player_game_stats` (0 rows) | `/stats` per player-game | untested beyond 2025 | observed sample (2 rows) | R9 availability, rest, variance | Further validation |
| Game-level advanced | none | `/v2/stats/advanced` 121 fields/player-game | 2025 sampled | observed sample (1 game) | R9 (matchup defence, usage by game) | Further validation — costly (~250 requests/season) |
| Starting lineups | none | `/lineups` starter/bench per game | doc: 2025 →; not verified | observed sample (2 games) | R11 starters | Further validation; **not** five-man units |
| Play-by-play | none | `/plays` 595 plays/game, with **sentinel coordinates** on non-spatial events | 2025 sampled | observed sample (1 game) | R10/R12 shot location | Further validation; sentinel must be filtered |
| Injuries (current) | none | 92 rows, status + return date + free text | current only | verified for the moment observed | R11 availability | **GOAT preferred** (only source); freshness unmeasured |
| Injury history | none | none | — | unavailable | R9 availability model | Unavailable until accumulated by Pivot's own refresh |
| Contracts per season | none (0 rows) | cap hit / base / cash per player-year | 2018, 2024 sampled (UTA) | verified for 2026-27: **82.5 % of roster priced** | Cap Center, trade matching | **GOAT preferred, partial** — see [contracts-and-cap-readiness.md](contracts-and-cap-readiness.md) |
| Contract length, guarantees, options | none | aggregate: years, total value, guarantees; options only as free text | historical contracts included | observed sample (16 contracts) | R13, R14 | Further validation (one request per player) |
| No-trade clauses, signing dates, exceptions, cap holds, two-way status | none | **not provided** | — | verified absent | trade legality | Unavailable from either |
| Cap parameters (cap, tax, aprons) | `league_cap_parameters` 2025-26, 2026-27 | not provided | — | present from official release | CBA | Existing primary |
| Update timestamps | `source_retrieved_at` (Pivot's own) | **none in any payload** | — | verified absent | freshness | Pivot's own clock only |

Reading the table: GOAT's advantage is **breadth** (families Pivot never had, contracts, injuries), not
**accuracy** — wherever both sources exist they are the same numbers. That is expected of an aggregator and it
means a GOAT outage or a GOAT/NBA.com disagreement is not independent evidence either way.
