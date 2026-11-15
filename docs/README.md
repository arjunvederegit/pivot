# Documentation index

Where to look, and what to trust. Paths are relative to `docs/`; the repository root holds
[README](../README.md), [CLAUDE.md](../CLAUDE.md) (read first in an AI session) and
[CONTRIBUTING](../CONTRIBUTING.md). Nothing here is copied from the documents it points to.
Later milestone documents supersede earlier ones where they disagree; check dates.

## 1. Current architecture
- [architecture.md](architecture.md) — system overview · [pivot/target-architecture.md](pivot/target-architecture.md) — layering and target (pre-R8 numbering of R9–R14, see §7) · [r8/architecture.md](r8/architecture.md) — canonical data layer
- [pivot/audit.md](pivot/audit.md), [pivot/gap-analysis.md](pivot/gap-analysis.md) — pre-R8 audit (2026-08); *historical roadmap numbering*
- [design-system.md](design-system.md) — UI system

## 2. Development safety and operational procedures
- **Start with** the README section [Operating safely](../README.md#operating-safely-data-training-and-models) — what each `make` target writes
- [r8-2/operational-runbook.md](r8-2/operational-runbook.md) — refreshes, season checks, and the only safe `train`/`score` procedure (§5)
- [r8-2/activation-checkpoint-report.md](r8-2/activation-checkpoint-report.md) — backups and the owner-database activation
- [r9-1/model-versioning-and-safety.md](r9-1/model-versioning-and-safety.md), [r9-2/model-safety-and-promotion.md](r9-2/model-safety-and-promotion.md) — candidate vs active, the guard, the (unbuilt) promotion procedure

## 3. Data identity, season context and ingestion
- [identity-resolution.md](identity-resolution.md) · [data-dictionary.md](data-dictionary.md) · [data-sources.md](data-sources.md) · [../data/README.md](../data/README.md) · [kaggle-setup.md](kaggle-setup.md)
- [r8-2/season-context-architecture.md](r8-2/season-context-architecture.md) — roster vs statistics vs contract seasons; [r8-1/season-readiness.md](r8-1/season-readiness.md) (superseded in part by the R8.2 runbook §5)
- [r8-3/provider-strategy.md](r8-3/provider-strategy.md), [r8-3/contracts-and-cap-readiness.md](r8-3/contracts-and-cap-readiness.md), [r8-3/data-coverage-matrix.md](r8-3/data-coverage-matrix.md) — BALLDONTLIE (GOAT)

## 4. Mathematical modeling and validation
- [methodology.md](methodology.md) — the technical write-up, including its *Evidence vocabulary*
- Model cards: [player impact (TEI)](model-card-player-impact.md) · [team projection](model-card-team-projection.md) · [market value](model-card-market-value.md) · [cba-rule-coverage.md](cba-rule-coverage.md)
- [r9-1/current-model-assessment.md](r9-1/current-model-assessment.md) — formal TEI assessment · [r9-1/evaluation-architecture.md](r9-1/evaluation-architecture.md) · [r9-1/candidate-experiment-results.md](r9-1/candidate-experiment-results.md)
- [limitations.md](limitations.md) — what the product does not claim

## 5. Feature-specific documentation
- R9.2 skills: [r9-2/skill-engine-architecture.md](r9-2/skill-engine-architecture.md), [r9-2/multidimensional-skill-coverage.md](r9-2/multidimensional-skill-coverage.md)
- R9.3A: [r9-3a/goat-operationalization.md](r9-3a/goat-operationalization.md), [r9-3a/financial-intelligence.md](r9-3a/financial-intelligence.md), [r9-3a/player-intelligence-integration.md](r9-3a/player-intelligence-integration.md), [r9-3a/connected-workflows.md](r9-3a/connected-workflows.md)
- Headshots (development-gated): [headshots/NBA-HEADSHOTS.md](headshots/NBA-HEADSHOTS.md)
- Ask Pivot (fictional sandbox, not the real-data assistant): [pivot/assistant-v2-runbook.md](pivot/assistant-v2-runbook.md), [pivot/assistant-v2-architecture.md](pivot/assistant-v2-architecture.md)

## 6. Architectural decisions
- [decision-log.md](decision-log.md) — ADRs (ADR-28 copilot boundary, ADR-30 Ask Pivot sandbox)

## 7. Historical milestone reports and evidence
Provenance record — do not rewrite. Reports describe the state on their date.
- Executive reports: [r8](r8/R8-EXECUTIVE-REPORT.md) · [r8-1](r8-1/R8-1-EXECUTIVE-REPORT.md) · [r8-2](r8-2/R8-2-EXECUTIVE-REPORT.md) · [r8-3](r8-3/GOAT-EXECUTIVE-REPORT.md) · [r9-1](r9-1/R9-1-EXECUTIVE-REPORT.md) · [r9-2](r9-2/R9-2-EXECUTIVE-REPORT.md) · [r9-3a](r9-3a/R9-3A-EXECUTIVE-REPORT.md)
- Roadmaps (two numbering schemes; the README *Roadmap* section explains the conflict): [r8/next-steps-roadmap.md](r8/next-steps-roadmap.md) · [r9-1/R9-VISION-AND-ROADMAP.md](r9-1/R9-VISION-AND-ROADMAP.md) · [r9-3a/R9-3B-AND-R10-RECOMMENDATIONS.md](r9-3a/R9-3B-AND-R10-RECOMMENDATIONS.md)
- R1–R7 (named RosterLab): the `ROSTERLAB_*.md` reports at the repository root, [rosterlab-enhancement-plan.md](rosterlab-enhancement-plan.md), [redesign/](redesign/)
- **Protected evidence — do not move, edit, regenerate or bulk-read:** `r9-1/results/` and `r9-2/results/` (validation outputs, `holdout_ledger.json`, assessments), `r9-1/preregistration.json`. Holdout ledgers are what keep holdout reads honest. Read one named file when a document cites it.

## 8. Preview and activation procedures
- [r9-3a/local-preview-and-activation.md](r9-3a/local-preview-and-activation.md) — `make preview-up`, and the reviewed (not yet performed) provider-data activation
- [r8-3/owner-next-steps.md](r8-3/owner-next-steps.md), [r8-2/cutover-verification.md](r8-2/cutover-verification.md) — owner steps (some superseded by later reports)
