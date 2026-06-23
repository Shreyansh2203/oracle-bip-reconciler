from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    import Levenshtein
    from scipy.optimize import linear_sum_assignment

    _HAS_LEVENSHTEIN = True
    _HAS_SCIPY = True
except ImportError:
    _HAS_LEVENSHTEIN = False
    _HAS_SCIPY = False


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


def safe_float_match(value1: Any, value2: Any, allow_abs: bool = True) -> bool:
    if value1 is None or value2 is None:
        return False
    try:
        v1_str = str(value1).replace(",", "").strip()
        v2_str = str(value2).replace(",", "").strip()
        if not v1_str or not v2_str:
            return False
        try:
            d1 = Decimal(v1_str).quantize(Decimal("0.01"))
            d2 = Decimal(v2_str).quantize(Decimal("0.01"))
            if d1 == d2:
                return True
            if allow_abs and abs(d1) == abs(d2):
                return True
        except InvalidOperation:
            f1, f2 = float(v1_str), float(v2_str)
            if abs(f1 - f2) < 0.015:
                return True
            if allow_abs and abs(abs(f1) - abs(f2)) < 0.015:
                return True
        return False
    except (ValueError, TypeError):
        return False


def safe_str_match(value1: Any, value2: Any) -> bool:
    if value1 is None or value2 is None:
        return False
    return str(value1).strip().lower() == str(value2).strip().lower()


def safe_fuzzy_reference_match(value1: Any, value2: Any) -> bool:
    if value1 is None or value2 is None:
        return False
    v1_stripped = re.sub(r"[^a-zA-Z0-9]", "", str(value1)).lower().lstrip("0")
    v2_stripped = re.sub(r"[^a-zA-Z0-9]", "", str(value2)).lower().lstrip("0")
    if not v1_stripped or not v2_stripped:
        return False

    # Minimum 5 character entropy to prevent '12' matching '12345'
    if len(v1_stripped) < 5 and len(v2_stripped) < 5:
        return False

    return v1_stripped in v2_stripped or v2_stripped in v1_stripped


def safe_starts_with(full_value: Any, prefix_value: Any) -> bool:
    if full_value is None or prefix_value is None:
        return False
    return str(full_value).strip().lower().startswith(str(prefix_value).strip().lower())


def safe_customer_name_match(value1: Any, value2: Any) -> bool:
    if not value1 or not value2:
        return False
    v1_str = str(value1).strip().lower()
    v2_str = str(value2).strip().lower()
    if v1_str == v2_str:
        return True
    if len(v1_str) >= 10 and v1_str in v2_str:
        return True
    if len(v2_str) >= 10 and v2_str in v1_str:
        return True
    return False


def get_receipt_phase(status_code: str) -> str:
    unapplied_codes = ["UNAPP", "UNID", "UNAPPLIED", "UNIDENTIFIED"]
    if status_code and status_code.upper() in unapplied_codes:
        return STATUS_UNAPPLIED
    return STATUS_APPLIED


def get_invoice_phase(status_code: str, balance: Any) -> str:
    if status_code:
        return status_code.upper()
    try:
        if float(balance) != 0:
            return STATUS_OPEN
        return STATUS_CLOSED
    except (ValueError, TypeError):
        return STATUS_OTHER


