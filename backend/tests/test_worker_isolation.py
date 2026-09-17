import sqlite3
import time
import os

import pytest

from backend import db, jobs, settings


def _identity(name="worker"):
    return {"instance_id": name, "worker_version": "storylingo-v4:test", "build_sha": "test"}


def test_process_probe_is_read_only_for_current_windows_process():
    assert db._process_is_alive(os.getpid()) is True


def test_same_version_worker_can_claim_and_audit_identity():
    assert db.register_worker_instance(
        instance_id="worker-a", worker_version="storylingo-v4:test", build_sha="test",
        process_id=1, db_path=settings.DB_PATH, worker_enabled=True)
    job_id = db.create_generation_job("audio_single", payload={})

    claimed = db.claim_generation_job(_identity("worker-a"))

    assert claimed["id"] == job_id
    assert claimed["worker_instance_id"] == "worker-a"
    assert claimed["worker_version"] == "storylingo-v4:test"
    assert claimed["worker_build_sha"] == "test"
    assert claimed["claimed_at"]
    assert claimed["worker_claim_token"]
    worker = db.list_worker_instances()[0]
    assert worker["service_types"] == "AI,TTS"


def test_different_build_is_refused_and_old_style_claim_is_blocked():
    assert db.register_worker_instance(
        instance_id="worker-current", worker_version="storylingo-v4:new", build_sha="new",
        process_id=os.getpid(), db_path=settings.DB_PATH, worker_enabled=True)
    assert not db.register_worker_instance(
        instance_id="worker-old", worker_version="storylingo-v4:old", build_sha="old",
        process_id=2, db_path=settings.DB_PATH, worker_enabled=True)

    job_id = db.create_generation_job("audio_single", payload={})
    with pytest.raises(sqlite3.IntegrityError, match="worker_identity_required"):
        db.execute("UPDATE generation_jobs SET status='running', started_at=? WHERE id=?", (db.ts(), job_id))
    assert db.get_generation_job(job_id)["status"] == "pending"


def test_disabled_worker_does_not_consume_queue(monkeypatch):
    monkeypatch.setattr(settings, "WORKER_ENABLED", False)
    job_id = db.create_generation_job("audio_single", payload={})

    assert jobs.start_worker() is False
    time.sleep(0.05)
    assert jobs.worker_status()["alive"] is False
    assert db.get_generation_job(job_id)["status"] == "pending"


def test_health_identity_is_exposed_without_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_ENABLED", False)
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["worker"]["enabled"] is False
    assert "instanceId" in body["worker"]
    assert "buildSha" in body["worker"]
    assert "api_key" not in response.text.lower()


def test_health_reports_worker_consumer_readiness(client, monkeypatch):
    monkeypatch.setattr(settings, "WORKER_ENABLED", True)
    monkeypatch.setattr(jobs, "_worker", None)
    monkeypatch.setattr(jobs, "_worker_identity", {"instance_id": "missing-worker"})
    monkeypatch.setattr(jobs, "_worker_state", "stopped")
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["worker"] is False
    assert body["worker"]["consumerReady"] is False
