import asyncio
import logging

import httpx
import pytest
import respx

import src.main
from src.main import _fetch_invoices_concurrently, app
from src.models import InvoiceItem, ReconciliationRequest
from src.services.oracle_matcher import check_invoice_cascading, fetch_oracle_candidates

logger = logging.getLogger(__name__)

# Test 1: Verification of the Pagination Cap
@pytest.mark.asyncio
async def test_pagination_cap_limit(oracle_context, monkeypatch):
    """
    Verify that when fetching oracle candidates, the system paginates up to MAX_PAGES (10) and then caps.
    """
    monkeypatch.setenv("ORACLE_MAX_PAGES", "10")
    page_count = 0

    with respx.mock:
        def side_effect(request):
            nonlocal page_count
            page_count += 1
            return httpx.Response(200, json={
                "items": [{"TransactionNumber": f"INV-{page_count}"}],
                "hasMore": True
            })

        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        results = await fetch_oracle_candidates(oracle_context, "receivablesInvoices", "TransactionNumber='123'")

        # Should stop after exactly 10 requests (MAX_PAGES = 10)
        assert page_count == 10
        assert len(results) == 10
        assert results[-1]["TransactionNumber"] == "INV-10"


# Test 2: Concurrency Lock Contention & Starvation Verification
@pytest.mark.asyncio
async def test_concurrency_lock_and_semaphore_contention(mock_httpx_client):
    """
    Test how check_invoice_cascading behaves with customer cache and locks.
    Verify that only 1 REST call is made for customer name fallback, even when many tasks run concurrently.
    We also inspect how the global semaphore limit works with lock contention.
    """
    # Set the global http_client in main to our mock_httpx_client
    src.main.http_client = mock_httpx_client

    customer_calls = 0
    app.state.oracle_sem = asyncio.Semaphore(5)  # low semaphore limit to verify contention

    with respx.mock:
        def side_effect(request):
            nonlocal customer_calls
            url_str = str(request.url)
            if "standardReceipts" in url_str:
                return httpx.Response(200, json={"items": [], "hasMore": False})
            if "receivablesCreditMemos" in url_str:
                return httpx.Response(200, json={"items": [], "hasMore": False})
            if "q=BillToCustomerName" in url_str:
                customer_calls += 1
                return httpx.Response(200, json={
                    "items": [
                        {
                            "TransactionNumber": "INV-CUST",
                            "TransactionDate": "2023-10-01",
                            "EnteredAmount": 100.0,
                            "InvoiceStatus": "Open",
                            "BillToCustomerName": "BigCorp"
                        }
                    ],
                    "hasMore": False
                })
            # Default fallback for prefix, transaction number, etc.
            return httpx.Response(200, json={"items": [], "hasMore": False})

        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        # Create a payload with 10 invoices, all fallback to the same customer name
        payload = ReconciliationRequest(
            customer_name="BigCorp",
            payment_reference="REC-123",
            total_amount=1000.0,
            payment_date="2023-10-01",
            invoices=[
                InvoiceItem(invoice_number=f"INV-ERR-{i}", invoice_amount=100.0, invoice_date="2023-10-01")
                for i in range(10)
            ]
        )

        unmatched = payload.invoices

        results = await _fetch_invoices_concurrently(payload, unmatched, "user", "pass", "BigCorp")

        # Verify that only 1 API call was made to Oracle for customer name fallback
        assert customer_calls == 1

        # Also verify that the lock correctly populated the customer cache so all tasks resolved
        assert len(results) == 10
        for r in results:
            assert r["matched_in_oracle"] is True
            assert r["fusion_invoice_number"] == "INV-CUST"


# Test 3: Short / Empty Prefix Search Stress
@pytest.mark.asyncio
async def test_short_prefix_search_inflation(mock_httpx_client):
    """
    Verify that if a very short invoice number prefix is queried, it fetches and processes large volumes.
    We test if the API handles prefix matching correctly without raising errors but warning about pages.
    """
    with respx.mock:
        def side_effect(request):
            url_str = str(request.url)
            if "receivablesInvoices" in url_str:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "TransactionNumber": f"INV-{i}",
                                "TransactionDate": "2023-10-01",
                                "EnteredAmount": 100.0,
                                "InvoiceStatus": "Open"
                            }
                            for i in range(50)
                        ],
                        "hasMore": False
                    }
                )
            return httpx.Response(200, json={"items": [], "hasMore": False})

        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        result = await check_invoice_cascading(
            mock_httpx_client, "user", "pass", "I", "2023-10-01", 100.0, "", ""
        )

        assert result["matched_in_oracle"] is False
        assert "No single match found" in result["error"]
