"""生成工作佇列的失敗與有限重試。"""

import os
import pytest
from datetime import datetime, timedelta

from backend import db, jobs


def test_failed_job_retries_twice_then_fails(monkeypatch):
    job_id = db.create_generation_job("test", payload={"bid": "missing"})

    def fail(_job):
        raise RuntimeError("暫時性服務錯誤")

    monkeypatch.setattr(jobs, "_run", fail)

    assert jobs.run_pending_once() is True
    first = db.get_generation_job(job_id)
    assert first["status"] == "pending"
    assert first["attempts"] == 1
    assert first["error"] == "暫時性服務錯誤"

    assert jobs.run_pending_once() is True
    second = db.get_generation_job(job_id)
    assert second["status"] == "pending"
    assert second["attempts"] == 2

    assert jobs.run_pending_once() is True
    final = db.get_generation_job(job_id)
    assert final["status"] == "failed"
    assert final["attempts"] == 3
    assert final["error"] == "暫時性服務錯誤"


def test_worker_rejects_unknown_job_type(monkeypatch):
    job_id = db.create_generation_job("legacy_analyze", book_id=42, payload={"bid": "book-42"})
    for _ in range(3):
        assert jobs.run_pending_once() is True
    job = db.get_generation_job(job_id)
    assert job["status"] == "failed"
    assert "未知生成任務" in job["error"]


def test_queue_full_retry_honors_provider_retry_after(monkeypatch):
    from backend.services.tts_provider_v1 import ProviderV1Error

    job_id = db.create_generation_job("test", payload={"bid": "busy"})
    delays = []

    def fail(_job):
        raise ProviderV1Error("busy", code="queue_full", status=429,
                              retryable=True, retry_after=15)

    monkeypatch.setattr(jobs, "_run", fail)
    monkeypatch.setattr(jobs.time, "sleep", delays.append)
    assert jobs.run_pending_once() is True
    job = db.get_generation_job(job_id)
    assert job["status"] == "pending"
    assert delays == [15.0]


def test_stale_running_job_is_requeued_after_restart():
    job_id = db.create_generation_job("speaker_analysis", book_id=52, chapter_id=8,
                                      payload={"bid": "book-52", "seq": 0})
    old = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    db.execute(
        "UPDATE generation_jobs SET status='running', started_at=?, claimed_at=?, "
        "worker_instance_id=?, worker_version=?, worker_build_sha=?, worker_claim_token=? WHERE id=?",
        (old, old, "legacy-test-worker", "test-version", "test-sha", "test-token", job_id),
    )

    assert db.requeue_stale_generation_jobs(max_age_seconds=3600) == 1
    job = db.get_generation_job(job_id)
    assert job["status"] == "pending"
    assert job["started_at"] is None
    assert "重新排隊" in job["error"]


def test_live_worker_claim_is_not_requeued(monkeypatch):
    job_id = db.create_generation_job("speaker_analysis", book_id=53, chapter_id=9,
                                     payload={"bid": "book-53", "seq": 0})
    old = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    worker_id = "live-test-worker"
    db.execute(
        "INSERT INTO worker_instances(instance_id, worker_version, build_sha, process_id, db_path, "
        "started_at, heartbeat_at, active, worker_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
        (worker_id, "test-version", "test-sha", os.getpid(), "test.db", old, old, 1, 1),
    )
    db.execute(
        "UPDATE generation_jobs SET status='running', started_at=?, claimed_at=?, "
        "worker_instance_id=?, worker_version=?, worker_build_sha=?, worker_claim_token=? WHERE id=?",
        (old, old, worker_id, "test-version", "test-sha", "test-token", job_id),
    )
    monkeypatch.setattr(db, "_process_is_alive", lambda _pid: True)

    assert db.requeue_stale_generation_jobs(max_age_seconds=3600) == 0
    job = db.get_generation_job(job_id)
    assert job["status"] == "running"
    assert job["worker_instance_id"] == worker_id
