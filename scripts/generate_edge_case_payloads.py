import json
import os
import random
import uuid
from typing import Any

CUSTOMERS_FILE = "Customers.txt"
OUTPUT_DIR = "Test Cases"


def load_customers() -> list[str]:
    with open(CUSTOMERS_FILE, encoding="utf-8") as f:
        # skip header
        lines = f.readlines()[1:]
        return list({line.strip() for line in lines if line.strip()})


def _build_payload(name: Any, ref: Any, date: Any, invoices: list[dict], amount: Any) -> dict[str, Any]:
    return {
        "customer_name": name,
        "payment_reference": ref,
        "payment_date": date,
        "header_id": random.randint(100000000000000, 999999999999999),
        "invoices": invoices,
        "total_amount": amount,
        "confidence_score": round(random.uniform(50.0, 99.9), 2),
    }


def _build_invoice(num: Any, date: Any, amount: Any) -> dict[str, Any]:
    return {
        "Line_ID": random.randint(100000000000000, 999999999999999),
        "invoice_number": num,
        "invoice_date": date,
        "invoice_amount": amount,
        "customer_invoice_number": "",
        "storeNo": "",
    }


def generate_payloads():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    customers = load_customers()

    payloads = {}
    happy_cust = random.choice(customers)

    # [Previous 20 Cases]
    payloads["01_happy_path.json"] = _build_payload(
        happy_cust,
        f"REF-{uuid.uuid4().hex[:6].upper()}",
        "2026-03-01",
        [_build_invoice("INV-100", "2026-02-15", 100.0)],
        100.0,
    )
    payloads["02_longest_name.json"] = _build_payload(
        max(customers, key=len), "REF-02", "2026-03-01", [_build_invoice("INV-200", "2026-02-15", 200.0)], 200.0
    )

    special_custs = [c for c in customers if "&" in c or "'" in c or "-" in c]
    special_cust = special_custs[0] if special_custs else "O'Reilly & Sons - Test"
    payloads["03_special_characters.json"] = _build_payload(
        special_cust, "REF-03", "2026-03-01", [_build_invoice("INV-300", "2026-02-15", 300.0)], 300.0
    )

    payloads["04_missing_customer_name.json"] = _build_payload(
        None, "E000011239", "2026-03-02", [_build_invoice("26691902484", "2026-01-24", 353.53)], 1731.61
    )
    payloads["05_missing_payment_ref.json"] = _build_payload(
        "Galati Fresh Market", None, "2026-03-02", [_build_invoice("26691902484", "2026-01-24", 353.53)], 1731.61
    )
    payloads["06_missing_both_name_and_ref.json"] = _build_payload(
        None, None, "2026-03-02", [_build_invoice("26691902484", "2026-01-24", 353.53)], 1731.61
    )
    payloads["07_massive_500_invoices.json"] = _build_payload(
        random.choice(customers),
        "REF-07",
        "2026-03-01",
        [_build_invoice(f"INV-{i}", "2026-01-01", 10.0) for i in range(500)],
        5000.0,
    )
    payloads["08_decimal_and_negative_amounts.json"] = _build_payload(
        random.choice(customers),
        "REF-08",
        "2026-03-01",
        [_build_invoice("INV-DEC-1", "2026-02-01", 100.005), _build_invoice("INV-DEC-2", "2026-02-01", -99.995)],
        0.01,
    )
    payloads["09_missing_invoice_data.json"] = _build_payload(
        random.choice(customers),
        "REF-09",
        "2026-03-01",
        [
            _build_invoice(None, "2026-02-01", 50.0),
            _build_invoice("INV-MISS-2", None, 25.0),
            _build_invoice("INV-MISS-3", "2026-02-01", None),
            _build_invoice("", "", 0.0),
        ],
        100.0,
    )
    payloads["10_zero_amount_payload.json"] = _build_payload(
        random.choice(customers),
        "REF-10",
        "2026-03-01",
        [_build_invoice("INV-ZERO-1", "2026-02-01", 0.0), _build_invoice("INV-ZERO-2", "2026-02-01", 0.0)],
        0.0,
    )
    payloads["11_empty_invoices.json"] = _build_payload(random.choice(customers), "REF-11", "2026-03-01", [], 100.0)

    dup_inv = _build_invoice("INV-DUP-1", "2026-02-01", 50.0)
    payloads["12_duplicated_invoices.json"] = _build_payload(
        random.choice(customers), "REF-12", "2026-03-01", [dup_inv, dup_inv], 100.0
    )
    payloads["13_very_large_numbers.json"] = _build_payload(
        random.choice(customers),
        "REF-13",
        "2026-03-01",
        [_build_invoice("INV-HUGE-1", "2026-02-01", 9999999999.99)],
        9999999999.99,
    )
    payloads["14_tiny_fractional_numbers.json"] = _build_payload(
        random.choice(customers), "REF-14", "2026-03-01", [_build_invoice("INV-TINY-1", "2026-02-01", 0.0001)], 0.0001
    )
    payloads["15_sql_injection_attempt.json"] = _build_payload(
        "'; DROP TABLE USERS; --",
        "<script>alert(1)</script>",
        "2026-03-01",
        [_build_invoice("1=1", "2026-02-01", 100.0)],
        100.0,
    )
    payloads["16_unicode_and_emojis.json"] = _build_payload(
        "テスト顧客 🌟👨‍💻", "REF-こんにちは", "2026-03-01", [_build_invoice("INV-🌟", "2026-02-01", 100.0)], 100.0
    )
    payloads["17_null_dates.json"] = _build_payload(
        random.choice(customers), "REF-17", None, [_build_invoice("INV-NODATE-1", None, 100.0)], 100.0
    )
    payloads["18_empty_strings.json"] = _build_payload("", "", "", [_build_invoice("", "", None)], None)
    payloads["19_same_invoice_diff_dates.json"] = _build_payload(
        random.choice(customers),
        "REF-19",
        "2026-03-01",
        [_build_invoice("INV-SAME", "2026-02-01", 100.0), _build_invoice("INV-SAME", "2026-02-15", 100.0)],
        200.0,
    )
    payloads["20_extreme_stress_10000_invoices.json"] = _build_payload(
        random.choice(customers),
        "REF-20",
        "2026-03-01",
        [_build_invoice(f"INV-{i}", "2026-01-01", 1.0) for i in range(10000)],
        10000.0,
    )

    # --- NEW COMPLETENESS SCENARIOS ---

    # 21. Extra Unknown JSON Fields (tests Pydantic schema strictness)
    p21 = _build_payload(happy_cust, "REF-21", "2026-03-01", [_build_invoice("INV-21", "2026-02-01", 100.0)], 100.0)
    p21["hacker_field_bypass"] = "should be ignored"
    p21["invoices"][0]["extra_invoice_detail"] = 999
    payloads["21_extra_unexpected_fields.json"] = p21

    # 22. Strict Type Mismatches (Ints for strings, strings for floats, booleans for strings)
    payloads["22_strict_type_mismatches.json"] = _build_payload(
        name=123456789,  # Int where string expected
        ref=True,  # Boolean where string expected
        date="2026-03-01",
        amount="100.50",  # String where float expected
        invoices=[_build_invoice(999, "2026-02-15", "50.25")],
    )

    # 23. ISO 8601 Full Timestamps instead of YYYY-MM-DD
    payloads["23_iso_timestamp_dates.json"] = _build_payload(
        name=happy_cust,
        ref="REF-23",
        date="2026-03-01T14:30:00.000Z",
        amount=100.0,
        invoices=[_build_invoice("INV-23", "2026-02-15T09:00:00+05:30", 100.0)],
    )

    # 24. Out of Bounds / Garbage Dates
    payloads["24_garbage_dates.json"] = _build_payload(
        name=happy_cust,
        ref="REF-24",
        date="9999-99-99",
        amount=100.0,
        invoices=[_build_invoice("INV-24A", "0000-00-00", 50.0), _build_invoice("INV-24B", "Not-A-Date", 50.0)],
    )

    # 25. Logical Amount Mismatch (Total amount vastly different from sum of invoices)
    payloads["25_logical_sum_mismatch.json"] = _build_payload(
        name=happy_cust,
        ref="REF-25",
        date="2026-03-01",
        amount=100000.0,  # 100k total
        invoices=[_build_invoice("INV-25", "2026-02-15", 5.0)],  # but only 5 invoice
    )

    # 26. Payload Size Bomb (1 Megabyte String)
    large_string = "A" * 1000000
    payloads["26_megabyte_string_payload.json"] = _build_payload(
        name=large_string,
        ref="REF-26",
        date="2026-03-01",
        amount=100.0,
        invoices=[_build_invoice("INV-26", "2026-02-15", 100.0)],
    )

    # --- NEW FINANCIAL & BUSINESS LOGIC SCENARIOS ---

    # 27. Over-payment (Receipt amount is greater than sum of invoices)
    payloads["27_overpayment_scenario.json"] = _build_payload(
        name=happy_cust,
        ref="REF-27",
        date="2026-03-01",
        amount=500.0,  # Paid 500
        invoices=[_build_invoice("INV-27A", "2026-02-15", 200.0)],  # Invoices only sum to 200
    )

    # 28. Under-payment (Receipt amount is less than sum of invoices)
    payloads["28_underpayment_scenario.json"] = _build_payload(
        name=happy_cust,
        ref="REF-28",
        date="2026-03-01",
        amount=100.0,  # Paid 100
        invoices=[
            _build_invoice("INV-28A", "2026-02-15", 100.0),
            _build_invoice("INV-28B", "2026-02-15", 50.0),  # Invoices sum to 150
        ],
    )

    # 29. Future Dates (Invoice and Payment in 2099)
    payloads["29_future_dates.json"] = _build_payload(
        name=happy_cust,
        ref="REF-29",
        date="2099-12-31",
        amount=100.0,
        invoices=[_build_invoice("INV-29", "2099-01-01", 100.0)],
    )

    # 30. Ancient Dates (Invoice and Payment in 1970)
    payloads["30_ancient_dates.json"] = _build_payload(
        name=happy_cust,
        ref="REF-30",
        date="1970-01-01",
        amount=100.0,
        invoices=[_build_invoice("INV-30", "1969-12-31", 100.0)],
    )

    # 31. Exactly Matching But Negative (Full Refund Scenario)
    payloads["31_full_refund_negative.json"] = _build_payload(
        name=happy_cust,
        ref="REF-31",
        date="2026-03-01",
        amount=-500.0,
        invoices=[_build_invoice("INV-31", "2026-02-15", -500.0)],
    )

    # 32. Missing Decimals (Amount is an integer type instead of float)
    p32 = _build_payload(happy_cust, "REF-32", "2026-03-01", [], 100)
    p32["total_amount"] = 100  # strict int
    p32["invoices"] = [
        {
            "Line_ID": 123,
            "invoice_number": "INV-32",
            "invoice_date": "2026-02-15",
            "invoice_amount": 100,
            "customer_invoice_number": "",
            "storeNo": "",
        }
    ]
    payloads["32_strict_integers.json"] = p32

    # 33. Whitespace Padded Strings (Tests trimming logic)
    payloads["33_whitespace_padded_strings.json"] = _build_payload(
        name=f"   {happy_cust}   ",
        ref="   REF-33   ",
        date=" 2026-03-01 ",
        amount=100.0,
        invoices=[_build_invoice("   INV-33   ", " 2026-02-15 ", 100.0)],
    )

    # 34. Completely Null Payload Body (Empty dictionary)
    payloads["34_completely_empty_dictionary.json"] = {}  # Tests FastAPI root validation

    # 35. Invoice Amount is Empty String (Very common upstream parsing error)
    payloads["35_empty_string_amounts.json"] = _build_payload(
        name=happy_cust,
        ref="REF-35",
        date="2026-03-01",
        amount="",
        invoices=[_build_invoice("INV-35", "2026-02-15", "")],
    )

    for filename, payload in payloads.items():
        path = os.path.join(OUTPUT_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Generated {filename}")


if __name__ == "__main__":
    generate_payloads()
