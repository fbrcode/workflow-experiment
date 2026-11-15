# Workflow orchestrator experiment: Kestra vs Prefect. Run `make` for the list of targets.
SHELL := /bin/bash

# .env if present, otherwise the committed local-only defaults (one-command start from a clean clone).
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
INFRA    := docker compose --env-file $(ENV_FILE) -f infra/compose.yaml -p wfx-infra
KESTRA   := docker compose --env-file $(ENV_FILE) -f kestra/compose.yaml -p wfx-kestra
PREFECT  := docker compose --env-file $(ENV_FILE) -f prefect/compose.yaml -p wfx-prefect
MOCKS    := uv run --project infra/mocks

.DEFAULT_GOAL := help
.PHONY: help infra-up infra-down infra-ps infra-logs seed seed-check infra-check schema-check reset \
        golden golden-check fixtures test-mocks lint \
        kestra-up kestra-down kestra-deploy kestra-seed kestra-reset kestra-ps kestra-logs kestra-run test-kestra \
        prefect-up prefect-down prefect-deploy prefect-seed prefect-reset prefect-ps prefect-logs prefect-run test-prefect

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

schema-check: ## Diff app-db's \d+ output against the production schema (docs/baseline/prod-schema.md)
	@$(INFRA) exec -T app-db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --pset pager=off \
	  -c "\d+ news_email" -c "\d+ news_email_history" -c "\d+ telegram_chat"' | sh scripts/schema_check.sh

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
	$(MOCKS) ruff format --check infra/mocks scripts fixtures kestra prefect
	$(MOCKS) ruff check infra/mocks scripts fixtures kestra prefect

# --- Kestra (Phase 3, docs/kestra/README.md). One stack at a time: it brings its own copy of infra/.
KESTRA_API = set -a; source $(ENV_FILE); set +a; \
	KESTRA_URL=http://localhost:$$KESTRA_HOST_PORT KESTRA_USER=$$KESTRA_ADMIN_USER KESTRA_PASSWORD=$$KESTRA_ADMIN_PASSWORD

kestra-up: ## Build and start Kestra + its DB + the shared infra, wait until healthy, deploy flows
	$(KESTRA) up -d --build --wait
	@$(MAKE) --no-print-directory kestra-deploy

kestra-deploy: ## Push kestra/flows and the namespace files in kestra/files to the running Kestra
	@$(KESTRA_API) kestra/deploy.sh

kestra-seed: ## Empty the Kestra stack's mailbox and load the fixture emails as UNSEEN
	$(KESTRA) run --rm mail-seed --purge

kestra-reset: kestra-down kestra-up kestra-seed ## Recreate the Kestra stack from scratch and load the fixtures

kestra-down: ## Stop the Kestra stack and delete its volumes
	$(KESTRA) down -v --remove-orphans

kestra-ps: ## Kestra stack containers and health
	$(KESTRA) ps

kestra-logs: ## Follow the Kestra server log
	$(KESTRA) logs -f kestra

FLOW ?= email-summarize-send
kestra-run: ## Run a flow now and wait for it: make kestra-run [FLOW=email-ingest]
	@$(KESTRA_API) python3 scripts/kestra_run.py $(FLOW)

test-kestra: ## Golden-vector tests for the Kestra Python port (ING-1 also needs make kestra-up + kestra-seed)
	cd kestra && uv run pytest -q

# --- Prefect (Phase 4, docs/prefect/README.md). One stack at a time: it brings its own copy of infra/.
# Host-side Prefect CLI/client, pointed at the stack's API with its basic-auth credentials.
PREFECT_API = set -a; source $(ENV_FILE); set +a; \
	export PREFECT_API_URL=http://localhost:$$PREFECT_HOST_PORT/api PREFECT_API_AUTH_STRING=$$PREFECT_ADMIN_USER:$$PREFECT_ADMIN_PASSWORD;

prefect-up: ## Build and start Prefect server + worker + its DB + the shared infra, wait until healthy, deploy
	$(PREFECT) up -d --build --wait
	@$(MAKE) --no-print-directory prefect-deploy

prefect-deploy: ## Work pool, blocks and variables from the env file, and the deployments in prefect/prefect.yaml
	$(PREFECT) run --rm prefect-deploy

prefect-seed: ## Empty the Prefect stack's mailbox and load the fixture emails as UNSEEN
	$(PREFECT) run --rm mail-seed --purge

prefect-reset: prefect-down prefect-up prefect-seed ## Recreate the Prefect stack from scratch and load the fixtures

prefect-down: ## Stop the Prefect stack and delete its volumes
	$(PREFECT) down -v --remove-orphans

prefect-ps: ## Prefect stack containers and health
	$(PREFECT) ps

prefect-logs: ## Follow the Prefect worker log (flow runs log to the server too)
	$(PREFECT) logs -f prefect-worker

prefect-run: ## Run a deployment now and wait for it: make prefect-run [FLOW=email-ingest]
	@$(PREFECT_API) uv run --quiet --project prefect python scripts/prefect_run.py $(FLOW)

test-prefect: ## Golden-vector and unit tests for the Prefect port (ING-1 also needs make prefect-up + prefect-seed)
	cd prefect && uv run pytest -q
