from __future__ import annotations

import logging
import re
from datetime import datetime

logger = logging.getLogger("reconciliation_api.date_formatter")

# `05-06-2026`, `5/6/26`, `05.06.2026`: a two-digit field, a separator, another two-digit
# field, a separator, then a two- or four-digit year. The year is anchored so the ISO and
# compact forms (`2026-05-06`, `20261005`) are not captured here.
NUMERIC_ORDER_RE = re.compile(r"^(\d{1,2})([-/.])(\d{1,2})\2(\d{2}|\d{4})$")

# Tried in order. Nothing here depends on where a day/month order sits in the list any
# more: an ambiguous numeric order is rejected before the list is consulted, so the only
# numeric spellings that reach it have a field above 12 (or a first field that cannot be a
# day) and only one format can apply to them. The year-width variants are grouped together
# for the same reason: `05-06-2026` and `05-06-26` are the same ambiguity, and used to
# resolve differently.
FORMATS = [
    # ISO / Oracle canonical
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    # Day first / month first, four-digit year
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%m-%d-%Y",
    "%m/%d/%Y",
    "%m.%d.%Y",
    # The same orders with a two-digit year
    "%d-%m-%y",
    "%d/%m/%y",
    "%d.%m.%y",
    "%m-%d-%y",
    "%m/%d/%y",
    "%m.%d.%y",
    # Month name variants
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%d %b %y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%B %d, %Y",
    "%B %d %Y",
    # Compact (YYYYMMDD)
    "%Y%m%d",
]


def is_ambiguous_numeric_date(date_string: str) -> bool:
    """True when a numeric day/month order could be read two ways and the two differ.

    `05-06-2026` is either 6 May or 5 June and the string carries nothing that decides it.
    A day or a month above 12 decides it, and so does a first field that is not a valid day.
    """
    match = NUMERIC_ORDER_RE.match(date_string)
    if not match:
        return False
    first, second = int(match.group(1)), int(match.group(3))
    if first == second:
        # Both readings are the same calendar day, so nothing is at stake.
        return False
    return first <= 12 and second <= 12


def format_oracle_date(date_string: str | None) -> str | None:
    """
    Parses various date formats from the incoming JSON payload and converts them
    to the strict YYYY-MM-DD format required by Oracle Cloud ERP REST APIs.
    Returns None when the value cannot be parsed, including when it is ambiguous.

    A numeric order that reads two ways is rejected rather than resolved by list order.
    The caller's fallback for a None here is a raw string comparison, which can only fail
    to match -- so a refused date costs an agreement it might have had, while a guessed
    one binds the line to whichever ledger row the guess happened to hit. Callers see the
    refusal in the log rather than as a silently wrong match.
    """
    if date_string is None:
        return None

    s = str(date_string).strip()
    if not s:
        return None

    # Strip trailing timestamp portions (e.g., T12:30:00 or 12:30:00)
    s = re.sub(r"[T\s]+\d{2}:\d{2}:\d{2}.*$", "", s).strip()

    if is_ambiguous_numeric_date(s):
        logger.warning(
            "Refusing ambiguous numeric date %r: the day and month are both valid and "
            "the spelling does not say which is which. Send an ISO date (2026-06-05), a "
            "day above 12 (13-06-2026), or a month name (05-Jun-2026).",
            s,
        )
        return None

    for date_format in FORMATS:
        try:
            parsed_date_from_format = datetime.strptime(s, date_format)
            return parsed_date_from_format.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None
