# Oracle Reconciliation API

This is a high-performance FastAPI service designed to reconcile upstream invoice payloads with real-time data from an Oracle ERP system using Oracle BI Publisher (BIP) reports.

## Core Feature: Strict Sequential Customer Discovery

Because upstream systems frequently send corrupted, misspelled, or missing customer names and payment references, this API utilizes a highly resilient sequential architecture to identify the exact customer identity directly from Oracle before attempting any ledger reconciliation.

The engine moves to the next step only if the current one fails or returns no results.

### Step 1: Search by Customer Name
If the JSON provides a `customer_name`, the API uses it directly to find the records.

### Step 2: Search by Payment Reference
If Step 1 fails, the API uses the `payment_reference` to exactly search the **Receipt Details Report**.

### Step 3: Search by Invoice Details
If Step 2 fails (or if both Customer Name and Payment Reference are explicitly null), the API falls back to the **Invoice Details Report**. It applies parameters progressively to isolate a unique customer:
1. `invoice_number`
2. `invoice_number` + `invoice_amount`
3. `invoice_number` + `invoice_amount` + `invoice_date`

If a unique customer is still not identified after this rigorous sequence, the API gracefully returns `null`.

## Asynchronous Architecture
This engine is built on **FastAPI** and is 100% asynchronous. All heavy I/O operations and API requests to Oracle are automatically managed concurrently. This guarantees that the main event loop never blocks, allowing the server to handle thousands of concurrent reconciliation payloads simultaneously without freezing.

## Setup and Running

1. `uv sync`
2. `uv run task start` (or `make dev`)

The API will be available at `http://127.0.0.1:8000/`.
