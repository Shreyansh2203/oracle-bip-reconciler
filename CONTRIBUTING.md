# Contributing to Oracle-BIP-Reconciler

Thank you for your interest in contributing! This document provides guidelines for contributing to Oracle-BIP-Reconciler.

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold this code.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Git

### Development Setup

```bash
# Clone the repository
git clone https://github.com/Shreyansh2203/oracle-bip-reconciler.git
cd oracle-bip-reconciler

# Create venv with uv
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
uv sync --dev

# Configure environment
cp .env.example .env
# Edit .env with your Oracle credentials

# Run the API
uvicorn src.main:app --reload
```

### Running Tests

```bash
# Full test suite (330 tests, 100% coverage)
uv run pytest tests/ -q

# With coverage report
uv run pytest tests/ --cov=src --cov-report=term-missing

# Lint
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Type check
uv run mypy src/

# Security scan
uv run bandit -r src/
uv run pip-audit
```

## How to Contribute

### Reporting Bugs

1. Check if the bug has already been reported in [Issues](https://github.com/Shreyansh2203/oracle-bip-reconciler/issues)
2. If not, create a new issue using the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.yml)
3. Include reproduction steps, expected behavior, and actual behavior

### Suggesting Features

1. Check existing [Issues](https://github.com/Shreyansh2203/oracle-bip-reconciler/issues)
2. Create a new issue using the [Feature Request template](.github/ISSUE_TEMPLATE/feature_request.yml)

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature-name`
3. Make your changes
4. Run all tests and checks
5. Commit with a descriptive message (see below)
6. Push to your fork
7. Open a Pull Request

### Commit Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new feature
fix: fix a bug
docs: update documentation
style: formatting changes
refactor: code restructuring
test: add or update tests
chore: maintenance tasks
perf: performance improvements
ci: CI/CD changes
```

### Code Style

- **Python**: Ruff (lint + format), line-length 120, target py312
- **Type hints**: Required on all function signatures
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Imports**: Sorted by ruff (isort rules)

### Testing Requirements

- All new features must include tests
- All bug fixes must include a regression test
- Maintain 100% coverage on reconciliation layer
- Run the full test suite before submitting

## Review Process

1. A maintainer will review your PR within 48 hours
2. Address any requested changes
3. Once approved, a maintainer will merge

## License

By contributing, you agree that your contributions will be licensed under the MIT License. See [LICENSE](LICENSE) for details.