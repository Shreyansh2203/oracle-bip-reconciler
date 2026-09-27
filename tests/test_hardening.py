"""The hardening this service needed, asserted rather than assumed.

Each test here corresponds to a defect that was found by reading the code and reproduced
before the fix. Every one of them fails against the pre-fix tree; the docstring on each says
what it replaced. Nothing in this module reaches a real tenant: Oracle is either mocked at
the transport or replaced with a stub, and the only credentials in play are the placeholders
tests/conftest.py seeds.
"""

import asyncio
import base64
import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.core.config import Settings, settings
from src.core.dependencies import PUBLIC_ACCESS_DISABLED, rate_limit_key
from src.main import app
from src.models import InvoiceItem, ReconciliationRequest
from src.services import oracle_bip
from src.services import reconciliation as recon
from src.services.oracle_bip import _bip_cache, _get_cache_key, _run_bip_report
from src.services.reconciliation import map_ledger_to_payload
from src.utils.date_formatter import format_oracle_date
from src.utils.validators import sanitize_float_val

SOAP_URL = f"{settings.ORACLE_URL.rstrip('/')}/xmlpserver/services/ExternalReportWSSService"


@pytest.fixture(autouse=True)
def clear_bip_cache():
    # The report cache is module-level and outlives a test, so a second test asking for the
    # same report would be served from memory and never reach the mocked transport.
    _bip_cache.local.clear()
    oracle_bip._inflight.clear()
    yield
    _bip_cache.local.clear()
    oracle_bip._inflight.clear()


def _payload(**fields):
    return ReconciliationRequest(customer_name="Acme Corp", **fields)


# ---------------------------------------------------------------------------
# 1. The endpoint served any customer's whole ledger to anyone who named them.
#
# ORACLE_USER and ORACLE_PASS are one tenant-wide BI Publisher service account, so a caller
# naming a customer received that customer's entire invoice and receipt ledger, at ten
# requests a minute, with no credential. SECURITY.md called authentication out of scope,
# and both vercel.json and render.yaml produced a publicly reachable URL, so "out of scope"
# meant "open to the internet".
#
# Break it with: default ALLOW_UNAUTHENTICATED_ACCESS to True in src/core/config.py.
# ---------------------------------------------------------------------------


def test_the_refusal_is_the_default_not_something_an_operator_has_to_ask_for():
    # The guard is only worth having if forgetting to configure it is the safe outcome.
    fresh = Settings(
        ORACLE_URL="https://oracle.test.invalid",
        ORACLE_USER="u",
        ORACLE_PASS="p",
        ALLOW_UNAUTHENTICATED_ACCESS=False,
    )
    assert fresh.ALLOW_UNAUTHENTICATED_ACCESS is False
    assert Settings.model_fields["ALLOW_UNAUTHENTICATED_ACCESS"].default is False


def test_the_reconcile_endpoint_serves_nobody_until_an_operator_opts_in(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_ACCESS", False)

    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text="<x/>"))
        with TestClient(app) as http:
            response = http.post(
                "/v1/reconcile/batch",
                json={"customer_name": "Someone Else's Customer"},
            )

    assert response.status_code == 503
    assert response.json()["detail"] == PUBLIC_ACCESS_DISABLED
    # The ledger is never read, so there is nothing for the refusal to have leaked.
    assert route.call_count == 0


def test_the_health_endpoints_stay_reachable_while_the_endpoint_is_refused(monkeypatch):
    # An operator must be able to tell "deliberately closed" from "broken" without a
    # credential, and an orchestrator must never be locked out of liveness.
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_ACCESS", False)

    with TestClient(app) as http:
        assert http.get("/health").status_code == 200
        assert http.get("/ready").status_code == 200
        assert http.get("/").status_code == 200


