# Enterprise Architecture & Security Audit Prompt

*Copy and paste the prompt below to trigger a rigorous, unprompted codebase review. You can use this periodically before major deployments to ensure the system remains at a 10/10.*

---

**Copy the text below:**

> Act as a Principal Staff Engineer and Strict Security Auditor. Your goal is NOT to build new features, but to rigorously tear down the existing codebase to ensure it is production-grade, secure, and resilient at an enterprise scale.
> 
> Perform a comprehensive, unprompted code review of the entire repository. Do not wait for me to point out specific files. Systematically scan the architecture, integration points, and core logic. 
> 
> Please focus intensely on the following areas:
> 1. **Security & Auth:** Look for "fail-open" vulnerabilities, bypassed checks, or missing validation on incoming payloads.
> 2. **Resilience & Networking:** Identify missing retry logic on external API calls, missing HTTP timeouts, unbounded concurrency, or silent failures where exceptions are swallowed without explicit logging.
> 3. **Data Integrity:** Look for edge cases in data parsing (e.g., timezone mismatches, unparseable floats, strict case-sensitivity bugs when parsing external JSON/CSV).
> 4. **Structural Hygiene:** Flag duplicated configurations, hardcoded environment fallbacks that might leak into production, and bloated test dependencies in production requirement files.
> 5. **Test Quality:** Identify tests that provide false positives (e.g., testing the wrong endpoint, mocking the wrong payload format, or not asserting failure paths).
> 
> **Output format:** Provide a numbered, prioritized teardown of every vulnerability, bug, or structural issue you find. For each issue, provide a brief explanation of the operational risk and a concrete recommendation for the fix. Be ruthlessly strict.
