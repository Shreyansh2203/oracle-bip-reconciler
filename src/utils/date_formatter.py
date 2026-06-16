from __future__ import annotations

import re
from datetime import datetime

def format_oracle_date(date_str: str) -> str:
    """
    Parses various date formats from the incoming JSON payload and converts them
    to the strict YYYY-MM-DD format required by Oracle Cloud ERP REST APIs.
    """
    if not date_str:
        return ""

    date_str = str(date_str).strip()

    # Try ISO format first (handles Timezones natively in 3.11+)
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    date_str = date_str.replace('/', '-')

    date_str = re.sub(r'\+00:00$', 'Z', date_str)

    # NOTE on format ordering: DD-MM-YYYY is tried before MM-DD-YYYY because
    # this system integrates with Oracle ERP in an India locale where day-first
    # date formats are the norm. For ambiguous dates where day <= 12
    # (e.g. "06-03-2026"), this will parse as 6th March, not June 3rd.
    formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ"
    ]

    for fmt in formats:
        try:
            d = datetime.strptime(date_str, fmt)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # If all parsing fails, return "" to let Oracle/fallback logic safely bypass it without false-positives.
    return ""


def format_bip_date(date_str: str) -> str:
    """
    Converts a date string to DD-MM-YYYY format required by Oracle BIP report parameters.
    Uses format_oracle_date internally for normalization, then reformats.
    """
    normalized = format_oracle_date(date_str)
    if not normalized:
        return ""
    try:
        dt = datetime.strptime(normalized, "%Y-%m-%d")
        return dt.strftime("%d-%m-%Y")
    except ValueError:
        return ""
