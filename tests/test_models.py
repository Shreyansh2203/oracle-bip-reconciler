from src.models import InvoiceItem, ReconciliationRequest


def test_invoice_item_none_sanitization():
    # Valid floats
    item = InvoiceItem(invoice_amount=100.50, fusion_invoice_amount="200.00")
    assert item.invoice_amount == 100.50
    assert item.fusion_invoice_amount == 200.00

    # "none" strings should become None
    item_none = InvoiceItem(invoice_amount="none", fusion_invoice_amount=" NONE ")
    assert item_none.invoice_amount is None
    assert item_none.fusion_invoice_amount is None


def test_reconciliation_request_none_sanitization():
    # "none" strings should become None
    req = ReconciliationRequest(total_amount="none", confidence_score="None")
    assert req.total_amount is None
    assert req.confidence_score is None


def test_string_sanitization():
    # "none" strings for text fields should become None
    req = ReconciliationRequest(customer_name="none", payment_reference=" NONE ", payment_date="None")
    assert req.customer_name is None
    assert req.payment_reference is None
    assert req.payment_date is None

    item = InvoiceItem(invoice_number="none", invoice_date="None", customer_invoice_number=" NONE ")
    assert item.invoice_number is None
    assert item.invoice_date is None
    assert item.customer_invoice_number is None
