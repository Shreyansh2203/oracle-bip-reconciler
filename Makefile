.PHONY: setup dev build test lint deadcode check clean

# Install dependencies using uv
setup:
	uv sync

# Run the local development server
dev:
	uv run task start

# Build the docker container
build:
	docker build -t oracle-reconciliation-api .

# Run unit tests
test:
	uv run task test

# Run code linter
lint:
	uv run task lint

# Report unreferenced code
deadcode:
	uv run task deadcode

# Run all code quality checks (linting, deadcode, tests)
check:
	uv run task check_all

# Remove caches and byte-compiled files
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
