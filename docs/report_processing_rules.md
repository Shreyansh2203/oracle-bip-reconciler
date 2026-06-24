# Oracle ERP Reconciliation Rules Engine

> **CONTEXT FOR LLMS:** This document defines the exact deterministic matching logic for the Oracle Reconciliation Engine. When analyzing or modifying this codebase, you MUST adhere strictly to the constraints, evaluation rules, and waterfall sequences defined below.

## 1. Core Architecture
The engine operates in two distinct phases:
- **Phase 1: Network Layer (Customer Discovery)** -> Executes Oracle BIP queries to discover the customer and download their ledger.
- **Phase 2 & 3: RAM Layer (In-Memory)** -> Executes deterministic Best-Fit rule cascades against the downloaded ledger. *Note: All heavy CPU-bound matching algorithms are wrapped in `asyncio.to_thread()` to guarantee the FastAPI event loop remains 100% non-blocking and highly concurrent.*

## 2. Phase 1: Network Layer (Customer Discovery)
The engine queries Oracle in a strict, simplified 3-rule sequence to discover `BILL_CUSTOMER_NAME`. 

- **Rule 1: Customer Name Priority**
  If `customer_name` is provided in the JSON, the engine immediately proceeds with it. No preliminary discovery search is performed.
- **Rule 2: Payment Reference Fallback**
  If `customer_name` is missing, the engine queries the Oracle Receipt Report using the `payment_reference`. To guarantee accuracy, the retrieved receipt is passed through the Best-Fit scoring algorithm. The customer name is only accepted if the receipt scores >= 50 points (proving amount/date alignment). Both exact and stripped references are attempted.
- **Rule 3: Invoice Fallback (With Verification)**
  If both `customer_name` and `payment_reference` are missing, the engine queries the Oracle Invoice Report using the provided `invoice_number`, `invoice_amount`, and `invoice_date`. Once a potential customer name is discovered, the engine executes a verification query to the Receipt Report, demanding an exact match on the JSON's `total_amount` and `payment_date` before accepting the customer name.


