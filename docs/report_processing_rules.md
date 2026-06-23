# Oracle ERP Reconciliation Rules Engine

> **CONTEXT FOR LLMS:** This document defines the exact deterministic matching logic for the Oracle Reconciliation Engine. When analyzing or modifying this codebase, you MUST adhere strictly to the constraints, evaluation rules, and waterfall sequences defined below.

## 1. Core Architecture
The engine operates in two distinct phases:
- **Phase 1: Network Layer (Targeted Bulk-Fetch)** -> Executes Oracle BIP queries to discover the customer and download their ledger.
- **Phase 2 & 3: RAM Layer (In-Memory)** -> Executes deterministic rule cascades against the downloaded ledger.

## 2. Phase 1: Network Layer (Discovery Waterfall)
The engine queries Oracle in a strict sequence to discover `BILL_CUSTOMER_NAME`. It stops at the first successful priority level.
- **Priority 1**: `payment_reference` ONLY (Must yield EXACTLY 1 match).
- **Priority 2**: `customer_name` ONLY (Extracts exact string).
- **Priority 3**: `payment_reference` + `total_amount` + `payment_date` (Yields ALL unique customer names).
- **Priority 4**: `payment_date` + `total_amount` ONLY (Must yield EXACTLY 1 match).
- **Priority 5**: `invoice_number` + `invoice_date` + `invoice_amount` (Strict concurrent 3-way search. Payload invoices missing ANY of these 3 fields are completely ignored).

*Multi-Customer Testing Loop*: If multiple potential customers are discovered (e.g., via P3), the engine tests Phase 2 and Phase 3 against each customer's ledger sequentially. The first customer ledger that successfully matches the payload is securely locked in.

## 3. Global Field Validation Constraints
These constraints apply strictly to all in-memory matching (Phases 2 & 3).
- `payment_reference` == `RECEIPT_NUMBER`: Exact case-insensitive match. Substring matching is prohibited.
- `total_amount` == `RECEIPT_AMOUNT`: Float match within ±0.01 tolerance (rounded to 2 decimal places to prevent float noise).
- `payment_date` == `RECEIPT_DATE`: Exact string match.
- `customer_name` == `BILL_CUSTOMER_NAME`: Bidirectional substring match with a minimum of 10 characters. (E.g., `A in B OR B in A`).

## 4. Phase 2: Receipt Reconciliation Rules
Evaluates the payload against `UNAPPLIED/UNID` receipts first. If no match is found, repeats against `APPLIED` receipts.
*Rule Cascade Mechanic*: If a rule yields 0 matches or >1 matches, the engine advances to the next rule. If it yields exactly 1 match, it returns it. If the final applicable rule yields >1 match, it returns NULL to prevent misallocation.

### Scenario A: `payment_reference` provided in payload
*(If `customer_name` is provided, it must also pass the global substring constraint for every rule below).*
1. **Rule A1**: `RECEIPT_NUMBER` + `RECEIPT_AMOUNT`
2. **Rule A2**: `RECEIPT_NUMBER`
3. **Rule A3**: `RECEIPT_NUMBER` + `RECEIPT_AMOUNT` + `RECEIPT_DATE`
4. **Rule A4**: `RECEIPT_AMOUNT` + `BILL_CUSTOMER_NAME` (`customer_name` is mandatory for this fallback)

### Scenario B: `payment_reference` missing from payload
*(If `customer_name` is provided, it must also pass the global substring constraint for every rule below).*
1. **Rule B1**: `RECEIPT_AMOUNT` + `RECEIPT_DATE`
2. **Rule B2**: `RECEIPT_AMOUNT` + `BILL_CUSTOMER_NAME` (`customer_name` is mandatory)

## 5. Phase 3: Invoice Reconciliation Rules
Evaluates the payload against `OPEN` invoices first (including Credit Memos). If any payload invoice remains unmatched, repeats against `CLOSED` invoices.

### Sub-Phase 1: Exact Rules (Linear Cascade)
1. **Rule 1a**: `TRANSACTION_NUMBER` + `TRANSACTION_DATE`
2. **Rule 1b**: `TRANSACTION_NUMBER`
3. **Rule 2**: `DOCUMENT_NUMBER` + `TRANSACTION_DATE`
4. **Rule 3**: `TRANSACTION_NUMBER` Prefix Match (Oracle number starts with payload value) + `TRANSACTION_DATE`

### Sub-Phase 2: Relaxed Rules (Linear Cascade)
Runs only for payload invoices unmatched by Sub-Phase 1.
1. **Rule Cust+AmtDate**: `BILL_CUSTOMER_NAME` + `TOTAL_AMOUNTS` + `TRANSACTION_DATE`
2. **Rule Cust+Amt**: `BILL_CUSTOMER_NAME` + `TOTAL_AMOUNTS`

### Sub-Phase 3: Bipartite Optimization (Hungarian Algorithm)
For any invoices STILL unmatched, computes the globally optimal 1-to-1 assignment.
- **Constraint 1 (Amount)**: `TOTAL_AMOUNTS` must exactly equal payload amount (absolute value used for Credit Memos). Discrepancy = Infinite Cost (Block).
- **Constraint 2 (Date)**: Date difference > 1 day = Infinite Cost (Block).
- **Cost Minimization**: Computes optimal pairing using Levenshtein edit distance on Customer Name.

## 6. Output Contract
- **Success**: `receipt` populated, `invoices` array populated.
- **Partial**: One populated, the other empty. (e.g., `receipt` has data, `invoices` = `[]`).
- **Failure**: `receipt` = `{"matched_in_oracle": false}`, `invoices` = `[]`.
