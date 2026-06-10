import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.services.oracle_matcher import check_receipt_cascading, check_invoice_cascading

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
    # Rule A1 is index 0. We'll make it return a match.
    # The queries array order: A1, A2, A3, A4, A5
    mock_client.get.side_effect = [
        create_mock_response(200, [{"ReceiptNumber": "REC123", "ReceiptDate": "2026-05-10", "CustomerName": "Test Customer"}]), # A1 match
        create_mock_response(200, []), # A2 no match
        create_mock_response(200, []), # A3
        create_mock_response(200, []), # A4
        create_mock_response(200, []), # A5
    ]
    
    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )
    
    assert result["matched_in_oracle"] is True
    assert result["fusion_receipt_number"] == "REC123"
    assert mock_client.get.call_count == 5  # gather fires all

@pytest.mark.asyncio
async def test_check_receipt_cascading_priority(mock_client):
    # If A1 AND A4 both match, it should return A1 because it has higher priority (idx 0 vs idx 3)
    # Even if A4 returns faster, asyncio.gather sorts by idx!
    
    # We simulate them all matching
    mock_client.get.side_effect = [
        create_mock_response(200, [{"ReceiptNumber": "A1_MATCH"}]),
        create_mock_response(200, [{"ReceiptNumber": "A2_MATCH"}]),
        create_mock_response(200, [{"ReceiptNumber": "A3_MATCH"}]),
        create_mock_response(200, [{"ReceiptNumber": "A4_MATCH"}]),
        create_mock_response(200, [{"ReceiptNumber": "A5_MATCH"}]),
    ]
    
    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )
    
    assert result["matched_in_oracle"] is True
    assert result["fusion_receipt_number"] == "A1_MATCH"

@pytest.mark.asyncio
async def test_check_receipt_no_match(mock_client):
    # Simulation where no rules find exactly 1 item
    mock_client.get.return_value = create_mock_response(200, [])
    
    result = await check_receipt_cascading(
        mock_client, "user", "pass", "REC123", 100.0, "2026-05-10", "Test Customer"
    )
    
    assert result["matched_in_oracle"] is False
    assert "No single match found" in result["error"]

@pytest.mark.asyncio
async def test_check_invoice_cascading_priority(mock_client):
    # Simulating all 5 queries returning a match. It should prioritize Rule 1a.
    mock_client.get.side_effect = [
        create_mock_response(200, [{"TrxNumber": "INV_1a", "InvoiceAmount": 100}]),
        create_mock_response(200, [{"TrxNumber": "INV_1b", "InvoiceAmount": 100}]),
        create_mock_response(200, [{"TrxNumber": "INV_2", "InvoiceAmount": 100}]),
        create_mock_response(200, [{"TrxNumber": "INV_3", "InvoiceAmount": 100}]),
        create_mock_response(200, [{"TrxNumber": "INV_4", "InvoiceAmount": 100}]),
    ]
    
    result = await check_invoice_cascading(
        mock_client, "user", "pass", "123", "2026-05-10", 100.0, "DOC1", "Cust"
    )
    
    assert result["matched_in_oracle"] is True
    assert result["fusion_invoice_number"] == "INV_1a"
