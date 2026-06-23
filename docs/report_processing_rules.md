# Oracle ERP Reconciliation Rules Engine

> **CONTEXT FOR LLMS:** This document defines the exact deterministic matching logic for the Oracle Reconciliation Engine. When analyzing or modifying this codebase, you MUST adhere strictly to the constraints, evaluation rules, and waterfall sequences defined below.

## 1. Core Architecture
The engine operates in two distinct phases:
- **Phase 1: Network Layer (Targeted Bulk-Fetch)** -> Executes Oracle BIP queries to discover the customer and download their ledger.
- **Phase 2 & 3: RAM Layer (In-Memory)** -> Executes deterministic rule cascades against the downloaded ledger. *Note: All heavy CPU-bound matching algorithms (Levenshtein, SciPy Hungarian bipartite mapping) are wrapped in `asyncio.to_thread()` to guarantee the FastAPI event loop remains 100% non-blocking and highly concurrent.*

## 2. Phase 1: Network Layer (Discovery Waterfall)
The engine queries Oracle in a strict sequence to discover `BILL_CUSTOMER_NAME`. It stops at the first successful priority level.
- **Priority 1**: `payment_reference` ONLY (Must yield EXACTLY 1 match).
- **Priority 1b**: Stripped `payment_reference` ONLY (Strips non-alphanumeric/leading zeros, must be >= 6 chars, yields EXACTLY 1 match).
- **Priority 2**: `customer_name` ONLY (Extracts exact string).
- **Priority 3**: `payment_reference` + `total_amount` + `payment_date` (Yields ALL unique customer names).
- **Priority 3b**: Stripped `payment_reference` + `total_amount` + `payment_date`.
- **Priority 4**: `invoice_number` + `invoice_date` + `invoice_amount` (Strict concurrent 3-way search. Payload invoices missing ANY of these 3 fields are completely ignored).

*Multi-Customer Testing Loop*: If multiple potential customers are discovered (e.g., via P3), the engine tests Phase 2 and Phase 3 against each customer's ledger sequentially. The first customer ledger that successfully matches the payload is securely locked in.

## 3. Strict Cross-Validation Gate Philosophy
The entire matching engine operates on a mathematically pure **Strict Cross-Validation Philosophy**. 

If a piece of data is present in the JSON payload, it MUST match the corresponding data in Oracle exactly. If any field mismatches, the Oracle candidate is rejected. If a field is omitted (`null` or `""`) in the JSON, it is skipped during validation.

## 4. Phase 2: Receipt Reconciliation Rules
Evaluates the payload against `UNAPPLIED/UNID` receipts first. If no match is found, repeats against `APPLIED` receipts.

Instead of complex fallback scenarios, every receipt candidate is passed through the Strict Cross-Validation Gate:
1. **`payment_reference`**: If provided, must strictly match or fuzzy match `RECEIPT_NUMBER`.
2. **`total_amount`**: If provided, must strictly match `RECEIPT_AMOUNT` (no variances allowed).
3. **`payment_date`**: If provided, must strictly match `RECEIPT_DATE` (no variances allowed).
4. **`customer_name`**: If provided, must bidirectionally substring match `BILL_CUSTOMER_NAME` (min 10 chars).

If multiple receipts pass the gate, they are deduplicated by `RECEIPT_NUMBER`. If exactly 1 unique receipt remains, it is matched. If >1 remains, it is considered ambiguous and fails.

## 5. Phase 3: Invoice Reconciliation Rules
Evaluates the payload against `OPEN` invoices first (including Credit Memos). If any payload invoice remains unmatched, repeats against `CLOSED` invoices.

Instead of cascaded linear rules, every invoice candidate is passed through the Strict Cross-Validation Gate via the SciPy Hungarian Bipartite Assignment algorithm (`match_invoices_bipartite`) or via direct customer matching (`match_invoice_by_customer`):
1. **`invoice_number`**: If provided, must strictly match `TRANSACTION_NUMBER`.
2. **`invoice_date`**: If provided, must strictly match `TRANSACTION_DATE` (no variances allowed).
3. **`invoice_amount`**: If provided, must strictly match `AMOUNT_DUE_REMAINING` (no absolute value fuzzy matching for credit memos; must be exact mathematical equivalency).

Any candidate that fails validation is assigned an infinite cost in the distance matrix.

## 6. Output Contract
- **Success**: `receipt` populated, `invoices` array populated.
- **Partial**: One populated, the other empty. (e.g., `receipt` has data, `invoices` = `[]`).
- **Failure**: `receipt` = `{"matched_in_oracle": false}`, `invoices` = `[]`.
