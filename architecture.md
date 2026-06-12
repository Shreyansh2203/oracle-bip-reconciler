# Oracle Reconciliation API Architecture

## Overview
This service provides an enterprise-grade reconciliation engine that matches incoming user payloads against Oracle ERP Cloud. Due to extreme Oracle REST API throttling (limit 1,000 items, low TPS), the application employs a **Hybrid Matching Architecture**: 
1. **Bulk Pre-fetching** via Oracle BI Publisher (XML payload chunking)
2. **Concurrent Fallback** via Oracle REST API

## Hybrid Pipeline
1. **Receipt Data Matching**: The system attempts to match the payload's payment reference/amount to an Oracle `standardReceipts` object.
2. **BIP Bulk Match (Phase 1)**: The system extracts all unique invoice numbers from the payload and issues bulk `xmlpserver` SOAP queries to Oracle BI Publisher in chunks of 500. This efficiently maps up to 95% of invoices in a few seconds.
3. **Validation Filter**: The returned BIP matches are strictly validated against the expected `invoice_amount` and `invoice_date`. False-positives are discarded.
4. **REST Fallback (Phase 2)**: Any invoice that BIP failed to find (or that failed validation) is grouped. The system issues concurrent, deduplicated REST API calls to `receivablesInvoices` and `receivablesCreditMemos`.

## Cascading Rules

When fetching data from REST, the engine does not blindly accept matches. It applies a series of cascading exact-match rules to disambiguate collisions.

### Receipt Cascading (Two-Phase: Unapplied → Applied)
*   **A1**: Exact Match: `ReceiptNumber`, `Amount`, `CustomerName`
*   **A2**: Exact Match: `ReceiptNumber`, `CustomerName`
*   **A3**: Exact Match: `ReceiptNumber`, `Amount`, `ReceiptDate`, `CustomerName`
*   **A4**: Exact Match: `CustomerName`, `Amount`
*   **A5**: Exact Match: `CustomerName`, `ReceiptDate`
*   *(B-rules mirror A-rules but omit ReceiptNumber requirements if missing)*

### Invoice Cascading (Two-Phase: Open → Closed)
*   **1a**: Exact Match: `TransactionNumber`
*   **1b**: Exact Match: `TransactionNumber`, `TransactionDate`
*   **2**: Exact Match: `DocumentNumber`, `TransactionDate`
*   **3**: Exact Match: `BillToCustomerName`, `TransactionDate`, `EnteredAmount`

*(Note: The system attempts to find a match in OPEN invoices first. If no rules match, it runs the same ruleset against CLOSED invoices).*
