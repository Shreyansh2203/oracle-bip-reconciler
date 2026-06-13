## 2026-06-13T16:39:38Z

You are a patch worker. Please apply robust, non-destructive patches to the codebase located at c:\Users\Shreyansh\Desktop\urban-octo-tribble to resolve the logic deviations, resilience issues, and security risks identified in the audit.
Your role: teamwork_preview_worker.
Your working directory: c:\Users\Shreyansh\Desktop\urban-octo-tribble\.agents\worker_patches_1.

Follow these tasks:

1. **Modify `src/utils/date_formatter.py`**:
   - Normalize timezone-aware datetimes to UTC before formatting. For example:
     ```python
     dt = datetime.fromisoformat(date_str)
     if dt.tzinfo:
         dt = dt.astimezone(timezone.utc)
     return dt.strftime("%Y-%m-%d")
     ```
   - Add a `safe_date_match(date1: Any, date2: Any) -> bool` function:
     ```python
     def safe_date_match(date1: Any, date2: Any) -> bool:
         if not date1 or not date2:
             return False
         d1 = format_oracle_date(str(date1))
         d2 = format_oracle_date(str(date2))
         return bool(d1) and bool(d2) and d1 == d2
     ```

2. **Modify `src/services/oracle_matcher.py`**:
   - Refactor `safe_float_match` to use Python's `decimal.Decimal` and strip commas to prevent floating-point comparison and comma parsing issues:
     ```python
     from decimal import Decimal, InvalidOperation
     def safe_float_match(expected_amount: Any, actual_amount: Any) -> bool:
         if expected_amount is None or actual_amount is None:
             return False
         try:
             exp_str = str(expected_amount).strip().replace(",", "")
             act_str = str(actual_amount).strip().replace(",", "")
             if not exp_str or not act_str or exp_str.lower() == "none" or act_str.lower() == "none":
                 return False
             return Decimal(exp_str) == Decimal(act_str)
         except (ValueError, TypeError, InvalidOperation):
             return False
         ...
     ```
   - Import `safe_date_match` from `src.utils.date_formatter` and replace all raw date string comparisons with `safe_date_match`.
   - In the invoice `rules` list (around line 310), reorder the rules so that Rule 1b (Number + Date + EnteredAmount) is evaluated BEFORE Rule 1a (Number + EnteredAmount) to avoid shadowing.
   - Add `timeout=15.0` parameter to candidate query client calls (e.g. `client.get` in `_fetch_page`).

3. **Modify `src/services/oracle_bip.py`**:
   - In `run_bip_bulk_match`, modify the CSV parsing loop to map each transaction number to a LIST of dicts (candidates) instead of overwriting with a single dict:
     ```python
     if trx_num:
         if trx_num not in invoice_map:
             invoice_map[trx_num] = []
         invoice_map[trx_num].append(clean_row)
     ```
   - In `run_bip_bulk_match`, handle `httpx.HTTPStatusError` explicitly. If the response status code is a transient one (429, 500, 502, 503, 504), raise it so tenacity retries. For other status codes, log the error and return `{}` (or return empty map).
   - Update the `@retry` decorator on `run_bip_bulk_match` to include `httpx.HTTPStatusError` in the `retry_if_exception_type` check: `retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError))`.

4. **Modify `src/main.py`**:
   - Add a key normalization helper function:
     ```python
     def normalize_invoice_candidate(raw: dict[str, Any]) -> dict[str, Any]:
         normalized = {}
         def get_any(keys: list[str]) -> Any:
             for k in keys:
                 if k in raw:
                     return raw[k]
                 if k.upper() in raw:
                     return raw[k.upper()]
                 if k.lower() in raw:
                     return raw[k.lower()]
                 k_clean = k.upper().replace("_", "").replace(" ", "")
                 if k_clean in raw:
                     return raw[k_clean]
             return None
         normalized["TransactionNumber"] = get_any(["TransactionNumber", "TRANSACTION_NUMBER", "InvoiceNumber", "INVOICE_NUMBER"])
         normalized["TransactionDate"] = get_any(["TransactionDate", "TRANSACTION_DATE", "InvoiceDate", "INVOICE_DATE"])
         normalized["EnteredAmount"] = get_any(["EnteredAmount", "ENTERED_AMOUNT", "TotalAmounts", "TOTAL_AMOUNTS", "Amount", "AMOUNT"])
         normalized["InvoiceStatus"] = get_any(["InvoiceStatus", "INVOICE_STATUS", "CreditMemoStatus", "CREDIT_MEMO_STATUS", "Status", "STATUS"])
         normalized["InvoiceBalanceAmount"] = get_any(["InvoiceBalanceAmount", "INVOICE_BALANCE_AMOUNT", "TransactionBalanceDue", "TRANSACTION_BALANCE_DUE", "Balance", "BALANCE"])
         normalized["DocumentNumber"] = get_any(["DocumentNumber", "DOCUMENT_NUMBER"])
         normalized["BillToCustomerName"] = get_any(["BillToCustomerName", "BILL_TO_CUSTOMER_NAME", "BillCustomerName", "BILL_CUSTOMER_NAME", "CustomerName", "CUSTOMER_NAME"])
         for k, v in raw.items():
             if k not in normalized:
                 normalized[k] = v
         return normalized
     ```
   - In `_build_bip_invoice_map`, update the chunk combination to properly merge dictionary keys containing lists of dicts:
     ```python
     final_map = {}
     for res in results:
         if isinstance(res, dict):
             for k, v_list in res.items():
                 if k not in final_map:
                     final_map[k] = []
                 if isinstance(v_list, list):
                     final_map[k].extend(v_list)
                 else:
                     final_map[k].append(v_list)
     ```
   - Refactor `_map_bip_invoices` to run the cascading rules (Rule 1b first, then Rule 1a, Rule 2, Rule 3, Rule 4) and Two-Phase Status Priority check (Open first, then Closed) on normalized candidates. Use the helper `apply_rules_to_candidates` (imported from `src.services.oracle_matcher` or redefine locally).
   - In `get_api_key`, use `secrets.compare_digest` for secure API Key verification.

5. **Modify `src/config.py`**:
   - Enforce HTTPS URL scheme check: reject `http://` URLs in production configurations unless the host is `localhost` or `127.0.0.1`, or `ENV` environment variable is development/test/dev.

6. **Verify and Test**:
   - Run the complete pytest suite to ensure that all 18 existing tests continue to pass.
   - Write additional pytest tests (in a new test file or by modifying existing ones) to explicitly test:
     - Date comparisons with timezone offsets.
     - Commas in amounts parsing and Decimal matching.
     - API key constant-time check.
     - BIP pipeline status priority/duplicates behavior.
     - Insecure URL rejection in production.
     - BIP retry logic for transient status codes.
   - Run pytest and document the output in your handoff report.
