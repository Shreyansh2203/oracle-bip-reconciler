import re
from datetime import datetime
from typing import Any


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

    formats = [
        "%Y-%m-%d", "%m-%d-%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
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



def safe_date_match(date1: Any, date2: Any) -> bool:
    if not date1 or not date2:
        return False
    d1 = format_oracle_date(str(date1))
    d2 = format_oracle_date(str(date2))
    return bool(d1) and bool(d2) and d1 == d2

