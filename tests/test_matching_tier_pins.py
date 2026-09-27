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


def test_the_amount_and_date_bucket_refuses_a_row_with_an_unrelated_number():
    payload = _run(
        InvoiceItem(invoice_number="INV-0001234", invoice_date="2026-01-01", invoice_amount=100.0),
        ledger=[
            _ledger_row("INV-9000-XYZ", "2026-01-01", "100.00"),
            _ledger_row("INV-0001235", "2026-01-01", "5.00"),
        ],
    )
    matched = payload.invoices[0]
    assert matched.match_phase == "MATCHED"
    # INV-9000-XYZ agrees on amount and date but its number shares nothing with the
    # payload's, so it is not a candidate: binding a payment to it would reconcile
    # against an unrelated invoice. INV-0001235 is a one-edit fuzzy hit on the number
    # corroborated by the date, and is what a human would have chosen.
    assert matched.fusion_invoice_number == "INV-0001235"
    assert matched.match_rule == "FUZZY_NUMBER_AMOUNT_OR_DATE"


def test_the_amount_and_date_bucket_does_not_bind_a_different_invoice_on_row_order():
    request = {"invoice_number": "INV-AAA-777", "invoice_date": "2026-01-01", "invoice_amount": 99.0}
    first = _run(
        InvoiceItem(**request),
        ledger=[
            _ledger_row("INV-BBB-001", "2026-01-01", "99.00"),
            _ledger_row("INV-CCC-002", "2026-01-01", "99.00"),
        ],
    )
    second = _run(
        InvoiceItem(**request),
        ledger=[
            _ledger_row("INV-CCC-002", "2026-01-01", "99.00"),
            _ledger_row("INV-BBB-001", "2026-01-01", "99.00"),
        ],
    )
    # Neither row has any relationship to INV-AAA-777, so both invoices go to manual
    # review rather than one of them being bound according to BIP row order.
    assert first.invoices[0].match_phase == "UNMATCHED"
    assert second.invoices[0].match_phase == "UNMATCHED"


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
# The 1-way exact-number tier's uniqueness guard, which used to be inert.
#
# README.md describes the 1-way tier as refusing an exact number that two unmapped rows
# share, and then used to document that `matches_fuzzy_num[0]` took the first of those same
# rows immediately afterwards, so the refusal changed nothing. The bare fuzzy-number bucket
# now carries the same `len(...) == 1` guard as the amount-only and date-only buckets, so
# the 1-way guard does what the README says it does: a line whose exact number is shared and
# whose date and amount both disagree goes to review.
#
# Break it with: change `elif len(matches_fuzzy_num) == 1:` back to
# `elif matches_fuzzy_num:`.
# ---------------------------------------------------------------------------


def test_the_1_way_exact_number_guard_refuses_a_number_two_rows_share():
    for number in ("INV-0001", "AB1"):
        payload = _run(
            InvoiceItem(invoice_number=number, invoice_date="2026-05-05", invoice_amount=500.0),
            ledger=[
                _ledger_row(number, "2026-01-01", "10.00"),
                _ledger_row(number, "2026-02-02", "20.00"),
            ],
        )
        # 999.00 matches neither row and 2026-05-05 matches neither, so the two rows agree
        # with the line on nothing at all and there is no evidence for either.
        assert payload.invoices[0].match_phase == "UNMATCHED", number
        assert payload.invoices[0].match_rule is None, number
    # The single-candidate case is matched by the same path, and is unaffected.
    lone = _run(
        InvoiceItem(invoice_number="AB1", invoice_date="2026-05-05", invoice_amount=500.0),
        ledger=[_ledger_row("AB1", "2026-01-01", "10.00")],
    )
    assert lone.invoices[0].match_phase == "MATCHED"
    assert lone.invoices[0].fusion_invoice_number == "AB1"
    assert lone.invoices[0].match_rule == "EXACT_NUMBER_UNIQUE"


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
# A receipt reference used to have no minimum length, unlike an invoice number.
#
# The receipt comparison applied no floor, so a one-character payment reference matched any
# receipt number containing it, and the matched row's currency, status, customer number and
# applied amount were then reported as this payment's -- including as its `total_amount`,
# which a caller sending only a fragment does not have. The floor in
# `_is_substring_num_ok` now applies to both sides of both comparisons.
#
# Break it with: replace the `_is_substring_num_ok` call in the receipt loop with a bare
# `receipt_number.lower() in cand_num.lower()`.
# ---------------------------------------------------------------------------


def test_a_one_character_payment_reference_claims_no_receipt():
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
    assert payload.fusion_receipt_number is None
    # The wrong row's currency and status are no longer reported as this payment's.
    assert payload.fusion_currency is None
    assert payload.fusion_receipt_status_code is None
    assert payload.total_amount is None
    # _is_num_ok refuses the same pair, and both sides now read the same floor.
    from src.services.reconciliation import _is_num_ok

    assert not _is_num_ok("1", "RCP-999001")


def test_a_truncated_receipt_reference_still_claims_its_receipt():
    # The floor is a floor, not a prohibition: six characters against a long reference is
    # the OCR truncation the substring comparison exists to recover.
    payload = _run(
        payment_reference="RCP-999",
        receipts=[{"RECEIPT_NUMBER": "RCP-999001", "RECEIPT_AMOUNT": "1.00"}],
    )
    assert payload.fusion_receipt_number == "RCP-999001"


# ---------------------------------------------------------------------------
# An ambiguous numeric date is refused, and the refusal is logged.
#
# The order of FORMATS used to decide this, and it decided it differently depending on
# whether the year had four digits: %m-%d-%Y came before %d-%m-%Y, but %d-%m-%y came
# before %m-%d-%y. So "05-06-2026" was 6 May and "05-06-26" was 5 June -- the same field,
# read two ways, chosen by list order. A payload then matched whichever ledger row the
# guess landed on, and the response carried that row's number, date and amount.
#
# Neither reading is more correct than the other for an OCR corpus, so a numeric order
# whose day and month are both valid and different is refused instead. The caller's
# fallback for a refusal is a raw string comparison, which cannot match wrongly.
#
# Break it with: delete the is_ambiguous_numeric_date guard from format_oracle_date.
# ---------------------------------------------------------------------------


def test_an_ambiguous_numeric_date_is_refused_at_both_year_widths():
    from src.utils.date_formatter import format_oracle_date

    assert format_oracle_date("05-06-2026") is None
    assert format_oracle_date("05-06-26") is None
    assert format_oracle_date("2026-06-05") == "2026-06-05"


def test_an_ambiguous_date_binds_no_ledger_row_rather_than_the_wrong_one():
    # The pinned behaviour was that a payload dated 05-06-2026 bound a 5 June row, and it
    # bound it because the parse had guessed 6 May. With the date refused the line agrees
    # with neither row and goes to review instead of to the wrong customer row.
    payload = _run(
        InvoiceItem(invoice_number="INV-ZZZZ", invoice_date="05-06-2026", invoice_amount=50.0),
        ledger=[
            _ledger_row("INV-A", "2026-06-05", "50.00"),
            _ledger_row("INV-B", "2026-05-06", "50.00"),
        ],
    )
    assert payload.invoices[0].match_phase == "UNMATCHED"
    assert payload.invoices[0].fusion_invoice_number is None


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