def test_opting_in_restores_the_reconciliation_path(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_ACCESS", True)

    async def failed(*_args, **_kwargs):
        return None, recon.CLIENT_UPSTREAM_ERROR, 502

    monkeypatch.setattr("src.api.routers.reconciliation.process_reconciliation_batch", failed)

    with TestClient(app) as http:
        response = http.post("/v1/reconcile/batch", json={"customer_name": "Acme Corp"})

    assert response.status_code == 502


# ---------------------------------------------------------------------------
# 2. A one-character payment reference matched any receipt containing it.
#
# The comparison at map_ledger_to_payload was a bidirectional substring test with no minimum
# length, so payment_reference "1" matched RECEIPT_NUMBER "RCP-999001", and the matched
# row's currency, status, customer number and applied amount were then reported as this
# payment's -- with total_amount filled in from it, since a caller sending a bare fragment
# has no total of their own. The invoice side already refused the same pair through the
# floor in _is_num_ok; the receipt side never had one.
#
# Break it with: replace the _is_substring_num_ok call in the receipt loop with a bare
# `receipt_number.lower() in cand_num.lower()`.
# ---------------------------------------------------------------------------


def _receipt_row(**overrides):
    row = {
        "BILL_CUSTOMER_NAME": "Someone Else",
        "RECEIPT_NUMBER": "RCP-999001",
        "RECEIPT_DATE": "2026-01-01",
        "RECEIPT_AMOUNT": "1.00",
        "CURRENCY": "EUR",
        "RECEIPT_STATUS_CODE": "REVERSED",
        "BILL_CUSTOMER_NUMBER": "C-00",
    }
    row.update(overrides)
    return row


def test_a_one_character_payment_reference_matches_no_receipt_at_all():
    payload = _payload(payment_reference="1", total_amount=None)
    map_ledger_to_payload(payload, "Acme Corp", [_receipt_row()], [])

    assert payload.fusion_receipt_number is None
    assert payload.fusion_currency is None
    assert payload.fusion_receipt_status_code is None
    assert payload.fusion_customer_number is None
    assert payload.fusion_applied_amount is None
    # The harm: a caller who sent a fragment and no total was handed somebody else's amount.
    assert payload.total_amount is None
    # And its own reference is not overwritten by the row that was not matched.
    assert payload.payment_reference == "1"


@pytest.mark.parametrize("reference", ["1", "9", "RCP", "rcp-"])
def test_no_fragment_short_of_the_floor_can_claim_a_receipt(reference):
    payload = _payload(payment_reference=reference)
    map_ledger_to_payload(payload, "Acme Corp", [_receipt_row()], [])
    assert payload.fusion_receipt_number is None


def test_a_truncated_receipt_reference_still_matches():
    # The floor must not cost the OCR-truncation recovery the substring test exists for:
    # six characters against a long reference is a real truncation, not a coincidence.
    payload = _payload(payment_reference="RCP-99")
    map_ledger_to_payload(payload, "Acme Corp", [_receipt_row()], [])
    assert payload.fusion_receipt_number == "RCP-999001"
    assert payload.total_amount == 1.0


def test_the_receipt_and_invoice_sides_share_one_length_floor():
    from src.services.reconciliation import MIN_NUMBER_MATCH_LENGTH

    assert MIN_NUMBER_MATCH_LENGTH == 5
    # The same constant decides both sides, so they cannot drift apart again.
    for short, long_ in (("1", "RCP-999001"), ("RCP", "RCP-999001"), ("RCP-99", "R")):
        assert recon._is_substring_num_ok(short, long_) is False, (short, long_)
    assert recon._is_substring_num_ok("RCP-99", "RCP-999001") is True
    # And the invoice side, which always had the floor, still refuses the same pair.
    assert recon._is_num_ok("1", "RCP-999001") is False


def test_an_empty_number_is_not_a_match_on_either_side():
    # A ledger row with no number, and a payload line with no number, both key to "" so
    # they have to be refused before any comparison is attempted.
    assert recon._is_substring_num_ok("", "RCP-999001") is False
    assert recon._is_substring_num_ok("RCP-999001", "") is False
    assert recon._is_num_ok("", "RCP-999001") is False
    assert recon._is_num_ok("INV-0001", "") is False


def test_an_unreadable_amount_pair_is_not_treated_as_equal():
    # _is_amount_equal used to be the one comparison whose own error path was uncovered.
    # A value sanitize_float_val cannot read at all must not compare equal to anything,
    # including another value it also cannot read.
    assert recon._is_amount_equal("not a number", "not a number") is False
    assert recon._is_amount_equal(None, "1.00") is False
    assert recon._is_amount_equal("1.00", "1.00") is True


# ---------------------------------------------------------------------------
# 3. An ambiguous numeric date was resolved by list order, differently per year width.
#
# date_formatter listed %m-%d-%Y before %d-%m-%Y but %d-%m-%y before %m-%d-%y, so the same
# spelling read as two different calendar days depending on whether the year had four
# digits. A payload dated 05-06-2026 then matched whichever ledger row the guess happened
# to land on, and the response carried that row's number, date and amount as though it were
# the invoice's.
#
# Break it with: delete the is_ambiguous_numeric_date guard from format_oracle_date.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["05-06-2026", "05-06-26", "05/06/2026", "1-2-26", "05.06.2026"])
def test_a_day_and_month_that_are_both_valid_are_refused(spelling):
    assert format_oracle_date(spelling) is None


def test_the_two_year_widths_are_read_the_same_way():
    # The defect was not that a precedence existed, it was that the precedence changed with
    # the width of the year. Both spellings now resolve identically: to nothing.
    assert format_oracle_date("05-06-2026") == format_oracle_date("05-06-26") is None
    assert format_oracle_date("13-06-2026") == format_oracle_date("13-06-26") == "2026-06-13"


def test_an_ambiguous_date_is_logged_rather_than_quietly_dropped(caplog):
    with caplog.at_level(logging.WARNING, logger="reconciliation_api.date_formatter"):
        assert format_oracle_date("05-06-2026") is None
    assert any("05-06-2026" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("2026-06-05", "2026-06-05"),
        ("08/14/2026", "2026-08-14"),
        ("13-06-2026", "2026-06-13"),
        ("31/12/2026", "2026-12-31"),
        ("05-05-2026", "2026-05-05"),
        ("05-Jun-2026", "2026-06-05"),
        ("20261005", "2026-10-05"),
    ],
)
def test_an_unambiguous_date_is_still_parsed(spelling, expected):
    # A field above 12, an ISO year-first spelling or a month name all decide the order, so
    # the refusal costs only the genuinely undecidable cases.
    assert format_oracle_date(spelling) == expected


def test_an_ambiguous_date_binds_no_ledger_row_rather_than_the_wrong_one():
    # The pinned behaviour was that a payload dated 05-06-2026 bound a 5 June row, and it
    # bound it because the parse guessed 6 May. Refusing the date means the date agrees with
    # nothing, and the line goes to review instead of to the wrong customer row.
    payload = _payload(invoices=[InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="05-06-2026", invoice_amount=50.0)])
    ledger = [
        {"TRANSACTION_NUMBER": "INV-A", "TRANSACTION_DATE": "2026-06-05", "TRANSACTION_TOTAL": "50.00"},
        {"TRANSACTION_NUMBER": "INV-B", "TRANSACTION_DATE": "2026-05-06", "TRANSACTION_TOTAL": "50.00"},
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], ledger)
    assert payload.invoices[0].match_phase == "UNMATCHED"
    assert payload.invoices[0].fusion_invoice_number is None


# ---------------------------------------------------------------------------
# 4. sanitize_float_val never failed loudly, and corrupted a European spelling.
#
# `(1,234.56)` came back as None, so an accounting-negative credit memo vanished from the
# reconciliation, and `1.234,56` came back as 1.23456 -- a confidently wrong number, off by
# three orders of magnitude, which then went on to be matched on.
#
# Break it with: replace the body of sanitize_float_val with the old
# `float(value.replace(",", ""))` and no logging.
# ---------------------------------------------------------------------------


def test_an_accounting_negative_is_read_as_a_negative_number():
    assert sanitize_float_val("(1,234.56)") == -1234.56
    assert sanitize_float_val("(1.234,56)") == -1234.56
    assert sanitize_float_val(" ( 1,234.56 ) ") == -1234.56


def test_european_grouping_is_not_silently_reinterpreted_as_us_grouping():
    # The old code dropped every comma, so 1.234,56 became 1.23456.
    assert sanitize_float_val("1.234,56") == 1234.56
    assert sanitize_float_val("1,234.56") == 1234.56
    assert sanitize_float_val("1234,56") == 1234.56


