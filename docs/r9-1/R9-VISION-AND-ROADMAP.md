# R9 — Player Intelligence Engine: vision and roadmap

Status: R9.1 is built (this branch). R9.2 and R9.3 are **specified here and not implemented.**
Companion documents: [R9-1-EXECUTIVE-REPORT.md](R9-1-EXECUTIVE-REPORT.md) ·
[R9-2-IMPLEMENTATION-RECOMMENDATION.md](R9-2-IMPLEMENTATION-RECOMMENDATION.md).

## 1. Where R9 sits

Pivot's North Star is a decision-intelligence environment for basketball in which every tool draws on the same
evidence. The roadmap is a dependency chain, not a feature list:

| Release | What it establishes | Consumes |
| --- | --- | --- |
| R8 | A canonical, season-aware, provenance-tracked data foundation | providers |
| **R9** | **A defensible understanding of individual players** | R8 |
| R10 | Player archetypes | R9.2 skill vectors |
| R11 | Roster needs | R9.2 skills, R10 |
| R12 | Player–team fit | R9.2, R10, R11 |
| R13 | Comparable trades | R9.2 (what was traded), R12 |
| R14 | Transaction optimiser | everything above, and R9's uncertainty |

Every later release is only as trustworthy as the player-level quantities it is built on, so R9's job is not to produce
more numbers — Pivot already has plenty — but to establish **which numbers mean what, how well they hold up, and how
sure we are.** The guiding principle: *earn breadth through technical depth.*

## 2. The three milestones, deliberately separate

### R9.1 — Evaluation & model infrastructure (done)

The scientific and engineering foundation: the ability to say, with reproducible evidence, whether an analytical method
improves Pivot's understanding or prediction of basketball performance — without risking anything already in use.

Built: as-of data views that make temporal leakage structurally impossible; a rolling-origin backtest harness with
paired uncertainty over players and seasons; transparent baselines (including the incumbent TEI, run through production's own
code); candidate-only training with a fingerprint guard that proves active state is untouched; a pre-registered
development/holdout protocol with a read ledger; a typed output contract. **First findings:** the incumbent TEI forecasts its own
target no better than a simple regression on the player's own history (10.8 % worse on the holdout) and its likely-range band
covers 57 % against a nominal 80 %; none of three interpretable candidates cleared the pre-registered rule (0 of 18). Evidence and results:
[evaluation-architecture.md](evaluation-architecture.md), [model-versioning-and-safety.md](model-versioning-and-safety.md),
[candidate-experiment-results.md](candidate-experiment-results.md).

### R9.2 — Reusable Player Skill Engine (next; not started)

A multidimensional, versioned, backend-served representation of what a player does well, distinguishing **measured**,
**inferred** and **projected** quantities (§3), each with period, provenance, sample size and uncertainty. Dimensions are
chosen by basketball reasoning *and* by what the data can support — [R9-2-IMPLEMENTATION-RECOMMENDATION.md](R9-2-IMPLEMENTATION-RECOMMENDATION.md)
recommends a concrete first slice.

### R9.3 — Connected player investigation (later; not started)

Player intelligence that travels: a user investigating a player carries identity, season, team context, model version and
scenario from Player Explorer into Team Outlook, Trade Evaluator, Strategy Lab and Ask Pivot without rebuilding the
investigation, with the transfer explicit rather than implied.

## 3. The three kinds of statement, and how each is validated

| Kind | Meaning | Example | Valid only if | R9.1 mechanism |
| --- | --- | --- | --- | --- |
| **Measured** | what was directly observed in a stated period | "shot 58.3 % true shooting in 2025-26 on 1,100 attempts" | the data is the right data (integrity, units, identity) and reproducible | panel validation, unit checks, data fingerprint |
| **Inferred** | an estimate of an underlying ability, correcting for noise and context | "estimated shooting ability, 56 % ± …" | it is *stable* (re-measurable), has construct validity, and carries honest uncertainty | year-over-year stability tooling; intervals; **no claim made in R9.1** |
| **Projected** | an estimate of a specific future observation | "forecast 2026-27 true shooting" | it beats simple alternatives **out of time**, and its intervals cover at the stated rate | rolling-origin backtest, baselines, paired bootstrap, interval coverage |

They are not interchangeable. A forecast that scores well is *evidence about* ability, not a measurement of it; a
measurement is not an estimate of ability because small samples are noisy; and a ranking of players is none of these. The
output contract (`app/modeling/contract.py`) makes the distinction a type error rather than a convention.

## 4. Principles, and the mechanism that enforces each

| Principle | Enforced by |
| --- | --- |
| Intelligence is reusable | all logic in `app/modeling`, no frontend calculation; one contract for every consumer |
| Statistical meaning is explicit | `TargetSpec` carries unit, exposure and interpretation; `OutputKind` in the contract |
| Models are empirically justified | pre-registered advance rule; negative results recorded; baselines are mandatory comparators |
| Uncertainty is a product requirement | intervals carry method + nominal level + calibration evidence; `reliability_statement()` refuses to call an unmeasured level a probability |
| Historical reproducibility | data fingerprint, environment capture, deterministic fits, experiment ids derived from config + data |
| No destructive experimentation | `train_candidate` fingerprints all protected tables before and after, and rolls back on any difference; mutation tests prove the guard fires |
| Improvements compound | candidate/active/rejected lifecycle, generic `candidate_estimates` table keyed by target, same harness for any target |

## 5. Preliminary acceptance criteria (to be refined when each milestone starts)

### R9.2 — done when

1. At least four skill dimensions are available for every player with sufficient sample, each **named by what is measured**
   (e.g. "three-point shooting accuracy", not "shooting rating"), each with all three of measured / inferred / projected
   where meaningful, or an explicit statement of which is not supported.
2. Every inferred dimension reports its **year-over-year stability** and the **reliability** of a player's value as a
   function of sample size, and no dimension below an agreed reliability floor is shown as a player attribute.
3. Every projected quantity has a rolling-origin backtest against the same baselines (R9.1 harness), with paired
   improvement intervals, and is *not* offered where it does not beat persistence/regressed persistence.
4. Every interval shown has measured out-of-time coverage reported next to it.
5. Skill outputs are served by a backend service returning `PlayerIntelligenceRecord`s, versioned, and retrievable *as of a
   historical date* (reproducible).
6. Candidate skill models go through `train_candidate` and the holdout protocol; none is promoted without the audited
   promotion step R9.2 must also build.
7. Identity: only high-confidence provider mappings (or mappings corroborated by the explicit data rule in
   [goat-feature-readiness.md](goat-feature-readiness.md)) feed training.
8. Existing consumers are unchanged until a deliberate, reviewed cut-over.

### R9.3 — done when

1. A user can open a player in Player Explorer, move to Team Outlook and Trade Evaluator, and the same canonical player,
   statistical season, model version and evaluation scenario are shown and *can be inspected*.
2. The carried context is an explicit, serialisable object (not implicit shared state), versioned, and displayed.
3. If a tool cannot honour the carried context (different season basis, model version unavailable), it says so instead of
   silently substituting.
4. Provenance and uncertainty arrive with the player, not re-derived per tool.
5. Ask Pivot can explain a number by citing the record's method, period, sample and evaluation status.

## 6. What R9 will not do

Replace the live rating, promote a candidate, activate partial contract data, or present a rank, a rating or a percentage
that has no defensible interpretation. Where a quantity cannot be validated, Pivot will say so.
