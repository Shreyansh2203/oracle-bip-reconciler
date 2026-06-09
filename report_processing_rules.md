# Data Reconciliation Rules & Mapping

This document outlines the standard cascading rules used to reconcile incoming extracted data (Payload) with the source-of-truth Oracle ERP data (Reports). 

## 1. Universal Principles
1. **Cascading Priority:** Execute the rules in numerical order (A1, then A2, etc.). The moment any rule yields **exactly one match**, stop immediately and use that match.
2. **Exact Amounts:** Amount matching must be exact. No fuzzy matching or rounding is allowed.
3. **Optional Parameters:** If a field like `[+ Optional Customer]` is listed, it means: "If the customer name exists in the payload, add it to the search criteria to make it more accurate."

---

## 2. Receipt Reconciliation (Standard Receipts)

**Data Mappings:**
| Payload JSON Field | CSV Report Column | Oracle API Field |
| :--- | :--- | :--- |
| `payment_reference` | `RECEIPT_NUMBER` | `ReceiptNumber` |
| `total_amount` | `RECEIPT_AMOUNT` | `Amount` |
| `payment_date` | `RECEIPT_DATE` | `ReceiptDate` |
| `customer_name` | `BILL_CUSTOMER_NAME` | `CustomerName` |

### Scenario A: We have a Payment Reference (`RECEIPT_NUMBER`)
Try these steps in order. Stop as soon as you find exactly 1 match.

* **Rule A1:** Match strictly by `RECEIPT_NUMBER` and `RECEIPT_AMOUNT` `[+ Optional Customer]`
* **Rule A2:** Match by `RECEIPT_NUMBER` only `[+ Optional Customer]`
* **Rule A3:** Match by `RECEIPT_NUMBER`, `RECEIPT_AMOUNT`, and `RECEIPT_DATE` `[+ Optional Customer]`
* **Rule A4:** Abandon the receipt number. Match by Customer and `RECEIPT_AMOUNT`
* **Rule A5:** Last resort. Match by Customer and `RECEIPT_DATE`

### Scenario B: We DO NOT have a Payment Reference
Try these steps in order. Stop as soon as you find exactly 1 match.

* **Rule B1:** Match by `RECEIPT_AMOUNT` and `RECEIPT_DATE` `[+ Optional Customer]`
* **Rule B2:** Match by Customer and `RECEIPT_AMOUNT`
* **Rule B3:** Last resort. Match by Customer and `RECEIPT_DATE`

---

## 3. Invoice Reconciliation (Receivables Invoices)

**Data Mappings:**
| Payload JSON Field | CSV Report Column | Oracle API Field |
| :--- | :--- | :--- |
| `invoice_number` | `TRANSACTION_NUMBER` | `TrxNumber` |
| `invoice_date` | `TRANSACTION_DATE` | `TrxDate` |
| `invoice_amount` | `TOTAL_AMOUNTS` | `InvoiceAmount` |
| `customer_invoice_number`| `DOCUMENT_NUMBER` | `CustomerReference` |
| `customer_name` | `BILL_CUSTOMER_NAME` | `BillToCustomerName` |

For **each** invoice in the payload, try these steps in order. Stop as soon as you find exactly 1 match.

* **Rule 1a (Exact Number):** 
  Match strictly by `TRANSACTION_NUMBER`.
* **Rule 1b (Number + Date):** 
  Match strictly by `TRANSACTION_NUMBER` and `TRANSACTION_DATE`.
* **Rule 2 (Document Match):** 
  Match by the customer's `DOCUMENT_NUMBER` and `TRANSACTION_DATE`.
* **Rule 3 (Partial Number):** 
  Do a partial/substring search for the `TRANSACTION_NUMBER` alongside the `TRANSACTION_DATE`.
* **Rule 4 (Amount & Date Fallback):** 
  Match by Customer, `TRANSACTION_DATE`, and `TOTAL_AMOUNTS`.

---

## 4. Fallback Failure
If all rules in a sequence fail to yield a single, unique match, the system must flag an error: `"No single match found after cascading rules"`.
