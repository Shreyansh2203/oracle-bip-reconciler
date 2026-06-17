from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from src.constants import (
    PHASE_APPLIED,
    PHASE_CLOSED_OR_OTHER,
    PHASE_OPEN,
    PHASE_UNAPPLIED,
    STATUS_APPLIED,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_OTHER,
    STATUS_UNAPPLIED,
)
from src.utils.date_formatter import format_oracle_date

logger = logging.getLogger(__name__)

def safe_float_match(value1: Any, value2: Any) -> bool:
    try:
        if value1 is None or value2 is None:
            return False
        v1_str = str(value1).replace(",", "").strip()
        v2_str = str(value2).replace(",", "").strip()
        if not v1_str or not v2_str:
            return False
        d1 = Decimal(v1_str).quantize(Decimal('0.01'))
        d2 = Decimal(v2_str).quantize(Decimal('0.01'))
        return d1 == d2
    except (InvalidOperation, ValueError, TypeError):
        return False

def safe_str_match(value1: Any, value2: Any) -> bool:
    if value1 is None or value2 is None:
        return False
    return str(value1).strip().lower() == str(value2).strip().lower()

def safe_substring_match(value1: Any, value2: Any) -> bool:
    if not value1 or not value2:
        return False
    v1_str = str(value1).strip().lower()
    v2_str = str(value2).strip().lower()
    return v1_str in v2_str or v2_str in v1_str

def safe_starts_with(full_value: Any, prefix_value: Any) -> bool:
    if full_value is None or prefix_value is None:
        return False
    return str(full_value).strip().lower().startswith(str(prefix_value).strip().lower())

def safe_receipt_number_match(value1: Any, value2: Any) -> bool:
    if not value1 or not value2:
        return False
    v1_str = str(value1).strip().lower()
    v2_str = str(value2).strip().lower()
    if len(v1_str) < 4 or len(v2_str) < 4:
        return v1_str == v2_str
    return v1_str in v2_str or v2_str in v1_str

def safe_customer_name_match(value1: Any, value2: Any) -> bool:
    if not value1 or not value2:
        return False
    v1_str = str(value1).strip().lower()
    v2_str = str(value2).strip().lower()
    if v1_str == v2_str:
        return True
    if len(v1_str) >= 4 and v1_str in v2_str:
        return True
    if len(v2_str) >= 4 and v2_str in v1_str:
        return True
    return False

def escape_query_value(value: Any) -> str:
    """Escapes single quotes for Oracle REST query strings."""
    if value is None:
        return ""
    return str(value).replace("'", "''")

def get_receipt_phase(status_code: str) -> str:
    unapplied_codes = ["UNAPP", "UNID", "UNAPPLIED", "UNIDENTIFIED"]
    if status_code and status_code.upper() in unapplied_codes:
        return STATUS_UNAPPLIED
    return STATUS_APPLIED

def get_invoice_phase(status_code: str, balance: Any) -> str:
    if status_code:
        return status_code.upper()
    try:
        if float(balance) > 0:
            return STATUS_OPEN
        return STATUS_CLOSED
    except (ValueError, TypeError):
        return STATUS_OTHER

# =========================================================================
# IN-MEMORY BATCH MATCHING (APPROACH 1)
# =========================================================================

def _filter_receipt_candidates(
    candidates: list[dict[str, Any]],
    receipt_number: str | None = None,
    amount: float | None = None,
    formatted_date: str | None = None,
    customer_name: str | None = None,
    exact_receipt: bool = False
) -> list[dict[str, Any]]:
    return [
        candidate for candidate in candidates
        if (not receipt_number or (safe_str_match(candidate.get("RECEIPT_NUMBER"), receipt_number) if exact_receipt else safe_receipt_number_match(candidate.get("RECEIPT_NUMBER"), receipt_number)))
        and (amount is None or safe_float_match(candidate.get("RECEIPT_AMOUNT"), amount))
        and (not formatted_date or safe_str_match(candidate.get("RECEIPT_DATE"), formatted_date))
        and (not customer_name or safe_customer_name_match(candidate.get("BILL_CUSTOMER_NAME"), customer_name))
    ]

