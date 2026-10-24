# R8.3 — BALLDONTLIE GOAT live integration: executive report

**Status: Partially complete.** The live connection, the evaluation, the adapter repair, the canonical
ingestion pathway and the cross-tool checks are done and evidenced. Contract coverage fell short of its own
threshold (82.5 % vs 90 %), PostgreSQL and a game-night latency test were not possible, and nothing was activated
on your real database (by instruction). Detail and evidence: [live-api-evaluation.md](live-api-evaluation.md).

## The seven questions

**Was the live connection successful?** Yes. The key authenticated and **every GOAT-gated endpoint tried returned
200** (season averages in 8 categories, team averages, standings, contracts, contract aggregates, lineups,
play-by-play, advanced game stats, injuries). **148 live requests**, 7 non-2xx (all self-inflicted or deliberate),
no 429, no 5xx.

**What access level was verified?** GOAT-class access *by observation*: the provider reports
`x-ratelimit-limit: 600`, and 84 requests in 16 s are impossible on a 5/min trial. Billing state itself was not
inspected, and 600/min is the provider's statement, not a measurement.

**Which data categories were tested?** Teams, active players, rosters, games, standings, player and team season
statistics (base, advanced, clutch, defence, shooting zones, tracking, hustle, play types, shot dashboard),
history samples back to 1995, contracts (per-year and aggregate), injuries, lineups, play-by-play, game-level
advanced. Betting/DFS: out of scope.

**What was integrated?** Into an *isolated copy* of your development database, through Pivot's canonical layer,
idempotently and non-destructively: **2026-27 contracts for 453 players** (434 on the rosters), **92 injuries**,
**4,055 statistic snapshots** (7 groups, 2025-26 regular season), and **691 verified identity links** (30 teams, 661
players). Run history, provenance and quarantines are recorded.

**What actually improved in Pivot?** For the first time Pivot has (1) 2026-27 salaries — a lower-bound payroll for all 30
teams, (2) an injury feed, (3) defence, shot-zone, tracking, hustle and clutch families it never had, (4) an
independent identity/games/standings cross-check (and a repair source for 5 game rows missing a home team), and
(5) a provider adapter that works against the real API. **Equally important, real data exposed two latent defects in
Pivot's own contract display** (every contract looked "expiring"; years-remaining read 1) — fixed and tested.

**What remains incomplete?** 17.5 % of rostered players have no cap hit (probably non-counting deals — unverified);
no team has a verified payroll; contract length, options and guarantees are not loaded (one request per player;
needs your approval); no no-trade clause, signing dates, exceptions or roster type exist in the provider; the
history of the new families is sampled, not verified; update latency was not measured; PostgreSQL was not run.

**Is GOAT worth $39.99/month?** **Keep it for now; re-decide about 2026-11-15.** By the R8 criteria written before
any live data: identity ≥ 95 % ✔ (99.6 %), per-game agreement ≥ 95 % ✔ (100 %), plus the added-value tests —
defensive/clutch/shot-zone fields ✔, injury feed with return dates ✔, play-by-play with coordinates ✔ (sentinel
caveat) — hold; contracts ≥ 90 % ✘ (82.5 %). The honest caveats: its statistics are **numerically identical to
NBA.com's** (so it adds *breadth*, not accuracy or independence), the licensing question is open, and its value is
concentrated in contracts, injuries and the new families. If you only want injuries, ALL-STAR ($9.99) is enough.

**Are we ready to begin R9?** Yes for **R9.1** (rolling-origin backtest harness and candidate-only training, on
data Pivot already has). The new families need a one-time historical backfill (≈ 260 requests, your approval) and
an append-only snapshot history before they can be tested honestly. See [R9-DATA-READINESS.md](R9-DATA-READINESS.md).

## Findings worth your attention

1. **The first live run failed 10 probes, almost all because of Pivot's own adapter** (written from the OpenAPI
   document): nested `player` objects, per-category `type` values, 89 teams instead of 30, "LA" vs "Los Angeles"
   Clippers (which cascaded into 18 false roster disagreements and 87 unmatched games). After repair: 16 pass,
   1 fail (contract coverage), 14 informational.
2. **GOAT and NBA.com are the same numbers.** 392/392 players' points per game equal; the 8 advanced fields have
   a maximum difference of 0.0. Agreement validates the pipe, not the numbers.
3. **Provider data hazards to remember:** play-by-play non-shot events carry a sentinel coordinate
   (−214748340); `contract_type` is free-text; `contract_notes` is free-text; no payload has an update time.
4. **Pivot's own defect surfaced:** 5 of 1,230 games have no home team (the known neutral-site problem); GOAT has them.
5. **Training is still destructive** (`make train` deletes the previous model's estimates). R9.1 must fix that first.

## Analytical integrity

Verified by row-level hashes: all 22 model, estimate, scenario, trade, statistic, roster and identity tables are
**identical** between your database (opened read-only), a baseline copy and the ingested copy. 181 of 218 API
endpoints changed — **only** contract-derived fields. No model, coefficient, estimate or saved scenario was
touched; `make train`/`make score` were never run; trade-legality results are identical with and without the data.

## Tests

Backend **2,765 passed · 57 skipped · 0 failed** (coverage 91.7 %, floor 85); `ruff` and `mypy` clean; frontend
**308 passed**, `tsc` clean, production build OK; Alembic drift check passes. Skips are optional assistant
packages and PostgreSQL-only tests. **Not verified:** PostgreSQL, Playwright, Ask Pivot, a game-night refresh.

## Where things are

`docs/r8-3/`: this report · [live-api-evaluation.md](live-api-evaluation.md) ·
[data-coverage-matrix.md](data-coverage-matrix.md) · [canonical-integration-report.md](canonical-integration-report.md) ·
[contracts-and-cap-readiness.md](contracts-and-cap-readiness.md) · [provider-strategy.md](provider-strategy.md) ·
[R9-DATA-READINESS.md](R9-DATA-READINESS.md) · [owner-next-steps.md](owner-next-steps.md).
Branch `feat/r8-3-goat-live-integration`, worktree `/Users/arjunvedere/pivot-r8-3-worktree`.
