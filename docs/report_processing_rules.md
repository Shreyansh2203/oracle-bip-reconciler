# Customer Identification Logic

## Overview
This logic provides a structured approach to identify customers using available data fields, with built-in fallback mechanisms for ambiguous or incomplete information.

Follow these steps in order to identify the customer. Move to the next step only if the current one fails or returns no results.

---

### Step 1 — Search by Customer Name *(Receipt Details Report)*
Use the **Customer Name** directly as the search parameter.

| Query Parameter | Source Field |
|---|---|
| `P_CUSTOMER_NAME` | Customer Name |

---

### Step 2 — Search by Payment Reference *(Receipt Details Report)*
Use the **Payment Reference** to locate the associated Customer Name.

| Query Parameter | Source Field |
|---|---|
| `P_RECEIPT_NUMBER` | Payment Reference |
| `P_CUSTOMER_NAME` | Customer Name |
| `P_RECEIPT_DATE` | Payment Date |
| `P_RECEIPT_AMOUNT` | Total Amount |

---

### Step 3 — Search by Invoice Details *(Invoice Details Report)*
Use invoice parameters to identify the customer. Apply them **progressively** based on results:

| Priority | Parameters Used | When to Apply |
|---|---|---|
| 1st | `P_INVOICE_NUM` | Always start here — Invoice Number is a unique identifier |
| 2nd | `P_INVOICE_NUM` + `P_INVOICE_AMOUNT` | If multiple customers are returned |
| 3rd | `P_INVOICE_NUM` + `P_INVOICE_AMOUNT` + `P_INVOICE_DATE` | If still multiple customers are returned |

| Query Parameter | Source Field |
|---|---|
| `P_CUSTOMER_NAME` | Customer Name |
| `P_INVOICE_NUM` | Invoice Number |
| `P_INVOICE_DATE` | Invoice Date |
| `P_INVOICE_AMOUNT` | Invoice Amount |

---

### ⚠️ Special Case — Both Customer Name and Payment Reference are Null
Skip Steps 1 and 2 entirely. **Go directly to Step 3** and use the Invoice Details sequence.

---

> **Note:** Customer Name is a common field available in **both** the Receipt Details Report and the Invoice Details Report and can be retrieved from either source.

---

## Invoice Matching Logic
For the final ledger reconciliation step, an invoice from the JSON payload maps successfully to an Oracle invoice record **only** when all three of the following details match perfectly:
1. **Invoice Number**
2. **Invoice Date**
3. **Invoice Amount**

If any of these three fields are null or misaligned, the specific invoice will fail to match, even if the parent Customer was successfully identified.