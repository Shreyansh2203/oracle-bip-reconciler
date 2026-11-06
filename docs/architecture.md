# Architecture

## Overview

Oracle-BIP-Reconciler is a FastAPI service for reconciling Oracle ERP Cloud / Oracle BIP (Business Intelligence Publisher) data.

## Components

### API Layer (FastAPI)
- `/health` — Service health check
- `/ready` — Readiness probe
- `/metrics` — Prometheus metrics
- `/v1/reconcile/batch` — Batch reconciliation

### Core
- `config.py` — pydantic-settings configuration (fail-closed)
- `dependencies.py` — Auth limiter, dependencies
- `metrics.py` — Prometheus REQUEST_COUNT, REQUEST_DURATION
- `middleware.py` — RequestBodyLimitMiddleware

### Services
- `oracle_bip.py` — Oracle BIP client with caching
- `discovery.py` — Service discovery
- `reconciliation.py` — Reconciliation logic

### Security
- Fail-closed authentication (ALLOW_UNAUTHENTICATED_ACCESS=false by default)
- Authenticating proxy required in production
- Rate limiting via slowapi
- Request body limits via middleware

## Deployment

- Render: `render.yaml` (pre-configured)
- Docker: `Dockerfile`
- Vercel: `vercel.json` (serverless functions)
- Raw: `uvicorn src.main:app`