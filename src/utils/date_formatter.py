from __future__ import annotations

import re
from datetime import datetime


def format_oracle_date(date_string: str | None) -> str | None:
    """
    Parses various date formats from the incoming JSON payload and converts them
    to the strict YYYY-MM-DD format required by Oracle Cloud ERP REST APIs.
    Returns None when the value cannot be parsed.
    """
    if date_string is None:
        return None

    s = str(date_string).strip()
    if not s:
        return None

    # Strip trailing timestamp portions (e.g., T12:30:00 or 12:30:00)
    s = re.sub(r"[T\s]+\d{2}:\d{2}:\d{2}.*$", "", s).strip()

    # Ordered from most specific to least specific to avoid ambiguity
    formats = [
        # ISO / Oracle canonical
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        # US style
        "%m-%d-%Y", "%m/%d/%Y", "%m.%d.%Y",
        # Day first (common in invoices)
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
        # Two-digit year variants
        "%d-%m-%y", "%m-%d-%y",
        "%d/%m/%y", "%m/%d/%y",
        # Month name variants
        "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %b %y",
        "%b %d, %Y", "%b %d %Y",
        "%d-%B-%Y", "%d %B %Y", "%B %d, %Y", "%B %d %Y",
        # Compact (YYYYMMDD)
        "%Y%m%d",
    ]

    for date_format in formats:
        try:
            parsed_date_from_format = datetime.strptime(s, date_format)
            return parsed_date_from_format.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def format_bip_date(date_string: str | None) -> str:
    """
    Converts a date string to MM-DD-YYYY format required by Oracle BIP report parameters.
    Uses format_oracle_date internally for normalization, then reformats.
    """
    normalized = format_oracle_date(date_string)
    if not normalized:
        return ""
    try:
        parsed_date = datetime.strptime(normalized, "%Y-%m-%d")
        return parsed_date.strftime("%m-%d-%Y")
    except ValueError:
        return ""
