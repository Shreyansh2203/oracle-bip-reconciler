# Oracle Cloud ERP Reconciliation API

A professional, enterprise-grade FastAPI microservice designed to perform real-time, strict cascading reconciliation of incoming payment and invoice payloads against Oracle Cloud ERP's REST APIs.

## Architecture

This project is structured for high maintainability, separating routing, business logic, and data models:
- `src/main.py`: FastAPI application entrypoint and route definitions.
- `src/services/oracle_matcher.py`: Core asynchronous cascading rule execution against Oracle ERP.
- `src/models.py`: Pydantic models enforcing schema contracts.
- `src/utils/date_formatter.py`: Utilities for standardizing incoming dates to Oracle's `YYYY-MM-DD` format.

## Setup & Local Development

This project uses `uv` for lightning-fast dependency management.

1. **Install dependencies:**
   ```bash
   uv pip install -r requirements.txt
   ```

2. **Environment Variables:**
   Copy the example config and add your Oracle credentials:
   ```bash
   cp .env.example .env
   ```

3. **Run the local development server:**
   ```bash
   fastapi dev src/main.py
   ```
   The API will be available at `http://localhost:8000`.

## Deployment

This repository is pre-configured for Serverless Deployment on **Vercel**.
- The `vercel.json` file points directly to the `src/main.py` entrypoint.
- Ensure you set the `ORACLE_USER` and `ORACLE_PASS` Environment Variables in your Vercel Project Settings before deploying.

## API Usage

### `POST /reconcile`
Takes an extracted JSON payload containing receipt and invoice details and matches them against live Oracle data.

**Example Request:**
```json
{
    "payment_reference": "RECEIPT005",
    "total_amount": 3424.0,
    "payment_date": "2026/05/10",
    "customer_name": "New Horizon Foods",
    "invoices": [
        {
            "invoice_number": "126129803472",
            "invoice_amount": 3424.0,
            "invoice_date": "2026/10/05"
        }
    ]
}
```

The API will execute its strict cascading rule engine concurrently and return the exact payload enriched with `fusion_` prefixed attributes where matches were found in Oracle.
