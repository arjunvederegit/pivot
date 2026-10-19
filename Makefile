SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python
PIP := $(BACKEND)/.venv/bin/pip

E2E_DB ?= $(CURDIR)/backend/rosterlab-e2e.db

.PHONY: setup dev dev-backend dev-frontend test test-backend test-frontend lint format \
        sync-data build-features train score seed-config migrate e2e help \
        index-assets import-stats-csv import-kaggle seed-demo visual-qa worker \
        sync-corpus-stats sync-season-calendar \
        purge-fixtures import-contracts contract-coverage \
        import-draft-picks pick-ownership fetch-transactions import-transactions \
        transaction-coverage comparable-validation acquisition-validation lineup-availability \
        adversarial-validation \
        backtest train-candidate candidates materialise-skills \
        data-status season-status refresh link-balldontlie refresh-balldontlie balldontlie-plan balldontlie-preflight balldontlie-evaluate \
        assistant-setup assistant-models assistant-dev assistant-test assistant-eval \
        assistant-eval-selftest assistant-eval-retrieval assistant-eval-conversations

# `[a-z-]+` missed targets containing a digit or ending the alternation early, so
# `e2e` never appeared. Match the full target token instead.
help:
	@grep -E '^[a-z0-9][a-z0-9-]*:' Makefile | sed 's/:.*//' | sort -u

setup: ## Create backend venv, install backend + frontend dependencies
	python3.12 -m venv $(BACKEND)/.venv 2>/dev/null || python3.11 -m venv $(BACKEND)/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(BACKEND)[dev]"
	cd $(FRONTEND) && npm install
	@test -f .env || cp .env.example .env
	@echo "Setup complete. Edit .env, then run: make migrate && make sync-data && make dev"

migrate: ## Apply database migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

dev: ## Run backend (:8000) and frontend (:3000) together
	@trap 'kill 0' EXIT; \
	(cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000) & \
	(cd $(FRONTEND) && npm run dev) & \
	wait

dev-backend:
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd $(FRONTEND) && npm run dev

worker: ## Run the background scheduler locally (the same one docker compose runs)
	cd $(BACKEND) && .venv/bin/python -m app.worker

test: test-backend test-frontend ## Run all tests

# Mirrors CI exactly (see .github/workflows/ci.yml) so a green local run means a green
# CI run — including the coverage floor.
test-backend:
	cd $(BACKEND) && .venv/bin/pytest -q --cov=app --cov-report=term-missing --cov-fail-under=85

test-frontend:
	cd $(FRONTEND) && npm run test -- --run

backtest: ## R9.1 rolling-origin backtest (read-only on the DB): make backtest BT_STAGE=development|holdout BT_TARGET=impact_proxy|all [BT_OUT=dir]
	@test -n "$(BT_STAGE)" || (echo "usage: make backtest BT_STAGE=development|holdout [BT_TARGET=impact_proxy|all] [BT_OUT=dir]"; exit 2)
	cd $(BACKEND) && .venv/bin/python -m app.cli backtest $(BT_STAGE) $(if $(BT_TARGET),--target $(BT_TARGET),) $(if $(BT_OUT),--out $(BT_OUT),)

train-candidate: ## R9.1 candidate-only training (never changes an active model): make train-candidate CAND_MODEL=ridge_boxscore CAND_TARGET=impact_proxy [CAND_ORIGIN=2025-26] [CAND_EXPERIMENT=dir]
	@test -n "$(CAND_MODEL)" -a -n "$(CAND_TARGET)" || (echo "usage: make train-candidate CAND_MODEL=<name> CAND_TARGET=<target> [CAND_ORIGIN=<season>] [CAND_EXPERIMENT=<dir>]"; exit 2)
	cd $(BACKEND) && .venv/bin/python -m app.cli train-candidate --model $(CAND_MODEL) --target $(CAND_TARGET) $(if $(CAND_ORIGIN),--origin $(CAND_ORIGIN),) $(if $(CAND_EXPERIMENT),--experiment $(CAND_EXPERIMENT),)

