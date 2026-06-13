# Challenger Verification Report

## 1. Observation
We reviewed the reconciliation matching logic and executed the project test suite under adversarial conditions. The key files investigated are:
- `src/main.py`
- `src/services/oracle_matcher.py`
- `src/services/oracle_bip.py`
- `src/models.py`
- `src/utils/date_formatter.py`

### Specific Code Points Observed:
1. **Global Semaphore Contention & Starvation (`src/main.py` lines 127-152):**
   ```python
   async def _fetch_invoices_concurrently(payload: ReconciliationRequest, unmatched_invoices: list[Any], x_oracle_user: str, x_oracle_pass: str, customer_name: str) -> list[Any]:
       sem = app.state.oracle_sem
       shared_customer_cache = {}
       customer_lock = asyncio.Lock()

       async def check_invoice_with_semaphore(*args, **kwargs):
           async with sem:
               return await check_invoice_cascading(*args, **kwargs)
   ```
   *Observation:* The global semaphore wrapper `check_invoice_with_semaphore` blocks execution until a slot is available, holding the slot during the *entire* execution of `check_invoice_cascading`, including when waiting on `customer_lock` (used to prevent duplicate REST queries).

2. **Database Pagination Cap Truncation (`src/services/oracle_matcher.py` lines 73-86):**
   ```python
           pages = 0
           MAX_PAGES = 10
           while has_more and pages < MAX_PAGES:
               data = await _fetch_page(offset)
               items = data.get("items", [])
               all_items.extend(items)
               has_more = data.get("hasMore", False)
               offset += limit
               pages += 1
               
           if has_more:
               logger.warning(f"Pagination capped at {MAX_PAGES} pages. Some candidates may be truncated.")
   ```
   *Observation:* The REST candidate fetcher caps queries at exactly 10 pages (`MAX_PAGES = 10`). Under a default limit of 499, this limits the retrieved results to 4,990 candidates.

3. **Customer Fallback skips Credit Memos (`src/services/oracle_matcher.py` lines 225-246):**
   ```python
       try:
           inv_res = await fetch_oracle_candidates(context, "receivablesInvoices", query, fields=inv_fields)
           if isinstance(inv_res, list):
               candidates.extend(inv_res)
       except Exception as e:
           logger.warning(f"Raw Invoice fetch exception: {e}")
           last_exception = e

       if not candidates:
           try:
               cm_res = await fetch_oracle_candidates(context, "receivablesCreditMemos", query, fields=cm_fields)
   ```
   *Observation:* Credit memos are only queried sequentially from `receivablesCreditMemos` if `candidates` is empty after querying `receivablesInvoices`.

4. **Float Conversion Crashes on NaN/Inf (`src/main.py` lines 145-146):**
   ```python
   inv_amount_cents = round(float(inv.invoice_amount) * 100) if inv.invoice_amount is not None else None
   ```
   *Observation:* Direct call to `round(float(...) * 100)` without trapping NaN or Infinity.

5. **API Payload Rejection on Commas (`src/models.py` lines 12-13):**
   ```python
   class InvoiceItem(BaseModel):
       ...
       invoice_amount: float | None = None
   ```
   *Observation:* Amounts in the request payload are typed as `float`, which throws a Pydantic `ValidationError` (HTTP 422) when commas are passed as thousands separators.

6. **Error Swallowing on Sequential Fetching (`src/services/oracle_matcher.py` lines 225-246):**
   *Observation:* If the `receivablesInvoices` query crashes (e.g. database error), it is caught and logged as a warning, and `fetch_by_query` sequentially queries `receivablesCreditMemos`. If the credit memo query succeeds, the initial crash is swallowed, returning `[]` and hiding database failures.

7. **Float Precision Matching Mismatch (`src/services/oracle_matcher.py` lines 94-104):**
   ```python
   def safe_float_match(expected_amount: Any, actual_amount: Any) -> bool:
       ...
           return Decimal(exp_str) == Decimal(act_str)
   ```
   *Observation:* Decimal exact matching is performed. Calculated floats (e.g. `0.1 + 0.2`) result in string values like `"0.30000000000000004"`, which fail to match the target string `"0.3"`.

---

## 2. Logic Chain
1. **Semaphore Contention:**
   - If a request contains `MAX_CONCURRENCY` (e.g. 50) or more unmatched invoices, 50 tasks will enter `check_invoice_with_semaphore` and occupy all slots in `app.state.oracle_sem`.
   - If these invoices fallback to the same customer name, the first task acquires `customer_lock` and triggers the HTTP fetch, while the other 49 tasks wait on `customer_lock`.
   - Because these 49 tasks are suspended *inside* the semaphore, they continue to hold all semaphore slots.
   - Consequently, the global semaphore is saturated. Any other requests (or invoices in the same request that do not require the customer lock) are blocked from executing, reducing effective concurrent REST API throughput to 1.

2. **Pagination Cap Truncation:**
   - Since pagination is capped at 10 pages, a maximum of 4,990 candidates is fetched.
   - If a customer has >4,990 open invoices, the candidates beyond that offset are discarded.
   - Therefore, valid matches residing beyond the first 4,990 items will never be resolved, compromising matching correctness.

