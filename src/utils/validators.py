from __future__ import annotations

import math
from typing import Any


def sanitize_string_val(value: str | int | None) -> str | None:
    """Sanitize string inputs: strip whitespace, convert None and 'none' to empty string."""
    if value is None:
        return None
    stripped = str(value).strip()
    if stripped.lower() == "none" or stripped == "":
        return None
    return stripped


def sanitize_float_val(value: Any) -> float | None:
    """Sanitize float inputs: strip commas, convert 'none' to None, validate finiteness."""
    if isinstance(value, str):
        clean_value = value.strip().replace(",", "")
        if clean_value.lower() == "none" or clean_value == "":
            return None
        value = clean_value
    if value is not None:
        try:
            float_value = float(value)
            if not math.isfinite(float_value):
                return None
            return float_value
        except (ValueError, TypeError):
            return None
    return value
