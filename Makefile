.PHONY: help start stop restart logs build test clean migrate db-shell format lint type-check
.PHONY: obs-start obs-stop obs-logs metrics traces dashboards
.PHONY: test-local test-local-stop test-e2e test-e2e-python

help:
	@echo "CIRIS Billing API - Available Commands:"
	@echo ""
	@echo "  make start         - Start all services"
	@echo "  make stop          - Stop all services"
	@echo "  make restart       - Restart all services"
	@echo "  make logs          - View logs (follow mode)"
	@echo "  make build         - Build Docker images"
	@echo "  make test          - Run tests"
	@echo "  make migrate       - Run database migrations"
	@echo "  make db-shell      - Open PostgreSQL shell"
	@echo "  make format        - Format code with black"
	@echo "  make lint          - Lint code with ruff"
	@echo "  make type-check    - Type check with mypy"
	@echo "  make clean         - Remove containers and volumes"
	@echo ""
	@echo "Observability Commands:"
	@echo "  make obs-start     - Start observability stack (Jaeger, Prometheus, Grafana)"
	@echo "  make obs-stop      - Stop observability stack"
	@echo "  make obs-logs      - View observability stack logs"
	@echo "  make metrics       - Open Prometheus UI"
	@echo "  make traces        - Open Jaeger UI"
	@echo "  make dashboards    - Open Grafana dashboards"
	@echo ""
	@echo "Local Testing Commands:"
	@echo "  make test-local    - Start complete local testing stack with test data"
	@echo "  make test-local-stop - Stop local testing stack"
	@echo "  make test-e2e      - Run end-to-end tests (bash)"
	@echo "  make test-e2e-python - Run end-to-end tests (pytest)"
	@echo ""

start:
	@./start.sh

stop:
	@echo "Stopping services..."
	@docker-compose down

restart: stop start

logs:
	@docker-compose logs -f

build:
	@echo "Building Docker images..."
	@docker-compose build

test:
	@echo "Running tests..."
	@docker-compose exec -T billing-api-1 pytest -v --cov=app

migrate:
	@echo "Running database migrations..."
	@docker-compose exec -T billing-api-1 alembic upgrade head

db-shell:
	@docker-compose exec postgres-primary psql -U billing_admin -d ciris_billing

format:
	@echo "Formatting code..."
	@black app/ tests/

lint:
	@echo "Linting code..."
	@ruff check app/ tests/

type-check:
	@echo "Type checking..."
	@mypy app/

clean:
	@echo "Cleaning up..."
	@docker-compose down -v
	@docker-compose -f docker-compose.observability.yml down -v
	@echo "Removed all containers and volumes"

# Observability commands
obs-start:
	@echo "Starting observability stack..."
	@docker-compose -f docker-compose.observability.yml up -d
	@echo "Observability stack started:"
	@echo "  Grafana:    http://localhost:3000 (admin/admin)"
	@echo "  Prometheus: http://localhost:9091"
	@echo "  Jaeger:     http://localhost:16686"

obs-stop:
	@echo "Stopping observability stack..."
	@docker-compose -f docker-compose.observability.yml down

obs-logs:
	@docker-compose -f docker-compose.observability.yml logs -f

metrics:
	@echo "Opening Prometheus..."
	@open http://localhost:9091 2>/dev/null || xdg-open http://localhost:9091 2>/dev/null || echo "Open http://localhost:9091 in your browser"

traces:
	@echo "Opening Jaeger..."
	@open http://localhost:16686 2>/dev/null || xdg-open http://localhost:16686 2>/dev/null || echo "Open http://localhost:16686 in your browser"

dashboards:
	@echo "Opening Grafana..."
	@open http://localhost:3000 2>/dev/null || xdg-open http://localhost:3000 2>/dev/null || echo "Open http://localhost:3000 in your browser (admin/admin)"

# Local testing commands
test-local:
	@./test-local.sh

test-local-stop:
	@echo "Stopping local testing stack..."
	@docker-compose -f docker-compose.local.yml down

test-e2e:
	@echo "Running end-to-end tests (bash)..."
	@./tests/e2e/run-tests.sh

test-e2e-python:
	@echo "Running end-to-end tests (pytest)..."
	@pip install -q httpx pytest 2>/dev/null || true
	@pytest tests/e2e/test_api_endpoints.py -v
