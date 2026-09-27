"""The BI Publisher boundary: SOAP request building, response parsing, error handling.

tests/test_reconciliation_batch.py exercises the whole flow, but it patches the discovery
seam in some of its cases and asserts outcomes rather than the wire format. These tests sit
directly on the HTTP boundary instead, so the SOAP envelope this service sends, the CSV it
reads back, the cache in front of it and the retry classification on its failures are all
pinned. Everything is mocked at the transport; nothing here reaches a real tenant.
"""

import asyncio
import base64
import binascii
import csv
import logging
import sys
import xml.etree.ElementTree as ET

import httpx
import pytest
import respx
from defusedxml.common import DefusedXmlException

from src.core.config import settings
from src.services import oracle_bip
from src.services.oracle_bip import (
    OracleBIPTransientError,
    _bip_cache,
    _get_cache_key,
    _parse_soap_response_sync,
    _run_bip_report,
    fetch_bip_invoices,
    fetch_bip_receipts,
)

SOAP_NS = "http://www.w3.org/2003/05/soap-envelope"
PUB_NS = "http://xmlns.oracle.com/oxp/service/PublicReportService"
SOAP_URL = f"{settings.ORACLE_URL.rstrip('/')}/xmlpserver/services/ExternalReportWSSService"

INVOICE_CSV = (
    "TRANSACTION_NUMBER,TRANSACTION_DATE,TRANSACTION_TOTAL,BILL_CUSTOMER_NAME\n"
    'INV-2026-00881,08/14/2026,"9,500.25",Acme Corp\n'
)
RECEIPT_CSV = (
    "RECEIPT_NUMBER,RECEIPT_DATE,RECEIPT_AMOUNT,CURRENCY,BILL_CUSTOMER_NAME\n"
    "RCPT-45021,2026-08-14,\"4,750.50\",USD,Acme Corp\n"
)


@pytest.fixture(autouse=True)
def clear_bip_cache():
    # _bip_cache is a module-level TTL cache that outlives a single test, so a repeat run of
    # the same report would be served from memory and never reach the mocked transport.
    _bip_cache.local.clear()
    yield
    _bip_cache.local.clear()


def soap_envelope(csv_text, namespaced=True):
    encoded = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")
    prefix = "pub:" if namespaced else ""
    ns = f' xmlns:pub="{PUB_NS}"' if namespaced else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}">'
        "<soap:Body>"
        f'<{prefix}runReportResponse{ns}>'
        f"<{prefix}reportOutput><{prefix}reportBytes>{encoded}</{prefix}reportBytes></{prefix}reportOutput>"
        f"</{prefix}runReportResponse>"
        "</soap:Body></soap:Envelope>"
    )


def run(coro_fn, *args, **kwargs):
    async def _run():
        async with httpx.AsyncClient() as client:
            return await coro_fn(client, *args, **kwargs)

    return asyncio.run(_run())


# ── response parsing ────────────────────────────────────────────────────────────────────────


def test_parse_reads_the_base64_csv_out_of_the_soap_envelope():
    rows = _parse_soap_response_sync(soap_envelope(INVOICE_CSV))
    assert rows == [
        {
            "TRANSACTION_NUMBER": "INV-2026-00881",
            "TRANSACTION_DATE": "08/14/2026",
            "TRANSACTION_TOTAL": "9,500.25",
            "BILL_CUSTOMER_NAME": "Acme Corp",
        }
    ]


def test_parse_matches_report_bytes_with_or_without_a_namespace():
    # Oracle's envelope prefix is not guaranteed, and the parser matches on the local name.
    assert _parse_soap_response_sync(soap_envelope(RECEIPT_CSV, namespaced=False))
    assert _parse_soap_response_sync(soap_envelope(RECEIPT_CSV, namespaced=True))


def test_parse_returns_nothing_when_the_report_carries_no_bytes():
    # An empty result set is still a successful call: BIP omits reportBytes entirely.
    empty = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body>'
        f'<pub:runReportResponse xmlns:pub="{PUB_NS}"><pub:reportOutput/></pub:runReportResponse>'
        "</soap:Body></soap:Envelope>"
    )
    assert _parse_soap_response_sync(empty) == []

    blank = soap_envelope("").replace(base64.b64encode(b"").decode("ascii"), "")
    assert _parse_soap_response_sync(blank) == []


