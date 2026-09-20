"""Minimal production-runtime smoke test.

This intentionally exercises the real FastAPI application in production mode
without calling external AI/TTS providers. Readiness is expected to be 503 when
no production TTS provider is configured; that proves the production safety gate
is active rather than silently bypassed.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="storylingo-production-smoke-") as tmp:
        root = Path(tmp)
        data = root / "data"
        storage = root / "storage"
        data.mkdir()
        storage.mkdir()

        os.environ.update(
            {
                "APP_ENV": "production",
                "ADMIN_PASSWORD": "ci-production-smoke-password",
                "AI_API_KEY": "ci-production-smoke-key",
                "DB_PATH": str(data / "app.db"),
                "WORKER_ENABLED": "false",
            }
        )

        # Import only after production environment variables are in place.
        from backend import settings
        from backend.main import app

        # settings.py derives paths from ROOT_DIR, while DB_PATH is explicitly
        # overridden above. Keep runtime directories isolated from the checkout.
        settings.DB_PATH = str(data / "app.db")
        settings.APP_ENV = "production"
        settings.ADMIN_PASSWORD = "ci-production-smoke-password"
        settings.AI_API_KEY = "ci-production-smoke-key"
        settings.WORKER_ENABLED = False

        with TestClient(app) as client:
            live = client.get("/api/health/live")
            if live.status_code != 200:
                raise AssertionError(f"live endpoint returned {live.status_code}")
            if live.json().get("ok") is not True:
                raise AssertionError("live endpoint did not report ok=true")

            ready = client.get("/api/health/ready")
            if ready.status_code != 503:
                raise AssertionError(
                    "production readiness must reject an unconfigured TTS provider; "
                    f"got HTTP {ready.status_code}: {ready.text}"
                )
            body = ready.json()
            if body.get("ok") is not False or body.get("checks", {}).get("tts") is not False:
                raise AssertionError("production TTS readiness gate is not active")

    print("PRODUCTION SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
