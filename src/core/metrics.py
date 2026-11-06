from __future__ import annotations

from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter(
    "reconciliation_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status_code"],
)

REQUEST_DURATION = Histogram(
    "reconciliation_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

RECONCILIATION_COUNT = Counter(
    "reconciliation_batch_total",
    "Total number of reconciliation batch requests",
    ["status"],
)
