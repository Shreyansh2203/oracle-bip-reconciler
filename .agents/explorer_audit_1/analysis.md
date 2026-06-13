# Architectural and Logic Audit Report

## Executive Summary
This report presents the findings of a deep architectural and logic audit of the Oracle ERP Cloud Reconciliation API. While the codebase is structured well and the existing test suite passes, we identified multiple severe logical deviations from the processing rules, two-phase status priority gaps, network resilience failures, and security risks that must be addressed prior to production release.

---

## 1. Summary of Identified Issues and Risks

| ID | Category | Component | Description / Risk | Severity |
| :--- | :--- | :--- | :--- | :--- |
| **L-01** | Logic Deviation | `oracle_matcher.py` | Invoice Rule 1a shadows Rule 1b, making 1b unreachable for unique invoices. | High |
| **L-02** | Logic Deviation | `oracle_matcher.py` | Receipt Rule B2 is redundant and unreachable when customer name is present. | Medium |
| **L-03** | Status Priority | `main.py` (BIP Pipeline) | BI Publisher pipeline bypasses Two-Phase Status Priority check entirely and is non-deterministic. | High |
| **E-01** | Edge Case / Bug | `main.py` / `oracle_bip.py` | Float parsing fails on formatted CSV amounts containing commas (e.g. "1,234.56"). | High |
| **E-02** | Edge Case / Bug | `oracle_matcher.py` | `safe_float_match` uses `float` and `round`, introducing rounding that violates exact amount rules. | Medium |
| **E-03** | Edge Case / Bug | `date_formatter.py` | Timezone-aware ISO dates are formatted without UTC normalization, causing day-boundary matching failures. | Medium |
| **E-04** | Edge Case / Bug | `main.py` | BIP date validation treats unparseable dates as successful matches. | High |
| **E-05** | Edge Case / Bug | `oracle_matcher.py` | Receipt Rule A1 lacks a validity check on dates, matching empty dates incorrectly. | Medium |
| **S-01** | Security | `config.py` | Scheme validation permits plain HTTP, transmitting Basic Auth credentials in plaintext. | High |
| **S-02** | Security | `main.py` | Timing attack vulnerability in API Key header verification. | Low |
| **R-01** | Resilience | `oracle_bip.py` | BIP request wrapper swallows `HTTPStatusError` (e.g. 429, 503), preventing retry backoff from firing. | High |
| **R-02** | Resilience | `oracle_matcher.py` | REST candidate fetches have no request-level timeouts specified. | Medium |

---

## 2. Detailed Findings and Rationale

### L-01: Rule 1a Shadows Rule 1b (Unreachable Rule)
* **File:** `src/services/oracle_matcher.py` (Lines 310-313)
* **Verbatim Code:**
  ```python
  rules = [
      ("1a", lambda candidate: safe_str_match(candidate.get("TransactionNumber"), invoice_number) and safe_float_match(candidate.get("EnteredAmount"), amount)),
      ("1b", lambda candidate: safe_str_match(candidate.get("TransactionNumber"), invoice_number) and format_oracle_date(str(candidate.get("TransactionDate"))) == formatted_date and safe_float_match(candidate.get("EnteredAmount"), amount)),
      ...
  ]
  ```
* **Analysis:**
  Within a search phase, the rules are evaluated sequentially in numerical order. The moment a rule yields exactly one match, evaluation stops.
  Because Rule 1a is checked first and is looser (does not check the date), any candidate that matches Rule 1b will also match Rule 1a. In standard Oracle environments where `TransactionNumber` is unique, Rule 1a will yield exactly 1 match and stop.
  * **Result:** Rule 1b (Number + Date) is never evaluated. If a payload contains an invoice with a matching number and amount but a *mismatched date*, the system will successfully match it under Rule 1a, completely bypassing the date validation.
  * **Recommendation:** Place stricter rules (like Rule 1b, which requires both number and date) *before* looser rules (like Rule 1a, which only requires the number).

