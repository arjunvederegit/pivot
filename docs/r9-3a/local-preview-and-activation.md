# Local preview and safe activation

Everything below runs from a fresh terminal. The preview is a **copy**: it never opens your development database, never touches the servers on `:8000`, `:8010` or `:3000`,
and never needs a provider key. Activating the data on your real database is a separate, reviewed step (§4) that this release prepares but does **not** perform.

## 1. Start the improved Pivot (two commands)

```bash
cd /Users/arjunvedere/pivot-r9-3a-worktree
make preview-up
```

Then open:

| | URL |
| --- | --- |
| Cap Center | <http://localhost:3093/salary-cap-center> |
| Trade Evaluator | <http://localhost:3093/trade-evaluator> |
| Player Explorer | <http://localhost:3093/player-explorer> (open any player → Advanced / Contract tabs) |
| Backend API | <http://localhost:8093/api/v1/teams> |

Other targets: `make preview-status`, `make preview-down` (stops only what `preview-up` started), `make preview-verify` (checks the pristine snapshot against its checksum),
`make preview-reset` (stops the preview, then restores the database from that snapshot — undo any experiment).

### Where things are

| | Path |
| --- | --- |
| Worktree / branch | `/Users/arjunvedere/pivot-r9-3a-worktree` · `feat/r9-3a-goat-operational-tools` (pushed; not merged to `main`) |
| Preview data directory (outside Git) | `/Users/arjunvedere/pivot-r9-3a-data/` |
| Preview database (a **copy**) | `…/preview.db` — your development DB + R8.1–R9.1 migrations + R8.3 GOAT ingest + 2027-28…2030-31 contracts + R9.2 candidate models |
| Pristine snapshot + checksum | `…/backups/preview-ready.db` · `preview-ready.db.sha256` |
| Base before the 2026-27+ loads | `…/base-r83-ingest.db` |
| Logs and pids | `…/run/api.log`, `web.log`, `api.pid`, `web.pid` |

Override with `PIVOT_R93A_DATA`, `PIVOT_R93A_DB`, `PIVOT_R93A_API_PORT` (8093), `PIVOT_R93A_WEB_PORT` (3093). The script refuses to start if either port is held by something it did not start.

### Environment and feature flags

`make preview-up` sets these **for the preview processes only**; no default in the repository changes:

| Variable | Preview value | Default | Effect |
| --- | --- | --- | --- |
| `DATABASE_URL` | `sqlite:///…/preview.db` | the repo default | which database the backend reads |
| `CONTRACT_DATA_PROVIDER` | `balldontlie` | *(unset)* | makes the stored GOAT contracts count as "the contract provider" (the rules then say "this player has no salary on file" rather than "no provider configured") |
| `PLAYER_INTELLIGENCE_PREVIEW` | `true` | `false` | enables `/players/{id}/intelligence`; the experimental estimates still need an explicit click and stay labelled |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8093` | `http://localhost:8000` | where the Next rewrite sends `/api/v1/*` |

No `BALLDONTLIE_API_KEY` is needed to *run* the preview. It is needed only to *refresh* data, and then only server-side (`backend/.env`, never `NEXT_PUBLIC_*`).

### Troubleshooting

* *"preview database not found"* → `make preview-reset` (restores it from the snapshot).
* *"port … is already in use"* → something else holds it; pick other ports with the env vars above. The script will not stop it for you.
* *The frontend will not start* → `…/run/web.log`. Next refuses a second `next dev` in the same folder. Turbopack refuses a `node_modules` symlink that points outside the project; the script detects a symlink and uses `--webpack`.
* *Cap Center says "contract provider not configured"* → the backend was started without the flag; use `make preview-up`, not a bare `uvicorn`.
* *Player Explorer shows no "Research preview"* → the backend is running without `PLAYER_INTELLIGENCE_PREVIEW=true` (the panel hides itself on a 404).
* *Numbers look stale* → the preview is a point-in-time copy: contracts and roster as of 2026-10-18/19, statistics 2025-26. It does not refresh itself.
* *Do not run `next build` while the preview is up* — it overwrites the dev server's `.next` output and every static asset 404s (restart with `make preview-down && make preview-up`).
* *Disk* → the machine this was built on had ~6 GB free; each database copy is ~71 MB. `make preview-down` and deleting `…/backups/*.pre-r93a-*.db` frees space.

## 2. How the preview database was built (reproducible)

1. `cp` the owner's `tradelab.db` (row-fingerprinted: it equals the R8.3 baseline in every table the ingest did not touch) → `base-r83-ingest.db` is that copy after the R8.1/R8.3 migrations and the **R8.3 ingest** (identities 691, contracts 453, injuries 92, statistics 4,055; made on 2026-10-18 into an isolated copy).
2. `alembic upgrade head` (→ `a7d3e1f9c204`, the R9.1 migration — **R9.3A adds no migration**).
3. Future contract years, 4 × 30 requests: `refresh-balldontlie contracts --season 2027-28 … 2030-31 --cache <dir>` (`BALLDONTLIE_TIER=goat`; key read server-side; 120 requests).
4. `make materialise-skills SKILL_ORIGIN=2025-26` on this **copy** (four candidate models, none promoted).
5. A byte-identical copy → `backups/preview-ready.db` with its SHA-256.