def _apply_receipt_scenario_a(candidates: list[dict[str, Any]], receipt_number: str, amount: float | None, formatted_date: str | None, customer_name: str) -> dict[str, Any] | None:
    # A1: Substring Num, Amount, [Customer]
    results = _filter_receipt_candidates(candidates, receipt_number, amount, None, customer_name)
    if len(results) == 1:
        return _build_receipt_response(results[0], "A1")

    # A2: Substring Num, [Customer]
    results = _filter_receipt_candidates(candidates, receipt_number, None, None, customer_name, exact_receipt=True)
    if len(results) == 1:
        return _build_receipt_response(results[0], "A2")

    # A3: Substring Num, Amount, Date, [Customer]
    results = _filter_receipt_candidates(candidates, receipt_number, amount, formatted_date, customer_name)
    if len(results) == 1:
        return _build_receipt_response(results[0], "A3")

    # A4: Customer, Amount
    if customer_name and amount is not None:
        results = _filter_receipt_candidates(candidates, None, amount, None, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "A4")

    # A5: Customer, Date
    if customer_name and formatted_date:
        results = _filter_receipt_candidates(candidates, None, None, formatted_date, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "A5")

    return None

def _apply_receipt_scenario_b(candidates: list[dict[str, Any]], amount: float | None, formatted_date: str | None, customer_name: str) -> dict[str, Any] | None:
    # B1: Amount, Date, [Customer]
    if amount is not None and formatted_date:
        results = _filter_receipt_candidates(candidates, None, amount, formatted_date, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "B1")

    # B2: Customer, Amount
    if customer_name and amount is not None:
        results = _filter_receipt_candidates(candidates, None, amount, None, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "B2")

    # B3: Customer, Date
    if customer_name and formatted_date:
        results = _filter_receipt_candidates(candidates, None, None, formatted_date, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "B3")

    return None

def match_receipt_in_memory(receipt_number: str, amount: float | None, receipt_date: str, customer_name: str, bip_receipts: list[dict[str, Any]]) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not bip_receipts:
        return {"matched_in_oracle": False, "error": "No receipt matches returned from Oracle Batch."}

    formatted_date = format_oracle_date(receipt_date) if receipt_date else None

    # Phase 1: Unapplied/Unidentified, Phase 2: Applied
    for phase_num in [PHASE_UNAPPLIED, PHASE_APPLIED]:
        target_status = STATUS_UNAPPLIED if phase_num == PHASE_UNAPPLIED else STATUS_APPLIED
        candidates = [
            candidate for candidate in bip_receipts
            if get_receipt_phase(candidate.get("RECEIPT_STATUS_CODE", "")) == target_status
        ]

        if not candidates:
            continue

        if receipt_number:
            match = _apply_receipt_scenario_a(candidates, receipt_number, amount, formatted_date, customer_name)
            if match:
                return match
        else:
            match = _apply_receipt_scenario_b(candidates, amount, formatted_date, customer_name)
            if match:
                return match

    return {"matched_in_oracle": False, "error": "No single match found after cascading rules"}

def _build_receipt_response(match: dict[str, Any], rule_name: str) -> dict[str, Any]:
    return {
        "matched_in_oracle": True,
        "fusion_receipt_number": match.get("RECEIPT_NUMBER"),
        "fusion_receipt_date": match.get("RECEIPT_DATE"),
        "fusion_customer_name": match.get("BILL_CUSTOMER_NAME"),
        "fusion_customer_number": match.get("BILL_CUSTOMER_NUMBER"),
        "fusion_currency": match.get("CURRENCY"),
        "fusion_receipt_status_code": match.get("RECEIPT_STATUS_CODE"),
        "fusion_applied_amount": match.get("APPLIED_AMOUNT") if match.get("APPLIED_AMOUNT") else None,
        "match_phase": get_receipt_phase(match.get("RECEIPT_STATUS_CODE", "")),
        "match_rule": rule_name
    }