def test_a_lone_dot_is_a_decimal_point_and_a_lone_comma_can_be_either():
    # 1.234 out of a US-formatted Oracle cell is one-and-a-bit, and reading it as 1234 is a
    # 1000x error; ,50 is half a unit, and reading it as 50 is a 100x error.
    assert sanitize_float_val("1.234") == 1.234
    assert sanitize_float_val("1,234") == 1234.0
    assert sanitize_float_val(",50") == 0.5
    assert sanitize_float_val("1,23") == 1.23


def test_a_number_that_cannot_be_read_is_refused_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="reconciliation_api.validators"):
        assert sanitize_float_val("1.2.3") is None
        assert sanitize_float_val("1,23,456.78") is None
        assert sanitize_float_val("not a number") is None
        assert sanitize_float_val("1,23.4,5.6") is None
    # Four refusals, four warnings: the failure is observable rather than silent.
    refusals = [r for r in caplog.records if "Refusing to read" in r.getMessage()]
    assert len(refusals) == 4


def test_a_bare_separator_and_an_oversized_group_are_refused():
    # A leading or trailing separator is not a number, and a group that is not three digits
    # is not grouping.
    for spelling in (".5.", ",5,", "1,23,456", "1,2,3"):
        assert sanitize_float_val(spelling) is None, spelling


def test_a_non_finite_value_is_dropped_without_claiming_it_was_unreadable(caplog):
    # `inf` and `nan` parse, so they are not a malformed cell -- there is simply no finite
    # amount to reconcile, and the row goes unmatched rather than being called a bad format.
    with caplog.at_level(logging.WARNING, logger="reconciliation_api.validators"):
        assert sanitize_float_val("inf") is None
        assert sanitize_float_val("nan") is None
        assert sanitize_float_val(float("inf")) is None
        # A whole number past float's range is not a malformed cell either, and takes the
        # same path through the decimal branch.
        assert sanitize_float_val("9" * 400 + ".5") is None
    assert not [r for r in caplog.records if "Refusing to read" in r.getMessage()]


def test_a_refused_amount_cannot_reconcile_against_a_row_it_does_not_match():
    # The refusal has to cost an agreement rather than bind a wrong row, which is what the
    # caller's None fallback is for.
    payload = _payload(invoices=[InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount="1.234,56")])
    assert payload.invoices[0].invoice_amount == 1234.56
    ledger = [
        {"TRANSACTION_NUMBER": "INV-0001", "TRANSACTION_DATE": "2026-01-01", "TRANSACTION_TOTAL": "1.23456"},
        {"TRANSACTION_NUMBER": "INV-0001", "TRANSACTION_DATE": "2026-01-01", "TRANSACTION_TOTAL": "1,234.56"},
    ]
    map_ledger_to_payload(payload, "Acme Corp", [], ledger)
    assert payload.invoices[0].fusion_invoice_number == "INV-0001"
    assert payload.invoices[0].invoice_amount == 1234.56


# ---------------------------------------------------------------------------
# 5. The bare fuzzy-number bucket took matches_fuzzy_num[0] with no uniqueness check, while
#    the amount-only and date-only buckets either side of it required len(...) == 1.
#
# Nothing corroborates a row in that bucket: the only reason it is a candidate is that its
# number vaguely resembles the payload's. Two rows reached it on resemblance alone, and
# ledger order picked between them.
#
# Break it with: change `elif len(matches_fuzzy_num) == 1:` back to `elif matches_fuzzy_num:`.
# ---------------------------------------------------------------------------


def _row(number, date, amount):
    return {
        "BILL_CUSTOMER_NAME": "Acme",
        "TRANSACTION_NUMBER": number,
        "TRANSACTION_DATE": date,
        "TRANSACTION_TOTAL": amount,
    }


def test_a_fuzzy_number_agreed_by_two_rows_matches_neither():
    payload = _payload(
        invoices=[InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=999.0)]
    )
    map_ledger_to_payload(
        payload,
        "Acme Corp",
        [],
        [_row("INV-0001235", "2020-01-01", "5.00"), _row("INV-0001236", "2020-01-02", "6.00")],
    )
    assert payload.invoices[0].match_phase == "UNMATCHED"
    assert payload.invoices[0].match_rule is None


def test_a_lone_fuzzy_number_still_matches_on_its_own():
    # The gate must not cost the tier that exists: one near-miss number and nothing else
    # agreeing is the last-resort case it was written for.
    payload = _payload(
        invoices=[InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=999.0)]
    )
    map_ledger_to_payload(payload, "Acme Corp", [], [_row("INV-0001235", "2020-01-01", "5.00")])
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].match_rule == "FUZZY_NUMBER_ALONE"


def test_an_exact_number_two_rows_share_now_refuses_instead_of_taking_the_first():
    # The 1-way tier's uniqueness guard used to be inert for an exact number, because the
    # fuzzy bucket took the first of the same rows immediately afterwards. With the gate the
    # guard does what README.md says it does.
    payload = _payload(
        invoices=[InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=999.0)]
    )
    map_ledger_to_payload(
        payload,
        "Acme Corp",
        [],
        [_row("INV-0001", "2026-05-05", "100.00"), _row("INV-0001", "2026-06-06", "200.00")],
    )
    assert payload.invoices[0].match_phase == "UNMATCHED"


# ---------------------------------------------------------------------------
# 6. The caller-supplied strings were unbounded, and they reach the SOAP envelope and the
#    report cache key, both of which are sized by whatever the caller sends.
#
# Break it with: drop max_length from the InvoiceItem and ReconciliationRequest fields.
# ---------------------------------------------------------------------------


def test_an_oversized_caller_string_is_rejected_rather_than_forwarded_to_oracle():
    from pydantic import ValidationError

    oversized = "A" * 5000
    for build in (
        lambda: InvoiceItem(invoice_number=oversized),
        lambda: InvoiceItem(description=oversized),
        lambda: ReconciliationRequest(customer_name=oversized),
        lambda: ReconciliationRequest(payment_reference=oversized),
    ):
        with pytest.raises(ValidationError):
            build()


def test_the_bounds_allow_a_long_but_ordinary_reference():
    # The point is to bound abuse, not to refuse a 40-character ERP reference.
    assert InvoiceItem(invoice_number="X" * 120).invoice_number is not None
    assert ReconciliationRequest(customer_name="A" * 200).customer_name is not None


def test_an_oversized_field_is_a_422_that_does_not_echo_the_payload_back():
    invoices = [{"invoice_number": "A" * 5000} for _ in range(3)]
    with TestClient(app) as http:
        response = http.post("/v1/reconcile/batch", json={"invoices": invoices})

    assert response.status_code == 422
    assert "A" * 5000 not in response.text
    detail = response.json()["detail"]
    assert detail and all(set(item) <= {"type", "loc", "msg"} for item in detail)


# ---------------------------------------------------------------------------
# 7. A cold start issued one BIP request per fan-out member, all for the same cache key.
#
# _discover_by_invoice_sequence fans out up to DEFAULT_CONCURRENCY fetches, and _bip_cache
# only answered for a key it already held, so a key nobody had asked for before was fetched
# once per concurrent caller. The report is tenant-wide and slow, which is the exact
# request an upstream rate limit punishes.
#
# Break it with: make _fetch_once call factory() unconditionally.
# ---------------------------------------------------------------------------


def _soap(csv_text):
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        f"<pub:runReportResponse xmlns:pub=\"http://xmlns.oracle.com/oxp/service/PublicReportService\">"
        f"<pub:reportOutput><pub:reportBytes>{encoded}</pub:reportBytes></pub:reportOutput>"
        "</pub:runReportResponse></soap:Body></soap:Envelope>"
    )


ROWS_CSV = 'TRANSACTION_NUMBER,TRANSACTION_DATE,TRANSACTION_TOTAL,BILL_CUSTOMER_NAME\nINV-1,2026-01-01,5.00,Acme\n'


def test_concurrent_callers_of_one_cache_key_issue_one_report_request():
    parameters = [{"name": "P_CUSTOMER_NAME", "values": ["Acme Corp"]}]

    async def slow(_request):
        # The side effect has to yield, or the first caller finishes inside its own
        # scheduling slice, populates the cache, and the other 24 are cache hits that
        # never contend for the key. That would make this test pass for the wrong reason.
        await asyncio.sleep(0.05)
        return httpx.Response(200, text=_soap(ROWS_CSV))

    async def scenario():
        async with httpx.AsyncClient() as client:
            with respx.mock as router:
                route = router.post(SOAP_URL).mock(side_effect=slow)
                results = await asyncio.gather(
                    *(_run_bip_report(client, "u", "p", ["/r.xdo"], parameters, "invoice") for _ in range(25))
                )
            return route.call_count, results

    call_count, results = asyncio.run(scenario())

    # Before the fix this was 25 requests for one key. The cache above this would have made
    # the second caller a cache hit; a genuinely cold key is what this exercises.
    assert call_count == 1
    assert all(result == results[0] for result in results)
    assert results[0][0]["TRANSACTION_NUMBER"] == "INV-1"


