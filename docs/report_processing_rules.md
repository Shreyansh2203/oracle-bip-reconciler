# Customer Identification Logic

## Overview
This logic provides a structured approach to identify customers using available data fields, with built-in fallback mechanisms for ambiguous or incomplete information.

## Primary Identification Methods (in order of priority)

### Level 1: Direct Identification
If Customer Name is available → Use it directly to retrieve records.

### Level 2: Reference-Based Identification
If Payment Reference is available → Use it to identify the associated Customer Name.

### Level 3: Invoice-Based Identification
If both Customer Name and Payment Reference are unavailable, use Invoice Details in this sequence:

| Step | Search Criteria | Action |
|------|----------------|--------|
| 3.1 | Invoice Number | Retrieve all matching records |
| 3.2 | Invoice Number + Amount | Narrow results by adding amount |
| 3.3 | Invoice Number + Amount + Date | Further refine using invoice date |
| 3.4 | Amount + Date (if Invoice Number is null) | Search using financial details only |

## Handling Ambiguous Results
When a search returns multiple matching records:
- Cross-reference ALL available JSON fields (customer name, payment reference, amounts, dates)
- Validate against Receipt and Invoice Details data
- Confirm a match only when data is unambiguous and aligned

If no definitive match is found after Level 3:
- Attempt the search in the alternative report (if searched Receipt Details, try Invoice Details, and vice versa)
- If still unresolved, return NULL

## Available Search Fields

| Report Type | Fields |
|-------------|--------|
| Receipt Details | Customer Name, Payment Reference, Payment Date, Total Amount |
| Invoice Details | Invoice Number, Invoice Date, Invoice Amount |

## Key Principles
✓ Use the most specific identifier available
✓ Validate results against all related data fields
✓ Fallback to alternative reports if primary search fails
✓ Only commit to a match when data is conclusive