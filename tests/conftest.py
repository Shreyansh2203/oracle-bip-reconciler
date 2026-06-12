import os

os.environ["ORACLE_URL"] = "https://test.oracle.com"
os.environ["API_KEY"] = "test_key"
os.environ["ORACLE_USER"] = "test_user"
os.environ["ORACLE_PASS"] = "test_pass"

import httpx
import pytest

from src.models import InvoiceItem, ReconciliationRequest
from src.services.oracle_matcher import OracleClientContext


@pytest.fixture
def mock_httpx_client():
    return httpx.AsyncClient()

@pytest.fixture
def oracle_context(mock_httpx_client):
    return OracleClientContext(client=mock_httpx_client, user="test_user", password="test_password")

@pytest.fixture
def sample_payload():
    return ReconciliationRequest(
        payment_reference="REC-123",
        total_amount=100.0,
        payment_date="2023-10-01",
        customer_name="Test Customer",
        invoices=[
            InvoiceItem(invoice_number="INV-001", invoice_amount=50.0),
            InvoiceItem(invoice_number="INV-002", invoice_amount=50.0)
        ]
    )
