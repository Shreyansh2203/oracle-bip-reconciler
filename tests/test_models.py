import pytest
from pydantic import ValidationError

from src.models import InvoiceItem, ReconciliationRequest


def test_invoice_sanitization():
    invoice = InvoiceItem(
        invoice_amount="1,234.56",
        invoice_date="2026-10-05T12:00:00Z"
    )
    assert invoice.invoice_amount == 1234.56

def test_reconciliation_request_limits():
    # Should be able to create valid request
    req = ReconciliationRequest(
        customer_name="Test Corp",
        total_amount="123.45"
    )
    assert req.total_amount == 123.45
    assert req.invoice_count == 0

def test_reconciliation_request_invoice_limit():
    invoices = [InvoiceItem(invoice_number=f"INV-{i}") for i in range(2501)]
    with pytest.raises(ValidationError) as exc:
        ReconciliationRequest(
            customer_name="Test Corp",
            invoices=invoices
        )
    assert "List should have at most 2500 items" in str(exc.value)
