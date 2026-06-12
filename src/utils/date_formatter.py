from datetime import datetime
import re

def format_oracle_date(date_str: str) -> str:
    """
    Parses various date formats from the incoming JSON payload and converts them
    to the strict YYYY-MM-DD format required by Oracle Cloud ERP REST APIs.
    """
    if not date_str:
        return ""

    date_str = str(date_str).strip()
    date_str = date_str.replace('/', '-')

    date_str = re.sub(r'\+00:00$', 'Z', date_str)

    formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%m-%d-%Y"
    ]

    for fmt in formats:
        try:
            d = datetime.strptime(date_str, fmt)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # If all parsing fails, return "" to let Oracle/fallback logic safely bypass it without false-positives.
    return ""
