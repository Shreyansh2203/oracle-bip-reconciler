from __future__ import annotations

import math
from typing import Any


def sanitize_string_val(v: str | int | None) -> str:
    """Sanitize string inputs: strip whitespace, convert None and 'none' to empty string."""
    if v is None:
        return ""
    stripped = str(v).strip()
    if stripped.lower() == "none":
        return ""
    return stripped

def sanitize_float_val(v: Any) -> float | None:
    """Sanitize float inputs: strip commas, convert 'none' to None, validate finiteness."""
    if isinstance(v, str):
        v_clean = v.strip().replace(",", "")
        if v_clean.lower() == "none":
            return None
        v = v_clean
    if v is not None:
        try:
            f_val = float(v)
            if not math.isfinite(f_val):
                raise ValueError("Float value must be a finite number.")
            return f_val
        except (ValueError, TypeError):
            raise ValueError("Float value must be a finite number.") from None
    return v
