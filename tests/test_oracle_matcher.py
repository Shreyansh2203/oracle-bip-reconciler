import asyncio
import base64
import httpx
import pytest
import respx

from src.services.oracle_matcher import (
    check_invoice_cascading,
    check_receipt_cascading,
)

@pytest.mark.asyncio
async def test_check_invoice_cascading_success(mock_httpx_client):
    csv_data = "TRANSACTION_NUMBER,TRANSACTION_DATE,TRANSACTION_TOTAL,INVOICE_STATUS\nINV-123,2023-10-01,100.00,OPEN\n"
    encoded_csv = base64.b64encode(csv_data.encode("utf-8")).decode("utf-8")
    mock_response = {"reportBytes": encoded_csv}

    with respx.mock:
        def side_effect(request):
            return httpx.Response(200, json=mock_response)
        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        result = await check_invoice_cascading(
            mock_httpx_client, "user", "pass", "INV-123", "2023-10-01", 100.0, "", ""
        )

        assert result["matched_in_oracle"] is True
        assert result["fusion_invoice_number"] == "INV-123"
        assert result["match_phase"] == "OPEN"
        assert result["fusion_invoice_amount"] == 100.0

@pytest.mark.asyncio
async def test_check_receipt_cascading_success(mock_httpx_client):
    csv_data = "RECEIPT_NUMBER,RECEIPT_DATE,BILL_CUSTOMER_NAME,RECEIPT_STATUS_CODE\nREC-123,2023-10-01,Customer A,UNAPP\n"
    encoded_csv = base64.b64encode(csv_data.encode("utf-8")).decode("utf-8")
    mock_response = {"reportBytes": encoded_csv}

    with respx.mock:
        def side_effect(request):
            return httpx.Response(200, json=mock_response)
        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        result = await check_receipt_cascading(
            mock_httpx_client, "user", "pass", "REC-123", 100.0, "2023-10-01", "Customer A"
        )

        assert result["matched_in_oracle"] is True
        assert result["fusion_receipt_number"] == "REC-123"
        assert result["match_phase"] == "UNAPPLIED"
