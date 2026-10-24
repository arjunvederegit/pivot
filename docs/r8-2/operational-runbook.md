# R8.2 — Operational runbook

All commands run from the repo root. They reuse the existing Makefile/CLI; there is no second CLI.
Nothing here is run for you. `DATABASE_URL` selects the database — **point it at a copy while rehearsing**.

## 1. Refresh the 2026-27 rosters

```bash
make refresh DATASETS=rosters ARGS=--roster-only   # memberships + brand-new identities only (recommended)
make refresh DATASETS=rosters                     # also refreshes known players' position/height/weight/birth date/experience
```

`--roster-only` exists because the full refresh rewrote the bio fields of 448 known players on your real data,
and six newly filled birth dates alone moved the acquisition battery's headline numbers (teams answered 29 → 19).
Refresh bio fields deliberately, together with re-validating the batteries — not as a side effect.

* With no `--season` this now refreshes the **league year's** roster (2026-27 until 30 June 2027) — not the
  statistics season. `ARGS='--season 2025-26'` refreshes a historical one (history is not otherwise touched).
* ≈30 NBA.com requests, ~25 s, one snapshot. It **rejects** a snapshot that cannot be the league (empty,
  missing most teams, or a sudden collapse in size) and leaves the stored rows alone; an upstream outage also
  leaves them alone. Both are recorded as a failed run attributed to 2026-27.
* Output ends with **`roster_verification`**. Read `complete` and `unresolved`:

| Field | Healthy 2026-27 preseason value (2026-10-18) |
| --- | --- |
| `teams_covered` / `teams_expected` | 30 / 30 |
| `team_size` | 16–20 (min/median/max 16 / 18 / 20) |
| `players_on_multiple_current_rosters` | 0 |
| `players_on_a_different_team_than_previous_season` | ≈108 |
| `players_without_performance_season_statistics` | ≈85 (rookies, new signings; absent, not zero) |
| `last_refresh` | `added N, confirmed M, closed K` — a repeat run should read `added 0` |
| `provisional_note` | present until opening night |

`make refresh DATASETS=rosters,standings,player-stats,team-stats,games` refreshes more; add
`ARGS='--season 2026-27'` for the new season's statistics once games exist (preseason rows:
`--season-type preseason`).

## 2. See which season everything reads

```bash
make season-status
```

Prints, read-only: `seasons` (roster / performance / contract / projection / pins / notes),
`roster_verification`, `readiness` (rollover date, what is missing, `pinned_behind_calendar`) and each
dataset's freshness. The same block is served at `GET /api/v1/data/season-context` and rides on every tool
payload as `season_context`. In the app the sidebar shows "2026-27 rosters · 2025-26 stats".

| You see | It means | Do |
| --- | --- | --- |
| `roster.fell_back: true` | no 2026-27 snapshot ingested; last season's is shown, labelled | `make refresh DATASETS=rosters` |
| `roster.status: provisional_preseason` | camp/two-way/Exhibit-10 not distinguishable; before the cut-down | re-run after opening night (20 Oct) and again after the cut-down settles |
| `performance.is_baseline: true` | statistics are last season's by design until 3 Nov | nothing |
| `contract.state: missing` | no 2026-27 salaries on file | see remaining-work §C |
| `pinned.current_season` set | statistics are pinned; rosters are **not** | blank it to let the calendar drive |
| `pinned_behind_calendar: true` | the pin predates the calendar's analysis season | blank `CURRENT_SEASON` in `backend/.env` |

## 3. Freshness

`make data-status` (or `GET /data/freshness`). Rosters are judged on the roster season (24 h window, refreshed
every 6 h by the worker), statistics on the performance season, contracts on the cap year. `finalized` = a
completed season retrieved after it settled; `not_yet_available` = games not played; `stale`/`missing` need
attention; a failing refresh is shown even while rows read `fresh` (`refresh_failing`). `attention` lists
datasets needing action — on 2026-10-18 that is **contracts, injuries** (absent by design), not rosters.

