# Decision log (ADRs)

## ADR-1 · Provider adapters instead of direct nba_api calls

**Context:** NBA.com endpoints throttle aggressively, drift schemas, and block
non-browser TLS fingerprints; during this build stats.nba.com hung indefinitely on a
slightly non-standard header set.
**Decision:** every NBA.com call flows through one client
(`integrations/nba_api/client.py`) with rate limiting, retries + jitter, a circuit
breaker, caching, schema validation, and health metrics; domain code sees only
normalized records behind the `NBADataProvider` protocol.
**Alternatives:** direct endpoint calls per feature (rejected: reliability logic
would smear across the codebase); a third-party stats API (rejected: the spec and
the portfolio value both hinge on the canonical free source).
**Consequences:** provider swaps and contract tests are trivial; one extra layer of
indirection.

## ADR-2 · Four-state legality instead of legal/illegal

**Context:** without contract data most salary rules cannot be verified; a binary
answer would either lie or make the product useless.
**Decision:** `verified_legal / verified_illegal / conditionally_valid /
not_evaluated`, derived mechanically from rule results; partial validation can never
produce "legal".
**Consequences:** the default no-provider install shows `conditionally_valid` at
best — visibly honest, and the UI explains exactly which data is missing.

## ADR-3 · Component framework rather than a single model score

**Context:** trades trade off incommensurable objectives; a single score hides the
disagreement between them and can't adapt to strategy.
**Decision:** six 0–100 components with user weights, raw calculations attached,
missing components excluded with weight renormalization.
**Alternatives:** one learned "trade quality" model (rejected: no ground truth for
"good trade", unexplainable, overfit bait).
**Consequences:** more surface area to explain, which is the point.

## ADR-4 · Uncertainty and sensitivity as first-class outputs

**Context:** box-score impact models have real error bars; weight choices are
subjective.
**Decision:** every evaluation ships Monte Carlo win distributions and every
comparison ships Dirichlet rank-stability + tornado analysis.
**Consequences:** ~0.5–1 s extra compute per evaluation; recommendations can be
called robust or fragile with evidence.

## ADR-5 · Constrained candidate generation

**Context:** unconstrained trade search produces absurd deals and combinatorial
explosion.
**Decision:** beam search bounded by package sizes, TEI-gap plausibility filters, an
evaluation budget, and a counterparty utility floor; outputs labeled as model
exploration, never predictions.
**Consequences:** a bounded, explainable idea generator instead of an "AI GM".

## ADR-6 · Deferred CBA rules

**Context:** sign-and-trades, TPEs, cash, BYC, and hard-cap triggers require
transaction-level data no free provider supplies and would multiply rule complexity.
**Decision:** implement the core matching/apron/roster/restriction subset fully and
honestly; document everything else as unsupported (see cba-rule-coverage.md).
**Consequences:** smaller but truthful scope — aligned with the project's
"honest > broad" priority.

## ADR-7 · Provider-backed data over a bundled dataset

**Context:** bundling a CSV would make setup trivial but violate honesty (stale,
unlicensed redistribution, fake "freshness").
**Decision:** runtime ingestion only; the repo ships zero NBA data; test fixtures
are tiny, clearly-marked synthetic records.
**Consequences:** first run requires network + a few minutes of ingestion; every
displayed number is traceable to a retrieval timestamp.

## ADR-8 · SQLite + in-process cache as dev defaults, Postgres + Redis in compose

**Context:** the build machine had no Docker; requiring Postgres/Redis locally
would block the primary "clone and run" path.
**Decision:** `DATABASE_URL` defaults to SQLite and the cache falls back to an
in-process TTL store; docker-compose and production wire Postgres 16 + Redis 7. The
schema avoids Postgres-only types (UUIDs as strings, JSON columns) so Alembic
migrations run on both.
**Consequences:** identical code paths, two deployment textures; single-instance
cache semantics in dev (documented on /data-health via `cache_backend`).

