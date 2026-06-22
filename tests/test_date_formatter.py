from src.utils.date_formatter import format_oracle_date


def test_format_oracle_date_valid():
    # ISO formats
    assert format_oracle_date("2026-05-10") == "2026-05-10"
    assert format_oracle_date("2026-05-10T12:30:00") == "2026-05-10"
    assert format_oracle_date("2026-05-10T12:30:00.000Z") == "2026-05-10"

    # Slash formats
    assert format_oracle_date("2026/05/10") == "2026-05-10"
    assert format_oracle_date("10/05/2026") == "2026-05-10"

    # Hyphen formats
    assert format_oracle_date("10-05-2026") == "2026-05-10"


def test_format_oracle_date_invalid():
    # Should fall back to the empty string if parsing fails
    assert format_oracle_date("Not a date") == ""
    assert format_oracle_date("") == ""
    assert format_oracle_date(None) == ""


def test_format_oracle_date_whitespace():
    assert format_oracle_date("  2026-05-10  ") == "2026-05-10"


def test_format_oracle_date_timezone():
    assert format_oracle_date("2026-05-10T12:30:00+05:30") == "2026-05-10"
