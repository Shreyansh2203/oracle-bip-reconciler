from starlette.requests import Request

from src.core.config import settings
from src.core.dependencies import require_api_key


def _fake_request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "headers": [[k.lower().encode(), v.encode()] for k, v in headers.items()],
    }
    return Request(scope)


def test_require_api_key_passes_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", None)
    # No header, no secret: the proxy is expected to authenticate instead.
    require_api_key(_fake_request({}))


def test_require_api_key_passes_with_correct_key(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "s3cr3t")
    require_api_key(_fake_request({"x-api-key": "s3cr3t"}))


def test_require_api_key_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "s3cr3t")
    try:
        require_api_key(_fake_request({"x-api-key": "wrong"}))
    except Exception as exc:
        from fastapi import HTTPException

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401 for wrong API key")


def test_require_api_key_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "s3cr3t")
    try:
        require_api_key(_fake_request({}))
    except Exception as exc:
        from fastapi import HTTPException

        assert isinstance(exc, HTTPException)
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401 for missing API key")