## ADR-9 · APScheduler worker instead of Celery/RQ

**Context:** the job workload is a handful of I/O-bound batch syncs per day; Celery
adds a broker dependency and operational weight far beyond the need, and every job
is already idempotent.
**Decision:** a small APScheduler process (`app/worker.py`) triggers the same
CLI-exposed jobs on configurable intervals.
**Alternatives:** Celery/Dramatiq/RQ (rejected for scope: no fan-out, no queues, no
retry semantics beyond what the client layer already provides).
**Consequences:** one fewer moving part; if the product grew user-triggered async
work, a queue would be reintroduced behind the same job functions.

## ADR-10 · TEI target choice

**Context:** a defensible supervised target must exist in the data. Team wins
contribution can't be attributed per player from box data alone.
**Decision:** predict next-season `0.6·z(PIE) + 0.4·z(NET_RATING)` (minutes-weighted
z within season) — a box-derived impact proxy that exists for every player-season,
validated strictly forward in time; keep the transparent index as fallback and
report both against a persistence baseline.
**Consequences:** honest scope (a proxy, stated as such) with measurable skill: the
index beat persistence 0.645 vs 0.717 MAE on the held-out transition.

**Superseded in part by ADR-15 (R3-1).** The ridge candidate won this player-level
comparison at 0.637 and was served on the strength of it. That was the wrong test: the
product uses TEI at *team* level, where the ridge explained R² = 0.0039 of net rating
against the index's 0.7505. The proxy target remains a defensible way to check the index
forward in time; selecting a model on it was not.

## ADR-11 · Split the two accent colors by job, not by hierarchy

**Context:** the previous dark theme was near-black plus a single orange accent — the
most common look an AI-assisted design lands on, and one that made every element compete
at the same volume.
**Decision:** two accents with disjoint responsibilities. Cyan (`--signal`) is the
system's voice: live state, active navigation, focus rings, primary chart series. Orange
(`--leather`) is the ball: the brand mark and the single primary action on a screen,
nothing else. Team color is a third channel, scoped to team context only.
**Alternatives:** one accent with tints (rejected: no way to distinguish "the system is
telling you something" from "you can act here"); full team-color theming per page
(rejected: destroys cross-page consistency and contrast guarantees).
**Consequences:** orange appears rarely enough that it always means "act", and a page can
be dense without being loud.

## ADR-12 · A condensed display face as a layout tool

**Context:** the spec treats awkward wrapping as release-blocking, and the old header
wrapped onto two lines at 1440px with eight destinations.
**Decision:** Barlow Condensed for titles, module names, team abbreviations and numerals,
paired with Archivo for interface text and IBM Plex Mono for tabular data.
**Consequences:** the condensed face buys roughly 20% horizontal room on every label,
which is what makes four primary destinations plus a More menu, search and status chips
fit one line down to 1024px without shrinking type. It is also the authentic typographic
artifact of the sport (jersey numerals, scoreboards), so the structural fix and the
aesthetic choice are the same decision.

## ADR-13 · Normalize supplied logos rather than styling around them

**Context:** twelve of the thirty supplied logo files ship on an opaque white card and
eighteen are transparent, so a row of crests mixed white boxes with clean marks.
**Decision:** derive a transparent copy at index time by flood-filling near-white **from
the image edges only**, so white inside a mark survives. Derived files are written to a
gitignored `derived/` folder; originals are never modified, and the manifest records
which copy is served.
**Alternatives:** render every logo on a light chip (rejected: 30 bright tiles fight the
dark surface); CSS blend modes (rejected: unreliable across marks with dark elements).
**Consequences:** crests render consistently on dark; the fix lives in the data pipeline
where it belongs rather than in every component that shows a logo.

## ADR-14 · Publish an asset manifest instead of letting images 404

**Context:** roughly 12% of rostered players have no matched photo. The fallback rendered
correctly, but every miss still cost a request and a console error, and the spec requires
no broken requests.
**Decision:** `GET /api/v1/assets/manifest` returns the set of NBA ids and abbreviations
that actually have an indexed image. The client fetches it once per session and only
requests an image it knows exists.
**Consequences:** zero 404s and zero console errors across all seven QA viewports;
unmatched players render their initials immediately rather than after a failed round-trip.

## ADR-15 · Rename routes to the product's own vocabulary

**Context:** navigation labels moved to the module names the product actually uses
(Trade Evaluator, Strategy Lab, Player Explorer, Team Outlook, Salary-Cap Center, Data
Health), leaving URLs like `/trade-machine` and `/data-status` inconsistent with them.
**Decision:** rename the route directories to match, and keep a redirect for every prior
URL — including the ones from the first rename. Next forwards query strings, so shared
Trade Evaluator links (`?state=…`) survive.
**Consequences:** the URL bar and the interface agree; no published link breaks. The
redirect table is the permanent cost, and it is documented in `next.config.ts`.

## ADR-15 · Retire the ridge; the transparent index is the metric (R3-1)

**Context:** the ridge was selected on held-out MAE against a next-season player proxy —
0.637 vs the index's 0.645. That comparison answers "which better predicts a player's own
next-season box profile". The product asks a different question: how does a roster change
move *team* net rating. Measured at that level on 90 team-seasons, the ridge explains
**R² = 0.0039** against the index's **0.7505** (change-on-change over 60 transitions:
0.0030 vs 0.6236). It is additionally a volume metric (corr 0.716 with usage, 0.100 with
net rating) and is not computable per season from stored artifacts, so the R3-2
conversion could only have been fitted on n = 30 of a metric with no signal.

