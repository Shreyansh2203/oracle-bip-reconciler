# Oracle Reconciliation API

This is a high-performance FastAPI service designed to reconcile upstream invoice payloads with real-time data from an Oracle ERP system using Oracle BI Publisher (BIP) reports.

## Core Feature: The 5-Tier Customer Discovery Waterfall

Because upstream systems frequently send corrupted, misspelled, or missing customer names and payment references, this API utilizes a highly resilient 5-tier fallback search to reverse-engineer the correct customer identity directly from Oracle before attempting any ledger reconciliation. 

If one priority fails to find a match, it gracefully falls back to the next, guaranteeing maximum match-rates without false positives.

### Priority 1: Exact Payment Reference
The API queries Oracle for the exact `payment_reference`. If exactly one receipt is found, it extracts the customer name associated with that receipt.

### Priority 2: Exact Customer Name
If the reference fails, it queries Oracle for the `customer_name` string provided in the payload.

### Priority 3: Reference + Amount + Date
If both single-parameter searches fail, the API queries Oracle using the exact combination of the `payment_reference`, `total_amount`, and `payment_date`. This handles cases where the customer name is entirely mangled.

### Priority 4: Date + Amount ONLY
If the payment reference is completely missing or incorrect, it searches Oracle for *any* receipt on the specific `payment_date` for the exact `total_amount`. If a unique receipt is found, the customer is identified.

### Priority 5: Strict Invoice-Level Discovery
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