def test_parse_normalises_headers_and_trims_values():
    # BIP emits the column names as the data model wrote them, with spaces and mixed case.
    csv_text = "Bill Customer Name,Transaction Number,Transaction Total\n  Acme Corp  ,INV-1, 42.00 \n"
    rows = _parse_soap_response_sync(soap_envelope(csv_text))
    assert rows == [{"BILLCUSTOMERNAME": "Acme Corp", "TRANSACTIONNUMBER": "INV-1", "TRANSACTIONTOTAL": "42.00"}]


def test_parse_pads_short_rows_and_drops_overflow_columns():
    # A ragged CSV must not lose the row: the missing trailing cells become empty strings,
    # and the surplus cells land under a None key that the parser discards.
    csv_text = "TRANSACTION_NUMBER,TRANSACTION_DATE,TRANSACTION_TOTAL\nINV-1\nINV-2,2026-01-01,2.00,EXTRA\n"
    rows = _parse_soap_response_sync(soap_envelope(csv_text))
    assert rows == [
        {"TRANSACTION_NUMBER": "INV-1", "TRANSACTION_DATE": "", "TRANSACTION_TOTAL": ""},
        {"TRANSACTION_NUMBER": "INV-2", "TRANSACTION_DATE": "2026-01-01", "TRANSACTION_TOTAL": "2.00"},
    ]
    assert all(None not in row for row in rows)


def test_parse_keeps_a_bom_on_the_first_column_name():
    # Excel-generated CSVs open with a BOM, and str.strip() does not remove U+FEFF. This is
    # why _is_data_row lstrips it: without that, a parameter echo row whose first column is
    # the BOM'd P_CUSTOMER_NAME would still look like a data row on a partial match.
    csv_text = "\ufeffTRANSACTION_NUMBER,BILL_CUSTOMER_NAME\nINV-1,Acme Corp\n"
    rows = _parse_soap_response_sync(soap_envelope(csv_text))
    assert rows[0]["\ufeffTRANSACTION_NUMBER"] == "INV-1"


def test_every_row_of_a_parsed_report_shares_one_key_set():
    # This is the invariant _filter_data_rows relies on. csv.DictReader keys every row from
    # the single header line, so the per-row filter can never discriminate between rows of one
    # report: either all of them are data rows or none are. Recorded here so that a future
    # change to the parser -- a parameter-echo block, a second header -- has to revisit
    # _filter_data_rows rather than silently making its per-row branch dead.
    csv_text = (
        "P_CUSTOMER_NAME,TRANSACTION_NUMBER,TRANSACTION_DATE\n"
        "Acme Corp,INV-1,2026-01-01\n"
        "Acme Corp,INV-2,\n"
        ",INV-3,2026-01-03\n"
    )
    rows = _parse_soap_response_sync(soap_envelope(csv_text))
    assert len(rows) == 3
    assert len({frozenset(row) for row in rows}) == 1


def test_parse_refuses_a_doctype_declaration():
    # defusedxml is the reason the response side is not stdlib ElementTree: a hostile or
    # misconfigured Oracle reply must not be able to mount an entity-expansion attack.
    hostile = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE Envelope [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body>&xxe;</soap:Body></soap:Envelope>'
    )
    with pytest.raises(DefusedXmlException):
        _parse_soap_response_sync(hostile)


def test_parse_rejects_a_body_that_is_not_base64_csv():
    broken = soap_envelope(INVOICE_CSV).replace(base64.b64encode(INVOICE_CSV.encode()).decode(), "not-base64!!")
    with pytest.raises(binascii.Error):
        _parse_soap_response_sync(broken)


# ── cache keys ──────────────────────────────────────────────────────────────────────────────