**Decision:** retire it. Not demote — nothing in the serving path can select it, there is
no estimator import and no pickle to load. The index's fixed weights are the model, and
they are recorded in `model_versions`. Validation still runs time-aware against a
persistence baseline, because "better than assuming last season repeats" is still worth
checking.

**Consequences:** seven documents and the in-product methodology page carried "held-out
MAE 0.637" as the headline validation number; all now report the index's numbers and
state why the ridge lost. The interval band, which was derived from the retired model's
residual spread, had to be replaced in the same release (R3-4) rather than left pointing
at a model that no longer exists.

## ADR-16 · Fit the index→net-rating conversion; do not assume it (R3-2/R3-3)

**Context:** `team_tei_to_net_rating_delta` returned the difference in minutes-weighted
team TEI unchanged, on the reasoning that "TEI is on a per-100 individual scale". It is
not: the index is a weighted z-score on an arbitrary scale. A ×5 "players on court"
factor was also proposed and is equally unfounded.

**Decision:** fit `Δnet = b · Δ(team TEI)` change-on-change over 60 team transitions —
levels would carry everything the roster does not (coaching, health, schedule), and
differencing removes the team fixed effect. Measured **b = 14.977**, SE 1.528, t = 9.80,
R² 0.624; per-fold slopes 14.716 / 15.276; leave-one-transition-out RMSE 2.944 / 3.773
against 5.201 / 5.805 for predicting zero. Registered as its own `model_versions` row
with the regressor-construction string, because the coefficient is meaningless without it.

Two changes had to ship in the same release or the coefficient would have been silently
wrong:

- **Train/serve scale (C5).** Served rows were z-scored against the recency-weighted
  window's own distribution. Team-level correlation between the two constructions was
  r = 0.387, and the two rescalings that implied disagreed by **2.6×** — proof that no
  transfer factor existed. Serving against the reference season's moments removes the
  mismatch rather than correcting for it; slope is now 1.015 (r = 0.911).
- **Denominator and replacement level (R3-3).** Team impact was divided by the minutes a
  roster happened to fill, so giving players away took the same average over fewer
  minutes. It now divides by the 240 a team must field and charges the shortfall to a
  replacement-level player, derived (**−1.214**, mean TEI outside a team's top 10 by
  minutes) rather than hardcoded at −2.0, which sat at the 14.1st percentile.

