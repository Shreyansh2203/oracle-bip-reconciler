import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.services.oracle_matcher import check_receipt_cascading, check_invoice_cascading, fetch_oracle_candidates

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
async def test_check_receipt_cascading_scenario_a_rule_a1(mock_client):
    # Oracle returns the candidate
    mock_client.get.return_value = create_mock_response(200, [{"ReceiptNumber": "REC123", "Amount": 100.0, "ReceiptDate": "2026-05-10", "CustomerName": "Test Customer"}])
    
    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )
    
    assert result["matched_in_oracle"] is True
    assert result["fusion_receipt_number"] == "REC123"

@pytest.mark.asyncio
async def test_check_receipt_no_match(mock_client):
    # Oracle returns an empty array
    mock_client.get.return_value = create_mock_response(200, [])
    
    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )
    
    assert result["matched_in_oracle"] is False
    assert "No candidates found" in result["error"]

@pytest.mark.asyncio
async def test_check_receipt_cascading_priority(mock_client):
    # Two candidates returned.
    # Candidate 1 matches A4 (Amount & CustomerName, but wrong ReceiptNumber)
    # Candidate 2 matches A2 (ReceiptNumber & CustomerName)
    # Rule A2 has higher priority than A4!
    mock_client.get.return_value = create_mock_response(200, [
        {"ReceiptNumber": "WRONG_REC", "Amount": 100.0, "CustomerName": "Test Customer"}, # matches A4
        {"ReceiptNumber": "REC123", "Amount": 999.0, "CustomerName": "Test Customer"}     # matches A2
    ])
    
    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )
    
    assert result["matched_in_oracle"] is True
    assert result["fusion_receipt_number"] == "REC123"

@pytest.mark.asyncio
async def test_check_invoice_cascading_priority(mock_client):
    # Two candidates returned.
    # Candidate 1 matches Rule 3 (Partial match TrxNumber, correct Date)
    # Candidate 2 matches Rule 1a (Exact TrxNumber match)
    mock_client.get.return_value = create_mock_response(200, [
        {"TrxNumber": "PREFIX_INV123_SUFFIX", "TrxDate": "2026-05-10"}, # Matches Rule 3
        {"TrxNumber": "INV123", "TrxDate": "2026-01-01"}                # Matches Rule 1a
    ])
    
    result = await check_invoice_cascading(
        mock_client, "user", "pass", "INV123", "2026-05-10", 100.0, "DOC1", "Cust"
    )
    
    assert result["matched_in_oracle"] is True
    assert result["fusion_invoice_number"] == "INV123"
