# R8.3 — Provider strategy

Principle (R8): providers feed adapters; adapters feed the canonical layer; tools read only the canonical
layer. A provider is chosen **per category on observed reliability and usefulness**, never wholesale.

| Category | Primary | Secondary / cross-check | Decision (one of: BALLDONTLIE preferred · existing retained · complementary · further validation · unavailable) | Why |
| --- | --- | --- | --- | --- |
| Team & player identity | NBA.com (`nba_api`) | GOAT ids via `provider_entity_ids` | **existing retained** | GOAT's payload carries no NBA.com id; the link is Pivot's own work (524/526) |
| Current rosters | NBA.com | GOAT `/players/active` | **existing retained**; GOAT as a drift alarm | same state, shared upstream; do not auto-overwrite (instruction) |
| Roster type (two-way, Exhibit 10) | — | — | **unavailable** | neither source supplies it |
| Games, scores, standings | NBA.com | GOAT | **complementary** | 99.6 % equal; GOAT is the fallback if NBA.com blocks Pivot (it did in Oct 2026) and the repair source for Pivot's 5 null-home rows |
| Season statistics (base, advanced) | NBA.com | GOAT | **existing retained** | identical numbers; no gain from switching, some risk |
| Clutch, defence, shooting zones, tracking, hustle | GOAT | — | **BALLDONTLIE preferred** | the only source; verified complete for 2025-26 |
| Play types, shot dashboard, 11 tracking types, game-level advanced | GOAT | — | **further validation** | read on one page / one game only |
| Lineups, play-by-play | GOAT | — | **further validation** | shape known; completeness and coordinate quality are not |
| Injuries | GOAT | — | **BALLDONTLIE preferred** | the only source; 92/92 mapped; freshness unmeasured |
| Season contracts (cap hit, base, cash) | GOAT | a second source for the unpriced ~18 % | **BALLDONTLIE preferred, partial** | the only source; coverage 82.5 %, internally consistent, third-source accuracy unchecked |
| Contract terms (years, guarantees, options) | GOAT aggregates | Pivot's transaction ledger (future) | **further validation** | per-player endpoint; options are free text |
| No-trade clauses, exceptions, cap holds, signing dates | — | — | **unavailable** until a stronger/other source (ledger or licensed feed) | not in GOAT |
| Cap parameters | official NBA releases (YAML) | — | **existing retained** | primary-source |

## What GOAT does *not* settle

* **Licensing.** The BALLDONTLIE terms are permissive for BALLDONTLIE's own rights, but its data is
  aggregated from NBA.com and others, and the terms say that is not clearance for third-party rights
  (docs/r8/balldontlie-goat-evaluation.md §1). Observed identity of the numbers with NBA.com makes this sharper,
  not softer. Counsel, not engineering, decides it.
* **Independence.** Two sources that agree to the last digit are one source for validation purposes.
* **Dependence risk.** Contracts and injuries are single-sourced from a vendor whose upstream is undisclosed.
  Pivot's tools already withhold conclusions on partial coverage; keep it that way, and keep an exit: all GOAT
  data lands in Pivot-owned tables with provenance, so cancelling stops *refresh*, not *existence*.

## Operating model (no unattended ingestion is configured by R8.3)

| Dataset | Cadence to recommend | Why | Command |
| --- | --- | --- | --- |
| Identity crosswalk | weekly, and after any roster refresh that adds players | new signings/rookies need links; idempotent, ~7 requests | `make refresh-balldontlie WHAT=identities` |
| Injuries | every 3–6 h in season, hourly on game days; daily in the offseason | volatile; ~1 request | `… WHAT=injuries` |
| Contracts, current league year | daily in season; twice weekly in the offseason; **extra run after the trade deadline, after 1 July, after the cut-down** | signings, waivers, extensions change it; 30 requests | `… WHAT=contracts` |
| Current-season statistics groups | daily in season (after games settle) | rates move with every game; ~6 requests/group | `… WHAT=stats STAT_SEASON=2026-27` |
| Completed seasons (any provider data) | **once**, then frozen | a finished season does not change (allow one re-pull in July for corrections) | `… WHAT=stats STAT_SEASON=2025-26` |
| Lineups / play-by-play / game-level advanced | do not schedule yet | volume (hundreds of requests/season) and unverified completeness | — |

Everything refresh-related records a `data_sync_runs` row (`sync_provider_identities`, `sync_contracts`,
`sync_injuries`, `sync_provider_stats`) with `outcome: complete|partial`, rows inserted/updated/unchanged, request
count, unmapped/quarantined players and — for contracts — roster-side coverage. `GET /api/v1/data/providers`
reports BALLDONTLIE's linked and stored row counts; `GET /api/v1/data-health` shows contract coverage and refresh
health through the existing freshness policy. 600 requests/minute is ample: a full daily refresh of everything
above is roughly 50–110 requests depending on how many statistic groups are kept.
