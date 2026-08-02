.DEFAULT_GOAL := help
COMPOSE ?= docker compose
BACKEND  := backend

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- running the stack ------------------------------------------------------

.PHONY: up
up: ## Build and start the whole stack (postgres, qdrant, api, mcp, frontend)
	@test -f .env || (echo "No .env found. Run: cp .env.example .env" && exit 1)
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  Frontend    http://localhost:$${FRONTEND_PORT:-3000}"
	@echo "  API docs    http://localhost:$${API_PORT:-8000}/docs"
	@echo "  MCP server  http://localhost:$${MCP_PORT:-8080}/mcp  (Bearer \$$MCP_API_KEY)"

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete all data volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from every service
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

# --- data -------------------------------------------------------------------

.PHONY: seed
seed: ## Ingest the sample corpus (idempotent)
	$(COMPOSE) exec api kb-seed

.PHONY: migrate
migrate: ## Apply database migrations
	$(COMPOSE) exec api alembic upgrade head

# --- development ------------------------------------------------------------

.PHONY: install
install: ## Install backend and frontend dependencies locally
	cd $(BACKEND) && uv sync
	cd frontend && npm install

.PHONY: test
test: ## Run the backend test suite
	cd $(BACKEND) && uv run pytest -q

.PHONY: lint
lint: ## Lint and type-check the backend
	cd $(BACKEND) && uv run ruff check src tests
	cd $(BACKEND) && uv run ruff format --check src tests
	cd $(BACKEND) && uv run mypy

.PHONY: format
format: ## Auto-format the backend
	cd $(BACKEND) && uv run ruff format src tests
	cd $(BACKEND) && uv run ruff check --fix src tests

.PHONY: mcp-check
mcp-check: ## Verify the MCP endpoint: auth, handshake and tool list
	@./scripts/check_mcp.sh