def test_a_failed_fetch_is_not_pinned_as_a_result_for_the_next_caller():
    parameters = [{"name": "P_CUSTOMER_NAME", "values": ["Nobody"]}]

    async def scenario():
        async with httpx.AsyncClient() as client:
            with respx.mock as router:
                router.post(SOAP_URL).mock(return_value=httpx.Response(500, text="boom"))
                failed = await asyncio.gather(
                    *(
                        _safe_run(client, "u", "p", ["/r.xdo"], parameters, "invoice")
                        for _ in range(3)
                    ),
                    return_exceptions=True,
                )
            # The key must not still be registered, or the next caller awaits a dead future.
            assert oracle_bip._inflight == {}
            return failed

    failed = asyncio.run(scenario())
    assert failed, "the failing fetch should have raised"
    assert all(isinstance(item, BaseException) for item in failed)


def test_a_failed_key_can_be_retried_by_a_later_caller():
    # A future left registered after a failure would park every later caller on a dead
    # future, so the ledger is unavailable until the process restarts.
    parameters = [{"name": "P_CUSTOMER_NAME", "values": ["Nobody At All"]}]

    async def scenario():
        async with httpx.AsyncClient() as client:
            with respx.mock as router:
                route = router.post(SOAP_URL)
                route.mock(return_value=httpx.Response(500, text="boom"))
                with pytest.raises(oracle_bip.OracleBIPTransientError):
                    await _run_bip_report(client, "u", "p", ["/r.xdo"], parameters, "invoice")
                route.mock(return_value=httpx.Response(200, text=_soap(ROWS_CSV)))
                return await _run_bip_report(client, "u", "p", ["/r.xdo"], parameters, "invoice")

    assert asyncio.run(scenario())[0]["TRANSACTION_NUMBER"] == "INV-1"


async def _safe_run(client, username, password, paths, parameters, report_type):
    return await _run_bip_report(client, username, password, paths, parameters, report_type)


# ---------------------------------------------------------------------------
# 8. The cache key was not injective: a value could forge the separators.
#
# The key joined `name=value` with `|` and escaped nothing, and caller-supplied invoice
# numbers and customer names flow straight into it. The previous audit's worked example does
# not actually collide, and the two production call sites cannot be made to collide either,
# because they always pass the same four parameter names in the same order, which makes the
# encoding prefix-injective. The collision below is a real one at the function's own
# boundary: two different parameter lists, one key, one shared cache entry.
#
# Break it with: drop the quote() calls in _get_cache_key.
# ---------------------------------------------------------------------------


def test_two_different_parameter_lists_cannot_share_a_cache_key():
    # These two render identically without escaping: "A=1|B=2" is both the pair (A=1, B=2)
    # and the single parameter A=1|B=2, and they are different SOAP requests.
    two_parameters = [
        {"name": "A", "values": ["1"]},
        {"name": "B", "values": ["2"]},
    ]
    one_forged_parameter = [{"name": "A", "values": ["1|B=2"]}]

    assert _get_cache_key("invoice", two_parameters) != _get_cache_key("invoice", one_forged_parameter)


@pytest.mark.parametrize(
    "value",
    ["A|P_INVOICE_NUM=B", "1|B=2", "a|b", "=", "|", "x=y", "ACME|OTHER"],
)
def test_no_value_can_forge_a_separator_in_the_key(value):
    baseline = [{"name": "P_CUSTOMER_NAME", "values": ["Acme"]}, {"name": "P_INVOICE_NUM", "values": ["INV-1"]}]
    forged = [
        {"name": "P_CUSTOMER_NAME", "values": [value]},
        {"name": "P_INVOICE_NUM", "values": ["INV-1"]},
    ]
    other = [
        {"name": "P_CUSTOMER_NAME", "values": ["Acme"]},
        {"name": "P_INVOICE_NUM", "values": [value]},
    ]
    key = _get_cache_key("invoice", baseline)
    assert _get_cache_key("invoice", forged) != key
    assert _get_cache_key("invoice", other) != key


