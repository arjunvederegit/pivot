# BALLDONTLIE GOAT — integration readiness and trial evaluation

*Sources: https://docs.balldontlie.io/ · https://www.balldontlie.io/openapi/nba.yml ·
https://www.balldontlie.io/terms.html — all fetched and read on **2026-10-18** (terms "Last
Updated: September 17, 2026"). Re-read them on the day you start the trial; this document records
what they said, not what they will say.*

## 0. Status in one paragraph

**The adapter and the evaluation harness are built and tested against synthetic fixtures. They have
never touched the live API — no key was available, no trial was started, no charge was authorised.**
Every statement below about what the provider *returns* is therefore "documented", not "observed".
`live_verified: false` is reported by `GET /api/v1/data/providers` until someone runs the trial.
Pivot works without BALLDONTLIE; nothing depends on it.

## 1. What the documentation actually says (verified from the pages)

| Item | Documented value |
| --- | --- |
| Plans | Free $0 (5 req/min) · ALL-STAR **$9.99**/mo ($95.90/yr, 60/min) · **GOAT $39.99/mo ($383.90/yr, 600/min)** · ALL-ACCESS $299.99/mo. Paid tiers are per sport (the NBA plan does not cover other sports). |
| Trial | "48-hour free trial" of GOAT, **5 requests/minute**, all endpoints. **A payment method is required at signup; no charge until the trial ends; cancel from the dashboard.** One trial per sport per account. The same key converts to 600/min on subscription. |
| Free tier | Teams, Players, Games only. |
| ALL-STAR adds | Game player stats, active players, player injuries. |
| GOAT adds | **Season averages, team season averages, game advanced stats, box scores, lineups, standings (per the tier table), contracts, contract aggregates, plays (play-by-play).** |
| Auth | `Authorization: <key>` header (no `Bearer`). |
| Pagination | Cursor (`meta.next_cursor`), `per_page` ≤ **100**, default 25. |
| Errors | 400, **401 = "you need an API key *or* your account tier does not have access"**, 404, 406, 429, 500, 503. |
| Data licence (terms §6) | May "use, copy, cache, store, archive, modify, combine, analyze, publish, display, distribute, sublicense" the Data, for personal or commercial purposes including **AI/ML training or outputs**. No attribution required. **Not** permitted: building a product that **competes with BALLDONTLIE**, or reselling Data in its original unmodified form. |
| What the terms do *not* give you | §1/§11: BALLDONTLIE "is not the original producer", data is "compiled and aggregated from … third-party sources", it does **not** claim to have cleared league, team, trademark or publicity rights, and the permission "is not legal clearance from any third party". §8: data is best-effort, "may not match official league statistics". |

**The consequence the owner should sit with:** BALLDONTLIE's licence to Pivot is unusually permissive
and clear, which is genuinely valuable. It does **not** move NBA.com's own terms out of the way for
the *underlying* statistics (NBA.com Terms of Use §9 — see [resource-research.md](resource-research.md)).
Whether subscribing resolves Pivot's NBA-IP exposure is a question for counsel, not a property of
this vendor. Do not treat "GOAT" as a licensing fix.

### Endpoints relevant to Pivot (from the OpenAPI document)

| Endpoint | Tier | Returns | Pivot use |
| --- | --- | --- | --- |
| `/teams`, `/players`, `/games` | Free | identity; games with `season`, `postseason`, scores | crosswalk; second source for the game log |
| `/players/active` | All-Star | active players | crosswalk |
| `/stats` | All-Star | per-game box lines (`period` 0 = full game) | R9 game-level features |
| `/player_injuries` | All-Star | status, description, return date | availability (Pivot has **no** injury source today) |
| `/standings` | GOAT | W/L, conference/division rank, home/road | cross-check |
| `/season_averages/{general\|clutch\|defense\|shooting}?type=…` | GOAT | per-player season stats object, per `season_type` (`regular/playoffs/ist/playin`) | R9/R10 features: clutch, defensive, shot-zone families Pivot does not have |
| `/team_season_averages/{category}?type=…` | GOAT | team general/clutch/shooting/**playtype**/**tracking**/hustle/shotdashboard | R11 needs; playtype and tracking are new |
| `/v2/stats/advanced` | GOAT | per-game: box advanced + tracking + hustle + matchup defence + usage (≈100 fields) | R9 defence, R10 archetypes |
| `/box_scores`, `/box_scores/live` | GOAT | box score for a date | not needed |
| `/lineups` | GOAT | **starting lineups and bench per game, "only available starting from the 2025 NBA season"** | **not five-man combinations** — see below |
| `/plays` | GOAT | play-by-play incl. `coordinate_x/y`, participants | shot locations (R10/R12) — heavy |
| `/contracts/teams`, `/contracts/players` | GOAT | per-season `cap_hit`, `base_salary`, `total_cash`, `rank` | payroll, Cap Lab |
| `/contracts/players/aggregate` | GOAT | `contract_type`, `contract_years`, `total_value`, `guaranteed_at_signing`, `total_guaranteed`, `signed_using`, `free_agent_year/status`, free-form `contract_notes` | contract type — currently NULL for every contract in Pivot |