### L-02: Redundant Receipt Scenario B Rules
* **File:** `src/services/oracle_matcher.py` (Lines 181-185)
* **Verbatim Code:**
  ```python
  rules = [
      ("B1", lambda candidate: safe_float_match(candidate.get("Amount"), amount) and bool(formatted_date) and format_oracle_date(str(candidate.get("ReceiptDate"))) == formatted_date and (safe_str_match(candidate.get("CustomerName"), customer_name) if customer_name else True)),
      ("B2", lambda candidate: bool(customer_name) and safe_str_match(candidate.get("CustomerName"), customer_name) and safe_float_match(candidate.get("Amount"), amount) and bool(formatted_date) and format_oracle_date(str(candidate.get("ReceiptDate"))) == formatted_date),
  ]
  ```
* **Analysis:**
  If `customer_name` is present in the payload:
  - B1 requires Customer Name, Amount, and Date.
  - B2 also requires Customer Name, Amount, and Date.
  Since both rules run identical checks, if B1 fails (yielding 0 or >1 matches), B2 will also fail.
  If `customer_name` is absent:
  - B1 ignores the Customer Name check and matches on Amount and Date.
  - B2 fails immediately because of the `bool(customer_name)` check.
  * **Result:** Rule B2 is fully redundant and dead code.
  * **Recommendation:** Remove Rule B2 or redefine the hierarchy according to desired business intentions.

### L-03: BIP Pipeline Bypasses Status Priority and is Non-Deterministic
* **File:** `src/main.py` (Lines 266-300)
* **Verbatim Code:**
  ```python
  def _map_bip_invoices(payload: ReconciliationRequest, invoice_map: dict[str, Any]) -> list[Any]:
      unmatched_invoices = []
      for inv in payload.invoices:
          num = str(inv.invoice_number) if inv.invoice_number else ""
          if num and num in invoice_map:
              match = invoice_map[num]
              ...
              amount_matches = safe_float_match(inv.invoice_amount, fusion_amount)
              formatted_date = format_oracle_date(str(inv.invoice_date)) if inv.invoice_date else ""
              oracle_date = match.get("TRANSACTION_DATE") or match.get("INVOICEDATE") or match.get("TRANSACTIONDATE")
              
              date_matches = True
              if formatted_date and oracle_date:
                  oracle_date_fmt = format_oracle_date(str(oracle_date))
                  if formatted_date != oracle_date_fmt and oracle_date_fmt != "":
                      date_matches = False

              if amount_matches and date_matches:
                  inv.fusion_invoice_number = match.get("TRANSACTION_NUMBER") or match.get("INVOICENUMBER") or match.get("TRANSACTIONNUMBER")
                  ...
  ```
* **Analysis:**
  The BIP reconciliation check does not verify the `InvoiceStatus` or `InvoiceBalanceAmount` of the cached records. It matches invoices directly, completely skipping the requirement to prioritize "Open" records first before falling back to "Closed".
  Additionally, the BIP report map is built as:
  ```python
  for row in reader:
      ...
      invoice_map[trx_num] = clean_row
  ```
  If the BIP CSV output contains multiple rows for the same transaction number (e.g. one Open and one Closed due to adjustments/memos), the dictionary overwrites the first encounter with the last one processed. This introduces non-deterministic matching based purely on CSV row ordering.
  * **Recommendation:** Ensure the BIP dictionary maps a transaction number to a list of candidate records, and execute the two-phase status priority and cascading rules locally on that candidate list before mapping matches.

### E-01: CSV Amount Float Parsing Mismatch on Commas
* **File:** `src/main.py` (Lines 274-280)
* **Verbatim Code:**
  ```python
  raw_amt = match.get("TOTAL_AMOUNTS") or match.get("ENTEREDAMOUNT") or match.get("AMOUNT")
  if raw_amt is not None:
      fusion_amount = float(raw_amt)
  ```
