import pytest
import respx
import httpx
import asyncio
from src.services.oracle_matcher import check_invoice_cascading, check_receipt_cascading, safe_float_match, is_invoice_open, safe_str_match, fetch_by_query, OracleClientContext

def test_safe_float_match():
    assert safe_float_match(100.0, "100.00") is True
    assert safe_float_match(100.01, 100.01) is True
    assert safe_float_match(None, 100.0) is False
    assert safe_float_match(100.0, 200.0) is False

def test_is_invoice_open():
    assert is_invoice_open({"InvoiceStatus": "Open"}) is True
    assert is_invoice_open({"InvoiceStatus": "Closed"}) is False
    assert is_invoice_open({"InvoiceBalanceAmount": 10.0}) is True
    assert is_invoice_open({"InvoiceBalanceAmount": 0.0}) is False

def test_safe_str_match():
    assert safe_str_match("INV-123", "inv-123") is True
    assert safe_str_match("0", "0") is True
    assert safe_str_match("0", 0) is True
    assert safe_str_match("", "INV-123") is False
    assert safe_str_match(None, "INV-123") is False


@pytest.mark.asyncio
async def test_check_invoice_cascading_success(mock_httpx_client):
    mock_response = {
        "items": [
            {
                "TransactionNumber": "INV-123",
                "TransactionDate": "2023-10-01",
                "EnteredAmount": 100.0,
                "InvoiceStatus": "Open"
            }
        ],
        "hasMore": False
    }

    with respx.mock:
        def side_effect(request):
            url_str = str(request.url)
            if "receivablesCreditMemos" in url_str:
                return httpx.Response(200, json={"items": [], "hasMore": False})
            return httpx.Response(200, json=mock_response)
            
        respx.route(host="test.oracle.com").mock(side_effect=side_effect)

        result = await check_invoice_cascading(
            mock_httpx_client, "user", "pass", "INV-123", "2023-10-01", 100.0, "", ""
        )

        assert result["matched_in_oracle"] is True
        assert result["fusion_invoice_number"] == "INV-123"

@pytest.mark.asyncio
async def test_check_invoice_cascading_fallback(mock_httpx_client):
    empty_response = {"items": [], "hasMore": False}
    success_response = {
        "items": [
            {
                "TransactionNumber": "INV-123",
                "TransactionDate": "2023-10-01",
                "EnteredAmount": 100.0,
                "InvoiceStatus": "Open"
            }
        ],
        "hasMore": False
    }

    with respx.mock:
        calls = []
        def side_effect(request):
            url_str = str(request.url)
            calls.append(url_str)
            if "receivablesCreditMemos" in url_str:
                return httpx.Response(200, json=empty_response)
            if "q=TransactionNumber" in url_str:
                return httpx.Response(200, json=empty_response)
            if "q=DocumentNumber" in url_str:
                return httpx.Response(200, json=empty_response)
            if "q=BillToCustomerName" in url_str:
                return httpx.Response(200, json=success_response)
            return httpx.Response(200, json=empty_response)

        respx.route(host="test.oracle.com").mock(side_effect=side_effect)

        cache = {}
        lock = asyncio.Lock()
        result = await check_invoice_cascading(
            mock_httpx_client, "user", "pass", "INV-123", "2023-10-01", 100.0, "DOC-123", "Test Customer", cache, lock
        )

        assert result["matched_in_oracle"] is True
        assert any("q=TransactionNumber" in c for c in calls)
        assert any("q=DocumentNumber" in c for c in calls)
        assert any("q=BillToCustomerName" in c for c in calls)

@pytest.mark.asyncio
async def test_fetch_by_query_error_propagation(mock_httpx_client):
    context = OracleClientContext(mock_httpx_client, "user", "pass")
    
    with respx.mock:
        def side_effect(request):
            return httpx.Response(500, text="Internal Server Error")
        respx.route(host="test.oracle.com").mock(side_effect=side_effect)
        
        with pytest.raises(Exception) as excinfo:
            await fetch_by_query(context, "TransactionNumber='123'", "", "")
            
        assert "Both Invoice and CM fetch failed" in str(excinfo.value)
