import base64

import httpx
import pytest
import respx
from fastapi import HTTPException

from src.config import get_oracle_url
from src.main import _map_bip_invoices, get_api_key
from src.models import InvoiceItem, ReconciliationRequest
from src.services.oracle_bip import run_bip_bulk_match
from src.services.oracle_matcher import safe_date_match, safe_float_match
from src.utils.date_formatter import format_oracle_date


def test_format_oracle_date_timezone_aware():
    # Keep local date as-is (e.g. 2026-06-13 from 2026-06-13T02:30:00+05:30)
    assert format_oracle_date("2026-06-13T02:30:00+05:30") == "2026-06-13"
    assert format_oracle_date("2026-06-13T22:30:00+05:30") == "2026-06-13"

def test_safe_date_match():
    assert safe_date_match("2026-06-13T02:30:00+05:30", "2026-06-13") is True
    assert safe_date_match("2026-06-13T02:30:00+05:30", "2026-06-12") is False
    assert safe_date_match("2026-06-13", "2026-06-13T07:00:00Z") is True
    assert safe_date_match("2026-06-13", "") is False
    assert safe_date_match(None, "2026-06-13") is False



# 2. Test Commas in amounts parsing and Decimal matching
def test_safe_float_match_commas():
    assert safe_float_match("1,234.56", 1234.56) is True
    assert safe_float_match("12,345.67", "12345.670") is True
    assert safe_float_match("1,000,000.00", 1000000) is True
    assert safe_float_match("None", 100.0) is False
    assert safe_float_match(100.0, None) is False


# 3. Test API key constant-time check
@pytest.mark.asyncio
async def test_get_api_key_secure(monkeypatch):
    monkeypatch.setenv("API_KEY", "secure_secret_token")

    # Valid key
    assert await get_api_key("secure_secret_token") == "secure_secret_token"

    # Invalid key
    with pytest.raises(HTTPException) as exc:
        await get_api_key("wrong_token")
    assert exc.value.status_code == 401

    # Missing/None key
    with pytest.raises(HTTPException) as exc:
        await get_api_key(None)
    assert exc.value.status_code == 401


# 4. Test BIP pipeline status priority/duplicates behavior
def test_bip_pipeline_priority_and_duplicates():
    payload = ReconciliationRequest(
        payment_reference="REC-999",
        total_amount=100.0,
        payment_date="2026-06-13",
        customer_name="Global Corp",
        invoices=[
            InvoiceItem(invoice_number="INV-100", invoice_amount=100.0, invoice_date="2026-06-13")
        ]
    )

    # Duplicates in BIP: 2 matching transaction numbers, one Open and one Closed.
    # Open should take priority.
    invoice_map = {
        "INV-100": [
            {
                "TransactionNumber": "INV-100",
                "TransactionDate": "2026-06-13",
                "EnteredAmount": "100.0",
                "InvoiceStatus": "Closed"
            },
            {
                "TransactionNumber": "INV-100",
                "TransactionDate": "2026-06-13",
                "EnteredAmount": "100.0",
                "InvoiceStatus": "Open"
            }
        ]
    }

    unmatched = _map_bip_invoices(payload, invoice_map)
    assert len(unmatched) == 0
    assert payload.invoices[0].fusion_invoice_number == "INV-100"
    # Should pick Open phase
    assert payload.invoices[0].fusion_invoice_amount == 100.0

    # Rule priority check: Candidate matching Rule 1b (Number + Date + EnteredAmount)
    # vs Candidate matching Rule 1a (Number + EnteredAmount, wrong date).
    payload_rules = ReconciliationRequest(
        payment_reference="REC-999",
        total_amount=100.0,
        payment_date="2026-06-13",
        customer_name="Global Corp",
        invoices=[
            InvoiceItem(invoice_number="INV-200", invoice_amount=100.0, invoice_date="2026-06-13")
        ]
    )
    invoice_map_rules = {
        "INV-200": [
            {
                "TransactionNumber": "INV-200",
                "TransactionDate": "2026-06-12",  # Wrong date, matches 1a
                "EnteredAmount": "100.0",
                "InvoiceStatus": "Open"
            },
            {
                "TransactionNumber": "INV-200",
                "TransactionDate": "2026-06-13",  # Correct date, matches 1b
                "EnteredAmount": "100.0",
                "InvoiceStatus": "Open"
            }
        ]
    }
    unmatched_rules = _map_bip_invoices(payload_rules, invoice_map_rules)
    assert len(unmatched_rules) == 0
    assert payload_rules.invoices[0].fusion_invoice_number == "INV-200"
    # Ensure correct matching candidate (2026-06-13) was mapped
    assert payload_rules.invoices[0].fusion_invoice_date == "2026-06-13"


# 5. Test Insecure URL rejection in production
def test_oracle_url_validation(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ORACLE_URL", "https://example.com/api")
    assert get_oracle_url() == "https://example.com/api"

    monkeypatch.setenv("ORACLE_URL", "http://example.com/api")
    with pytest.raises(ValueError, match="Insecure HTTP protocol is not allowed for non-localhost URLs"):
        get_oracle_url()

    # Localhost/127.0.0.1 should be allowed in production
    monkeypatch.setenv("ORACLE_URL", "http://localhost/api")
    assert get_oracle_url() == "http://localhost/api"

    monkeypatch.setenv("ORACLE_URL", "http://127.0.0.1/api")
    assert get_oracle_url() == "http://127.0.0.1/api"

    # HTTPS should always be allowed
    monkeypatch.setenv("ORACLE_URL", "https://example.com/api")
    assert get_oracle_url() == "https://example.com/api"


# 6. Test BIP retry logic for transient status codes
@pytest.mark.asyncio
async def test_bip_retry_on_transient_status_codes(mock_httpx_client):
    calls_count = 0
    with respx.mock:
        def side_effect(request):
            nonlocal calls_count
            calls_count += 1
            if calls_count == 1:
                return httpx.Response(502, text="Bad Gateway")
            csv_data = "TransactionNumber,Amount\nINV-001,100.0\n"
            encoded_csv = base64.b64encode(csv_data.encode("utf-8")).decode("utf-8")
            return httpx.Response(200, json={"reportBytes": encoded_csv})

        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        # Should retry after 502 and succeed on the second try
        result = await run_bip_bulk_match(mock_httpx_client, "user", "pass", ["INV-001"])
        assert "INV-001" in result
        assert calls_count == 2

@pytest.mark.asyncio
async def test_bip_no_retry_on_permanent_errors(mock_httpx_client):
    calls_count = 0
    with respx.mock:
        def side_effect(request):
            nonlocal calls_count
            calls_count += 1
            return httpx.Response(400, text="Bad Request")

        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        # Permanent error (400) should raise immediately without retry
        with pytest.raises(httpx.HTTPStatusError):
            await run_bip_bulk_match(mock_httpx_client, "user", "pass", ["INV-001"])
        assert calls_count == 1

def test_nan_inf_validation():
    from pydantic import ValidationError

    from src.models import InvoiceItem, ReconciliationRequest

    with pytest.raises(ValidationError):
        InvoiceItem(invoice_number="INV-001", invoice_amount=float('nan'))
    with pytest.raises(ValidationError):
        InvoiceItem(invoice_number="INV-001", invoice_amount=float('inf'))
    with pytest.raises(ValidationError):
        ReconciliationRequest(total_amount=float('nan'))
    with pytest.raises(ValidationError):
        ReconciliationRequest(total_amount=float('inf'))

