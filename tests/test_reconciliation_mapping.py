import asyncio
import logging
import time

from fastapi.testclient import TestClient

from src.api.routers import reconciliation as router_mod
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


def test_receipt_backfills_a_customer_name_the_payload_never_supplied():
    # The caller may not know who they paid. The ledger row is authoritative, and the response
    # has to carry the name back so the next request can short-circuit discovery at step 1.
    payload = ReconciliationRequest(payment_reference="RCPT-45021")
    receipts = [
        {
            "BILL_CUSTOMER_NAME": "Acme Corp",
            "RECEIPT_NUMBER": "RCPT-45021",
            "RECEIPT_DATE": "2026-08-14",
            "RECEIPT_AMOUNT": "4,750.50",
        }
    ]

    map_ledger_to_payload(payload, "Acme Corp", receipts, [])

    assert payload.customer_name == "Acme Corp"
    assert payload.fusion_receipt_number == "RCPT-45021"
    assert payload.total_amount == 4750.50


def test_receipt_reference_match_is_a_case_insensitive_substring():
    # OCR and the ledger disagree on a reference's punctuation far more often than on its
    # content, and one side is routinely a prefix of the other.
    payload = ReconciliationRequest(payment_reference="rcpt-4502")
    receipts = [{"BILL_CUSTOMER_NAME": "Acme Corp", "RECEIPT_NUMBER": "RCPT-45021"}]

    map_ledger_to_payload(payload, "Acme Corp", receipts, [])

    assert payload.fusion_receipt_number == "RCPT-45021"


# --- the lower tiers ----------------------------------------------------------------------


def test_two_way_match_on_number_and_amount():
    # Exact number, amount agrees, date does not. The number has already pinned the row to a
    # small candidate set, so one agreeing field is enough.
    payload = _payload(InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=100.0))

    map_ledger_to_payload(payload, "Acme Corp", [], [_oracle_row("INV-0001", "2026-05-05", "100.00")])

    assert payload.invoices[0].match_phase == "MATCHED"
    # The Oracle date wins over the payload's, which is the whole point of the backfill.
    assert payload.invoices[0].invoice_date == "2026-05-05"


def test_one_way_match_on_a_unique_exact_number():
    # Both date and amount disagree, but the number is exact and exactly one unmapped row
    # carries it, so the OCR read of the number is the most trustworthy thing available.
    payload = _payload(InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=999.0))

    map_ledger_to_payload(payload, "Acme Corp", [], [_oracle_row("INV-0001", "2026-05-05", "100.00")])

    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].invoice_amount == 100.0


