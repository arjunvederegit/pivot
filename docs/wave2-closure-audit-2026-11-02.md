# Wave 2 closure audit and integration readiness (2026-11-02)

Independent re-assessment of `maint/wave2-correctness-overnight`. Nothing here was taken from the earlier report without re-checking; where a result is only carried over it says so. No implementation code was changed in this audit, no database or preview was opened, nothing was pushed or merged. Status words: **verified** (reproduced or read in code today), **inferred** (code reading only, not run), **unverified**.

## 1. Verified current state
| Item | Observed |
|---|---|
| Worktree / branch | `/Users/arjunvedere/pivot-wave2-overnight-worktree`, `maint/wave2-correctness-overnight` |
| HEAD at audit start | `25c3db70589fbb7a5a82c232bea275285354a6d1`; `664c4b2` is an ancestor; commits on top: `a77c7b5`, `5ec78ce` (code), `9d1fd2c`, `32d022d`, `25c3db7` (docs) |
| Working tree / stash | clean / none; free disk 12 GiB |
| Other worktrees | 11 read-only `status` checks; HEADs and dirty counts identical to before (R8 `pivot`, `r8-2`, `r9-1` carry 1 pre-existing dirty/untracked line each); `maint/application-integrity-w2` still `664c4b2`, clean |
| Pushed / merged | no; no remote branch contains HEAD |
| Services | owner dev backend `:8010` and R9.3A preview `:8093`/`:3093` running from other checkouts; not touched |

Re-run today at HEAD `25c3db7` (guarded launcher: `env -i`, in-memory `DATABASE_URL`, `CURRENT_SEASON=2025-26`, `CAP_LEAGUE_YEAR=2026-27`, **no** `ROSTER_SEASON` pin, `python -I -B`):
| Check | Result today |
|---|---|
| Backend `tests/integration` | **152 passed, 14 skipped, 0 failed** (reproduces the reported figure) |
| Backend `tests/unit` | **3200 passed, 5 skipped, 0 failed**, 1 warning (LangSmith beta notice), 442 s |
| Frontend full Vitest (jsdom, default-deny fetch, vetted read-only `node_modules`) | **35 files, 646 tests passed**, exit 0 |
| Whole-project `tsc --noEmit` (181/181 tracked TS files) | 0 diagnostics (run earlier today, frontend unchanged since) |
| `ruff check app tests` (CI step, whole tree) | clean |
| `mypy app` (CI step) | clean, 194 source files |
| Not run | browser smoke, `next build`, ESLint over the whole frontend, PostgreSQL tests, CI |

## 2. Final backup
- `~/pivot-backups/git/pivot-maint-wave2-correctness-overnight-25c3db7-2026-11-02.bundle`, 13,554,237 bytes, mode 0400, created with `git bundle create … maint/wave2-correctness-overnight`; name did not exist before. SHA-256 in the new `SHA256SUMS-maint-wave2-overnight-25c3db7.txt`; `shasum -a 256 -c` → OK.
- `git bundle verify`: "records a complete history". `list-heads`: `25c3db70… refs/heads/maint/wave2-correctness-overnight`. A scratch bare clone resolves `664c4b2`, `a77c7b5`, `5ec78ce`, `9d1fd2c`, `32d022d`, `25c3db7` as commits, has 5 commits beyond `664c4b2` and 172 in total (equal to the live branch), and `git fsck` reports no errors.
- Earlier bundles are untouched and their checksum files still pass (`…664c4b2…`, `…32d022d…`).
- **Off-device backup: still needed for the five new commits.** All bundles are on the same internal volume. The only off-device copy is the encrypted snapshot `Pivot Recovery/2026-10-23-W2/Pivot-W2-recovery-2026-10-23-r2.dmg` in the owner's iCloud Drive (W2.13B; the owner designated iCloud). It dates from 2026-10-23, so it can hold the Wave 2 chain only up to `664c4b2`, not `a77c7b5`…`25c3db7`. I did not open or modify it and copied nothing to any service. Suggested owner action: add this bundle and its checksum file to that iCloud folder (or any location the owner chooses), then `shasum -c` there.
- The closure document you are reading is committed after the bundle and is not in it; re-bundle after the final doc commit if that matters.

## 3. Adversarial review of `664c4b2..25c3db7` (code: `comparisons.py` +8/−2, `scenarios.py` +3/−1)