def test_cache_key_ignores_parameter_order():
    a = [{"name": "P_INVOICE_NUM", "values": ["INV-1"]}, {"name": "P_CUSTOMER_NAME", "values": ["Acme"]}]
    b = [{"name": "P_CUSTOMER_NAME", "values": ["Acme"]}, {"name": "P_INVOICE_NUM", "values": ["INV-1"]}]
    # Discovery issues the same report with its parameters in a different order depending on
    # which step it is in; without sorting, the cache would miss and re-run the report.
    assert _get_cache_key("invoice", a) == _get_cache_key("invoice", b)


def test_cache_key_separates_report_types_and_parameter_values():
    params = [{"name": "P_CUSTOMER_NAME", "values": ["Acme"]}]
    other = [{"name": "P_CUSTOMER_NAME", "values": ["Globex"]}]
    assert _get_cache_key("invoice", params) != _get_cache_key("receipt", params)
    assert _get_cache_key("invoice", params) != _get_cache_key("invoice", other)


# ── request building ────────────────────────────────────────────────────────────────────────


def sent_envelope(mock_route):
    body = mock_route.calls[-1].request.content.decode("utf-8")
    return ET.fromstring(body)


def test_soap_envelope_carries_the_namespaces_path_and_credential():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(
            return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV))
        )
        rows = run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-2026-00881")

    assert len(rows) == 1
    root = sent_envelope(route)
    assert root.tag == f"{{{SOAP_NS}}}Envelope"
    # The literal prefixes have to be registered for Oracle to accept the payload, so assert
    # on the serialised text rather than only on the resolved tag.
    raw = route.calls[-1].request.content.decode("utf-8")
    assert 'xmlns:soap="http://www.w3.org/2003/05/soap-envelope"' in raw
    assert f'xmlns:pub="{PUB_NS}"' in raw

    assert root.find(f".//{{{PUB_NS}}}attributeFormat").text == "csv"
    assert root.find(f".//{{{PUB_NS}}}reportAbsolutePath").text == oracle_bip.DEFAULT_INVOICE_REPORT_PATH
    assert root.find(f".//{{{PUB_NS}}}userID").text == "svc-account"
    assert root.find(f".//{{{PUB_NS}}}password").text == "svc-password"
    assert root.find(f".//{{{PUB_NS}}}sizeOfDataChunkDownload").text == str(oracle_bip.BIP_CHUNK_DOWNLOAD_SIZE)


def test_soap_request_authenticates_with_basic_auth():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        run(fetch_bip_invoices, "svc-account", "svc-password")

    request = route.calls[-1].request
    assert request.headers["authorization"].startswith("Basic ")
    assert base64.b64decode(request.headers["authorization"].split()[1]).decode() == "svc-account:svc-password"
    assert request.headers["content-type"] == 'application/soap+xml;charset=UTF-8;action=""'


def test_all_four_parameters_are_always_sent_even_when_blank():
    # A missing parameter makes BIP fall back to the data model default, which can be the
    # literal string "null" and fault the query. A single space is what Oracle's TRIM() sees
    # as blank, so blanks are sent explicitly rather than omitted.
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        run(fetch_bip_invoices, "svc-account", "svc-password")

    root = sent_envelope(route)
    items = root.findall(f".//{{{PUB_NS}}}parameterNameValues/{{{PUB_NS}}}item")
    sent = {item.find(f"{{{PUB_NS}}}name").text: item.find(f"{{{PUB_NS}}}values/{{{PUB_NS}}}item").text for item in items}

    assert set(sent) == {"P_CUSTOMER_NAME", "P_INVOICE_NUM", "P_INVOICE_AMOUNT", "P_INVOICE_DATE"}
    assert set(sent.values()) == {" "}


def test_optional_parameters_are_sent_when_supplied_and_blanked_otherwise():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        run(
            fetch_bip_invoices,
            "svc-account",
            "svc-password",
            customer_name="Acme Corp",
            invoice_number="INV-2026-00881",
            invoice_amount="1,234.56",
            invoice_date="05-Oct-2026",
        )

    root = sent_envelope(route)
    items = root.findall(f".//{{{PUB_NS}}}parameterNameValues/{{{PUB_NS}}}item")
    sent = {item.find(f"{{{PUB_NS}}}name").text: item.find(f"{{{PUB_NS}}}values/{{{PUB_NS}}}item").text for item in items}

    assert sent["P_CUSTOMER_NAME"] == "Acme Corp"
    assert sent["P_INVOICE_NUM"] == "INV-2026-00881"
    assert sent["P_INVOICE_AMOUNT"] == "1,234.56"
    # The date is normalised to the format Oracle wants; sending the client's own string is
    # what produces ORA-01861.
    assert sent["P_INVOICE_DATE"] == "2026-10-05"


