"""Customer discovery: which report is asked, in what order, and what each outcome means.

Discovery is the phase that decides how many Oracle round trips a batch costs, and every
branch here either short-circuits the phase or escalates it. It also owns the
parameter-echo row filter, whose behaviour depends on what the CSV parser can actually
produce -- see test_every_row_of_a_parsed_report_shares_one_key_set in test_oracle_bip.py
for the invariant that filter relies on.

The Oracle fetchers are patched; nothing here reaches a tenant.
"""

import asyncio
import base64

import httpx
import pytest

from src.models import InvoiceItem, ReconciliationRequest
from src.services import discovery
from src.services.discovery import _filter_data_rows, _is_data_row, discover_potential_customers
from src.services.oracle_bip import _parse_soap_response_sync

CUSTOMER = "Northwind Traders"
OTHER_CUSTOMER = "Globex Industrial"

RECEIPT_ROW = {"BILL_CUSTOMER_NAME": CUSTOMER, "RECEIPT_NUMBER": "RCPT-45021", "RECEIPT_AMOUNT": "4,750.50"}
INVOICE_ROW = {"BILL_CUSTOMER_NAME": CUSTOMER, "TRANSACTION_NUMBER": "INV-2026-00882"}
OTHER_INVOICE_ROW = {"BILL_CUSTOMER_NAME": OTHER_CUSTOMER, "TRANSACTION_NUMBER": "INV-2026-00882"}
PARAMETER_ECHO_ROW = {"P_CUSTOMER_NAME": CUSTOMER, "P_INVOICE_NUM": " "}


def _soap(csv_text):
    """Wrap a CSV the way BIP does, so the filter tests start from real parser output."""
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    return (
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        '<pub:runReportResponse xmlns:pub="http://xmlns.oracle.com/oxp/service/PublicReportService">'
        f"<pub:reportBytes>{encoded}</pub:reportBytes>"
        "</pub:runReportResponse></soap:Body></soap:Envelope>"
    )


def run_discovery(payload):
    async def _run():
        async with httpx.AsyncClient() as client:
            return await discover_potential_customers(client, "svc-account", "svc-password", payload)

    return asyncio.run(_run())


class FetchRecorder:
    """Stand-in for the two BIP fetchers that records how they were called.

    A response may be a fixed list or a callable taking the keyword arguments, which is how
    a test makes a query succeed or fail depending on which level of the Step 3 sequence
    asked for it.
    """

    def __init__(self, receipts=None, invoices=None):
        self.receipt_calls = []
        self.invoice_calls = []
        self._receipts = receipts if receipts is not None else []
        self._invoices = invoices if invoices is not None else []

    @staticmethod
    def _respond(spec, **kwargs):
        return spec(**kwargs) if callable(spec) else list(spec)

    def install(self, monkeypatch):
        async def fake_receipts(_client, _user, _pwd, **kwargs):
            self.receipt_calls.append(kwargs)
            return self._respond(self._receipts, **kwargs)

        async def fake_invoices(_client, _user, _pwd, **kwargs):
            self.invoice_calls.append(kwargs)
            return self._respond(self._invoices, **kwargs)

        monkeypatch.setattr(discovery, "fetch_bip_receipts", fake_receipts)
        monkeypatch.setattr(discovery, "fetch_bip_invoices", fake_invoices)
        return self


# ── the row filter ──────────────────────────────────────────────────────────────────────────


def test_is_data_row():
    # Row with just params
    assert not _is_data_row({"P_ORG_ID": "123", "P_DATE": "2023"})

    # Row with actual data
    assert _is_data_row({"BILL_CUSTOMER_NAME": "Test Corp", "TRANSACTION_NUMBER": "123"})

def test_filter_data_rows():
    rows = [
        {"P_ORG_ID": "123"},
        {"BILL_CUSTOMER_NAME": "Test Corp"}
    ]
    filtered = _filter_data_rows(rows)
    assert len(filtered) == 1
    assert filtered[0]["BILL_CUSTOMER_NAME"] == "Test Corp"

def test_filter_data_rows_empty():
    assert _filter_data_rows([]) == []


