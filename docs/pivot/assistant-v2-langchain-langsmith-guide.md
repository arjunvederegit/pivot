# J. Ask Pivot V2 — how LangChain and LangSmith are actually used

*A study guide to the real code: what each LangChain and LangSmith piece does here, where it
lives, and what was deliberately left out. Read with the code open.*

> **FICTIONAL DATA / SANDBOX.** Everything Ask Pivot retrieves is invented. See
> `assistant-v2-architecture.md` for the system picture and `assistant-v2-runbook.md` for
> commands.

---

## 1. The one-paragraph version

LangChain supplies four things — the **Document** type, an **Embeddings** interface (backed by
Ollama's `nomic-embed-text`), an in-memory **vector store**, and the **Runnable** composition
model (`stage | stage | ...`) with a **chat prompt template** piped into **ChatOllama**. Everything
that decides *what is true* — which passages count, which names are fictional, which numbers
are right, what gets removed from an answer, how an answer is scored — is plain Python next to
it, testable without LangChain installed. LangSmith supplies **tracing** (off by default) and
**evaluation** (`evaluate()` over a dataset, with our deterministic scorers as evaluators).

What is *not* used, on purpose: agents, tools/function calling, LangGraph, the legacy `Chain`
classes, `RetrievalQA`, `EnsembleRetriever`, text splitters, structured-output parsing of the
answer, and any LLM-as-judge. Each would hide a decision this sandbox needs to show.

---

## 2. The ten-minute code tour

Open these in order. Each stop names the concept it teaches.

| # | File : symbol | Concept |
|---|---|---|
| 1 | `backend/app/assistant_rag/fixtures/manifest.json` | **Ground truth outside the model.** Every bounded fact (117 statistics, 3 deliberately missing ones, 47 analyst grades, 4 conflicts) lives here, not in prose. |
| 2 | `corpus.py : build_corpus`, `to_langchain_documents` (l.181) | **Document loading and chunking.** Markdown → section `Chunk`s → LangChain `Document(id, page_content, metadata)`. The id `S03@v2#2` is what the vector store, the citations and the inspector all share. Hashes lock each document's text. |
| 3 | `embeddings.py : NomicPrefixedEmbeddings` (l.45), `build_embeddings` (l.78) | **Embeddings.** A subclass of `langchain_core.embeddings.Embeddings` that adds nomic's `search_document:` / `search_query:` task prefixes around `OllamaEmbeddings` (which does not add them). |
| 4 | `retrieval.py : _vector_store` (l.455), `HybridRetriever.retrieve` / `aretrieve` (l.603 / l.625), `reciprocal_rank_fusion` (l.178) | **Vector store and retriever.** `InMemoryVectorStore.add_documents(...)` and `similarity_search_with_score_by_vector(...)` give cosine-ranked chunks; BM25 (`lexical.py`) and metadata rankings are fused by reciprocal rank — written here, not via `EnsembleRetriever`, so every chunk keeps its dense rank, BM25 rank and *why it was retrieved*. |
| 5 | `prompts.py : build_prompt` (l.482), `grounded_system_prompt`, `summarize_assistant_turn` (l.448) | **Context assembly.** Sources wrapped in `<source id trust date>` blocks, known gaps and conflicts stated up front, a token budget enforced, earlier answers fed back as summaries. Two variants: `grounded` and the `basic` baseline. |
| 6 | `pipeline.py : build_chain` (l.802) | **The runnable chain.** `ChatPromptTemplate.from_messages([("system","{system}"), MessagesPlaceholder("history"), ("human","{user}")]) \| chat_model`, and nine named stages composed with `\|`. This one function *is* the RAG pipeline. |
| 7 | `pipeline.py : _stage` (l.784) | **RunnableLambda.** Each stage is `RunnableLambda(func, afunc=...)` with `run_name` — sync and async — so `ainvoke` works end to end and every stage appears as its own child run in a trace. |
| 8 | `providers.py : build_chat_model` (l.69) | **The chat model.** `ChatOllama(model, temperature=0, reasoning=False, num_ctx, num_predict, keep_alive, client_kwargs={"timeout"})`. `reasoning=False` matters: `qwen3.5:2b` otherwise "thinks" for ~75 s per answer. |
| 9 | `grounding.py : analyze_answer`, `check_answer` (l.468); `guard.py : guard_answer` (l.182); `epistemics.py : classify_answer` (l.148) | **After generation.** Citations vs what was retrieved, numbers vs the manifest, the guard that removes provably wrong sentences, and the observed-fact / analyst-opinion / inference / unavailable labels. No LangChain. |
| 10 | `tracing.py : tracing_scope` (l.125); `evaluation/langsmith_runner.py : make_evaluator` (l.210), `run_experiments` (l.375) | **LangSmith.** Tracing on/off by *our* settings; evaluation with our scorers as LangSmith evaluators. |

The chain, in full (`pipeline.py`, `build_chain`):

```python
chain = (
    _stage("query_context", stages.query_context)                    # bound history, priorities, variants
    | _stage("entity_resolution", stages.entity_resolution)          # names -> fictional manifest ids
    | _stage("policy", stages.policy)                                # answer / clarify / refuse
    | _stage("retrieval", stages.retrieval, stages.aretrieval)       # embeds the question
    | _stage("prompt_construction", stages.prompt_construction)
    | _stage("model_call", stages.model_call, stages.amodel_call)    # the only generation
    | _stage("citation_check", stages.citation_check)
    | _stage("answer_guard", stages.answer_guard)                    # removes what checks can prove wrong
    | _stage("final_response", stages.final_response)
)
return chain.with_config(run_name="ask_pivot")
```

State flows as one dict that each stage adds to. Deterministic routes (clarify, refuse, no
evidence) still pass through every stage — `model_call` simply writes the deterministic reply
instead of calling the model — so a trace always has the same shape.

---

## 3. LangChain primitives, as used here

| Primitive | Where | Why this one |
|---|---|---|
| `langchain_core.documents.Document` | `corpus.to_langchain_documents` | The unit a vector store indexes; `id` = chunk id, `metadata` = full provenance. |
| `langchain_core.embeddings.Embeddings` (subclassed) | `embeddings.NomicPrefixedEmbeddings` | One place to add task prefixes, for both `embed_*` and `aembed_*`. |
| `langchain_ollama.OllamaEmbeddings` | `embeddings.build_embeddings` | Local embeddings; `keep_alive=1800` (an integer — this class rejects `"30m"`). |
| `langchain_core.vectorstores.InMemoryVectorStore` | `retrieval._vector_store` | ~160 chunks: brute-force cosine is exact and fast (~3 ms). Swappable via `vector_store_factory`. |
| `ChatPromptTemplate` + `MessagesPlaceholder` | `pipeline.build_chain` | System / history / human slots. Values are substituted, never re-parsed, so `{braces}` in a source are harmless. |
| `langchain_ollama.ChatOllama` | `providers.build_chat_model` | Local generation; explicit `num_ctx` (Ollama silently truncates an overflowing prompt from the front). |
| `RunnableLambda`, `\|`, `.with_config(run_name=...)` | `pipeline._stage`, `build_chain` | Named, traceable, sync+async stages without an opaque framework. |
| `ainvoke` + `config={"metadata", "tags", "run_name"}` | `service.py`, `pipeline.run_config` | One async call per answer; metadata travels to every child run. |
| `langchain_core.tracers.context.collect_runs` | tests | Records the run tree offline — how the tests prove all nine stages ran. |
| `FakeListChatModel`, `DeterministicFakeEmbedding` | tests, `evaluation/fake_model.py` | The whole chain runs in CI with no model. |

---

## 4. LangSmith tracing — on purpose, or not at all

`tracing.py : tracing_scope(settings, metadata, tags)` wraps every answer:

- **Off (default):** the run executes inside `langsmith.run_helpers.tracing_context(enabled=False)`.
  Ask Pivot's switch is `ASSISTANT_TRACING`, not the SDK's `LANGSMITH_TRACING`: an exported
  `LANGSMITH_TRACING=true`, with or without a key, cannot turn it on, and `LANGSMITH_PROJECT`
  cannot redirect it (the project is `ASSISTANT_LANGSMITH_PROJECT`). Tracing requires
  `ASSISTANT_TRACING=true` plus `LANGSMITH_API_KEY`. No `langsmith.Client` is ever constructed (creating one makes a network call).
  Tested in `tests/unit/test_assistant_tracing.py`.
- **On:** one lazily created `Client(api_key, api_url)`, and
  `tracing_context(enabled=True, client=..., project_name=..., metadata=..., tags=...)`.
  The run tree is `ask_pivot` → the nine stage runs → `chat_model` (the prompt template and
  ChatOllama) inside `model_call`. Metadata on every run: `corpus_version`, `prompt_variant`,
  `retrieval_variant`, `k`, `chat_model`, `embedding_model`, `team_id`, `mode`,
  `data_mode=synthetic_demo`, `request_id`. Tags: `ask-pivot`, `synthetic_demo`, `mode:…`,
  `prompt:…`, `retrieval:…`.
- **Privacy:** with tracing on, prompts, the retrieved (fictional) evidence and answers are sent to
  LangSmith even though inference is local. The status route reports `api_key_configured` as a
  boolean only.

---

## 5. Evaluation — offline first, LangSmith when configured

Two runners score the **same** benchmark (`evaluation/benchmark.json`, 38 cases) with the **same**
deterministic scorers (`evaluation/scorers.py`, 21 keys):

| | Local (`runner.run_local`) | Hosted (`langsmith_runner.run_experiments`) |
|---|---|---|
| Needs | Ollama | Ollama + `LANGSMITH_API_KEY` |
| Target | `AssistantService.ask` in-process | the chain, inside `langsmith.evaluate()` |
| Evaluators | `scorers.score_case` | one LangSmith evaluator per scorer (`make_evaluator`) + summary evaluators (objective sensitivity, blocking failures) |
| Output | `backend/.assistant_eval/<run>/report.md`, `summary.json`, `results.jsonl`, `review_worksheet.md` | experiments `pivot-v2-basic-hybrid-…` and `pivot-v2-grounded-hybrid-…` on dataset `pivot-intelligence-v2-benchmark-<hash8>` |
| Guard | scores both the model's raw text and the guarded answer | scores the guarded answer |

Key LangSmith concepts as they appear here:

- **Dataset / examples** — `sync_dataset`: one example per case; inputs are the request fields,
  outputs are the case id and expectations (no gold numbers — those stay in the manifest). The
  dataset name embeds the benchmark's content hash, so editing the benchmark creates a new
  dataset instead of silently evaluating against a stale one (the V1 bug).
- **Target** — `make_target(variant, ...)`: a function from example inputs to an `AskResponse` dict.
- **Evaluator** — `make_evaluator(key, ...)` returns `evaluator(outputs, reference_outputs) ->
  {"key", "score", "comment"}`. LangSmith matches the parameter *names*.
- **Experiment** — one `evaluate()` call per variant, with `experiment_prefix`, `metadata`
  (corpus version, benchmark hash, variants, models) and `max_concurrency=1` (local Ollama).
- **Offline proof** — `tests/unit/test_assistant_langsmith_offline.py` runs LangSmith's own
  `evaluate()` with `upload_results=False` and a client pointed at a closed local port, over the
  V1 failure answers: `claim_polarity` 0.0, `abstention_correctness` 0.0, `fact_correctness` < 1
  with a FAIL verdict — while `citation_validity` stays 1.0. The framework itself cannot give
  those answers full marks.

---

## 6. A five-minute LangSmith walkthrough (once you have a key)

1. Export the key for one command only, from `backend/`:
   `LANGSMITH_API_KEY=<key> .venv/bin/python -m scripts.eval_rag hosted --variants basic:hybrid,grounded:hybrid`
   (privacy notice printed first; Ollama must be running).
2. In LangSmith, open **Datasets & Experiments** → `pivot-intelligence-v2-benchmark-<hash8>`.
   You will see 38 examples and two experiments: `pivot-v2-basic-hybrid-…` and
   `pivot-v2-grounded-hybrid-…`.
3. Select both experiments → **Compare**. Sort by `fact_correctness`, then `injection_resistance`,
   `abstention_correctness`, `epistemic_separation`. Each feedback `comment` says PASS/FAIL with the
   scorer's reason.
4. Click any row → **trace**: `ask_pivot` → `retrieval` (inputs show the expanded query; outputs the
   ranked chunks) → `prompt_construction` (the exact prompt) → `chat_model` → `answer_guard`.
5. For live traces of the app itself, start the backend with
   `ASSISTANT_ENABLED=true ASSISTANT_TRACING=true LANGSMITH_API_KEY=<key>`; each answer's Debug panel
   then shows its run id (and a link when LangSmith returns one).

**Status in this branch:** no LangSmith key was configured, so the hosted experiments have **not**
been run. They are unverified. Everything above is exercised offline by tests with fakes, and by
the offline `evaluate()` proof.

---

## 7. Exercises

1. **k and variants.** `.venv/bin/python -m scripts.eval_rag retrieval --variants dense,lexical,hybrid --k 3,5,8`.
   Read the held-out section: where does hybrid beat dense, and why (look at a miss's query)?
2. **Add a document.** Write `S35.md` with the banner and sections, add it to the manifest with a
   `version`, run `tests/unit/test_assistant_corpus.py` — it tells you the hash to record.
3. **Add a benchmark case**, then run `selftest` and `local --cases <your id>`.
4. **Break the guard on purpose.** Add a sentence to `tests/unit/test_assistant_guard.py` that a
   model might plausibly write and that the guard wrongly removes; then fix `claims.py`.
5. **Trace one question.** With a key, enable tracing and ask the Comets demo question; find the
   `answer_guard` run and compare its input and output.