* **Analysis:**
  BI Publisher CSV outputs often format currency fields with thousand-separator commas (e.g., `"1,234.56"`). Passing this string directly to `float()` raises a `ValueError`.
  * **Result:** For any invoices with amounts $\ge 1,000.00$ formatted with commas, the BIP match will fail and fall back to slow REST API queries, drastically decreasing system throughput and increasing Oracle ERP load.
  * **Recommendation:** Remove commas from the amount string prior to casting: `float(raw_amt.replace(",", ""))`.

### E-02: Float Comparison Precision and Rounding Issues
* **File:** `src/services/oracle_matcher.py` (Lines 93-99)
* **Verbatim Code:**
  ```python
  def safe_float_match(expected_amount: float | str | None, actual_amount: float | str | None) -> bool:
      try:
          if expected_amount is None or actual_amount is None:
              return False
          return round(float(expected_amount) * CENTS_MULTIPLIER) == round(float(actual_amount) * CENTS_MULTIPLIER)
      except (ValueError, TypeError):
          return False
  ```
* **Analysis:**
  IEEE 754 floating-point types cannot represent certain decimal values accurately. Furthermore, the use of `round()` violates the strict "Exact Amounts: No fuzzy matching or rounding is allowed" rule.
  * **Result:** If an Oracle invoice amount is `100.004` and the payload has `100.00`, they will both round to `10000` cents and match successfully, which is a financial accuracy risk.
  * **Recommendation:** Use Python's `decimal.Decimal` module for all financial amounts and perform exact string or Decimal matching without rounding.

### E-03: Timezone-Aware Dates Formatted Without UTC Normalization
* **File:** `src/utils/date_formatter.py` (Lines 15-18)
* **Verbatim Code:**
  ```python
  try:
      return datetime.fromisoformat(date_str).strftime("%Y-%m-%d")
  except ValueError:
      pass
  ```
* **Analysis:**
  `fromisoformat` parses ISO-8601 strings containing offsets (e.g., `"2026-06-13T22:00:00-05:00"`). When `.strftime("%Y-%m-%d")` is called, it extracts the date component directly from the timezone-aware object *as-is* without normalizing it.
  * **Result:** `"2026-06-13T22:00:00-05:00"` is formatted as `"2026-06-13"`. However, in UTC time (which Oracle ERP uses for records), this represents `"2026-06-14T03:00:00Z"`. This difference will result in false mismatches near day boundaries.
  * **Recommendation:** Convert timezone-aware datetimes to UTC before formatting:
    ```python
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d")
    ```

### E-04: BIP Date Validation Fallback Bug
* **File:** `src/main.py` (Lines 286-290)
* **Verbatim Code:**
  ```python
  date_matches = True
  if formatted_date and oracle_date:
      oracle_date_fmt = format_oracle_date(str(oracle_date))
      if formatted_date != oracle_date_fmt and oracle_date_fmt != "":
          date_matches = False
  ```
* **Analysis:**
  If the date in the BIP CSV is unparseable or empty (returning `oracle_date_fmt = ""`), the check `oracle_date_fmt != ""` prevents `date_matches` from being set to `False`.
  * **Result:** Unparseable or corrupt dates in the BIP response bypass validation and are matched as `True`, presenting a risk of false-positive matches.
  * **Recommendation:** Treat unparseable Oracle dates as a validation failure. If `formatted_date` is provided but `oracle_date_fmt` is empty, `date_matches` should be set to `False`.

### E-05: Receipt Rule A1 Lack of Date Validity Check
* **File:** `src/services/oracle_matcher.py` (Line 176)
* **Verbatim Code:**
  ```python
  ("A1", lambda candidate: safe_str_match(candidate.get("ReceiptNumber"), receipt_num) and safe_float_match(candidate.get("Amount"), amount) and format_oracle_date(str(candidate.get("ReceiptDate"))) == formatted_date and (safe_str_match(candidate.get("CustomerName"), customer_name) if customer_name else True)),
  ```