def test_a_blank_amount_parameter_is_not_treated_as_a_zero():
    # `if invoice_amount` would have dropped "0.00"; the check is on emptiness instead.
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        run(fetch_bip_invoices, "svc-account", "svc-password", invoice_amount="0.00")

    root = sent_envelope(route)
    items = root.findall(f".//{{{PUB_NS}}}parameterNameValues/{{{PUB_NS}}}item")
    sent = {item.find(f"{{{PUB_NS}}}name").text: item.find(f"{{{PUB_NS}}}values/{{{PUB_NS}}}item").text for item in items}
    assert sent["P_INVOICE_AMOUNT"] == "0.00"


def test_report_path_environment_variable_overrides_the_default(monkeypatch):
    monkeypatch.setenv("ORACLE_BIP_INVOICE_PATH", "/Custom/Someone Else/Their Report.xdo")
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        run(fetch_bip_invoices, "svc-account", "svc-password")

    root = sent_envelope(route)
    assert root.find(f".//{{{PUB_NS}}}reportAbsolutePath").text == "/Custom/Someone Else/Their Report.xdo"


def test_receipt_report_uses_its_own_default_path_and_parameters(monkeypatch):
    monkeypatch.delenv("ORACLE_BIP_RECEIPT_PATH", raising=False)
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(RECEIPT_CSV)))
        run(fetch_bip_receipts, "svc-account", "svc-password", receipt_number="RCPT-45021")

    root = sent_envelope(route)
    assert root.find(f".//{{{PUB_NS}}}reportAbsolutePath").text == oracle_bip.DEFAULT_RECEIPT_REPORT_PATH
    items = root.findall(f".//{{{PUB_NS}}}parameterNameValues/{{{PUB_NS}}}item")
    assert {item.find(f"{{{PUB_NS}}}name").text for item in items} == {
        "P_CUSTOMER_NAME",
        "P_RECEIPT_NUMBER",
        "P_RECEIPT_AMOUNT",
        "P_RECEIPT_DATE",
    }


# ── caching ────────────────────────────────────────────────────────────────────────────────


def test_a_repeat_report_is_served_from_cache_without_a_second_request():
    with respx.mock(assert_all_called=True) as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        first = run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-2026-00881")
        second = run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-2026-00881")

    assert route.call_count == 1
    assert first == second


def test_a_different_report_is_not_served_from_the_cache():
    with respx.mock(assert_all_called=True) as router:
        route = router.post(SOAP_URL).mock(
            side_effect=[
                httpx.Response(200, text=soap_envelope(INVOICE_CSV)),
                httpx.Response(200, text=soap_envelope(RECEIPT_CSV)),
            ]
        )
        run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-2026-00881")
        run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-OTHER")

    assert route.call_count == 2


def test_a_failed_report_is_not_cached():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(403, text="ORA-01017 invalid username"))
        with pytest.raises(httpx.HTTPStatusError):
            run(fetch_bip_invoices, "svc-account", "wrong-password")
        assert route.call_count == 1

    # A transient failure must not poison the cache with an empty result set.
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        rows = run(fetch_bip_invoices, "svc-account", "wrong-password")

    assert route.call_count == 1
    assert len(rows) == 1


# ── failure classification ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_raise_the_retryable_error(status):
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(status, text="upstream busy"))
        with pytest.raises(OracleBIPTransientError):
            run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")


@pytest.mark.parametrize("status", [401, 403])
def test_a_credentials_failure_is_not_retried(status):
    # 401/403 will not fix themselves, so spending the retry budget on them only delays the
    # 502 the caller gets. The batch test asserts the same at the endpoint; this pins the
    # classification itself.
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(status, text="ORA-01017"))
        with pytest.raises(httpx.HTTPStatusError):
            run(_run_bip_report, "svc-account", "bad", ["/Some Report.xdo"], [], "invoice")

    assert route.call_count == 1


