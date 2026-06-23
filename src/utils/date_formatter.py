from __future__ import annotations

import re
from datetime import datetime


def format_oracle_date(date_string: str) -> str:
    """
    Parses various date formats from the incoming JSON payload and converts them
    to the strict YYYY-MM-DD format required by Oracle Cloud ERP REST APIs.
    """
    if not date_string:
        return ""

    date_string = str(date_string).strip()

    # Try ISO format first (handles Timezones natively in 3.11+)
    try:
        parsed_date = datetime.fromisoformat(date_string)
        return parsed_date.strftime("%Y-%m-%d")
    except ValueError:
        pass

    date_string = date_string.replace("/", "-")

    date_string = re.sub(r"\+00:00$", "Z", date_string)

    # NOTE on format ordering: MM-DD-YYYY is tried before DD-MM-YYYY to
    # conform to standard US locales, preventing ambiguous dates where day <= 12
    # (e.g. "06-03-2026") from incorrectly parsing as 6th March.
    date_formats = [
        "%Y-%m-%d",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    ]

    for date_format in date_formats:
        try:
            parsed_date_from_format = datetime.strptime(date_string, date_format)
            return parsed_date_from_format.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # If all parsing fails, return "" to let Oracle/fallback logic safely bypass it without false-positives.
    return ""


def format_bip_date(date_string: str) -> str:
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
