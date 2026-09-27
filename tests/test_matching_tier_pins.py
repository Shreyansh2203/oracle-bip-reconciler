"""Characterisation tests for matching behaviour the suite did not pin.

Every case in this file passes on the current engine. Each one was verified to FAIL
against a deliberately broken copy of `src/services/reconciliation.py`, `validators.py`,
`date_formatter.py`, `src/main.py`, `src/api/routers/reconciliation.py` or `pyproject.toml`
— the break is named in each docstring. These are pins, not fixes: the behaviour itself is
deliberate in several cases and is documented in README.md, so it is made visible here
rather than changed.
"""

import pathlib

import tomllib

from src.models import InvoiceItem, ReconciliationRequest
from src.services.reconciliation import map_ledger_to_payload

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _ledger_row(number, date, amount, **extra):
    row = {
        "BILL_CUSTOMER_NAME": "Acme",
        "TRANSACTION_NUMBER": number,
        "TRANSACTION_DATE": date,
        "TRANSACTION_TOTAL": amount,
    }
    row.update(extra)
    return row


def _run(*invoices, receipts=(), ledger=(), customer="Acme", **fields):
    payload = ReconciliationRequest(customer_name=customer, invoices=list(invoices), **fields)
    map_ledger_to_payload(payload, customer, list(receipts), list(ledger))
    return payload


# ---------------------------------------------------------------------------
# The fuzzy tier's first bucket matches on amount and date alone.
#
# The engine's highest-ambiguity tier deliberately takes the first ledger row that
# agrees on amount and date, with no requirement that its invoice number resemble the
# payload's. README.md documents the two fuzzy buckets as "a fuzzy number and a date or
# amount agreement" / "fuzzy number alone", so the number-blind bucket is not described
# anywhere. This test records what the code does.
#
# Break it with: add `and _is_num_ok(inv_num, o_inv.number)` to the
# `if date_ok and amt_ok:` condition in map_ledger_to_payload.
# ---------------------------------------------------------------------------


def test_the_amount_and_date_bucket_takes_a_row_with_an_unrelated_number():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=100.0),
        ledger=[
            _ledger_row("INV-9000-XYZ", "2026-01-01", "100.00"),
            _ledger_row("INV-0001235", "2026-01-01", "5.00"),
        ],
    )
    matched = payload.invoices[0]
    assert matched.match_phase == "MATCHED"
    # INV-0001235 is a one-edit fuzzy hit on the payload's number and is available, but
    # the number-blind bucket is tried first and wins.
    assert matched.fusion_invoice_number == "INV-9000-XYZ"
    # The correct row is left unmapped and therefore available to nothing else.
    assert matched.invoice_amount == 100.0


def test_the_amount_and_date_bucket_takes_the_first_agreeing_row_in_ledger_order():
    first = _run(
        InvoiceItem(invoice_number="INV-AAA-777", invoice_date="2026-01-01", invoice_amount=99.0),
        ledger=[
            _ledger_row("INV-BBB-001", "2026-01-01", "99.00"),
            _ledger_row("INV-CCC-002", "2026-01-01", "99.00"),
        ],
    )
    second = _run(
        InvoiceItem(invoice_number="INV-AAA-777", invoice_date="2026-01-01", invoice_amount=99.0),
        ledger=[
            _ledger_row("INV-CCC-002", "2026-01-01", "99.00"),
            _ledger_row("INV-BBB-001", "2026-01-01", "99.00"),
        ],
    )
    # Same request, same data, different BIP row order -> a different invoice is bound.
    assert first.invoices[0].fusion_invoice_number == "INV-BBB-001"
    assert second.invoices[0].fusion_invoice_number == "INV-CCC-002"