## 4. Keep it current (worker or cron)

`python -m app.worker` (or `docker compose`) runs `sync_rosters` every 6 h — now the league-year roster — and
statistics daily. Without a worker, cron `make refresh DATASETS=rosters` at least daily through the preseason and
the first weeks (cut-down, two-way and trade churn move it). **Not run across a real date boundary yet.**

## 5. Preparing 2026-27 model artifacts (`make train && make score`) — read before running

A rehearsal on a disposable copy (`CURRENT_SEASON=2026-27 python -m app.cli train`, then `score`) established:

1. **`train` and `score` run on `Settings.current_season`.** With the calendar unpinned that is 2025-26 until
   **3 Nov**, so running them on 2 Nov re-trains **2025-26** — it does not prepare 2026-27. *(R8.1's remaining-work
   A1 said "between 2 Nov and the rollover"; that is incorrect and is superseded by this section.)*
2. **To make 2026-27 estimates you must either wait for 3 Nov or run with `CURRENT_SEASON=2026-27` set for that
   command only.** Do not edit `.env` to do it.
3. **`train` changes the model window**: three seasons ending at the performance season — `2024-25..2026-27`,
   dropping 2023-24. On the rehearsal copy (no 2026-27 rows) it still produced 500 estimates labelled 2026-27 from
   what is effectively 2024-25..2025-26 data, **replaced the active versions of all five models** (old rows kept,
   inactive) and reported "insufficient transitions for supervised validation". Run it only after 2026-27
   statistics are ingested.
4. **`score` stops** ("no team stats ingested") until 2026-27 team statistics exist, so 2026-27 team needs
   do not exist before then; evaluations say so.

Safe procedure, on or after 2026-11-03:

```bash
cp backend/tradelab.db /tmp/rehearsal.db                       # a COPY; never rehearse on the real file
DATABASE_URL=sqlite:////tmp/rehearsal.db make refresh DATASETS=player-stats,team-stats,standings ARGS='--season 2026-27'
DATABASE_URL=sqlite:////tmp/rehearsal.db make train && DATABASE_URL=sqlite:////tmp/rehearsal.db make score
make season-status      # on the real DB: projection.estimates_for_input_season should now read true once you promote it
```

Promote only after comparing the rehearsal's batteries (`make comparable-validation acquisition-validation
adversarial-validation`) with the current ones. **A `train` cannot be reversed by re-activating the old model versions:** `_gc_superseded_estimates` permanently
deletes every impact estimate that belongs to an inactive version (the version rows stay; their estimates do not).
The only rollback is restoring a pre-train backup, so take one first (see the activation checkpoint report).
*(This corrects the first version of this section, which said nothing is deleted.)* **R8.2 did not run train/score on any real database.**

## 6. Troubleshooting

| Symptom | Cause / action |
| --- | --- |
| `SnapshotRejectedError: … covers N of 30 known teams` | NBA.com returned a partial roster; the previous snapshot is intact. Retry later; check `make data-status` → `rosters.last_refresh.error` |
| `ProviderBlockedError` / HTTP 403 / throttling | NBA.com blocked the client; **stop** (do not loop). Lower cadence; see R8.1 live-ingestion-report. Stored rosters are untouched |
| roster shows last season's players | `roster.fell_back`; refresh. If `refresh` succeeded but the app still shows them, the server needs no restart — check `make season-status` |
| a player "on no roster" | `membership_status: unlisted` — not a claim of free agency; the snapshot may be provisional |
| `players_on_multiple_current_rosters > 0` | a player moved during the 30 sequential fetches or the source contradicts itself; the page shows `conflict` and no team. Re-run the refresh; if it persists, it is source ambiguity — report it, do not edit rows |
| evaluation says "team needs measured on the 2025-26 team" | expected until 2026-27 needs exist (§5); not an error |
| `payroll_available: false`, salaries `—` | no 2026-27 contract data (no provider); nothing is guessed |
