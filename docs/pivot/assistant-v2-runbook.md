# H. Ask Pivot V2 — runbook

*Exact Mac commands to install, run, evaluate and trace Ask Pivot locally. One command per
step. Everything here is local development only; nothing is meant for a public server.*

> **FICTIONAL DATA / SANDBOX.** Ask Pivot answers questions about an invented league. It never
> reads or writes Pivot's NBA database.

---

## 1. One-time setup

**1.1 Backend packages (optional extra).** From the repo root:

```bash
cd backend && .venv/bin/pip install -e ".[dev,assistant]"
```

This adds `langchain-core`, `langchain-ollama` and `langsmith` (and their dependencies) to
the backend venv. The standard install (`.[dev]`) is unchanged and keeps working without them.
To remove them later: `.venv/bin/pip uninstall langchain-core langchain-ollama langsmith ollama`.

**1.2 Ollama.** Install from <https://ollama.com/download> (the app, or `brew install ollama`),
then start the server (the macOS app starts it for you):

```bash
ollama serve
```

**1.3 Models** (≈2.7 GB chat model, ≈0.27 GB embedding model):

```bash
ollama pull qwen3.5:2b
```

```bash
ollama pull nomic-embed-text
```

Check they are present:

```bash
ollama list
```

A different chat model can be used with `ASSISTANT_CHAT_MODEL=<name>`; the benchmark numbers
in `assistant-v2-evaluation.md` were measured with `qwen3.5:2b`.

---

## 2. Launch Ask Pivot

Two terminals, from the repo root.

**Backend** (flag on, local only):

```bash
cd backend && ASSISTANT_ENABLED=true .venv/bin/uvicorn app.main:app --reload --port 8000
```

**Frontend** (the flag is inlined at build/dev start, and Next.js reads env from
`frontend/`, not the repo root):

```bash
cd frontend && NEXT_PUBLIC_ASSISTANT_ENABLED=true npm run dev -- --hostname 127.0.0.1
```

Bind the dev server to 127.0.0.1 as shown. Next's rewrite proxy forwards no client address to
the backend, so a dev server on the default 0.0.0.0 would let any machine on your network use
`/ask` through it (the backend additionally refuses any non-localhost `Host`, so a visit by
LAN IP gets a 403).

Or both at once:

```bash
make assistant-dev
```

Open **<http://localhost:3000/assistant>** (use `localhost` or `127.0.0.1`, not a LAN IP) — or click **Ask Pivot** (Intelligence group) in
the sidebar. The first answer after a cold start takes longer (model load + embedding the
corpus, ~10–30 s); later answers typically take 10–40 s on an Apple-silicon laptop.

Readiness check (shows Ollama, models, corpus, index and tracing state — never keys):

```bash
curl -s http://localhost:8000/api/v1/assistant/status | python3 -m json.tool
```

To persist the flags instead of exporting them, put `ASSISTANT_ENABLED=true` in the repo-root
`.env` (read by the backend's settings) and `NEXT_PUBLIC_ASSISTANT_ENABLED=true` in
`frontend/.env.local`. Both files are gitignored.

**Turning it off:** stop the servers, or start them without the flags. With the backend flag
off, `/assistant/ask` is a 404; with the frontend flag off, the nav item is gone and
`/assistant` renders the app's not-found page (served with HTTP 200 and `noindex`, because the root loading boundary streams first — a known limitation).

---

## 3. Tests

Ask Pivot unit tests (no Ollama needed — fakes throughout):

```bash
make assistant-test
```

The whole backend suite with the CI coverage floor:

```bash
make test-backend
```

Opt-in live tests against your local Ollama (real embeddings + generation):

```bash
cd backend && ASSISTANT_LIVE_TESTS=1 .venv/bin/pytest -q tests/unit/test_assistant_live_ollama.py
```

Frontend:

```bash
cd frontend && npm run lint && npx tsc --noEmit && npx vitest run
```

The frontend/backend contract test parses real backend responses (built with fakes) through
the frontend's zod schemas. After an intended change to the API shape, regenerate the fixture:

```bash
cd backend && UPDATE_CONTRACT_FIXTURE=1 .venv/bin/pytest -q tests/unit/test_assistant_contract_fixture.py
```

---

## 4. Evaluation

All evaluation commands run **from `backend/` as a module** (`python -m scripts.eval_rag`),
which is the fix for V1's `python scripts/eval_rag.py` import-path failure. Output goes to
`backend/.assistant_eval/` (gitignored).

**4.1 Scorer self-test** — proves every evaluator fails its adversarial false answers,
including the two V1 regressions. No Ollama needed:

```bash
make assistant-eval-selftest
```

**4.2 Retrieval benchmark** — recall@k / hit@k for dense, lexical and hybrid retrieval at
several k, on the benchmark *and* on 48 held-out questions written blind to the retriever
(`evaluation/retrieval_holdout.json`), plus how often the low-evidence gate fires on
out-of-scope questions (needs Ollama embeddings, no chat model; ~30 s):

```bash
make assistant-eval-retrieval
```

Without Ollama (keyword + entity retrieval only):

```bash
cd backend && .venv/bin/python -m scripts.eval_rag retrieval --no-embeddings
```

**4.3 Full local benchmark** — every case through the real pipeline, basic vs grounded
prompts, scored deterministically (needs Ollama; sequential; roughly 20–40 minutes):

```bash
make assistant-eval
```

Read `report.md` in the newest run directory. It reports every metric separately, twice:
on the model's raw text and on the answer after the guard removed provable errors (so the guard
cannot make the model look better than it is). Fill in `review_worksheet.md` by hand for the
subjective team-fit cases — the deterministic scorers do not judge recommendation quality — and
count it with:

```bash
cd backend && .venv/bin/python -m scripts.eval_rag review --tally .assistant_eval/<run>/review_worksheet.md
```

A harness check with a deterministic fake model (no Ollama; **not** a quality signal):

```bash
cd backend && .venv/bin/python -m scripts.eval_rag local --fake --cases fact_vance_three_point,legality_pure_holloway
```

**4.3b Multi-turn GM conversations** — four scripted conversations (13 turns) in which each
turn's history is the assistant's *own* earlier answers: a strategy, a priority revision, a
regret question, a legality question and a follow-up; the same question for two teams;
dated conflicts; a false premise, the injection document, a missing statistic and an
off-topic question. Writes a readable `transcript.md` (~3–4 minutes):

```bash
cd backend && .venv/bin/python -m scripts.eval_rag conversations
```

**4.4 Hosted LangSmith experiments** (only with an API key; see §5 first):

```bash
cd backend && LANGSMITH_API_KEY=<your key> .venv/bin/python -m scripts.eval_rag hosted --variants basic:hybrid,grounded:hybrid
```

The dataset name embeds the benchmark's content hash, so editing the benchmark creates a new
dataset instead of silently evaluating against a stale one.

---

## 5. LangSmith tracing (opt-in)

**Privacy:** with tracing on, every prompt, the retrieved fictional evidence and every answer
are uploaded to LangSmith, even though the model runs on your Mac. Keep it off unless you
want that.

Turn on for one backend run:

```bash
cd backend && ASSISTANT_ENABLED=true ASSISTANT_TRACING=true LANGSMITH_API_KEY=<your key> .venv/bin/uvicorn app.main:app --port 8000
```

Both values are required; `ASSISTANT_TRACING=true` without a key keeps tracing off. The
project defaults to `pivot-intelligence-v2` (`ASSISTANT_LANGSMITH_PROJECT`; a shell
`LANGSMITH_PROJECT` is ignored). With tracing on, each answer's Debug panel shows its run id
(and a link when LangSmith returns one).

Turn off: restart without `ASSISTANT_TRACING` (or set it to false). The SDK's own
`LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` / `LANGSMITH_PROJECT` are never read, so
exporting them for other projects — even together with `LANGSMITH_API_KEY` — cannot turn Ask
Pivot's tracing on or redirect its runs. If an older `.env` has `LANGSMITH_TRACING=true` for
Pivot, rename it to `ASSISTANT_TRACING=true`.

Never commit a key: `.env` and `frontend/.env.local` are gitignored; `.env.example` holds no
values.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Status shows Ollama unreachable, or `/ask` returns `provider_unavailable` | `ollama serve` (or open the Ollama app) |
| Status says a model is not pulled, or `/ask` returns `model_unavailable` | `ollama pull qwen3.5:2b` / `ollama pull nomic-embed-text` |
| Page says *The Pivot API isn't reachable* (a plain 500 from the Next dev proxy) | start the backend with `ASSISTANT_ENABLED=true` |
| `assistant_internal_error` "unexpected error (TypeError)" while building the index, status not ready | a bug, not Ollama: read the traceback in the backend log, then restart the backend |
| `assistant_dependencies_missing` | `cd backend && .venv/bin/pip install -e ".[dev,assistant]"` |
| `/assistant` shows the not-found page | start (or rebuild) the frontend with `NEXT_PUBLIC_ASSISTANT_ENABLED=true` |
| Page says the assistant is disabled on the server | start the backend with `ASSISTANT_ENABLED=true` |
| `assistant_local_only` (403) | while enabled, every assistant route requires a loopback peer, loopback `X-Forwarded-For` hops, and a `Host` / `X-Forwarded-Host` of localhost, 127.0.0.1 or [::1]. Opening the app by LAN IP or another hostname is refused; this is intended. `ASSISTANT_ALLOW_REMOTE=true` lifts it — never on a public server |
| `assistant_busy` | wait for the running answer (one at a time by default) |
| `provider_timeout` | the model took longer than `ASSISTANT_REQUEST_TIMEOUT_SECONDS` (150 s); retry — the first call after idle reloads the model |
| Very slow answers | make sure the model is not "thinking": Pivot sets `reasoning=False`; a different model may not support it |