def test_is_data_row_accepts_every_column_the_matcher_indexes():
    # Each name here is one the engine actually reads, so a column missing from this set is a
    # row the filter would drop and the matcher could never have used.
    for column in (
        "BILL_CUSTOMER_NAME",
        "TRANSACTION_NUMBER",
        "RECEIPT_NUMBER",
        "CUSTOMER_NAME",
        "ACCOUNT_NUMBER",
        "BUSINESS_UNIT",
        "CURRENCY",
        "INVOICE_STATUS",
        "RECEIPT_STATUS_CODE",
    ):
        assert _is_data_row({column: "x"}), column
    assert not _is_data_row({"P_CUSTOMER_NAME": "x"})
    assert not _is_data_row({})


def test_is_data_row_is_case_and_whitespace_insensitive():
    assert _is_data_row({" bill_customer_name ": "Acme"})
    assert _is_data_row({"BILL_CUSTOMER_NAME".lower(): "Acme"})


def test_is_data_row_sees_through_a_leading_byte_order_mark():
    # str.strip() does not remove U+FEFF, and an Excel-exported CSV puts it on the first
    # column name. Without the lstrip, a report whose only data column is the first one would
    # be filtered away as a parameter echo.
    assert _is_data_row({"\ufeffBILL_CUSTOMER_NAME": "Acme"})
    assert not _is_data_row({"\ufeffP_CUSTOMER_NAME": "Acme"})


def test_filter_drops_a_whole_parameter_only_report():
    # This is the case the production path can actually produce: a data model whose column
    # names are all P_*, so every row is a parameter echo and the result set is empty.
    csv_text = "P_CUSTOMER_NAME,P_INVOICE_NUM,P_INVOICE_AMOUNT,P_INVOICE_DATE\nAcme,INV-1,,\nAcme,INV-2,,\n"
    rows = _parse_soap_response_sync(_soap(csv_text))
    assert len(rows) == 2
    assert _filter_data_rows(rows) == []


def test_filter_keeps_a_whole_report_with_a_data_column():
    # The mirror image, and the reason the filter is all-or-nothing rather than per-row: a
    # parameter-echo column sitting alongside real data columns does not make the row a
    # parameter echo, because DictReader gives every row of the report the same keys.
    csv_text = "P_CUSTOMER_NAME,BILL_CUSTOMER_NAME,TRANSACTION_NUMBER\nAcme,Acme Corp,INV-1\nAcme,Acme Corp,INV-2\n"
    rows = _parse_soap_response_sync(_soap(csv_text))
    assert len(_filter_data_rows(rows)) == 2


def test_filter_passes_an_empty_row_list_through_untouched():
    assert _filter_data_rows([]) == []


# ── step 1: the payment reference ───────────────────────────────────────────────────────────


def test_step_1_identifies_the_customer_from_the_payment_reference(monkeypatch):
    recorder = FetchRecorder(receipts=[RECEIPT_ROW]).install(monkeypatch)
    payload = ReconciliationRequest(customer_name=CUSTOMER, payment_reference="RCPT-45021")

    name, cached = run_discovery(payload)

    assert (name, cached) == (CUSTOMER, None)
    # One query, and the invoice report is never touched: the phase short-circuited.
    assert recorder.receipt_calls == [{"receipt_number": "RCPT-45021"}]
    assert recorder.invoice_calls == []


def test_step_1_takes_the_name_from_the_first_receipt_row(monkeypatch):
    FetchRecorder(receipts=[RECEIPT_ROW, {"BILL_CUSTOMER_NAME": OTHER_CUSTOMER}]).install(monkeypatch)
    name, _ = run_discovery(ReconciliationRequest(payment_reference="RCPT-45021"))
    assert name == CUSTOMER


def test_a_receipt_row_with_no_customer_name_is_not_an_identification(monkeypatch):
    # An empty name must not short-circuit the phase with a falsy answer; it has to fall
    # through to the next step rather than report "no customer".
    recorder = FetchRecorder(receipts=[{"BILL_CUSTOMER_NAME": "", "RECEIPT_NUMBER": "RCPT-1"}], invoices=[INVOICE_ROW]).install(
        monkeypatch
    )
    payload = ReconciliationRequest(
        payment_reference="RCPT-45021", invoices=[InvoiceItem(invoice_number="INV-2026-00882")]
    )

    name, _ = run_discovery(payload)

    assert name == CUSTOMER
    # Step 1 asked by reference, step 2 was skipped because the payload named no customer.
    assert recorder.receipt_calls == [{"receipt_number": "RCPT-45021"}]
    assert len(recorder.invoice_calls) == 1


