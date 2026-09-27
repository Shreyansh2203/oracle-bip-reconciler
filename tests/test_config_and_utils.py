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


def test_oracle_url_rejects_an_empty_value():
    # An empty ORACLE_URL is a misconfigured deployment, not a default. Failing at import is
    # the only moment the operator is still watching.
    with pytest.raises(ValidationError, match="ORACLE_URL environment variable is missing"):
        Settings(**{**BASE, "ORACLE_URL": "   "})


def test_a_malformed_authority_is_refused_without_the_insecure_opt_in():
    # An unparseable host is not loopback, so the insecure-HTTP guard still applies. Failing
    # closed here is the point: an operator who cannot state a parseable host has not opted in
    # to anything specific.
    with pytest.raises(ValidationError, match="Insecure HTTP protocol is not allowed"):
        Settings(**{**BASE, "ORACLE_URL": "http://[::1"})


def test_a_malformed_authority_is_accepted_when_insecure_http_is_opted_into():
    # urlparse defers the IPv6 check to the hostname property, so the validator catches it and
    # treats the host as unknown. With the opt-in set, an unknown host is allowed: the decision
    # to use plain http was made before the host was known and this does not second-guess it.
    settings = Settings(**{**BASE, "ORACLE_URL": "http://[::1", "ALLOW_INSECURE_ORACLE_HTTP": True})
    assert settings.ORACLE_URL == "http://[::1"


def test_a_url_with_no_host_is_refused_without_the_insecure_opt_in():
    with pytest.raises(ValidationError, match="Insecure HTTP protocol is not allowed"):
        Settings(**{**BASE, "ORACLE_URL": "http://"})


def test_insecure_http_opt_in_is_only_honoured_for_the_url_it_was_declared_before():
    # ALLOW_INSECURE_ORACLE_HTTP is declared first because validate_oracle_url reads it out of
    # ValidationInfo.data, which only holds the fields validated so far. Reordering the two
    # fields would make the opt-in silently stop working, so the dependency is asserted.
    assert list(Settings.model_fields).index("ALLOW_INSECURE_ORACLE_HTTP") < list(Settings.model_fields).index("ORACLE_URL")

    # With the opt-in present, the validator sees it and permits a remote http:// URL.
    allowed = Settings(**{**BASE, "ORACLE_URL": "http://erp.example.com", "ALLOW_INSECURE_ORACLE_HTTP": True})
    assert allowed.ORACLE_URL == "http://erp.example.com"


def test_optional_settings_have_their_documented_defaults():
    settings = Settings(**BASE)
    assert settings.CORS_ORIGINS == ""
    assert settings.REDIS_URL is None
    assert settings.ALLOW_INSECURE_ORACLE_HTTP is False


def test_unknown_environment_variables_are_ignored():
    # extra="ignore" is what lets a deployment carry variables this service does not read
    # without every worker refusing to start.
    assert Settings(**BASE, SOME_UNRELATED_FLAG="1").ORACLE_URL == "https://erp.example.com"


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
