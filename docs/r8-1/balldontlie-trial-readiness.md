# R8.1 — BALLDONTLIE GOAT trial readiness

## Verdict: **READY WITH LIMITATIONS**

The canonical layer, the adapter and the evaluation harness are sufficiently prepared for the owner to
start the trial — **provided the free-key dry run in step 3 below is done first.** Not "READY",
because **nothing in this integration has ever touched the real API.** Every statement about what
BALLDONTLIE returns is still "documented", not "observed"; `live_verified` stays `false`. No trial was
started, no account created, no key used and no request made to BALLDONTLIE in this session.

Why not NOT READY: the problems that could have wasted the 48 hours were found in review and fixed (§3),
the plan is cheap (103 requests ≈ 26 minutes at the trial's 5 requests/minute), every probe is
independent, every response is cached on disk (a re-run or `--replay` costs nothing), and a new
preflight now refuses to start a live run when Pivot's own comparison data is not usable.

## 1. The seven questions

| # | Question | Answer |
| --- | --- | --- |
| 1 | Architecturally compatible with the canonical layer? | **Yes.** The adapter emits only `app.canonical.records` and `ProviderResult`; no tool imports it. R8.1 now routes nba_api through the same contract (`nba_api/canonical.py`), so the two providers are comparable record-for-record. *Observed via tests, not live.* |
| 2 | Authentication safely implemented? | **Yes.** `SecretStr` from settings/env only; sent only as the `Authorization` header (no `Bearer`, as documented); redacted from exception text; the client refuses before any request when no key is set; the key never appears in `/data/providers` or in the evidence directory (existing tests). `.env` and `data/imports/*` are gitignored (verified with `git check-ignore`). Not verified: that the header format is what the live API wants. |
| 3 | Provider ids mapped reliably? | **As far as can be known offline.** Teams by abbreviation + full name; players by tiered name resolution using draft year/number/height, ambiguity refused rather than guessed (the 38 duplicate-name pairs in Pivot are a tested case); links are idempotent and conflicts are filed. Unverified: real BALLDONTLIE payloads (P2 will measure the link rate). |
| 4 | Rate limits and pagination? | **Yes.** Pacing at 80 % of the published limit — **15 s between requests on the trial** (5/min), every retry paced too; `Retry-After` honoured; cursor pagination capped at 100 pages with a repeated-cursor guard. **R8.1 closes a gap:** a capped/looping read was returned with a warning that nothing surfaced — it now becomes an `inconclusive` "incomplete read" finding. |
| 5 | Missing / tier-restricted fields represented correctly? | **Yes.** Absent → `None` (never 0); a plan-excluded endpoint is `restricted` (published tier table) or inferred `TIER_RESTRICTED` after the key has succeeded elsewhere (a 401 cannot say which; the message says it is an inference); contract options/guarantees/no-trade are not inferred. |
| 6 | Does the harness cover meaningful requirements? | **Now yes, with named gaps** (§2). R8.1 added the two probes the capability list needed and could not previously test: **current-roster agreement (P2b, zero requests)** and **team per-game statistics (P5b, one request)**. |
| 7 | Additional checks before the trial? | **Yes — and they are built:** `make balldontlie-preflight` (§4) and the free-key dry run. |

## 2. What the workflow can assess

| Capability | Probe | How judged | Caveat |
| --- | --- | --- | --- |
| Team identities | P1 | link rate ≥ 100 % | |
| Player identities | P2 | ≥ 95 % of active players linked; ambiguous/unresolved listed | |
| **Current rosters** | **P2b (new)** | BDL team == Pivot's current roster team ≥ 90 % | compares against the league season's roster once staged; says nothing about which side is stale |
| Basic player statistics | P5 | PTS within 0.15 for ≥ 20 GP; infers per-game vs totals; ≥ 95 % | aggregation of `/season_averages` is undocumented — P5 decides it |
| **Team statistics** | **P5b (new)** + P7 | team PTS ≥ 95 %; P7 lists clutch/defence/shooting/playtype/tracking fields | the other categories are *informational* (field lists and fill), not agreement-tested |
| Advanced player statistics | P6 (+P7) | alias map, fill ≥ 90 %, median/max difference | defence/clutch/shot-zone families have no Pivot counterpart to agree with |
| Historical coverage | P8 | oldest season that answers (8 samples) | informational |
| Contracts and salaries | P9, P9b, P10 | roster coverage ≥ 90 %; salary agreement ≥ 90 % with the snapshot; guarantee/type fill | **P9b is `inconclusive` unless a contract snapshot is loaded — Pivot has none** |
| Injuries | P11 | shape, status vocabulary, return-date fill | informational |
| Play-by-play / lineups | P12 | field lists and fill on 3 games | informational; lineups are starting fives, not combinations |
| Completeness | every probe reports fill/compared counts; `inconclusive` marks incomplete reads | | |
| **Freshness / update latency** | **not automatable** | manual check M1 (watch a game finish) | only meaningful if games are played during the 48 hours; the regular season opens 2026-10-20 |

**Statuses the report uses** (and the report header now counts them): `pass` / `fail` (agreement with
Pivot's NBA.com-derived data — *agreement, not truth*), `inconclusive` (ran, but incomplete or
undecidable), `restricted` (skipped: outside the plan per the published table), `error` (blocked or
failed), **`not_run` (excluded with `--only` — new; previously silently omitted)**.

## 3. What R8.1 changed in the harness, and why

| Change | Reason |
| --- | --- |
| Comparison season defaults to the **last completed** season (`--season` overrides), not `CURRENT_SEASON` | After the automatic rollover on 2026-11-03 the tools read 2026-27, for which Pivot has days of data; a trial run then would have compared BALLDONTLIE against almost nothing and reported noise. Tested on both sides of the rollover. |
| Every roster read is scoped to a season (`current_roster_clause`) | **A defect in R8's harness:** sampling and the contract probe read `RosterEntry.is_current` unscoped — the exact trap R8 removed from the tools. With 2025-26 and 2026-27 rosters both staged (which R8.1 does) a traded player sat on two teams and coverage was miscomputed. Caught by mutation M7 once a test was written. |
| Contracts compare with the cap year's roster when staged | cap hits are 2026-27; comparing them with last season's rosters undercounts rookies and movers |
| P2b and P5b added | the capability list needed them |
| Truncated reads → `inconclusive` findings | previously a capped read could pass silently |
| `not_run` and `status_counts` in the report | "whether any probe was skipped, blocked, unsupported or incomplete" |
| Preflight (below) | the 48-hour clock is paid for |
| Plan estimate corrected | R8's doc says ≈130 requests / 35 min; `plan_summary` computes **103 requests / 25.8 min** (the extra was slack). Budget default 200 stands. |

## 4. Preflight — run it before starting the clock

```bash
make balldontlie-preflight            # no request, no key needed; exit 2 if not ready
```
Checks: key configured (blocker); Pivot's comparison-season player statistics, standings and games are
`finalized`/`fresh` and not failing (blocker, with the refresh command to fix it); the plan fits the
budget (blocker); warnings for unstaged league-season rosters, an empty contract snapshot, and "the
adapter has never been called live". `make balldontlie-evaluate` runs it first and **refuses** unless
`FORCE=1`. Output on the live-ingested isolated database (no key set): everything `finalized`, rosters
for 2026-27 staged, blockers = *key not set* only, warnings = no contract snapshot.

## 5. Limitations that remain (all disclosed, none blocking)

1. **Never run live** (the principal one). Unverified until step 3/6: the key header, the `/nba/v1`
   prefix (the narrative docs say `/v1`), array-parameter spellings (`seasons[]` vs `player_ids`), the
   shape of every payload, the real 429 behaviour. A wrong prefix would 404 every call; the client
   classifies 404 as `EMPTY` ("the provider answered: none"), so the free-key dry run is what prevents
   a trial burnt on a typo.
2. **P9b cannot decide** without a contract snapshot (none loaded). P9 (coverage) still runs.
3. Informational probes (P7 categories, P8, P10, P11, P12) record what exists; they have no pass/fail,
   so "does it add value" (the written criteria B1–B5) remains a human judgement from the report.
4. The trial's 5 requests/minute cannot test sustained load, and the 600/min tier cannot be tested
   without paying.
5. Agreement is with NBA.com-derived data, which is itself what Pivot is trying to reduce dependence on;
   it is the only accuracy check available without a second paid source.
6. **Licensing is not a technical test** and still dominates the subscription decision (criteria A3 in
   `docs/r8/balldontlie-goat-evaluation.md`): BALLDONTLIE's licence to Pivot does not clear NBA.com's
   terms for the underlying statistics. Nothing in R8.1 changes that.
7. Test coverage of `evaluation.py` rose from 63 % to 73 %; the remaining untested lines are mostly
   report formatting and the informational probes.

## 6. Owner checklist — in order

1. **Refresh Pivot's own data** (this branch; ≈ 61 requests to NBA.com, as the worker does every 6 hours):
   ```bash
   make refresh DATASETS=rosters                                  # 2025-26 rosters (89 d old)
   make refresh DATASETS=rosters ARGS='--season 2026-27'          # stage the incoming season
   make refresh DATASETS=games                                    # repairs 5 neutral-site games
   make season-status                                             # rosters fresh; 2025-26 finalized
   ```
2. **Create a free BALLDONTLIE key**, put `BALLDONTLIE_API_KEY=` and `BALLDONTLIE_TIER=free` in
   `backend/.env` (never in a commit or chat).
3. **Free dry run — the live verification, at no cost:** `make link-balldontlie DRY=1`. It must report
   30/30 teams linked and a sensible player link rate. If it 404s, set `BALLDONTLIE_API_PREFIX=/v1`
   and retry; if it still fails, **do not start the trial** — send me the error.
4. *(Optional, strongly useful)* load a contract snapshot so P9b can decide.
5. `make balldontlie-preflight` → must print `"ready": true`. Read the warnings.
6. **Start the 48-hour trial** (needs a payment method; one trial per sport per account). Set a
   reminder to cancel by **hour 40** unless you intend to subscribe. Change `BALLDONTLIE_TIER=trial`.
7. `make balldontlie-evaluate` (~26 min; keep the terminal open; `BUDGET=200` default). Evidence lands in
   `data/imports/balldontlie/eval-<time>/` (gitignored). Re-analyse for free with `REPLAY=<dir>`.
8. Do manual checks M1–M6 (`docs/r8/balldontlie-goat-evaluation.md` §4), apply the written criteria
   (§5), record the verdict, and **cancel before the 48 hours end if it fails**.

Commands above that need the owner's own judgement (payment, key, cancellation) were not run.
