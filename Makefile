# Workflow orchestrator experiment: Kestra vs Prefect. Run `make` for the list of targets.
SHELL := /bin/bash

# .env if present, otherwise the committed local-only defaults (one-command start from a clean clone).
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
INFRA    := docker compose --env-file $(ENV_FILE) -f infra/compose.yaml -p wfx-infra
MOCKS    := uv run --project infra/mocks

.DEFAULT_GOAL := help
.PHONY: help infra-up infra-down infra-ps infra-logs seed seed-check infra-check reset \
        golden golden-check fixtures test-mocks lint

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'
	@echo "  (env file: $(ENV_FILE))"

## --- Shared infrastructure (Phase 1) ---------------------------------------------------------
infra-up: ## Build and start app-db, mail, mock-telegram, mock-gemini; wait until healthy
	$(INFRA) up -d --build --wait

infra-down: ## Stop the shared infra and delete its volumes
	$(INFRA) down -v --remove-orphans

infra-ps: ## Show infra containers and health
	$(INFRA) ps

infra-logs: ## Follow infra logs
	$(INFRA) logs -f

seed: ## Empty the IMAP mailbox and load all fixture emails as UNSEEN
	$(INFRA) run --rm mail-seed --purge

seed-check: ## Print message/unseen counts of the IMAP mailbox
	$(INFRA) run --rm mail-seed --check

infra-check: ## Verify the running infra: DB schema + seed, mocks health, IMAP counts
	@echo "--- app-db"; \
	$(INFRA) exec -T app-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -tA' < infra/db/check.sql
	@echo "--- mocks"; \
	set -a; source $(ENV_FILE); set +a; \
	curl -fsS "http://localhost:$$MOCK_TELEGRAM_HOST_PORT/_mock/health"; echo; \
	curl -fsS "http://localhost:$$MOCK_GEMINI_HOST_PORT/_mock/health"; echo
	@echo "--- mail"; $(MAKE) --no-print-directory seed-check

reset: infra-down infra-up seed ## Recreate the infra from scratch and load the fixtures

## --- Golden vectors (Phase 2) -----------------------------------------------------------------
golden: ## Regenerate tests/golden/vectors from the original n8n code (starts infra, reseeds mail)
	$(INFRA) up -d --build --wait
	$(INFRA) run --rm mail-seed --purge
	$(INFRA) run --rm --build golden
	$(INFRA) run --rm mail-seed --purge  # n8n-style reads leave mail untouched, but keep state canonical

golden-check: golden ## Regenerate and fail if the committed vectors changed
	git diff --exit-code --stat -- tests/golden/vectors && echo "golden vectors reproducible"

## --- Development ------------------------------------------------------------------------------
fixtures: ## Regenerate fixtures/emails/*.eml from fixtures/generate.py
	python3 fixtures/generate.py

test-mocks: ## Run the mock services' unit tests
	cd infra/mocks && uv run pytest -q

lint: ## Ruff format check + lint for all Python in the repo
	$(MOCKS) ruff format --check infra/mocks scripts fixtures
	$(MOCKS) ruff check infra/mocks scripts fixtures
