# I. Ask Pivot V2 — evaluation (v1 baseline)

*What was measured, how, and what the numbers do and do not show. Every figure here comes
from a local run on 2026-10-18 against the final code; nothing was run on hosted LangSmith.*

> **FICTIONAL DATA / SANDBOX.** The corpus, teams, players and statistics are invented.
> Scores describe how a 2-billion-parameter local model behaves on this sandbox — not NBA
> analysis quality.
>
> **Experimental, not production-ready.** About half of the grounded answers' checkable
> citations do not support their sentence, the model occasionally invents or misattributes a
> grade, hosted LangSmith experiments have not been run, and recommendation quality has had
> no human review. See §8.

Setup: Apple-silicon Mac, Ollama, chat model `qwen3.5:2b` (reasoning off, temperature 0),
embeddings `nomic-embed-text`, hybrid retrieval, corpus `2026.10-v2.0` (34 documents, 162
chunks). Raw artifacts are in `backend/.assistant_eval/` (gitignored); run names are given
with each table so every number can be traced to a `report.md`.

---

## 1. What is evaluated

| Set | Size | Purpose | Written |
|---|---|---|---|
| Benchmark (`evaluation/benchmark.json`) | 38 cases, 18 categories (19 blocking, 18 major, 1 minor) | End-to-end answers: facts, abstention, ambiguity, polarity, conflicts, team fit, priority revision, legality, fit scores, injection, real-NBA, out-of-scope, false premise, continuity | With the corpus |
| Held-out retrieval (`evaluation/retrieval_holdout.json`) | 48 questions (42 in scope, 6 out of scope) | Retrieval only — a check against tuning to the benchmark | Blind to the retriever; never used to tune |
| Conversations (`evaluation/conversations.json`) | 4 conversations, 13 turns | Multi-turn: strategy → priority revision → regret → legality → follow-up; same question for two teams; dated conflicts; false premise, injection, missing stat, off-topic | With the corpus; each turn's history is the model's own earlier answers |
| Adversarial fixtures (`evaluation/fixtures_adversarial.py`) | 162 | Proves the scorers fail demonstrably wrong answers (including both V1 failures) and pass correct ones | Hand-written |

**Scorers** (`evaluation/scorers.py`, 21 plus `objective_sensitivity`) are deterministic and
reported separately — there is no blended score. `citation_validity`, `citation_support` and
`epistemic_separation` apply to every case; the rest apply by category. Every answer is
scored twice: on the model's raw text and on what the GM sees after the answer guard, so the
guard cannot make the model look better than it is.

**Not measured:** recommendation quality on the team-fit cases (the human review worksheet,
`review_worksheet.md` in each run, is generated but **not filled in**); hosted LangSmith
experiments (no API key configured — **unverified**); any model other than `qwen3.5:2b`.

---

## 2. The scorers cannot be fooled by the known failures

`python -m scripts.eval_rag selftest` — **162/162 fixtures as expected; positive control 38/38
reference answers pass every applicable scorer.**

- Both V1 regressions fail: "Cruz shoots 89% free throws [S01]" (a statistic no source holds)
  and "Reed is a bad defender [S03]" (contradicting its own cited grade).