def test_an_exact_number_shared_by_two_rows_falls_through_to_the_fuzzy_bucket():
    # Recorded as current behaviour, not endorsed. The 1-way tier correctly refuses an exact
    # number that two unmapped rows share, because the amount could belong to either. But
    # _is_num_ok also accepts an exact string, so the same row reaches the fuzzy bucket
    # immediately afterwards and matches_fuzzy_num[0] takes the first row in ledger order.
    #
    # The guard therefore does not do what the docs imply for exact numbers, only for fuzzy
    # ones. This is a question about intended semantics, not a typo, and the tier ordering is
    # load-bearing, so it is pinned here rather than changed. See the open item in the report.
    payload = _payload(InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=999.0))
    oracle = [_oracle_row("INV-0001", "2026-05-05", "100.00"), _oracle_row("INV-0001", "2026-06-06", "200.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-0001"
    # Ledger order decides, not the amount: 999.00 matches neither row and the first one wins.
    assert payload.invoices[0].invoice_amount == 100.0


def test_a_fuzzy_number_shared_by_two_rows_takes_ledger_order():
    # The fuzzy bucket has no uniqueness guard of its own. It is reached only when the number
    # is not an exact hit, so this is the one path where ledger order alone picks the row.
    payload = _payload(InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=999.0))
    oracle = [_oracle_row("INV-0001235", "2020-01-01", "5.00"), _oracle_row("INV-0001236", "2020-01-02", "6.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-0001235"


def test_fuzzy_number_alone_is_used_only_as_a_last_resort():
    # One misread character and nothing else agreeing. This is the weakest tier, reached only
    # because nothing stronger matched.
    payload = _payload(InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=100.0))

    map_ledger_to_payload(payload, "Acme Corp", [], [_oracle_row("INV-0001235", "2020-01-01", "5.00")])

    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-0001235"


def test_fuzzy_number_outranks_a_bare_amount_match():
    # The number identifies a document; an amount does not. Ordering these the other way round
    # is how a customer with a repeated invoice amount gets handed somebody else's row.
    payload = _payload(InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=100.0))
    oracle = [_oracle_row("INV-0001235", "2020-01-01", "5.00"), _oracle_row("INV-OTHER", "2020-01-01", "100.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].fusion_invoice_number == "INV-0001235"


def test_amount_only_match_is_accepted_only_when_exactly_one_row_agrees():
    payload = _payload(InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="2026-01-01", invoice_amount=100.0))
    oracle = [_oracle_row("INV-A", "2020-01-01", "5.00"), _oracle_row("INV-B", "2020-01-02", "100.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].fusion_invoice_number == "INV-B"


def test_amount_only_match_is_refused_when_two_rows_share_the_amount():
    # Two rows at the same amount means the amount says nothing about which one this is, and a
    # coin flip here silently corrupts a reconciliation.
    payload = _payload(InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="2026-01-01", invoice_amount=100.0))
    oracle = [_oracle_row("INV-A", "2020-01-01", "100.00"), _oracle_row("INV-B", "2020-01-02", "100.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].match_phase == "UNMATCHED"


def test_date_only_match_is_accepted_only_when_exactly_one_row_agrees():
    payload = _payload(InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="2026-01-01", invoice_amount=999.0))
    oracle = [_oracle_row("INV-A", "2026-01-01", "5.00"), _oracle_row("INV-B", "2020-01-02", "100.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].fusion_invoice_number == "INV-A"


def test_date_only_match_is_refused_when_two_rows_share_the_date():
    payload = _payload(InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="2026-01-01", invoice_amount=999.0))
    oracle = [_oracle_row("INV-A", "2026-01-01", "5.00"), _oracle_row("INV-B", "2026-01-01", "100.00")]

    map_ledger_to_payload(payload, "Acme Corp", [], oracle)

    assert payload.invoices[0].match_phase == "UNMATCHED"


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


def test_reconcile_endpoint_turns_a_service_error_into_the_declared_status(monkeypatch):
    # The router's own error path, as opposed to the exception path above: when the service
    # layer reports an upstream failure rather than raising, the status has to come from that
    # tuple and the body has to be the fixed message.
    async def failed(*_args, **_kwargs):
        return None, recon.CLIENT_UPSTREAM_ERROR, 502

    monkeypatch.setattr(router_mod, "process_reconciliation_batch", failed)

    with TestClient(app) as http:
        response = http.post("/v1/reconcile/batch", json={"customer_name": "Acme Corp"})

    assert response.status_code == 502
    assert response.json()["detail"] == recon.CLIENT_UPSTREAM_ERROR


def test_reconcile_endpoint_falls_back_to_500_when_no_status_is_supplied(monkeypatch):
    # status or 500, not a TypeError: an error without a code still has to produce a response.
    async def failed(*_args, **_kwargs):
        return None, "something went wrong", None

    monkeypatch.setattr(router_mod, "process_reconciliation_batch", failed)

    with TestClient(app) as http:
        response = http.post("/v1/reconcile/batch", json={"customer_name": "Acme Corp"})

    assert response.status_code == 500
    assert response.json()["detail"] == "something went wrong"


def test_unexpected_internal_error_returns_a_fixed_500(monkeypatch):
    # The catch-all handler exists so a bug in this service cannot leak a traceback or an
    # internal path to the caller. It also has to actually run, which nothing else exercises.
    async def boom(*_args, **_kwargs):
        raise RuntimeError("psycopg2 OperationalError at db.internal:5432")

    monkeypatch.setattr(router_mod, "process_reconciliation_batch", boom)

    with TestClient(app, raise_server_exceptions=False) as http:
        response = http.post("/v1/reconcile/batch", json={"customer_name": "Acme Corp"})

    assert response.status_code == 500
    assert response.json() == {"detail": "An unexpected internal server error occurred."}
    assert "db.internal" not in response.text
    assert "psycopg2" not in response.text


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
    # A ratio bound rather than an absolute slack: under load both runs inflate together,
    # so a fixed budget fails on a busy machine even when the scaling is still linear.
    # Linear growth is 10x here and the previous behaviour was ~100x, so 25x separates them.
    assert large < small * 25 + 1.0, f"{small:.2f}s for 200 invoices vs {large:.2f}s for 2000"


def test_fuzzy_fallback_stays_bounded_for_a_large_tenant_ledger(monkeypatch):
    # No payload invoice has a number that exists in the ledger, so every one of them has
    # to walk the whole ledger through the fuzzy tier. This is the worst legitimate batch.
    payload = _payload(
        *[
            InvoiceItem(invoice_number=f"OC-{i:06d}", invoice_date="2026-01-01", invoice_amount=float(i))
            for i in range(200)
        ]
    )
    ledger = [_oracle_row(f"INV-{i:06d}", "2026-01-01", f"{i}.00") for i in range(5000)]

    # The guard is a CALL COUNT, not a duration. The optimisation that matters hoisted
    # per-row normalisation out of the inner loop, so these run once per ledger row
    # (~5,000) rather than once per (invoice, row) pair (~1,000,000 here) — a 200x
    # difference that no amount of machine load or clock resolution can move. A wall-clock
    # bound on this workload is a ~9ms-to-~23s constant-factor difference, which a loaded
    # CI runner can cross by accident; the call count cannot be crossed by accident.
    # The duration below is only a coarse backstop against a gross regression.
    calls = {"date": 0, "amount": 0}
    real_date = recon.format_oracle_date
    real_amount = recon.sanitize_float_val

    def counting_date(value):
        calls["date"] += 1
        return real_date(value)

    def counting_amount(value):
        calls["amount"] += 1
        return real_amount(value)

    monkeypatch.setattr(recon, "format_oracle_date", counting_date)
    monkeypatch.setattr(recon, "sanitize_float_val", counting_amount)

    start = time.perf_counter()
    map_ledger_to_payload(payload, "Acme Corp", [], ledger)
    elapsed = time.perf_counter() - start

    # One pass over the ledger, plus a small per-invoice overhead. The old per-pair
    # rescan was ~1,000,000 of each.
    assert calls["date"] < len(ledger) * 2, f"format_oracle_date called {calls['date']} times for {len(ledger)} rows"
    assert calls["amount"] < len(ledger) * 2, f"sanitize_float_val called {calls['amount']} times for {len(ledger)} rows"
    assert elapsed < 30.0, f"fuzzy fallback took {elapsed:.2f}s"