def test_a_missing_report_falls_through_to_the_next_candidate_path():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(
            side_effect=[
                httpx.Response(404, text="Report definition not found"),
                httpx.Response(200, text=soap_envelope(INVOICE_CSV)),
            ]
        )
        rows = run(
            _run_bip_report,
            "svc-account",
            "svc-password",
            ["/Missing Report.xdo", "", "/Good Report.xdo"],
            [{"name": "P_CUSTOMER_NAME", "values": ["Acme"]}],
            "invoice",
        )

    assert len(rows) == 1
    # The blank candidate is dropped rather than attempted: BIP treats an empty absolute path
    # as a 500, not a 404, and it would burn the transient-error budget.
    assert route.call_count == 2
    paths = [
        ET.fromstring(call.request.content.decode("utf-8")).find(f".//{{{PUB_NS}}}reportAbsolutePath").text
        for call in route.calls
    ]
    assert paths == ["/Missing Report.xdo", "/Good Report.xdo"]


def test_a_report_not_found_body_on_a_500_still_falls_through():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(
            side_effect=[
                httpx.Response(500, text="java.lang.Exception: Report definition not found"),
                httpx.Response(200, text=soap_envelope(INVOICE_CSV)),
            ]
        )
        rows = run(
            _run_bip_report,
            "svc-account",
            "svc-password",
            ["/Missing Report.xdo", "/Good Report.xdo"],
            [],
            "invoice",
        )

    assert len(rows) == 1
    assert route.call_count == 2


def test_the_last_missing_path_failure_is_re_raised():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(404, text="Report definition not found"))
        with pytest.raises(httpx.HTTPStatusError) as exc:
            run(_run_bip_report, "svc-account", "svc-password", ["/A.xdo", "/B.xdo"], [], "invoice")

    assert exc.value.response.status_code == 404
    assert route.call_count == 2


def test_no_usable_candidate_path_makes_no_request():
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        rows = run(_run_bip_report, "svc-account", "svc-password", ["", "   "], [], "invoice")

    assert rows == []
    assert route.call_count == 0


def test_a_500_without_a_not_found_body_is_treated_as_transient():
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(500, text="java.sql.SQLException: ORA-01555"))
        with pytest.raises(OracleBIPTransientError):
            run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")


def test_a_throttled_response_that_also_says_not_found_falls_through():
    # There are two "not found" guards. The first catches any status whose body says
    # "Report definition not found". This exercises the second, weaker one: a 429 or 5xx whose
    # body only says "not found" in some other phrasing. It looks transient but is not, and
    # treating it as transient would spend the whole retry budget on a path that will never
    # work.
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(
            side_effect=[
                httpx.Response(503, text="Runtime error: report job not found"),
                httpx.Response(200, text=soap_envelope(INVOICE_CSV)),
            ]
        )
        rows = run(
            _run_bip_report,
            "svc-account",
            "svc-password",
            ["/Missing Report.xdo", "/Good Report.xdo"],
            [],
            "invoice",
        )

    assert len(rows) == 1
    assert route.call_count == 2


def test_a_throttled_response_that_says_nothing_useful_is_transient():
    # The other side of the same guard: a 503 with no "not found" phrasing at all has to stay
    # retryable, or a genuinely overloaded Oracle would be reported as a missing report.
    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(503, text="upstream busy"))
        with pytest.raises(OracleBIPTransientError):
            run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")

    assert route.call_count == 1


# ── the shared cache ─────────────────────────────────────────────────────────────────────────


