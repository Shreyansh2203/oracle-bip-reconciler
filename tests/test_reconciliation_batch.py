"""End-to-end coverage for process_reconciliation_batch.

map_ledger_to_payload is unit tested in test_reconciliation_mapping.py, but the
orchestrator that drives it -- customer discovery, the two BIP report fetches, the
parameter-echo row filter and the mapping itself -- had no test at all. These tests
run the real discovery, filtering and mapping code and stop at the Oracle boundary.
"""

import asyncio
import base64
import json
import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.core.config import settings
from src.main import app
from src.models import InvoiceItem, ReconciliationRequest
from src.services import discovery, oracle_bip
from src.services import reconciliation as recon
from src.services.reconciliation import process_reconciliation_batch

SOAP_URL = f"{settings.ORACLE_URL.rstrip('/')}/xmlpserver/services/ExternalReportWSSService"

CUSTOMER = "Northwind Traders"
RECEIPT_NUMBER = "RCPT-45021"

# A report that came back with a header and no rows: a well-formed answer meaning "no data".
NO_ROWS_CSV = "TRANSACTION_NUMBER,BILL_CUSTOMER_NAME\n"

# Oracle BIP returns the report as base64 CSV inside a runReportResponse envelope.
# Amounts come out of Oracle grouped by thousands and dates are not always ISO.
INVOICE_REPORT_CSV = (
    "TRANSACTION_NUMBER,TRANSACTION_DATE,TRANSACTION_TOTAL,BILL_CUSTOMER_NUMBER,BILL_CUSTOMER_NAME,INVOICE_STATUS\n"
    'INV-2026-00881,08/14/2026,"9,500.25",1042,Northwind Traders,VALID\n'
    'INV-2026-00882,2026-08-14,"4,750.50",1042,Northwind Traders,VALID\n'
    'INV-2026-00990,2026-08-20,"1,000.00",1042,Northwind Traders,VALID\n'
)
RECEIPT_REPORT_CSV = (
    "RECEIPT_NUMBER,RECEIPT_DATE,RECEIPT_AMOUNT,CURRENCY,RECEIPT_STATUS_CODE,BILL_CUSTOMER_NUMBER,BILL_CUSTOMER_NAME\n"
    f'{RECEIPT_NUMBER},2026-08-14,"14,250.75",USD,APPLIED,1042,{CUSTOMER}\n'
)

# A row BIP emits when "show parameters" is enabled on the data model: only P_ keys, so
# _is_data_row rejects it and _filter_data_rows has to drop it before mapping.
PARAMETER_ECHO_ROW = {"P_CUSTOMER_NAME": CUSTOMER, "P_INVOICE_NUM": " "}

LEDGER_ROWS = [
    {
        "BILL_CUSTOMER_NAME": CUSTOMER,
        "TRANSACTION_NUMBER": "INV-2026-00882",
        "TRANSACTION_DATE": "2026-08-14",
        "TRANSACTION_TOTAL": "4,750.50",
    }
]
RECEIPT_ROWS = [
    {
        "BILL_CUSTOMER_NAME": CUSTOMER,
        "RECEIPT_NUMBER": RECEIPT_NUMBER,
        "RECEIPT_DATE": "2026-08-14",
        "RECEIPT_AMOUNT": "4,750.50",
        "CURRENCY": "USD",
    }
]

ORACLE_INTERNAL_DETAIL = (
    "ORA-01017: invalid username/password; logon denied at erp.internal.acme.corp:8080 "
    "for report /Custom/Shreyansh/Finacials/Receivables/Upgrade/Get Receipt Details Report.xdo"
)


@pytest.fixture(autouse=True)
def clear_bip_cache():
    # _bip_cache is a module-level TTL cache that outlives a single test, so a repeat run
    # of the same report would be served from memory and never reach the mocked transport.
    oracle_bip._bip_cache.local.clear()
    yield
    oracle_bip._bip_cache.local.clear()


def payment_payload():
    return ReconciliationRequest(
        customer_name=CUSTOMER,
        payment_reference=RECEIPT_NUMBER,
        payment_date="2026-08-14",
        total_amount=14250.75,
        invoices=[
            InvoiceItem(invoice_number="INV-2026-00881", invoice_date="2026-08-14", invoice_amount=9500.25),
            InvoiceItem(invoice_number="INV-2026-00882", invoice_date="2026-08-14", invoice_amount=4750.50),
            # Deliberately absent from the ledger, so the flow must not report a clean sweep.
            InvoiceItem(invoice_number="INV-2026-00777", invoice_date="2026-07-02", invoice_amount=310.00),
        ],
    )


def run_batch(payload):
    async def _run():
        async with httpx.AsyncClient() as client:
            return await process_reconciliation_batch(payload, client, "svc-account", "svc-password")

    return asyncio.run(_run())