**Consequences:** the performance component moved from sd 1.27 to **18.2** across a
150-trade sample, and stripping a roster of its three best players now scores at most
**14.7** across all 30 teams, from 56.4. The clamp binds on 2.7 % of that sample. The
falsification note is recorded with the fit: if TEI were already in additive
net-rating units the coefficient would be ≈5; it is ≈15.

## ADR-17 · One `delta_net`, computed once (R3-5)

**Context:** the point estimate reallocated all 240 minutes across the whole roster; the
Monte Carlo summed raw minute shares over the traded players only. Two different
quantities printed beside each other as if they were the same one, diverging with trade
size.

**Decision:** the simulation draws over the rotation the allocator produced — same
players, same minutes, same replacement fill, same coefficient. Incumbents appear on both
sides and, keyed on player identity, their draws cancel exactly.

**Consequences:** the simulated **mean** now equals the point estimate to within Monte
Carlo error (the regression test's tolerance is the simulation's own standard error, so
it tightens automatically if the draw count rises). The median sits slightly off it
because the availability beta is skewed, which is a real property of the distribution
rather than a disagreement; `mean` is therefore reported alongside the quantiles.

## ADR-18 · A comparable is a **side**, not a trade (R6-2)

**Context:** "Boston traded Marcus Smart for Kristaps Porziņģis" and "Washington traded
Kristaps Porziņģis for Marcus Smart" are one transaction. A front office asking for
precedent is not asking about the transaction; it is asking what happened to teams that
did what it is about to do.

**Decision:** the retrieval unit is one team's view of one trade. A three-team trade
contributes three of them, direction lives on the asset rather than on the team, and a
result list returns at most one side of any transaction — both remain in the corpus and
both are ranked.

**Consequences:** the corpus is 1,225 sides over 565 trades, 337 of them rankable. The
direction of a deal became a first-class property, which is what made
`direction_confusion` measurable at all: of the neighbours returned for a side that sold
on-court value for first-round picks, **1.0 %** are sides that bought it.

## ADR-19 · Similarity excludes what only one half can state (R6-2)

**Context:** a distance is a claim that two things are alike in the dimensions it reads.
Three dimensions are available on only one side of the comparison: salary (no historical
contracts exist here), cash and trade exceptions (the corpus states both, a *proposed*
trade states neither), and the outcome.

**Decision:** none of the three is scored. Cash and trade exceptions are reported as
attributes of a neighbour; salary and outcome are named in a `not_scored` block with the
reason on each.

**Consequences:** a feature the query can only ever answer "no" to would have penalized
the **37 %** of completed trades whose notes report a trade exception — a systematic bias
that would have looked like a preference for clean two-team deals. And the product never
implies that a comparable predicts anything: "resemblance is not consequence" is in the
panel's own text and in the memo, not in a tooltip.

## ADR-20 · A target list ranks on wins and filters on the need — not one blended score (R6-3)

**Context:** need-driven discovery has to combine "does he fix our problem" with "is he
any good". A single score needs a weight between them, and nothing in this repository
labels a target as good, so the weight could not be fitted.

**Decision:** two rules, both printed in the response. Filter on the need; rank on the
projected win change from adding the player. `sort=need` reorders by need improvement
instead. Ranking on `fit` was rejected because `fit_score` normalises minutes within a
side, so an 8-minute specialist scores like a 32-minute starter.

**Consequences:** ranking by wins alone gave all 30 teams the same names — **26 distinct
players** across every team's top five. Putting each candidate through the trade
evaluator under the candidate generator's own conditions took that to **72**, and made
the acquisition path and the generator agree by construction about what a front office
would accept.

## ADR-21 · Lineup-aware fit is refused on a measurement, not deferred on a schedule (R6-4)

**Context:** R6's third objective was a lineup-aware fit "where the available data
honestly permits it". The obvious answer — "the data is missing" — was not true:
`LeagueDashLineups` is reachable and returns real five-man data.