def parse_oracle_date(date_str: str) -> datetime.date | None:
    formatted = format_oracle_date(date_str)
    if formatted:
        try:
            return datetime.strptime(formatted, "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def safe_parse_amount(amount_str: Any) -> float:
    if amount_str is None:
        return 0.0
    try:
        s = str(amount_str).replace(",", "").strip()
        if not s:
            return 0.0
        return float(s)
    except ValueError:
        return 0.0


def get_invoice_amount(candidate: dict[str, Any]) -> float:
    return safe_parse_amount(candidate.get("AMOUNT_DUE_REMAINING"))


# =========================================================================
# ADVANCED DATA STRUCTURES
# =========================================================================


# =========================================================================
# INDEX CLASSES
# =========================================================================


class OracleInvoiceIndex:
    def __init__(self, bip_invoices: list[dict[str, Any]]):
        self.bip_invoices = bip_invoices


class OracleReceiptIndex:
    def __init__(self, bip_receipts: list[dict[str, Any]]):
        self.bip_receipts = bip_receipts


# =========================================================================
# RECEIPT MATCHING
# =========================================================================


def _filter_receipt_candidates(
    candidates: list[dict[str, Any]],
    receipt_number: str | None = None,
    amount: float | None = None,
    formatted_date: str | None = None,
    customer_name: str | None = None,
    allow_fuzzy_ref: bool = False,
) -> list[dict[str, Any]]:
    filtered = []
    for candidate in candidates:
        cand_num = candidate.get("RECEIPT_NUMBER")
        num_matches = False
        if not receipt_number:
            num_matches = True
        else:
            if safe_str_match(cand_num, receipt_number):
                num_matches = True
            elif allow_fuzzy_ref and safe_fuzzy_reference_match(cand_num, receipt_number):
                num_matches = True

        if (
            num_matches
            and (amount is None or safe_float_match(candidate.get("RECEIPT_AMOUNT"), amount))
            and (not formatted_date or safe_str_match(candidate.get("RECEIPT_DATE"), formatted_date))
            and (not customer_name or safe_customer_name_match(candidate.get("BILL_CUSTOMER_NAME"), customer_name))
        ):
            filtered.append(candidate)

    # Deduplicate by RECEIPT_NUMBER so multiple lines (APP, REV) for the same receipt don't cause ambiguous match failures
    seen = set()
    deduped = []
    for c in filtered:
        rec_num = str(c.get("RECEIPT_NUMBER", "")).strip().upper()
        if rec_num not in seen:
            seen.add(rec_num)
            deduped.append(c)

    return deduped


def _apply_receipt_scenario_a(
    candidates: list[dict[str, Any]],
    receipt_number: str,
    amount: float | None,
    formatted_date: str | None,
    customer_name: str,
) -> dict[str, Any] | None:
    # A1 (Strict)
    results = _filter_receipt_candidates(candidates, receipt_number, amount, None, customer_name, allow_fuzzy_ref=False)
    if len(results) == 1:
        return _build_receipt_response(results[0], "A1")
    # A1 (Fuzzy Fallback - Safe because amount is strictly checked)
    if amount is not None:
        results = _filter_receipt_candidates(
            candidates, receipt_number, amount, None, customer_name, allow_fuzzy_ref=True
        )
        if len(results) == 1:
            return _build_receipt_response(results[0], "A1_FUZZY")

    # A2 (STRICT ONLY. We never allow fuzzy reference checking on Rule A2 because it does not verify amount)
    results = _filter_receipt_candidates(candidates, receipt_number, None, None, customer_name, allow_fuzzy_ref=False)
    if len(results) == 1:
        return _build_receipt_response(results[0], "A2")

    # A3 (Strict)
    results = _filter_receipt_candidates(
        candidates, receipt_number, amount, formatted_date, customer_name, allow_fuzzy_ref=False
    )
    if len(results) == 1:
        return _build_receipt_response(results[0], "A3")
    # A3 (Fuzzy Fallback - Safe because amount and date are strictly checked)
    if amount is not None and formatted_date is not None:
        results = _filter_receipt_candidates(
            candidates, receipt_number, amount, formatted_date, customer_name, allow_fuzzy_ref=True
        )
        if len(results) == 1:
            return _build_receipt_response(results[0], "A3_FUZZY")

    # A4
    if customer_name and amount is not None:
        results = _filter_receipt_candidates(candidates, None, amount, None, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "A4")

    return None


def _apply_receipt_scenario_b(
    candidates: list[dict[str, Any]], amount: float | None, formatted_date: str | None, customer_name: str
) -> dict[str, Any] | None:
    if amount is not None and formatted_date:
        results = _filter_receipt_candidates(candidates, None, amount, formatted_date, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "B1")

    if customer_name and amount is not None:
        results = _filter_receipt_candidates(candidates, None, amount, None, customer_name)
        if len(results) == 1:
            return _build_receipt_response(results[0], "B2")

    return None


def match_receipt_in_memory(
    receipt_number: str, amount: float | None, receipt_date: str, customer_name: str, index: OracleReceiptIndex
) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not index.bip_receipts:
        return {"matched_in_oracle": False, "error": "No receipt matches returned from Oracle Batch."}

    formatted_date = format_oracle_date(receipt_date) if receipt_date else None

    for phase_num in [PHASE_UNAPPLIED, PHASE_APPLIED]:
        target_status = STATUS_UNAPPLIED if phase_num == PHASE_UNAPPLIED else STATUS_APPLIED
        candidates = [
            c for c in index.bip_receipts if get_receipt_phase(c.get("RECEIPT_STATUS_CODE", "")) == target_status
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
        "fusion_applied_amount": safe_parse_amount(match.get("APPLIED_AMOUNT"))
        if match.get("APPLIED_AMOUNT") is not None
        else None,
        "match_phase": get_receipt_phase(match.get("RECEIPT_STATUS_CODE", "")),
        "match_rule": rule_name,
    }


# =========================================================================
# INVOICE MATCHING
# =========================================================================


def _apply_invoice_rules(
    candidates: list[dict[str, Any]],
    invoice_number: str,
    formatted_date: str | None,
    document_number: str,
    customer_name: str,
) -> dict[str, Any] | None:
    # Rule 1a: Num + Date
    if invoice_number and formatted_date:
        results = [
            candidate
            for candidate in candidates
            if safe_str_match(candidate.get("TRANSACTION_NUMBER"), invoice_number)
            and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)
        ]
        unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
        if len(unique_results) == 1:
            return _build_invoice_response(unique_results[0], "Rule 1a")

    # Rule 1b: Exact Num
    if invoice_number:
        results = [
            candidate for candidate in candidates if safe_str_match(candidate.get("TRANSACTION_NUMBER"), invoice_number)
        ]
        unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
        if len(unique_results) == 1:
            return _build_invoice_response(unique_results[0], "Rule 1b")

    # Rule 2: Doc Num + Date
    if document_number and formatted_date:
        results = [
            candidate
            for candidate in candidates
            if safe_str_match(candidate.get("DOCUMENT_NUMBER"), document_number)
            and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)
        ]
        unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
        if len(unique_results) == 1:
            return _build_invoice_response(unique_results[0], "Rule 2")

    # Rule 3: Prefix Match + Date
    if invoice_number and formatted_date:
        results = [
            candidate
            for candidate in candidates
            if safe_starts_with(candidate.get("TRANSACTION_NUMBER"), invoice_number)
            and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)
        ]
        unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
        if len(unique_results) == 1:
            return _build_invoice_response(unique_results[0], "Rule 3")

    # Rule 4: Customer + Date
    if customer_name and formatted_date:
        results = [
            candidate
            for candidate in candidates
            if safe_customer_name_match(candidate.get("BILL_CUSTOMER_NAME"), customer_name)
            and safe_str_match(candidate.get("TRANSACTION_DATE"), formatted_date)
        ]
        unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
        if len(unique_results) == 1:
            return _build_invoice_response(unique_results[0], "Rule 4")

    return None


