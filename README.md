# Oracle-BIP-Reconciler

<div align="center">

[![Build Status](https://img.shields.io/github/actions/workflow/status/Shreyansh2203/oracle-bip-reconciler/ci.yml?branch=main&label=CI&logo=github-actions&style=for-the-badge)](https://github.com/Shreyansh2203/oracle-bip-reconciler/actions)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/Shreyansh2203/oracle-bip-reconciler/codeql.yml?branch=main&label=CodeQL&logo=github&style=for-the-badge)](https://github.com/Shreyansh2203/oracle-bip-reconciler/security/code-scanning)
[![Bandit](https://img.shields.io/badge/security-bandit-green?style=for-the-badge)](https://bandit.readthedocs.io/)
[![Vulture](https://img.shields.io/badge/dead-code-vulture-blue?style=for-the-badge)](https://github.com/jendrikseipp/vulture)
[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-blue?style=for-the-badge&logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-white?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)

</div>

<details open>
<summary><b>Table of Contents</b></summary>

- [Overview](#-overview)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [API Endpoints](#-api-endpoints)
- [Security](#-security)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [Contributing](#-contributing)
- [License](#-license)

</details>

---

## Overview

**Oracle-BIP-Reconciler** is a production-grade FastAPI service for reconciling Oracle ERP Cloud / Oracle BIP (Business Intelligence Publisher) data. It provides authenticated, rate-limited reconciliation endpoints with structured JSON logging, Prometheus metrics, fail-closed authorization, and comprehensive test coverage (100% on the reconciliation layer).

Designed to run behind an authenticating proxy in enterprise environments, with fail-closed security by default.

---

## Features

| Feature | Description |
|---------|-------------|
| **Fail-closed auth** | `ALLOW_UNAUTHENTICATED_ACCESS=false` by default; requires authenticating proxy |
| **Structured logging** | `JsonFormatter` + `RequestIdAdapter` for request-traceable logs |
| **Prometheus metrics** | `/metrics` endpoint with `REQUEST_COUNT` and `REQUEST_DURATION` |
| **Rate limiting** | `slowapi` middleware with configurable limits |
| **Request body limits** | Middleware-enforced max request size |
| **CORS** | Configurable `CORSMiddleware` |
| **Health / readiness** | `/health` and `/ready` endpoints |
| **Dead-code detection** | `vulture` integrated into CI |
| **Security scanning** | `bandit` SAST + `pip-audit` dependency audit |
| **100% test coverage** | 1032/1032 tests passing on reconciliation layer |

---

## Tech Stack

<details>
<summary><b>Backend</b></summary>

- [Python 3.12+](https://www.python.org/)
- [FastAPI](https://fastapi.tiangolo.com/) — async web framework
- [Pydantic 2](https://docs.pydantic.dev/) — data validation & settings
- [Uvicorn](https://www.uvicorn.org/) — ASGI server
- [httpx](https://www.python-httpx.org/) — async HTTP client
- [tenacity](https://tenacity.readthedocs.io/) — retry logic
- [slowapi](https://pypi.org/project/slowapi/) — rate limiting
- [redis](https://redis.io/) — caching (optional)
- [prometheus-client](https://github.com/prometheus/client_python) — metrics

</details>

<details>
<summary><b>Dev / Security</b></summary>

- [pytest](https://docs.pytest.org/) + [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) — testing
- [respx](https://respx.readthedocs.io/) — HTTP mocking
- [ruff](https://docs.astral.sh/ruff/) — lint + format
- [mypy](http://mypy.readthedocs.io/) — static type checking
- [bandit](https://bandit.readthedocs.io/) — SAST security scanning
- [vulture](https://github.com/jendrikseipp/vulture) — dead code detection
- [pip-audit](https://pypa.github.io/pip-audit/) — dependency vulnerability audit

</details>

---

## Project Structure

```
oracle-bip-reconciler/
├── src/
│   ├── main.py                 # FastAPI app, JsonFormatter, RequestIdAdapter
│   ├── constants.py            # DEFAULT_TIMEOUT, MAX_CONNECTIONS
│   ├── models.py               # Pydantic models
│   ├── api/
│   │   ├── routers/
│   │   │   ├── health.py       # /health, /ready
│   │   │   └── reconciliation.py
│   │   └── __init__.py
│   ├── core/
│   │   ├── config.py           # pydantic-settings Settings
│   │   ├── dependencies.py     # limiter, auth dependencies
│   │   ├── metrics.py          # REQUEST_COUNT, REQUEST_DURATION
│   │   ├── middleware.py       # RequestBodyLimitMiddleware
│   │   └── __init__.py
│   ├── services/
│   │   ├── oracle_bip.py       # Oracle BIP client + cache
│   │   ├── discovery.py        # service discovery
│   │   ├── reconciliation.py   # reconciliation logic
│   │   └── __init__.py
│   ├── templates/
│   │   └── index.html
│   └── utils/
│       ├── date_formatter.py
│       └── validators.py
├── tests/                      # 330 tests, 100% coverage
├── .github/workflows/
│   ├── ci.yml
│   └── codeql.yml
├── pyproject.toml
├── render.yaml
├── Dockerfile
├── Makefile
├── .env.example
├── uv.lock
└── LICENSE
```

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Install & Run

```bash
cd oracle-bip-reconciler

# Create venv with uv
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
uv sync

# Configure environment (copy from .env.example)
cp .env.example .env
# Edit .env with your ORACLE_URL, ORACLE_USER, ORACLE_PASS

# Run the API (http://localhost:8000)
uvicorn src.main:app --reload
```

### Verify It's Running

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/ready
# {"status":"ready"}

curl http://localhost:8000/metrics
# Prometheus text format
```

---

## Configuration

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `ORACLE_URL` | — | Yes | Oracle BIP base URL |
| `ORACLE_USER` | — | Yes | Oracle BIP username |
| `ORACLE_PASS` | — | Yes | Oracle BIP password |
| `ALLOW_UNAUTHENTICATED_ACCESS` | `false` | No | Fail-closed auth (keep `false` in prod) |
| `DEFAULT_TIMEOUT` | `30` | No | HTTP timeout seconds |
| `MAX_CONNECTIONS` | `100` | No | Max HTTP connections |

All settings are loaded via [pydantic-settings](https://docs.pydantic.dev/latest/integrations/settings/) from environment variables or `.env`. The app **refuses to start** if required Oracle credentials are missing (fail-closed).

---

## API Endpoints

### `GET /health`

Service health check.

```json
{"status": "ok"}
```

### `GET /ready`

Readiness probe (checks dependencies).

```json
{"status": "ready"}
```

### `GET /metrics`

Prometheus-formatted metrics:

```
# HELP request_count Total HTTP requests
# TYPE request_count counter
request_count{method="GET",path="/health"} 1
```

### `POST /reconcile` (and related)

Oracle BIP reconciliation endpoints — see `src/api/routers/reconciliation.py` for full contract.

---

## Security

| Control | Implementation |
|---------|---------------|
| **Fail-closed auth** | `ALLOW_UNAUTHENTICATED_ACCESS=false` by default; README §Security requires authenticating proxy |
| **SAST** | `bandit` scans on every push |
| **Dependency audit** | `pip-audit` in CI |
| **Dead code** | `vulture` checks for unused code |
| **Secrets never logged** | `JsonFormatter` scrubs sensitive fields |
| **Rate limiting** | `slowapi` middleware |
| **Body limits** | `RequestBodyLimitMiddleware` |

Authentication must be handled by an authenticating proxy in front of this service. See [SECURITY policy](https://github.com/Shreyansh2203/oracle-bip-reconciler/security/policy).

---

## Testing

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

Coverage gate: 100% on reconciliation layer. CI enforces `1032/1032` tests passing.

---

## Deployment

| Platform | Target |
|----------|--------|
| **Render** | `render.yaml` (pre-configured) |
| **Docker** | `docker build -t oracle-bip-reconciler .` |
| **Vercel** | `vercel.json` (serverless functions) |
| **Raw** | `uvicorn src.main:app --host 0.0.0.0 --port 8000` |

**Production requirements:**
- Set `ALLOW_UNAUTHENTICATED_ACCESS=false`
- Place an authenticating proxy (OAuth2/OIDC/IAP) in front
- Provide real `ORACLE_URL`/`ORACLE_USER`/`ORACLE_PASS`
- Enable TLS termination at the edge

---

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/your-feature`)
3. Run checks: `ruff check`, `ruff format --check`, `mypy src/`, `bandit -r src/`, `pytest tests/`
4. Commit and open a Pull Request

---

## License

Distributed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<div align="center">

**Made with ❤️ by [Shreyansh Srivastava](https://github.com/Shreyansh2203)**

[![GitHub stars](https://img.shields.io/github/stars/Shreyansh2203/oracle-bip-reconciler?style=social)](https://github.com/Shreyansh2203/oracle-bip-reconciler/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/Shreyansh2203/oracle-bip-reconciler?style=social)](https://github.com/Shreyansh2203/oracle-bip-reconciler/network)

</div>