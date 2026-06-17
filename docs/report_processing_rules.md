# Oracle ERP Data Reconciliation Rules Engine

## 1. System Overview

This document outlines the deterministic matching logic used by the Reconciliation Engine to map incoming JSON payload data against Oracle ERP Cloud records. The engine is designed to intelligently resolve **Receipts** and **Invoices** through a sequential cascade of matching rules, ensuring maximum reconciliation rates even when payload data is partially incomplete.

---

## 2. Global Execution Protocols

1. **Status Prioritization:** 
   The system mandates a two-phase evaluation process. It strictly prioritizes **Unapplied Receipts** and **Open Invoices** (Phase 1). If and only if Phase 1 yields exactly 0 matches, the system proceeds to evaluate against **Applied Receipts** and **Closed Invoices** (Phase 2).
2. **Sequential Rule Cascades:**
   Matching rules are evaluated in sequential order (e.g., Rule 1, then Rule 2).
   - If a rule yields **exactly 1 match**, execution immediately halts. The system returns the matched record alongside the corresponding `"match_rule"` identifier.
   - If a rule yields **0 matches** or **multiple ambiguous matches**, the system proceeds to the next rule in the sequence.
3. **Unmatched Resolution:** 
   If all cascading rules are exhausted without yielding a single deterministic match, the system terminates the process for that record, returning `null` alongside a `"No_Match_Reason"` in the response metadata.

---

## 3. Receipt Reconciliation Rules

This protocol governs the matching of incoming payload data against Oracle Standard Receipts.

### Field Mapping
The following table demonstrates the explicit mapping between the incoming payload and the Oracle database columns:

| Payload Field | Oracle Database Column |
| :--- | :--- |
| `payment_reference` | `RECEIPT_NUMBER` |
| `total_amount` | `RECEIPT_AMOUNT` |
| `payment_date` | `RECEIPT_DATE` |
| `customer_name` | `BILL_CUSTOMER_NAME` |

**Output Extraction Fields:**
Upon a successful match, the system automatically extracts and appends the following Oracle columns to the final API response:
* `fusion_receipt_number` *(extracted from RECEIPT_NUMBER)*
* `fusion_receipt_date` *(extracted from RECEIPT_DATE)*
* `fusion_customer_name` *(extracted from BILL_CUSTOMER_NAME)*
* `fusion_customer_number` *(extracted from BILL_CUSTOMER_NUMBER)*
* `fusion_currency` *(extracted from CURRENCY)*
* `fusion_receipt_status_code` *(extracted from RECEIPT_STATUS_CODE)*
* `fusion_applied_amount` *(extracted from APPLIED_AMOUNT)*

### Execution Cascade
The engine dynamically selects its execution path based on the presence of the `payment_reference` field.

#### Scenario A: `payment_reference` is Provided
The system executes the following rules sequentially:
1. **Rule A1:** Match `RECEIPT_NUMBER` (Bidirectional Substring) **AND** `RECEIPT_AMOUNT`. *(If `customer_name` is present in the payload, it must also match)*.
2. **Rule A2:** Match `RECEIPT_NUMBER` (Bidirectional Substring). *(If `customer_name` is present in the payload, it must also match)*.
3. **Rule A3:** Match `RECEIPT_NUMBER` (Bidirectional Substring) **AND** `RECEIPT_AMOUNT` **AND** `RECEIPT_DATE`. *(If `customer_name` is present in the payload, it must also match)*.
4. **Rule A4:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_AMOUNT`. *(This rule is bypassed if the payload lacks either field)*.
5. **Rule A5:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_DATE`. *(This rule is bypassed if the payload lacks either field)*.

#### Scenario B: `payment_reference` is Null or Missing
The system executes the following rules sequentially:
1. **Rule B1:** Match `RECEIPT_AMOUNT` **AND** `RECEIPT_DATE`. *(If `customer_name` is present in the payload, it must also match)*.
2. **Rule B2:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_AMOUNT`. *(This rule is bypassed if the payload lacks either field)*.
3. **Rule B3:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_DATE`. *(This rule is bypassed if the payload lacks either field)*.

---

## 4. Invoice Reconciliation Rules

This protocol governs the matching of invoice arrays within the payload against Oracle Receivables Invoices.

### Field Mapping
The following table demonstrates the explicit mapping between the incoming payload and the Oracle database columns:

| Payload Field | Oracle Database Column |
| :--- | :--- |
| `invoice_number` | `TRANSACTION_NUMBER` |
| `invoice_date` | `TRANSACTION_DATE` |
| `invoice_amount` | `TOTAL_AMOUNTS` |
| `customer_invoice_number` | `DOCUMENT_NUMBER` |
| `customer_name` | `BILL_CUSTOMER_NAME` |

> [!IMPORTANT]
> **Strict Amount Constraint:** For invoice reconciliation, every rule below implicitly requires `TOTAL_AMOUNTS` to equal the payload's `invoice_amount`. If `invoice_amount` is missing or null in the payload, this strict constraint is bypassed.

### Execution Cascade
The system executes the following rules sequentially for each invoice item:
1. **Rule 1a:** Match `TRANSACTION_NUMBER` (Exact) **AND** `TRANSACTION_DATE`.
2. **Rule 1b:** Match `TRANSACTION_NUMBER` (Exact).
3. **Rule 2:** Match `DOCUMENT_NUMBER` **AND** `TRANSACTION_DATE`. *(This rule is bypassed if the payload lacks `customer_invoice_number`)*.
4. **Rule 3:** Match `TRANSACTION_NUMBER` by Prefix (verifying if the Oracle number begins with the payload number) **AND** `TRANSACTION_DATE`.
5. **Rule 4:** Match `BILL_CUSTOMER_NAME` **AND** `TRANSACTION_DATE`. *(This rule is bypassed if the payload lacks `customer_name`)*.