class FakeRedis:
    """Enough of redis.asyncio to exercise the fallback paths without a server."""

    def __init__(self, get_error=False, set_error=False):
        self.store = {}
        self.get_error = get_error
        self.set_error = set_error
        self.set_calls = []

    async def get(self, key):
        if self.get_error:
            raise ConnectionError("redis is down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))
        if self.set_error:
            raise ConnectionError("redis is down")
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value


def test_the_cache_defaults_to_the_in_process_ttl_cache(monkeypatch):
    monkeypatch.setattr(oracle_bip.settings, "REDIS_URL", None)
    cache = oracle_bip.AsyncCache()
    assert cache.redis is None

    asyncio.run(cache.set("k", [{"A": "1"}]))
    assert asyncio.run(cache.get("k")) == [{"A": "1"}]
    assert asyncio.run(cache.get("absent")) is None


def test_a_configured_redis_backs_the_shared_cache(monkeypatch):
    monkeypatch.setattr(oracle_bip.settings, "REDIS_URL", "redis://cache.invalid:6379/0")
    cache = oracle_bip.AsyncCache()
    assert cache.redis is not None

    # Two workers on two hosts share the ledger through Redis, so the TTL has to travel with
    # the value rather than living only in the local cache's own expiry.
    cache.redis = FakeRedis()
    asyncio.run(cache.set("k", [{"A": "1"}]))
    assert cache.redis.set_calls == [("k", '[{"A": "1"}]', oracle_bip.BIP_CACHE_TTL_SECONDS)]
    assert asyncio.run(cache.get("k")) == [{"A": "1"}]


def test_a_redis_read_failure_falls_back_to_the_local_cache(monkeypatch):
    monkeypatch.setattr(oracle_bip.settings, "REDIS_URL", "redis://cache.invalid:6379/0")
    cache = oracle_bip.AsyncCache()
    cache.redis = FakeRedis(get_error=True)
    cache.local["k"] = [{"A": "local"}]

    assert asyncio.run(cache.get("k")) == [{"A": "local"}]


def test_a_missing_redis_package_falls_back_to_the_local_cache(monkeypatch):
    # redis is a declared runtime dependency, but a slimmed-down deployment image can still
    # not have it. The service has to keep working on an in-process cache rather than refuse
    # to import, and the operator has to be told the sharing is off.
    monkeypatch.setattr(oracle_bip.settings, "REDIS_URL", "redis://cache.invalid:6379/0")
    # Setting a sys.modules entry to None makes `import` raise ImportError for that name.
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)

    cache = oracle_bip.AsyncCache()
    assert cache.redis is None

    asyncio.run(cache.set("k", [{"A": "1"}]))
    assert asyncio.run(cache.get("k")) == [{"A": "1"}]


def test_a_redis_write_failure_falls_back_to_the_local_cache(monkeypatch):
    monkeypatch.setattr(oracle_bip.settings, "REDIS_URL", "redis://cache.invalid:6379/0")
    cache = oracle_bip.AsyncCache()
    cache.redis = FakeRedis(set_error=True)

    asyncio.run(cache.set("k", [{"A": "1"}]))
    # Losing the shared cache must degrade to a per-process cache, not to no cache at all.
    assert cache.local["k"] == [{"A": "1"}]
    assert cache.redis.set_calls, "the write should be attempted against Redis before degrading"

    # A later read still finds it: the local copy is what answers once Redis is unreachable.
    cache.redis.get_error = True
    assert asyncio.run(cache.get("k")) == [{"A": "1"}]


def test_fetch_retries_a_transient_failure_and_then_succeeds(monkeypatch):
    from tenacity import wait_none

    # The real backoff is 2s then 4s, which has no place in a unit test. Swapping the wait
    # strategy keeps the retry *behaviour* under test and drops only the sleeping.
    original = fetch_bip_invoices.retry.wait
    monkeypatch.setattr(fetch_bip_invoices.retry, "wait", wait_none())

    with respx.mock as router:
        route = router.post(SOAP_URL).mock(
            side_effect=[
                httpx.Response(503, text="temporarily unavailable"),
                httpx.Response(200, text=soap_envelope(INVOICE_CSV)),
            ]
        )
        rows = run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-2026-00881")

    assert len(rows) == 1
    assert route.call_count == 2
    assert original is not wait_none