Method: re-read the diff; enumerated every write handler in `backend/app/api` (`git grep` for `commit|db.add|delete|@router.(post|put|patch|delete)`); searched the evaluation path for session writes; ran a **differential probe** — one throwaway test, run against an exported copy of `664c4b2` (`git archive`, no git metadata added) and against this branch, on the file-backed SQLite harness, with outputs normalised and diffed. The probe files were never committed (copies are in the evidence folder below).

### Fix 1 — `a77c7b5`, `POST /comparisons`
1. **Defect.** `ComparisonSet` committed before `get_comparison` evaluated the trades; a rejection (e.g. 409 cap data missing) left a durable comparison. An unknown `scenario_id` returned 201, was stored, and was silently replaced by default "custom" weights at read time.
2. **Change.** Scenario existence check (404 `scenario <id> not found`, after the existing trade check); `db.flush()` replaces the first `commit`; response built; single `commit`; return.
3. **Why it works.** The session factory is `autoflush=False, expire_on_commit=False`; the flush assigns the Python-side UUID, `get_comparison` reads the row from the identity map, and any exception unwinds through `get_db`'s `finally: db.close()` (rollback). Probe: at the single commit the session holds **no** staged additions, modifications or deletions beyond the comparison (`before_commit` capture: `new=[] modified=[] deleted=[]` after the flush), so moving the commit later persists nothing the old code discarded. The evaluation path has no write calls (`identity.link` has no caller there).
4. **Tests establish.** Valid create persists and reads back identically; unknown scenario → 404 and no row; unknown trade → 404, no row (control, passed before); rejected evaluation (cap rows deleted) → 409, no row. Red-then-green on the two real defects.
5. **Tests do not establish.** (a) The **valid-scenario** path — what Strategy Lab uses — has no permanent test before or after; I verified it only with the throwaway probe (response byte-identical to `664c4b2`: weights `performance 0.5, others 0.1`, focal team preserved). (b) PostgreSQL. (c) Concurrency, and how long the SQLite write lock is now held (see risks). (d) Rollback under a non-`DomainError` exception (reasoned from `get_db`, not tested).
6. **Risks.** SQLite: the write lock taken at the flush is now held while up to five trades are evaluated (previously released at the first commit); another writer on the same file could see `database is locked`. Duration on real data is unmeasured. PostgreSQL: row-level only, no concern. Pre-existing, unchanged, now documented: with a scenario whose focal team is not in the trades, a comparison returns **201 with zero alternatives and no explanation** (probe: `alternatives 0, excluded_from_ranking []`).
7. **Verdict: ACCEPT.** Recommend adding a permanent valid-scenario test (optional, test-only).

### Fix 2 — `5ec78ce`, `POST /scenarios`
1. **Defect.** Unknown `focal_team_id` → committed scenario + 6 weight rows, then HTTP 500 while serialising (`focal_team.abbreviation`); every later `GET /scenarios` → 500. Probe at `664c4b2`: `""` and `"AAA"` (abbreviation) each stored a scenario (`scenarios 3`, `weights 18` vs `1`/`6` on the branch) and `scenario_list` → 500.
2. **Change.** `if db.get(Team, payload.focal_team_id) is None: raise NotFoundError(f"team {…} not found")` before any `add`.
3. **Why it works.** The check precedes every mutation, so there is nothing to roll back; `ScenarioPatch` has no `focal_team_id`, so no other path creates the state.
4. **Tests establish.** Unknown team → 404, no scenario/weight rows, later list still 200; valid create → 201, abbreviation, weight rows, listed. Probe adds: `""` and abbreviation → 404; valid response identical to baseline.
5. **Do not establish.** PostgreSQL (there the FK would already have rejected the insert, so the pre-fix harm was SQLite-specific); that `PATCH` is safe.
6. **Risks.** The id must be the internal team UUID (callers sending an abbreviation or NBA id now get a clean 404 instead of a 500). **Related pre-existing gap (confirmed, not caused by the fix):** `PATCH /scenarios/{id}` accepts `horizon_years=-3` and `risk_tolerance="banana"` (probe: 200, stored) while `POST` rejects both (422). A search found no analytical code reading `scenario.risk_tolerance`/`horizon_years` (only a default in `domain/mandate.py`), so no output is affected today.
7. **Verdict: ACCEPT.**

Neither fix touches a formula, weight, cap computation or model; the only behavioural change is a rejection in place of silent substitution or a 500. **No material defect was found in either fix.**

## 4. Wave 2 requirement status

