# Oracle ERP Data Reconciliation Rules Engine

## System Overview
This document defines the deterministic rules for matching incoming JSON payloads against Oracle ERP Cloud records. The system attempts to match **Receipts** and **Invoices**. It is designed to be highly flexible, eagerly attempting matches even if payload data is partially missing.

## 1. Global Processing Rules
- **Status Filtering:** 
  - The system ALWAYS evaluates rules against "Unapplied Receipts" and "Open Invoices" first (Phase 1).
  - If and ONLY if Phase 1 yields exactly `0` matches, the system evaluates against "Applied Receipts" and "Closed Invoices" (Phase 2).
- **Match Termination:**
  - The system evaluates rules in sequential order (e.g., Step 1, then Step 2).
  - If a rule yields exactly `1` match, execution immediately HALTS and returns that match.
  - If a rule yields `0` matches, or `> 1` matches, execution proceeds to the next rule in the sequence.
  - If all rules are exhausted without finding exactly 1 match, the system returns `null` (No Match).
- **Data Sanitization:**
  - Amounts: Strip commas before comparison. Missing amounts (`null` or invalid strings) bypass amount-matching constraints.
  - Dates: Normalize to YYYY-MM-DD UTC before comparison.
  - Substring Matching: "Bidirectional Substring" means `String A contains String B` OR `String B contains String A` (case-insensitive).

## 2. Receipt Reconciliation Logic
Matches the payload against Oracle Standard Receipts.

### Field Mapping
* `payment_reference` (Payload) <--> `RECEIPT_NUMBER` (Oracle)
* `total_amount` (Payload) <--> `RECEIPT_AMOUNT` (Oracle)
* `payment_date` (Payload) <--> `RECEIPT_DATE` (Oracle)
* `customer_name` (Payload) <--> `BILL_CUSTOMER_NAME` (Oracle)

### Execution Cascade
Evaluate which scenario applies based on the presence of `payment_reference`:

#### Scenario A: IF `payment_reference` IS NOT NULL
Execute these rules sequentially:
1. **Rule A1**: Match `RECEIPT_NUMBER` (Bidirectional Substring) AND `RECEIPT_AMOUNT`. *(If `customer_name` is present in payload, also match `BILL_CUSTOMER_NAME`)*.
2. **Rule A2**: Match `RECEIPT_NUMBER` (Bidirectional Substring). *(If `customer_name` is present in payload, also match `BILL_CUSTOMER_NAME`)*.
3. **Rule A3**: Match `RECEIPT_NUMBER` (Bidirectional Substring) AND `RECEIPT_AMOUNT` AND `RECEIPT_DATE`. *(If `customer_name` is present in payload, also match `BILL_CUSTOMER_NAME`)*.
4. **Rule A4**: Match `BILL_CUSTOMER_NAME` AND `RECEIPT_AMOUNT`. *(Skips if payload is missing `customer_name` or `total_amount`)*.
5. **Rule A5**: Match `BILL_CUSTOMER_NAME` AND `RECEIPT_DATE`. *(Skips if payload is missing `customer_name` or `payment_date`)*.

#### Scenario B: IF `payment_reference` IS NULL
Execute these rules sequentially:
1. **Rule B1**: Match `RECEIPT_AMOUNT` AND `RECEIPT_DATE`. *(If `customer_name` is present in payload, also match `BILL_CUSTOMER_NAME`)*.
2. **Rule B2**: Match `BILL_CUSTOMER_NAME` AND `RECEIPT_AMOUNT`. *(Skips if payload is missing `customer_name` or `total_amount`)*.
3. **Rule B3**: Match `BILL_CUSTOMER_NAME` AND `RECEIPT_DATE`. *(Skips if payload is missing `customer_name` or `payment_date`)*.

## 3. Invoice Reconciliation Logic
Matches individual invoice items from the payload against Oracle Receivables Invoices.

### Field Mapping
* `invoice_number` (Payload) <--> `TRANSACTION_NUMBER` (Oracle)
* `invoice_date` (Payload) <--> `TRANSACTION_DATE` (Oracle)
* `invoice_amount` (Payload) <--> `TOTAL_AMOUNTS` (Oracle)
* `customer_invoice_number` (Payload) <--> `DOCUMENT_NUMBER` (Oracle)
* `customer_name` (Payload) <--> `BILL_CUSTOMER_NAME` (Oracle)

### Global Invoice Constraint
- EVERY invoice rule below strictly requires `TOTAL_AMOUNTS` == `invoice_amount`.
- If `invoice_amount` is `null` in the payload, this constraint is ignored.

### Execution Cascade
Execute these rules sequentially for each invoice in the payload:
1. **Rule 1a**: Match `TRANSACTION_NUMBER` (Exact) AND `TRANSACTION_DATE`.
2. **Rule 1b**: Match `TRANSACTION_NUMBER` (Exact).
3. **Rule 2**: Match `DOCUMENT_NUMBER` AND `TRANSACTION_DATE`. *(Skips if payload is missing `customer_invoice_number`)*.
4. **Rule 3**: Match `TRANSACTION_NUMBER` (Prefix: Oracle number starts with Payload number) AND `TRANSACTION_DATE`.
5. **Rule 4**: Match `BILL_CUSTOMER_NAME` AND `TRANSACTION_DATE`. *(Skips if payload is missing `customer_name`)*.

## 4. Unmatched Fallback
If the execution cascades complete without finding exactly 1 match:
* Halt execution for that specific record.
* Return `matched_in_oracle: false`.
* Set error message: `"No single match found after cascading rules"`.
