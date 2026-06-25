# Customer Identification Logic

## Overview
This logic provides a structured approach to identify customers using available data fields, with built-in fallback mechanisms for ambiguous or incomplete information.

Follow these steps in order to identify the customer. Move to the next step only if the current one fails or returns no results.

---

### Step 1 — Search by Payment Reference *(Receipt Details Report)*
Use the **Payment Reference** to locate the associated Customer Name.

| Query Parameter | Source Field |
|---|---|
| `P_RECEIPT_NUMBER` | Payment Reference |

---

### Step 2 — Search by Customer Name *(Receipt Details Report)*
Use the **Customer Name** directly as the search parameter.

| Query Parameter | Source Field |
|---|---|
| `P_CUSTOMER_NAME` | Customer Name |

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
For the final ledger reconciliation step, an invoice from the JSON payload maps successfully to an Oracle invoice record **only** when all three of the following details match:
1. **Invoice Number** — String exactly matches
2. **Invoice Date** — Compared after **date normalization** (see below)
3. **Invoice Amount** — Evaluated mathematically, e.g., `8000.0` matches `"8000"`

### Date Normalization
Dates from the JSON payload and Oracle are normalized to a canonical `YYYY-MM-DD` format before comparison. This handles industry-standard variations including:
- **Separators**: slashes, dashes, dots (`2026/10/05`, `2026-10-05`, `05.10.2026`)
- **Orderings**: `YYYY-MM-DD`, `DD-MM-YYYY`, `MM-DD-YYYY`
- **Month names**: `05-Jan-2026`, `January 5 2026`, `5 Jan 2026`
- **Compact**: `20261005`
- **Timestamps**: trailing time portions are stripped (`2026-10-05T00:00:00`)

If a date cannot be parsed by any known format, the system falls back to a raw string comparison.

If any of these three fields are null or misaligned, the specific invoice will fail to match, even if the parent Customer was successfully identified.