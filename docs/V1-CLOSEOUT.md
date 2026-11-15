# Pivot V1 closeout (2026-11-15)

## Status

V1 is a **closeout checkpoint**: the completed, tested work through R9.3A, the Wave 1 and Wave 2
maintenance, and an opt-in safeguard for official NBA headshots, all integrated into `main` with CI
green. It is **not declared production-ready.** The limitations below are known and open at the time of
the checkpoint; none of them was introduced by the closeout, and none is hidden by it.

The checkpoint is the annotated tag `pivot-v1-closeout-2026-11-15`, created on the `main` commit that
merges this document once CI on that commit is green. Earlier tags, including `rosterlab-r1-r7-complete`, are unchanged.

## What V1 contains

- R1–R7 (RosterLab): the trade engine, CBA legality, evaluation, comparison and the product shell
  (tag `rosterlab-r1-r7-complete`).
- Pivot redesign and the Ask Pivot V2 sandbox (off by default), PRs #1–#2.
- R8 canonical data foundation, R9.1 evaluation infrastructure, R9.2 skill engine, R9.3A GOAT
  operational tools, PRs #3–#6; season-rollover and `APPLY` safety fixes, PRs #7–#8.
- Maintenance Wave 1 (documentation honesty, development-safety guidance) and Wave 2 (trade, scenario
  and comparison write atomicity, cap-year check, TEI labelled heuristic, evaluator response
  validation and stale-response protection): PRs #10–#12.
- Official NBA headshots, **opt-in and off by default** because usage rights are not cleared: PR #9.

## How the last 23 commits arrived

Twenty-three commits written between 2026-10-20 and 2026-11-02 existed only on local branches. They
were integrated through four sequential pull requests (#9, #10, #11, #12), each with a
merge commit, so every original commit kept its SHA, author and date. Nothing was squashed, rebased,
force-pushed or re-dated. One new commit (the headshots opt-in safeguard) was added on top of the
original headshots commit; it is dated when it was written.

## Verification at closeout

- **Integration pull requests.** #9 (headshots), #10 (Wave 1 docs), #11 (Wave 2 fixes) and #12 (Wave 2
  overnight) were each merged with a merge commit after all nine CI checks passed on a `main`-based
  merge ref (CI runs 43474955945, 43745087096, 43745095805, 43745103883). Each merge produced exactly the
  tree CI had tested.
- **A false alarm worth recording.** The first CI runs on #10–#12 failed ten clock-dependent tests. Those
  PRs had been stacked on branches that predate the calendar-pinning fix in PR #7, so the runs did not
  test the real integrated result. Re-run against `main` they passed, and a local run reproduced the ten
  failures on the stacked branch and none on the integrated tree.
- **Local checks on the integrated tree** (`main` plus all four branches): full backend suite 3,792
  passed, 19 skipped, 0 failed (Python 3.11 locally; CI uses 3.12); frontend `tsc` and ESLint clean,
  Vitest 650 passed, production build succeeds; gitleaks 8.24.3 found no leaks in the 23 historical
  commits.
- **Headshots.** With the opt-in flag unset, a development server and a production server both answer
  `/api/headshot-status` with 404. The tests stub the flag and `fetch`; no request to the NBA CDN was
  made during testing.
- **History.** All 23 historical commits are reachable from `main` with their original SHAs, authors and
  author dates; nothing was squashed, rebased, force-pushed or re-dated. One new commit (the headshots
  opt-in safeguard) was added on top of the original headshots commit.
- CI on the final `main` commit is recorded in the annotation of the tag.

## Known limitations (open at closeout)

