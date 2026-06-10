from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.oracle_matcher import (
    check_invoice_cascading,
    check_receipt_cascading,
    is_invoice_open,
    is_receipt_unapplied,
)


@pytest.fixture
def mock_client():
    client = AsyncMock()
    return client

def create_mock_response(status_code, items=None):
    response = MagicMock()
    response.status_code = status_code
    if items is not None:
        response.json.return_value = {"items": items}
    return response

@pytest.mark.asyncio
async def test_check_receipt_two_phase_priority(mock_client):
    # Two identical candidates but one is Applied and one is Unapplied.
    # The Unapplied one should be chosen first!
    mock_client.get.return_value = create_mock_response(200, [
        {"ReceiptNumber": "REC123", "Amount": 100.0, "State": "Applied", "CustomerName": "Test Customer", "ReceiptDate": "2026-05-10"},
        {"ReceiptNumber": "REC123", "Amount": 100.0, "State": "Unapplied", "CustomerName": "Test Customer", "ReceiptDate": "2026-05-10"}
    ])

    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )

    assert result["matched_in_oracle"] is True
    assert result["match_phase"] == "UNAPPLIED"

@pytest.mark.asyncio
async def test_check_receipt_fallback_to_applied(mock_client):
    # Only Applied candidate exists. It should gracefully fallback and match it.
    mock_client.get.return_value = create_mock_response(200, [
        {"ReceiptNumber": "REC123", "Amount": 100.0, "State": "Applied", "CustomerName": "Test Customer", "ReceiptDate": "2026-05-10"}
    ])

    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )

    assert result["matched_in_oracle"] is True
    assert result["match_phase"] == "APPLIED"

@pytest.mark.asyncio
async def test_check_invoice_two_phase_priority(mock_client):
    # Two invoices, one Closed, one Open.
    # The Open one should be chosen first.
    mock_client.get.return_value = create_mock_response(200, [
        {"TransactionNumber": "INV123", "InvoiceStatus": "Closed", "TransactionDate": "2026-05-10"},
        {"TransactionNumber": "INV123", "InvoiceStatus": "Incomplete", "TransactionDate": "2026-05-10"}
    ])

    result = await check_invoice_cascading(
        mock_client, "user", "pass", "INV123", "2026-05-10", 100.0, "DOC1", "Cust"
    )

    assert result["matched_in_oracle"] is True
    assert result["match_phase"] == "OPEN"

def test_is_receipt_unapplied():
    assert is_receipt_unapplied({"State": "Unapplied"}) is True
    assert is_receipt_unapplied({"State": "UNAPP"}) is True
    assert is_receipt_unapplied({"State": "Applied"}) is False
    assert is_receipt_unapplied({}) is False

def test_is_invoice_open():
    assert is_invoice_open({"InvoiceStatus": "Closed"}) is False
    assert is_invoice_open({"InvoiceStatus": "Incomplete"}) is True
    assert is_invoice_open({"InvoiceBalanceAmount": "100.5"}) is True
    assert is_invoice_open({"InvoiceBalanceAmount": "0.0", "InvoiceStatus": "Complete"}) is False