# ── step 2: the customer name, and the cached ledger ────────────────────────────────────────


def test_step_2_confirms_the_name_and_hands_back_the_receipt_rows(monkeypatch):
    # Returning the rows lets process_reconciliation_batch skip its own receipt fetch, so this
    # is the path that turns a two-report phase 2 into a one-report one.
    recorder = FetchRecorder(receipts=[RECEIPT_ROW], invoices=[INVOICE_ROW]).install(monkeypatch)
    payload = ReconciliationRequest(customer_name=CUSTOMER)

    name, cached = run_discovery(payload)

    assert name == CUSTOMER
    assert cached == [RECEIPT_ROW]
    # Step 1 is skipped entirely when there is no payment reference.
    assert recorder.receipt_calls == [{"customer_name": CUSTOMER}]
    assert recorder.invoice_calls == []


def test_step_2_miss_falls_through_to_invoice_discovery(monkeypatch):
    recorder = FetchRecorder(invoices=[INVOICE_ROW]).install(monkeypatch)
    payload = ReconciliationRequest(
        customer_name="A Customer With No Receipts",
        payment_reference="RCPT-0000",
        invoices=[InvoiceItem(invoice_number="INV-2026-00882")],
    )

    name, cached = run_discovery(payload)

    assert (name, cached) == (CUSTOMER, None)
    # Step 1 by reference, then step 2 by name, then step 3 by invoice.
    assert recorder.receipt_calls == [{"receipt_number": "RCPT-0000"}, {"customer_name": "A Customer With No Receipts"}]
    assert len(recorder.invoice_calls) == 1


def test_a_parameter_only_receipt_report_does_not_confirm_the_customer(monkeypatch):
    # The production-reachable filter case: BIP answered, but with a parameter echo rather
    # than ledger rows. Step 2 must not treat "a response arrived" as "the customer exists".
    FetchRecorder(receipts=[PARAMETER_ECHO_ROW], invoices=[INVOICE_ROW]).install(monkeypatch)
    payload = ReconciliationRequest(
        customer_name="A Customer With No Receipts",
        payment_reference="RCPT-0000",
        invoices=[InvoiceItem(invoice_number="INV-2026-00882")],
    )

    name, cached = run_discovery(payload)

    assert name == CUSTOMER
    assert cached is None


# ── step 3: the invoice sequence ────────────────────────────────────────────────────────────


def test_both_name_and_reference_null_skip_straight_to_invoices(monkeypatch):
    recorder = FetchRecorder(invoices=[INVOICE_ROW]).install(monkeypatch)

    name, cached = run_discovery(ReconciliationRequest(invoices=[InvoiceItem(invoice_number="INV-2026-00882")]))

    assert (name, cached) == (CUSTOMER, None)
    assert recorder.receipt_calls == []
    assert len(recorder.invoice_calls) == 1


def test_step_3_queries_on_the_invoice_number_alone_first(monkeypatch):
    # The cheapest query first. The number is the unique identifier, so a hit here ends the
    # phase without ever sending the amount or the date.
    recorder = FetchRecorder(invoices=[INVOICE_ROW]).install(monkeypatch)
    payload = ReconciliationRequest(invoices=[InvoiceItem(invoice_number="INV-2026-00882", invoice_date="2026-08-14", invoice_amount=4750.50)])

    name, _ = run_discovery(payload)

    assert name == CUSTOMER
    assert recorder.invoice_calls == [{"invoice_number": "INV-2026-00882"}]


