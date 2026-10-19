# R9.3A — final QA sign-off

Verdict: **Complete for the isolated preview. Not activated on the primary development database (by design: reviewed, rehearsed procedure only).**
Reviewers were AI agents (four structured rounds + a final verification pass by the author); there was no human QA. Evidence paths are in `qa-and-regression-report.md`.

## Test and check results (final, on the release candidate)

| Check | Result |
| --- | --- |
| Backend, SQLite — full suite | **3,327 passed · 19 skipped · 0 failed** (R9.2 baseline: 3,188 / 19 / 0; +139 tests; skips: live Ollama, PostgreSQL-URL tests, two local-data files) |
| Backend, PostgreSQL 16 — new and related files (`test_r93a_*`, round-1/4 regressions, injury, readiness, ingestion, `test_postgres_compat`, disclosed-coverage) | **193 passed · 0 failed** |
| Frontend unit tests (vitest) | **345 passed · 0 failed** in 25 files (R9.2 baseline: 311) |
| `ruff check` (backend) · `mypy app` (194 files) · `eslint` · `tsc --noEmit` | clean · clean · clean · clean |
| Frontend production build (`next build --webpack`) | succeeds |
| Alembic: `alembic check` (model/migration drift) and upgrade→downgrade→upgrade on a fresh DB | no drift; round trip clean |
| Alembic upgrade→downgrade on a **copy of the real database** | all 37 tables byte-identical to the original afterwards |
| Validation batteries vs R9.2 on the same data (`adversarial`, `acquisition`, `comparable`) | byte-identical outputs (timestamps masked); the acquisition battery's one failing check (`same_need_teams_hear_more_alike`, 0.0302 < 0.05) fails identically on R9.2 — pre-existing |
| 300 random real-data trades: engine status, R9.2 vs R9.3A | identical on every trade (161 conditionally valid, 139 verified illegal) |
| Replay re-ingestion of two real seasons (idempotency, 0 requests) | complete, 0 created / 0 updated / 0 not re-seen |
| Mutation / controlled defects | 35 own planted defects (all killable ones caught after tests were added) + 44 by the round-4 reviewer (survivors triaged; 4 low-impact open) |
| Playwright e2e | **not run** (requires the demo database and a browser harness; the existing specs reference only unchanged legality labels) |
| Browser smoke (author) | Cap Center, Trade Evaluator (carried player → partner → deal), Player Explorer tabs, Data Health at 1440 px; 390 px overflow check — see report §7 |

## Acceptance criteria

### Data activation
| Criterion | State | Evidence |
| --- | --- | --- |
| GOAT ingestion functional on isolated data | ✅ | 4 league-wide season loads + replay idempotency; `goat-operationalization.md` |
| Current-season contract coverage quantified | ✅ | 434/526 (82.5 %), per-team 76–89 %, 0/30 complete — `make r93a-readiness` |
| Contract source and financial season preserved | ✅ | every record names season, source, retrieval time |
| Missing salaries unknown, not zero | ✅ | financials/trade/Cap Center tests; mutation M09–M10, M21 |
| Historical and current-season records distinct | ✅ | per-season rows; thresholds per season; season-mismatch withheld |
| R9.2 measured intelligence retrievable | ✅ | `/players/{id}/intelligence`; Explorer Advanced tab |
| Candidate access gated | ✅ | three barriers (`player-intelligence-integration.md` §2); round-2 scan of 573×6 |

### Cap Center / Trade Evaluator / Player Explorer / connected tools
| Criterion | State | Evidence |
| --- | --- | --- |
| Cap Center shows verified current-season records, player contracts, completeness; no partial-as-payroll; no unsupported apron claim | ✅ | browser + 30-team reviewer pass; `TestTeamFinancials` |
| Trade Evaluator uses verified records, meaningful salary comparison, handles missing contracts, separates verified from unchecked rules, no unsupported legality, preserves basketball evaluation | ✅ | 300-trade + battery equivalence; assessment tests; reviewer scans (0 `passes_checked_rules`/`verified_legal` in 1,307 trades) |
| Player Explorer uses the shared service, measured vs experimental, season/sample/provenance/uncertainty, graceful unavailability | ✅ | `skill-evidence` tests; round-2 review |
| Player identity survives Explorer → Trade Evaluator; context preserved; `?team=` unchanged; shared data consistent | ✅ | `connected-workflows.md`; 526-player cross-endpoint consistency scan (0 disagreements) |

### Real product verification
| Criterion | State | Evidence |
| --- | --- | --- |
| Working local preview | ✅ | `make preview-up` (script exercised on alternate ports and the real ones) |
| Core workflows tested in a browser | ✅ | report §7 and round-3 review |
| Current NBA data appears where available | ✅ | 2026-27 rosters/contracts, 2025-26 statistics |
| Existing visual design preserved | ✅ | round-3 review; no logo/palette change |
| Regression tests pass | ✅ | table above |
| Multiple independent QA rounds documented | ✅ | `qa-and-regression-report.md` §2–§5 |
| Critical/high findings resolved or blocked | ✅ | 2 high (same defect found twice; the dropped-team read) fixed; no open critical/high |

### Safety
| Criterion | State | Evidence |
| --- | --- | --- |
| Existing model versions intact; TEI estimates preserved | ✅ | 27 core tables row-identical in the preview snapshot vs your database; 19 model rows + active TEI model (512 estimates) unchanged after the real-data rehearsal |
| No shared database overwritten | ✅ | only copies touched; `tradelab.db` read-only (copies made with `cp`/`.backup`-equivalent reads) |
| No credentials committed; none logged or sent to the browser | ✅ | diff scan; key read server-side from an existing file, never printed |
| No raw licensed GOAT dump in Git | ✅ | cache is `~/pivot-r9-3a-data/provider-cache/` |
| No unrelated servers interrupted | ✅ | :8000/:8010/:3000 untouched; only own processes stopped |
| No merge to `main` | ✅ | branch only |

## Known limitations and open items (not blocking the preview)

* No complete payroll for any team; no options / guarantees / no-trade clauses / signing dates; roster is provisional; one unlinked provider identity (*Hansen Yang*, and *David Jones Garcia*); no second source verified any individual contract amount.
* Trade-deal uncertainty bands remain the uncalibrated TEI band (R9.2 carry-over, untouched).
* Open low-impact items: three-team lane name clipping and unlabeled scrollers (pre-existing), a bogus `team=` id silently showing ATL (pre-existing), four planted defects not killed by tests (see report §5), `data_sync_runs` keeps the first 300 unpriced rows per run.
* Playwright e2e not run; PostgreSQL run covers the new and related files, not the entire suite; provider licensing for a shared deployment is unresolved; the research preview is experimental by construction.
* Activation on the primary database is the owner's reviewed step (`local-preview-and-activation.md` §4).
