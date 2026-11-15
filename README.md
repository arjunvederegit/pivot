# Pivot — Basketball Intelligence for Better Decisions

**Understand a roster, find what it is missing, test a move, and read why — on live NBA data, with every number traced to how it was produced and what it cannot support.**

Pivot is a basketball decision-intelligence platform: real provider-backed NBA data, a CBA-aware rules engine with a four-state honesty standard, a transparent player-impact index with its out-of-time evaluation published (including where it falls short), and a broadcast-grade product layer over serious, inspectable analytics.

The product philosophy is one loop:

```
OBSERVE  →  DIAGNOSE  →  COMPARE  →  SIMULATE  →  RECOMMEND  →  EXPLAIN
```

![Pivot Command Center](docs/screenshots/overview.png)

> **Status:** a fully functional **local** build — not a deployed service. The R8–R9.3A work described below lives on unmerged feature branches (`main` is the pre-R8 baseline); see [Development status](#development-status-as-of-2026-10-21). Data below was ingested live from NBA.com (July 2026), enriched from a user CSV, the Kaggle basketball database, and local image datasets. **Not affiliated with the NBA.**
>
> Pivot was called **RosterLab** through its R1–R7 generation; those release reports are kept under their original names as the provenance record. A few infrastructure identifiers (the `tradelab-backend` distribution, the default `tradelab.db` filename, the `tradelab:` cache prefix, and the `TEI` acronym) deliberately keep their historical spelling — renaming them would move a database, invalidate a cache namespace, or change the meaning of a persisted column without changing anything a user sees. See [ADR-24](docs/decision-log.md).

---

## The decision workflow

| Step | Module | What it answers |
| --- | --- | --- |
| **1 · Understand the roster** | Teams | What does this team actually contain — rotation, roles, measured strengths, and the needs that follow? |
| **2 · Identify the edge** | Players | Who is out there, what do they do, and which of them answers a need this roster has? |
| **3 · Test the move** | Trade Evaluator | Two- and three-team deals with a live rules check, projected impact, fit, cost and risk |
| **4 · Make the decision** | Decision Board | The options side by side under your own priorities, with the trade-off frontier and rank stability |

## The modules

| Module | What it does | Status |
| --- | --- | --- |
| **Trade Evaluator** | The flagship. Two- and three-team deals across team workspaces joined by a transaction lane; drag-and-drop with accessible send controls, a live backend rules check, fan verdict then advanced analysis, save and share links | available |
| **Strategy Lab** | A decision board: visual deal cards, a component matrix, priority sliders that re-rank live, the trade-off frontier and rank stability | available |
| **Player Explorer** | 573 imported 2025-26 stat lines: photos, totals *and* derived per-game (never mixed), 2–4-player comparison, and league percentiles taken only over players whose sample can support one | available |
| **Team Outlook** | Broadcast-style team dashboard: roster by position group, model-derived strengths & needs, competitive window, payroll honesty, strategy setup | available |
| **Salary-Cap Center** | Payroll by season, top/expiring contracts, option markers, cap reference lines — activates when contracts are imported | needs contract import |
| **Precedent** | Ten seasons of completed trades, normalized locally. Ask what a proposal resembles and get back real transactions with a per-dimension similarity breakdown — and the statement that resemblance is not consequence | available |
| **Who fixes this?** | Start from a diagnosed need instead of a trade: filtered candidates, ranked by projected wins, each one already run through the trade evaluator with a balancing package | available |
| **Decision memo** | The whole analysis as one reviewable document — recommendation, rotation consequences, fit, cost, rules, precedent, risks, and one consolidated section naming what could not be established | available |
| **Contract Predictor** | Deliberately **on the roadmap**: needs historical contract data before a validated model can exist — Pivot ships no fake models | roadmap |

Routes were renamed to match these module names; every previous URL still redirects (share links keep their query strings).

## Data sources & hierarchy

Six sources, joined by **stable NBA IDs** with recorded confidence (see [docs/identity-resolution.md](docs/identity-resolution.md)); conflicts are logged, never silently overwritten:

1. **NBA.com via [`nba_api`](https://github.com/swar/nba_api)** (authoritative): teams, players, rosters, standings, stats, games — hardened client with rate limiting, retries, circuit breaker, schema contracts.
2. **User CSV** (`data/imports/nba_player_stats_2026.csv`): 2025-26 season **totals**, imported by official `PLAYER_ID` (573/582 matched; 9 unmatched recorded), per-game derived safely via GP.
3. **Kaggle [`wyattowalsh/basketball`](https://www.kaggle.com/datasets/wyattowalsh/basketball)** via `kagglehub`: historical bio/draft enrichment — fills only NULL fields (4,516 players enriched; 273 conflicts preserved un-overwritten).
4. **Basketball-Reference contracts snapshot** (user-downloaded page, parsed locally — no live scraping): salaries by league year with option markers; enables salary-matching rules and Cap Lab.
5. **Basketball-Reference season transaction pages** (`make fetch-transactions`, one request per season 3.5 s apart, honouring the source's published `Crawl-delay: 3`): **565 completed trades across 2016-17 … 2025-26**, 2,568 asset legs, 69 of them multi-team. 89.4 % of player legs resolve to a player here; the rest are filed as warnings and none is fuzzy-matched. Raw pages stay gitignored.
6. **`LeagueGameLog`, as a season calendar** (`make sync-season-calendar`): the first and last regular-season game of each of the ten seasons, so a completed trade is described by production that had actually been played when it was made. Ten rows. Without it the feature season falls back to the calendar month, which mis-describes draft-night, preseason and November-2020 trades — 57 of 565.
6. **Local image datasets** (gitignored): 30/30 team logos, 2,196/2,476 player-image folders resolved to identities by name→ID matching (280 unmatched kept for review); served by the backend with deterministic fallbacks.

**Data Status** shows six plain-language source cards (fresh / stale / derived / incomplete / unavailable) with coverage and the exact next step for anything missing.

## Honesty guarantees

- A trade is **never** labeled legal from partial validation: `passes / fails / incomplete (data missing) / not checked`.
- Missing salaries, injuries, or pick ownership are explicit unavailable states — never estimated.
- Season totals are never presented as per-game values; proxies are labeled as proxies.
- Every screen shows source + last-updated; every model shows its validation numbers.

## The analytics (inspectable, with the strength of evidence stated)

Not everything below is *empirically validated*, and a passing test suite is not validation. Each item is labelled by what backs it:
an **interpretable heuristic** (transparent construction, no outcome validation), a **fitted calibration** (a coefficient estimated from ingested data, valid only for how it was fitted), or a **consistency battery** (a command that exits non-zero when a stated threshold fails — it tests internal behaviour, not real-world outcomes). Candidate models from R9 are stored but not served as validated; see the [evidence vocabulary](docs/methodology.md#evidence-vocabulary).

- **TEI (estimated player impact)** — *interpretable heuristic, descriptive*. A hand-weighted composite of ten within-season box-score z-scores over a recency-weighted 3-season window; nothing is fitted. It is a unitless index (×2.5), **not** points per 100 possessions and **not** a causal estimate of a player's incremental impact. What the evidence shows: it beats raw last-season persistence on one forward transition (**0.645 vs 0.717 MAE**, n = 464), against a proxy target that is 60 % PIE (the index correlates **0.96** with same-season PIE); it tracks same-season team net rating (concurrent, not out-of-time, R² **0.751**, 90 team-seasons). The R9.1 out-of-time backtest then found it **indistinguishable from regressing last season toward the mean with an age curve** and **10.8 % worse** than a four-parameter history regression on the locked holdout (MAE 0.436 vs 0.394). Its per-player "likely range" (σ² = 0.0326 + 240.9/minutes) is **not calibrated**: the nominal 80 % band contained **57 %** of holdout outcomes (61 % in development). Differences smaller than the forecast error are noise. A ridge challenger was retired in R3-1: it won player-level MAE (0.637) yet explained **R² = 0.004** of team net rating. [Model card](docs/model-card-player-impact.md) · [R9.1 assessment](docs/r9-1/current-model-assessment.md).
- **Wins projection** — *fitted calibration, inherits TEI's limits*: 240-minute rotation reallocation with availability discounting; a net-rating→wins slope fitted on 90 team-seasons (2.24, R² = 0.95, cross-sectional, assuming context stays stable) and a TEI→net-rating conversion (≈15, change-on-change R² 0.62, n = 60); 2,000-draw Monte Carlo per evaluation, whose player inputs are TEI's uncalibrated bands.
- **Decision score** — *interpretable heuristic*: six user-weighted components (missing components excluded, weights renormalized) + Dirichlet rank-stability and tornado sensitivity. It is not a fitted or outcome-validated predictor.
- **CBA engine** — *rules implementation*: expanded/standard TPE bands (verified 2025-26 & 2026-27 figures), apron restrictions, aggregation prohibition, roster limits, recently-signed windows. Correctness rests on documented rules and unit tests for pass, fail and unavailable paths. [Rule-by-rule coverage](docs/cba-rule-coverage.md).
- **Comparable-trade retrieval** — *consistency battery*: an interpretable grouped distance over **1,151 rankable team-sides** from 535 completed trades (R7 widened the corpus window from three seasons to ten). Archetype precision@5 **0.917** against a 0.433 base rate, with a random ranker at 0.444 and a shuffled-feature corpus at 0.424; direction confusion **0.025** against a 0.05 ceiling; `make comparable-validation` re-runs it in about 70 s. These gates test that retrieval recovers archetype and direction, not that a resembling past trade predicts a deal's outcome — resemblance is not consequence.
- **Need-driven discovery** — *consistency battery*: filtered by the need, ranked by projected wins, then put through the trade evaluator under the candidate generator's own conditions — which takes the distinct players named across the league's top fives from **26 to 72**. That measures differentiation between needs, not recommendation quality. `make acquisition-validation`.

Full write-up: [docs/methodology.md](docs/methodology.md) · in-product: `/methodology` (plain-language layer + technical layer).

## Screenshots

| Trade Evaluator | Team Outlook |
| --- | --- |
| ![Trade Evaluator](docs/screenshots/trade-evaluator.png) | ![Team Outlook](docs/screenshots/team-outlook.png) |

| Strategy Lab | Data Health |
| --- | --- |
| ![Strategy Lab](docs/screenshots/strategy-lab.png) | ![Data Health](docs/screenshots/data-health.png) |

| Player Explorer | Saved deal report |
| --- | --- |
| ![Player Explorer](docs/screenshots/player-explorer.png) | ![Deal report](docs/screenshots/deal-report.png) |

## Getting started

> **Every command from `make migrate` onward writes to the database that `DATABASE_URL` names** — by default
> `backend/tradelab.db`, which on a long-lived checkout may be a hand-built, irreplaceable database. Know which
> database you are pointing at before you run anything past `make setup`, and prefer a **copy** for development.
> Read [Operating safely](#operating-safely-data-training-and-models) before running `make train` or `make score`.

```bash
make setup          # backend venv + frontend npm install + .env from template
make migrate        # Alembic (SQLite by default; Postgres via DATABASE_URL): changes that database's schema
make seed-config    # verified salary-cap parameters (2025-26 + 2026-27)
make sync-data      # live NBA data from NBA.com via nba_api (network): writes the ingested tables
make dev            # backend :8000 + frontend :3000
```

Training and scoring are deliberately not part of this sequence. On a **fresh, disposable database you just
created**, `make train && make score` is how the app obtains impact estimates and team needs. On any database that
already holds estimates you care about, they are destructive — see below.

### Optional data imports

Everything in this block also writes to the database `DATABASE_URL` names.

```bash
make index-assets      # player photos (./nbaplayerimages) + team logos (./nbalogos)
                       # Both folders are local and gitignored. Without them the app still works:
                       # teams show colour crests and players show initials/silhouettes. After adding
                       # or moving the folders, re-run this (or set ASSET_PLAYER_IMAGES_DIR / ASSET_LOGOS_DIR).
make import-stats-csv  # 2025-26 season totals (data/imports/nba_player_stats_2026.csv)
make import-kaggle     # Kaggle basketball DB enrichment (~700MB download; no auth for
                       # public datasets — see docs/kaggle-setup.md)
# Contracts: save basketball-reference.com/contracts/players.html to
# data/imports/contracts/players.html, set CONTRACT_DATA_PROVIDER=bbref_snapshot,
# then: make sync-data   (see data/imports/README.md)

make sync-corpus-stats                       # ten seasons of player stats, standings, calendars
make fetch-transactions FROM=2017 TO=2026   # completed trades, one page per season
make import-transactions                     # parse them into historical_trades
make transaction-coverage                    # what the corpus holds
```

`sync-corpus-stats` widens what the **comparable-trade corpus** can be described by. It
does not widen the modelling window: every served estimate stays fitted on
`HISTORY_SEASONS`, and `tests/unit/test_corpus_window_isolation.py` keeps the two apart.

Containerized: `docker compose up --build` (Postgres 16 + Redis 7 + API + worker + frontend).

### Testing

```bash
make test                     # backend pytest (coverage floor 85 %) + frontend vitest
make lint                     # ruff + mypy + eslint + tsc
make e2e                      # Playwright core flows against a dedicated demo database
make comparable-validation    # comparable-trade battery; exits non-zero on a threshold
make acquisition-validation   # need-driven discovery battery, over all 30 rosters
make lineup-availability      # re-measure whether lineup data could support a fit model
```

Visual QA is scripted too — `node scripts/visual_qa.mjs docs/qa/run` screenshots every
route at 1920/1440/1366/1280/1024/768/390 — 15 routes, 105 shots — and fails loudly on
horizontal overflow, console errors or an empty body. Its output is gitignored; the curated shots above live in
`docs/screenshots/`.

## Operating safely: data, training and models

Ingestion, training and scoring are **operations on a database**, not setup steps. Verified against
`Makefile`, `backend/app/cli.py` and `backend/app/analytics/` — the runbooks linked below hold the full detail.

| Command | What it does to the database `DATABASE_URL` names |
| --- | --- |
| `make migrate`, `make seed-config` | Alter the schema / load cap parameters. Rehearse migrations on a copy first. |
| `make sync-data` (`sync-all`) | Network. Upserts teams, players, rosters, standings, team and player statistics, games, the season calendar and contracts, then writes data-quality issue rows. A full roster refresh can rewrite stored bio fields — see the [R8.2 runbook](docs/r8-2/operational-runbook.md) §1. Seasons come from settings, not from an argument. |
| `make refresh`, `sync-corpus-stats`, `import-*`, `link-balldontlie`, `refresh-balldontlie` | Write ingested or imported rows; several also reach a third party. |
| **`make train`** (legacy) | Retires the active version of up to five model families (impact, archetype, TEI→net-rating, team projection, pick curve) and activates the new ones **in the same transaction**, with no evaluation step. Writes impact estimates and archetypes labelled with `Settings.current_season`. **Unless `ARGS=--preserve-history`, it permanently deletes every impact estimate of an inactive version** — re-activating an old version does not bring them back; only a backup does. |
| **`make score`** | Writes, updates and **deletes** `team_needs` rows for `Settings.current_season`. |
| `make train-candidate`, `make materialise-skills` | Write *candidate* models and estimates that no serving path reads, and prove that nothing else changed. Still write — run them on a copy. |
| `make backtest` | Read-only on the database; writes artifacts under `models/experiments/`. Its `holdout` stage spends the holdout (below). |
| `make activate-provider-data` | Dry run unless `APPLY=1`; an apply writes into `DATABASE_URL` after taking a backup. |
| `make purge-fixtures` | Lists test leftovers; deletes only with `APPLY=1`. |
| `make seed-demo`, `make e2e` | **Delete and recreate** `E2E_DB` (default `backend/rosterlab-e2e.db`), then train and score it. Never point `E2E_DB` at a real database. |
| `make data-status`, `make season-status`, `make candidates`, `make contract-coverage` | Read-only. Use these first. |

**Rules**

- **Never train or score against the owner's real database** without a separately approved operational procedure
  that includes a verified backup. Rehearse on a copy (`cp backend/tradelab.db <copy>`, then
  `DATABASE_URL=sqlite:///<copy> make …`).
- **Candidate training and activation are different operations.** `make train-candidate` never changes an active
  model. Nothing in the repository promotes a candidate: promotion is designed but not built — see
  [model versioning and safety](docs/r9-1/model-versioning-and-safety.md) and
  [model safety and promotion](docs/r9-2/model-safety-and-promotion.md).
- **Holdout evaluation follows the preregistered protocol.** Run `make backtest BT_STAGE=development` first; the
  `holdout` stage needs exactly one development run per target, records every read in `holdout_ledger.json`, and a
  re-read needs an explicit `--acknowledge-reread`. Never tune on the holdout.
  See [evaluation architecture](docs/r9-1/evaluation-architecture.md) and
  [`preregistration.json`](docs/r9-1/preregistration.json).
- **Verify the season settings before training or evaluating.** `make train` and `make score` use
  `Settings.current_season` — the pinned `CURRENT_SEASON` if set, otherwise the calendar's analysis season — and
  `make train` derives its three-season window from it. Run `make season-status` and read `pinned_behind_calendar`
  and the `seasons` block. **2026-11-03 is the documented statistics rollover checkpoint** (see
  [season-context architecture](docs/r8-2/season-context-architecture.md)); confirm the configuration actually in
  force rather than assuming it is right, and do not train or score across it without the
  [R8.2 operational runbook](docs/r8-2/operational-runbook.md) §5.

## Repository policy

No NBA data, images, or licensed datasets are committed — everything is fetched or imported locally by the operator with provenance. Test fixtures are tiny, clearly-marked synthetic records. See [data/README.md](data/README.md).

## Documentation

Start with the **[documentation index](docs/README.md)** (and [CLAUDE.md](CLAUDE.md) for AI-agent sessions). Direct links: [Pivot audit](docs/pivot/audit.md) · [Target architecture](docs/pivot/target-architecture.md) · [Gap analysis](docs/pivot/gap-analysis.md) · [R8 readiness](docs/pivot/r8-readiness.md) · [Architecture](docs/architecture.md) · [Methodology](docs/methodology.md) · [CBA coverage](docs/cba-rule-coverage.md) · [Data sources](docs/data-sources.md) · [Data dictionary](docs/data-dictionary.md) · [Identity resolution](docs/identity-resolution.md) · [Kaggle setup](docs/kaggle-setup.md) · [Decision log](docs/decision-log.md) · [Model cards](docs/model-card-player-impact.md) · [Limitations](docs/limitations.md) · [Demo script](docs/demo-script.md) · [Interview guide](docs/interview-guide.md) · [Enhancement plan (historical)](docs/rosterlab-enhancement-plan.md)

## Development status (as of 2026-10-21)

Evidence for every row is the milestone's own report under `docs/`; nothing here was re-run for this page. "Unmerged" means the work exists on a feature branch, not on `main`; nothing in this table is deployed to production.

| State | What |
| --- | --- |
| **Baseline (`main`)** | The pre-R8 Pivot redesign, with Ask Pivot V2 off by default. |
| **Unmerged lineage (linear)** | `feat/r8-canonical-data-foundation` → `r8-1-operational-validation` → `r8-2-activation-checkpoint` → `r8-3-goat-live-integration` → `r9-1-player-intelligence-foundation` → `r9-2-player-skill-engine` → `r9-3a-goat-operational-tools` → `nba-headshots`. All but the headshots branch have been pushed; none is merged. Verify with `git`, not with this list. |
| **Implemented and tested** | R8 canonical, provenance-tracked data layer · R8.1 live-validated ingestion and PostgreSQL 16 compatibility · R8.2 separate roster-season and statistics-season clocks · R8.3 BALLDONTLIE (GOAT) provider · R9.1 candidate-only training, rolling-origin backtest and preregistered-holdout infrastructure · R9.2 skill engine producing *experimental candidate* estimates (nothing promoted) · R9.3A Cap Center, contract-aware trade-legality assessment and a Player Explorer research preview. |
| **Test evidence (dated)** | R9.3A final QA, 2026-10-19: backend 3,327 passed / 0 failed, frontend 345 passed, PostgreSQL subset 193 passed ([R9.3A report](docs/r9-3a/R9-3A-EXECUTIVE-REPORT.md)). Earlier milestones report their own counts; none was re-run by the documentation waves. Tests establish behaviour, not model accuracy (see *The analytics*). |
| **Preview only** | R9.3A runs in an isolated preview (`make preview-up`, ports 8093/3093) over a *copy* of the data that includes GOAT contracts, injuries and stored R9.2 candidates. |
| **Activated** | One roster-only refresh (2026-27 rosters) was applied to the owner's development database on 2026-10-18 after a verified backup ([R8.2 activation report](docs/r8-2/activation-checkpoint-report.md)); cutting the running app over is the owner's step. **R9.3A provider-data activation on the owner's database has not been performed** (rehearsed on a copy; [procedure](docs/r9-3a/local-preview-and-activation.md)). No active model has been retrained, promoted or activated by R8.2–R9.3A. |
| **Development-gated** | Official NBA headshots (`feat/nba-headshots`, development builds only) until image-rights clearance — [details](docs/headshots/NBA-HEADSHOTS.md). |
| **Ask Pivot** | A separate, loopback-only, off-by-default demonstration over a *fictional* league ([ADR-30](docs/decision-log.md)). It does not read Pivot's NBA data; a real-data assistant is not built. |
| **Known honesty gaps** | TEI's evidence and limits: *The analytics* above and [the model card](docs/model-card-player-impact.md). |

## Roadmap

Future work is described by the R8–R9 roadmap documents ([R8 next steps](docs/r8/next-steps-roadmap.md), [R9 vision](docs/r9-1/R9-VISION-AND-ROADMAP.md), [R9.3B and R10 recommendations](docs/r9-3a/R9-3B-AND-R10-RECOMMENDATIONS.md)). These are plans, not commitments, and none is built beyond the starting points noted.

| | Planned capability | Starting point in the repository |
| --- | --- | --- |
| **R10** | Archetypes | 14 rule-based functional roles ship; multi-membership does not |
| **R11** | Roster needs | roster profile and needs ship; scarcity and complementarity do not |
| **R12** | Player–team fit | a one-way fit baseline ships; counterfactual fit does not |
| **R13** | Comparable trades | retrieval over 565 completed trades ships; richer contract and pick context does not |
| **R14** | Transaction optimization | an experimental, hidden candidate generator and the legality engine exist; the legality inputs it needs do not |

Separately, a **GM Copilot / real-data Ask Pivot** is a future capability that needs the above to be trustworthy. Older documents numbered it R14 (and called R13 a "scenario engine"): [gap-analysis](docs/pivot/gap-analysis.md) (2026-08-28), the [target architecture](docs/pivot/target-architecture.md) and ADR-28/30. The R8–R9 roadmap documents (2026-10-18/19) number R14 as the transaction optimizer. No document records the renumbering, so the README keeps the Copilot unnumbered until the owner decides.

Earlier ideas — Salary Predictor (once historical contract data exists), Free Agency Planner,
Draft Fit, Rotation Builder, Extension Simulator — remain honest "not built" states in the
product, never fake functionality.

## Disclaimer

Independent portfolio project; not affiliated with or endorsed by the NBA or NBPA. NBA data is retrieved at runtime under the source sites' terms and is not redistributed. Team names/logos and player images belong to their owners and are used locally for identification. This is an analytical simulator, not professional cap-management or investment advice.
