from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.api.routers import health, reconciliation
from src.constants import DEFAULT_TIMEOUT, MAX_CONNECTIONS
from src.core.config import settings
from src.core.dependencies import limiter
from src.core.metrics import REQUEST_COUNT, REQUEST_DURATION
from src.core.middleware import RequestBodyLimitMiddleware
from src.services.oracle_bip import close_cache


class JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        return json.dumps(payload, ensure_ascii=False)


class RequestIdAdapter(logging.LoggerAdapter):
    """Bind a request ID to every log record emitted for one request."""

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        extra = kwargs.get("extra")
        if extra is None:
            extra = {}
            kwargs["extra"] = extra
        if not isinstance(extra, dict):
            raise TypeError("extra must be a dict")
        extra["request_id"] = self.extra["request_id"]  # type: ignore[index]
        return msg, kwargs


# Configure structured JSON logging
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])
logger = logging.getLogger("reconciliation_api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS),
    )
    app.state.http_client = client
    logger.info("Initialized HTTP client in lifespan")
    try:
        yield
    finally:
        logger.info("Shutting down HTTP client")
        await client.aclose()
        await close_cache()


app = FastAPI(
    title="Oracle Reconciliation Live API",
    version="4.0.0",
    description="Professional enterprise API for Oracle ERP Cloud reconciliation matching.",
    contact={
        "name": "Platform Engineering",
        "email": "platform@example.com",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
    openapi_tags=[
        {
            "name": "health",
            "description": "Service health and readiness probes.",
        },
        {
            "name": "reconciliation",
            "description": "Oracle ERP Cloud invoice and receipt reconciliation.",
        },
    ],
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
    """Assign and propagate a request ID on every request.

    The request ID is read from ``X-Request-ID`` when present, otherwise a new
    UUIDv4 is minted. It is added to the response as ``X-Request-ID`` and bound
    to the log record so log aggregation can trace a request end to end.
    """
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id

    logger_with_id = RequestIdAdapter(logger, {"request_id": request_id})

    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger_with_id.exception("Unhandled error during request processing")
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger_with_id.info(
            "request completed",
            extra={
                "method": request.method,
                "path": str(request.url.path),
                "status_code": getattr(response, "status_code", None),
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )

    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def metrics_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Any]]
) -> Any:
    """Collect Prometheus metrics for every request."""
    if request.url.path == "/metrics":
        return await call_next(request)

    start = time.monotonic()
    response = await call_next(request)
    duration = time.monotonic() - start

    REQUEST_COUNT.labels(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        path=request.url.path,
    ).observe(duration)

    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.warning(
        "Request validation failed",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "errors": [
                {key: item[key] for key in ("type", "loc", "msg") if key in item}
                for item in exc.errors()
            ],
        },
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {key: item[key] for key in ("type", "loc", "msg") if key in item}
                for item in exc.errors()
            ]
        },
    )


# Setup CORS
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
if not origins:
    logger.warning("CORS_ORIGINS is empty. API will fail closed to browsers.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else [],  # Fail closed if no origins provided
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.add_middleware(RequestBodyLimitMiddleware)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, cast(Any, _rate_limit_exceeded_handler))
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled internal error",
        extra={
            "request_id": getattr(request.state, "request_id", None),
            "error_type": type(exc).__name__,
        },
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected internal server error occurred."},
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint."""
    return PlainTextResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


app.include_router(health.router)
app.include_router(reconciliation.router)