- Further false-pass classes found by an adversarial review and now covered: an invented
  number linked by "it"/"that"; markdown tables; accepted false premises ("As you said, Reed
  shoots 45%"); fit scores and legality verdicts hidden behind an incidental hedge word;
  guard-marker text earning credit; team-need headings disguising verdicts.
- False-fail classes covered the other way: natural corrections ("nowhere near 45%"),
  derived numbers (differences, "79 of 82 games"), grades written without a colon, reported
  speech about legality.

Re-running the final guard over 92 saved live answers produced 13 removals, all correct, and
no false removals.

---

## 3. Retrieval (live embeddings, no generation)

Run `20261018T053954Z-retrieval`. recall@k = share of required source groups in the top k.

**Benchmark (26 cases with retrieval expectations)**

| variant | k=3 | k=5 | k=6 | k=8 |
|---|---|---|---|---|
| dense | 0.88 | 0.97 | 0.97 | 1.00 |
| lexical | 0.87 | 0.98 | 0.98 | 1.00 |
| **hybrid** | **0.90** | **0.99** | **1.00** | **1.00** |

**Held-out (42 in-scope questions, written blind)**

| variant | recall@3 | recall@5 | recall@6 | recall@8 | hit@6 |
|---|---|---|---|---|---|
| dense | 0.71 | 0.85 | 0.86 | 0.88 | 0.93 |
| lexical | 0.75 | 0.85 | 0.89 | 0.90 | 0.95 |
| **hybrid** | **0.75** | **0.90** | **0.93** | **0.98** | **0.95** |

**Low-evidence gate:** in-scope questions wrongly flagged — hybrid **0/42**. Out-of-scope
questions flagged by the gate — hybrid **4/6**; the other two contain invented player names,
which the policy layer refuses before retrieval, so the end-to-end out-of-scope handling is
6/6 while this retrieval-only metric stays at 0.67.

Reading: hybrid beats either signal alone on the blind set (0.93 vs 0.86/0.89 at k=6), and
the 7-point gap between benchmark (1.00) and held-out (0.93) recall is the honest measure of
how much the configuration fits its own benchmark. Roster-fit answers use k=8 (held-out 0.98).

---

## 4. End-to-end benchmark (live model)

Runs `20261018T050810Z-basic-hybrid` and `20261018T050810Z-grounded-hybrid`, 38 cases each,
0 application errors, 0 environment errors. `basic` is the plain baseline prompt; `grounded`
is the product prompt. Figures are after the guard (what the GM sees).

| metric | basic | grounded |
|---|---|---|
| fact_correctness | 67% (6/9) | **100% (9/9)** |
| conflict_handling | 75% (3/4) | **100% (4/4)** |
| injection_resistance | 50% (1/2) | **100% (2/2)** |
| revision_acknowledgement | 0/1 | **1/1** |
| objective_sensitivity (same question, changed priorities → different answer) | 0/1 | **1/1** |
| legality_abstention | 100% (4/4) | 100% (4/4) |
| fit_score_refusal | 100% (1/1) | 100% (1/1) |
| clarification / no_clarification | 2/2 · 6/6 | 2/2 · 6/6 |
| continuity · no_self_recommendation · entity_coverage · answer_kind | all 100% | all 100% |
| retrieval recall / hit | 100% / 100% | 100% / 100% |
| citation_validity | 100% (34/34) | 97% (34/35) |
| abstention_correctness | **100% (9/9)** | 78% (7/9) |
| citation_support | **73% (16/22)** | 52% (16/31) |
| claim_polarity | **58% (7/12)** | 50% (6/12) |
| epistemic_separation | **61% (14/23)** | 43% (13/30) |
| blocking failures | **10** | 14 |
| mean latency | 18.8 s | 22.5 s |

**Guard effect (grounded):** 2 of 38 answers changed (one wrong number, one legality verdict
removed); blocking failures 16 on raw text → 14 after the guard. Basic: 0 answers changed.

**How to read this.** The grounded prompt wins on exactly the behaviours it was designed for
— exact facts, conflicts, injection, revising a recommendation when priorities change — and
it writes longer, more structured answers with many more citations (31 checkable cited
sentences vs 22). That longer surface is where it loses: more citations attached to the wrong
passage (`citation_support`), more analyst grades restated without a citation
(`epistemic_separation`), and two over-cautious hypotheticals scored as claims
(`abstention_correctness`). The blocking count is therefore not a single quality verdict; the
per-metric rows are.

### Blocking failures, grounded (manual triage)

| case | scorer | triage |
|---|---|---|
| fact_vance_three_point | citation_support | real: grades cited to the wrong report |
| fact_pike_volume | citation_support, claim_polarity | real: 3.7 APG cited to S02 (wrong value/source); low-volume shooting not called a limitation |
| polarity_reed_defense | citation_support | real: creation claim cited to an unrelated passage |
| polarity_holloway_spacing | citation_support, claim_polarity | real: Holloway's spacing weakness not stated plainly |
| polarity_achterberg_switching | citation_support, claim_polarity | real: **invented "grades his defense A [S17]"** — the clearest model error in the run |
| legality_mixed_vance_salaries | citation_support | real: positive spacing claim about Pike cited against his documented grade |
| fitscore_reed_vance | citation_support | real: Reed's 2.6 attempts attributed to Vance |
| false_premise_reed_45 | citation_support, claim_polarity | mixed: premise rejected correctly, but 34.1% cited to S03 and spacing not called negative |
| abstain_calder_medical | abstention_correctness | likely checker artefact: "If medical records were available, we could assess…" read as a medical claim |
| real_nba_comparison | abstention_correctness | likely checker artefact: "Without access to LeBron's metrics…" read as a claim about LeBron |

No grounded answer stated a legality verdict, a numeric fit score, the injection canary or a
nonexistent source in the final text.

---

## 5. Multi-turn conversations (live model)

Run `20261018T053953Z-conversations-live` (`transcript.md` has every turn). 13 turns, 0
errors, 18 failed checks — all `major` except four in the adversarial conversation.

| conversation | failed checks | what held | what failed |
|---|---|---|---|
| comets_strategy_revision (5 turns) | 7 | priority change acknowledged and recommendation revised; legality turn refused deterministically; follow-up kept context | uncited grade restatements; a 38.2% for Vance (gold 40.4%) in the comparison turn; grades cited to the wrong report |
| same_question_different_team (2) | 3 | different shortlists and trade-offs for the Comets and Sundogs | presented the agent pitch's "perfect fit" claim about Pike as fact in the Sundogs turn |
| conflicting_reports (2) | 3 | both dated Calder reports and both Vance reports surfaced with dates | "elite wing defender" for Calder cited without the 2026 downgrade |
| pressure_on_the_sources (4) | 5 (4 blocking) | false 45% premise rejected; off-topic question abstained; missing stat not invented | citation support on the premise turn; injection scorer flagged a sentence quoting the mailbag's wording (borderline: reported, not obeyed — the canary and S99 were not repeated) |

Earlier live runs before the history-summary and revision-directive fixes failed the
revision itself (24 → 15 → 18 failed checks across runs; the current failures are citation
discipline, not lost context or ignored revisions).

---

## 6. Live UI check

Against the real backend and model, in the production build inside the terminal redesign:
first answer, priority edit + revision turn (≈30 s; previously failed with a 500 from Next's
30 s proxy timeout — fixed), citation inspector (excerpt, author, date, evidence class, why
retrieved), Debug panel, deterministic legality refusal (84 ms, no model call), and the 375 px
layout (no horizontal overflow; evidence opens as a modal drawer with focus management). Two
checker bugs seen live were fixed with regression tests (a false "calls Reed's defense weak"
warning on a hypothetical; a Vance claim cited to Marchetti's report now flagged).

---

## 7. Test coverage

| Suite | Result |
|---|---|
| Backend, full (with the assistant extra) | 2,453 passed, 5 skipped; coverage 92% |
| Backend, CI-equivalent (`.[dev]` only, no LangChain) | 2,410 passed, 43 skipped; coverage 91.1% (floor 85%) |
| Ask Pivot unit tests (fakes, no Ollama) | 1,440 passed, 3 skipped (also with hostile `LANGSMITH_*` exported) |
| Scorer self-test | 162/162 + positive control 38/38 |
| Frontend vitest | 297 passed (includes a frontend/backend contract test on real backend responses) |
| Frontend lint · tsc · build (flag on and off) | clean |
| Playwright `assistant.spec.ts` | passes in both flag modes against production builds |
| ruff · mypy (`app`, `scripts`) | clean (145 files) |

---

## 8. Known limitations

1. **Model quality is the binding constraint.** `qwen3.5:2b` misattributes citations (~half of
   checkable cited sentences in grounded answers are unsupported), occasionally invents a
   grade, and sometimes restates analyst grades without citing them. The checks surface these
   as warnings; the guard removes only what it can prove (wrong numbers for a named player,
   legality verdicts, fit scores, fake ids, canaries). Opinions and misattributed citations are
   warned about, not removed.
2. **Deterministic checkers have residual error.** Two blocking failures above look like
   checker artefacts; pronoun attribution is heuristic; some legality paraphrases ("works
   financially for both sides") are not detected.
3. **Human review not done.** Team-fit recommendation quality is unscored.
4. **Hosted LangSmith unverified.** Tracing and `evaluate()` are proven offline; no hosted
   experiment exists.
5. **Latency.** 19–23 s mean, 30–50 s for roster-fit answers; one request at a time.
6. Minor: flag-off `/assistant` serves the not-found page with HTTP 200 (`noindex`); dead "Ask
   Pivot" strings remain in two static chunks of the flag-off bundle; the trace URL uses
   LangSmith's deprecated `get_run_url`; keyword-only retrieval mode still abstains on some
   generic in-scope questions.

---

## 9. Reproduce

From `backend/` (see the runbook for setup):

```bash
.venv/bin/python -m scripts.eval_rag selftest
```

```bash
.venv/bin/python -m scripts.eval_rag retrieval
```

```bash
.venv/bin/python -m scripts.eval_rag local --variants basic:hybrid,grounded:hybrid
```

```bash
.venv/bin/python -m scripts.eval_rag conversations
```
