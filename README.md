# Oracle Reconciliation API

This is a high-performance FastAPI service designed to reconcile upstream invoice payloads with real-time data from an Oracle ERP system using Oracle BI Publisher (BIP) reports.

## Core Feature: The 5-Tier Customer Discovery Waterfall

Because upstream systems frequently send corrupted, misspelled, or missing customer names and payment references, this API utilizes a highly resilient 5-tier fallback search to reverse-engineer the correct customer identity directly from Oracle before attempting any ledger reconciliation. 

If one priority fails to find a match, it gracefully falls back to the next, guaranteeing maximum match-rates without false positives.

### Priority 1: Exact Payment Reference
The API queries Oracle for the exact `payment_reference`. If exactly one receipt is found, it extracts the customer name associated with that receipt.

### Priority 1b: Stripped Payment Reference
If the exact match fails, the API strips all non-alphanumeric characters and leading zeros from the reference. If the resulting string is at least 6 characters long, it queries Oracle again. This safely recovers payments where banks prepend codes (e.g. `WT-000000324185` -> `324185`).

### Priority 2: Exact Customer Name
If the reference fails, it queries Oracle for the `customer_name` string provided in the payload.

### Priority 3: Reference (Locally Filtering Amount + Date)
If both single-parameter searches fail, the API queries Oracle using the `payment_reference` and then filters the results in Python memory to strictly match the `total_amount` and `payment_date`. This handles cases where the customer name is entirely mangled without triggering Oracle parameter errors.

### Priority 3b: Stripped Reference (Locally Filtering Amount + Date)
Similar to Priority 1b, if the exact 3-way match fails, it queries Oracle again using the stripped numeric reference and filters the results locally by the exact amount and date.

### Priority 4: Strict Invoice-Level Discovery
If all receipt searches fail (meaning the receipt is missing or the date/amounts don't align), the system falls back to the provided invoice lines. 
To guarantee strict matching and avoid pulling irrelevant data, the API will **only** search for an invoice if the payload provides **all three** of the following parameters:
1. `invoice_number`
2. `invoice_date`
3. `invoice_amount`

If an invoice line possesses all three, they are sent to Oracle concurrently. If Oracle finds that specific invoice, the API extracts the `BILL_CUSTOMER_NAME` from it and uses it to locate the missing receipt.

## Setup and Running

1. `uv sync`
2. `uv run task start` (or `make dev`)

The API will be available at `http://127.0.0.1:8000/`.
