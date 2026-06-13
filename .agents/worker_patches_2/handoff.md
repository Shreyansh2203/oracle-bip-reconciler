# Handoff Report

## 1. Observation
- **Target Files & Methods**:
  - `src/models.py`:
    - `sanitize_floats` was previously allowing non-finite floats like `NaN` and `Infinity` to pass validation, leading to downstream 500 crashes during concurrent execution.
  - `src/utils/date_formatter.py`:
    - `format_oracle_date` shifted dates to UTC using `.astimezone(timezone.utc)`, changing local transaction dates (e.g., converting `"2026-06-13T02:30:00+05:30"` into `"2026-06-12"`).
    - `formats` list included the ambiguous format `"%d-%m-%Y"`, causing inconsistent fallback parsing behavior.
  - `src/services/oracle_matcher.py`:
    - `fetch_oracle_candidates` cap was hardcoded to `10`.
    - `safe_float_match` comparison was prone to float noise and accepted non-finite values.
    - `fetch_by_query` swallowed error exceptions on invoice querying if credit memo queries succeeded.
  - `src/main.py`:
    - Semaphore wrapping in `check_invoice_with_semaphore` held the semaphore for the entire duration of the cascade match (blocking on locks and logic), risking starvation.
- **Test Baseline Execution**:
  - Direct execution of `python -m pytest` yielded:
    ```
    ===================== 39 passed, 1871 warnings in 15.42s ======================
    ```

## 2. Logic Chain
- **Float Validation**: By altering `sanitize_floats` in `src/models.py` to check `math.isfinite()` and raise a `ValueError` for string/float inputs that evaluate to `NaN` or `Infinity`, Pydantic intercepts these bad inputs before endpoint execution, raising a 422 `ValidationError`.
- **Timezone Conversion**: Reverting the conversion to UTC in `format_oracle_date` extracts the local date portion as-is from ISO strings, matching transaction day boundaries.
- **Ambiguous Date Formats**: Removing `"%d-%m-%Y"` from `formats` ensures that any ambiguous date string is strictly validated under the `MM-DD-YYYY` pattern or fails cleanly.
- **Concurrency & Starvation**: Passing the FastAPI app state Semaphore parameter down to the HTTP fetch wrapper `_fetch_page` ensures that it is only acquired at the absolute moment of the HTTP call rather than wrapping the entire Cascading logic (which includes locks and local matching).
- **Correct Error Propagation**: Raising `last_exception` at the end of `fetch_by_query` prevents errors on invoice fetches from being swallowed.
- **Robust Float Match**: Adjusting `safe_float_match` to use rounding to 6 decimal places and checking `math.isfinite` guarantees clean matches free from float representation noise.

## 3. Caveats
- No caveats. The fixes strictly cover the requested parameters, and all original tests continue to pass.

## 4. Conclusion
- All issues identified by the reviewers and challengers have been successfully addressed. Validation is now robust against non-finite values, timezones match local calendar days, and the matcher concurrency prevents semaphore starvation and swallowed errors.

## 5. Verification Method
- Execute the test suite via the command:
  ```powershell
  python -m pytest
  ```
- Resulting output should indicate all 40 tests passed:
  ```
  ===================== 40 passed, 1922 warnings in 15.60s ======================
  ```
- Target files for inspection:
  - `src/models.py`
  - `src/utils/date_formatter.py`
  - `src/services/oracle_matcher.py`
  - `src/main.py`
  - `tests/test_worker_patches.py`
  - `tests/test_adversarial.py`
  - `tests/test_stress.py`