| # | Requirement | Status | Evidence |
|---|---|---|---|
| A | Unsafe in-app training/scoring guidance | **VERIFIED COMPLETE** | `git grep` over `frontend/app|components|lib` and user-facing `backend/app` strings: no `make train`/`make score` guidance remains (only developer docstrings/comments in `analytics/train.py`, `score.py`, `registry.py`, `evaluation.py:333`). Replacement texts pinned by `test_acquisition_targets.py:64`, `test_candidate_generator.py:205`; `make materialise-skills` hint (`player_intelligence/service.py:356`) says "on a copy of the database" and writes candidates no serving path reads. |
| B | Validation before commit | **VERIFIED COMPLETE** (SQLite harness) | All five write handlers in `api/` (`POST /trades`, `POST /comparisons`, `POST`+`PATCH /scenarios`, `GET /trades/{id}/report`) reviewed; the first three are covered by file-backed tests (11 + 4 + 2) observing commits through a separate engine. PostgreSQL not run. |
| C | Cap-parameter season selection | **VERIFIED COMPLETE** for the defect | `ingestion/quality.py:313` uses `settings.cap_league_year`; every other lookup already did; `cba/builder.py:load_cap_params` raises `DataUnavailableError` on a missing year with no fallback; tests cover the cap-year × stats-year matrix. Residual: the cap year is pinned config (`CAP_LEAGUE_YEAR=2026-27`) with a rollover warning (`data_provenance.py:461-470`); there is no per-request season by design. |
| D | Evaluator API response validation | **PARTIALLY COMPLETE** | `POST /trades/evaluate` is schema-validated (`lib/tradeEvaluate.ts` → `evaluateResponseSchema`, malformed 200 → `contract_mismatch`). **`POST /trades/validate` (live rules check) and `POST /comparisons` responses have no runtime schema** (`useTradeAnalysis.ts:270`, `strategy-lab/page.tsx:204`), and `components/trade/desk.tsx:51` dereferences `validation.rule_results.filter` unguarded. A malformed 200 would throw at render (inferred, not reproduced in a mounted page). Same-origin first-party API, so likelihood is low. |
| E | Stale-response protection | **IMPLEMENTED BUT NOT FULLY VERIFIED** | Epoch + request-id reducer (`lib/useTradeAnalysis.ts`), reveal and callback ownership; 646 Vitest tests pass; the page was never mounted in a browser; a transient rules-check failure persists until the draft changes (accepted product limit). |
| F | TEI mislabelled `VALIDATED` | **VERIFIED COMPLETE** | `services/intelligence.py:236` is `Confidence.HEURISTIC`, formula untouched; the only `Confidence.VALIDATED` definitions left are the enum; the label is computed per response and **not persisted**; README/`limitations.md` say "interpretable heuristic"; no UI copy calls TEI validated. Caveat: `main` and the R8–R9 branches keep `VALIDATED` until this chain lands; `docs/resume-bullets.md:48` says "validated impact modeling". |
| G | Other high-severity issues | **PARTIALLY COMPLETE** | Two found and fixed (§3). Open, minor: PATCH-scenario validation asymmetry; silent omission in comparisons (above); legacy `fillna(0.0)` in `analytics/impact.py`/`features.py` (report-only per CLAUDE.md). |

### Deferred items
| Item | Finding | Class |
|---|---|---|
| Saved-trade response validation | `tradeDetailSchema` **is** applied at four consumer sites (`trades/[tradeId]`, `trade-evaluator`, `strategy-lab`); the deferral is its depth (it reuses the loose `teamEvaluation`), and `/comparisons`, `/trades/validate` have none (see D). | The `/validate` and `/comparisons` part is a **correctness gap**; deepening `tradeDetailSchema` is **optional hardening**. |
| `adopt()` | Exported by `useTradeAnalysis.ts:319`; **no production caller**; only `selected-team.test.tsx` and `trade-analysis.test.tsx` use it. | Optional cleanup; no correctness risk. |
| `make seed-config` / `make sync-data` hints | ~25 in-app strings recommend ingestion commands (`sync-data`, `refresh`, `import-*`, `fetch-transactions`, `import-kaggle` "multi-GB", `seed-config`), e.g. `data_health.py:338,430,469`, `data-health/page.tsx:653`, `salary-cap-center/page.tsx:81`, `cba/builder.py:31`, `ingestion/quality.py:320`. None is training, scoring or model replacement; per the README `sync-data` upserts ingested tables over the network and `seed-config` writes cap rows. | Optional wording/policy (owner decision); not a Wave 2 correctness item. |

