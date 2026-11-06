"""Tests for the app-level plumbing in ``src/main.py``.

These cover the lines the rest of the suite never reached: the JSON log
formatter (including its exception and request-id branches), the request-id
log adapter's guard against a non-dict ``extra``, and the Prometheus
``/metrics`` endpoint (whose early return in the metrics middleware is also
exercised by requesting it).
"""

import json
import logging

from fastapi.testclient import TestClient

from src.main import JsonFormatter, RequestIdAdapter, app

client = TestClient(app)


def test_json_formatter_emits_structured_payload():
    record = logging.LogRecord(
        name="reconciliation_api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request completed",
        args=(),
        exc_info=None,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "reconciliation_api"
    assert payload["message"] == "request completed"
    assert "timestamp" in payload
    assert "exc_info" not in payload
    assert "request_id" not in payload


def test_json_formatter_includes_exc_info_and_request_id():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="reconciliation_api",
            level=logging.ERROR,
            pathname=__file__,
            lineno=2,
            msg="failed",
            args=(),
            exc_info=__import__("sys").exc_info(),
        )
    record.request_id = "req-123"  # type: ignore[attr-defined]
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "req-123"
    assert "ValueError: boom" in payload["exc_info"]


def test_request_id_adapter_adds_request_id_to_extra():
    logger = logging.getLogger("reconciliation_api.test")
    adapter = RequestIdAdapter(logger, {"request_id": "req-42"})
    msg, kwargs = adapter.process("hello", {})
    assert msg == "hello"
    assert kwargs["extra"]["request_id"] == "req-42"


def test_request_id_adapter_rejects_non_dict_extra():
    logger = logging.getLogger("reconciliation_api.test")
    adapter = RequestIdAdapter(logger, {"request_id": "req-42"})
    try:
        adapter.process("hello", {"extra": "not-a-dict"})
    except TypeError as exc:
        assert str(exc) == "extra must be a dict"
    else:
        raise AssertionError("expected TypeError for non-dict extra")


def test_metrics_endpoint_serves_prometheus_output():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "python_gc_objects_collected_total" in response.text or "python_gc_objects_collected_total" in response.text


def test_health_still_works_alongside_metrics():
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
