from typing import Any

from pydantic import BaseModel


class InvoiceItem(BaseModel):
    Line_ID: Any = None
    invoice_number: Any = None
    fusion_invoice_number: Any = None
    invoice_date: Any = None
    fusion_invoice_date: Any = None
    invoice_amount: Any = None
    fusion_invoice_amount: Any = None
    description: Any = None
    customer_invoice_number: Any = None
    storeNo: Any = None

class ReconciliationRequest(BaseModel):
    customer_name: Any = None
    fusion_customer_name: Any = None
    payment_reference: Any = None
    fusion_receipt_number: Any = None
    payment_date: Any = None
    fusion_receipt_date: Any = None
    header_id: Any = None
    invoices: list[InvoiceItem] = []
    total_amount: Any = None
    confidence_score: Any = None
    confidence_label: Any = None
    invoice_count: Any = None
    meta_data: dict | None = None
