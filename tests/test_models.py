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

def test_inbound_fusion_applied_amount_coerces_like_total_amount():
    # The model is dual-use: fusion_applied_amount is documented as an out-field, but
    # nothing stops a client sending it. Without the before-validator, "9,500.25"
    # 422ed here while the sibling total_amount coerced -- an asymmetry a client had
    # no way to predict from the schema.
    req = ReconciliationRequest(fusion_applied_amount="9,500.25")
    assert req.fusion_applied_amount == 9500.25
    assert isinstance(req.fusion_applied_amount, float)


def test_reconciliation_request_invoice_limit():
    invoices = [InvoiceItem(invoice_number=f"INV-{i}") for i in range(2501)]
    with pytest.raises(ValidationError) as exc:
        ReconciliationRequest(
            customer_name="Test Corp",
            invoices=invoices
        )
    assert "List should have at most 2500 items" in str(exc.value)


# ── fusion_* response contract ────────────────────────────────────────────────────────────
# fusion_invoice_amount is declared float | None. map_ledger_to_payload assigns the raw Oracle
# CSV cell onto it, so before validate_assignment was enabled the field held "9,500.25" -- a str
# behind a float annotation -- and a client doing arithmetic on it got a TypeError. These tests
# pin the assignment-time coercion, not just the construction-time sanitiser.


def test_fusion_invoice_amount_is_coerced_on_assignment():
    invoice = InvoiceItem()
    invoice.fusion_invoice_amount = "9,500.25"
    assert invoice.fusion_invoice_amount == 9500.25
    assert isinstance(invoice.fusion_invoice_amount, float)


def test_fusion_invoice_amount_coercion_matches_the_request_field():
    # Both amounts on the same line end up as the same float, so a client does not have to
    # know which of the two fields is the "raw" one.
    invoice = InvoiceItem(invoice_number="INV-1")
    invoice.fusion_invoice_amount = "1,234.56"
    invoice.invoice_amount = invoice.fusion_invoice_amount
    assert invoice.invoice_amount == 1234.56
    assert invoice.fusion_invoice_amount == 1234.56


@pytest.mark.parametrize("raw", ["", "   ", "none", "None", "not a number", "nan", "inf", "-"])
def test_fusion_invoice_amount_unparseable_values_become_null(raw):
    # Oracle cells are free text, so the assignment path has to be as forgiving as the
    # construction path. A cell the engine cannot read is null, not a validation error:
    # one bad ledger cell must not fail the whole batch.
    invoice = InvoiceItem()
    invoice.fusion_invoice_amount = raw
    assert invoice.fusion_invoice_amount is None


def test_fusion_invoice_amount_keeps_a_real_zero():
    # "0" is falsy as a string-handling mistake waiting to happen; a zero-value invoice is a
    # legitimate credit and must survive as 0.0, not collapse to null.
    invoice = InvoiceItem()
    invoice.fusion_invoice_amount = "0.00"
    assert invoice.fusion_invoice_amount == 0.0
    assert invoice.fusion_invoice_amount is not None


def test_fusion_invoice_amount_keeps_a_negative_credit_memo():
    invoice = InvoiceItem()
    invoice.fusion_invoice_amount = "-250.75"
    assert invoice.fusion_invoice_amount == -250.75


def test_fusion_invoice_date_keeps_the_oracle_string_verbatim():
    # fusion_invoice_date is declared str | None and deliberately not normalised: it is the
    # ledger's own rendering of the date, which is what makes it worth returning separately
    # from the normalised invoice_date.
    invoice = InvoiceItem()
    invoice.fusion_invoice_date = "08/14/2026"
    assert invoice.fusion_invoice_date == "08/14/2026"
    assert isinstance(invoice.fusion_invoice_date, str)


def test_fusion_invoice_date_rejects_a_non_string():
    # validate_assignment is on for the whole model, so the str annotation is enforced on
    # writes too. A CSV cell is always a str; a date object reaching here is a bug and
    # should fail loudly at the assignment rather than serialise as something surprising.
    invoice = InvoiceItem()
    with pytest.raises(ValidationError):
        invoice.fusion_invoice_date = 20260814


def test_every_declared_amount_type_holds_after_assignment():
    # Structural check: whichever field a client reads the Oracle amount from, the declared
    # type and the runtime type agree once the ledger has been mapped onto the payload.
    invoice = InvoiceItem(invoice_number="INV-1", invoice_date="05-Oct-2026")
    invoice.fusion_invoice_number = "INV-0001234"
    invoice.fusion_invoice_date = "08/14/2026"
    invoice.fusion_invoice_amount = "9,500.25"
    invoice.match_phase = "MATCHED"
    invoice.invoice_number = invoice.fusion_invoice_number
    invoice.invoice_date = invoice.fusion_invoice_date
    invoice.invoice_amount = invoice.fusion_invoice_amount

    for field in ("invoice_amount", "fusion_invoice_amount"):
        declared = InvoiceItem.model_fields[field].annotation
        runtime = type(getattr(invoice, field))
        assert runtime is float, field
        assert "float" in str(declared), field

    for field in ("invoice_date", "fusion_invoice_date", "invoice_number", "fusion_invoice_number"):
        assert isinstance(getattr(invoice, field), str), field