materialise-skills: ## R9.2 store the validation registry's recommended models as candidates (never promotes; run on a COPY): make materialise-skills [SKILL_ORIGIN=2025-26]
	cd $(BACKEND) && .venv/bin/python -m app.cli materialise-skills $(if $(SKILL_ORIGIN),--origin $(SKILL_ORIGIN),)

candidates: ## List candidate and rejected model versions
	cd $(BACKEND) && .venv/bin/python -m app.cli candidates

sync-corpus-stats: ## Ingest player stats + standings + calendar for every CORPUS_SEASONS season
	cd $(BACKEND) && .venv/bin/python -m app.cli sync-corpus-stats

sync-season-calendar: ## Ingest each season's first and last regular-season game date
	cd $(BACKEND) && .venv/bin/python -m app.cli sync-season-calendar

import-contracts: ## Import contracts from the configured provider and report ROSTER-side coverage
	cd $(BACKEND) && .venv/bin/python -m app.cli sync-contracts

contract-coverage: ## Report contract coverage without importing anything
	cd $(BACKEND) && .venv/bin/python -m app.cli contract-coverage

data-status: ## Freshness, completeness and provider of every dataset the tools read (read-only)
	cd $(BACKEND) && .venv/bin/python -m app.cli data-status

refresh: ## Refresh selected datasets: make refresh DATASETS=rosters,standings,player-stats [ARGS='--season 2026-27 --season-type preseason'] (rosters default to the league year; add a roster_verification report)
	@test -n "$(DATASETS)" || (echo "usage: make refresh DATASETS=rosters,standings,player-stats,team-stats,games,season-calendar,contracts [ARGS='--season 2026-27']"; exit 2)
	cd $(BACKEND) && .venv/bin/python -m app.cli refresh $(DATASETS) $(ARGS)

season-status: ## Which season each kind of data is read from (rosters / statistics / contracts), roster completeness, and rollover readiness (read-only)
	cd $(BACKEND) && .venv/bin/python -m app.cli season-status

balldontlie-preflight: ## Is Pivot ready for the 48-hour trial clock? (no request, no key needed)
	cd $(BACKEND) && .venv/bin/python -m app.cli balldontlie-preflight $(if $(BUDGET),--budget $(BUDGET),) $(if $(SEASON),--season $(SEASON),)

balldontlie-plan: ## Print the GOAT trial evaluation plan and its request cost (no requests)
	cd $(BACKEND) && .venv/bin/python -m app.cli balldontlie-plan $(TIER)

link-balldontlie: ## Link BALLDONTLIE ids to Pivot players/teams (needs BALLDONTLIE_API_KEY, free tier is enough; DRY=1 to preview)
	cd $(BACKEND) && .venv/bin/python -m app.cli link-balldontlie $(if $(DRY),--dry-run,) $(if $(ACTIVE_ONLY),--active-only,)

refresh-balldontlie: ## Ingest BALLDONTLIE data (WHAT=identities|contracts|injuries|stats|all; CACHE=dir, REPLAY=1, DRY=1; needs a key unless REPLAY=1)
	cd $(BACKEND) && .venv/bin/python -m app.cli refresh-balldontlie $(WHAT) $(if $(SEASON),--season $(SEASON),) $(if $(STAT_SEASON),--stat-season $(STAT_SEASON),) $(if $(GROUPS),--groups $(GROUPS),) $(if $(CACHE),--cache $(CACHE),) $(if $(REPLAY),--replay,) $(if $(DRY),--dry-run,)

balldontlie-evaluate: ## Run the trial evaluation (needs a key; REPLAY=<dir> re-analyses recorded responses)
	cd $(BACKEND) && .venv/bin/python -m app.cli balldontlie-evaluate $(if $(REPLAY),--replay $(REPLAY),) $(if $(RESUME),--resume $(RESUME),) $(if $(BUDGET),--budget $(BUDGET),) $(if $(ONLY),--only $(ONLY),) $(if $(SEASON),--season $(SEASON),) $(if $(FORCE),--force,)