def test_step_3_escalates_to_the_amount_level_when_two_numbers_disagree(monkeypatch):
    # Two invoice numbers resolving to two different customers is the ambiguity the sequence
    # exists to resolve. Level 2 adds the amount, which narrows it to one.
    calls = []

    def invoices_for(**kwargs):
        calls.append(sorted(kwargs))
        if "invoice_amount" in kwargs:
            return [INVOICE_ROW]
        return [INVOICE_ROW] if kwargs["invoice_number"] == "INV-A" else [OTHER_INVOICE_ROW]

    FetchRecorder(invoices=invoices_for).install(monkeypatch)
    payload = ReconciliationRequest(
        invoices=[
            InvoiceItem(invoice_number="INV-A", invoice_date="2026-08-14", invoice_amount=4750.50),
            InvoiceItem(invoice_number="INV-B", invoice_date="2026-08-14", invoice_amount=4750.50),
        ]
    )

    name, _ = run_discovery(payload)

    assert name == CUSTOMER
    by_number_only = [c for c in calls if "invoice_amount" not in c]
    with_amount = [c for c in calls if "invoice_amount" in c]
    # as_completed does not preserve submission order, so the calls are compared as a set.
    assert sorted(tuple(c) for c in by_number_only) == [("invoice_number",), ("invoice_number",)]
    assert sorted(tuple(c) for c in with_amount) == [("invoice_amount", "invoice_number")] * 2


def test_step_3_reaches_the_date_only_level_when_the_amount_does_not_narrow_it(monkeypatch):
    calls = []

    def invoices_for(**kwargs):
        calls.append(sorted(kwargs))
        return [INVOICE_ROW] if kwargs["invoice_number"] == "INV-A" else [OTHER_INVOICE_ROW]

    FetchRecorder(invoices=invoices_for).install(monkeypatch)
    payload = ReconciliationRequest(
        invoices=[
            InvoiceItem(invoice_number="INV-A", invoice_date="2026-08-14", invoice_amount=4750.50),
            InvoiceItem(invoice_number="INV-B", invoice_date="2026-08-14", invoice_amount=4750.50),
        ]
    )

    name, _ = run_discovery(payload)

    assert name is None
    assert sum(1 for c in calls if "invoice_date" in c) == 2
    assert all("invoice_date" in c for c in calls if "invoice_amount" in c and c.count("invoice") == 3)


def test_step_3_sends_no_date_parameter_when_the_invoice_has_none(monkeypatch):
    # Sending a blank date is what produces ORA-01861 on the Oracle side, so the engine
    # escalates only through the levels the payload can actually support.
    calls = []

    def invoices_for(**kwargs):
        calls.append(sorted(kwargs))
        return [INVOICE_ROW] if kwargs["invoice_number"] == "INV-A" else [OTHER_INVOICE_ROW]

    FetchRecorder(invoices=invoices_for).install(monkeypatch)
    payload = ReconciliationRequest(
        invoices=[
            InvoiceItem(invoice_number="INV-A", invoice_amount=4750.50),
            InvoiceItem(invoice_number="INV-B", invoice_amount=4750.50),
        ]
    )

    run_discovery(payload)

    assert not [c for c in calls if "invoice_date" in c]
    # Three levels, two invoice numbers each: with no date to add, level 3 repeats level 2
    # rather than sending a blank the Oracle report would reject with ORA-01861.
    assert len(calls) == 6


def test_invoices_without_a_number_are_never_queried(monkeypatch):
    # A number-less line cannot be searched for, and querying it with a blank parameter would
    # ask Oracle for the whole ledger.
    recorder = FetchRecorder(invoices=[INVOICE_ROW]).install(monkeypatch)
    payload = ReconciliationRequest(invoices=[InvoiceItem(invoice_amount=100.0), InvoiceItem(invoice_number="INV-2026-00882")])

    name, _ = run_discovery(payload)

    assert name == CUSTOMER
    assert recorder.invoice_calls == [{"invoice_number": "INV-2026-00882"}]


def test_a_batch_with_no_usable_invoices_skips_the_sequence_entirely(monkeypatch):
    recorder = FetchRecorder().install(monkeypatch)
    name, _ = run_discovery(ReconciliationRequest(invoices=[InvoiceItem()]))
    assert name is None
    assert recorder.invoice_calls == []


def test_a_failing_invoice_query_does_not_abort_the_sequence(monkeypatch):
    # One unreachable invoice must not lose the customer that another line can identify.
    async def fake_invoices(_client, _user, _pwd, **kwargs):
        recorder.invoice_calls.append(kwargs)
        if kwargs["invoice_number"] == "INV-BAD":
            raise RuntimeError("ORA-01403 no data found")
        return [INVOICE_ROW]

    recorder = FetchRecorder().install(monkeypatch)
    monkeypatch.setattr(discovery, "fetch_bip_invoices", fake_invoices)
    payload = ReconciliationRequest(
        invoices=[InvoiceItem(invoice_number="INV-BAD"), InvoiceItem(invoice_number="INV-2026-00882")]
    )

    name, _ = run_discovery(payload)

    assert name == CUSTOMER
    assert len(recorder.invoice_calls) == 2