Two things the advertised "lineups" do **not** do: they are starting-five/bench lists per game, not
lineup-combination minutes or net ratings, so GOAT does not reopen the lineup-aware fit model R6
declined on measurement. And there is **no** per-year guarantee, option or no-trade field in the
documented schemas; the aggregate's `contract_notes` is free-form and unspecified.

### Documentation inconsistencies that must be resolved live

1. **Path prefix.** The narrative docs show `https://api.balldontlie.io/v1/teams`; the OpenAPI
   document serves `/nba/v1/teams` (and a few endpoints under `/nba/v2/…`). Configurable via
   `BALLDONTLIE_API_PREFIX`; harness probe P1 will show which one the live API accepts.
2. **Array parameter spelling** is inconsistent in the OpenAPI document (`seasons[]` on `/games`,
   `player_ids` on `/season_averages/{type}`, `team_ids` on `/team_season_averages`). The client
   sends exactly what the document spells; a 400 here means the document is wrong, and is a finding.
3. **`/season_averages` aggregation is undocumented.** Per-game averages? Totals? The adapter marks
   `aggregation="unspecified"` and probe P5 decides it by reconciling against NBA.com per-game rows.
4. **`season_type` default.** On `/games` and `/stats`, omitting it returns *all non-preseason
   types* — playoff games in a "regular season" pull. The adapter always sends it, and rejects any
   returned row that contradicts the request.
5. **A 401 cannot distinguish a bad key from a plan restriction.** The client infers
   `tier_restricted` only after the same key has succeeded on another endpoint, and says that it is
   an inference.

## 2. What is built

`backend/app/integrations/balldontlie/`:

| File | Responsibility |
| --- | --- |
| `client.py` | server-side key (`SecretStr`; never logged, never in an exception, never in a browser response), pacing at 80 % of the plan's published rate (15 s on the trial), bounded retries honouring `Retry-After`, cursor pagination with a page cap and a repeated-cursor guard (a capped read says it is **truncated**) |
| `normalizers.py` | row models written from the OpenAPI schema → canonical records; a row failing its minimum contract is rejected with a reason, never repaired; blanks → `None`; height `"6-8"` → 80 in |
| `provider.py` | `teams, players, active_players, games, standings, player_season_stats, team_season_stats, injuries, team_contracts, player_contracts, contract_aggregate`, each returning a `ProviderResult` |
| `crosswalk.py` | links BDL ids to Pivot players/teams (`provider_entity_ids`): abbreviation + full name for teams; tiered name resolution with draft-year/number/height hints for players; ambiguous and unresolved reported, never linked |
| `evaluation.py` | the trial plan (§4), response cache, report writer |

CLI (all refuse under `ROSTERLAB_OFFLINE=1`, none runs without a key):

```bash
make balldontlie-plan TIER=trial          # cost and pacing of the trial plan — no requests
make link-balldontlie DRY=1               # preview the id crosswalk (free-tier endpoints only)
make balldontlie-evaluate                 # the trial plan; evidence → data/imports/balldontlie/eval-<time>/
make balldontlie-evaluate REPLAY=<dir>    # re-analyse recorded responses: zero requests
```

Tests: `tests/unit/test_balldontlie_adapter.py` (35) and `…_harness.py` (18) — auth header, key
hygiene, pagination, every error class, 429/Retry-After, pacing, truncation, 401 inference,
namesake separation, idempotent linking, conflict filing, the harness's comparison arithmetic, budget
refusal, tier skipping, replay with zero requests, no key in the evidence directory. **These verify
the code, not the provider.** The fixtures say so in every file (`"_fixture": "SYNTHETIC FIXTURE…"`) and
a test fails if one stops saying it.