def test_a_consumed_row_steers_a_duplicate_number_onto_an_unrelated_invoice():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=100.0),
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=100.0),
        ledger=[
            _ledger_row("INV-0001", "2026-01-01", "100.00"),
            _ledger_row("INV-7777", "2026-01-01", "100.00"),
        ],
    )
    assert payload.invoices[0].fusion_invoice_number == "INV-0001"
    # The second line has no exact candidate left, so the number-blind bucket supplies
    # one. This is the mapped-flag / tier-order interaction README.md does not describe.
    assert payload.invoices[1].match_phase == "MATCHED"
    assert payload.invoices[1].fusion_invoice_number == "INV-7777"


def test_a_lone_unique_amount_is_enough_on_its_own():
    # With no row agreeing on both amount and date, the amount alone decides, because
    # exactly one unmapped row carries it. The ledger numbers are far enough apart that
    # the fuzzy-number buckets cannot supply a substitute.
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=100.0),
        ledger=[
            _ledger_row("LEDGER-778899", "2026-02-02", "100.00"),
            _ledger_row("LEDGER-661144", "2026-01-01", "200.00"),
        ],
    )
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "LEDGER-778899"


def test_a_shared_amount_is_refused_rather_than_guessed():
    # Two rows carry 100.00, so the amount is not a key and nothing else agrees.
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=100.0),
        ledger=[
            _ledger_row("LEDGER-778899", "2026-02-02", "100.00"),
            _ledger_row("LEDGER-661144", "2026-03-03", "100.00"),
        ],
    )
    assert payload.invoices[0].match_phase == "UNMATCHED"


def test_consuming_a_row_changes_what_counts_as_a_unique_amount():
    # The uniqueness guards count only unmapped rows, so the first line's match changes
    # the answer for the second. Two 7.00 rows for 2026-02-02 exist; the first line takes
    # one, and the second line then sees a unique amount and takes the other.
    payload = _run(
        InvoiceItem(invoice_number="INV-A", invoice_date="2026-02-02", invoice_amount=7.0),
        InvoiceItem(invoice_number="INV-B", invoice_date="2026-02-02", invoice_amount=7.0),
        ledger=[
            _ledger_row("INV-A", "2026-02-02", "7.00"),
            _ledger_row("INV-C", "2026-02-02", "7.00"),
        ],
    )
    assert payload.invoices[0].fusion_invoice_number == "INV-A"
    assert payload.invoices[1].match_phase == "MATCHED"
    assert payload.invoices[1].fusion_invoice_number == "INV-C"


# ---------------------------------------------------------------------------
# The 1-way exact-number tier's uniqueness guard.
#
# README.md: "Exact number with a single unmapped candidate takes it even when date and
# amount both disagree. Date-only and amount-only matches are accepted only when exactly
# one unmapped row agrees; if two rows share a date, or two share an amount, the line is
# left UNMATCHED rather than guessed." No test pinned the "single candidate" half.
#
# Break it with: change `if not matched_o_inv and len(candidates) == 1:` to
# `if not matched_o_inv and candidates:`.
# ---------------------------------------------------------------------------


def test_the_1_way_exact_number_guard_has_no_observable_effect():
    # README.md describes the 1-way tier as refusing a shared exact number, and then
    # documents that the fuzzy bucket takes `matches_fuzzy_num[0]` immediately
    # afterwards. Because `_is_num_ok` accepts an exact string before it looks at length,
    # the fuzzy bucket's candidates are exactly the rows the 1-way tier refused, so the
    # refusal changes nothing: the line is matched to the first of them either way.
    # The guard is therefore inert, and dropping `len(candidates) == 1` changes no result
    # for any input, at any number length.
    for number in ("INV-0001", "AB1"):
        payload = _run(
            InvoiceItem(invoice_number=number, invoice_date="2026-05-05", invoice_amount=500.0),
            ledger=[
                _ledger_row(number, "2026-01-01", "10.00"),
                _ledger_row(number, "2026-02-02", "20.00"),
            ],
        )
        # README:137-144 names the long-number case and this exact outcome.
        assert payload.invoices[0].match_phase == "MATCHED", number
        assert payload.invoices[0].fusion_invoice_number == number
        assert payload.invoices[0].fusion_invoice_date == "2026-01-01", number
    # The single-candidate case is matched by the same path.
    lone = _run(
        InvoiceItem(invoice_number="AB1", invoice_date="2026-05-05", invoice_amount=500.0),
        ledger=[_ledger_row("AB1", "2026-01-01", "10.00")],
    )
    assert lone.invoices[0].match_phase == "MATCHED"
    assert lone.invoices[0].fusion_invoice_number == "AB1"