def test_a_key_is_still_stable_across_parameter_order_and_lookups():
    # Escaping must not make the key vary for a request that is genuinely the same one.
    forwards = [
        {"name": "P_CUSTOMER_NAME", "values": ["Acme Corp"]},
        {"name": "P_INVOICE_NUM", "values": ["INV-1"]},
    ]
    backwards = list(reversed(forwards))
    assert _get_cache_key("invoice", forwards) == _get_cache_key("invoice", backwards)


# ---------------------------------------------------------------------------
# 9. The rate limiter bucketed by the socket peer, which behind Render is one address for
#    every caller, so ten requests a minute was ten requests a minute for the whole internet.
#
# Break it with: set TRUSTED_PROXY_HEADERS back to False in the two tests below.
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, host, headers=None):
        self.client = type("Client", (), {"host": host})()
        self.headers = headers or {}


def test_the_limiter_ignores_the_forwarded_header_until_a_proxy_is_declared_trusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", False)
    header = {"x-forwarded-for": "203.0.113.7"}
    assert rate_limit_key(_FakeRequest("10.0.0.1", header)) == "10.0.0.1"
    # Two different callers behind the same edge share the edge's bucket, which is the
    # documented and safe behaviour: sharing a bucket throttles, believing a header does not.
    assert rate_limit_key(_FakeRequest("10.0.0.1", {"x-forwarded-for": "198.51.100.9"})) == "10.0.0.1"


def test_a_declared_trusted_proxy_separates_the_callers(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", True)
    assert rate_limit_key(_FakeRequest("10.0.0.1", {"x-forwarded-for": "203.0.113.7"})) == "203.0.113.7"
    assert rate_limit_key(_FakeRequest("10.0.0.1", {"x-forwarded-for": "198.51.100.9"})) == "198.51.100.9"


def test_a_forged_leading_entry_does_not_move_a_callers_bucket(monkeypatch):
    # The last entry is the one the edge appended. A caller that prepends an address of their
    # own choosing is ignored, which is why the header is not trusted by default and why
    # uvicorn's --forwarded-allow-ips=* (which reads the leftmost entry from any peer) is
    # not used.
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", True)
    forwarded = {"x-forwarded-for": "1.2.3.4, 203.0.113.7"}
    assert rate_limit_key(_FakeRequest("10.0.0.1", forwarded)) == "203.0.113.7"


def test_a_trusted_proxy_with_no_forwarded_header_falls_back_to_the_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HEADERS", True)
    assert rate_limit_key(_FakeRequest("10.0.0.1", {})) == "10.0.0.1"
    assert rate_limit_key(_FakeRequest("10.0.0.1", {"x-forwarded-for": "  "})) == "10.0.0.1"
    assert rate_limit_key(_FakeRequest("10.0.0.1", {"x-forwarded-for": "203.0.113.7,,"})) == "10.0.0.1"


# ---------------------------------------------------------------------------
# 10. A 422 echoed the whole rejected payload back to the caller.
#
# FastAPI's default validation body repeats the offending input under every error, so a
# single rejection of a 2,501-invoice batch returned all 2,501 objects to a caller who had
# already been told the request was refused.
#
# Break it with: delete the RequestValidationError handler in src/main.py.
# ---------------------------------------------------------------------------


def test_a_rejected_batch_does_not_come_back_with_every_invoice_in_it():
    invoices = [{"invoice_number": f"INV-{index}", "invoice_amount": "1.00"} for index in range(300)]
    with TestClient(app) as http:
        response = http.post("/v1/reconcile/batch", json={"invoices": invoices, "confidence_score": 5.0})

    assert response.status_code == 422
    # The caller still gets what it needs to fix the request...
    assert any(item["loc"][-1] == "confidence_score" for item in response.json()["detail"])
    # ...and not a copy of what it just sent.
    assert "INV-0" not in response.text
    assert len(response.json()["detail"]) == 1