3. **Sequential Credit Memo Exclusion:**
   - If a query is run by `BillToCustomerName` (customer name fallback), `receivablesInvoices` is fetched first.
   - If the customer has at least one active standard invoice, `candidates` is non-empty.
   - Since `candidates` is not empty, the sequential logic skips `receivablesCreditMemos` entirely.
   - Thus, any Credit Memo matches for that customer will be missed if they also have active standard invoices.

4. **NaN/Inf Crashes:**
   - If a payload contains `invoice_amount` as `NaN` or `Infinity` (which can bypass initial validation if passed as float strings like `"NaN"` or `"Infinity"`), `round(float("nan") * 100)` throws a `ValueError` (cannot convert float NaN to integer), causing the entire endpoint processing to crash with a 500 status code.

5. **Float Precision Matching Failure:**
   - Dynamic math operations in Python floats yield binary inaccuracies (e.g., `0.30000000000000004`).
   - Converting this string representation into a `Decimal` yields `Decimal('0.30000000000000004')`, which fails to match `Decimal('0.3')`.

---

## 3. Caveats
- We did not conduct live database load testing on physical Oracle Cloud ERP REST APIs due to network isolation requirements (`CODE_ONLY` mode). Mocks were used to simulate API responses.
- We assumed a default pagination limit of 499 based on `DEFAULT_ORACLE_LIMIT = 499` in `oracle_matcher.py`. If the environment overrides `ORACLE_LIMIT` to a lower value, the pagination truncation threshold will decrease proportionally.

---

## 4. Conclusion
While the patch introduces helpful structures like per-request lazy-loaded locks and bulk BIP fetches, it introduces critical concurrency starvation risks, functional correctness defects (truncation of candidate search pools after 4,990 items, skipped Credit Memos during customer fallback queries), and potential server-side crashes under invalid amounts.

---

## 5. Verification Method
The issues were verified using the custom stress suite at `tests/test_stress.py` and the adversarial suite at `tests/test_adversarial.py`.
- **Run command:** `.venv\Scripts\pytest tests/test_stress.py tests/test_adversarial.py`
- **Result:** 13/13 passing, proving the presence of the pagination cap limit, lock contention under low semaphore limits, and float conversion crashes.

---

# Adversarial Challenge Report

## Challenge Summary
**Overall risk assessment**: HIGH

The patched code successfully solves the N+1 REST call duplication issue but suffers from design flaws that introduce concurrency starvation under load, truncation of candidates for active customers, skipped Credit Memos during customer-name queries, and server-side crashes on unvalidated float payloads.

## Challenges

### [High] Challenge 1: Concurrency Starvation under Lock Contention
- **Assumption challenged:** Wrapping all cascading logic in a global semaphore prevents rate-limiting without starvation.
- **Attack scenario:** Enqueuing many unmatched invoices for the same customer blocks other requests because tasks waiting for the customer name lock continue to hold slots in the global semaphore.
- **Blast radius:** All concurrent REST API matching calls are blocked, dropping the API throughput to 1 call per lock resolution.
- **Mitigation:** Acquite the global semaphore *inside* the HTTP execution wrapper rather than wrapping the entire `check_invoice_cascading` call.

### [High] Challenge 2: Candidate Pool Truncation
- **Assumption challenged:** Capping pagination at 10 pages is sufficient for candidate fetching.
- **Attack scenario:** Active customers with more than 4,990 invoices will have their candidates truncated.
- **Blast radius:** Matches beyond the 10th page will be silently ignored, resulting in unmatched reconciliation items.
- **Mitigation:** Allow page count limits to scale dynamically, or restrict customer-name queries to narrower search windows (e.g. date ranges) to keep candidates per request below the limit.

### [Medium] Challenge 3: Credit Memos Skipped in Customer Fallback
- **Assumption challenged:** Sequential fetching of Invoices then Credit Memos is a safe performance optimization.
- **Attack scenario:** If a customer has standard invoices, `fetch_by_query` skips querying credit memos, making it impossible to resolve credit memos via customer-name fallback.
- **Blast radius:** All credit memos remain unmatched if the customer has standard invoices.
- **Mitigation:** Only apply sequential skipping to single-item queries (e.g., by unique `TransactionNumber`), but execute concurrent queries for customer-name queries.

### [Medium] Challenge 4: NaN/Infinity Float Crashes
- **Assumption challenged:** Pydantic validation guarantees safe numeric amounts.
- **Attack scenario:** Payloads containing `"NaN"` or `"Infinity"` in amount fields bypass validation and crash the cents rounding logic (`round(float(amount) * 100)`).
- **Blast radius:** 500 Internal Server Errors and request aborts.
- **Mitigation:** Add sanitization to validate that amount inputs are finite numbers before processing.

---

## Stress Test Results

| Scenario | Expected Behavior | Actual Behavior | Pass/Fail |
|---|---|---|---|
| Fetch pagination > 10 pages | Caps at 10 requests, truncating output | Capped at exactly 10 pages (4,990 candidates) | **PASS** |
| 10 concurrent customer name lookups | Only 1 DB query triggered, others wait | 1 DB query triggered, cache populated | **PASS** |
| Float inputs with NaN / Inf | Graceful validation or reject | Crashes with ValueError/OverflowError (500 Status Code) | **FAIL (Vulnerability)** |
| 50 candidates prefix query | Reject multiple matching rules | Fails to match, continues to next rule | **PASS** |

## Unchallenged Areas
- **BIP Report server-side limits:** The physical report size limits and processing timeouts on the BI Publisher server could not be tested without access to the actual Oracle ERP environment.
