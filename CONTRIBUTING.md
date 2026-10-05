# Contributing

Thanks for looking at this. The service is small, but two of its behaviours are load-bearing
in ways that are easy to break by accident, so they are called out first.

## The one rule that is not negotiable

**Never point a test at a real Oracle tenant.**

`tests/conftest.py` sets `ORACLE_URL`, `ORACLE_USER` and `ORACLE_PASS` unconditionally —
not with `setdefault`, so a developer's `.env` cannot leak into a test run. `Settings()` is
constructed at import time, so the suite cannot even collect without them.

If you add a test that needs a BIP response, mock the transport:

```python
import httpx, respx

SOAP_URL = f"{settings.ORACLE_URL.rstrip('/')}/xmlpserver/services/ExternalReportWSSService"

def test_something():
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(csv_text)))
        rows = run(fetch_bip_invoices, "svc", "pwd", invoice_number="INV-1")

    assert len(rows) == 1
```

Use `.invalid` for hostnames (`https://oracle.test.invalid`), never a real one. A test that
reaches the network is not a slow test, it is a credential leak with extra steps, and this
repository has already had one of those. See [SECURITY.md](SECURITY.md).

Also: `.env` and `env` files with real content never get committed. `.env.example` is the
one tracked template and its values must stay `YOUR_...` placeholders.
`tests/test_deploy_contract.py` asserts all of this.

## The two things that are easy to break

**`src/services/reconciliation.py` — the matching tiers.** The order is the feature:
exact three-way, then two-way, then one-way on a unique exact number, then fuzzy, then
amount-only, then date-only. The inverted tie-break (a fuzzy match is taken *before* a bare
amount match), the per-row `mapped` flag, and the single-pass ledger index that replaced a
rescan per invoice are all deliberate. If a test you write exposes a genuine bug, add a
dedicated regression test for it and open the discussion — do not quietly reorder the tiers.

**`src/core/config.py` — the field order.** `validate_oracle_url` reads
`ALLOW_INSECURE_ORACLE_HTTP` out of `ValidationInfo.data`, which only holds the fields
validated so far. Moving that field below `ORACLE_URL` makes the insecure-HTTP opt-in
silently stop working. There is a test asserting the order, but it exists to make the
constraint loud rather than to make reordering impossible.

## Getting set up

Requires [uv](https://docs.astral.sh/uv/) and **Python 3.12 or newer** — that is the floor in
`requires-python`, and the same version `.python-version`, CI and the Dockerfile use. 3.9 and
3.10 are no longer resolved at all. `make` is not needed; the tasks are run through uv.

```bash
uv sync                 # install, including the dev group
uv run task check_all   # every gate: lint, types, security, deadcode, test
uv run task start       # serve on http://127.0.0.1:8000
```

`uv run task check_all` must exit 0 before you open a pull request.

## The gates

| Task | Tool | Enforces |
|---|---|---|
| `uv run task lint` | ruff | pycodestyle, pyflakes, isort, bugbear, comprehensions, pyupgrade |
| `uv run task types` | mypy | static types in `src/` and `api/` |
| `uv run task security` | bandit | security anti-patterns |
| `uv run task deadcode` | vulture | unreachable code at ≥ 80 % confidence |
| `uv run task test` | pytest | the suite, plus the count and coverage floors |
| `uv run task audit` | pip-audit | known vulnerabilities (needs network; not in `check_all`) |

Two floors are enforced and both are real gates — they exit non-zero, not just print. The
numbers below are quoted in exactly these words in
[README.md](README.md#the-two-floors) and asserted against each other *and* against this
run by `tests/test_docs.py`, so they cannot quietly go stale in one document while the other
keeps claiming to be right:

- **Coverage** of `src/` and `api/`: measured **100.00 %**, floor **98 %**
  (`fail_under` in `[tool.coverage.report]`).
- **Tests collected**: measured **300**, floor **283** (`MIN_TESTS` in `tests/conftest.py`).

To raise either one, add the tests that earn it in the same commit, then update both
documents with the new measurement. Never reach a number by adding `# pragma: no cover`, an
exclusion, a `skip` or an `xfail`; `tests/test_docs.py` fails if a `# pragma: no cover`
directive appears anywhere in the tree, and fails if any `[tool.coverage.report].exclude_also`
pattern actually matches a line in `src/` or `api/`.

## Adding a matching tier

The tiers live in one loop in `map_ledger_to_payload` in `src/services/reconciliation.py`.
Before adding one:

1. **Decide where it belongs.** A new tier goes *above* any tier it is more reliable than.
   The existing order is roughly: how much do we trust an exact agreement on all three
   fields, down to how little do we trust a single amount agreement. If your tier is less
   reliable than an existing one, it goes below it, and you need a reason.
2. **Use the candidate set, not the ledger.** Tier 1 filters `inv_by_num` and then marks each
   matched row in `mapped`. A tier that scans `available_rows` is a rescans-whole-ledger bug
   in waiting, which is the performance problem that restructure fixed.
3. **Handle ambiguity explicitly.** A tier that can match on a non-unique field has to decide
   what to do when two rows agree, and the answer must be in a test. The amount-only and
   date-only tiers both require `len(...) == 1`, and so does the bare fuzzy bucket:
   `matches_fuzzy_num` is only taken when exactly one row is a candidate. (It used not to be,
   which let the fuzzy tier undo the 1-way exact-number guard — see
   [README.md](README.md#a-number-two-rows-share-is-not-a-match). Ledger order alone is not
   evidence, and "the fuzzy bucket is exempt" is not an answer, it is the bug.)
4. **Write the test first, for the disagreement case.** For every tier, test what happens
   when the distinguishing field matches two rows. A tier that has only a happy-path test is
   a tier nobody has checked.
5. **Update `docs/report_processing_rules.md` and the README's matching section** in the same
   commit. Both list the tiers, and a tier documented in neither is invisible.

Then run `uv run task check_all`. If the coverage floor fails because the new branch is
untested, that is the floor working.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), which is what
`git log --oneline` in this repository already uses:

```
<type>(optional scope): <imperative summary under ~72 chars>

<body: what changed and why, wrapped at 72 columns>
```

Types in use here: `feat`, `fix`, `test`, `ci`, `docs`, `chore`, `perf`, `refactor`.
Use `!` and a `BREAKING CHANGE:` footer for a change a caller can feel — a response field
changing JSON type is exactly that.

Two examples of the body style, because the reason is the part that matters:

```
fix: enforce the declared type of fusion_invoice_amount on assignment

InvoiceItem.fusion_invoice_amount is annotated float | None, but pydantic does
not validate on assignment, so the field shipped as the string "9,500.25" inside
every reconciled response.
```

```
chore(deps): bump uv and re-export requirements.txt

Regenerated so the Vercel runtime installs the same pins the lockfile holds.
```

## Pull requests

- One logical change per PR. A refactor and a behaviour change in the same commit cannot be
  reviewed and cannot be reverted independently.
- Say in the description which gates you ran and what they said. If you could not run one,
  say so.
- Update the documentation in the same PR. This repository treats a code change without a
  doc change as unfinished.
- Never `git commit --amend` or force-push to `main`. If a commit is on `main`, add another.

## Reporting a security issue

Do not open a public issue. See [SECURITY.md](SECURITY.md#reporting-a-vulnerability).
