from src.services.discovery import _filter_data_rows, _is_data_row


def test_is_data_row():
    # Row with just params
    assert not _is_data_row({"P_ORG_ID": "123", "P_DATE": "2023"})

    # Row with actual data
    assert _is_data_row({"BILL_CUSTOMER_NAME": "Test Corp", "TRANSACTION_NUMBER": "123"})

def test_filter_data_rows():
    rows = [
        {"P_ORG_ID": "123"},
        {"BILL_CUSTOMER_NAME": "Test Corp"}
    ]
    filtered = _filter_data_rows(rows)
    assert len(filtered) == 1
    assert filtered[0]["BILL_CUSTOMER_NAME"] == "Test Corp"

def test_filter_data_rows_empty():
    assert _filter_data_rows([]) == []
