# R8.2 — Season-context architecture

*Code: `backend/app/canonical/calendar.py` (`SeasonContext`), `canonical/rosters.py` (`choose_roster_season`),
`canonical/tool_seasons.py` (the response block), `config/__init__.py` (the overrides). Tests:
`tests/unit/test_roster_season_split.py` (42), `tests/unit/test_roster_refresh_r82.py` (11).*

## 1. The defect this removes

One string, `Settings.current_season`, governed rosters, statistics, archetypes, impact estimates and team
needs. From 1 July until two weeks into the new season that string is the *last completed* season, so the
tools showed 2025-26 rosters in October 2026 although 2026-27 rosters were ingested and fresh
(R8.1 remaining-work **B1**). The two things have different clocks.

## 2. The four seasons

| Question | Name | Source of truth | Turns over | Default on 2026-10-18 |
| --- | --- | --- | --- | --- |
| Who is on the team? | **roster season** | league year (`SeasonContext.roster_season`); `ROSTER_SEASON` overrides | 1 July | **2026-27** |
| What did they do? | **performance season** | `Settings.current_season` (calendar `analysis_season`; `CURRENT_SEASON` pins) | 14 days into the new season | **2025-26** |
| What do they cost? | **contract season** | `CAP_LEAGUE_YEAR` (hand edit) | 1 July, by hand | **2026-27** |
| What is forecast? | **projection target** | league season; inputs are the performance season | 1 July | 2026-27 (input 2025-26) |

`Settings.current_season` keeps its meaning — the performance (statistics) season — so the ~60 existing
reads of it are unchanged. What changed is **which sites read the roster season**: team roster and
roster-derived pages, the player directory and player page membership, payroll, the CBA trade context
(who is on which team), the trade evaluator's rosters, the impact-reference "rostered" population, asset
coverage, contract matching, data-quality roster checks and `sync_rosters`' default.

Not changed, on purpose: `analytics/train.py`, `analytics/score.py` and `corpus`/history windows. They compute
*performance-season* artifacts (impact, archetypes, team needs) and read the performance-season roster, so a
re-run produces what R8.1 produced.

## 3. The pieces

* **`SeasonContext`** (pure calendar) gains `roster_season`, `performance_season`, `projection_target_season`
  and `roster_basis` (`offseason_snapshot` / `provisional_preseason` / `regular_season_snapshot`). All are
  derived from dates; nothing reads the database.
* **`choose_roster_season(db, season=None)`** — an explicit season is honoured (historical exploration is
  unchanged). Otherwise the preferred (league-year) season is used; **if no current snapshot exists for it yet,
  the most recent earlier snapshot is served and `fell_back` is set**, with a sentence saying so. One
  indexed query. This is the only way a roster can be held back, and it can never be held back by statistics.
* **`tool_seasons(db)`** builds the block every tool attaches as `season_context`:
  `roster {season, status, provisional, fell_back, snapshot_retrieved_at, …}`,
  `performance {season, state, is_baseline, behind_roster_season, next_rollover_date}`,
  `contract {season, state: available|missing}`, `projection {target_season, input_season,
  estimates_for_input_season}`, `pinned`, `notes`. It is additive: no existing field changed meaning.
* **`roster_verification(db)`** — coverage, team sizes, contradictions, movers vs the previous season,
  players without performance-season statistics, last refresh outcome, and an `unresolved` list.
  Printed by `make season-status` and after `make refresh DATASETS=rosters`; served at `GET /data/season-context`.
* **`EvaluationService.season_basis()`** — which season each evaluation input came from, plus
  `limitations[]` (§5). Attached to `/trades/evaluate`, `/intelligence/teams/{id}/profile` and each team's evaluation.

## 4. Configuration and overrides

| Setting | Effect |
| --- | --- |
| *(both blank, default)* | roster = league year; statistics = calendar rollover |
| `CURRENT_SEASON=2025-26` (the old `.env.example` default) | pins **statistics only**. After 3 Nov it falls behind the calendar (`pinned_behind_calendar` in `make season-status`) but **rosters stay on the league year** — a stale pin must not drag rosters back to last season |
| `ROSTER_SEASON=2026-27` | pins the roster season |
| both set to an older season | a deliberately historical session (backtest); nothing else is needed |

Both are validated (`"26-27"` raises). `make season-status` reports the *values* of the two season pins only (never any credential). The owner's real
`.env` was not opened, so whether it pins `CURRENT_SEASON` is unknown.

## 5. Service-level flow and what each tool reads

```
sync_rosters (league year, every 6 h)  ──►  rosters[season=2026-27, is_current]
                                                   │ choose_roster_season(db)
   /teams/{id}/roster ─ /players ─ /players/{id} ─ payroll ─ trade context ─ EvaluationService rosters
                                                   │
player_season_stats[2025-26] ─► impact estimates (model window 2023-24..2025-26) ─► TEI on every card
team_stats[2025-26] + 2025-26 roster ─► team_needs[2025-26] ──► labelled `needs_basis`, caps confidence
contract_years[season == CAP_LEAGUE_YEAR] ─► salary (None when absent; never another season's)
```

| Input to an evaluation | Season | Safe with a 2026-27 roster? |
| --- | --- | --- |
| Roster membership | roster season | yes — it is the point |
| Player impact (TEI), skills, role | performance-season estimates | **yes** — properties of the player, not the team; labelled `impact_estimate_seasons` |
| Rookies / no history | — | yes — excluded from the rotation and named (`unmodeled_players`), never zero |
| Team needs → `fit` | performance-season *team* | **limited** — they describe last season's team. Computed unchanged, labelled (`needs_season`, `needs_match_roster: false`), a clean evaluation's confidence is capped at *medium*. Not recomputed: that would be a new model |
| A player arriving who is on **no** current roster | roster season | **labelled** — `membership_warnings[]`, confidence capped at *medium*; the move is not refused (the builder's long-standing offseason-free-agent allowance) |
| Contract / assets | `CAP_LEAGUE_YEAR` | only where 2026-27 salaries exist (today: none → components excluded, as before) |
| After the 3 Nov rollover, before `make train && make score` | — | `limitations[performance]`: the model has no estimates for the new performance season and the evaluation says which season's it still reads |

## 6. Backward compatibility

* Every existing response field keeps its meaning (`season` = statistics season). New fields only:
  `roster_season`, `season_context`, `season_basis`, `stat_attribution`, `missing_reasons`, `needs_basis`,
  `impact.estimate_season`, and notes. Verified: 396 API responses from R8.1 and R8.2 code over identical
  databases differ only by those additions plus the explicit fallback note (validation-report §5).
* When roster and statistics seasons agree (a historical pin, or no 2026-27 snapshot yet) behaviour is
  **byte-identical** to R8.1 (three analytical batteries, validation-report §4).
* The old `prepare_league_season` job remains scheduled and is a no-op in the default configuration (the
  league year *is* the roster season); it only stages when a pin separates them.
* Roster runs now record `{season}` in `data_sync_runs.detail` *before* anything can fail, so a rejected or
  failed refresh is attributed to the right season. Runs from before R8.2 without it are attributed to the
  statistics season.
