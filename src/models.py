from typing import Any
from pydantic import BaseModel, Field, field_validator


class InvoiceItem(BaseModel):
    line_id: int | str | None = Field(None, alias="Line_ID")
    invoice_number: str | int | None = None
    fusion_invoice_number: str | None = None
    invoice_date: str | None = None
    fusion_invoice_date: str | None = None
    invoice_amount: float | None = None
    fusion_invoice_amount: float | None = None
    description: str | None = None
    customer_invoice_number: str | int | None = None
    store_no: str | int | None = Field(None, alias="storeNo")

    @field_validator("invoice_number", "invoice_date", "customer_invoice_number", mode="before")
    @classmethod
    def sanitize_strings(cls, v: str | int | None) -> str | int | None:
        if v is None:
            return ""
        stripped = str(v).strip()
        if stripped.lower() == "none":
            return ""
        return stripped

    @field_validator("invoice_amount", "fusion_invoice_amount", mode="before")
    @classmethod
    def sanitize_floats(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() == "none":
            return None
        return v

class MetaDataModel(BaseModel):
    warnings: list[str] = Field(default_factory=list)

class ReconciliationRequest(BaseModel):
    customer_name: str | None = None
    fusion_customer_name: str | None = None
    payment_reference: str | int | None = None
    fusion_receipt_number: str | None = None
    payment_date: str | None = None
    fusion_receipt_date: str | None = None
    header_id: int | str | None = None
    invoices: list[InvoiceItem] = Field(default=[], max_length=2500)
    total_amount: float | None = None
    confidence_score: float | None = None
    confidence_label: str | None = None
    invoice_count: int | None = None
    meta_data: MetaDataModel | None = None

    @field_validator("customer_name", "payment_reference", "payment_date", mode="before")
    @classmethod
    def sanitize_strings(cls, v: str | int | None) -> str | int | None:
        if v is None:
            return ""
        stripped = str(v).strip()
        if stripped.lower() == "none":
            return ""
        return stripped

    @field_validator("total_amount", "confidence_score", mode="before")
    @classmethod
    def sanitize_floats(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() == "none":
            return None
        return v

