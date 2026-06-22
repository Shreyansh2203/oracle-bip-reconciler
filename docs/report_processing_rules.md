# Oracle ERP Data Reconciliation Rules Engine

This document defines the deterministic matching logic used by the Reconciliation Engine to map incoming JSON payload data against Oracle ERP Cloud records.

The engine operates in two sequential phases:

1. **Network Phase** — Downloads the minimum necessary records from Oracle using a targeted bulk-fetch strategy.
2. **Memory Phase** — Matches the downloaded records against payload data in RAM using strict field comparisons and, as a last resort, bipartite optimization.

---

## Output Contract

The engine returns a standardized JSON object. Phases 2 and 3 operate independently against the same downloaded ledger. 
- **Success:** Populates the `receipt` object and `invoices` array.
- **Partial Match:** Populates whichever phase succeeded (e.g., `receipt` has data, `invoices` is an empty array `[]`).
- **Total Failure:** If neither phase finds a match (or if the Network Phase fails to find the customer), `receipt` returns `{"matched_in_oracle": false}` and `invoices` returns `[]`.

---

## Phase 1: Network Layer (Targeted Bulk-Fetch)

The engine discovers the true `BILL_CUSTOMER_NAME` using a strict, isolated discovery waterfall to prevent dirty payload data from poisoning database queries. Once a potential customer is identified, the engine dynamically pulls the full ledger and proceeds to in-memory evaluation.

### The 5-Tier Discovery Waterfall

The engine queries Oracle in the following sequence. It stops searching the moment one or more potential customers are found.

1. **Priority 1**: `payment_reference` ONLY. (Gate: Pick if exactly 1 match).
2. **Priority 2**: `customer_name` ONLY.
3. **Priority 3**: `payment_reference` + `total_amount` + `payment_date`. (Gate: If multiple records are returned, extract *all* unique customer names).
4. **Priority 4**: `payment_date` + `total_amount` ONLY. (Gate: Pick if exactly 1 match).
5. **Priority 5 (Invoice Fallback)**: Concurrent Strict 3-Way Search (`Invoice Number` + `Invoice Date` + `Invoice Amount`).

> [!CAUTION]
> **No blank or partial searches in Priority 5.** If the fallback reaches Priority 5, all three fields must be present for a payload invoice to qualify. Invoices missing any of the three are silently skipped.

### Multi-Customer Evaluation Loop

If the discovery waterfall returns a single customer name, the engine downloads that customer's ledger (Receipts and Invoices) and moves to Phase 2. 

If a query (such as Priority 3) returns ambiguous records belonging to *multiple different customers*, the engine extracts a list of all potential customers and executes a dynamic testing loop:
1. Pull the full ledger for Customer A.
2. Run Phase 2 (Receipt Match) and Phase 3 (Invoice Match).
3. If the invoice amounts or receipt parameters successfully match, the engine conclusively locks in Customer A.
4. If Customer A fails the rules, the engine moves to Customer B, pulling the next ledger until a match is confirmed.

If the loop finishes and no customer is successfully matched, the engine returns `null` and stops.

---

## Phase 2: Receipt Reconciliation (In-Memory)

With the customer ledger in memory, the engine attempts to match the payload against Oracle Standard Receipts.

**Evaluation order:** The engine evaluates all rules against `UNAPPLIED/UNID` receipts first. If no match is found, it repeats the entire cascade against `APPLIED` receipts.

---

### Field Validation Constraints

All field comparisons use the following hardened matching mechanics. These apply globally across all rules in Phase 2.

| Payload Field | Oracle Column | Matching Rule |
| :--- | :--- | :--- |
| `payment_reference` | `RECEIPT_NUMBER` | **Exact, case-insensitive.** Substring matching is prohibited. |
| `total_amount` | `RECEIPT_AMOUNT` | **Float match within ±0.01.** Both values are rounded to two decimal places before comparison to eliminate float representation noise (e.g., `100.00001` vs `100.00`). |
| `payment_date` | `RECEIPT_DATE` | **Exact string match.** |
| `customer_name` | `BILL_CUSTOMER_NAME` | **Bidirectional substring, minimum 10 characters.** Either the Oracle value contains the payload value as a substring, or the payload value contains the Oracle value as a substring. The 10-character minimum prevents short tokens like `"Inc."` or `"LLC"` from matching across unrelated customers. |

---

### Cascade Logic

The engine tries rules in order, advancing to the next rule if the current rule produces **zero matches or more than one match**. It stops as soon as exactly **one match** is found. If no rule yields exactly one match, Phase 2 returns no result.

> [!CAUTION]
> **Final rule with multiple matches:** If the last applicable rule (A4 or B2) still yields more than one match, the engine abandons the match entirely to prevent assigning money to the wrong receipt.

The execution path is determined by whether `payment_reference` is present in the payload.

#### Global constraint for Scenario A

If `customer_name` is present in the payload, **every rule in Scenario A requires it to match** `BILL_CUSTOMER_NAME`. This constraint is not repeated per-rule below.

---

#### Scenario A: `payment_reference` Is Present