* **Analysis:**
  If the payload has an empty `receipt_date`, `formatted_date` is `""`. If a candidate receipt also has an unparseable or empty date, `format_oracle_date(str(candidate.get("ReceiptDate"))) == formatted_date` evaluates as `"" == ""` which is `True`.
  * **Result:** Receipts with missing dates match Rule A1.
  * **Recommendation:** Add a `bool(formatted_date)` check to Rule A1, similar to Rule A4, B1, and B2.

### S-01: Plaintext Credentials Permitted
* **File:** `src/config.py` (Lines 4-8)
* **Verbatim Code:**
  ```python
  def get_oracle_url():
      url = os.getenv("ORACLE_URL", "")
      if url and not (url.startswith("http://") or url.startswith("https://")):
          raise ValueError(...)
      return url
  ```
* **Analysis:**
  Allowing `http://` for `ORACLE_URL` means Basic Authentication credentials (`ORACLE_USER` and `ORACLE_PASS`) can be transmitted in cleartext over the network.
  * **Recommendation:** Restrict URL scheme validation to `https://` in production configurations.

### S-02: Non-Constant-Time API Key Comparison
* **File:** `src/main.py` (Line 45)
* **Verbatim Code:**
  ```python
  if api_key != expected_api_key:
  ```
* **Analysis:**
  Using the standard `!=` operator for API Key verification is susceptible to timing attacks, where an attacker measures response latency to guess the API key character-by-character.
  * **Recommendation:** Use `secrets.compare_digest` to perform constant-time comparisons.

### R-01: BI Publisher Retry Swallows HTTPStatusError
* **File:** `src/services/oracle_bip.py` (Lines 80-85)
* **Verbatim Code:**
  ```python
  try:
      response = await client.post(...)
      response.raise_for_status()
      ...
  except httpx.RequestError as e:
      logger.warning(f"Transient BIP fetch error: {e}")
      raise e
  except Exception as e:
      logger.error(f"Failed to execute BIP report: {e}")
      return {}
  ```
* **Analysis:**
  The `tenacity.retry` decorator for `run_bip_bulk_match` is configured to retry on `httpx.RequestError`.
  If BI Publisher returns an HTTP status error (like 429 Too Many Requests or 503 Service Unavailable), `raise_for_status()` raises `httpx.HTTPStatusError`.
  Because `HTTPStatusError` is not a subclass of `RequestError`, it falls into the `except Exception as e` block, which logs the failure and returns `{}`.
  * **Result:** The exception is swallowed, and the retry decorator never triggers. The system immediately fails the bulk BIP match and triggers REST fallbacks, causing a massive surge of concurrent queries to Oracle ERP.
  * **Recommendation:** Raise `HTTPStatusError` for transient status codes (e.g. 429, 5xx) in the try block, and update the retry decorator to include `httpx.HTTPStatusError` (or `httpx.HTTPError`).

---

## 3. Recommended Fixes and Action Plan

1. **Re-order Invoice Cascading Rules:**
   Modify the invoice rules array in `oracle_matcher.py` to evaluate `1b` (stricter number + date match) before `1a` (number only match).
2. **Correct BI Publisher Exception Catching:**
   Ensure `HTTPStatusError` for transient codes is not swallowed in `oracle_bip.py`. Let it raise, and update tenacity to retry on both `RequestError` and `HTTPStatusError`.
3. **Parse CSV Amounts Safely:**
   In `main.py`, remove commas from BI Publisher amounts before converting them to floats: `float(raw_amt.replace(",", ""))`.
4. **Enforce Decimal Types for Financial Matching:**
   Refactor `safe_float_match` to use Python's `decimal.Decimal` to avoid floating-point representation and rounding issues.
5. **Normalize Datetimes to UTC:**
   Update `format_oracle_date` to always convert timezone-aware inputs to UTC prior to generating the date string.
6. **Correct BIP Date Validation Fallback:**
   Change `_map_bip_invoices` date comparison to explicitly fail if `oracle_date_fmt` is empty.
7. **Enforce HTTPS Scheme Check:**
   Modify scheme validation in `config.py` to reject non-HTTPS URLs in production.
