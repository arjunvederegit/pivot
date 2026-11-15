# CLAUDE.md — Pivot

Read this first; the linked documents hold the detail. If it conflicts with a task, ask.

## 1. What Pivot is
A basketball decision-intelligence platform. Guiding principle: **earn breadth through
technical depth.** Preserve the rigorous data infrastructure, validated analytical
behavior, explanatory evidence and interconnected decision workflows.

## 2. Repository state — inspect before you edit
- Development is a **linear R8 → R8.1 → R8.2 → R8.3 → R9.1 → R9.2 → R9.3A lineage on
  feature branches, plus `feat/nba-headshots`**, each with its own git worktree.
  `main` is the pre-R8 baseline, **not** the latest implementation.
- Before any change run `git worktree list`, `git branch -vv`, `git status` and
  `git merge-base --is-ancestor` for the lineage you assume. Hashes go stale: when written
  (2026-10-21) the tip was `feat/nba-headshots` at `2e836dc`; verify, never trust this line.
- Work on an isolated branch or worktree. Do not touch other worktrees, their
  `node_modules`/venvs (some are symlinked or serve a running preview), or running services.

## 3. Database and environment protection
- **Never modify a real database** (the owner's `backend/tradelab.db`, or any DB a server
  or preview is using) without explicit authorization. Work on a copy:
  `DATABASE_URL=sqlite:///<copy> make …`.
- **Do not run destructive operations without the matching approval:** `make train`,
  `make score`, `activate-provider-data APPLY=1`, `make preview-reset`,
  `purge-fixtures APPLY=1`, `seed-demo`/`e2e` against a non-dedicated DB, any reset.
  `make train` retires active models and (by default) permanently deletes superseded impact
  estimates; `make score` rewrites and deletes `team_needs`. Both use the implicit
  `Settings.current_season`.
- Protect backups, migrations and provider caches (`data/imports/`; BALLDONTLIE responses
  cost API requests). Do not delete, move or "tidy" them.
- Never read, print, log or commit `.env*` contents, API keys or tokens (`.env.example` is
  the only env file to read).
- Do not disturb active services or shared environments (ports 8000, 8010, 3000, 8093,
  3093 may be in use) or run a build that overwrites a live dev server's output.
- Files under Claude scratchpads (`/private/tmp/claude-*`) are **not** safely disposable:
  they may hold data directories, venvs or the only copy of something. Never delete them
  on assumption; do not keep irreplaceable work there.

## 4. Analytical integrity
Preserve: canonical player/team identity (stable NBA IDs); **explicit season context**
(roster, statistics, contract and projection seasons run on different clocks); data
provenance; missing-data honesty; model validation and uncertainty; the
candidate-vs-validated distinction; reproducibility and the holdout protocol; cross-tool
consistency (the same player or number reads the same everywhere).
- **Never silently replace missing model inputs with zero.** Missing is an explicit
  unavailable state. Legacy code already uses `fillna(0.0)` in places
  (`analytics/impact.py`, `analytics/features.py`): **report it; do not change model
  mathematics without approval.**
- Candidate ≠ validated ≠ active. Nothing here promotes a candidate; never set
  `is_active` or edit model versions by hand.
- A holdout is read once: development stage first, ledger entry, `--acknowledge-reread`
  for any re-read, no tuning on it.
- Run `make season-status` (read-only) before training or evaluating. 2026-11-03 is the
  documented statistics rollover checkpoint; confirm the configuration, do not assume it.

## 5. Product and architecture boundaries
- Player Intelligence and Trade Evaluation must consume trustworthy analytical contracts:
  serving tiers (measured / validated / experimental / withheld), labelled, with
  provenance and uncertainty.
- **Ask Pivot today is a separate fictional demonstration environment** (invented league,
  optional extras, off by default, never reads the NBA database). Future real-data Ask
  Pivot and R14 integration are distinct work, not the shipped sandbox.
- NBA CDN headshots are **development-gated** pending image-rights clearance: do not enable
  them in production or commit NBA images.
- R10–R14 should reuse validated R8–R9 infrastructure rather than rebuild it.

## 6. Development workflow
- Small, scoped changes; preserve existing feature behavior unless told otherwise.
- Tests appropriate to each change; document any behavioral or mathematical change.
- Review `git diff` before every commit; conventional-commit prefixes.
- **No automatic merges, pushes, production deployment or database activation.**
- Prefer read-only checks (`git`, `make data-status`, `make season-status`,
  `make candidates`) over builds, ingestion or evaluation unless the task requires them.

## 7. Authoritative references
- Command-by-command effects: [README](README.md#operating-safely-data-training-and-models) · [CONTRIBUTING](CONTRIBUTING.md)
- Architecture: [architecture](docs/architecture.md) · [target architecture](docs/pivot/target-architecture.md) · [R8 data foundation](docs/r8/architecture.md) · [identity resolution](docs/identity-resolution.md)
- Seasons and operations: [season context](docs/r8-2/season-context-architecture.md) · [operational runbook](docs/r8-2/operational-runbook.md) · [activation checkpoint](docs/r8-2/activation-checkpoint-report.md)
- Model safety: [versioning (R9.1)](docs/r9-1/model-versioning-and-safety.md) · [evaluation](docs/r9-1/evaluation-architecture.md) · [preregistration](docs/r9-1/preregistration.json) · [promotion and serving (R9.2)](docs/r9-2/model-safety-and-promotion.md)
- Preview and headshots: [R9.3A preview and activation](docs/r9-3a/local-preview-and-activation.md) · [NBA headshots](docs/headshots/NBA-HEADSHOTS.md)
- Roadmap and Ask Pivot: [R9 vision](docs/r9-1/R9-VISION-AND-ROADMAP.md) · [Ask Pivot runbook](docs/pivot/assistant-v2-runbook.md) · [limitations](docs/limitations.md)