| # | Area | Limitation | Impact | Planned handling |
|---|---|---|---|---|
| 1 | API contracts | The responses of `POST /trades/validate` and `POST /comparisons` have no runtime schema; they are cast by a TypeScript generic. `components/trade/desk.tsx` reads `validation.rule_results` without a guard. `POST /trades/evaluate` *is* validated. | A malformed HTTP 200 would throw at render (inferred, not reproduced in a mounted page). Same-origin first-party API, so likelihood is low. Error statuses are handled. | Add schemas and a permanent valid-scenario comparison test (post-closeout priority 2) |
| 2 | Browser coverage | The evaluator's stale-response protection (epoch and request-id reducer) has unit coverage; the author did not exercise the page in a real browser. CI's Playwright run is the only browser evidence. | Behaviour under real timing is unobserved. A transient rules-check failure persists until the draft changes (accepted product limit). | Browser check or targeted e2e |
| 3 | SQLite locking | `POST /comparisons` now holds the SQLite write lock while evaluating up to five trades. Duration on real data is unmeasured. | A concurrent writer could see `database is locked`. PostgreSQL is unaffected. | Measure; revisit if observed |
| 4 | Scenario validation | `PATCH /scenarios/{id}` accepts values `POST` rejects (for example `horizon_years=-3`, `risk_tolerance="banana"`). `risk_tolerance` is `String(20)` and unbounded on PATCH. | No analytical code reads these fields today; an over-long value would error on PostgreSQL. | Share the POST validation |
| 5 | Comparisons | A comparison can silently omit an alternative (zero-alternative case). | Fewer rows than selected, without a message. | Surface an explicit state |
| 6 | Legacy analytics | `fillna(0.0)` remains in `analytics/impact.py` and `analytics/features.py` (report-only paths). | Missing values are read as zero in those reports. | Replace with explicit unavailable states |
| 7 | Existing databases | A database that served requests before the Wave 2 fixes may hold rows those defects left behind: trades without stored results, comparisons naming an unknown scenario, scenarios with an unknown focal team, a spurious `cap_parameters_missing` issue. | Odd rows in lists; `GET /scenarios` returning 500 would indicate the last case. Not checked on any real database. | Run the read-only queries in `wave2-closure-audit-2026-11-02.md` §5 on a copy |
| 8 | In-app wording | About 25 in-app strings recommend ingestion commands (`sync-data`, `import-*`, `seed-config`). None recommends training, scoring or model replacement. | Users are pointed at commands that write to the database or use the network. | Wording and policy decision |
| 9 | NBA headshots | Usage rights are not cleared. The official source is off unless a developer sets `NEXT_PUBLIC_PIVOT_OFFICIAL_HEADSHOTS=on` under `next dev`, and can never run in a production build. | Default behaviour shows the local photo or a placeholder. Turning it on makes that developer's browser request images from the NBA CDN. | Obtain written permission before any change |
| 10 | Data licensing | Licensing of NBA.com-derived aggregates is unresolved. | Redistribution terms are unconfirmed. | Owner decision |
| 11 | Dependencies | **Unresolved.** GitHub reported 63 open Dependabot alerts (re-read on 2026-11-15): 7 critical, 25 high, 26 medium, 5 low. `npm audit` is non-blocking in CI. `next` 16.2.10, a direct runtime dependency, is below the patched versions for five critical advisories. The 7 critical alerts were triaged (below); the high, medium and low alerts were not. | None of the five critical advisories is reachable in the local-only V1 configuration, but the package is unpatched. Exposure on a network would change that. | **Priority:** upgrade `next` to 16.4.1 before any external deployment; then triage the rest |
| 12 | Repository governance | `main` has no branch protection or rulesets. | Merges rely on convention; the V1 integration PRs were merged by hand after CI. | Add protection |
| 13 | PostgreSQL | The PostgreSQL-compatibility tests are not run by CI. | PostgreSQL behaviour is verified only by occasional manual runs. | Add a CI job before any PostgreSQL deployment |
| 14 | Models and data | TEI is a heuristic composite (see the model card and `limitations.md`); provider contract data is partial and provider-data activation has not been performed on any real database. | Outputs carry the confidence labels documented elsewhere; some financial figures are withheld. | See `limitations.md` and `r9-3a/local-preview-and-activation.md` |