**Decision:** measure the samples instead of assuming them. At the median five-man group
among the top 2,000 by minutes (20.2 minutes, 2024-25) a net-rating estimate carries a
standard error of about **16 points per 100 possessions**, against a league team spread
of roughly ±10. Two- and three-man groups are estimable and still cannot support a
*trade* fit model, because a trade prices combinations that have never played together
and nothing here holds a held-out target to validate a synergy model against.

**Consequences:** what shipped is roster composition — minutes by role, before and after,
against the league's own distribution — labelled in its own text as not lineup data.
`make lineup-availability` re-runs the measurement, so the refusal is falsifiable rather
than permanent.

---

# Pivot restructure (ADR-22 … ADR-28)

*The R1–R7 product was renamed and restructured into Pivot, a basketball
decision-intelligence platform. These record the decisions that were not obvious, including
the ones that were to leave something alone.*

## ADR-22 · `Scenario` means two things; the domain gets the new one, the database keeps the old

**Context:** the `scenarios` table does not hold scenarios in the sense Pivot's roadmap
means. It holds a team's *decision mandate* — strategy, horizon, risk tolerance, apron
willingness, untouchable players, and the six component weights scoring runs under. It is a
settings bag attached to a team and it never changes as a result of a move. R13's Scenario is
a roster-state trajectory: state → move → state. Two different nouns, one word, and the
collision would have poisoned the scenario layer had it not been named before anything was
built on it.

**Decision:** the stored entity keeps its table name, its `/scenarios` routes and its API
shape. The domain vocabulary calls it `TeamMandate` (`domain/mandate.py`), and that module is
where the mapping is written down. `Scenario` in the R13 sense is `domain.moves.ScenarioStep`.

**Alternatives:** rename the table and the routes (rejected: a migration, a breaking API
change, and a rewrite of the share links and query parameters that seed the trade evaluator —
all to relabel a concept users already see under a different word, since the UI has always
called it a *strategy*); leave the collision unnamed (rejected: the next person to read
`ScenarioStep` and `Scenario` in one file would have to guess).

**Consequences:** one word means two things in the codebase, and there is now exactly one
place that says so.

## ADR-23 · The domain layer owns the vocabulary, and the analytics re-export it

**Context:** `SKILL_KEYS`, `ROLE_ID`, `NEED_TO_SKILL`, `UNADDRESSABLE_NEEDS` and the strategy
weight table were each declared inside the module that happened to use them first. Every
future engine needs the same vocabulary, and copying it would create exactly the drift the
product cannot afford — a skill described to a user that no engine computes.

**Decision:** `app/domain` owns the vocabulary and computes nothing. The constants **move**
there; `analytics.archetypes`, `analytics.needs` and `services.evaluation` re-export them, so
every existing import keeps working. An AST test asserts `domain` imports nothing from pandas,
SQLAlchemy, FastAPI, `app.analytics`, `app.services`, `app.db` or `app.api`.

**Why the move was safe, measured rather than assumed:** every moved value is asserted
byte-identical to what shipped, and `skill_schema_fingerprint()` is unchanged at
`14a7dac41af3` — it hashes the *contents* of `SKILL_KEYS`, not its address, so no cached skill
vector was orphaned. `ROLE_ID` remains frozen and append-only; `player_archetypes.role_id` is
persisted per player-season and renumbering would silently rewrite the meaning of every
historical row.

**Consequences:** two names for one list (`domain.skills.SKILL_KEYS` and the re-export). The
alternative — a flag day migrating 30+ call sites — buys nothing and risks the fingerprint.

## ADR-24 · Rename what a user reads; keep the identifiers that point at something

**Context:** 95 occurrences of the old naming across 40 files, and four of them are
load-bearing in ways a search-and-replace would not notice.

**Decision — renamed:** all rendered UI copy, the wordmark, page titles, the OpenAPI title and
root payload, the decision-memo `<title>`, the outbound Basketball-Reference User-Agent, the
`pyproject` description, README and docs prose, module docstrings.

