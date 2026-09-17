from fastapi.testclient import TestClient

from backend.main import app


def test_liveness_and_readiness_endpoints():
    with TestClient(app) as client:
        live = client.get("/api/health/live")
        assert live.status_code == 200
        assert live.json()["ok"] is True

        ready = client.get("/api/health/ready")
        assert ready.status_code == 200
        assert ready.json()["ok"] is True
        assert ready.json()["checks"]["db"] is True
        assert ready.json()["checks"]["worker"] is True
