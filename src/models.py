from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.utils.validators import sanitize_float_val, sanitize_string_val

# Upper bounds on the caller-supplied strings. Oracle BI Publisher parameter values are
# carried in a SOAP envelope and in the report cache key, and both are sized by whatever
# the caller sends, so the fields that reach them are bounded rather than trusted. The
# fusion_* fields are deliberately left unbounded: they are read back out of the ledger,
# they are not validated on assignment, and a ledger row longer than this would fail the
# response rather than the request.
MAX_TEXT_LENGTH = 512
MAX_IDENTIFIER_LENGTH = 256


class InvoiceItem(BaseModel):
    # validate_assignment makes the declared types load-bearing for writes as well as reads.
    # map_ledger_to_payload assigns the raw Oracle CSV cell straight onto fusion_invoice_amount
    # ("9,500.25"), and without this the field would keep that str while claiming to be a
    # float, so the declared type was a lie in every response the service produced.
    # ReconciliationRequest deliberately does NOT set it: _set_invoice_count is a mode="after"
    # model validator that assigns a field, and an after-validator that re-enters itself on
    # every assignment recurses until the stack overflows. Its float fields are coerced at
    # their single assignment site instead.
    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)

    line_id: int | str | None = Field(default=None, alias="line_id")
    invoice_number: str | int | None = Field(default=None, max_length=MAX_IDENTIFIER_LENGTH)
    fusion_invoice_number: str | None = None
    invoice_date: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    fusion_invoice_date: str | None = None
    invoice_amount: float | None = None
    fusion_invoice_amount: float | None = None
    description: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    customer_invoice_number: str | int | None = Field(default=None, max_length=MAX_IDENTIFIER_LENGTH)
    store_no: int | str | None = Field(default=None, alias="store_no")
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


# fusion_invoice_date is deliberately typed str | None and is NOT normalised: it carries the
# Oracle ledger's own date string verbatim ("08/14/2026", "2026-08-14"), which is the point of
# the field. Only the amount is a number, because only the amount is consumed as one.


class MetaDataModel(BaseModel):
    warnings: list[str] = Field(default_factory=list)


class ReconciliationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    customer_name: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    fusion_customer_name: str | None = None
    payment_reference: str | int | None = Field(default=None, max_length=MAX_IDENTIFIER_LENGTH)
    fusion_receipt_number: str | None = None
    payment_date: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    fusion_receipt_date: str | None = None
    fusion_customer_number: str | None = None
    fusion_currency: str | None = None
    fusion_receipt_status_code: str | None = None
    fusion_applied_amount: float | None = None
    # header_id is caller-supplied and echoed verbatim by the response model, so it is
    # bounded like the other caller-supplied strings even though it is not a fusion_* field.
    header_id: int | str | None = Field(default=None, max_length=MAX_IDENTIFIER_LENGTH)
    # 2500 caps the work a single request can ask for. Zero is legal: a receipt-only
    # lookup carries no invoice lines and is reconciled entirely from payment_reference,
    # payment_date and total_amount.
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

    # fusion_applied_amount is an out-field by contract, but the model is dual-use
    # (request and response share it), so the inbound edge gets the same coercion the
    # outbound mapping already applies: "9,500.25" must not 422 here when total_amount
    # accepts it.
    @field_validator("total_amount", "confidence_score", "fusion_applied_amount", mode="before")
    @classmethod
    def sanitize_floats(cls, v: float | str | None) -> float | None:
        return sanitize_float_val(v)

    @model_validator(mode="after")
    def _set_invoice_count(self) -> ReconciliationRequest:
        """Auto-populate invoice_count from actual invoices list length."""
        self.invoice_count = len(self.invoices)
        return self