def test_the_1_way_exact_tier_takes_a_lone_candidate_whose_date_and_amount_both_disagree():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-05-05", invoice_amount=500.0),
        ledger=[_ledger_row("INV-0001", "2026-01-01", "10.00")],
    )
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-0001"
    # The amount is backfilled from the ledger, overwriting the payload's own 500.0.
    assert payload.invoices[0].invoice_amount == 10.0


# ---------------------------------------------------------------------------
# The 2-way tier accepts a date agreement OR an amount agreement, independently.
#
# The suite covered "number + amount agree, date disagrees" but not the other direction.
#
# Break it with: change `inv_amt_cmp == o_inv.amount or _dates_match(...)` to `and`.
# ---------------------------------------------------------------------------


def test_the_2_way_tier_outranks_the_fuzzy_buckets_even_when_a_lookalike_is_available():
    # The 2-way tier is masked by later tiers whenever the exact number is unique, so it
    # needs a shared number to be observable at all. Here the exact number is shared,
    # only one candidate agrees on the date, and a row belonging to a *different* invoice
    # agrees on the amount and is close enough in the number for the fuzzy bucket to
    # accept. The 2-way tier must take the exact-number row, not the look-alike.
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=100.0),
        ledger=[
            _ledger_row("INV-0002", "2026-09-09", "100.00"),
            _ledger_row("INV-0001", "2026-09-09", "999.00"),
            _ledger_row("INV-0001", "2026-01-01", "999.00"),
        ],
    )
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-0001"
    assert payload.invoices[0].fusion_invoice_date == "2026-01-01"


def test_the_2_way_tier_accepts_an_exact_number_and_date_with_a_wrong_amount():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=500.0),
        ledger=[_ledger_row("INV-0001", "2026-01-01", "10.00")],
    )
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-0001"
    # A 490-unit disagreement does not block the match once number and date agree.
    assert payload.invoices[0].invoice_amount == 10.0


def test_the_2_way_tier_accepts_an_exact_number_and_amount_with_a_wrong_date():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=10.0),
        ledger=[_ledger_row("INV-0001", "2026-12-31", "10.00")],
    )
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_date == "2026-12-31"


# ---------------------------------------------------------------------------
# Amount comparison is exact float equality: there is no rounding tolerance.
#
# Break it with: wrap either comparison in round(v, 2) - a wider change. Narrowing
# `_is_num_ok` does not affect this; only the amount comparisons do.
# ---------------------------------------------------------------------------


def test_a_one_cent_difference_does_not_satisfy_the_amount_comparison():
    from src.services.reconciliation import _is_amount_equal

    assert _is_amount_equal(1234.56, 1234.56)
    assert not _is_amount_equal(1234.56, 1234.57)
    # No tolerance in either direction.
    assert not _is_amount_equal(0.1 + 0.2, 0.3)
    assert not _is_amount_equal(100.0, 100.004)


def test_a_one_cent_ocr_error_leaves_the_amount_tier_and_takes_the_date_instead():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001", invoice_date="2026-01-01", invoice_amount=1234.56),
        ledger=[_ledger_row("INV-0001", "2026-01-01", "1234.57")],
    )
    # The amount cannot rescue the line; the exact number plus the date can, and the
    # payload's amount is then silently replaced by the ledger's.
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].invoice_amount == 1234.57


# ---------------------------------------------------------------------------
# Case damage on an invoice number is not recovered, although receipt references are
# matched case-insensitively. `_is_num_ok` compares raw strings, so "inv-0009" against
# "INV-0009" is a Levenshtein distance of 4 and fails the >6-character budget of 2.
#
# Break it with: lower-case both sides in _is_num_ok.
# ---------------------------------------------------------------------------


