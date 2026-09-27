from __future__ import annotations

import logging
import math
import re

logger = logging.getLogger("reconciliation_api.validators")

# Currency marks and whitespace that may sit outside a number in an exported cell. They
# carry no magnitude, so they are removed rather than rejected.
_CURRENCY_SYMBOLS = " \t\u00a0$£€¥₹"

# A single decimal separator, with the integer part allowed to be absent (`,50`).
_DECIMAL_RE = re.compile(r"^([+-]?)(\d*)[.,](\d+)$")


def sanitize_string_val(value: str | int | None) -> str | None:
    """Sanitize string inputs: strip whitespace, convert None and 'none' to empty string."""
    if value is None:
        return None
    stripped = str(value).strip()
    if stripped.lower() == "none" or stripped == "":
        return None
    return stripped


def _strip_thousands(text: str, separator: str) -> str | None:
    """Drop thousands separators, or return None when the grouping is not well formed.

    Well formed means 1-3 leading digits and then groups of exactly three, so both
    ``1,234`` and ``1,234,567`` are accepted and ``1,23`` and ``,50`` are not. The caller
    passes the separator it has already found, so this does not have to re-derive it.
    """
    if separator == ",":
        head, _, tail = text.partition(",")
        groups = tail.split(",")
    else:
        head, _, tail = text.rpartition(".")
        groups = tail.split(".")
    if not head.isdigit() or len(head) > 3 or any(len(group) != 3 for group in groups):
        return None
    return head + "".join(groups)


def _reject(value: object) -> float | None:
    """Null a cell and make the refusal visible.

    Declared as returning ``float | None`` because callers use it as the return value; it
    only ever yields the None.

    Silently dropping a number is how a credit memo disappears from a reconciliation: the
    row still appears, its amount simply is not there any more. The caller is told in the
    log, and the caller's own fallback for None is a comparison that cannot match, so a
    refused amount costs an agreement rather than binding the wrong row.
    """
    logger.warning(
        "Refusing to read %r as a number. Accepted spellings are plain digits, an "
        "accounting negative such as (1,234.56), and one of 1,234.56 / 1.234,56 / 1234,56.",
        value,
    )
    return None


def _read_as_decimal(text: str) -> str | None:
    """Normalise a single decimal separator to a full stop, or return None.

    A bare `,50` has no integer part, so the leading group is allowed to be empty. The
    integer and fraction must both be digits, which is what refuses `1.2.3` and `1.`.
    """
    match = _DECIMAL_RE.match(text)
    if match is None:
        return None
    return f"{match.group(1)}{match.group(2) or '0'}.{match.group(3)}"


def sanitize_float_val(value: float | str | None) -> float | None:
    """Coerce a cell to a finite float, or return None and log why it could not be.

    The rules, in order:

    * an accounting negative -- ``(1,234.56)`` -- reads as ``-1234.56``. These are credit
      memos and refunds, and reading them as unreadable used to drop them silently.
    * when both ``.`` and ``,`` are present, the **rightmost** is the decimal separator and
      the other one groups thousands, so ``1,234.56`` and ``1.234,56`` both read as
      1234.56. This is decided from the separators' own positions, not from a locale.
    * a lone ``.`` is always a decimal point, because ``1.234`` out of a US-formatted
      Oracle cell is one-and-a-bit, and reading it as 1234 is a 1000x error.
    * a lone ``,`` groups thousands when what follows it is in threes (``1,234`` -> 1234)
      and is a decimal separator otherwise (``,50`` -> 0.5). Reading ``,50`` as 50 is a
      100x error, and ``1,23`` is not a spelling anyone exports.
    """
    if not isinstance(value, str):
        if value is None:
            return None
        try:
            float_value = float(value)
        except (TypeError, ValueError):
            return _reject(value)
        return float_value if math.isfinite(float_value) else None

    text = value.translate({ord(char): None for char in _CURRENCY_SYMBOLS}).strip()
    if text.lower() == "none" or text == "":
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text[:1] in ("-", "+"):
        negative = negative or text[0] == "-"
        text = text[1:].strip()

    if not text:
        return _reject(value)

    has_dot = "." in text
    has_comma = "," in text

    if has_dot and has_comma:
        # The rightmost separator is the decimal point; the other one groups thousands.
        decimal_at = max(text.rfind("."), text.rfind(","))
        thousands_sep = "." if text[decimal_at] == "," else ","
        whole = _strip_thousands(text[:decimal_at], thousands_sep)
        fraction = text[decimal_at + 1 :]
        if decimal_at == 0 or whole is None or not fraction.isdigit():
            return _reject(value)
        text = f"{whole}.{fraction}"
    elif has_comma:
        grouped = _strip_thousands(text, ",")
        if grouped is not None:
            text = grouped
        else:
            decimal = _read_as_decimal(text)
            if decimal is None:
                return _reject(value)
            text = decimal
    elif has_dot:
        # A lone dot is always a decimal point: 1.234 out of a US-formatted Oracle cell is
        # one-and-a-bit, and reading it as 1234 would be a 1000x error.
        decimal = _read_as_decimal(text)
        if decimal is None:
            return _reject(value)
        text = decimal
    else:
        try:
            plain = float(text)
        except ValueError:
            return _reject(value)
        return -plain if negative else (plain if math.isfinite(plain) else None)

    # Every branch above leaves a plain decimal literal of digits and at most one full stop,
    # so this cannot fail to parse. A very long run of digits still can be beyond float's
    # range, and an amount that is not finite is not an amount.
    float_value = float(text)
    if not math.isfinite(float_value):
        return None
    return -float_value if negative else float_value