**Decision — deliberately not renamed:**

| Identifier | Why |
| --- | --- |
| `tradelab-backend` (distribution name) | the import root is the generic `app`; there is no `import tradelab_backend` anywhere, so the rename touches only build artifacts |
| `sqlite:///./tradelab.db` | points at a live 40 MB ingested database on the operator's disk |
| `tradelab:` cache prefix | namespaces every cached entry; the data-version counter under it carries a ten-year TTL |
| `TEI` | names a DB column, an API field and every registered `ModelVersion`. The **expansion** became "Pivot Estimated Impact"; the acronym did not move |
| `ROSTERLAB_OFFLINE` | the test suite's third-party network interlock, read in `cli.py` and set in `conftest.py` |
| `rosterlab.favoriteTeam` | renaming silently discards every user's saved team |
| 13 `ROSTERLAB_*.md` reports | the provenance record of what was built under what name |

Each site that keeps a historical name carries a comment saying why, so the next reader does
not "fix" it.

**Also fixed:** the repository shipped *three* taglines simultaneously — "Basketball Decision
Intelligence", "NBA Front Office Simulator", and a third in page copy. Pivot has one, exported
once from `components/brand.tsx` and asserted by a test.

## ADR-25 · Navigation follows the workflow; route paths do not move

**Context:** the primary nav led with the Trade Evaluator — the *last* step of the decision
workflow — which made the product read as a collection of calculators that share a header.
The brief's information architecture groups modules under Players / Teams / GM Lab.

**Decision:** regroup and relabel navigation around observe → diagnose → test → decide, and
change **no route paths**. GM Lab is a nav group over three real modules, not an index page.

**Alternatives:** rename paths to match the IA (rejected: breaks ten legacy redirects, a
fourteen-route visual-QA manifest, the e2e specs and every shared Trade Evaluator link —
which carries a `?state=` query string — for no gain a user notices, since labels are what a
user reads); add a GM Lab landing page (rejected: the brief forbids empty pages, and a page
promising a module Pivot has not built is the presentation-layer version of a fabricated
number).

**Consequences:** the nav label and the URL differ for some destinations. A new test
(`tests/unit/navigation.test.ts`) reads the route manifest off disk and asserts every nav
entry resolves to a real App Router directory, which nothing checked before.

## ADR-26 · A basketball judgement belongs on the server, even a small one

**Context:** the thresholds that turn a need row into a headline strength or weakness —
severity 0.35, percentile 65 — were constants in the browser (`frontend/lib/needs.ts`). That
made "is this team bad at this?" a presentation-layer decision no backend test could reach,
and it is how QA-9 happened: a franchise appeared under Strengths *and* Needs for the same
row, with a zero-length bar under a caption promising a longer bar meant a larger shortfall.

**Decision:** the thresholds live in `domain/needs.py` and are applied by
`IntelligenceService.team_profile`, which returns pre-classified `weaknesses` and `strengths`.
The two lists are disjoint by construction — a weakness needs real severity, a strength needs
severity *exactly* zero — and a zod refinement at the client boundary asserts it.

The old browser fallback ("if nothing clears the threshold, show the top four by severity
anyway") is deliberately not reproduced: 135 of 279 stored rows have severity 0, so it
presented teams with a weakness list they did not have.

**Consequences:** `lib/needs.ts` and its tests are retained for the legacy `/teams/{id}/needs`
shape, with a docstring recording that the rule is now server-authoritative and that the two
copies must not drift.

## ADR-27 · Fit is exposed conditionally, and withheld where it inverts

**Context:** the brief's position is that no universal player fit score exists. `fit_score`
was already team-conditional but only reachable through a trade, so "would this player fit
here?" could not be asked.

**Decision:** `Fit(player, team)` is addressable, with `team_id` **required** — a request
without one is a 422, not a default. There is no `fit(player)` entry point, because a
signature that permitted a universal score would contradict the product's position before any
handler ran.

**And it was measured before it was exposed.** Across the 30 ingested rosters, where the need
vector has signal it discriminates correctly — DEN's best fits are rim protectors (needs POA
defense 1.00, rim protection 0.86; n=127, median 63.9, sd 28.9), SAC's are shooting bigs,
MEM's are rebounding bigs. Where no need clears the severity threshold it **inverts**: fit is
a needs term minus a redundancy term, so with the needs term near zero it becomes a pure
redundancy penalty and ranks *better* players lower — on ATL (max severity 0.172), 88.6 % of
candidates score below neutral and the worst-ranked player is Donovan Mitchell.