def _apply_invoice_rules(candidates: list[dict[str, Any]], invoice_number: str, formatted_date: str | None, document_number: str, customer_name: str) -> dict[str, Any] | None:
    # Rule 1a: Num + Date
    if invoice_number and formatted_date:
        results = [candidate for candidate in candidates if safe_str_match(candidate.get("TRANSACTION_NUMBER"), invoice_number)
                and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)]
        if len(results) == 1:
            return _build_invoice_response(results[0], "Rule 1a")

    # Rule 1b: Exact Num
    if invoice_number:
        results = [candidate for candidate in candidates if safe_str_match(candidate.get("TRANSACTION_NUMBER"), invoice_number)]
        if len(results) == 1:
            return _build_invoice_response(results[0], "Rule 1b")

    # Rule 2: Doc Num + Date
    if document_number and formatted_date:
        results = [candidate for candidate in candidates if safe_str_match(candidate.get("DOCUMENT_NUMBER"), document_number)
                and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)]
        if len(results) == 1:
            return _build_invoice_response(results[0], "Rule 2")

    # Rule 3: Prefix Match + Date
    if invoice_number and formatted_date:
        results = [candidate for candidate in candidates if safe_starts_with(candidate.get("TRANSACTION_NUMBER"), invoice_number)
                and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)]
        if len(results) == 1:
            return _build_invoice_response(results[0], "Rule 3")

    # Rule 4: Customer + Date
    if customer_name and formatted_date:
        results = [candidate for candidate in candidates if safe_customer_name_match(candidate.get("BILL_CUSTOMER_NAME"), customer_name)
                and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)]
        if len(results) == 1:
            return _build_invoice_response(results[0], "Rule 4")

    return None

def match_invoice_in_memory(invoice_number: str, invoice_date: str, amount: float | None, document_number: str, customer_name: str, bip_invoices: list[dict[str, Any]]) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not bip_invoices:
        return {"matched_in_oracle": False, "error": "No customer invoices returned from Oracle Batch."}

    formatted_date = format_oracle_date(invoice_date) if invoice_date else None

    # Filter base candidates by amount constraint up front (implicit constraint)
    base_candidates = [candidate for candidate in bip_invoices if amount is None or safe_float_match(candidate.get("TOTAL_AMOUNTS"), amount)]

    # Phase 1: OPEN, Phase 2: CLOSED/OTHER
    for phase_num in [PHASE_OPEN, PHASE_CLOSED_OR_OTHER]:
        candidates = [
            candidate for candidate in base_candidates
            if (phase_num == PHASE_OPEN and get_invoice_phase(candidate.get("INVOICE_STATUS"), 0) == STATUS_OPEN) or
               (phase_num == PHASE_CLOSED_OR_OTHER and get_invoice_phase(candidate.get("INVOICE_STATUS"), 0) != STATUS_OPEN)
        ]

        if not candidates:
            continue

        match = _apply_invoice_rules(candidates, invoice_number, formatted_date, document_number, customer_name)
        if match:
            return match

    return {"matched_in_oracle": False, "error": "No single match found after cascading rules"}

def _build_invoice_response(match: dict[str, Any], rule_name: str) -> dict[str, Any]:
    amount_string = match.get("TOTAL_AMOUNTS", "")
    parsed_amount = None
    if amount_string:
        try:
            parsed_amount = float(amount_string.replace(",", ""))
        except ValueError:
            pass

    return {
        "matched_in_oracle": True,
        "fusion_invoice_number": match.get("TRANSACTION_NUMBER"),
        "fusion_invoice_date": match.get("TRANSACTION_DATE"),
        "fusion_invoice_amount": parsed_amount,
        "match_phase": get_invoice_phase(match.get("INVOICE_STATUS", STATUS_OTHER), 0),
        "match_rule": rule_name
    }


