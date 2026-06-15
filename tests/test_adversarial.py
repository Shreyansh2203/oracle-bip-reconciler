import httpx
import pytest
import respx

from src.services.oracle_matcher import check_receipt_cascading, safe_float_match
from src.utils.date_formatter import format_oracle_date, safe_date_match

# --- 1. Date Formatting Adversarial Checks ---

def test_date_formatter_empty_null_weird_types():
    assert format_oracle_date(None) == ""
    assert format_oracle_date("") == ""
    assert format_oracle_date("   ") == ""
    # Test weird types
    assert format_oracle_date([]) == ""
    assert format_oracle_date({}) == ""
    assert format_oracle_date(True) == ""
    assert format_oracle_date(123) == ""

def test_date_formatter_invalid_dates():
    assert format_oracle_date("2023-13-45") == ""
    assert format_oracle_date("2023-02-30") == ""
    assert format_oracle_date("0000-00-00") == ""
    assert format_oracle_date("9999-99-99") == ""
    assert format_oracle_date("2023-10-01' OR '1'='1") == ""
    assert format_oracle_date("2023-10-01; DROP TABLE receipts;") == ""

def test_date_formatter_leap_years():
    # Valid leap year
    assert format_oracle_date("2024-02-29") == "2024-02-29"
    # Invalid leap year
    assert format_oracle_date("2023-02-29") == ""

def test_date_formatter_ambiguity():
    # Because "%m-%d-%Y" is tried before "%d-%m-%Y":
    # 05-06-2026 is parsed as Month 05, Day 06 -> 2026-05-06
    assert format_oracle_date("05-06-2026") == "2026-05-06"

    # 15-06-2026 cannot be parsed as MM-DD-YYYY since month 15 is invalid.
    # It falls back to %d-%m-%Y and parses correctly to 2026-06-15.
    assert format_oracle_date("15-06-2026") == "2026-06-15"

def test_date_formatter_timezone_shifting():
    # 2026-06-13T02:30:00+05:30 keeps the local date as-is.
    assert format_oracle_date("2026-06-13T02:30:00+05:30") == "2026-06-13"

    # They should match local calendar date "2026-06-13".
    assert safe_date_match("2026-06-13T02:30:00+05:30", "2026-06-13") is True



# --- 2. Amount Matching (Float/Decimal) Adversarial Checks ---

def test_safe_float_match_precision():
    # Math operations on floats can result in precision issues
    # E.g. 0.1 + 0.2 = 0.30000000000000004
    # With our new tolerance logic, this correctly evaluates to True.
    assert safe_float_match(0.1 + 0.2, "0.3") is True

    # Let's check very close but distinct numbers
    assert safe_float_match(100.00000000000000000001, 100) is True
    # Under the tolerance logic (0.01), this also matches.
    assert safe_float_match("100.00000000000000000001", 100) is True

def test_safe_float_match_weird_strings():
    # "NaN" vs "NaN"
    assert safe_float_match("NaN", "NaN") is False
    # "Infinity" vs "Infinity"
    assert safe_float_match("Infinity", "Infinity") is False
    # "-Infinity" vs "-Infinity"
    assert safe_float_match("-Infinity", "-Infinity") is False

    # Scientific notation
    assert safe_float_match("1e2", 100.0) is True
    assert safe_float_match("1.23e3", 1230) is True

    # Bad formats with characters
    assert safe_float_match("100.00 USD", 100) is False
    assert safe_float_match("$100.00", 100) is False
    assert safe_float_match("None", 100) is False
    assert safe_float_match("", 100) is False



# --- 3. Matcher query building and safety ---

@pytest.mark.asyncio
async def test_check_receipt_cascading_nan_inf_amounts(mock_httpx_client):
    # What happens when we pass float('nan') or float('inf') to check_receipt_cascading?
    with respx.mock:
        respx.route(url__startswith="https://test.oracle.com").mock(httpx.Response(200, json={"items": [], "hasMore": False}))

        # NaN amount
        # float('nan') is caught by the guard, returning early without making an API request.
        result = await check_receipt_cascading(
            mock_httpx_client, "user", "pass", "REC-123", float('nan'), "2023-10-01", "Customer"
        )
        assert result["matched_in_oracle"] is False
        assert "Invalid amount" in result["error"]
        assert len(respx.calls) == 0

        # Infinity amount
        # float('inf') is also caught by the guard.
        result_inf = await check_receipt_cascading(
            mock_httpx_client, "user", "pass", "REC-123", float('inf'), "2023-10-01", "Customer"
        )
        assert result_inf["matched_in_oracle"] is False
        assert "Invalid amount" in result_inf["error"]
        assert len(respx.calls) == 0


# --- 4. Overflow / NaN errors in model validation ---

@pytest.mark.asyncio
async def test_reconcile_nan_inf_overflow(mock_httpx_client):
    from pydantic import ValidationError

    from src.models import InvoiceItem

    # NaN amount
    with pytest.raises(ValidationError) as exc:
        InvoiceItem(invoice_number="INV-001", invoice_amount=float('nan'))
    assert "Float value must be a finite number." in str(exc.value)

    # Infinity amount
    with pytest.raises(ValidationError) as exc_inf:
        InvoiceItem(invoice_number="INV-001", invoice_amount=float('inf'))
    assert "Float value must be a finite number." in str(exc_inf.value)


# --- 5. Error Swallowing in fetch_by_query ---

@pytest.mark.asyncio
async def test_fetch_by_query_swallows_invoice_error(mock_httpx_client):
    from src.services.oracle_matcher import OracleClientContext, fetch_by_query
    context = OracleClientContext(mock_httpx_client, "user", "pass")

    with respx.mock:
        def side_effect(request):
            url_str = str(request.url)
            if "receivablesInvoices" in url_str:
                return httpx.Response(500, text="Internal Server Error")
            if "receivablesCreditMemos" in url_str:
                return httpx.Response(200, json={"items": [], "hasMore": False})
            return httpx.Response(404)

        respx.route(url__startswith="https://test.oracle.com").mock(side_effect=side_effect)

        # Calling fetch_by_query should fail because receivablesInvoices failed.
        # It no longer swallows the exception.
        with pytest.raises(Exception) as exc:
            await fetch_by_query(context, "TransactionNumber='123'", "", "")
        assert "500" in str(exc.value)