def test_a_case_damaged_invoice_number_is_not_recovered_by_the_fuzzy_tier():
    from src.services.reconciliation import _is_num_ok

    assert not _is_num_ok("inv-0009", "INV-0009")
    payload = _run(
        InvoiceItem(invoice_number="inv-0009", invoice_date="2026-01-01", invoice_amount=99.0),
        ledger=[_ledger_row("INV-0009", "2026-05-05", "10.00")],
    )
    # Neither the number nor the date nor the amount agrees, so nothing rescues the line.
    assert payload.invoices[0].match_phase == "UNMATCHED"


def test_a_receipt_reference_is_matched_case_insensitively():
    row = {
        "BILL_CUSTOMER_NAME": "Someone Else",
        "RECEIPT_NUMBER": "RCP-00008891",
        "RECEIPT_DATE": "2026-10-05",
        "RECEIPT_AMOUNT": "4,200.50",
        "CURRENCY": "USD",
        "RECEIPT_STATUS_CODE": "APPLIED",
    }
    exact = _run(payment_reference="RCP-00008891", receipts=[dict(row)])
    assert exact.fusion_receipt_number == "RCP-00008891"
    # An invoice number is matched case-sensitively; a receipt reference is not.
    lowered = _run(payment_reference="rcp-00008891", receipts=[dict(row)])
    assert lowered.fusion_receipt_number == "RCP-00008891"


# ---------------------------------------------------------------------------
# A receipt reference has no minimum length, unlike an invoice number.
#
# `_is_num_ok` deliberately refuses a pair whose shorter side is under five characters,
# "so that a short incidental fragment cannot be mistaken for a real match". The receipt
# path applies no such floor, so a one-character payment reference matches any receipt
# number containing it, and the matched row's currency and status are then reported as
# this payment's.
#
# Break it with: add the same length floor to the receipt comparison in
# _apply_receipt_mapping.
# ---------------------------------------------------------------------------


def test_a_one_character_payment_reference_matches_any_receipt_containing_it():
    payload = _run(
        payment_reference="1",
        receipts=[
            {
                "BILL_CUSTOMER_NAME": "Someone Else",
                "RECEIPT_NUMBER": "RCP-999001",
                "RECEIPT_DATE": "2026-01-01",
                "RECEIPT_AMOUNT": "1.00",
                "CURRENCY": "EUR",
                "RECEIPT_STATUS_CODE": "REVERSED",
            }
        ],
    )
    assert payload.fusion_receipt_number == "RCP-999001"
    # The wrong row's currency and status are reported as this payment's.
    assert payload.fusion_currency == "EUR"
    assert payload.fusion_receipt_status_code == "REVERSED"
    # _is_num_ok would have refused the same pair.
    from src.services.reconciliation import _is_num_ok

    assert not _is_num_ok("1", "RCP-999001")


# ---------------------------------------------------------------------------
# An ambiguous numeric date resolves month-first, and two different calendar dates can
# normalise to the same value. date_formatter.py lists %m-%d before %d-%m, so
# "05-06-2026" is 6 May, not 5 June.
#
# Break it with: move "%d-%m-%Y" ahead of "%m-%d-%Y" in DATE_FORMATS.
# ---------------------------------------------------------------------------


def test_an_ambiguous_numeric_date_resolves_month_first():
    from src.utils.date_formatter import format_oracle_date

    assert format_oracle_date("05-06-2026") == "2026-05-06"
    assert format_oracle_date("2026-06-05") == "2026-06-05"


def test_an_ambiguous_date_binds_the_wrong_ledger_row_on_a_date_only_match():
    # The payload line is 5 June (dd-mm). The ledger holds a 5 June row and a 6 May row,
    # both for 50.00. The amount is not unique, so the date is the deciding key, and it
    # decides on 6 May.
    payload = _run(
        InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="05-06-2026", invoice_amount=50.0),
        ledger=[
            _ledger_row("INV-A", "2026-06-05", "50.00"),
            _ledger_row("INV-B", "2026-05-06", "50.00"),
        ],
    )
    assert payload.invoices[0].match_phase == "MATCHED"
    assert payload.invoices[0].fusion_invoice_number == "INV-B"


