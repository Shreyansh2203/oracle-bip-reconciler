from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.utils.validators import sanitize_float_val, sanitize_string_val


class InvoiceItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    line_id: int | str | None = Field(default=None, alias="line_id")
    invoice_number: str | int | None = None
    fusion_invoice_number: str | None = None
    invoice_date: str | None = None
    fusion_invoice_date: str | None = None
    invoice_amount: float | None = None
    fusion_invoice_amount: float | None = None
    description: str | None = None
    customer_invoice_number: str | int | None = None
    store_no: str | int | None = Field(default=None, alias="store_no")
    match_phase: Literal["MATCHED", "UNMATCHED"] | None = None
    match_rule: str | None = None

    @field_validator("invoice_number", "invoice_date", "customer_invoice_number", mode="before")
    @classmethod
    def sanitize_strings(cls, v: str | int | None) -> str | int | None:
        return sanitize_string_val(v)

    @field_validator("invoice_amount", "fusion_invoice_amount", mode="before")
    @classmethod
    def sanitize_floats(cls, v: float | str | None) -> float | None:
        return sanitize_float_val(v)


class MetaDataModel(BaseModel):
    warnings: list[str] = Field(default_factory=list)


class ReconciliationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    customer_name: str | None = None
    fusion_customer_name: str | None = None
    payment_reference: str | int | None = None
    fusion_receipt_number: str | None = None
    payment_date: str | None = None
    fusion_receipt_date: str | None = None
    fusion_customer_number: str | None = None
    fusion_currency: str | None = None
    fusion_receipt_status_code: str | None = None
    fusion_applied_amount: float | None = None
    header_id: int | str | None = None
    invoices: list[InvoiceItem] = Field(default_factory=list, max_length=2500)
    total_amount: float | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_label: str | None = None
    invoice_count: int | None = None
    meta_data: MetaDataModel | None = None
    meta_extra: dict[str, Any] | None = Field(default=None, alias="_meta")
    match_phase: Literal["MATCHED", "UNMATCHED"] | None = None
    match_rule: str | None = None

    @field_validator("customer_name", "payment_reference", "payment_date", mode="before")
    @classmethod
    def sanitize_strings(cls, v: str | int | None) -> str | int | None:
        return sanitize_string_val(v)

    @field_validator("total_amount", "confidence_score", mode="before")
    @classmethod
    def sanitize_floats(cls, v: float | str | None) -> float | None:
        return sanitize_float_val(v)

    @model_validator(mode="after")
    def _set_invoice_count(self) -> ReconciliationRequest:
        """Auto-populate invoice_count from actual invoices list length."""
        self.invoice_count = len(self.invoices)
        return self

    def add_warning(self, message: str) -> None:
        if self.meta_data is None:
            self.meta_data = MetaDataModel()
        self.meta_data.warnings.append(message)
