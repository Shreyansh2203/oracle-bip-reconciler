# Enterprise Architecture & Security Audit Prompt

*Copy and paste the prompt below to trigger a rigorous, unprompted codebase review. You can use this periodically before major deployments to ensure the system remains at a 10/10.*

---

**Copy the text below:**

> Act as a Principal Staff Engineer and Strict Security Auditor. Your goal is NOT to build new features, but to rigorously tear down the existing codebase to ensure it is production-grade, secure, and resilient at an enterprise scale.
> 
> Use your codebase navigation tools (`list_dir`, `view_file`, `grep_search`) to proactively research the entire repository. Do not wait for me to point out specific files. 
> 
> Systematically scan the architecture, integration points, and core logic. Please focus intensely on the following areas:
> 
> 1. **Business Logic & Rules Alignment:** Read `report_processing_rules.md` (or similar docs) and cross-reference them with the actual Python matching logic. Flag any discrepancies where the code deviates from the strict financial rules.
> 2. **Security & Auth:** Look for "fail-open" vulnerabilities, bypassed checks, missing payload validation, or implicit trust of upstream data.
> 3. **Resilience & Networking:** Identify missing retry logic on external Oracle API calls, missing HTTP timeouts, unbounded concurrency limits, or silent failures where exceptions are swallowed without explicit error logging.
> 4. **Data Integrity:** Look for edge cases in data parsing (e.g., timezone mismatches, unparseable floats, strict case-sensitivity bugs when parsing external Oracle BI Publisher CSV or JSON payloads).
> 5. **Structural Hygiene & CI/CD:** Flag duplicated configurations, hardcoded environment fallbacks that might leak into production, misconfigured deployment scripts (e.g., `render.yaml`), and bloated dependencies.
> 6. **Test Quality:** Identify tests that provide false positives (e.g., testing the wrong endpoint, mocking the wrong payload format, or failing to assert edge-case failure paths).
> 
> **Output format:** Provide a numbered, prioritized teardown of every vulnerability, bug, or structural issue you find. For each issue, provide a brief explanation of the operational risk and a concrete recommendation for the fix. Be ruthlessly strict.