Raw provider responses are cached in `…/provider-cache/` (outside Git) so every contract load can be re-run at zero requests with `--cache <dir> --replay`; nothing licensed is committed.

## 3. Verify what the preview holds

```bash
cd backend
DATABASE_URL=sqlite:////Users/arjunvedere/pivot-r9-3a-data/preview.db .venv/bin/python -m app.cli r93a-readiness   # evidence classes, coverage, horizon, candidates
```

## 4. Activating on your real development database (not done by R9.3A)

R9.3A's data lives only in the preview copy. Activation is a four-step, reviewed, **reversible** procedure. Nothing below was run against `/Users/arjunvedere/pivot-work/pivot/backend/tradelab.db`.

### 4.1 What was rehearsed

On a disposable copy of your real `tradelab.db` (71 MB): migrate to head → `provider-data-activate` dry run (10,835 rows to add, 0 conflicts, 0 dangling) → apply → integrity check
and 30 of your 37 tables byte-identical to your original. The 7 that differ are exactly `alembic_version`, `contracts`, `contract_years`, `injuries`, `provider_entity_ids`,
`data_sync_runs` (43→52) and `model_versions` (19→23: the four candidates; your 19 rows, the active TEI model `20260825-0c89cb6a46a8` and its 512 estimates are unchanged); the two tables
your database did not have (`provider_stat_snapshots`, `candidate_estimates`) are created by the migration and filled by the activation. Then an API smoke test on the activated copy, then restore from the backup.
The rehearsal also found a real bug (a second SQLite connection inside the write transaction → "database is locked" on a database this size) which is fixed and has a regression test.

### 4.2 The procedure

```bash
# 0. Stop anything using the database (the shell in which you run the next commands must set DATABASE_URL):
export DATABASE_URL=sqlite:////Users/arjunvedere/pivot-work/pivot/backend/tradelab.db
cd /Users/arjunvedere/pivot-r9-3a-worktree/backend

# 1. Backup, and prove it reads (the tool also takes its own verified backup before writing):
mkdir -p ~/pivot-backups
sqlite3 "$HOME/pivot-work/pivot/backend/tradelab.db" ".backup '$HOME/pivot-backups/tradelab-pre-r93a-$(date +%F).db'"
sqlite3 "$HOME/pivot-backups/tradelab-pre-r93a-$(date +%F).db" "pragma integrity_check;"          # must print: ok
df -h ~ | tail -1                                                                               # need > 300 MB free

# 2. Rehearse on a copy first (optional but recommended): cp the DB to /tmp, point DATABASE_URL at the copy, run steps 3–4 there.

# 3. Migrate the schema (R8.1 → R8.3 → R9.1; additive; each has a downgrade):
.venv/bin/alembic upgrade head

# 4. Dry run, read the table report, then apply:
make activate-provider-data SOURCE=/Users/arjunvedere/pivot-r9-3a-data/preview.db
make activate-provider-data SOURCE=/Users/arjunvedere/pivot-r9-3a-data/preview.db APPLY=1 BACKUP_DIR=$HOME/pivot-backups
```

`provider-data-activate` refuses (and writes nothing) if: the schema revisions differ, a row exists in the target with different content (a *conflict*), a copied row names a player or team the target lacks
(*dangling*), the disk is short, or the backup does not verify. It copies only the allow-listed tables (`data_sync_runs` for provider jobs, `provider_entity_ids`, `contracts`, `contract_years`, `injuries`,
`provider_stat_snapshots`, **candidate** `model_versions` and `candidate_estimates`) and rolls the whole transaction back unless every other table is byte-identical afterwards.

### 4.3 Rollback

```bash
# servers stopped:
cp ~/pivot-backups/<the pre-r93a backup the tool printed> /Users/arjunvedere/pivot-work/pivot/backend/tradelab.db
# schema only (no provider data): alembic downgrade a9d4c6e18b53 — refuses while candidate models exist, by design (R9.1); restore the backup instead.
```

### 4.4 Turn the features on (after activation)

Add to `backend/.env` (or the environment of the server): `CONTRACT_DATA_PROVIDER=balldontlie` and, if you want the research preview, `PLAYER_INTELLIGENCE_PREVIEW=true`. Both are off by default.
Without the contract flag the stored contracts still price rosters, but several rules say "no contract provider is configured" while 453 contracts exist (`docs/r8-3/contracts-and-cap-readiness.md` §5).

### 4.5 Safe cutover options

| Option | When | How |
| --- | --- | --- |
| **A. Additive activation into your DB** (recommended) | you want R9.3A data in your normal dev setup | §4.2 on your real file after a rehearsal. Anything you saved since the preview was built is preserved. |
| **B. Switch the app to the preview DB** | you only want to look | `make preview-up` (already isolated). |
| **C. Make the worktree the primary checkout** | you want the code | merge `feat/r9-3a-goat-operational-tools` through your normal review — **not done here**; the shared `pivot-work` checkout is untouched. |

Before either A or C, re-run the suites in `qa-and-regression-report.md` §6 on the activated copy.
