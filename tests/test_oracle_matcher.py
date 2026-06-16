import pytest
from src.services.oracle_matcher import (
    match_invoice_in_memory,
    match_receipt_in_memory,
)

def test_match_invoice_in_memory_success():
    bip_invoices = [
        {'TRANSACTION_NUMBER': 'INV-123', 'TRANSACTION_DATE': '2023-10-01', 'TRANSACTION_TOTAL': '100.00', 'INVOICE_STATUS': 'OPEN'}
    ]
    result = match_invoice_in_memory('INV-123', '2023-10-01', 100.0, '', '', bip_invoices)
    assert result['matched_in_oracle'] is True
    assert result['fusion_invoice_number'] == 'INV-123'
    assert result['match_phase'] == 'OPEN'
    assert result['fusion_invoice_amount'] == 100.0

def test_match_receipt_in_memory_success():
    bip_receipts = [
        {'RECEIPT_NUMBER': 'REC-123', 'RECEIPT_DATE': '2023-10-01', 'RECEIPT_STATUS_CODE': 'UNAPP', 'BILL_CUSTOMER_NAME': 'Customer A'}
    ]
    result = match_receipt_in_memory('REC-123', 100.0, '2023-10-01', 'Customer A', bip_receipts)
    assert result['matched_in_oracle'] is True
    assert result['fusion_receipt_number'] == 'REC-123'
    assert result['match_phase'] == 'UNAPPLIED'
