import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.utils.date_formatter import format_oracle_date
from src.utils.validators import sanitize_float_val, sanitize_string_val

BASE = {
    "ORACLE_URL": "https://erp.example.com",
    "ORACLE_USER": "svc",
    "ORACLE_PASS": "secret",
}


def test_oracle_url_requires_a_scheme():
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "ORACLE_URL": "erp.example.com"})


def test_oracle_url_rejects_plain_http_for_remote_hosts():
    with pytest.raises(ValidationError):
        Settings(**{**BASE, "ORACLE_URL": "http://erp.example.com"})


def test_oracle_url_allows_plain_http_for_loopback():
    assert Settings(**{**BASE, "ORACLE_URL": "http://localhost:8080"}).ORACLE_URL == "http://localhost:8080"


def test_oracle_url_insecure_opt_in_is_honoured():
    settings = Settings(**{**BASE, "ORACLE_URL": "http://erp.example.com", "ALLOW_INSECURE_ORACLE_HTTP": True})
    assert settings.ALLOW_INSECURE_ORACLE_HTTP is True


def test_oracle_url_trailing_slash_is_stripped():
    assert Settings(**{**BASE, "ORACLE_URL": "https://erp.example.com/"}).ORACLE_URL == "https://erp.example.com"


def test_settings_do_not_serialise_secrets():
    # pydantic reprs must not be able to leak the password into a log line.
    assert "secret" not in repr(Settings(**BASE))


def test_sanitize_string_val():
    assert sanitize_string_val("  Acme Corp  ") == "Acme Corp"
    assert sanitize_string_val("  ") is None
    assert sanitize_string_val("None") is None
    assert sanitize_string_val("none") is None
    assert sanitize_string_val(None) is None
    assert sanitize_string_val(12345) == "12345"


def test_sanitize_float_val():
    assert sanitize_float_val("1,234.56") == 1234.56
    assert sanitize_float_val("100") == 100.0
    assert sanitize_float_val("  ") is None
    assert sanitize_float_val("none") is None
    assert sanitize_float_val("nan") is None
    assert sanitize_float_val("inf") is None
    assert sanitize_float_val("not a number") is None
    assert sanitize_float_val("") is None
    assert sanitize_float_val(None) is None


def test_sanitize_float_val_keeps_a_real_zero():
    assert sanitize_float_val("0") == 0.0
    assert sanitize_float_val("0.00") == 0.0


def test_format_oracle_date_strips_offsets():
    assert format_oracle_date("2026-10-05T12:00:00+05:30") == "2026-10-05"
    assert format_oracle_date("2026-10-05 12:00:00") == "2026-10-05"


def test_format_oracle_date_rejects_impossible_values():
    # strptime raises rather than silently rolling over into a real date.
    assert format_oracle_date("2026-13-01") is None
    assert format_oracle_date("2026-02-30") is None
