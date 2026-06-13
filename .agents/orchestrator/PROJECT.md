# Project: Codebase Robustness Audit

## Architecture
- FastAPI application for reconciliation matching of Standard Receipts and Receivables Invoices with Oracle Cloud ERP REST APIs and BI Publisher bulk report.
- Modules:
  - `src/config.py`: Configuration helper (Oracle URL verification).
  - `src/models.py`: Pydantic models with input sanitization.
  - `src/main.py`: Main endpoints, hybrid bulk matching logic, concurrent REST API fallback, CORS setup.
  - `src/services/oracle_bip.py`: Oracle BI Publisher bulk CSV report retrieval.
  - `src/services/oracle_matcher.py`: Core receipt and invoice cascading logic, paging logic, local filtering.
  - `src/utils/date_formatter.py`: Date format parsing and formatting.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration & Codebase Audit | Comprehensive audit of codebase logic & rules alignment | none | DONE |
| 2 | Implementation of Patches | Address discrepancies, resilience issues, test quality, edge cases | M1 | DONE |
| 3 | Verification & Auditing | Run full pytest suite, challenge changes, forensic audit | M2 | DONE |
| 4 | Sentinel Notification | Report successful completion to the main agent | M3 | IN_PROGRESS |

## Interface Contracts
- Standard FastAPI request/response models: `ReconciliationRequest`.
- Matching endpoints: `/v1/reconcile`.