def test_no_level_resolving_a_customer_reports_nothing(monkeypatch):
    # (None, None) is not an error: it is how the service says "I could not identify this
    # customer", which the API turns into a null 200 body rather than a 502.
    FetchRecorder().install(monkeypatch)
    name, cached = run_discovery(
        ReconciliationRequest(invoices=[InvoiceItem(invoice_number="INV-NOT-IN-LEDGER")])
    )
    assert (name, cached) == (None, None)


def test_a_conflict_never_resolves_to_an_arbitrary_customer(monkeypatch):
    # The short-circuit is the point: two candidates from two different queries means the
    # answer is unknown, and picking one would mis-attribute somebody's ledger.
    def invoices_for(**kwargs):
        return [INVOICE_ROW] if kwargs["invoice_number"] == "INV-A" else [OTHER_INVOICE_ROW]

    FetchRecorder(invoices=invoices_for).install(monkeypatch)
    payload = ReconciliationRequest(
        invoices=[InvoiceItem(invoice_number="INV-A"), InvoiceItem(invoice_number="INV-B")]
    )

    name, _ = run_discovery(payload)

    assert name is None


def test_a_conflict_inside_one_response_is_not_detected(monkeypatch):
    # Recorded as current behaviour, not endorsed. _discover_by_invoice_sequence reads the
    # customer name from rows[0] of each response, so a single report that returns two
    # different customers is not seen as ambiguous -- only a disagreement *between* queries is.
    # Whether that matters depends on whether a tenant can hold one invoice number under two
    # customers, which is a question about the Oracle data model rather than about this code.
    FetchRecorder(invoices=[INVOICE_ROW, OTHER_INVOICE_ROW]).install(monkeypatch)
    name, _ = run_discovery(ReconciliationRequest(invoices=[InvoiceItem(invoice_number="INV-SHARED")]))

    assert name == CUSTOMER


def test_a_parameter_only_invoice_report_is_not_a_customer(monkeypatch):
    FetchRecorder(invoices=[PARAMETER_ECHO_ROW]).install(monkeypatch)
    name, _ = run_discovery(ReconciliationRequest(invoices=[InvoiceItem(invoice_number="INV-2026-00882")]))
    assert name is None


def test_discovery_asks_for_one_report_per_invoice_number(monkeypatch):
    # Phase 3 must be bounded by the batch, not by a ledger scan, or a 2,500-line batch
    # becomes 2,500 Oracle calls.
    recorder = FetchRecorder(invoices=[INVOICE_ROW]).install(monkeypatch)
    numbers = [f"INV-{i:05d}" for i in range(250)]
    payload = ReconciliationRequest(invoices=[InvoiceItem(invoice_number=n) for n in numbers])

    name, _ = run_discovery(payload)

    assert name == CUSTOMER
    assert len(recorder.invoice_calls) == 250
    assert {call["invoice_number"] for call in recorder.invoice_calls} == set(numbers)


def test_a_whitespace_only_customer_name_is_treated_as_absent(monkeypatch):
    # sanitize_string_val already turns " " into None on the way in, so this asserts the
    # discovery layer agrees rather than querying Oracle for a blank customer.
    recorder = FetchRecorder(invoices=[INVOICE_ROW]).install(monkeypatch)
    name, _ = run_discovery(
        ReconciliationRequest(
            customer_name="   ", payment_reference="   ", invoices=[InvoiceItem(invoice_number="INV-2026-00882")]
        )
    )

    assert name == CUSTOMER
    assert recorder.receipt_calls == []


def test_discovery_propagates_an_upstream_failure_to_the_caller(monkeypatch):
    # process_reconciliation_batch turns this into the 502 with the fixed message, so the
    # exception has to escape rather than be swallowed into a "not identified" answer.
    async def boom(*_args, **_kwargs):
        raise RuntimeError("ORA-01017 invalid username/password")

    monkeypatch.setattr(discovery, "fetch_bip_receipts", boom)

    with pytest.raises(RuntimeError, match="ORA-01017"):
        run_discovery(ReconciliationRequest(payment_reference="RCPT-45021"))