def soap_envelope(csv_text):
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
        "<soap:Body>"
        '<pub:runReportResponse xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">'
        "<pub:reportOutput>"
        f"<pub:reportBytes>{encoded}</pub:reportBytes>"
        "</pub:reportOutput>"
        "</pub:runReportResponse>"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def bip_transport():
    """Route the single SOAP endpoint to the invoice or receipt report by report path."""

    def _side_effect(request):
        body = request.content.decode("utf-8")
        if "Get Receipt Details Report" in body:
            return httpx.Response(200, text=soap_envelope(RECEIPT_REPORT_CSV))
        if "Get Invoice Details Report" in body:
            return httpx.Response(200, text=soap_envelope(INVOICE_REPORT_CSV))
        raise AssertionError(f"unexpected BIP report request: {body}")

    return respx.mock(assert_all_called=True), _side_effect


def test_batch_maps_a_three_way_match_through_the_whole_flow():
    mock, side_effect = bip_transport()
    with mock as router:
        route = router.post(SOAP_URL).mock(side_effect=side_effect)
        result, err, status = run_batch(payment_payload())

    assert (err, status) == (None, None)
    assert result is not None

    # Step 1 resolves the customer from the receipt number, then the ledger is fetched for
    # both reports: three SOAP round trips in total.
    assert route.call_count == 3

    assert result.fusion_customer_name == CUSTOMER
    assert result.fusion_receipt_number == RECEIPT_NUMBER
    assert result.fusion_receipt_date == "2026-08-14"
    assert result.fusion_applied_amount == 14250.75
    assert result.fusion_currency == "USD"
    assert result.fusion_receipt_status_code == "APPLIED"
    assert result.fusion_customer_number == "1042"
    assert result.invoice_count == 3

    exact, exact_again, absent = result.invoices
    assert [exact.match_phase, exact_again.match_phase, absent.match_phase] == [
        "MATCHED",
        "MATCHED",
        "UNMATCHED",
    ]

    # fusion_invoice_number and fusion_invoice_date carry the Oracle value verbatim, including
    # the US date format; fusion_invoice_amount is coerced to a float on assignment, because
    # InvoiceItem sets validate_assignment and its declared type is float | None. The plain
    # fields carry the same coerced value used for matching.
    assert exact.fusion_invoice_number == "INV-2026-00881"
    assert exact.fusion_invoice_date == "08/14/2026"
    assert exact.fusion_invoice_amount == 9500.25
    assert exact.invoice_number == "INV-2026-00881"
    assert exact.invoice_date == "08/14/2026"
    assert exact.invoice_amount == 9500.25

    assert exact_again.fusion_invoice_number == "INV-2026-00882"
    assert exact_again.fusion_invoice_date == "2026-08-14"
    assert exact_again.fusion_invoice_amount == 4750.50
    assert exact_again.invoice_amount == 4750.50

    assert absent.fusion_invoice_number is None
    assert absent.invoice_number == "INV-2026-00777"


def test_response_serialises_the_oracle_amount_as_a_json_number():
    # The declared type of fusion_invoice_amount is float | None, and InvoiceItem validates on
    # assignment, so the value the service puts there is already a float. This is the boundary
    # a client actually sees: mode="json" is what FastAPI's response_model serialises, and a
    # regression to a str would show up here as a JSON string rather than a number.
    mock, side_effect = bip_transport()
    with mock as router:
        router.post(SOAP_URL).mock(side_effect=side_effect)
        result, err, status = run_batch(payment_payload())

    assert (err, status) == (None, None)
    wire = result.model_dump(mode="json")
    amount = wire["invoices"][0]["fusion_invoice_amount"]

    assert isinstance(amount, float)
    assert amount == 9500.25
    assert json.dumps(wire).count('"fusion_invoice_amount": 9500.25') == 1
    # The date stays the Oracle string on purpose: fusion_invoice_date is typed str | None and
    # carries the ledger's own formatting rather than a normalised one.
    assert wire["invoices"][0]["fusion_invoice_date"] == "08/14/2026"


