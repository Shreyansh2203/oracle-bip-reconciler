import asyncio
import logging
import time

from fastapi.testclient import TestClient

from src.main import app
from src.models import InvoiceItem, ReconciliationRequest
from src.services import reconciliation as recon
from src.services.reconciliation import _is_num_ok, map_ledger_to_payload, process_reconciliation_batch


def _oracle_row(number, date, amount, **extra):
    row = {
        "BILL_CUSTOMER_NAME": "Acme Corp",
        "TRANSACTION_NUMBER": number,
        "TRANSACTION_DATE": date,
        "TRANSACTION_TOTAL": amount,
    }
    row.update(extra)
    return row


def _payload(*invoices):
    return ReconciliationRequest(customer_name="Acme Corp", invoices=list(invoices))


# --- _is_num_ok: minimum length must hold on both sides -------------------------


def test_is_num_ok_rejects_short_oracle_number():
    # 4-char Oracle number is 1 edit from a 5-char OCR number; the typo budget of a
    # short number is a far too large fraction of the whole string.
    assert not _is_num_ok("12345", "1245")
    assert not _is_num_ok("12345", "1345")
    assert not _is_num_ok("1245", "12345")
    # ... but a genuine 5-char number with a typo is still accepted.
    assert _is_num_ok("12345", "12S45")


def test_is_num_ok_typo_budget_scales_with_shorter_number():
    # A truncated OCR number must not be handed the typo budget of its longer self.
    # "ABCDEFG" -> "ABCDEX" is 2 edits on a 6-character Oracle number (33% of the string).
    assert not _is_num_ok("ABCDEFG", "ABCDEX")
    # ... while a single typo on a long number is still absorbed.
    assert _is_num_ok("INV-00001234", "INV-0000123S")


def test_is_num_ok_symmetric_short_input_rejected():
    assert not _is_num_ok("12S4", "1234")


# --- tiered matching ------------------------------------------------------------