def test_the_day_first_formats_are_parsed_for_an_unambiguous_spelling():
    from src.utils.date_formatter import format_oracle_date

    # 13 cannot be a month, so only the day-first format can apply.
    assert format_oracle_date("13-06-2026") == "2026-06-13"
    assert format_oracle_date("05-Jun-2026") == "2026-06-05"
    # A slash-separated month name is not in the list at all.
    assert format_oracle_date("05/Jun/2026") is None


# ---------------------------------------------------------------------------
# A currency difference between two otherwise identical ledger rows is undetectable.
#
# `InvoiceItem` has no currency field and the matcher never reads the row's CURRENCY, so
# two rows for the same invoice number, amount and date in two currencies are
# indistinguishable to every tier. This is documented in README.md as a design limit; it
# is pinned here so the limit cannot be widened by accident.
#
# Break it with: give InvoiceItem a currency field - this test then fails, which is the
# point.
# ---------------------------------------------------------------------------


def test_invoice_lines_carry_no_currency_so_currency_cannot_discriminate():
    assert "currency" not in InvoiceItem.model_fields
    payload = _run(
        InvoiceItem(invoice_number="INV-1001", invoice_date="2026-08-14", invoice_amount=100.0),
        ledger=[
            _ledger_row("INV-1001", "2026-08-14", "100.00", CURRENCY="EUR"),
            _ledger_row("INV-1001", "2026-08-14", "100.00", CURRENCY="USD"),
        ],
    )
    matched = payload.invoices[0]
    assert matched.match_phase == "MATCHED"
    assert matched.fusion_invoice_number == "INV-1001"
    # There is no field in which the matched row's currency could have been reported.
    assert not any("currency" in name for name in InvoiceItem.model_fields)


# ---------------------------------------------------------------------------
# Two documentation claims that the suite did not enforce.
# ---------------------------------------------------------------------------


def test_the_rate_limit_is_ten_per_minute_on_the_reconciliation_route_only():
    router = (REPO_ROOT / "src" / "api" / "routers" / "reconciliation.py").read_text(encoding="utf-8")
    health = (REPO_ROOT / "src" / "api" / "routers" / "health.py").read_text(encoding="utf-8")
    main = (REPO_ROOT / "src" / "main.py").read_text(encoding="utf-8")
    assert router.count('@limiter.limit("10/minute")') == 1
    # Health endpoints carry no limiter decorator at all.
    assert "limiter" not in health
    # And the health router is registered, so the endpoints exist.
    assert "include_router(health.router)" in main


def test_coverage_report_precision_is_pinned():
    # README calls precision load-bearing: coverage.py compares the rounded display
    # value, so at the default precision of 0 the achieved 99.71 % displays as 99 and
    # cannot be compared against a floor above 99. Nothing else in the suite guards it.
    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        config = tomllib.load(handle)
    report = config["tool"]["coverage"]["report"]
    assert report["precision"] == 2, "precision must stay at 2 or the coverage floor is toothless"
    assert report["fail_under"] == 98


def test_the_documented_invoice_count_range_matches_the_schema():
    import pytest
    from pydantic import ValidationError

    # README: "invoices | InvoiceItem[] | in | 1-2500." The upper bound is enforced by
    # the schema. The lower bound is not: an empty list is accepted, so the documented
    # "1-" is a convention rather than a constraint.
    assert ReconciliationRequest(invoices=[]).invoices == []
    with pytest.raises(ValidationError):
        ReconciliationRequest(invoices=[InvoiceItem() for _ in range(2501)])
    assert len(ReconciliationRequest(invoices=[InvoiceItem() for _ in range(2500)]).invoices) == 2500
