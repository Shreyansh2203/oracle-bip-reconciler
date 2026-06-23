.PHONY: setup dev build test lint check clean

# Install dependencies using uv
setup:
	uv sync

# Run the local development server
dev:
	uv run taskipy run start

# Build the docker container
build:
	docker build -t oracle-reconciliation-api .

# Run unit tests
test:
	uv run taskipy run test

# Run code linter
lint:
	uv run taskipy run lint

# Run all code quality checks (linting, deadcode, tests)
check:
	uv run taskipy run check_all

# Clean cache directories
clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache __pycache__
