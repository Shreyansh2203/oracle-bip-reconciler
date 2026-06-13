# Challenger Handoff Report

This report presents adversarial verification findings of the patched reconciliation logic in the `urban-octo-tribble` workspace.

## 1. Observation

Adversarial testing was performed by writing a dedicated test suite `tests/test_adversarial.py` and running the entire workspace test suite via `python -m pytest`. All 39 tests passed, confirming the following specific system behaviors:

1. **Date Formatting Ambiguity**:
   In `src/utils/date_formatter.py` line 29:
   ```python
   formats = [
       "%Y-%m-%d", "%m-%d-%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
       "%Y-%m-%dT%H:%M:%SZ"
   ]
   ```
   When given input in DD-MM-YYYY format, `"05-06-2026"` (June 5th) is parsed as May 6th (`"2026-05-06"`), while `"15-06-2026"` (June 15th) is correctly parsed as June 15th (`"2026-06-15"`).
   
2. **Timezone Date Shifting**:
   In `src/utils/date_formatter.py` line 18:
   ```python
   try:
       dt = datetime.fromisoformat(date_str)
       if dt.tzinfo:
           dt = dt.astimezone(timezone.utc)
       return dt.strftime("%Y-%m-%d")
   ```
   `format_oracle_date("2026-06-13T02:30:00+05:30")` outputs `"2026-06-12"`, shifting the transaction business date to the previous day and causing a matching failure against the ledger.

3. **Float Precision Matching Failure**:
   In `src/services/oracle_matcher.py` line 102:
   ```python
   return Decimal(exp_str) == Decimal(act_str)
   ```
   Floating-point calculations in memory can result in tiny representation differences. For instance, `safe_float_match(0.1 + 0.2, "0.3")` returns `False` since `str(0.1 + 0.2)` evaluates to `"0.30000000000000004"`.

4. **Unhandled NaN/Infinity Input Crash**:
   In `src/main.py` line 145:
   ```python
   inv_amount_cents = round(float(inv.invoice_amount) * 100) if inv.invoice_amount is not None else None
   ```
   If a payload is submitted with an amount of `"NaN"` or `"Infinity"`, Pydantic parses them into floats. However, the subsequent call to `round()` on `nan` or `inf` raises `ValueError: cannot convert float NaN to integer` or `OverflowError: cannot convert float infinity to integer`, crashing the request with an HTTP 500 status code.

5. **Silent Exception Swallowing**:
   In `src/services/oracle_matcher.py` lines 225-246:
   ```python
   try:
       inv_res = await fetch_oracle_candidates(context, "receivablesInvoices", query, fields=inv_fields)
       ...
   except Exception as e:
       logger.warning(f"Raw Invoice fetch exception: {e}")
       last_exception = e
   ...
   if not candidates:
       try:
           cm_res = await fetch_oracle_candidates(context, "receivablesCreditMemos", query, fields=cm_fields)
           ...
   ```
   If the invoice API query fails with a server error, the exception is caught and logged, but if the credit memo query succeeds, the function returns `[]` without raising any error, leading to a false negative match result.

---

## 2. Logic Chain

1. **Date Ambiguity**: Because the parser tries `"%m-%d-%Y"` before `"%d-%m-%Y"`, any DD-MM-YYYY date with a day of 12 or less will swap the month and day, whereas dates with a day of 13 or more will fall back to the correct parsing. This leads to silent mismatching of transactions on days 1–12 of every month.
2. **Timezone Shifts**: Converting ISO datetimes with timezone offsets directly to UTC changes the calendar date if the local time falls near midnight. Since ledger transactions match on business dates (local calendar days), this shift results in false mismatch errors.
3. **Float Precision**: Comparing floats converted via `str()` directly to `Decimal()` keeps any representation errors (e.g. `0.30000000000000004`), resulting in inequality against clean decimal representations (like `Decimal("0.3")`).
4. **NaN/Infinity Crash**: Python's `round()` does not support float `nan` or `inf` values. Unhandled inputs of `nan` or `inf` cause `ValueError` and `OverflowError` respectively, leading to top-level API failures (HTTP 500).
5. **Exception Swallowing**: If the `receivablesInvoices` API call raises a server error but `receivablesCreditMemos` returns an empty list, the final return value of `fetch_by_query` is `[]`, masking a transient API failure as a normal "no candidates found" outcome.

---

## 3. Caveats

- Tests are mock-based using `respx` and do not test interactions with live Oracle instances.
- Very large candidate volumes (>4990) are paginated and capped, which might truncate valid matches if there are many candidates.

---

## 4. Conclusion

The patched reconciliation logic is correct for standard, well-formatted inputs but contains five distinct vulnerabilities/bugs:
1. Swapping month/day incorrectly for days 1-12 under DD-MM-YYYY inputs.
2. UTC timezone conversion shifts business dates and breaks ledger matching.
3. Floating-point precision representation failures.
4. HTTP 500 server errors on NaN/Infinity amounts.
5. Silent error swallowing on invoices API endpoint failures.

---

## 5. Verification Method

To verify these vulnerabilities, run the adversarial test suite:
```powershell
python -m pytest tests/test_adversarial.py
```
Or run the full test suite (including stress and integration tests):
```powershell
python -m pytest
```
All 39 tests (including the 10 custom adversarial checks) will run and pass, validating these findings.
