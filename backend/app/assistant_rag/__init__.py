"""Ask Pivot / Pivot Intelligence — an experimental, read-only research assistant over a
FICTIONAL scouting corpus.

What this package is, in one paragraph: a retrieval-augmented generation (RAG) pipeline.
A GM's question, their selected fictional team and their stated priorities are turned into
a search over ~34 synthetic scouting documents; the best passages are handed to a local
language model (Ollama) with strict rules about citing them; and the model's answer is then
*checked* — citation ids against what was actually retrieved, numbers against a
machine-readable fact manifest — before it is returned with every source attached.

What it is not: it is not connected to Pivot's NBA database, the analytics engines, the CBA
engine or the R14 tools registry (`app/services/tools.py`). It never writes anything. It
cannot compute a fit score, a trade's legality or a salary match, and it says so instead of
guessing. Every answer it returns is labelled `synthetic_demo` / FICTIONAL DATA.

Module map (read in this order to learn the pipeline):

  fixtures/manifest.json   the authoritative fictional league: teams, players, exact stats,
                           missing stats, graded assessments, known conflicts, documents
  manifest.py              loads and validates that manifest (pure Python)
  corpus.py                document loader + section chunker -> Chunks (LangChain Documents)
  entities.py              resolves names in a question against the manifest (pure Python)
  lexical.py               a small BM25 keyword index (pure Python)
  embeddings.py            Ollama embeddings with nomic's task prefixes (LangChain)
  retrieval.py             dense + lexical + reciprocal-rank fusion, with diagnostics
  claims.py                sentence / number / mention analysis shared by checks and evals
  policy.py                deterministic intent rules: legality, fit scores, out-of-corpus
  prompts.py               the system and user prompts (grounded and basic variants)
  grounding.py             post-generation checks: citations, numbers, legality, injection
  pipeline.py              the LangChain runnable chain that ties the stages together
  providers.py             Ollama chat model factory, readiness probe, error mapping
  tracing.py               opt-in LangSmith tracing (off by default)
  service.py               request bounding: in-flight limit, timeout, cancellation
  models.py                the typed API request/response contract
  evaluation/              the benchmark, deterministic scorers, local + LangSmith runners

Nothing in this package imports LangChain at module import time except the modules that
exist to wrap it (`embeddings`, `pipeline`, `providers`, `tracing`), and nothing imports
those from `app.main`'s import path: the API router imports them lazily inside the request,
so the standard install (`pip install -e ".[dev]"`) never needs the optional extra.
"""

DATA_MODE = "synthetic_demo"
SANDBOX_LABEL = "FICTIONAL DATA / SANDBOX"
#: The provenance key every source in this package carries. Registered in
#: `app.core.provenance.UPSTREAM_BY_PROVIDER` so it can never be described as NBA.com.
PROVIDER_KEY = "assistant_synthetic_corpus"
