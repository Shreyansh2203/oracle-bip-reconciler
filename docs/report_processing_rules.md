# Customer Identification Logic

## Objective

Identify the **Customer Name**. Once the Customer Name is found, use it to retrieve the required records.

If the Customer Name cannot be identified from the **Receipt Details Report**, attempt to identify it using the **Invoice Details Report**. If the Customer Name still cannot be determined, return **null**.

## Search Priority

### 1. Customer Name Available

* Use Customer Name directly to find the records.

### 2. Payment Reference Available

* Use Payment Reference to identify the Customer Name.

### 3. Invoice Details Available

Use the following sequence until a unique customer is identified:

1. Invoice Number
2. Invoice Number + Invoice Amount
3. Invoice Number + Invoice Amount + Invoice Date

## Special Case

If both Customer Name and Payment Reference are null in the JSON, use the Invoice Details sequence above.

## Available Search Fields

### Receipt Details Report

* Customer Name
* Payment Reference
* Payment Date
* Total Amount

### Invoice Details Report

* Invoice Number
* Invoice Date
* Invoice Amount

## Final Fallback

1. Attempt to identify the Customer Name using the Receipt Details Report.
2. If not found, attempt to identify the Customer Name using the Invoice Details Report.
3. If the Customer Name cannot be identified from either report, return **null**.