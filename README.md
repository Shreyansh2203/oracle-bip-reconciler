# Oracle Reconciliation API

This is a high-performance FastAPI service designed to reconcile upstream invoice payloads with real-time data from an Oracle ERP system using Oracle BI Publisher (BIP) reports.

## Core Feature: Concurrent Search & Cross-Validation

Because upstream systems frequently send corrupted, misspelled, or missing customer names and payment references, this API utilizes a highly resilient architecture to identify the exact customer identity directly from Oracle before attempting any ledger reconciliation.

Instead of performing a strict sequential fallback where one search must fail before trying another, the engine runs concurrent searches across both the **Receipt Details Report** and the **Invoice Details Report**. 

### Level 1: Direct Identification
If the JSON provides a `customer_name`, the API uses it directly to find the records.

### Level 2 & 3: Concurrent Searching
If the Customer Name is missing, the API simultaneously searches:
- **Receipt Details**: Using the `payment_reference` (attempting exact, then stripped matches).
- **Invoice Details**: Using a strict sequence to isolate the customer mathematically:
  1. `invoice_number`
  2. `invoice_number` + `invoice_amount`
  3. `invoice_number` + `invoice_amount` + `invoice_date`
  4. `invoice_amount` + `invoice_date` (if Invoice Number is completely null)

### Handling Ambiguous Results
By running these searches in parallel, the engine is able to triangulate and cross-validate the correct customer:
- If both searches perfectly align on exactly one customer, the match is confirmed.
- If the search returns multiple matching records (a tie/clash), the engine **cross-references ALL available JSON fields** (e.g. searching the Receipts report using the payload's `total_amount` and `payment_date`).
- It will only confirm a match when the data is entirely unambiguous and aligned. If still unresolved after cross-validation, it returns `null`.

## Asynchronous Architecture
This engine is built on **FastAPI** and is 100% asynchronous. All heavy CPU-bound mathematical operations (e.g., Levenshtein distance calculations, SciPy's Hungarian algorithm for bipartite mapping) are automatically offloaded to a background thread pool via `asyncio.to_thread()`. This guarantees that the main event loop never blocks, allowing the server to handle thousands of concurrent reconciliation payloads simultaneously without freezing.

## Setup and Running

1. `uv sync`
2. `uv run task start` (or `make dev`)

The API will be available at `http://127.0.0.1:8000/`.