purge-fixtures: ## List test-run leftovers in the dev DB (add APPLY=1 to delete)
	cd $(BACKEND) && .venv/bin/python -m app.cli purge-fixtures $(if $(APPLY),--apply,)

seed-demo: ## Build a DEDICATED e2e database from the synthetic demo league
	rm -f "$(E2E_DB)"
	cd $(BACKEND) && DATABASE_URL="sqlite:///$(E2E_DB)" .venv/bin/alembic upgrade head
	cd $(BACKEND) && DATABASE_URL="sqlite:///$(E2E_DB)" .venv/bin/python -m app.cli seed-config
	cd $(BACKEND) && DATABASE_URL="sqlite:///$(E2E_DB)" .venv/bin/python -m app.cli seed-demo
	cd $(BACKEND) && DATABASE_URL="sqlite:///$(E2E_DB)" .venv/bin/python -m app.cli train
	cd $(BACKEND) && DATABASE_URL="sqlite:///$(E2E_DB)" .venv/bin/python -m app.cli score

e2e: seed-demo ## Playwright end-to-end tests against the dedicated demo database
	@# R7. Playwright no longer reuses a running server, because it was attaching to a
	@# developer's `make dev` — pointed at their ingested database — running the whole
	@# suite against it and writing fixture trades into it, while every test passed.
	@# Its own error for a busy port suggests setting `reuseExistingServer: true`, which
	@# is the defect. Say so before it gets the chance.
	@if lsof -ti:8000 -sTCP:LISTEN >/dev/null 2>&1 || lsof -ti:3000 -sTCP:LISTEN >/dev/null 2>&1; then 		echo ""; 		echo "  Port 8000 or 3000 is in use — probably \`make dev\`."; 		echo ""; 		echo "  This suite writes fixture trades into whatever backend it reaches, so it"; 		echo "  starts its own against \$$E2E_DB rather than reusing yours. Stop the dev"; 		echo "  servers and re-run."; 		echo ""; 		echo "  Do NOT set reuseExistingServer — attaching to a dev server is how the suite"; 		echo "  came to be testing the wrong database. If you really do have a demo-seeded"; 		echo "  backend up, use E2E_NO_SERVER=1."; 		echo ""; 		exit 1; 	fi
	cd $(FRONTEND) && DATABASE_URL="sqlite:///$(E2E_DB)" npx playwright test

visual-qa: ## Screenshot every route at every supported viewport; fails on problems
	cd $(FRONTEND) && node ../scripts/visual_qa.mjs $(OUT)

lint: ## Ruff + mypy + eslint + tsc
	cd $(BACKEND) && .venv/bin/ruff check app tests && .venv/bin/mypy app
	cd $(FRONTEND) && npm run lint && npx tsc --noEmit

format:
	cd $(BACKEND) && .venv/bin/ruff format app tests && .venv/bin/ruff check --fix app tests

sync-data: ## Ingest current NBA data via nba_api (network required)
	cd $(BACKEND) && .venv/bin/python -m app.cli sync-all

build-features: ## Build modeling features from ingested data
	cd $(BACKEND) && .venv/bin/python -m app.cli build-features

train: ## LEGACY: retrains and ACTIVATES the impact model and deletes superseded impact estimates (add ARGS=--preserve-history to keep them); for experiments use `make train-candidate`
	cd $(BACKEND) && .venv/bin/python -m app.cli train $(ARGS)

score: ## Score current players and compute team needs
	cd $(BACKEND) && .venv/bin/python -m app.cli score

seed-config: ## Load salary-cap parameter YAML into the database
	cd $(BACKEND) && .venv/bin/python -m app.cli seed-config

index-assets: ## Index local player photos / team logos into the media manifest
	cd $(BACKEND) && .venv/bin/python -m app.cli index-assets

import-stats-csv: ## Import the user-supplied season-totals CSV (data/imports/)
	cd $(BACKEND) && .venv/bin/python -m app.cli import-stats-csv

