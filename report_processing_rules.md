# Oracle Reconciliation Matching Rules

This document defines the strict, cascading logical rules for matching incoming JSON payloads to Oracle Cloud ERP records (Receipts and Invoices).

## Universal Principles
- **Cascading Execution:** Execute the rules in numerical order (e.g., A1, then A2, then A3).
- **Early Exit:** Stop execution immediately when any single rule yields **exactly one match** (`count == 1`). Return that match.
- **Strict Amount Matching:** Amounts must match via exact float equality. No rounding, no fuzzy matching, no tolerance (e.g., `Oracle.Amount == Payload.amount`).
- **Date Formatting:** Dates in Oracle are queried as `YYYY-MM-DD`. Payload dates must be parsed and converted to this format before matching.
- **Optional Attributes:** If a rule specifies `[+ Optional Field]`, it means: "If the field exists and is not null in the Payload, append `AND Oracle.Field == Payload.Field` to the query."

---

## RULE 1: Find the Receipt (StandardReceipts)

**Oracle Entity:** `standardReceipts`
**Payload Inputs:** `payment_reference`, `total_amount`, `payment_date`, `customer_name`
**Target Oracle Fields:** `ReceiptNumber`, `Amount`, `ReceiptDate`, `CustomerName`

### SCENARIO A: `payment_reference` is provided in Payload
Execute these steps in order. Stop if `matches == 1`.

* **Step A1:** Match strictly by Receipt Number and Amount.
  `ReceiptNumber == payload.payment_reference` AND `Amount == payload.total_amount` [+ Optional `CustomerName == payload.customer_name`]
* **Step A2:** Match by Receipt Number only.
  `ReceiptNumber == payload.payment_reference` [+ Optional `CustomerName == payload.customer_name`]
* **Step A3:** Match by Receipt Number, Amount, and Date.
  `ReceiptNumber == payload.payment_reference` AND `Amount == payload.total_amount` AND `ReceiptDate == payload.payment_date` [+ Optional `CustomerName == payload.customer_name`]
* **Step A4:** Abandon `payment_reference`. Match by Customer and Amount.
  `CustomerName == payload.customer_name` AND `Amount == payload.total_amount`
* **Step A5:** Last resort. Match by Customer and Date.
  `CustomerName == payload.customer_name` AND `ReceiptDate == payload.payment_date`

### SCENARIO B: `payment_reference` is missing or null
Execute these steps in order. Stop if `matches == 1`.

* **Step B1:** Match by Amount and Date.
  `Amount == payload.total_amount` AND `ReceiptDate == payload.payment_date` [+ Optional `CustomerName == payload.customer_name`]
* **Step B2:** Match by Customer and Amount.
  `CustomerName == payload.customer_name` AND `Amount == payload.total_amount`
* **Step B3:** Last resort. Match by Customer and Date.
  `CustomerName == payload.customer_name` AND `ReceiptDate == payload.payment_date`

---

## RULE 2: Find the Invoices (ReceivablesInvoices)

**Oracle Entity:** `receivablesInvoices`
**Payload Inputs (per invoice in list):** `invoice_number`, `invoice_date`, `invoice_amount`, `customer_invoice_number`
**Global Payload Inputs:** `customer_name`
**Target Oracle Fields:** `TrxNumber`, `TrxDate`, `InvoiceAmount`, `CustomerReference`, `BillToCustomerName`

For *each* invoice item in the payload, execute these steps in order. Stop if `matches == 1`.

* **Step 1a:** Exact Invoice Number match.
  `TrxNumber == payload.invoice_number`
* **Step 1b:** Exact Invoice Number and Date match.
  `TrxNumber == payload.invoice_number` AND `TrxDate == payload.invoice_date`
* **Step 2:** Match by Document Number (Customer Reference) and Date.
  `CustomerReference == payload.customer_invoice_number` AND `TrxDate == payload.invoice_date`
* **Step 3:** Substring match on Invoice Number and Date.
  `TrxNumber` contains `payload.invoice_number` (e.g., `LIKE '%invoice_number%'`) AND `TrxDate == payload.invoice_date`
* **Step 4:** Last resort. Match by Customer, Date, and Amount.
  `BillToCustomerName == payload.customer_name` AND `TrxDate == payload.invoice_date` AND `InvoiceAmount == payload.invoice_amount`

---

## Fallback Behavior
If all cascading steps for a scenario are exhausted and `matches != 1`, return a "No single match found after cascading rules" error for that specific entity.
