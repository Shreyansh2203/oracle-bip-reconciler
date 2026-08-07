
from src.services.reconciliation import _is_num_ok
from src.utils.date_formatter import format_oracle_date


def test_is_num_ok_exact_match():
    assert _is_num_ok("12345", "12345")
    assert _is_num_ok("INV-001", "INV-001")

def test_is_num_ok_substring():
    # Long strings (>4) support substring matches
    assert _is_num_ok("12345", "INV-12345")
    assert _is_num_ok("INV-12345", "12345")

def test_is_num_ok_fuzzy_match():
    # Up to 6 chars allows 1 typo
    assert _is_num_ok("12345", "12S45") # 'S' instead of '5'
    assert _is_num_ok("123456", "123450") # '0' instead of '6'

    # Beyond 6 chars allows 2 typos
    assert _is_num_ok("1234567", "12S456Z") # 'S' and 'Z' typos

def test_is_num_ok_failures():
    assert not _is_num_ok("12345", "123") # Too far
    assert not _is_num_ok("1234", "12S4") # Length < 5 doesn't allow fuzzy match
    assert not _is_num_ok("", "12345")
    assert not _is_num_ok(None, "12345")

def test_format_oracle_date_iso():
    assert format_oracle_date("2026-10-05") == "2026-10-05"
    assert format_oracle_date("2026-10-05T12:00:00Z") == "2026-10-05"

def test_format_oracle_date_variations():
    assert format_oracle_date("10-05-2026") == "2026-10-05"
    assert format_oracle_date("05-Oct-2026") == "2026-10-05"
    assert format_oracle_date("05 Oct 2026") == "2026-10-05"
    assert format_oracle_date("October 05, 2026") == "2026-10-05"

def test_format_oracle_date_compact():
    assert format_oracle_date("20261005") == "2026-10-05"

def test_format_oracle_date_invalid():
    assert format_oracle_date("not-a-date") is None
    assert format_oracle_date("") is None
    assert format_oracle_date(None) is None