## 5. Could historical defects have affected persisted data?
Answered from code only; no database, preview or `.env` was opened. SQLite does not enforce foreign keys here (`db/base.py` sets no `PRAGMA foreign_keys`), so the owner's dev database and any SQLite preview copy can hold such rows; PostgreSQL could hold the ghost-row kinds but not dangling FKs.
| Past defect | Possible leftover | Visible symptom |
|---|---|---|
| `POST /trades` early commit (pre-`54ec97f`) | "ghost" trades: proposal/teams/assets with **no** rule results or evaluations, left by rejected requests (missing cap data, duplicate/phantom move, unknown scenario/player). They would appear in trade lists and the Strategy Lab picker. | none until opened |
| `POST /comparisons` (pre-`a77c7b5`) | comparisons from rejected evaluations; comparisons with an unknown `scenario_id` | silently default weights |
| `POST /scenarios` (pre-`5ec78ce`) | scenarios with unknown `focal_team_id` | `GET /scenarios` returns 500 — so **if that endpoint works on a database, none exists** |
| `validate_data` cap check (pre-`f2410e6`) | a spurious open `cap_parameters_missing` row naming the statistics season | Data Health issue only; no analytical effect |
| TEI `VALIDATED` | nothing persisted (computed per response) | none |
| Train/score guidance | nothing persisted | none |

Owner-run, **read-only, on a copy** (suggested; not run by me; table names taken from `db/models.py`):
```sql
-- ghost trades
SELECT t.id, t.name, t.created_at FROM trade_proposals t
 WHERE NOT EXISTS (SELECT 1 FROM trade_evaluations e WHERE e.trade_id = t.id)
   AND NOT EXISTS (SELECT 1 FROM trade_rule_results r WHERE r.trade_id = t.id);
-- dangling scenarios / comparisons
SELECT id, name, focal_team_id FROM scenarios WHERE focal_team_id NOT IN (SELECT id FROM teams);
SELECT id, name, scenario_id FROM comparison_sets
 WHERE scenario_id IS NOT NULL AND scenario_id NOT IN (SELECT id FROM scenarios);
-- spurious quality issue
SELECT id, check_name, severity, resolved_at FROM data_quality_issues WHERE check_name = 'cap_parameters_missing';
```
Note: a trade with no stored results is not proof of a ghost (any trade created before result persistence existed would match); treat hits as candidates to inspect. `purge-fixtures` (dry run by default) is the existing tool for fixtures; no repair tool exists for the rows above and none was written.

## 6. Release verification gaps
| Gap | Risk left untested | Needed for Wave 2 closure? | Needed before `main`? | Smallest safe way | Safe for existing DBs/previews? |
|---|---|---|---|---|---|
| **CI run** | Python 3.12, coverage ≥ 85 %, Alembic check, ESLint, `next build`, Docker builds, dependency/secret scans, Playwright | No | **Yes** (it is the integration gate; also supplies the next two rows) | Open a PR from this branch; CI triggers on `pull_request`. Needs push approval. | Yes — hosted runners, demo DB only |
| **Browser smoke** | Evaluator page behaviour, stale-response behaviour, Strategy Lab compare and Team Outlook "save strategy" in a real page | Desirable (E is "not fully verified" without it) | Yes, via CI e2e: `decision-flow.spec.ts` "full flow: team outlook → strategy → trade evaluator → rules → evaluate → save → compare" already exercises `POST /scenarios` and `POST /comparisons` | CI Playwright, or locally `make e2e` on a **dedicated** `E2E_DB` with new ports. Local use also needs frontend dependencies in this worktree and (per `make seed-demo`) trains/scores that demo DB — outside this session's rules. | CI: yes. Local: only on a dedicated DB and fresh ports, never `:8010`/`:8093`/`:3093` |
| **`next build`** | Compile/route errors, font/asset fetch | No (types and tests pass; zero frontend diff vs `664c4b2`) | Yes, via CI | CI `frontend` job (`npm run build`). Locally: `.next/` write plus ~560 MB of dependencies; not done. | CI: yes |
| **PostgreSQL** | Migration chain on PG, season-type constraints, ingestion idempotence | No (the diff has no model or migration change; the 14 tests are the PG-compat module) | No (CI does not run them either; recommended before any PG activation) | `~/pivot-tools/pg16` exists (PostgreSQL 16.2 binaries). A throw-away cluster in a scratch directory (`initdb -D <scratch>`, loopback port, socket under scratch) plus `PIVOT_TEST_POSTGRES_URL` would satisfy the suite's own "unique database, drop only that" design. I did **not** run it: it starts a server, and that tooling's README states a full cluster start/test run was not performed from its current location (steps "unverified from this location"). | Yes if the cluster is private to scratch; never point at an existing server |