def test_fetch_gives_up_after_the_configured_attempts(monkeypatch):
    from tenacity import wait_none

    monkeypatch.setattr(fetch_bip_invoices.retry, "wait", wait_none())

    with respx.mock as router:
        route = router.post(SOAP_URL).mock(return_value=httpx.Response(503, text="still unavailable"))
        with pytest.raises(OracleBIPTransientError):
            run(fetch_bip_invoices, "svc-account", "svc-password", invoice_number="INV-2026-00881")

    assert route.call_count == oracle_bip.BIP_MAX_RETRIES


def test_an_unparseable_report_body_is_reported_not_swallowed():
    # A 200 with a reportBytes that is not decodable is a fault on our side of the wire, and
    # silently returning no rows would look to the caller like "this customer has no ledger",
    # which is a wrong answer rather than a visible failure.
    corrupt = soap_envelope(INVOICE_CSV).replace(base64.b64encode(INVOICE_CSV.encode()).decode(), "not-base64!!")
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=corrupt))
        with pytest.raises(Exception, match="Failed to parse SOAP response"):
            run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")


def test_a_well_formed_envelope_with_no_report_yields_no_rows():
    # A SOAP fault rendered as a 200, or any envelope the parser does not recognise, is an
    # empty ledger rather than an exception. The batch layer turns that into "customer not
    # identified", not a 502, so the two failure shapes stay distinguishable.
    fault = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}"><soap:Body>'
        '<soap:Fault><faultcode>soap:Server</faultcode><faultstring>Job failed</faultstring></soap:Fault>'
        "</soap:Body></soap:Envelope>"
    )
    with respx.mock as router:
        router.post(SOAP_URL).mock(return_value=httpx.Response(200, text=fault))
        rows = run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")

    assert rows == []


# ── transport hygiene ───────────────────────────────────────────────────────────────────────


def test_plain_http_to_a_remote_host_is_warned_about(monkeypatch, caplog):
    # Settings refuses an insecure ORACLE_URL unless explicitly allowed, so reaching this
    # branch means the operator opted in. The warning has to say so at the point of use,
    # because ORACLE_PASS travels in this request.
    monkeypatch.setattr(oracle_bip.settings, "ORACLE_URL", "http://erp.example.com")
    url = "http://erp.example.com/xmlpserver/services/ExternalReportWSSService"

    with respx.mock as router:
        route = router.post(url).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        with caplog.at_level(logging.WARNING):
            rows = run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")

    assert len(rows) == 1
    assert route.call_count == 1
    assert "unencrypted HTTP" in caplog.text


def test_plain_http_to_loopback_is_not_warned_about(monkeypatch, caplog):
    monkeypatch.setattr(oracle_bip.settings, "ORACLE_URL", "http://127.0.0.1:8080")
    url = "http://127.0.0.1:8080/xmlpserver/services/ExternalReportWSSService"

    with respx.mock as router:
        router.post(url).mock(return_value=httpx.Response(200, text=soap_envelope(INVOICE_CSV)))
        with caplog.at_level(logging.WARNING):
            run(_run_bip_report, "svc-account", "svc-password", ["/Some Report.xdo"], [], "invoice")

    assert "unencrypted HTTP" not in caplog.text


def test_a_csv_with_only_a_header_yields_no_rows():
    header_only = "TRANSACTION_NUMBER,BILL_CUSTOMER_NAME\n"
    assert _parse_soap_response_sync(soap_envelope(header_only)) == []


def test_the_parser_handles_a_crlf_report():
    # BIP writes CRLF on Windows-originated data models; csv handles it, but the value would
    # keep the \r if the reader were swapped for a naive split, so this pins the behaviour.
    rows = _parse_soap_response_sync(soap_envelope("A,B\r\n1,2\r\n"))
    assert rows == [{"A": "1", "B": "2"}]


def test_parsed_rows_are_plain_dicts_the_matcher_can_key():
    # reconciliation.py indexes these with .get() on normalised column names and stores them
    # in a per-number dict, so the value must be a dict and not a csv.DictReader row object.
    rows = _parse_soap_response_sync(soap_envelope(INVOICE_CSV))
    assert isinstance(rows[0], dict)
    assert rows[0].get("TRANSACTION_NUMBER") == "INV-2026-00881"
    assert not isinstance(rows[0], csv.DictReader)
