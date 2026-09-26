import os

# Settings() is constructed at import time in src.core.config, so the suite needs
# these three values to collect. They are set unconditionally rather than with
# setdefault so a developer .env can never supply real credentials to a test run.
os.environ["ORACLE_URL"] = "https://oracle.test.invalid"
os.environ["ORACLE_USER"] = "test-service-account"
os.environ["ORACLE_PASS"] = "test-service-account-password"