def match_invoice_by_customer(
    invoice_number: str,
    invoice_date: str,
    amount: float | None,
    document_number: str,
    customer_name: str,
    index: OracleInvoiceIndex,
) -> dict[str, Any]:
    if not customer_name or not index.bip_invoices:
        return {"matched_in_oracle": False, "error": "No customer name provided or no data for customer search."}

    formatted_date = format_oracle_date(invoice_date) if invoice_date else None

    # Filter to records matching this customer name (broad match)
    customer_candidates = [
        c for c in index.bip_invoices if safe_customer_name_match(c.get("BILL_CUSTOMER_NAME"), customer_name)
    ]

    if not customer_candidates:
        return {"matched_in_oracle": False, "error": f"No records found for customer '{customer_name}'."}

    for phase_num in [PHASE_OPEN, PHASE_CLOSED_OR_OTHER]:
        candidates = [
            c
            for c in customer_candidates
            if (
                phase_num == PHASE_OPEN
                and get_invoice_phase(c.get("INVOICE_STATUS"), get_invoice_amount(c)) == STATUS_OPEN
            )
            or (
                phase_num == PHASE_CLOSED_OR_OTHER
                and get_invoice_phase(c.get("INVOICE_STATUS"), get_invoice_amount(c)) != STATUS_OPEN
            )
        ]
        if not candidates:
            continue

        # Standard rules without amount
        match = _apply_invoice_rules(candidates, invoice_number, formatted_date, document_number, customer_name)
        if match:
            match["match_rule"] = f"Cust+{match['match_rule']}"
            return match

        # Relaxed: Amt + Date
        if amount is not None and formatted_date:
            results = [
                c
                for c in candidates
                if safe_float_match(get_invoice_amount(c), amount)
                and safe_str_match(c.get("TRANSACTION_DATE"), formatted_date)
            ]
            unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
            if len(unique_results) == 1:
                return _build_invoice_response(unique_results[0], "Cust+AmtDate")

        # Relaxed: Amt only
        if amount is not None:
            results = [c for c in candidates if safe_float_match(get_invoice_amount(c), amount)]
            unique_results = list({c.get("TRANSACTION_NUMBER"): c for c in results}.values())
            if len(unique_results) == 1:
                return _build_invoice_response(unique_results[0], "Cust+Amt")

    return {"matched_in_oracle": False, "error": "No single match found after customer name search."}


def _build_invoice_response(match: dict[str, Any], rule_name: str) -> dict[str, Any]:
    amount_string = match.get("AMOUNT_DUE_REMAINING", "")

    parsed_amount = None
    if amount_string:
        try:
            parsed_amount = float(str(amount_string).replace(",", ""))
        except ValueError:
            pass

    return {
        "matched_in_oracle": True,
        "fusion_invoice_number": match.get("TRANSACTION_NUMBER"),
        "fusion_invoice_date": match.get("TRANSACTION_DATE"),
        "fusion_invoice_amount": parsed_amount,
        "match_phase": get_invoice_phase(match.get("INVOICE_STATUS", STATUS_OTHER), parsed_amount),
        "match_rule": rule_name,
    }