## 3. Before you start the clock

The 48 hours start at signup, and the harness spends about 35 minutes of them. Preparation that
should not be done inside the window:

1. **Refresh Pivot's own data first.** It is 55–89 days stale. Comparing a live provider against a
   stale local snapshot manufactures disagreements. `make refresh DATASETS=rosters,standings,player-stats,team-stats,games`
   (needs NBA.com reachable; see the reliability note in the research). Then `make data-status` should
   show `fresh`.
2. **Load the contract snapshot if you have one** (`data/imports/contracts/` is empty). Without it
   probe P9b has nothing to compare against and reports `inconclusive`; P9 (coverage) still runs.
3. Put the key in `backend/.env` as `BALLDONTLIE_API_KEY=` and set `BALLDONTLIE_TIER=trial`. Never in
   a commit, a chat message, or the browser. `.env` is gitignored.
4. `make balldontlie-plan` and read the step list.
5. Decide who watches: the 2026-27 season starts 2026-10-20 — during the trial the provider's
   "current" season may be in flux, which is useful for freshness (P3/P11) and noisy for contracts.

## 4. The evaluation plan (what the harness runs)

Roughly **130 requests ≈ 35 minutes** on the trial tier, capped by `--budget` (default 200); every
response is cached on disk so a rerun costs nothing.

| Probe | Area | Question | Method | Proposed pass threshold |
| --- | --- | --- | --- | --- |
| P1 | identity | do 30 teams link? | abbreviation + full name against `teams` | 100 % |
| P2 | identity | do active players link, and how many are ambiguous? | tiered name + biography hints | ≥ 95 % linked; ambiguity and unresolved listed |
| P3 | games | does the 2025-26 regular season match nba_api's game log? | (date, home, away) match + score equality | ≥ 99 % matched |
| P3b | games | is `season_type` honoured? | playoff pull vs regular pull id overlap | 0 overlap |
| P4 | standings | do W–L records agree? | exact equality | all |
| P5 | stats | are season averages per-game, and do they agree with NBA.com? | PTS within 0.15 for players with ≥ 20 GP; infers per-game vs totals | ≥ 95 % agree |
| P6 | advanced | which NBA.com advanced fields have a counterpart, how full, how close? | alias map, fill rate, median/max abs difference | ≥ 90 % mean fill |
| P7 | advanced | what do clutch / defense / shooting / team playtype & tracking contain? | one page each: field lists, fill | informational |
| P8 | history | how far back does it go? | first page of 2024, 2022, 2019, 2016, 2013, 2010, 2005, 2000 | informational |
| P9 | contracts | what share of rostered players have a cap hit? | `contracts/teams` ×30 vs current rosters | ≥ 90 % |
| P9b | contracts | do salaries agree with the snapshot? | within 2 % | ≥ 90 % |
| P10 | contracts | what do aggregates actually supply? | fill of guarantee/type/free-agent fields; whether `contract_notes` mentions no-trade/option | informational |
| P11 | injuries | shape and fields | statuses, return-date fill | informational |
| P12 | raw | lineups, plays, advanced box | field lists and fill for 3 games | informational |
| P13 | errors | bad key, bad parameter | classification | 401 → `unauthorized`; 400 → `schema_error` |

Thresholds were fixed **before any live data**. They are proposals; change them in
`evaluation.THRESHOLDS`, not after seeing a result. Pass means *agreement with Pivot's existing
NBA.com-derived data*, not truth — the only accuracy test available without a second paid source.

### Representative cases

The harness draws samples from Pivot's own data (`select_sample_players`): 6 starters and 6 reserves
by minutes, 6 rookies, up to 6 players whose season-row team differs from their current roster team
(traded). Add by hand, in a second short session (≈ 20 requests), the cases that matter to you:
a two-way player, a player with a duplicated name (the Brandon Williams pair), a player traded
mid-season, a 2016-17 player (history), and one team in each conference.

### Manual checks the harness cannot do (≈ 30 minutes)

* **M1 freshness:** note the time a game finishes tonight, then when `/games` shows the final score and
  when `/stats` shows the box line. That is the real update latency.
* **M2 accuracy against a second source:** for 5 players, compare BDL `season_averages` to
  NBA.com's own player page and to one other site you are entitled to read.
* **M3 contract spot-check:** 10 contracts against a source you trust (the NBA's cap-year releases
  confirm league figures; player-level needs a human look at Spotrac/HoopsHype).