| Rule | Fields Required | Notes |
| :--- | :--- | :--- |
| **A1** | `RECEIPT_NUMBER` + `RECEIPT_AMOUNT` | Normal case: ID and amount both verify. |
| **A2** | `RECEIPT_NUMBER` | Relaxed: tries ID alone in case the payload amount is wrong or rounded differently from Oracle's value. |
| **A3** | `RECEIPT_NUMBER` + `RECEIPT_AMOUNT` + `RECEIPT_DATE` | Tiebreaker: if A2 matches multiple receipts sharing the same ID, adding the date narrows to one. |
| **A4** | `RECEIPT_AMOUNT` + `BILL_CUSTOMER_NAME` | Last resort: used when the receipt ID is so corrupted that ID-based matching is abandoned entirely. `customer_name` is **required** here (not optional). |

> [!NOTE]
> **Why A3 comes after A2:** A1 verifies ID + amount; it fails when the amounts don't agree. A2 then relaxes the amount constraint, but can return multiple receipts if the same ID appears more than once in the ledger. A3 re-adds the date to disambiguate. This ordering moves from most likely (A1) through progressively targeted recovery strategies.

---

#### Scenario B: `payment_reference` Is Absent

If `customer_name` is present in the payload, it must match `BILL_CUSTOMER_NAME` in every rule below.

| Rule | Fields Required |
| :--- | :--- |
| **B1** | `RECEIPT_AMOUNT` + `RECEIPT_DATE` |
| **B2** | `RECEIPT_AMOUNT` + `BILL_CUSTOMER_NAME` |

> [!NOTE]
> **Removed legacy rules:** Rules that matched solely on Customer Name + Date (with no amount or ID check) were permanently removed to prevent false positives.

---

## Phase 3: Invoice Reconciliation (In-Memory)

Invoice matching runs in three sub-phases. Sub-phases 1 and 2 use linear rule cascades; Sub-phase 3 uses bipartite optimization for any remaining unmatched invoices.

**Evaluation order:** All three sub-phases are run first against `OPEN` invoices. If any payload invoice remains unmatched after Sub-phase 3, the entire three-sub-phase sequence repeats against `CLOSED` invoices.

---

### Sub-Phase 1: Exact Rules

These rules require precise data.

| Rule | Fields Required |
| :--- | :--- |
| **1a** | `TRANSACTION_NUMBER` (exact) + `TRANSACTION_DATE` |
| **1b** | `TRANSACTION_NUMBER` (exact) |
| **2** | `DOCUMENT_NUMBER` (exact) + `TRANSACTION_DATE` |
| **3** | `TRANSACTION_NUMBER` prefix match (Oracle number begins with payload value) + `TRANSACTION_DATE` |

---

### Sub-Phase 2: Relaxed Customer Rules

Runs only for invoices that Sub-phase 1 did not match. The `BILL_CUSTOMER_NAME` bidirectional substring rule (10-character minimum) from Phase 2 applies here as well.

| Rule | Fields Required |
| :--- | :--- |
| **Cust+AmtDate** | `BILL_CUSTOMER_NAME` + `TOTAL_AMOUNTS` + `TRANSACTION_DATE` |
| **Cust+Amt** | `BILL_CUSTOMER_NAME` + `TOTAL_AMOUNTS` |

---

### Sub-Phase 3: Hungarian Bipartite Matching

For any invoices still unmatched after Sub-phases 1 and 2, the engine abandons rule-checking and computes the globally optimal 1-to-1 assignment using the Hungarian algorithm.

**Step 1 — Build the cost matrix.** Every unmatched payload invoice is plotted against every unmatched Oracle invoice in a 2D matrix. Each cell represents the cost of pairing that combination.

**Step 2 — Score each cell** using the following criteria:

| Criterion | Rule |
| :--- | :--- |
| **Amount** | `TOTAL_AMOUNTS` must match the payload amount exactly. For Credit Memos, the absolute value is used. Any discrepancy blocks the cell entirely (sets cost to infinity). |
| **Customer Name string distance** | A `Levenshtein` edit distance is calculated between `BILL_CUSTOMER_NAME` and the payload `customer_name`. (The Invoice Numbers themselves are scored using strict heuristics, not Levenshtein). |
| **Date proximity** | If `TRANSACTION_DATE` and `invoice_date` differ by more than 1 day (86,400 seconds), the proximity score is invalidated for that cell. |

**Step 3 — Resolve.** The algorithm minimizes total global cost across all cells, producing the optimal 1-to-1 pairing. This correctly handles cases where both sides contain typos or minor date misalignments.

---

## Cross-Phase Behavior

The execution of Phases 2 and 3 guarantees that invoices and receipts are processed comprehensively without deadlocking:
1. **Sequencing:** Phase 3 (invoice matching) runs **after** Phase 2 (receipt matching) finishes evaluating the ledger.
2. **Dependency:** Phase 3 is **NOT gated** by Phase 2. Even if no matching receipt is found, invoice matching still fully executes against the downloaded ledger.
3. **No Cross-Pollination:** Invoices and Receipts are matched against entirely separate datasets (`bip_invoices` vs `bip_receipts`). They do not steal or block each other's data points.
