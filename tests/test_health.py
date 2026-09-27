from fastapi.testclient import TestClient

from api.index import app as vercel_app
from src.core.config import settings
from src.main import app

client = TestClient(app)


def test_vercel_shim_reexports_the_one_app_object():
    # vercel.json builds and routes to api/index.py, and that module is only allowed to
    # re-export the app. If it ever grows its own copy, the serverless deployment and the
    # local one would drift apart silently.
    assert vercel_app is app


def test_root_endpoint_no_auth():
    response = client.get("/")
    assert response.status_code == 200
    assert "Oracle BIP Reconciler" in response.text


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_check():
    response = client.get("/ready")
    if not settings.ORACLE_USER or not settings.ORACLE_PASS or not settings.ORACLE_URL:
        assert response.status_code == 503
    else:
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


def test_cors_fails_closed_when_no_origins_configured():
    # CORS_ORIGINS is unset in CI, so no origin may be echoed back. Browsers then refuse to
    # expose the response body to the calling page, which is the documented fail-closed path.
    if settings.CORS_ORIGINS:
        return

    response = client.get("/health", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers

    preflight = client.options(
        "/v1/reconcile/batch",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
