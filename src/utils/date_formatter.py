from datetime import datetime


def format_oracle_date(date_str: str) -> str:
    """
    Parses various date formats from the incoming JSON payload and converts them
    to the strict YYYY-MM-DD format required by Oracle Cloud ERP REST APIs.
    """
    if not date_str:
        return ""

    date_str = str(date_str).strip()
    date_str = date_str.replace('/', '-')

    formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ",
        "%d-%m-%y", "%m-%d-%Y"
    ]

    for fmt in formats:
        try:
            d = datetime.strptime(date_str, fmt)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # If all parsing fails, return the original string to let Oracle handle it (or fail).
    return date_str
