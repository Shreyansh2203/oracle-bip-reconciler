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

## 3. Best-Fit Matching Philosophy (Oracle as Source of Truth)
The matching engine operates on the philosophy that **Oracle Fusion is the ultimate source of truth**. JSON payloads extracted via OCR or AI may contain typos, missing decimals, or date shifts. 

Therefore, once the engine securely discovers the customer's identity (Phase 1), it uses **Best-Fit Scoring and Distance Metrics** to map the flawed JSON data to the true Oracle ledger, gracefully overriding JSON errors.

## 4. Phase 2: Receipt Reconciliation Rules
Evaluates the payload against `UNAPPLIED/UNID` receipts first. If no match is found, repeats against `APPLIED` receipts.

Receipt candidates are evaluated using a **0-100 point Scoring System**:
1. **`payment_reference`**: If it strictly or fuzzy matches `RECEIPT_NUMBER`, it awards **+50 points** (Primary Identifier).
2. **`total_amount`**: If exactly matching, awards **+30 points**. If within standard bank variances (1% or $25.00), awards **+15 points**.
3. **`payment_date`**: If exactly matching, awards **+20 points**. If within ACH delay variance (±3 days), awards **+10 points**.

To be accepted, a receipt must achieve a **minimum of 50 points**. This guarantees that the engine either perfectly matched the unique reference, or perfectly matched BOTH the date and amount. The highest scoring receipt is matched.

## 5. Phase 3: Invoice Reconciliation Rules
Evaluates the payload against `OPEN` invoices first. If any payload invoice remains unmatched, repeats against `CLOSED` invoices.

Invoices are matched using the **SciPy Hungarian Bipartite Assignment Algorithm** (`match_invoices_bipartite`). Instead of strict boolean rejection, the engine calculates a **Distance Cost Metric** for every possible pair:
1. **`invoice_number`**: An exact substring match adds 0 cost. A non-exact substring match adds 50. A complete mismatch adds a massive 10000 penalty.
2. **`invoice_date`**: Every day of deviation between the JSON and Oracle adds 10 to the cost. Unparseable dates add 500.
3. **`invoice_amount`**: The absolute difference in dollar amount is added to the cost (e.g., a $5.00 typo adds 5.0 to the cost). Perfect matches receive a -100 bonus reward.

The Hungarian Algorithm mathematically finds the lowest total cost assignment between all JSON invoices and Oracle invoices. Assignments with a cost `>= 5000` are rejected to prevent wild hallucinations.

## 6. Output Contract
- **Success**: `receipt` populated, `invoices` array populated.
- **Partial**: One populated, the other empty. (e.g., `receipt` has data, `invoices` = `[]`).
- **Failure**: `receipt` = `{"matched_in_oracle": false}`, `invoices` = `[]`.
