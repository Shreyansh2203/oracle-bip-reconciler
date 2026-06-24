# Oracle Reconciliation API

This is a high-performance FastAPI service designed to reconcile upstream invoice payloads with real-time data from an Oracle ERP system using Oracle BI Publisher (BIP) reports.

## Core Feature: Strict 3-Tier Customer Discovery Sequence

Because upstream systems frequently send corrupted, misspelled, or missing customer names and payment references, this API utilizes a highly resilient sequence to identify the exact customer identity directly from Oracle before attempting any ledger reconciliation.

If the Customer Name cannot be identified from the **Receipt Details Report**, the system attempts to identify it using the **Invoice Details Report**. If it still cannot be determined, the API returns **null**.

### Priority 1: Customer Name Available
If the JSON provides a `customer_name`, the API uses it directly to find the records.

### Priority 2: Payment Reference Available (Receipt Details)
If the Customer Name fails or is missing, the API uses the `payment_reference` to search the **Receipt Details Report**. It attempts an exact match, and if that fails, a stripped match (removing all non-alphanumeric characters and leading zeros).

### Priority 3: Invoice Details Sequence (Final Fallback)
If both the Customer Name and Payment Reference are missing from the JSON, or if the Receipt Details Report failed to identify the customer, the system falls back to the **Invoice Details Report**.

To avoid false positives, the API queries Oracle in a strict sequence to isolate exactly one unique customer:
1. **Level 1**: Queries Oracle using ONLY the `invoice_number` across all provided invoices.
2. **Level 2**: If multiple clashing customers are found (or none), it narrows the search using `invoice_number` + `invoice_amount`.
3. **Level 3**: If still not uniquely identified, it narrows the search further using `invoice_number` + `invoice_amount` + `invoice_date`.

Once exactly one customer name is mathematically isolated from this sequence, the API adopts it and proceeds to the mapping phase.

## Asynchronous Architecture
This engine is built on **FastAPI** and is 100% asynchronous. All heavy CPU-bound mathematical operations (e.g., Levenshtein distance calculations, SciPy's Hungarian algorithm for bipartite mapping) are automatically offloaded to a background thread pool via `asyncio.to_thread()`. This guarantees that the main event loop never blocks, allowing the server to handle thousands of concurrent reconciliation payloads simultaneously without freezing.

## Setup and Running

1. `uv sync`
2. `uv run task start` (or `make dev`)

The API will be available at `http://127.0.0.1:8000/`.
