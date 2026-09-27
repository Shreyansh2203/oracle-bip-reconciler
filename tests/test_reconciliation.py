
from src.services.reconciliation import _is_amount_equal, _is_date_equal, _is_num_ok
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


def test_is_date_equal_normalises_before_comparing():
    # The same day written two ways is the same day, and the receipt fallback depends on it.
    assert _is_date_equal("08/14/2026", "2026-08-14")
    assert _is_date_equal("14-Aug-2026", "2026-08-14")
    assert not _is_date_equal("08/14/2026", "2026-08-15")


def test_is_date_equal_falls_back_to_raw_comparison_when_unparseable():
    # A date the engine cannot parse still has to be compared with *something*. Two identical
    # junk strings are treated as equal so a consistently formatted ledger column still
    # matches; a junk string never matches a real date.
    assert _is_date_equal("sometime in August", "sometime in August")
    assert not _is_date_equal("sometime in August", "2026-08-14")
    assert not _is_date_equal("", "2026-08-14")


def test_is_amount_equal_coerces_both_sides():
    assert _is_amount_equal("1,234.56", 1234.56)
    assert _is_amount_equal("100", 100.0)
    assert not _is_amount_equal("100", 101.0)


def test_is_amount_equal_requires_both_sides_to_be_present():
    # An absent amount is not a wildcard. Returning True here would let a number-only match
    # succeed against a ledger row that says nothing about money.
    assert not _is_amount_equal(None, 1.0)
    assert not _is_amount_equal(1.0, None)
    assert not _is_amount_equal(None, None)


def test_is_amount_equal_does_not_raise_on_unparseable_values():
    # A junk amount compares false rather than faulting the batch it belongs to.
    assert not _is_amount_equal(object(), 1.0)
    assert not _is_amount_equal(1.0, "not a number")
