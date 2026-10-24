# R8.2 — Application cutover verification

*2026-10-18. Code: `feat/r8-2-activation-checkpoint` @ `27d2ce4` (pushed, clean worktree `/Users/arjunvedere/pivot-r8-2-worktree`). No code change was needed.*

## Result: **R8.2 is ready for ordinary local development use.** Not yet your primary environment — that is a switch only you make (procedure below).

## What was run
Isolated backend (`:8044`) + production frontend build (`:3044`) from the checkpoint code over a **logically identical copy** of the refreshed
`tradelab.db` (SQLite backup API; hash over all 37 tables equal; rosters 2025-26 = 530, 2026-27 = 526; schema `a9d4c6e18b53`, unmigrated), with your
`nbalogos`/`nbaplayerimages` directories mounted read-only. Your servers (`:8010`) and `tradelab.db` were not touched (0 open handles before/after, same row counts).

## Browser smoke (headless Chromium, 30 checks, 0 application errors)
**0 page errors, 0 console errors, 0 HTTP ≥ 400; 158 logo/photo requests, 0 failed; every `<img>` on Workspace (50), player page (6) and Cap Center (35) decoded.**

| Area | Verified |
| --- | --- |
| Workspace (PHI selected) | "2026-27 rosters · 2025-26 statistics"; "2025-26 record"; "17 rostered players"; 6 links carry the selected team |
| Team Outlook | "2026-27 roster [provisional] · statistics: 2025-26 regular season"; needs-limitation notice; Trade / Compare / Cap links keep the team |
| Player Explorer `?team=PHI` | "2025-26 totals" + "2026-27 rosters"; 8 cards "totals with <old team>"; PHI filter shows only PHI's roster players |
| Identity | roster ↔ player page agree on name, NBA id, team and roster season (6/6); 573 explorer rows, 0 duplicates; Jaylen Brown = PHI now, totals with BOS; mover, rookie (no invented line) and unlisted player (no team) pages correct |
| Trade Evaluator | selected team's 17-player roster loads; full evaluate flow; "Limited analysis" + "Input data: 2025-26 season stats, 2026-27 roster snapshot…, cap year 2026-27" |
| Strategy Lab | 3 saved deals; compare renders; "Unverified roster membership" notice for the deal with a player on no roster |
| Cap Center | "0 of 17 salaries on file", 17 "not on file" cells, no dollar figure, payroll/cap/apron "withheld" |
| Ask Pivot | off by default (`/assistant` → 404) |

One check failed on first run (Trade Evaluator roster text read before the async load); re-run with a proper wait: PHI header visible, Brown listed, roster 17 — a script timing artifact, not an app fault.

## Making R8.2 your primary local environment (nothing here replaces a running process)
The shared checkout is still R8 (`5cada84`). Two safe options; **A is recommended** (no branch switch, no migration, fully reversible).

**A — run from the R8.2 worktree.** `Settings` reads `backend/.env` of whatever tree it runs in (gitignored), so one file points the whole worktree at your real database:
```bash
# 1. confirm nothing writes the DB (expect no output), and ports 8000/3000 are free
lsof /Users/arjunvedere/pivot-work/pivot/backend/tradelab.db; lsof -nP -iTCP:8000 -iTCP:3000 -sTCP:LISTEN
# 2. one-time config (no secrets yet)
cat > /Users/arjunvedere/pivot-r8-2-worktree/backend/.env <<'ENV'
DATABASE_URL=sqlite:////Users/arjunvedere/pivot-work/pivot/backend/tradelab.db
ASSET_LOGOS_DIR=/Users/arjunvedere/pivot-work/pivot/nbalogos
ASSET_PLAYER_IMAGES_DIR=/Users/arjunvedere/pivot-work/pivot/nbaplayerimages
ENV
# 3. start (backend :8000, frontend :3000 — same ports as before; your :8010 server is unaffected)
cd /Users/arjunvedere/pivot-r8-2-worktree && make dev
# 4. check
curl -s localhost:8000/api/v1/data/season-context | python3 -m json.tool | head -20   # roster 2026-27, performance 2025-26
```
Keep `CURRENT_SEASON` and `ROSTER_SEASON` **unset**. Roster refreshes from the same tree: `make refresh DATASETS=rosters ARGS=--roster-only` (**never** the plain command — it rewrites known players' bio fields and moves the analytics).
Back out: stop `make dev`, delete that `.env`. The database needs no change either way.

**B — switch the shared checkout** (`git switch feat/r8-2-activation-checkpoint` in `/Users/arjunvedere/pivot-work/pivot`, then restart its servers). Works without a migration, but it changes the checkout your other session uses — your call.

Do **not** run `make train`/`make score` (see activation report §E). Optional: the `.env` is also where the BALLDONTLIE key goes (below).

## BALLDONTLIE free-key preflight — next step confirmed
Adapter and harness re-reviewed: key is a `SecretStr`, sent only as the `Authorization` header and redacted from errors; pacing 80 % of the tier limit (free = 5/min); teams/players are free-tier endpoints; 69 adapter/harness tests pass; `make balldontlie-preflight` on the refreshed data reports **no blocker except the missing key** (and the 2026-27 roster is staged). No key exists in the environment; nothing was called.
Order for the owner (from the R8.2 worktree, after option A so it uses the real DB):
1. Create a free key; add `BALLDONTLIE_API_KEY=…` and `BALLDONTLIE_TIER=free` to the worktree's `backend/.env` (never in chat or a commit).
2. `make link-balldontlie DRY=1` — expect 30/30 teams linked and a sensible player link rate; it rolls back (writes nothing). The full player list is ~50 pages at 5/min ≈ 10–13 min; add `ACTIVE_ONLY=1` for a faster pass. If it 404s, set `BALLDONTLIE_API_PREFIX=/v1` and retry; if it still fails, stop.
3. `make balldontlie-preflight` → must print `"ready": true` (set `BALLDONTLIE_TIER=trial` only when starting the paid trial).
So: **yes, we can proceed directly to the free-key evaluation** once you have a key. Remaining limitations unchanged: the adapter has never touched the live API; salary agreement stays inconclusive without a contract snapshot; the roster is provisional (re-run `--roster-only` after opening night / the cut-down).
