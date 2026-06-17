# 🧾 Oracle ERP Data Reconciliation Rules Engine

Welcome to the **Oracle Reconciliation Engine**! This document explains exactly how the system matches incoming JSON payload data against Oracle ERP Cloud records. 

We try to be as flexible as possible: even if the payload is missing a few fields, the system will still try to find a match using fallback rules. 

---

## 🌎 1. How It Works (The Basics)

1. **Prioritize Unapplied/Open:** 
   We *always* look at **Unapplied Receipts** and **Open Invoices** first (Phase 1). If we find absolutely nothing there, we move on to check **Applied Receipts** and **Closed Invoices** (Phase 2).
2. **One Rule at a Time (The Cascade):**
   We check our matching rules one by one, from top to bottom.
   - If a rule finds **exactly 1 match**, we yell "BINGO!", return the match, and stop searching.
   - If a rule finds **0 matches** or **multiple ambiguous matches**, we simply move down to the next rule.
3. **No Match Found:** 
   If we reach the very bottom of our rule list and still haven't found exactly 1 match, we return a `null` result and log a reason why.

---

## 🏦 2. Receipt Matching Rules

When we receive a receipt, we try to match it against Oracle Standard Receipts.

### 🔗 How We Map the Fields
Here is how your payload fields translate into Oracle columns:

| What you send (Payload) | What we look for in Oracle |
| :--- | :--- |
| `payment_reference` | `RECEIPT_NUMBER` |
| `total_amount` | `RECEIPT_AMOUNT` |
| `payment_date` | `RECEIPT_DATE` |
| `customer_name` | `BILL_CUSTOMER_NAME` |

**When we successfully find a match, we pull these columns from Oracle to send back to you:**
* `fusion_receipt_number` *(from RECEIPT_NUMBER)*
* `fusion_receipt_date` *(from RECEIPT_DATE)*
* `fusion_customer_name` *(from BILL_CUSTOMER_NAME)*
* `fusion_customer_number` *(from BILL_CUSTOMER_NUMBER)*
* `fusion_currency` *(from CURRENCY)*
* `fusion_receipt_status_code` *(from RECEIPT_STATUS_CODE)*
* `fusion_applied_amount` *(from APPLIED_AMOUNT)*

### 🔍 The Receipt Cascade
Depending on whether you send us a `payment_reference` or not, we follow one of two paths:

#### 👉 Scenario A: You Provided a `payment_reference`
We try these rules in order:
1. **Rule A1:** Match `RECEIPT_NUMBER` (Bidirectional Substring) **AND** `RECEIPT_AMOUNT`. *(If `customer_name` was provided, it must match too)*.
2. **Rule A2:** Match `RECEIPT_NUMBER` (Bidirectional Substring) only. *(If `customer_name` was provided, it must match too)*.
3. **Rule A3:** Match `RECEIPT_NUMBER` (Bidirectional Substring) **AND** `RECEIPT_AMOUNT` **AND** `RECEIPT_DATE`. *(If `customer_name` was provided, it must match too)*.
4. **Rule A4:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_AMOUNT`. *(We skip this if you didn't give us a customer or amount)*.
5. **Rule A5:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_DATE`. *(We skip this if you didn't give us a customer or date)*.

#### 👉 Scenario B: `payment_reference` is Missing
We try these rules in order:
1. **Rule B1:** Match `RECEIPT_AMOUNT` **AND** `RECEIPT_DATE`. *(If `customer_name` was provided, it must match too)*.
2. **Rule B2:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_AMOUNT`. *(We skip this if you didn't give us a customer or amount)*.
3. **Rule B3:** Match `BILL_CUSTOMER_NAME` **AND** `RECEIPT_DATE`. *(We skip this if you didn't give us a customer or date)*.

---

## 📜 3. Invoice Matching Rules

When we receive an invoice array, we evaluate them one by one against Oracle Receivables Invoices.

### 🔗 How We Map the Fields
Here is how your payload fields translate into Oracle columns:

| What you send (Payload) | What we look for in Oracle |
| :--- | :--- |
| `invoice_number` | `TRANSACTION_NUMBER` |
| `invoice_date` | `TRANSACTION_DATE` |
| `invoice_amount` | `TOTAL_AMOUNTS` |
| `customer_invoice_number` | `DOCUMENT_NUMBER` |
| `customer_name` | `BILL_CUSTOMER_NAME` |

> [!IMPORTANT]
> **The Amount Rule**: For invoices, **every single rule below** strictly requires `TOTAL_AMOUNTS` to equal your `invoice_amount`. However, if you don't send an `invoice_amount` at all, we kindly ignore this rule.

### 🔍 The Invoice Cascade
We try these rules in order for each invoice:
1. **Rule 1a:** Match the `TRANSACTION_NUMBER` exactly **AND** the `TRANSACTION_DATE`.
2. **Rule 1b:** Match the `TRANSACTION_NUMBER` exactly.
3. **Rule 2:** Match the `DOCUMENT_NUMBER` **AND** the `TRANSACTION_DATE`. *(We skip this if you didn't give us a `customer_invoice_number`)*.
4. **Rule 3:** Match `TRANSACTION_NUMBER` by Prefix (does the Oracle number start with your payload number?) **AND** the `TRANSACTION_DATE`.
5. **Rule 4:** Match the `BILL_CUSTOMER_NAME` **AND** the `TRANSACTION_DATE`. *(We skip this if you didn't give us a `customer_name`)*.
