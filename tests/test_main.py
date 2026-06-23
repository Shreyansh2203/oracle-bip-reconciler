from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import _apply_receipt_match_result, app


@pytest.fixture
def mock_oracle_env(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "user")
    monkeypatch.setenv("ORACLE_PASS", "pass")


def test_root_endpoint():
    with TestClient(app) as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "status" in response.json()
        assert response.json()["status"] == "online"


def test_apply_receipt_match_result():
    from src.models import ReconciliationRequest

    req = ReconciliationRequest()
    result = {
        "fusion_receipt_number": "R123",
        "fusion_receipt_date": "2026-05-10",
        "fusion_customer_name": "Test Cust",
        "fusion_customer_number": "CUST01",
        "fusion_currency": "USD",
        "fusion_receipt_status_code": "UNAPP",
        "fusion_applied_amount": 100.50,
        "match_phase": "UNAPPLIED",
        "match_rule": "A1",
    }
    _apply_receipt_match_result(req, result)

    assert req.fusion_receipt_number == "R123"
    assert req.fusion_receipt_date == "2026-05-10"
    assert req.fusion_customer_name == "Test Cust"
    assert req.fusion_customer_number == "CUST01"
    assert req.fusion_currency == "USD"
    assert req.fusion_receipt_status_code == "UNAPP"
    assert req.fusion_applied_amount == 100.50
    assert req.match_phase == "UNAPPLIED"
    assert req.match_rule == "A1"


def test_reconcile_endpoint_happy_path(mock_oracle_env):
    payload = {
        "payment_reference": "REC-123",
        "total_amount": 100.0,
        "payment_date": "2023-10-01",
        "customer_name": "Test Customer",
        "invoices": [{"invoice_number": "INV-001", "invoice_amount": 50.0, "invoice_date": "2023-10-01"}],
    }

    from unittest.mock import AsyncMock

    with (
        patch(
            "src.main.fetch_bip_invoices",
            new_callable=AsyncMock,
            return_value=[
                {
                    "TRANSACTION_NUMBER": "INV-001",
                    "TRANSACTION_DATE": "2023-10-01",
                    "TOTAL_AMOUNTS": "50.0",
                    "INVOICE_STATUS": "OPEN",
                    "BILL_CUSTOMER_NAME": "Test Customer",
                }
            ],
        ),
        patch("src.main.fetch_bip_receipts", new_callable=AsyncMock, return_value=[]),
    ):
        with TestClient(app) as test_client:
            response = test_client.post("/v1/reconcile/batch", json=payload)

            assert response.status_code == 200
            data = response.json()
            assert data["invoices"][0]["fusion_invoice_number"] == "INV-001"
