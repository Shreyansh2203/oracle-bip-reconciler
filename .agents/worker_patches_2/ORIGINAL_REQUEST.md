## 2026-06-13T16:46:00Z

You are a patch worker. Please apply the second round of robust, non-destructive patches to the codebase located at c:\Users\Shreyansh\Desktop\urban-octo-tribble to resolve the issues identified by the reviewers and challengers.
Your role: teamwork_preview_worker.
Your working directory: c:\Users\Shreyansh\Desktop\urban-octo-tribble\.agents\worker_patches_2.

Follow these tasks:

1. **Modify `src/models.py`**:
   - In `sanitize_floats` (for both `InvoiceItem` and `ReconciliationRequest`), strip commas from string values and validate that the value is a finite number. If it is `NaN`, `Infinity`, or `-Infinity` (parsed as float or passed as string), raise a `ValueError` so Pydantic returns a 422 validation error instead of passing it to the endpoints where it causes crashes.
     ```python
     import math
     # inside sanitize_floats:
     if isinstance(v, str):
         v_clean = v.strip().replace(",", "")
         if v_clean.lower() == "none":
             return None
         v = v_clean
     if v is not None:
         try:
             f_val = float(v)
             if not math.isfinite(f_val):
                 raise ValueError("Float value must be a finite number.")
         except (ValueError, TypeError):
             raise ValueError("Float value must be a finite number.")
     return v
     ```

2. **Modify `src/utils/date_formatter.py`**:
   - Revert UTC timezone conversion in `format_oracle_date`. We must extract the local calendar date as-is (e.g. `"2026-06-13"` from `"2026-06-13T02:30:00+05:30"`) because ledger transaction dates represent local business days.
   - Remove `"%d-%m-%Y"` from `formats` list to ensure consistent parsing of ambiguous formats (always parse as `MM-DD-YYYY` rather than swapping month/day inconsistently).

3. **Modify `src/services/oracle_matcher.py`**:
   - Update `OracleClientContext` definition to include `sem: asyncio.Semaphore | None = None`.
   - Update `_fetch_page` to acquire `context.sem` before executing the `client.get(...)` request to prevent semaphore starvation:
     ```python
     if context.sem:
         async with context.sem:
             response = await context.client.get(page_url, auth=(context.user, context.password), timeout=15.0)
     else:
         response = await context.client.get(page_url, auth=(context.user, context.password), timeout=15.0)
     ```
   - Update `fetch_oracle_candidates` to fetch `MAX_PAGES` from environment variable `ORACLE_MAX_PAGES` (default of `100` instead of `10` to avoid candidate pool truncation).
   - Update `fetch_by_query` to take `force_both: bool = False`. If `force_both` is `True` or `candidates` is empty, query credit memos. Do NOT swallow exceptions; if `last_exception` is not `None`, raise it.
   - Update `fetch_by_field` to take `is_unique: bool = True` and pass `force_both=not is_unique` to `fetch_by_query`.
   - In `safe_float_match`, convert both inputs to float, round to 6 decimal places to discard float representation noise, and check `math.isfinite()` before comparing:
     ```python
     def safe_float_match(expected_amount: Any, actual_amount: Any) -> bool:
         if expected_amount is None or actual_amount is None:
             return False
         try:
             exp_str = str(expected_amount).strip().replace(",", "")
             act_str = str(actual_amount).strip().replace(",", "")
             if not exp_str or not act_str or exp_str.lower() == "none" or act_str.lower() == "none":
                 return False
             f_exp = float(exp_str)
             f_act = float(act_str)
             import math
             if not math.isfinite(f_exp) or not math.isfinite(f_act):
                 return False
             return Decimal(f"{f_exp:.6f}") == Decimal(f"{f_act:.6f}")
         except (ValueError, TypeError, InvalidOperation):
             return False
     ```

4. **Modify `src/main.py`**:
   - In `reconcile_data_v1`, inject the FastAPI `Request` object and pass `request.app.state.oracle_sem` to `_process_reconciliation`.
   - Update `_process_reconciliation`, `_fetch_receipt_data`, and `_fetch_invoices_concurrently` signatures to accept and pass the semaphore `sem`.
   - Remove the `async with sem:` block from `check_invoice_with_semaphore` in `_fetch_invoices_concurrently`.
   - Construct `OracleClientContext` using `sem=sem`.

5. **Modify Tests**:
   - Update timezone-aware date tests in `tests/test_worker_patches.py` and `tests/test_adversarial.py` to expect local calendar dates (e.g. `"2026-06-13"` for `"2026-06-13T02:30:00+05:30"`).
   - Update `NaN`/`Infinity` tests in `tests/test_worker_patches.py` and `tests/test_adversarial.py` to assert that they raise Pydantic `ValidationError` (422 status) rather than causing 500 endpoint crashes.
   - Update `test_date_formatter_ambiguity` in `tests/test_adversarial.py` to assert that `"15-06-2026"` fails to parse and returns `""`.
   - Run the complete pytest suite to ensure that all 39 tests pass successfully.