## 7. Remaining risks
- **Critical:** none confirmed.
- **High:** (1) The branch cannot be integrated alone: it is the tip of a 29-commit linear stack (§8); the actual remote `main` was not re-checked (local `origin/main` is stale at `ccaf0b8`). (2) The Wave 2 chain exists only locally and in same-volume bundles. (3) CI has never run on it.
- **Medium:** `/trades/validate` and `/comparisons` responses unvalidated (D); evaluator page never mounted in a browser; SQLite write lock now held through comparison evaluation (duration unmeasured); possible ghost/dangling rows in existing databases (§5, unchecked); carried open items from the R9.2 merge handoff: frontend dependency vulnerabilities (1 critical, 10 high per `npm audit`), unresolved licensing of NBA.com-derived aggregates, no branch protection on `main`.
- **Minor:** PATCH-scenario validation asymmetry; silent zero-alternative comparison; `adopt()` dead code; ingestion-command hints; `resume-bullets.md` wording; `risk_tolerance` is `String(20)` and PATCH does not bound it (would error on PostgreSQL).

## 8. Proposed integration procedure (owner approval needed at each step; nothing below was done)
1. **Re-verify the remote** (read-only): actual `main` SHA (the R9.2 merge handoff recorded `eee121e`; local refs say `ccaf0b8`), open PRs, branch protection.
2. **Understand the stack.** From the R9.2 tip `1c7e9bb` to this HEAD: R9.3A 7 commits → headshots 1 → W1 4 → W2 12 → this session 5 = **29 commits, 117 files, no new Alembic migration**. R9.3A (provider-activation tooling) and the dev-gated headshots are ancestors, so they cannot be skipped without rewriting history.
3. **Choose** (a) one PR of the whole stack, or (b) sequential PRs: R9.3A (existing remote branch) → headshots → W1+W2. Merge-commit method in both cases so every hash is preserved (as for PR #4/#5).
4. **Before pushing:** make the Wave 2 decision in §9 (D), refresh the bundle, copy it off-device, run a Gitleaks pass on the new commits with the existing allowlist.
5. **Open the PR; let CI run** (backend, assistant, frontend incl. lint/build, e2e, docker, security). Treat any failure as blocking; do not merge on a local pass alone.
6. **Merge only after** green CI and the owner reads the diff of `a77c7b5`, `5ec78ce` and the earlier W2 commits; do not enable auto-merge.
7. **After merge:** no activation, retraining, materialisation or real-data migration is implied. Run the §5 queries on a copy of each database before first use of the merged build.

## 9. Decision

**CONDITIONAL — SPECIFIC ACTIONS REQUIRED.** No confirmed material defect; both implementation fixes are accepted. The smallest set of actions before integration:
1. **Owner decision on Issue D's residual:** either accept in writing that `/trades/validate` and `/comparisons` responses stay unvalidated (documented risk), or authorise one small, tested change (a schema for the rules-check response and for the comparison board, applied via the existing `api.post(path, body, schema)` signature). Recommended: authorise it; it is the only part of the original acceptance criteria ("validate external response boundaries") not yet met.
2. **Authorise a push of this stack to a PR and let CI run** — the integration gate and the source of browser/build/lint evidence; re-verify the remote first (§8).
3. **Copy the final bundle off-device** and refresh it after the last doc commit.
4. Owner-run §5 queries on database copies before first use of the merged build (recommended, not blocking Wave 2 code closure).

Wave 2 is **not** declared complete: item 1 is open, E lacks browser evidence, and nothing has run on CI.

## 10. Recommended next milestone
Close Wave 2 with the small response-validation change plus a permanent valid-scenario comparison test, run it through CI, then integrate the stack. Only after that begin the thesis data-readiness task already specified in `docs/wave2-overnight-2026-10-30.md` §9 (a one-season stint-reconstruction feasibility audit), which needs no application change.

## Evidence
Scratch evidence (probe script, baseline/branch probe outputs, re-verification logs) is copied to `~/pivot-backups/maintenance/w2-closure-audit-25c3db7-2026-11-02/` with `SHA256SUMS.txt`.
