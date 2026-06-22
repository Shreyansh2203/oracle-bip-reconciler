import os

import httpx
import pytest

from src.models import InvoiceItem, ReconciliationRequest


@pytest.fixture(autouse=True)
def setup_env():
    old_env = dict(os.environ)
    os.environ["ORACLE_URL"] = "https://test.oracle.com"
    os.environ["ORACLE_USER"] = "test_user"
    os.environ["ORACLE_PASS"] = "test_pass"
    yield
    os.environ.clear()
    os.environ.update(old_env)


@pytest.fixture
def mock_httpx_client():
    return httpx.AsyncClient()


@pytest.fixture
def sample_payload():
    return ReconciliationRequest(
        payment_reference="REC-123",
        total_amount=100.0,
        payment_date="2023-10-01",
        customer_name="Test Customer",
        invoices=[
            InvoiceItem(invoice_number="INV-001", invoice_amount=50.0),
            InvoiceItem(invoice_number="INV-002", invoice_amount=50.0),
        ],
    )
