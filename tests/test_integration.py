import pytest
import respx
import httpx
import base64
import httpx
from fastapi.testclient import TestClient
from src.main import app
from src.models import ReconciliationRequest, InvoiceItem

client = TestClient(app)

@pytest.fixture
def override_env(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "test_user")
    monkeypatch.setenv("ORACLE_PASS", "test_pass")
    monkeypatch.setenv("API_KEY", "test_key")

@pytest.mark.asyncio
async def test_reconcile_integration_hybrid(override_env):
    """
    End-to-end integration test spanning the full hybrid pipeline:
    - BIP successfully matches 1 invoice
    - BIP misses 1 invoice -> Falls back to REST
    - REST successfully matches the fallback invoice
    """
    
    # We must mock the httpx client used inside main.py 
    # Since main.py uses a global http_client initialized in lifespan, we mock it using respx.
    with respx.mock:
        csv_text = "TRANSACTION_NUMBER,TRANSACTION_DATE,TOTAL_AMOUNTS\nINV-100,2026-05-10,150.0\n"
        bip_mock_json = {
            "reportBytes": base64.b64encode(csv_text.encode("utf-8")).decode("utf-8")
        }

        def side_effect(request):
            url_str = str(request.url)
            if "standardReceipts" in url_str:
                return httpx.Response(200, json={"items": [], "hasMore": False})
            if "xmlpserver" in url_str:
                return httpx.Response(200, json=bip_mock_json)
            if "receivablesInvoices" in url_str:
                return httpx.Response(200, json={
                    "items": [
                        {
                            "TransactionNumber": "INV-200",
                            "TransactionDate": "2026-05-11",
                            "EnteredAmount": 250.0,
                            "InvoiceStatus": "Open",
                            "InvoiceBalanceAmount": 250.0
                        }
                    ],
                    "hasMore": False
                })
            if "receivablesCreditMemos" in url_str:
                return httpx.Response(200, json={"items": [], "hasMore": False})
            return httpx.Response(404)

        respx.route(host="test.oracle.com").mock(side_effect=side_effect)

        # Create payload
        payload = {
            "customer_name": "Test Customer",
            "payment_reference": "REC-001",
            "payment_date": "2026-05-10",
            "total_amount": 400.0,
            "invoices": [
                {
                    "invoice_number": "INV-100",
                    "invoice_date": "2026-05-10",
                    "invoice_amount": 150.0
                },
                {
                    "invoice_number": "INV-200",
                    "invoice_date": "2026-05-11",
                    "invoice_amount": 250.0
                }
            ]
        }

        # Instead of TestClient which bypasses lifespan in some async contexts,
        # let's trigger it directly or ensure lifespan is running.
        with TestClient(app) as live_client:
            response = live_client.post(
                "/v1/reconcile",
                json=payload,
                headers={"X-API-Key": "test_key"}
            )

        assert response.status_code == 200
        data = response.json()
        
        # Verify BIP mapped invoice
        assert data["invoices"][0]["fusion_invoice_number"] == "INV-100"
        assert data["invoices"][0]["fusion_invoice_amount"] == 150.0

        # Verify REST mapped invoice
        assert data["invoices"][1]["fusion_invoice_number"] == "INV-200"
        assert data["invoices"][1]["fusion_invoice_amount"] == 250.0
