import base64

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.core.config import settings
from src.main import app
from src.services.oracle_bip import _bip_cache, _inflight


def _soap(csv_text: str) -> str:
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    return (
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        '<pub:runReportResponse xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">'
        f"<pub:reportBytes>{encoded}</pub:reportBytes></pub:runReportResponse></soap:Body></soap:Envelope>"
    )


RECEIPT_CSV = (
    "RECEIPT_NUMBER,RECEIPT_DATE,RECEIPT_AMOUNT,CURRENCY,BILL_CUSTOMER_NAME\n"
    "RCPT-45021,2026-08-14,\"4,750.50\",USD,Acme Corp\n"
)
INVOICE_CSV = (
    "TRANSACTION_NUMBER,TRANSACTION_DATE,TRANSACTION_TOTAL,BILL_CUSTOMER_NAME\n"
    "INV-1,2026-08-14,\"1,250.00\",Acme Corp\n"
)

SOAP_URL = f"{settings.ORACLE_URL.rstrip('/')}/xmlpserver/services/ExternalReportWSSService"


@pytest.fixture(autouse=True)
def clear_bip_cache():
    _bip_cache.local.clear()
    _inflight.clear()
    yield
    _bip_cache.local.clear()
    _inflight.clear()


def test_end_to_end_reconcile_hits_mocked_oracle_and_returns_matched_batch():
    def side_effect(request: httpx.Request) -> httpx.Response:
        body = request.content or b""
        if b"P_RECEIPT_NUMBER" in body:
            csv_text = RECEIPT_CSV
        else:
            csv_text = INVOICE_CSV
        return httpx.Response(200, text=_soap(csv_text))

    with respx.mock as router:
        route = router.post(SOAP_URL).mock(side_effect=side_effect)
        with TestClient(app) as http:
            response = http.post(
                "/v1/reconcile/batch",
                json={
                    "customer_name": "Acme Corp",
                    "payment_reference": "RCPT-45021",
                    "invoices": [
                        {
                            "invoice_number": "INV-1",
                            "invoice_date": "2026-08-14",
                            "invoice_amount": 1250.0,
                        }
                    ],
                },
            )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["fusion_receipt_number"] == "RCPT-45021"
    assert data["fusion_applied_amount"] == 4750.5
    assert data["total_amount"] == 4750.5
    assert data["invoices"][0]["match_phase"] == "MATCHED"
    assert data["invoices"][0]["fusion_invoice_number"] == "INV-1"
    assert data["invoices"][0]["match_rule"] == "EXACT_NUMBER_AMOUNT_DATE"
    assert route.call_count >= 1


def test_end_to_end_health_and_ready():
    with TestClient(app) as http:
        assert http.get("/health").status_code == 200
        assert http.get("/health").json() == {"status": "ok"}
        assert http.get("/ready").status_code == 200
        assert http.get("/ready").json() == {"status": "ready"}
        assert http.get("/").status_code == 200


def test_end_to_end_requires_api_key_when_configured(monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "API_KEY", "prod-secret")
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=_soap(INVOICE_CSV)))
        with TestClient(app) as http:
            response = http.post(
                "/v1/reconcile/batch",
                json={"customer_name": "Acme Corp"},
            )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API key"

    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=_soap(INVOICE_CSV)))
        with TestClient(app) as http:
            response = http.post(
                "/v1/reconcile/batch",
                json={"customer_name": "Acme Corp"},
                headers={"x-api-key": "prod-secret"},
            )
    assert response.status_code != 401, response.text
