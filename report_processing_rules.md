# Data Reconciliation Rules & Mapping

This document defines the strict, declarative rules engine for reconciling incoming Payload data against Oracle ERP Reports. 

## 1. Universal Execution Principles
1. **Two-Phase Status Priority:**
   - **Phase 1:** Evaluate all rules against **Unapplied Receipts / Open Invoices** candidates only.
   - **Phase 2:** If Phase 1 yields zero matches, evaluate all rules against **Applied Receipts / Closed Invoices** candidates.
2. **Cascading Evaluation:** Rules must be evaluated sequentially in numerical order (e.g., A1 -> A2).
3. **Termination Condition:** The moment a rule evaluates to **exactly 1 match**, halt execution and return that match. If a rule yields >1 matches, proceed to the next rule.
4. **Strict Amount Matching:** Amount fields must match exactly. Floating-point rounding or fuzzy matching is prohibited.
5. **Data Normalization Requirements:** 
   - **Dates:** Normalize timezone-aware ISO-8601 timestamps to the calendar day (UTC) prior to comparison.
   - **Amounts:** Strip thousand-separators (e.g., `,`) before numerical evaluation.

---

## 2. Receipt Reconciliation (Standard Receipts)

### Data Mappings
| Payload Entity | Oracle Report Column | Oracle API Field |
| :--- | :--- | :--- |
| `payment_reference` | `RECEIPT_NUMBER` | `ReceiptNumber` |
| `total_amount` | `RECEIPT_AMOUNT` | `Amount` |
| `payment_date` | `RECEIPT_DATE` | `ReceiptDate` |
| `customer_name` | `BILL_CUSTOMER_NAME` | `CustomerName` |

### Scenario A: Payment Reference is Present
**Precondition:** Payload `payment_reference` is not null/empty.

* **Rule A1 (Full Strict Match):** 
  - **Match:** `RECEIPT_NUMBER` AND `RECEIPT_AMOUNT` AND `RECEIPT_DATE`
  - **Conditional Match:** AND `BILL_CUSTOMER_NAME` (if `customer_name` exists in Payload)

* **Rule A2 (Number & Amount):** 
  - **Match:** `RECEIPT_NUMBER` AND `RECEIPT_AMOUNT`
  - **Conditional Match:** AND `BILL_CUSTOMER_NAME` (if `customer_name` exists in Payload)

* **Rule A3 (Number Only):** 
  - **Match:** `RECEIPT_NUMBER`
  - **Conditional Match:** AND `BILL_CUSTOMER_NAME` (if `customer_name` exists in Payload)

* **Rule A4 (Amount & Date Fallback):** 
  - **Match:** `BILL_CUSTOMER_NAME` AND `RECEIPT_AMOUNT` AND `RECEIPT_DATE`
  - **Precondition for A4:** Payload MUST contain `customer_name`.

### Scenario B: Payment Reference is Absent
**Precondition:** Payload `payment_reference` is null/empty.

* **Rule B1 (Amount & Date):** 
  - **Match:** `RECEIPT_AMOUNT` AND `RECEIPT_DATE`
  - **Conditional Match:** AND `BILL_CUSTOMER_NAME` (if `customer_name` exists in Payload)

---

## 3. Invoice Reconciliation (Receivables Invoices)

### Data Mappings
| Payload Entity | Oracle Report Column | Oracle API Field |
| :--- | :--- | :--- |
| `invoice_number` | `TRANSACTION_NUMBER` | `TransactionNumber` |
| `invoice_date` | `TRANSACTION_DATE` | `TransactionDate` |
| `invoice_amount` | `TOTAL_AMOUNTS` | `EnteredAmount` |
| `customer_invoice_number`| `DOCUMENT_NUMBER` | `DocumentNumber` |
| `customer_name` | `BILL_CUSTOMER_NAME` | `BillToCustomerName` |

### Execution Logic
**Implicit Constraint:** EVERY rule below strictly requires `TOTAL_AMOUNTS` == `invoice_amount`. If amounts differ, the candidate is instantly rejected.

* **Rule 1a (Number + Date):** 
  - **Match:** `TRANSACTION_NUMBER` AND `TRANSACTION_DATE`

* **Rule 1b (Exact Number):** 
  - **Match:** `TRANSACTION_NUMBER`

* **Rule 2 (Document Match):** 
  - **Match:** `DOCUMENT_NUMBER` AND `TRANSACTION_DATE`
  - **Precondition for Rule 2:** Payload MUST contain `customer_invoice_number`.

* **Rule 3 (Prefix Match):** 
  - **Match:** `TRANSACTION_NUMBER` starts with Payload `invoice_number` AND `TRANSACTION_DATE`

* **Rule 4 (Customer Fallback):** 
  - **Match:** `BILL_CUSTOMER_NAME` AND `TRANSACTION_DATE`
  - **Precondition for Rule 4:** Payload MUST contain `customer_name`.

---

## 4. Fallback Failure

**Trigger:** All rules within the applicable scenario fail to yield exactly 1 match.
**Action:** The system MUST return the following structured JSON-like response:
- `error_code`: `NO_SINGLE_MATCH`
- `message`: `"No single match found after cascading rules"` (Contextual identifiers like invoice/receipt numbers may be appended for debugging).
