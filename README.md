# Oracle BIP Reconciler

[![CI](https://github.com/Shreyansh2203/oracle-bip-reconciler/actions/workflows/ci.yml/badge.svg)](https://github.com/Shreyansh2203/oracle-bip-reconciler/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A production reconciliation service that matches a payment/receipt ledger row — often a photo of a paper remittance advice, read by OCR — against the customer's real invoice and receipt history in **Oracle Fusion ERP Cloud BI Publisher**. The incoming JSON is repaired in place: truncated invoice numbers, mistyped amounts and mixed date formats are resolved back to the authoritative Oracle record, or explicitly marked `UNMATCHED`.

---

## The problem

Remittance data arrives in whatever shape the customer's bank produced. In practice that means:

- **OCR noise.** `INV‑0001234` arrives as `INV-0001234S`, or truncated to `INV-00012`.
- **Money as text.** `"1,234.56"`, `"1234.56 "`, `"none"`.
- **Every date format at once.** `2026-10-05`, `05/10/2026`, `5 Oct 2026`, `20261005`, `2026-10-05T00:00:00Z`.
- **You may not know the customer.** Sometimes only a payment reference is present; sometimes only a handful of invoice numbers.

Doing this as a straight join against Oracle is not an option. One BI Publisher report for a large tenant can return tens of thousands of rows and take minutes; issuing one query per invoice line will time out the upstream server and blow the caller's request budget.

The answer is a **three-phase engine**: identify the customer with the fewest possible queries, download their ledger **once**, then match everything in memory. Phase 3 makes zero network calls, so a 2,500-line batch costs the same number of round trips as a 1-line batch.

---

## Architecture

```mermaid
flowchart TD
    Client([Ledger JSON<br/>OCR or ERP extract]) -->|POST /v1/reconcile/batch| RL{{SlowAPI<br/>10 req/min per IP}}
    RL --> Pyd[ReconciliationRequest<br/>Pydantic v2 sanitising]
    Pyd --> Disc

    subgraph Phase1["Phase 1 — Customer discovery (fewest queries possible)"]
        Disc[discover_potential_customers]
        Disc --> S1{{Step 1<br/>Receipt by payment_reference?}}
        S1 -->|miss| S2{{Step 2<br/>Receipt by customer_name?}}
        S2 -->|miss| S3{{Step 3<br/>Invoice by number → +amount → +date}}
        S3 -->|unique name| Ident[(Customer identified)]
    end

    subgraph Phase2["Phase 2 — Ledger fetch (one download per report)"]
        BIP[[Oracle BIP SOAP<br/>ExternalReportWSSService]]
        Cache[(TTLCache / Redis<br/>report-level cache)]
        BIP <--> Cache
        BIP --> Inv[(Invoice report CSV)]
        BIP --> Rec[(Receipt report CSV)]
    end

    subgraph Phase3["Phase 3 — In-memory matching (no network)"]
        Match[map_ledger_to_payload]
        Match --> Exact[Exact number index]
        Exact --> T3[3-way: num+date+amount]
        T3 --> T2[2-way: num+date / num+amount]
        T2 --> T1[1-way: unique num / date / amount]
        T1 --> Fz[Fuzzy: substring + Levenshtein]
    end

    Ident --> BIP
    Inv --> Filter[_filter_data_rows<br/>drops parameter echo rows]
    Rec --> Filter
    Filter --> Match
    Match --> Resp[Reconciled payload<br/>MATCHED / UNMATCHED + backfill]
    Resp --> Client
```

The detailed discovery, receipt and invoice rules are documented in
[docs/report_processing_rules.md](docs/report_processing_rules.md).

---

## The tiered invoice-matching algorithm

This is the core of the service. Each incoming invoice line is resolved against the
customer's downloaded ledger using progressively looser tiers. The first tier that produces
a match wins, and **a match is only accepted when it is unambiguous**.

Before matching starts, every Oracle row is normalised **once** — invoice number key,
date folded to `YYYY-MM-DD`, amount coerced to `float`. All later comparisons are then
plain equality checks.

### 0. Already-mapped rows are excluded

A per-row `mapped` flag, not a set of invoice numbers, tracks consumption. This matters:
a customer can legitimately have two ledger rows with the same invoice number (partial
shipments, split payments, a credit memo), and a number-keyed set cannot tell them apart —
it would starve the second one of its match.

### 1. Three-way match — number + date + amount *(highest confidence)*

Exact number hit through a dictionary index built from the ledger, then both the date and
the amount agree. This is the only tier that repairs a fully correct row.

### 2. Two-way match — number + date, or number + amount

Exact number hit, and one of the other two fields agrees. Safe because the number already
pins the row to a small candidate set.

### 3. One-way match — number, date, or amount *(only if unique)*

Exact number with a **single** unmapped candidate takes it even when date and amount both
disagree. Date-only and amount-only matches are accepted **only when exactly one** unmapped
row agrees; if two rows share a date, or two share an amount, the line is left
`UNMATCHED` rather than guessed.

### 4. Fuzzy number match — OCR recovery

Runs only when the number is not an exact dictionary hit. `_is_num_ok` accepts:

| Relationship | Rule | Rationale |
|---|---|---|
| Equal | exact | — |
| Substring, either direction | both sides **≥ 5** characters | OCR truncation of a long number |
| Levenshtein ≤ 1 | shorter side **≤ 6** characters | single-character misread of a short number |
| Levenshtein ≤ 2 | shorter side **> 6** characters | two misreads of a longer number |

The minimum length and the typo budget both key off the **shorter** of the two numbers. A
truncated OCR input is usually *shorter* than the Oracle number, so budgeting from the
input would hand a 6-character Oracle number the same slack as a 20-character one — which
is how `"ABCDEFG"` ends up "matching" an unrelated `"ABCDEX"`.

Fuzzy candidates are split into two buckets, and **only corroborated ones outrank the 1-way
tiers**:

1. fuzzy number **and** a date or amount agreement → beats everything below 3-way
2. fuzzy number alone → used only if nothing else matched

### What the tiers deliberately do *not* do

They never re-query Oracle. Every tier is a pure function over the ledger already in
memory, which is what keeps a 2,500-line batch inside a single request.

---

## API reference

Interactive docs are served at `/docs` (Swagger UI) and `/redoc`.

### `POST /v1/reconcile/batch`

Rate limited to **10 requests/minute per client IP**. No API key.

Request body — every field is optional; the engine discovers what it can:

```jsonc
{
  "customer_name": "Acme Corp",        // Step 2 discovery input
  "payment_reference": "RCPT-8891",    // Step 1 discovery input
  "payment_date": "05-Oct-2026",
  "total_amount": "4,200.50",
  "header_id": 5512,
  "invoices": [                        // max 2500
    {
      "line_id": 1,
      "invoice_number": "INV-00012",   // may be OCR-damaged
      "invoice_date": "05-Oct-2026",
      "invoice_amount": "1,234.56",
      "customer_invoice_number": "CN-99",
      "store_no": "S-104"
    }
  ]
}
```

A successful response is the **same object, repaired** — matched fields overwritten with
the authoritative Oracle values, unmatched fields left alone:

```jsonc
{
  "fusion_customer_name": "Acme Corp",
  "fusion_receipt_number": "RCPT-8891",
  "fusion_receipt_date": "2026-10-05",
  "fusion_applied_amount": 4200.50,
  "fusion_currency": "USD",
  "fusion_receipt_status_code": "APPLIED",
  "fusion_customer_number": "C-77",

  "invoices": [
    {
      "invoice_number": "INV-000123",      // repaired from "INV-00012"
      "invoice_date": "2026-10-05",        // Oracle's own string, see below
      "invoice_amount": 1234.56,
      "fusion_invoice_number": "INV-000123",
      "fusion_invoice_date": "2026-10-05",
      "fusion_invoice_amount": 1234.56,    // a JSON number, not "1,234.56"
      "match_phase": "MATCHED"             // or "UNMATCHED"
    }
  ],
  "invoice_count": 1
}
```

#### Types of the `fusion_*` fields

`fusion_*` is the Oracle record that won. Read these literally:

| Field | JSON type | Contract |
|---|---|---|
| `fusion_invoice_number` | `string \| null` | The ledger's number, verbatim. |
| `fusion_invoice_date` | `string \| null` | The ledger's date **string**, verbatim — `"08/14/2026"` if that is what Oracle returned. It is *not* normalised to `YYYY-MM-DD`; the normalised value only ever existed inside the matcher. Compare with `format_oracle_date` rather than parsing it yourself. |
| `fusion_invoice_amount` | `number \| null` | Coerced to a JSON **number**. Oracle's `"9,500.25"` is serialised as `9500.25`, and a cell the engine cannot parse is `null`, never a string. |
| `fusion_receipt_number` / `fusion_receipt_date` | `string \| null` | Verbatim from the receipt row. |
| `fusion_applied_amount` | `number \| null` | Coerced, like the invoice amount. |
| `fusion_currency` / `fusion_receipt_status_code` / `fusion_customer_number` | `string \| null` | Verbatim. |

`InvoiceItem` sets `validate_assignment=True`, so these types are enforced on the writes the
engine performs, not only on the request that came in. Before that was on,
`fusion_invoice_amount` held the raw Oracle *string* behind a `float` annotation and clients
doing arithmetic on it got a `TypeError`. `invoice_amount` and `invoice_date` are then
overwritten with the same matched values, so a client that only needs the repaired number and
date can ignore the `fusion_*` pair entirely.

`ReconciliationRequest` deliberately does *not* set `validate_assignment`: its
`_set_invoice_count` is a `mode="after"` model validator that assigns a field, and an
after-validator that re-enters itself on every assignment recurses. Its float fields are
coerced at their single assignment site instead.

| Status | Meaning |
|---|---|
| `200` | Reconciled. **A `null` body means the customer could not be identified** — not an error. |
| `422` | Payload failed Pydantic validation (e.g. more than 2,500 invoices). |
| `429` | Rate limit exceeded. |
| `502` | Oracle ERP was unreachable or errored. The response carries a **fixed** message; the upstream detail is written to the server log only. |

### `GET /`

Landing page (`src/templates/index.html`).

### `GET /health`

`{"status": "ok"}` — liveness. Unthrottled, and performs no I/O.

### `GET /ready`

`{"status": "ready"}`, or `503` when Oracle credentials are unconfigured. Unthrottled.

### `GET /docs`, `GET /redoc`, `GET /openapi.json`

Generated API documentation.

---

## Configuration

All configuration is environment-driven, read once at import by `src/core/config.py`.
Copy the template and edit it:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `ORACLE_URL` | *required* | ERP Cloud base URL. Must be `https://`, or `http://` only for loopback. Trailing slash stripped. |
| `ORACLE_USER` | *required* | BI Publisher service account. |
| `ORACLE_PASS` | *required* | Service account password. `repr=False`, so it is masked in `str()`/`repr()` and in `ValidationError` output. |
| `CORS_ORIGINS` | `""` | Comma-separated browser origins. **Empty fails closed** — no `Access-Control-Allow-Origin` is emitted, so browsers refuse to expose the response. Non-browser clients are unaffected, as always with CORS. |
| `ALLOW_INSECURE_ORACLE_HTTP` | `false` | Permit a plain `http://` `ORACLE_URL` for a non-loopback host. |
| `REDIS_URL` | *empty* | Shared report cache. Falls back to an in-process TTL cache. |
| `ORACLE_BIP_INVOICE_PATH` | *empty* | Absolute `.xdo` path, overriding the default in `src/constants.py`. |
| `ORACLE_BIP_RECEIPT_PATH` | *empty* | Absolute `.xdo` path, overriding the default. |
| `BIP_CACHE_TTL_SECONDS` | `60` | Report cache lifetime. |

`.env` is gitignored. Never commit real credentials, and never commit live customer or
financial data — `Customers.txt` and `Real Test Cases/` are ignored for that reason.

---

## Local development

```bash
# Install (uv resolves the lockfile)
uv sync

# Run on http://127.0.0.1:8000 with hot reload
uv run task start          # or: make dev
```

`uv` and `taskipy` are the only prerequisites — there is no need for a virtualenv to exist
first, `uv` creates it.

### Quality gates

```bash
uv run task check_all      # or: make check
```

runs, in order:

| Task | Tool | What it enforces |
|---|---|---|
| `lint` | ruff | pycodestyle, pyflakes, isort, bugbear, comprehensions, pyupgrade |
| `types` | mypy | static types across `src/` and `api/` |
| `security` | bandit | common security anti-patterns |
| `deadcode` | vulture | unreachable code at ≥ 80 % confidence |
| `test` | pytest | the full suite, against a test-count and coverage floor |

`uv run task lint`, `types`, `security`, `deadcode` and `test` can be run individually.

### Dependency audit

```bash
uv run task audit        # pip-audit against requirements.txt
```

This one needs network access, which is why it is **not** in `check_all` — a gate that
cannot run offline is a gate people learn to skip. It is a real gate in CI instead, as its
own `dependency-audit` job:

- runs on every push and pull request, and
- runs on a schedule, **Mondays at 06:17 UTC**, because advisories are published
  continuously and a scan that only ran on push would miss one published for a dependency
  that is already merged.

It audits `requirements.txt` — the exact set Vercel and Render install — and separately the
development toolchain, so a CVE in a linter neither hides nor blocks a finding in a runtime
package. `pip-audit` exits non-zero on a finding, and `--strict` also fails on a requirement
it cannot resolve, so a malformed export cannot pass as "no known vulnerabilities". Both
commands were verified to exit 1 against a deliberately vulnerable pin and exit 0 against
this lockfile.

Dependabot (`.github/dependabot.yml`) opens the PRs that keep the pinned action SHAs and
`uv.lock` current.

### Testing notes

`Settings` requires `ORACLE_URL`, `ORACLE_USER` and `ORACLE_PASS` at import time, so the
suite needs them present — CI sets throwaway values, and `http://localhost:8080` is accepted
by the URL validator without any opt-in. Nothing in the suite reaches the network: the
service layer is patched at the discovery seam.

```bash
uv run pytest -q
uv run pytest tests/test_reconciliation_mapping.py -q   # the matching engine
```

---

## Docker

```bash
docker build -t oracle-reconciliation-api .
docker run --env-file .env -p 8000:8000 oracle-reconciliation-api
```

The image is a `python:3.12-slim` base with `uv sync --no-dev --frozen` into `/app/.venv`,
which is put on `PATH` so the `uvicorn` entry point resolves. `.dockerignore` keeps the
host virtualenv, `.git` and caches out of the build context — without it the build context
is ~300 MB and the host's virtualenv overwrites the one in the image.

---

## Deployment

### Render

`render.yaml` defines a Python web service: `uv sync --frozen --no-dev` at build, then
`.venv/bin/uvicorn src.main:app` bound to `$PORT` with two workers. Three details are
deliberate:

- **`--frozen`** so the build installs exactly what `uv.lock` pins and never re-resolves, and
  **`--no-dev`** so the test and lint tooling is not in the production image.
- **The interpreter is named explicitly.** Render does not promise to put `.venv/bin` on
  `PATH` for a custom build command, and a bare `uvicorn` is a build that succeeds followed by
  a runtime that cannot start.
- **`ORACLE_URL`, `ORACLE_USER` and `ORACLE_PASS` use `sync: false`**, because `Settings` has
  no default for them and Render should prompt rather than start with a placeholder. The six
  optional settings carry their documented default instead, so a deploy is not blocked on a
  prompt for a value the service does not need.

The blueprint declares exactly the nine variables the code reads — six through `Settings`, three
through `os.getenv` — and nothing else. The earlier `ENV`, `MAX_CONCURRENCY`, `ORACLE_LIMIT` and
`ORACLE_MAX_PAGES` entries were read by nothing at all; if they still exist in your Render
dashboard, delete them there. `tests/test_deploy_contract.py` fails if the blueprint and the
code drift apart again.

### Vercel

There is exactly **one** application object, `app` in `src/main.py`. `api/index.py` is a
one-line re-export of it:

```python
from src.main import app  # noqa: F401
```

This is deliberate, not duplication. Vercel's Python builder only discovers an ASGI app
from a module inside `api/`, and `vercel.json` rewrites `/(.*)` to that module — the
rewrite is required, without it every path 404s. `requirements.txt` is the `uv export`
pin set that the Vercel runtime installs, regenerated with:

```bash
uv export --no-dev --no-emit-project --format requirements-txt -o requirements.txt
```

Regenerate it whenever `pyproject.toml`'s runtime dependencies or `uv.lock` change. A drift
here is silent until a deploy, and that is exactly how the last one failed: the export was
missing `pydantic-settings` and `redis`, both declared runtime dependencies, so the runtime
could not import the app. `tests/test_deploy_contract.py` asserts that every declared
runtime dependency is pinned, that no dev-only package leaked in, and that the blueprint,
`vercel.json` and `api/index.py` still agree.

### Self-hosted

```bash
uv sync --no-dev
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Run the ASGI app behind a TLS-terminating proxy. `ORACLE_PASS` travels in the SOAP
envelope and in HTTP basic auth, so plain `http://` to a remote Oracle host is refused at
startup unless `ALLOW_INSECURE_ORACLE_HTTP=true`.

---

## Security

Read [SECURITY.md](SECURITY.md) first. A real Oracle BI Publisher service-account
credential was once committed to this repository's history; it has been purged from the
working tree and from git history, but **it must still be rotated in Oracle** — purging
cannot un-leak it for anyone who cloned before the rewrite. `SECURITY.md` documents the
incident and the exact rotation procedure.

Design notes:

- **No API key.** Authentication is the responsibility of the caller or a gateway; the
  service exposes a rate limit rather than a shared secret.
- **Fail-closed CORS** by default, and `allow_credentials=False` throughout.
- **Rate limiting** on the reconciliation endpoint only; health endpoints stay unthrottled
  so orchestrators are never locked out.
- **Safe XML parsing** — `defusedxml` for the SOAP envelope, so a hostile or malformed
  Oracle response cannot mount an entity-expansion attack.
- **No internal error text crosses the wire.** Oracle exceptions are logged with their type
  and message; the client receives a fixed 502 message.
- **Bounded memory** — the report cache is a `TTLCache` (or Redis), not an unbounded dict.
- `bandit` runs in CI and locally via `uv run task check_all`, and dependencies are
  scanned by `pip-audit` on every build.

---

## Contributing

1. `uv sync`
2. `uv run task check_all` must pass before you push.
3. Keep changes focused. If you touch `map_ledger_to_payload`, add or update the cases in
   `tests/test_reconciliation_mapping.py` — the tier ordering is load-bearing behaviour,
   not an implementation detail.

## License

[MIT License](LICENSE).
