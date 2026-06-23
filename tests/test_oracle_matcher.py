import math

from src.services.oracle_matcher import (
    OracleReceiptIndex,
    _build_receipt_response,
    parse_oracle_date,
    match_receipt_in_memory,
)


def test_match_receipt_in_memory_success():
    bip_receipts = [
        {
            "RECEIPT_NUMBER": "REC-123",
            "RECEIPT_DATE": "2023-10-01",
            "RECEIPT_STATUS_CODE": "UNAPP",
            "BILL_CUSTOMER_NAME": "Customer A",
        }
    ]
    index = OracleReceiptIndex(bip_receipts)
    result = match_receipt_in_memory("REC-123", 100.0, "2023-10-01", "Customer A", index)
    assert result["matched_in_oracle"] is True
    assert result["fusion_receipt_number"] == "REC-123"


def test_parse_oracle_date_nan():
    # If invalid date is passed, it should return None
    val = parse_oracle_date("not-a-date")
    assert val is None

    # Valid date
    dt = parse_oracle_date("2026-05-10")
    assert dt is not None
    assert dt.year == 2026


def test_build_receipt_response_preserves_zero():
    # If APPLIED_AMOUNT is zero, it should be kept, not dropped to None
    match = {
        "RECEIPT_NUMBER": "123",
        "APPLIED_AMOUNT": 0.0,
        "RECEIPT_STATUS_CODE": "UNAPP"
    }
    resp = _build_receipt_response(match, "Rule 1")
    assert resp["fusion_applied_amount"] == 0.0

    match_str_zero = {
        "RECEIPT_NUMBER": "123",
        "APPLIED_AMOUNT": "0.00",
        "RECEIPT_STATUS_CODE": "UNAPP"
    }
    resp_str = _build_receipt_response(match_str_zero, "Rule 2")
    assert resp_str["fusion_applied_amount"] == 0.0

    match_none = {
        "RECEIPT_NUMBER": "123",
        "APPLIED_AMOUNT": None,
        "RECEIPT_STATUS_CODE": "UNAPP"
    }
    resp_none = _build_receipt_response(match_none, "Rule 3")
    assert resp_none["fusion_applied_amount"] is None
