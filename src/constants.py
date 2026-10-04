from __future__ import annotations

import logging
import os

logger = logging.getLogger("reconciliation_api.constants")

# Networking Constants
DEFAULT_TIMEOUT = 15.0
MAX_CONNECTIONS = 1000
DEFAULT_CONCURRENCY = 100

# BIP Constants
BIP_TIMEOUT = 180.0
BIP_CHUNK_DOWNLOAD_SIZE = -1
BIP_MAX_RETRIES = 3
BIP_MIN_WAIT_SECONDS = 1
BIP_MAX_WAIT_SECONDS = 10
# Parsed here rather than in Settings because the cache is built at import time. A
# non-numeric value used to kill startup with an opaque ImportError-adjacent traceback;
# a bad TTL is a misconfiguration to warn about, not a reason the service cannot boot.
_ttl_str = os.getenv("BIP_CACHE_TTL_SECONDS", "60").strip()
try:
    BIP_CACHE_TTL_SECONDS = int(_ttl_str) if _ttl_str else 60
except ValueError:
    logger.warning("BIP_CACHE_TTL_SECONDS=%r is not a number of seconds; using 60.", _ttl_str)
    BIP_CACHE_TTL_SECONDS = 60
DEFAULT_INVOICE_REPORT_PATH = "/Custom/Shreyansh/Financials/Receivable Transactions/Upgrade/Get Invoice Details Report.xdo"
DEFAULT_RECEIPT_REPORT_PATH = "/Custom/Shreyansh/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo"