Two of thirty rosters are in that state. They return an explicit unavailable with the reason
rather than a number that would rank a star last.

**Alternatives:** ship it unqualified (rejected: it would be wrong for two teams and nothing
would say so); withdraw the endpoint entirely (rejected: it is correct for 28 of 30, and the
failure mode is precisely characterised); change the one-way baseline to `REPLACEMENT_SKILLS`
(rejected: measured, and *worse* — 91.1 % below neutral, with replacement-level players
ranking highest).

**Consequences:** a documented, testable gate that R12 must remove by building a fit model
that survives a sparse need vector, rather than by lowering the threshold.

## ADR-28 · The Copilot boundary is a registry, and it is read-only

**Context:** §12 of the brief requires that a future assistant not be the analytical engine.
That is an architectural property, and architectural properties that live only in a document
do not hold.

**Decision:** `services/tools.py` declares the tool vocabulary as data — ten named tools with
JSON Schema parameters, four implemented and six declared with the reason they cannot be. Two
properties are asserted by tests: `ToolSpec` raises on construction if `readonly=False`, so a
conversation can never change state a person did not confirm; and an unavailable tool has no
handler, so it cannot be quietly callable and return something adjacent.

Each tool carries `result_caveats` that travel with its result — `calculate_fit` carries "fit
measures the direction of a change, not its size", which is exactly the misreading a fluent
model would otherwise produce.

**Consequences:** no LLM exists and none is implied. The six unbuilt tools are the concrete
R8 work list, because each is blocked by a real structural problem (most importantly: the
evaluation composite has no single entry point, being duplicated across four API handlers).

## ADR-29 · An editorial dark system, and a league percentile beside the impact index

**Context:** the "court terminal" system (light graph-paper canvas, volt green, expanded
display face, mono labels) read as an instrument panel rather than a basketball product,
and the scroll-driven homepage put a narrative between a returning user and their work.
Separately, the impact index (TEI) was shown as a bare signed number that users read as
points per 100 possessions, which it is not.

**Decision:** one dark editorial system (`docs/design-system.md`): near-black ground,
charcoal cards, warm white text, a single subdued mint accent, Instrument Serif for titles
and headline statistics, Geist for everything operational, and a persistent left sidebar.
The homepage is an intelligence dashboard behind one static illustrated arena; it shows only
served data and says what is missing. Impact is placed as a league percentile and rank
among every scored player, from a new read-only endpoint (`GET /players/impact-distribution`)
that serves the scored population, with the raw index kept at 2 dp beside it.

**Alternatives:** keep the two-scope paper/terminal system and only restyle the home page
(rejected: the product would carry two visual languages); a photographic arena hero
(rejected: no licensed image in the repository, and a downloaded asset is a dependency the
brief did not ask for); a 0–100 "overall" player rating (rejected: arbitrary, and it would
merge on-court impact, team fit and projected wins, which the product keeps separate).

**Consequences:** token names from the old system are kept and re-valued, so no page needed
a rename to restyle. The scroll-home experiment is preserved on `feat/pivot-scroll-home`;
this branch removes its components. `/command-center` keeps serving the dashboard so old
links still land. The percentile is mid-rank with ties shared, clamped to 1–99 on display,
and is never applied to a precedent's `tei`, which is a different quantity.