import-kaggle: ## Import historical enrichment from the Kaggle basketball dataset
	cd $(BACKEND) && .venv/bin/python -m app.cli import-kaggle

import-draft-picks: ## Import pick ownership from the local RealGM snapshot (data/imports/)
	cd $(BACKEND) && .venv/bin/python -m app.cli import-draft-picks

pick-ownership: ## Report verified pick ownership for a draft year (YEAR=2029 ROUND=1)
	cd $(BACKEND) && .venv/bin/python -m app.cli pick-ownership $(YEAR) $(ROUND)

fetch-transactions: ## Fetch BBRef season transaction pages into data/imports/ (FROM=2017 TO=2026)
	cd $(BACKEND) && .venv/bin/python -m app.cli fetch-transactions $(FROM) $(TO) $(if $(FORCE),--force,)

import-transactions: ## Parse the local transaction snapshots into historical_trades
	cd $(BACKEND) && .venv/bin/python -m app.cli import-transactions

transaction-coverage: ## Report what the imported historical-trade corpus contains
	cd $(BACKEND) && .venv/bin/python -m app.cli transaction-coverage

adversarial-validation: ## Run the adversarial trade battery (fails on any honesty violation)
	cd $(BACKEND) && .venv/bin/python -m app.cli adversarial-validation

comparable-validation: ## Run the comparable-trade validation battery (fails on a threshold)
	cd $(BACKEND) && .venv/bin/python -m app.cli comparable-validation

acquisition-validation: ## Run the need-driven acquisition battery over the league
	cd $(BACKEND) && .venv/bin/python -m app.cli acquisition-validation

lineup-availability: ## Re-measure whether lineup data could support a fit model (network)
	cd $(BACKEND) && .venv/bin/python -m app.cli lineup-availability $(SEASON)

# --- Ask Pivot / Pivot Intelligence (EXPERIMENTAL; fictional corpus; local only) ---------
# Nothing below runs in CI or touches the NBA database. See docs/pivot/assistant-v2-runbook.md.

assistant-setup: ## Install the optional Ask Pivot packages (LangChain, Ollama client, LangSmith)
	$(PIP) install -e "$(BACKEND)[dev,assistant]"

assistant-models: ## Pull the two local Ollama models Ask Pivot uses (requires Ollama)
	ollama pull qwen3.5:2b
	ollama pull nomic-embed-text

# The frontend binds to loopback: Next's rewrite proxy forwards no client address, so a
# dev server on 0.0.0.0 would let any LAN machine reach the unauthenticated /ask route.
assistant-dev: ## Run backend + frontend with Ask Pivot enabled (local development only)
	@trap 'kill 0' EXIT; \
	(cd $(BACKEND) && ASSISTANT_ENABLED=true .venv/bin/uvicorn app.main:app --reload --port 8000) & \
	(cd $(FRONTEND) && NEXT_PUBLIC_ASSISTANT_ENABLED=true npm run dev -- --hostname 127.0.0.1) & \
	wait

assistant-test: ## Ask Pivot unit tests only (no Ollama needed)
	cd $(BACKEND) && .venv/bin/pytest -q tests/unit -k assistant

assistant-eval-selftest: ## Prove every evaluator fails its adversarial false answers (no Ollama)
	cd $(BACKEND) && .venv/bin/python -m scripts.eval_rag selftest

assistant-eval-retrieval: ## Recall@k for dense / lexical / hybrid retrieval (needs Ollama embeddings)
	cd $(BACKEND) && .venv/bin/python -m scripts.eval_rag retrieval

assistant-eval: ## Local benchmark, basic vs grounded prompts (needs Ollama; ~20-40 min)
	cd $(BACKEND) && .venv/bin/python -m scripts.eval_rag local --variants basic:hybrid,grounded:hybrid

assistant-eval-conversations: ## Scripted multi-turn GM conversations, scored turn by turn (needs Ollama; ~4 min)
	cd $(BACKEND) && .venv/bin/python -m scripts.eval_rag conversations