* **M4 plays:** open one game's `/plays` and check whether shot coordinates exist and are plausible.
* **M5 history:** ask for a 2016-17 season; note the oldest season with a non-empty answer.
* **M6 failure behaviour:** deliberately request an endpoint outside the free plan *before* upgrading
  (if you start on the free key) and record what the API does.

## 5. Objective subscription criteria

Subscribe **only if** all of A hold, and at least two of B. Otherwise stay on the free tier or on
the ALL-STAR tier for injuries.

**A — necessary**
1. P1–P2 identity pass (≥ 95 % of active players link, ambiguities small and explainable). Without a
   trustworthy crosswalk, nothing else is joinable.
2. P5: the season averages are documented-by-observation as per-game (or totals that Pivot can
   convert) **and** agree with NBA.com on ≥ 95 % of compared players.
3. Counsel confirms (or the owner accepts in writing) the position on NBA.com's underlying-statistics
   terms for a commercial product. This is not a technical test and it dominates the decision.

**B — what GOAT must *add* (each maps to a Pivot capability)**
1. P6/P7 deliver defensive, clutch or shot-zone fields Pivot does not have, filled for ≥ 90 % of
   rotation players (R9 defence, R10 archetypes).
2. P9 gives ≥ 90 % roster coverage of a cap year **and** P10 shows `contract_type` and guarantee
   fields that are actually filled (R2 contract rules; Cap Lab; today's data: none).
3. P11 supplies an injury feed with usable return dates (availability; Pivot has none).
4. P12 shows play-by-play with coordinates for the 2025 season (shot-location features).
5. P3/M1 show update latency inside a few hours and P3 matches ≥ 99 % of games (a credible second
   source if NBA.com blocks Pivot again — the October 2026 outage makes this concrete).

**Do not subscribe** because: the feature list is long; the price is low; or the trial "looked fine".
A failing A1–A2 means the data cannot be joined or trusted; a failing B-set means the incremental
value is under $39.99.

## 6. Cost scenarios (monthly, vendor-advertised)

| Stack | $/month | What you get | What you still lack |
| --- | --- | --- | --- |
| Current only (nba_api + free files) | 0 | season Base/Advanced stats, rosters, standings, games | contracts, injuries, tracking/clutch/defence families, a second source, **a clear commercial licence** |
| Current + free BALLDONTLIE | 0 | an independent teams/players/games source; crosswalk | everything GOAT-gated |
| Current + ALL-STAR | 9.99 | + game box lines, active players, injuries | season averages, advanced, contracts |
| **Current + GOAT** | **39.99** (383.90/yr) | + the table in §1 | licensed images; legality inputs; guarantees/options/no-trade; five-man lineups |
| GOAT + SportsDataIO quote / MySportsFeeds | 39.99 + (CAD 29 or quote) | a licence-oriented second vendor | — see the checklist |
| Future commercial licence | quote | enterprise rights | — |

The first dollar of marginal value is the **free** tier (an independent identity/game cross-check).
The next is **injuries** (ALL-STAR). GOAT's increment is contracts + advanced families, and it is only
worth $30 more than ALL-STAR if §5-B items 1 and 2 hold.

## 7. Expected limitations (hypotheses to test, not findings)

* Coverage will not be uniform: the terms disclaim completeness, and "advanced/tracking" fields are
  likely sparse for low-minute players and for seasons before tracking data existed.
* `/lineups` begins with the 2025 season; `/plays` volume per game is large and may be paginated or
  truncated.
* Contract fields will probably lack options/guarantee dates; do not infer them (Pivot does not).
* BALLDONTLIE is an aggregator: if its upstream is NBA.com, outages there may propagate.
* The 5 req/min trial cannot test sustained-load behaviour; the 600/min tier is untestable without paying.

## 8. After the trial

1. `make balldontlie-evaluate REPLAY=<dir>` to re-run the analysis with different thresholds, free.
2. Record the verdict (A and B, pass/fail, numbers) in this file under a dated heading.
3. If subscribing: cancel nothing; the same key converts. Then the follow-up is *persistence*
   (BDL records → statistics/contract tables behind the canonical layer) — scoped in
   [next-steps-roadmap.md](next-steps-roadmap.md).
4. If not subscribing: **cancel from the dashboard before the 48 hours end.** A payment method was
   required at signup.
