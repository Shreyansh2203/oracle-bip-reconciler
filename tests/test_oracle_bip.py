import pytest
import respx
import httpx
import base64
from src.services.oracle_bip import run_bip_bulk_match

@pytest.mark.asyncio
async def test_run_bip_bulk_match_success(mock_httpx_client):
    csv_data = "TransactionNumber,Amount\nINV-001,100.0\nINV-002,200.0\n"
    encoded_csv = base64.b64encode(csv_data.encode("utf-8")).decode("utf-8")
    
    mock_response = {
        "reportBytes": encoded_csv
    }

    with respx.mock:
        respx.post(url__regex=r".*/xmlpserver/services/rest/v1/reports/.*/run").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        
        invoice_numbers = ["INV-001", "INV-002"]
        result = await run_bip_bulk_match(mock_httpx_client, "user", "pass", invoice_numbers)
        
        assert len(result) == 2
        assert result["INV-001"]["Amount"] == "100.0"
        assert result["INV-002"]["Amount"] == "200.0"

@pytest.mark.asyncio
async def test_run_bip_bulk_match_missing_bytes(mock_httpx_client):
    mock_response = {"other_data": "foo"}

    with respx.mock:
        respx.post(url__regex=r".*/xmlpserver/services/rest/v1/reports/.*/run").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        
        result = await run_bip_bulk_match(mock_httpx_client, "user", "pass", ["INV-001"])
        assert result == {}
