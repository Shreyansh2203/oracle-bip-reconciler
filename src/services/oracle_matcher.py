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


def get_amount_due_remaining(candidate: dict[str, Any]) -> float:
    return safe_parse_amount(candidate.get("AMOUNT_DUE_REMAINING"))


def get_transaction_total(candidate: dict[str, Any]) -> float:
    return safe_parse_amount(candidate.get("TRANSACTION_TOTAL"))


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

from typing import Any
import math


from datetime import datetime, timedelta

def score_receipt_candidate(
    candidate: dict[str, Any],
    receipt_number: str | None,
    amount: float | None,
    formatted_date: str | None,
) -> int:
    score = 0
    
    # 1. Reference Matching (Primary Identifier)
    if receipt_number:
        cand_num = candidate.get("RECEIPT_NUMBER")
        if safe_str_match(cand_num, receipt_number) or safe_fuzzy_reference_match(cand_num, receipt_number):
            score += 50
            
    # 2. Amount Matching
    if amount is not None:
        cand_amt = safe_parse_amount(candidate.get("RECEIPT_AMOUNT"))
        if safe_float_match(cand_amt, amount):
            score += 30
        else:
            # Check for standard bank fees ($25 or 1%)
            diff = abs(cand_amt - amount)
            if diff <= 25.00 or diff <= (amount * 0.01):
                score += 15

    # 3. Date Matching
    if formatted_date:
        cand_date_str = str(candidate.get("RECEIPT_DATE") or "").strip()
        if safe_str_match(cand_date_str, formatted_date):
            score += 20
        else:
            # Check for +/- 3 days ACH delay
            try:
                cand_dt = datetime.strptime(cand_date_str, "%Y-%m-%d")
                target_dt = datetime.strptime(formatted_date, "%Y-%m-%d")
                if abs((cand_dt - target_dt).days) <= 3:
                    score += 10
            except ValueError:
                pass
                
    return score

def match_receipt_in_memory(
    receipt_number: str, amount: float | None, receipt_date: str, customer_name: str, index: OracleReceiptIndex
) -> dict[str, Any]:
    if amount is not None and not math.isfinite(amount):
        return {"matched_in_oracle": False, "error": f"Invalid amount: {amount} (must be a finite number)"}

    if not index.bip_receipts:
        return {"matched_in_oracle": False, "error": "No receipt matches returned from Oracle Batch."}

    formatted_date = format_oracle_date(receipt_date) if receipt_date else None

    best_match = None
    best_score = -1

    for phase_num in [PHASE_UNAPPLIED, PHASE_APPLIED]:
        target_status = STATUS_UNAPPLIED if phase_num == PHASE_UNAPPLIED else STATUS_APPLIED
        candidates = [
            c for c in index.bip_receipts if get_receipt_phase(c.get("RECEIPT_STATUS_CODE", "")) == target_status
        ]

        for c in candidates:
            # We already know these belong to the correct customer because they are from the customer's ledger
            # Just score them based on reference, amount, and date fit
            score = score_receipt_candidate(c, receipt_number, amount, formatted_date)
            
            # Minimum confidence threshold (must match reference OR perfectly match date+amount)
            if score >= 50 and score > best_score:
                best_score = score
                best_match = c

        if best_match:
            return _build_receipt_response(best_match, f"Best-Fit Scoring ({best_score} pts)")

    return {"matched_in_oracle": False, "error": "No receipt met the minimum Best-Fit score threshold (50 pts)."}


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


