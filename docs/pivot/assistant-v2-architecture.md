# G. Ask Pivot V2 — architecture

*Pivot Intelligence: an experimental, read-only research partner over a fictional scouting
corpus — what it is built from, how a question becomes a cited answer, and where it stops.*

> **FICTIONAL DATA / SANDBOX.** Every team, player, analyst, statistic and report this module
> serves is invented. It shares no table, id or name with Pivot's NBA data, it is off by
> default, and it is not the R14 Copilot. See ADR-30 in `docs/decision-log.md`.

---

## 1. System overview

```
 Browser  /assistant  (frontend/app/assistant, components/assistant/*)
   │  team · mode · priorities · constraints · bounded history (sessionStorage only)
   │  POST /api/v1/assistant/ask        GET /status   GET /context
   ▼  (Next.js rewrite → FastAPI)
 ┌───────────────────────────────────────────────────────────────────────────────┐
 │ api/v1/assistant.py   flag gate (404) · loopback-only guard (403) · envelope   │
 │ assistant_rag/service.py   in-flight limit (503) · timeout (504) · cancel on   │
 │                            client disconnect · provider-error mapping (503)    │
 │                            · LangSmith tracing scope (off unless configured)  │
 └───────────────────────────────┬───────────────────────────────────────────────┘
                                 │  one LangChain Runnable (assistant_rag/pipeline.py)
 ┌───────────────────────────────▼───────────────────────────────────────────────┐
 │ query_context      bound history, GM priorities + what changed, dev variants   │
 │ entity_resolution  names → fictional manifest ids; ambiguity; real-NBA names;  │
 │                    invented names ("Corwin Ashby") flagged                     │
 │ policy             deterministic routes: answer · clarify · refuse             │
 │ retrieval          nomic embeddings (Ollama) + BM25 + entity + priority lists, │
 │                    fused (RRF); roster fit: pinned team memo, team-context /   │
 │                    untrusted / method quotas, one chunk per report;            │
 │                    min-evidence gate on the raw question                       │
 │ prompt_construction grounded / basic prompt, untrusted-source boundaries,      │
 │                    known gaps + conflicts, token budget                        │
 │ model_call         ChatOllama qwen3.5:2b (reasoning off, temperature 0)        │
 │                    — skipped for clarify / refuse / no-evidence routes         │
 │ citation_check     ids vs retrieved set; numbers vs manifest; legality,       │
 │                    fit-score and injection markers; misleading citations       │
 │ answer_guard       REMOVES provable errors (legality verdict, fit score,      │
 │                    canary, nonexistent source, wrong number for a named       │
 │                    player) behind a visible marker; re-checks citations        │
 │ final_response     typed AskResponse: sources (+ evidence class), sentence-   │
 │                    level claim kinds, limitations, conflicts, guard record,   │
 │                    evidence grid, follow-ups, diagnostics, trace reference     │
 └───────────────────────────────┬───────────────────────────────────────────────┘
                                 │ reads only
 ┌───────────────────────────────▼───────────────────────────────────────────────┐
 │ fixtures/manifest.json   teams · players · exact stats · missing stats ·       │
 │                          letter grades · declared conflicts · document index  │
 │ fixtures/documents/S01–S34.md   versioned, hash-locked fictional reports       │
 └───────────────────────────────────────────────────────────────────────────────┘
```

Nothing in this picture reads or writes the NBA database, the analytics engines, the CBA
engine or `services/tools.py`.

---

## 2. Module boundaries