## Critical dependency alerts: unresolved, with a priority fix

All seven open critical alerts are the same package: `next` (direct, runtime, `frontend/package.json`,
installed 16.2.10). They are five distinct advisories; two are listed twice, once per manifest. The fix
for all five is `next` 16.4.1 (a minor bump within 16.x). **Treat all seven as unresolved
vulnerabilities.** The reachability assessment below lowers the urgency for the current local-only
configuration; it does not close the alerts. It is by configuration and by probing a local production
build, not a penetration test.

| Advisory | Condition for exploitation | Pivot V1 | Verdict |
|---|---|---|---|
| GHSA-p293-qw3h-jr36 (RCE) | Server hosted on a Windows filesystem | Developed on macOS; Docker image is `node:22-alpine`; CI is Linux. No Windows hosting | Not reachable |
| GHSA-2xp9-vwfh-vxw4 (RCE, AVIF in image optimization) | An AVIF file passes through the image optimizer | No `next/image`, no `images` config, no remote patterns, no AVIF files, no uploads. The optimizer endpoint exists by default, but on a local production build it served only the project's own `public/` PNGs and answered 400 for SVG, for a remote URL and for the proxied backend asset path | Not reachable without attacker-controlled images |
| GHSA-2464-fhr4-w9xw (RCE, image optimization on glibc Linux; needs `sharp` ≥ 0.35.5) | Attacker-supplied image content on a glibc-based Linux host | Lockfile has `sharp` 0.34.5 (below the fix). The Docker image is musl (alpine); development is macOS. The same optimizer limits as above apply | Not reachable in V1 hosting; the patched `next` brings a fixed `sharp` |
| GHSA-vcvr-r3jv-pc5j (RCE, `next/og` `ImageResponse`) | Attacker-controlled values rendered by `ImageResponse` | `next/og` and `ImageResponse` are not used | Not reachable |
| GHSA-673q-mjm6-r5xr (RCE, `use cache` returning typed arrays) | Cache Components enabled and such a `use cache` function | Cache Components are not enabled and no `use cache` exists | Not reachable |

**Conclusion.** As configured, none of the five is reachable, and the repository has no recorded
deployments or environments. That is not a clean bill of health: the dependency is unpatched, and
`docker compose` publishes the frontend on port 3000. **Priority security-hardening task, required
before any external deployment or any exposure of the frontend to an untrusted network:** upgrade
`next` to 16.4.1 (a single-package change verified by CI, not a broad upgrade), then re-triage. If it
must be exposed first, `images: { unoptimized: true }` in `next.config.ts` turns image optimization off (confirm the endpoint's behaviour on a build before relying on it); it does not address
the Windows-hosting advisory.

## Post-closeout priorities

In order. Nothing here is done at the checkpoint.

1. **Security (before any external deployment):** upgrade `next` 16.2.10 to 16.4.1 as a single-package PR verified by CI (it also brings `sharp` to a fixed version); then triage the remaining 56 Dependabot alerts.
2. Add runtime schemas for the `POST /trades/validate` and `POST /comparisons` responses and a permanent valid-scenario comparison test (limitation 1).
3. Protect `main` (branch protection or a ruleset requiring the CI checks) (limitation 12).
4. Run the read-only data-residue queries from the Wave 2 closure audit on a copy of each real database (limitation 7).
5. Add a PostgreSQL CI job before any PostgreSQL deployment (limitation 13).
6. Exercise the evaluator in a real browser or e2e (limitation 2); measure the SQLite lock (limitation 3).
7. Obtain written permission before enabling official NBA headshots, and resolve NBA.com-derived data licensing (limitations 9 and 10).

## Not claimed

V1 does not claim to be deployed, load-tested, security-reviewed or approved for public use, and no
statement in the repository should be read as saying so. Model outputs remain decision support, not
guarantees; see [limitations.md](limitations.md).