def match_invoices_bipartite(
    payload_invoices: list[Any], customer_name: str, index: OracleInvoiceIndex, phase: int = PHASE_OPEN
) -> dict[int, dict[str, Any]]:
    """Uses the Hungarian Algorithm to find the optimal assignment of payload invoices to Oracle invoices."""
    if not payload_invoices or not index.bip_invoices:
        return {}

    try:
        import numpy as np
    except ImportError:
        return {}  # If scipy/numpy missing, fallback gracefully

    candidates = []
    for c in index.bip_invoices:
        c_phase = get_invoice_phase(c.get("INVOICE_STATUS"), get_invoice_amount(c))
        if phase == PHASE_OPEN and c_phase == STATUS_OPEN:
            candidates.append(c)
        elif phase == PHASE_CLOSED_OR_OTHER and c_phase != STATUS_OPEN:
            candidates.append(c)

    if not candidates:
        return {}

    n_payload = len(payload_invoices)
    n_oracle = len(candidates)

    cost_matrix = np.full((n_payload, n_oracle), 1000000.0)
    match_rules = {}

    for i, p_inv in enumerate(payload_invoices):
        inv_num = str(p_inv.invoice_number) if p_inv.invoice_number else ""
        inv_date = str(p_inv.invoice_date) if p_inv.invoice_date else ""
        inv_amt = p_inv.invoice_amount
        doc_num = str(p_inv.customer_invoice_number) if p_inv.customer_invoice_number else ""

        fmt_date = format_oracle_date(inv_date) if inv_date else None
        date_p = parse_oracle_date(inv_date)

        for j, o_inv in enumerate(candidates):
            o_num = str(o_inv.get("TRANSACTION_NUMBER", "")).strip().lower()
            o_doc = str(o_inv.get("DOCUMENT_NUMBER", "")).strip().lower()
            o_date = str(o_inv.get("TRANSACTION_DATE", "")).strip().lower()
            o_cust = str(o_inv.get("BILL_CUSTOMER_NAME", "")).strip().lower()
            o_amt = get_invoice_amount(o_inv)

            best_score = 0
            best_rule = None

            # Amount tolerance checks (allowing absolute value matches for Credit Memos)
            amt_match = (inv_amt is None) or safe_float_match(o_amt, inv_amt, allow_abs=True)

            if not amt_match:
                continue

            # Rule 1a: Exact Num + Date
            if inv_num and fmt_date and safe_str_match(o_num, inv_num) and safe_str_match(o_date, fmt_date):
                score = 100
                if score > best_score:
                    best_score, best_rule = score, "Rule 1a"

            # Rule 1b: Exact Num
            if inv_num and safe_str_match(o_num, inv_num):
                score = 90
                if score > best_score:
                    best_score, best_rule = score, "Rule 1b"

            # Rule 2: Doc Num + Date
            if doc_num and fmt_date and safe_str_match(o_doc, doc_num) and safe_str_match(o_date, fmt_date):
                score = 85
                if score > best_score:
                    best_score, best_rule = score, "Rule 2"

            # Rule 3: Prefix Match + Date
            if inv_num and fmt_date and safe_starts_with(o_num, inv_num) and safe_str_match(o_date, fmt_date):
                score = 70
                if score > best_score:
                    best_score, best_rule = score, "Rule 3"

            # Rule 4: Customer + Date (using fuzzy string matching tolerance)
            if customer_name and fmt_date and safe_str_match(o_date, fmt_date):
                if safe_customer_name_match(o_cust, customer_name):
                    score = 60
                    if score > best_score:
                        best_score, best_rule = score, "Rule 4"
                elif _HAS_LEVENSHTEIN:
                    cust_len = len(customer_name.strip())
                    if cust_len > 3 and Levenshtein.distance(o_cust, customer_name.strip().lower()) <= min(
                        3, max(1, cust_len // 4)
                    ):
                        score = 55
                        if score > best_score:
                            best_score, best_rule = score, "Rule 4 (Fuzzy)"

            # Date Range Proximity Match
            if inv_amt is not None and fmt_date:
                date_o = parse_oracle_date(o_date)
                if date_p and date_o:
                    if abs((date_p - date_o).days) <= 1:
                        score = 50
                        if score > best_score:
                            best_score, best_rule = score, "Date Range Proximity Match"

            if best_score > 0:
                cost_matrix[i, j] = -best_score
                match_rules[(i, j)] = best_rule

    if _HAS_SCIPY:
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        results = {}
        for i, j in zip(row_ind, col_ind):  # noqa: B905
            if cost_matrix[i, j] < 0:
                results[i] = _build_invoice_response(candidates[j], match_rules[(i, j)])
        return results
    return {}