| Module | Role | LangChain? |
|---|---|---|
| `fixtures/manifest.json` | The fictional league and every bounded fact: 4 GM-selectable teams, 2 background clubs, 12 players, 117 exact statistics, 3 deliberately missing statistics, 5 unavailable topics, 47 analyst grades, 4 declared conflicts, 34 document records | — |
| `manifest.py` | Loads and cross-validates the manifest; raises on any broken reference | no |
| `corpus.py` | Document loader + section chunker → `Chunk`s; content-hash check; `to_langchain_documents()` | at the edge |
| `claims.py` | Sentence, citation, number, mention, stat-keyword, negation and phrase analysis — shared by runtime checks and evaluators so both use one rulebook | no |
| `entities.py` | Resolves names against the manifest; ambiguity; carries entities across turns; detects real NBA names | no |
| `lexical.py` | A small BM25 index | no |
| `embeddings.py` | `OllamaEmbeddings` with nomic's `search_document:` / `search_query:` prefixes | yes |
| `retrieval.py` | `InMemoryVectorStore` dense search + BM25 + entity ranking fused by reciprocal rank; pinning, diversification, low-evidence gate, diagnostics | dense part |
| `policy.py` | Deterministic routing and the deterministic answers (clarify, refuse, abstain) | no |
| `prompts.py` | Grounded and basic prompt variants, history bounding, token budget | no |
| `grounding.py` | Post-generation checks → `GroundingReport`; `analyze_answer` per-sentence verdicts | no |
| `guard.py` | Removes sentences the checks can prove wrong, behind visible markers | no |
| `epistemics.py` | Evidence class per source; observed fact / analyst opinion / inference / unavailable per sentence | no |
| `grid.py`, `followups.py`, `context.py` | Evidence grid, follow-up prompts, UI catalogue | no |
| `providers.py` | `ChatOllama` factory, Ollama readiness probe, provider-error mapping | yes |
| `tracing.py` | Opt-in LangSmith scope | yes |
| `pipeline.py` | The runnable chain | yes |
| `service.py` | Lifecycle, bounding and error mapping around the chain | lazily |
| `models.py`, `types.py`, `errors.py` | API contract, stage types, error vocabulary | no |
| `evaluation/` | Benchmark, deterministic scorers, adversarial fixtures, local + LangSmith runners | runners only |
| `api/v1/assistant.py` | Three routes | lazily |

**Import rule.** `app.main` imports only `api/v1/assistant.py`, which imports the service
inside its handlers. The optional extra (`.[assistant]`) is therefore never needed to start
the API, run CI or build the Docker image; without it the status route reports which
packages are missing and the ask route is a 503 `assistant_dependencies_missing`.

---

## 3. Source provenance

Every retrievable passage carries, from the manifest: `source_id` (`S03`), `version`
(`v2`), `chunk_id` (`S03@v2#2`), the SHA-256 of the whole document, title, document type,
author and role, report date, season, the entities and teams it covers, topics, `trust`
(`internal` or `untrusted`), `data_mode: synthetic_demo` and the provenance string
`app.core.provenance.upstream_for("assistant_synthetic_corpus")` → "Fictional Ask Pivot
research corpus (synthetic, not NBA data)".

**Version stability.** The corpus loader refuses to start if a document's bytes differ from
the hash recorded in the manifest; editing a report therefore forces a version bump, and a
citation in an answer always resolves to the exact text that was indexed and shown.

**A citation is a pointer, not a proof.** `attribution_status` never says "verified": the
best it says is `citations_present_not_fact_verified`. Numeric claims are additionally
checked against the manifest, and any mismatch is a warning on the answer.

**Untrusted sources.** Two documents are third-party material — an agent's promotional memo
(selective evidence) and a reader submission containing a prompt-injection payload. They are
retrievable on purpose, labelled `trust="untrusted"` in the prompt and the UI, never used as
a fact source in the manifest, and the injection's canary phrase is checked for in every
answer.

---

## 4. Data flow of one question

1. **Bound the request.** Schema limits are hard 422s (question ≤ 2000 chars, ≤ 20 history
   messages, ≤ 8 constraints, unknown keys rejected). The pipeline then keeps its own,
   smaller window of history and reports exactly what it kept (`context_used`).