def calculate_invoice_cost(
    candidate: dict[str, Any],
    invoice_number: str | None,
    invoice_date: str | None,
    invoice_amount: float | None,
) -> float:
    cost = 0.0

    # 1. Number Matching
    if invoice_number:
        cand_num = str(candidate.get("TRANSACTION_NUMBER") or "").strip()
        if safe_str_match(cand_num, invoice_number):
            cost += 0.0
        elif invoice_number in cand_num or cand_num in invoice_number:
            cost += 50.0
        else:
            cost += 10000.0

    # 2. Date Matching
    if invoice_date:
        formatted_date = format_oracle_date(invoice_date)
        if formatted_date:
            cand_date_str = str(candidate.get("TRANSACTION_DATE") or "").strip()
            if not safe_str_match(cand_date_str, formatted_date):
                try:
                    cand_dt = datetime.strptime(cand_date_str, "%Y-%m-%d")
                    target_dt = datetime.strptime(formatted_date, "%Y-%m-%d")
                    days_diff = abs((cand_dt - target_dt).days)
                    cost += days_diff * 10.0
                except ValueError:
                    cost += 500.0

    # 3. Amount Matching
    if invoice_amount is not None:
        amount_due = get_amount_due_remaining(candidate)
        trans_total = get_transaction_total(candidate)
        diff_due = abs(amount_due - invoice_amount)
        diff_total = abs(trans_total - invoice_amount)
        best_diff = min(diff_due, diff_total)
        
        # If amount matches perfectly, huge reward (negative cost)
        if best_diff == 0:
            cost -= 100.0
        else:
            cost += best_diff

    return cost


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
                and get_invoice_phase(c.get("INVOICE_STATUS"), get_amount_due_remaining(c)) == STATUS_OPEN
            )
            or (
                phase_num == PHASE_CLOSED_OR_OTHER
                and get_invoice_phase(c.get("INVOICE_STATUS"), get_amount_due_remaining(c)) != STATUS_OPEN
            )
        ]
        if not candidates:
            continue

        valid_candidates = []
        for c in candidates:
            cost = calculate_invoice_cost(c, invoice_number, invoice_date, amount)
            if cost < 5000.0:  # Threshold for acceptability
                valid_candidates.append((cost, c))
                
        valid_candidates.sort(key=lambda x: x[0])
        unique_results = []
        seen = set()
        for cost, c in valid_candidates:
            tnum = c.get("TRANSACTION_NUMBER")
            if tnum not in seen:
                seen.add(tnum)
                unique_results.append((cost, c))

        if len(unique_results) >= 1:
            best_cost, best_match = unique_results[0]
            # Only return single match if it is distinctively better than others, or if it's the only one
            if len(unique_results) == 1 or unique_results[1][0] - best_cost > 10.0:
                return _build_invoice_response(best_match, f"Best-Fit Cost Matrix (Cost: {best_cost:.2f})")

    return {"matched_in_oracle": False, "error": "No single match found after strict cross-validation."}


def _build_invoice_response(match: dict[str, Any], rule_name: str) -> dict[str, Any]:
    trans_total = match.get("TRANSACTION_TOTAL", "")
    parsed_total = None
    if trans_total:
        try:
            parsed_total = float(str(trans_total).replace(",", ""))
        except ValueError:
            pass

    amount_due = match.get("AMOUNT_DUE_REMAINING", "")
    parsed_due = None
    if amount_due:
        try:
            parsed_due = float(str(amount_due).replace(",", ""))
        except ValueError:
            pass

    return {
        "matched_in_oracle": True,
        "fusion_invoice_number": match.get("TRANSACTION_NUMBER"),
        "fusion_invoice_date": match.get("TRANSACTION_DATE"),
        "fusion_invoice_amount": parsed_total,
        "match_phase": get_invoice_phase(match.get("INVOICE_STATUS", STATUS_OTHER), parsed_due),
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
        c_phase = get_invoice_phase(c.get("INVOICE_STATUS"), get_amount_due_remaining(c))
        if phase == PHASE_OPEN and c_phase == STATUS_OPEN:
            candidates.append(c)
        elif phase == PHASE_CLOSED_OR_OTHER and c_phase != STATUS_OPEN:
            candidates.append(c)

    if not candidates:
        return {}

    n_payload = len(payload_invoices)
    n_oracle = len(candidates)

    cost_matrix = np.full((n_payload, n_oracle), 1000000.0)

    for i, p_inv in enumerate(payload_invoices):
        inv_num = str(p_inv.invoice_number).strip() if p_inv.invoice_number else None
        inv_date = str(p_inv.invoice_date).strip() if p_inv.invoice_date else None
        inv_amt = p_inv.invoice_amount

        for j, o_inv in enumerate(candidates):
            cost_matrix[i, j] = calculate_invoice_cost(o_inv, inv_num, inv_date, inv_amt)

    if _HAS_SCIPY:
        from scipy.optimize import linear_sum_assignment

        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        results = {}
        for i, j in zip(row_ind, col_ind):
            if cost_matrix[i, j] < 5000.0:
                results[i] = _build_invoice_response(candidates[j], f"Best-Fit Bipartite (Cost: {cost_matrix[i, j]:.2f})")
        return results
    return {}
