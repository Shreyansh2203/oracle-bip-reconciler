# High-Level Architecture Overview

To process batches of invoices efficiently and prevent Oracle API timeouts, the reconciliation engine operates in three distinct phases:

1. **Customer Discovery (Oracle API)**: The engine queries Oracle BI Publisher to figure out *who* the customer is using progressive searches (e.g. searching by Invoice Number, then Invoice Number + Amount). The engine short-circuits this phase immediately upon identifying the customer to save API calls.
2. **Ledger Fetch (Oracle API)**: Once the Customer Name is discovered (e.g. "New Horizon Foods"), the engine sends **one single query** to download that customer's entire ledger (all invoices and receipts) into local memory.
3. **In-Memory Matching (Python)**: The engine matches the JSON payload against the downloaded ledger entirely in memory. It uses a tiered safety logic (3-Way, 2-Way, 1-Way fallbacks) to maximize OCR error recovery without sending any further queries to Oracle.

---

# Customer Identification Logic (Discovery Phase)

## Overview
This logic provides a structured approach to identify customers using available data fields, with built-in fallback mechanisms for ambiguous or incomplete information.

Follow these steps in order to identify the customer. Move to the next step only if the current one fails or returns no results. This phase short-circuits as soon as a single unique customer is found.

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

## Receipt Mapping Logic
For the final ledger reconciliation step, the receipt is mapped to an Oracle receipt record using the following priority:
1. **Payment Reference**: If the JSON payload provides a `payment_reference`, it must be a substring match (case-insensitive) with the Oracle `RECEIPT_NUMBER`.
2. **Amount and Date Fallback**: If the `payment_reference` is missing or null, the receipt maps successfully if both the `total_amount` and `payment_date` match an Oracle receipt's `RECEIPT_AMOUNT` and `RECEIPT_DATE` respectively. The date is compared using the same date normalization logic as invoices.

**Receipt Backfill:**
Once successfully mapped, the payload's `payment_reference`, `payment_date`, `total_amount`, and `customer_name` are automatically backfilled using the truthful data from the Oracle report if they were originally missing or incorrect. Additional fields like `fusion_customer_number`, `fusion_currency`, and `fusion_receipt_status_code` are also populated.

---

## Invoice Matching Logic (Tiered Fallback)
For the final ledger reconciliation step, an invoice from the JSON payload attempts to map to an Oracle invoice record using a tiered fallback strategy. This handles OCR errors and missing fields by safely searching the customer's isolated ledger. 

The system tracks mapped invoices to prevent assigning the same Oracle invoice twice.

1. **3-Way Match (Highest Priority)**: Invoice Number, Date, and Amount all match.
2. **2-Way Match**: Two out of three fields match (Number + Date, Number + Amount, or Date + Amount). This is only accepted if exactly *one* unique invoice matches these two fields in the remaining ledger.
3. **1-Way Match (Lowest Priority)**: Only one field matches (Number, Date, or Amount). This is only accepted if exactly *one* unique invoice matches this field in the remaining ledger.

*Note: Invoice Number matching allows for partial substrings (e.g. truncated OCR numbers) if the string is at least 5 characters long. However, to prevent risky mappings, **1-Way Matches on the Invoice Number require an EXACT string match**. Substring matches are only accepted if accompanied by a Date or Amount match (2-way/3-way).*

**Invoice Backfill:**
Once successfully mapped, the payload's `invoice_number`, `invoice_date`, and `invoice_amount` are automatically corrected to reflect the exact data from the Oracle report, effectively repairing any OCR typos.

### Date Normalization
Dates from the JSON payload and Oracle are normalized to a canonical `YYYY-MM-DD` format before comparison. This handles industry-standard variations including:
- **Separators**: slashes, dashes, dots (`2026/10/05`, `2026-10-05`, `05.10.2026`)
- **Orderings**: `YYYY-MM-DD`, `DD-MM-YYYY`, `MM-DD-YYYY`
- **Month names**: `05-Jan-2026`, `January 5 2026`, `5 Jan 2026`
- **Compact**: `20261005`
- **Timestamps**: trailing time portions are stripped (`2026-10-05T00:00:00`)

If a date cannot be parsed by any known format, the system falls back to a raw string comparison.