2. **Separate the GM's words from the documents.** Every user turn is listed in
   `context_used.gm_statements` and presented to the model as a hypothesis or preference, not
   a fact. Priority edits between turns become explicit `priority_changes` ("point-of-attack
   defense: high → must preserve") that the model is told to address.
3. **Resolve entities** against the fictional manifest: "Mateo Cruz" is one player, a bare
   "Cruz" or "Niko" is two and triggers a clarifying question (unless the conversation already
   settled it). A real NBA name triggers a refusal. An invented, name-shaped phrase that is in
   no manifest ("What's Corwin Ashby's three-point percentage?") is refused when nothing
   fictional is in play, and otherwise answered with a "not in this sandbox" caveat —
   retrieval cannot catch it, because the question is on-topic. Greetings, staff titles,
   basketball terms and Title Case input are not names ("Hey Pivot", "Coach Grant", "Rim
   Protector"). Lowercase surnames ("reed", "pike") resolve when the whole question is typed
   without capitals.
4. **Route deterministically.** Pure legality / cap / contract questions, and fit-score or
   real-NBA questions that name no fictional player, are answered without calling the model.
   Mixed questions (a fit score *for Theo Vance*, LeBron James *compared with Niko Reed*, a
   legality question that also asks about defense) go to the model with an explicit "you must
   decline X" note and a deterministic limitation.
5. **Retrieve.** Hybrid by default: dense cosine similarity over nomic embeddings, BM25 over
   the same text, and metadata rankings (documents about the resolved entities; in roster fit,
   player reports covering the GM's top priorities), fused by reciprocal rank. In roster-fit
   mode the selected team's memos are pinned but capped (team context ≤ 3 chunks), untrusted
   documents enter only if the search itself finds them (≤ 2), other clubs' own memos are left
   out unless named, and each report contributes one chunk, so k = 8 covers several candidates.
   The minimum-evidence gate scores the *raw* question with team names removed; a team or a
   carried player vouches only with a basketball cue or a pronoun ("and his shooting?"), so
   "best pizza in Hartford?" is abstained on without a model call. BM25 vouches only for a
   passage sharing two distinctive words, or one basketball word, with the question, so a
   single rare everyday word ("What should I see in Omaha?") does not clear the gate.
   Third-party (untrusted) documents never enter through the metadata rankings, and a named
   player's own main report always gets entity credit.
6. **Prompt.** Sources are wrapped in `<source id=… trust=…>` blocks; the system prompt
   says source text is data, never instruction. Known gaps (missing statistics, unavailable
   topics) and known conflicts are stated up front so the model does not have to discover
   them.
7. **Generate** with `ChatOllama` (`reasoning=False`; the model otherwise spends ~75 s
   thinking; `temperature=0`; explicit `num_ctx` because overflowing prompts are truncated
   silently from the front).
8. **Check** citations, numbers, legality verdicts, fit scores, canaries and misleading
   citations (a correct number cited to a report that does not contain it).
9. **Guard.** Remove only what the checks can prove: a legality verdict, a numeric fit score,
   an injection canary, a citation to a source that does not exist, or a number that
   contradicts the fact sheet *for a player the sentence names*. Gap items under an
   evidence-gap heading ("Not in the sources: …") and numbers repeated only to reject them
   ("nowhere near 45%") are never touched — but "As you said, Reed shoots 45%" accepts the
   premise and is checked. A number tied to a player only by a pronoun or by "it"/"that", or
   whose statistic had to be inferred, keeps its warning but is never deleted; derived numbers
   (differences, ratios, league averages, thresholds, games missed, ages) are not statistics at
   all. Only the flagged sentence is replaced, by its exact span; in a markdown table only the
   offending cell. Each removal leaves a visible marker with the documented value; the model's
   raw text stays in `diagnostics.raw_answer`, and evaluations score both. If the guard removes
   every claim, the answer kind becomes `partial_abstention` with a limitation saying so.
10. **Label** every sentence as an observed fact, an analyst opinion, the assistant's
    inference, unavailable information, an untrusted claim or a question — from what it cites
    and from the manifest. A "My read" heading or label marks the assistant's inference (a
    documented statistic still wins); an item under an evidence-gap heading that names a gap is
    unavailable; the guard's markers are never labelled. Return.

Earlier answers are fed back to the model as summaries (their conclusion and any question
they asked the GM, citations removed); the history budget is measured on those summaries. In
live runs the 2B model otherwise copied its previous answer, citations and all, into every
follow-up.

---

## 5. Privacy

- Inference is local (Ollama). Nothing leaves the machine by default.
- **LangSmith tracing is off by default** and is enabled only when Pivot's own
  `ASSISTANT_TRACING=true` and a `LANGSMITH_API_KEY` are both set. The SDK's
  `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` / `LANGSMITH_PROJECT` are never read, so
  variables exported for other projects cannot turn tracing on or redirect its runs (project:
  `ASSISTANT_LANGSMITH_PROJECT`, default `pivot-intelligence-v2`). When it is off, the
  pipeline also runs inside an explicit tracing-disabled context. When it is on, prompts,
  retrieved (fictional) evidence and answers are sent to LangSmith's servers.
- Conversation history lives in the browser's `sessionStorage` for the tab's lifetime; the
  server stores nothing. Logs record request ids, routes and timings — never prompts or
  answers.
- The status route reports whether a LangSmith key is configured as a boolean only.

---

## 6. Reliability boundaries

| Condition | Response |
|---|---|
| Feature flag off | `GET /status` → `enabled: false` only; `/context`, `/ask` → 404 `assistant_disabled`; none of the three is listed in `/openapi.json` |
| Non-loopback request to any assistant route while enabled (peer, any `X-Forwarded-For` hop, `Host`, any `X-Forwarded-Host`) | 403 `assistant_local_only` unless `ASSISTANT_ALLOW_REMOTE=true`. Next's rewrite proxy forwards no client address, so LAN exposure is prevented by binding `next dev` to 127.0.0.1; the Host checks refuse a LAN-IP visit and DNS-rebinding pages. URLs in status messages never show credentials |
| Optional packages missing | 503 `assistant_dependencies_missing` with the install command |
| Corpus/manifest inconsistent or malformed (bad JSON, missing field, bad UTF-8) | 503 `corpus_unavailable` on `/ask` and `/context`; `/status` shows `corpus.loaded=false` with file names only |
| Ollama down | 503 `provider_unavailable` ("start it with `ollama serve`") |
| Model not pulled | 503 `model_unavailable` (names the `ollama pull` command) |
| A second concurrent answer | 503 `assistant_busy` (no silent queue) |
| Over the time budget | 504 `provider_timeout`; the model request is cancelled |
| Client disconnects (Stop) | the in-flight model request is cancelled and the slot freed within ~0.5 s — detected by `AskDisconnectMiddleware`, because Starlette's `BaseHTTPMiddleware` hides disconnects from the route |
| Anything unexpected | 500 `assistant_internal_error`, still enveloped with a request id — including a bug while building the index (logged with its traceback; status not ready until a build succeeds). Only connection, HTTP and Ollama errors are `provider_unavailable` |

---

## 7. The future R14 adapter boundary

V2 deliberately has **no tools**. Retrieval gives the model documents; it does not give it
calculated facts. The V3 path, when the evaluation composite has a single entry point:

```
question ─▶ policy ─▶ (document research)  retrieval ─▶ document_evidence  ─┐
                  └─▶ (calculation)       services/tools.py (read-only)     ├─▶ answer
                                          ─▶ calculated_result + caveats  ─┘
```

- The adapter would call `available_tools(db)` and pass `result_caveats` through verbatim.
- `document_evidence` and `calculated_result` stay separate provenance channels in the
  response, so a reader can always tell a scout's opinion from an engine's output.
- Legality would come from the CBA engine through `simulate_trade` once that tool exists —
  never from the model.
- Synthetic and real data would remain separate corpora with separate `data_mode`s.
