.PHONY: install test test-coverage lint format typecheck check run-stdio run-http docker-build docker-run env up down logs clean

install:
	uv pip install -e ".[dev]"

test:
	python -m pytest tests/ -v

test-coverage:
	python -m pytest tests/ --cov=grafana_mcp --cov-report=term-missing

lint:
	ruff check src/ tests/ scripts/

format:
	ruff format src/ tests/ scripts/

typecheck:
	mypy src/

check: lint typecheck test

run-stdio:
	python -m grafana_mcp

run-http:
	MCP_TRANSPORT=http MCP_HTTP_HOST=127.0.0.1 MCP_HTTP_PORT=8000 python -m grafana_mcp

docker-build:
	docker build -t grafana-server-mcp .

docker-run:
	docker compose up

# Export the host's gcx contexts and keyring tokens to grafana.env (mode 0600)
env:
	uv run scripts/gcx_to_env.py $(ARGS)

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist
