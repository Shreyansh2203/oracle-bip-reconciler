import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from src.main import app

@pytest.fixture
def mock_oracle_env(monkeypatch):
    monkeypatch.setenv("ORACLE_USER", "user")
    monkeypatch.setenv("ORACLE_PASS", "pass")

def test_root_endpoint():
    with TestClient(app) as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        assert response.json()["status"] == "online"

def test_reconcile_endpoint_happy_path(mock_oracle_env):
    payload = {
        "payment_reference": "REC-123",
        "total_amount": 100.0,
        "payment_date": "2023-10-01",
        "customer_name": "Test Customer",
        "invoices": [
            {
                "invoice_number": "INV-001",
                "invoice_amount": 50.0
            }
        ]
    }

    with patch("src.main._fetch_receipt_data") as mock_receipt, \
         patch("src.main._build_bip_invoice_map", return_value={"INV-001": {"TransactionNumber": "INV-001", "TransactionDate": "2023-10-01", "EnteredAmount": 50.0}}) as mock_bip:
        
        with TestClient(app) as test_client:
            response = test_client.post("/reconcile", json=payload)
            
            assert response.status_code == 200
            data = response.json()
            assert data["invoices"][0]["fusion_invoice_number"] == "INV-001"