def test_batch_drops_parameter_echo_rows_before_mapping(monkeypatch):
    async def fake_invoices(*_args, **_kwargs):
        return [PARAMETER_ECHO_ROW, *LEDGER_ROWS]

    async def fake_receipts(*_args, **_kwargs):
        return [PARAMETER_ECHO_ROW, *RECEIPT_ROWS]

    for module in (recon, discovery):
        monkeypatch.setattr(module, "fetch_bip_invoices", fake_invoices)
        monkeypatch.setattr(module, "fetch_bip_receipts", fake_receipts)

    payload = ReconciliationRequest(
        customer_name=CUSTOMER,
        payment_reference=RECEIPT_NUMBER,
        invoices=[
            # A number-less, amount-less invoice can only be matched by the parameter echo
            # row, because that row is the only ledger row keyed on the empty number. If the
            # orchestrator skipped _filter_data_rows this would come back MATCHED.
            InvoiceItem(),
            InvoiceItem(invoice_number="INV-2026-00882", invoice_date="2026-08-14", invoice_amount=4750.50),
        ],
    )
    result, err, status = run_batch(payload)

    assert (err, status) == (None, None)
    numberless, genuine = result.invoices
    assert numberless.match_phase == "UNMATCHED"
    assert genuine.match_phase == "MATCHED"
    assert genuine.fusion_invoice_number == "INV-2026-00882"
    assert result.fusion_receipt_number == RECEIPT_NUMBER


def test_a_successful_reconciliation_returns_200_over_http():
    # The only test in the suite that goes all the way through ASGI on the happy path, so the
    # response_model and the null-vs-error contract are exercised on a real response body.
    mock, side_effect = bip_transport()
    with mock as router:
        router.post(SOAP_URL).mock(side_effect=side_effect)
        with TestClient(app) as http:
            response = http.post("/v1/reconcile/batch", json={"customer_name": CUSTOMER, "payment_reference": RECEIPT_NUMBER})

    assert response.status_code == 200
    body = response.json()
    assert body["fusion_customer_name"] == CUSTOMER
    assert body["invoice_count"] == 0
    # The declared float reaches the wire as a JSON number, not as Oracle's string.
    assert body["fusion_applied_amount"] == 14250.75


def test_an_undiscovered_customer_is_a_200_with_a_null_body():
    # Not an error. The contract is a 200 and no payload, so a client can tell "I could not
    # identify this customer" apart from "Oracle was down" without parsing a message.
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(NO_ROWS_CSV)))
        with TestClient(app) as http:
            response = http.post("/v1/reconcile/batch", json={"customer_name": CUSTOMER})

    assert response.status_code == 200
    assert response.json() is None


def test_discovery_cached_receipt_rows_save_a_second_report_fetch(monkeypatch):
    # Step 2 already fetched the receipt report in order to confirm the customer. Handing those
    # rows forward is what keeps a named batch at two Oracle calls instead of three, which is
    # the whole point of returning them from discovery.
    mock, side_effect = bip_transport()
    calls = {"receipts": 0, "invoices": 0}
    real_fetch = oracle_bip.fetch_bip_receipts
    real_invoices = oracle_bip.fetch_bip_invoices

    async def counting_receipts(*args, **kwargs):
        calls["receipts"] += 1
        return await real_fetch(*args, **kwargs)

    async def counting_invoices(*args, **kwargs):
        calls["invoices"] += 1
        return await real_invoices(*args, **kwargs)

    monkeypatch.setattr(discovery, "fetch_bip_receipts", counting_receipts)
    monkeypatch.setattr(discovery, "fetch_bip_invoices", counting_invoices)
    monkeypatch.setattr(recon, "fetch_bip_receipts", counting_receipts)
    monkeypatch.setattr(recon, "fetch_bip_invoices", counting_invoices)

    with mock as router:
        router.post(SOAP_URL).mock(side_effect=side_effect)
        result, err, status = run_batch(ReconciliationRequest(customer_name=CUSTOMER))

    assert (err, status) == (None, None)
    assert result is not None and result.fusion_customer_name == CUSTOMER
    # One receipt fetch: step 2's. The invoice fetch is the one the mapping phase needs.
    assert calls == {"receipts": 1, "invoices": 1}


def test_discovery_failure_returns_a_generic_502_and_never_the_oracle_detail(caplog):
    # 403 is neither a retryable transient status nor a "report not found", so it fails
    # fast instead of burning the BIP retry budget on a credentials problem.
    with respx.mock(assert_all_called=True) as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(403, text=ORACLE_INTERNAL_DETAIL))
        with caplog.at_level(logging.ERROR):
            result, err, status = run_batch(payment_payload())

    assert result is None
    assert status == 502
    assert err == recon.CLIENT_UPSTREAM_ERROR
    assert err == "The reconciliation service is temporarily unable to reach the Oracle ERP ledger."

    # httpx puts the request URL in the exception message and Oracle puts the ORA code in
    # the response body, so both are the realistic leak vectors if this ever regresses to
    # returning str(exc).
    for leaked in (ORACLE_INTERNAL_DETAIL, "ORA-01017", "erp.internal.acme.corp", "403", SOAP_URL):
        assert leaked not in err
    # The operator still needs a breadcrumb, so the failure has to reach the log even
    # though it must never reach the client.
    assert "403" in caplog.text
