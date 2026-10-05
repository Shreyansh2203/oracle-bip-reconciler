import os

import pytest

# Settings() is constructed at import time in src.core.config, so the suite needs
# these three values to collect. They are set unconditionally rather than with
# setdefault so a developer .env can never supply real credentials to a test run.
os.environ["ORACLE_URL"] = "https://oracle.test.invalid"
os.environ["ORACLE_USER"] = "test-service-account"
os.environ["ORACLE_PASS"] = "test-service-account-password"

# The reconciliation endpoint fails closed unless an operator explicitly opts in, because it
# returns a named customer's whole ledger and the service account is tenant-wide. The tests
# that exercise the endpoint need it open, so it is opened here for the whole suite and the
# tests that assert the guard turn it off with monkeypatch.
os.environ["ALLOW_UNAUTHENTICATED_ACCESS"] = "true"

# A coverage floor says "this much of the code is exercised". It says nothing about the
# suite itself shrinking, which is the other way a project quietly loses its safety net: a
# refactor deletes twenty tests and every gate still passes. This is the floor for that.
#
# Set just below the current count so it catches a deletion rather than a rounding
# difference, and raised only in the commit that adds the tests that earn it.
MIN_TESTS = 283


def pytest_collection_modifyitems(config, items):
    # -k, -m and --collect-only narrow a run on purpose. The floor applies to a full
    # collection, which is what `uv run task test` and CI both do.
    if config.option.keyword or config.option.markexpr or config.option.collectonly:
        return

    if len(items) < MIN_TESTS:
        pytest.exit(
            f"Only {len(items)} tests were collected, below the floor of {MIN_TESTS}. "
            "If that is deliberate, raise MIN_TESTS in tests/conftest.py in the same commit "
            "and say why in the message.",
            returncode=1,
        )