def test_three_way_match_wins_over_fuzzy_number_match():
    payload = _payload(InvoiceItem(invoice_number="INV-0009", invoice_date="2026-01-05", invoice_amount=100.0))
    oracle = [
        _oracle_row("INV-0009", "2026-01-05", "100.00"),
        _oracle_row("INY-0009", "2026-02-02", "999.00"),
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    assert payload.invoices[0].fusion_invoice_number == "INV-0009"
    assert payload.invoices[0].match_phase == "MATCHED"


def test_fuzzy_number_match_wins_over_single_amount_match():
    payload = _payload(InvoiceItem(invoice_number="INV-0009", invoice_date="2026-02-02", invoice_amount=100.0))
    oracle = [
        _oracle_row("INY-0009", "2026-02-02", "999.00"),
        _oracle_row("INV-0008", "2026-03-03", "100.00"),
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    # The fuzzy number match is corroborated by the date, so it outranks the lone amount match.
    assert payload.invoices[0].fusion_invoice_number == "INY-0009"


def test_fuzzy_tie_break_prefers_first_corroborated_candidate():
    payload = _payload(InvoiceItem(invoice_number="INV-0009", invoice_date="2026-02-02", invoice_amount=100.0))
    oracle = [
        _oracle_row("INY-0009", "2026-01-01", "100.00"),
        _oracle_row("INX-0009", "2026-02-02", "999.00"),
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    # Both are corroborated (amount and date respectively). Every other tier picks the
    # first candidate in ledger order, so the fuzzy tier must too.
    assert payload.invoices[0].fusion_invoice_number == "INY-0009"


def test_single_amount_or_date_match_is_accepted_only_when_unique():
    payload = _payload(
        InvoiceItem(invoice_number="INV-0009", invoice_date="2026-01-01", invoice_amount=100.0),
        InvoiceItem(invoice_number="INV-0010", invoice_date="2026-01-01", invoice_amount=100.0),
    )
    oracle = [
        _oracle_row("A-1", "2026-01-01", "500.00"),
        _oracle_row("A-2", "2026-01-01", "500.00"),
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    assert [i.match_phase for i in payload.invoices] == ["UNMATCHED", "UNMATCHED"]


def test_duplicate_oracle_numbers_are_never_mapped_twice():
    payload = _payload(
        InvoiceItem(invoice_number="INV-0009", invoice_date="2026-01-01", invoice_amount=100.0),
        InvoiceItem(invoice_number="INV-0009", invoice_date="2026-01-01", invoice_amount=100.0),
    )
    oracle = [
        _oracle_row("INV-0009", "2026-01-01", "100.00"),
        _oracle_row("INV-0009", "2026-01-01", "100.00"),
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    assert [i.match_phase for i in payload.invoices] == ["MATCHED", "MATCHED"]


# --- number key normalisation ---------------------------------------------------


def test_numberless_oracle_row_is_consumed_once():
    payload = _payload(
        InvoiceItem(invoice_number=None, invoice_date="2026-01-01", invoice_amount=100.0),
        InvoiceItem(invoice_number=None, invoice_date="2026-01-02", invoice_amount=200.0),
    )
    oracle = [_oracle_row("", "2026-01-01", "100.00")]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number is None
    assert payload.invoices[1].match_phase == "UNMATCHED"


def test_literal_none_number_is_not_swallowed_by_a_numberless_row():
    # A number-less row and a row whose Oracle number was exported as the literal string
    # "None" must not collapse onto the same "already mapped" token.
    payload = _payload(
        InvoiceItem(invoice_number=None, invoice_date="2026-01-01", invoice_amount=100.0),
        InvoiceItem(invoice_number=None, invoice_date="2026-01-05", invoice_amount=None),
    )
    oracle = [
        _oracle_row("", "2026-01-01", "100.00"),
        _oracle_row("None", "2026-01-05", "250.00"),
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], oracle)
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number is None
    assert payload.invoices[1].match_phase == "MATCHED"
    assert payload.invoices[1].fusion_invoice_number == "None"
    assert payload.invoices[1].invoice_date == "2026-01-05"


# --- receipt mapping ------------------------------------------------------------


def test_receipt_backfills_missing_payment_fields():
    payload = _payload()
    receipts = [
        {
            "RECEIPT_NUMBER": "RCPT-8891",
            "RECEIPT_DATE": "2026-04-01",
            "RECEIPT_AMOUNT": "4,200.50",
            "CURRENCY": "USD",
            "RECEIPT_STATUS_CODE": "APPLIED",
            "BILL_CUSTOMER_NUMBER": "C-77",
        }
    ]
    payload.payment_reference = "RCPT-8891"
    map_ledger_to_payload(payload, "Acme Corp", receipts, [])
    assert payload.fusion_receipt_number == "RCPT-8891"
    assert payload.fusion_applied_amount == 4200.50
    assert payload.fusion_currency == "USD"
    assert payload.fusion_customer_number == "C-77"
    assert payload.total_amount == 4200.50
    assert payload.payment_date == "2026-04-01"


def test_receipt_falls_back_to_amount_and_date_when_reference_missing():
    payload = _payload()
    receipts = [
        {
            "RECEIPT_NUMBER": "RCPT-1",
            "RECEIPT_DATE": "04-01-2026",
            "RECEIPT_AMOUNT": "100.00",
        }
    ]
    payload.total_amount = 100.0
    payload.payment_date = "2026-04-01"
    map_ledger_to_payload(payload, "Acme Corp", receipts, [])
    assert payload.fusion_receipt_number == "RCPT-1"


# --- information disclosure -----------------------------------------------------

SECRET_LEAK = "ORA-00942: table APPS.XX_CUSTOM_GL does not exist at erp.internal.acme.corp:8080"


def test_discovery_failure_does_not_leak_oracle_internals(monkeypatch, caplog):
    async def boom(*_args, **_kwargs):
        raise RuntimeError(SECRET_LEAK)

    monkeypatch.setattr(recon, "discover_potential_customers", boom)
    with caplog.at_level(logging.ERROR):
        result, err, status = asyncio.run(
            process_reconciliation_batch(_payload(), client=None, oracle_user="u", oracle_pass="p")
        )

    assert result is None
    assert status == 502
    assert err == recon.CLIENT_UPSTREAM_ERROR
    assert SECRET_LEAK not in err
    assert SECRET_LEAK in caplog.text


def test_ledger_fetch_failure_does_not_leak_oracle_internals(monkeypatch, caplog):
    async def discover(*_args, **_kwargs):
        return "Acme Corp", None

    async def boom(*_args, **_kwargs):
        raise RuntimeError(SECRET_LEAK)

    monkeypatch.setattr(recon, "discover_potential_customers", discover)
    monkeypatch.setattr(recon, "fetch_bip_invoices", boom)
    monkeypatch.setattr(recon, "fetch_bip_receipts", boom)
    with caplog.at_level(logging.ERROR):
        result, err, status = asyncio.run(
            process_reconciliation_batch(_payload(), client=None, oracle_user="u", oracle_pass="p")
        )

    assert result is None
    assert status == 502
    assert err == recon.CLIENT_UPSTREAM_ERROR
    assert SECRET_LEAK not in err
    assert SECRET_LEAK in caplog.text


def test_undiscovered_customer_returns_no_error(monkeypatch):
    async def discover(*_args, **_kwargs):
        return None, None

    monkeypatch.setattr(recon, "discover_potential_customers", discover)
    result, err, status = asyncio.run(
        process_reconciliation_batch(_payload(), client=None, oracle_user="u", oracle_pass="p")
    )
    assert (result, err, status) == (None, None, None)


def test_reconcile_endpoint_never_returns_oracle_internals(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise RuntimeError(SECRET_LEAK)

    monkeypatch.setattr(recon, "discover_potential_customers", boom)

    with TestClient(app) as http:
        response = http.post("/v1/reconcile/batch", json={"customer_name": "Acme Corp"})

    assert response.status_code == 502
    assert response.json()["detail"] == recon.CLIENT_UPSTREAM_ERROR
    assert SECRET_LEAK not in response.text


# --- scale ----------------------------------------------------------------------


def test_matching_does_not_degrade_to_a_per_invoice_ledger_rescan():
    ledger = [_oracle_row(f"INV-{i:06d}", "2026-01-01", f"{i}.00") for i in range(5000)]

    def run(n_invoices):
        payload = _payload(
            *[
                InvoiceItem(invoice_number=f"INV-{i:06d}", invoice_date="2026-01-01", invoice_amount=float(i))
                for i in range(n_invoices)
            ]
        )
        start = time.perf_counter()
        map_ledger_to_payload(payload, "Acme Corp", [], ledger)
        return time.perf_counter() - start, payload

    small, small_payload = run(200)
    large, large_payload = run(2000)

    assert all(i.match_phase == "MATCHED" for i in small_payload.invoices)
    assert all(i.match_phase == "MATCHED" for i in large_payload.invoices)
    # Ten times the batch size must stay close to linear. Rescanning the ledger once per
    # invoice (the previous behaviour) made this grow with the product of batch and
    # ledger size, and blocked the event loop while doing it.
    assert large < small + 0.5, f"{small:.2f}s for 200 invoices vs {large:.2f}s for 2000"


def test_fuzzy_fallback_stays_bounded_for_a_large_tenant_ledger():
    # No payload invoice has a number that exists in the ledger, so every one of them has
    # to walk the whole ledger through the fuzzy tier. This is the worst legitimate batch.
    payload = _payload(
        *[
            InvoiceItem(invoice_number=f"OC-{i:06d}", invoice_date="2026-01-01", invoice_amount=float(i))
            for i in range(200)
        ]
    )
    ledger = [_oracle_row(f"INV-{i:06d}", "2026-01-01", f"{i}.00") for i in range(5000)]

    start = time.perf_counter()
    map_ledger_to_payload(payload, "Acme Corp", [], ledger)
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, f"fuzzy fallback took {elapsed:.2f}s"
