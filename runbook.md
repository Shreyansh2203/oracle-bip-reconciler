# Operational Runbook

## Service Dependencies
*   **Oracle ERP Cloud**: External SaaS system containing the source-of-truth invoices.
*   **Redis/Memcached**: Not used. State is maintained in-memory per-request.

## Metrics & Alerting
The system emits structured JSON logs upon completion of each request. Filter by `Reconciliation Summary` to parse:
*   `duration_ms`: Total latency. Alert if > 100,000ms.
*   `bip_matched` / `rest_matched`: Matching efficiency.
*   `oracle_calls`: Number of REST fallback queries generated. Alert if consistently high (>50 per payload) as it indicates a failure in the BIP system.

## Handling Oracle 429 Throttle / 503 Errors
If the service logs permanent `503` or `429` errors:
1.  **Cause**: The concurrency limit (`MAX_CONCURRENCY`) is set too high for your Oracle tenant's capacity limit.
2.  **Mitigation**: Lower the `MAX_CONCURRENCY` environment variable (default: 50). Oracle typically peaks at 26 TPS, so a concurrency of 30-40 is extremely safe, while 50 pushes the boundaries of a standard tenant.
3.  **Automatic Behavior**: The app uses Tenacity to automatically implement Exponential Backoff retries for transient HTTP errors. No manual intervention is needed for sporadic blips.

## Credential Rotation
To rotate Oracle credentials:
1. Update `ORACLE_USER` and `ORACLE_PASS` in the deployment environment variables.
2. Restart the deployment (or trigger a new build). The system will read the new credentials on the next request. No downtime is expected.

## API Key Rotation
To rotate the consumer API Key:
1. Update `API_KEY` in the deployment environment variables.
2. Ensure the consuming clients are updated to send the new key via the `X-API-Key` HTTP Header.

## SLA & Expected Latency
*   **Small Payloads** (< 50 Invoices): 5 - 8 Seconds
*   **Medium Payloads** (~ 500 Invoices): 10 - 15 Seconds
*   **Massive Payloads** (> 1,000 Invoices): 30 - 45 Seconds
*(Latency assumes a 90%+ BI Publisher hit rate. If BIP fails, REST fallback will linearly increase latency).*
