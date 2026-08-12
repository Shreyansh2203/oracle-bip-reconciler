from fastapi.testclient import TestClient

from src.core.config import settings
from src.main import app

client = TestClient(app)

def test_root_endpoint_no_auth():
    # Because API_KEY dependency is on the root endpoint,
    # it requires the X-API-KEY header (or depends on how get_api_key is implemented).
    response = client.get("/")
    # If API_KEY is set in settings, it will return 401. Otherwise it might pass.
    if settings.API_KEY:
        assert response.status_code in (401, 403)
    else:
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
