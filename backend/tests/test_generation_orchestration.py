from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from backend import db
from backend import storage
from backend.services import audio_generation as audio_generation_service
from backend.services import generation_orchestration as orchestration
from backend.services import content_requests, generation_pipeline
from .conftest import new_user, new_author, upload_book
from .fakes_generation import FakeAIProvider, FakeProviderError, FakeTTSProvider


def _identity(name):
    return {"instance_id": name, "worker_version": "test-orchestration-v2", "build_sha": "test"}


def test_claim_race_has_one_winner_and_attempt_history():
    job_id = db.create_generation_job("test", payload={"kind": "race"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda name: db.claim_generation_job(_identity(name)), ("worker-a", "worker-b")))
    winners = [job for job in claimed if job]
    assert len(winners) == 1
    assert winners[0]["id"] == job_id
    assert len(db.list_generation_attempts(job_id)) == 1


def test_provider_capacity_two_with_five_racing_workers(monkeypatch):
    monkeypatch.setenv("GENERATION_AI_MAX_CONCURRENCY", "10")
    provider_id = db.create_ai_provider({
        "name": "fake-capacity", "provider_type": "openai", "base_url": "https://example.test",
        "model": "fake", "max_concurrency": 2,
    })
    for _ in range(5):
        db.create_generation_job("speaker_analysis", provider={"id": provider_id, "name": "fake-capacity", "model": "fake", "config_version": 1})
    with ThreadPoolExecutor(max_workers=5) as pool:
        claimed = list(pool.map(lambda i: db.claim_generation_job(_identity(f"worker-{i}"), service_type="AI"), range(5)))
    assert sum(bool(job) for job in claimed) == 2
    active = db.query_one("SELECT COUNT(*) AS n FROM generation_jobs WHERE status='running' AND provider_id=?", (provider_id,))
    assert active["n"] == 2


def test_ten_workers_claim_five_jobs_once_each(monkeypatch):
    monkeypatch.setenv("GENERATION_TTS_MAX_CONCURRENCY", "10")
    provider_id = db.create_tts_provider({
        "name": "fake-claim-burst", "base_url": "https://claim-burst.example.test",
        "max_concurrency": 10,
    })
    job_ids = [db.create_generation_job(
        "audio_single", service_type="TTS",
        provider={"id": provider_id, "name": "fake-claim-burst", "config_version": 1},
    ) for _ in range(5)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        claimed = list(pool.map(
            lambda i: db.claim_generation_job(_identity(f"burst-worker-{i}"), service_type="TTS"),
            range(10),
        ))
    winners = [job for job in claimed if job]
    assert sorted(job["id"] for job in winners) == sorted(job_ids)
    assert all(len(db.list_generation_attempts(job_id)) == 1 for job_id in job_ids)


def test_full_provider_does_not_block_another_provider(monkeypatch):
    monkeypatch.setenv("GENERATION_AI_MAX_CONCURRENCY", "2")
    provider_a = db.create_ai_provider({
        "name": "fake-provider-a", "provider_type": "openai", "base_url": "https://a.example.test",
        "model": "fake-a", "max_concurrency": 1,
    })
    provider_b = db.create_ai_provider({
        "name": "fake-provider-b", "provider_type": "openai", "base_url": "https://b.example.test",
        "model": "fake-b", "max_concurrency": 1,
    })
    job_a = db.create_generation_job("speaker_analysis", provider={"id": provider_a, "name": "A"})
    job_b = db.create_generation_job("speaker_analysis", provider={"id": provider_b, "name": "B"})
    first = db.claim_generation_job(_identity("provider-a-worker"), service_type="AI")
    second = db.claim_generation_job(_identity("provider-b-worker"), service_type="AI")
    assert first and first["id"] == job_a
    assert second and second["id"] == job_b


def test_ai_and_tts_queues_are_independent(monkeypatch):
    monkeypatch.setenv("GENERATION_AI_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("GENERATION_TTS_MAX_CONCURRENCY", "1")
    ai_id = db.create_ai_provider({
        "name": "fake-ai-independent", "provider_type": "openai", "base_url": "https://example.test", "model": "fake",
    })
    tts_id = db.create_tts_provider({
        "name": "fake-tts-independent", "base_url": "http://127.0.0.1:8100", "max_concurrency": 1,
    })
    db.create_generation_job("speaker_analysis", provider={"id": ai_id, "name": "fake-ai-independent", "model": "fake", "config_version": 1})
    db.create_generation_job("audio_single", provider={"id": tts_id, "name": "fake-tts-independent", "config_version": 1})
    ai_job = db.claim_generation_job(_identity("ai"), service_type="AI")
    tts_job = db.claim_generation_job(_identity("tts"), service_type="TTS")
    assert ai_job and tts_job


def test_expired_lease_reclaims_and_fences_late_worker():
    job_id = db.create_generation_job("audio_single", payload={"kind": "lease"})
    first = db.claim_generation_job(_identity("old-worker"), lease_seconds=5)
    assert first and first["id"] == job_id
    expired = (datetime.now() - timedelta(seconds=5)).isoformat(timespec="seconds")
    db.execute("UPDATE generation_jobs SET lease_until=?, heartbeat_at=? WHERE id=?", (expired, expired, job_id))
    assert db.recover_expired_generation_jobs() == 1
    second = db.claim_generation_job(_identity("new-worker"), lease_seconds=60)
    assert second and second["worker_claim_token"] != first["worker_claim_token"]
    assert db.finalize_generation_job(job_id, first["worker_claim_token"], "success") is False
    assert db.finalize_generation_job(job_id, second["worker_claim_token"], "success") is True
    assert db.get_generation_job(job_id)["status"] == "success"
    assert db.list_generation_attempts(job_id)[0]["outcome"] == "worker_lost"


def test_healthy_long_tts_lease_survives_heartbeat():
    job_id = db.create_generation_job("audio_single")
    job = db.claim_generation_job(_identity("long-tts"), lease_seconds=5)
    assert job
    assert db.heartbeat_generation_job(job_id, job["worker_claim_token"], lease_seconds=120)
    assert db.recover_expired_generation_jobs() == 0
    assert db.get_generation_job(job_id)["status"] == "running"


def test_cancel_complete_race_converges_to_cancelled():
    job_id = db.create_generation_job("audio_single")
    job = db.claim_generation_job(_identity("worker"))
    assert job
    assert db.request_generation_job_cancel(job_id, actor_id=None) == "requested"
    assert db.finalize_generation_job(job_id, job["worker_claim_token"], "success") is True
    assert db.get_generation_job(job_id)["status"] == "cancelled"
    assert len(db.list_generation_attempts(job_id)) == 1


def test_retry_backoff_is_persisted_and_classification_is_bounded():
    info = orchestration.classify_error(type("E", (), {"code": "queue_full", "retry_after": 15, "retryable": True})())
    assert info["category"] == "provider_busy"
    assert info["retryable"] is True
    delay = orchestration.backoff_seconds(2, retry_after=15, jitter=0)
    assert delay == 15.0
    job_id = db.create_generation_job("audio_single")
    job = db.claim_generation_job(_identity("retry"))
    assert job and db.requeue_generation_job(job_id, "busy", next_attempt_at=orchestration.due_at(60),
                                             failure_category=info["category"], claim_token=job["worker_claim_token"])
    row = db.get_generation_job(job_id)
    assert row["status"] == "pending" and row["next_attempt_at"]


def test_migration_rerun_preserves_legacy_job_without_fabricated_attempt():
    job_id = db.create_generation_job("audio_single", book_id=None, payload={"legacy": True})
    db.update_generation_job(job_id, "success", 100)
    db.init_db()
    assert db.get_generation_job(job_id)["status"] == "success"
    assert db.list_generation_attempts(job_id) == []
    assert db.query("PRAGMA foreign_key_check") == []


def test_reviewer_operation_api_is_safe_and_author_cannot_operate_job():
    author = new_author("generation_author")
    reviewer = new_user("generation_reviewer", role="reviewer")
    provider_id = db.create_tts_provider({
        "name": "safe-provider", "base_url": "https://tts.example.test", "is_default": True,
        "secret_ciphertext": "encrypted-secret-that-must-not-leak",
    })
    db.replace_tts_provider_voices(provider_id, [{"id": "voice-1", "name": "測試", "lang": "zh"}])
    book = upload_book(author)
    submitted = author.post(f"/api/books/{book['id']}/requests", json={
        "requestType": "audiobook", "audioMode": "single", "defaultVoiceId": "voice-1",
    }).json()
    assert reviewer.post(f"/api/review/requests/{submitted['id']}/start").status_code == 200
    assert reviewer.post(f"/api/review/requests/{submitted['id']}/approve").status_code == 200
    assert author.post(f"/api/review/requests/{submitted['id']}/generation").status_code == 403
    started = reviewer.post(f"/api/review/requests/{submitted['id']}/generation")
    assert started.status_code == 200, started.text
    operation_id = started.json()["operationId"]
    detail = reviewer.get(f"/api/review/generation/operations/{operation_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["jobs"][0]["providerLabel"] == "safe-provider"
    assert "encrypted-secret" not in detail.text
    assert reviewer.get("/api/admin/generation/summary").status_code == 403


def test_source_revision_snapshot_rejects_changed_audiobook_before_execution():
    author = new_author("generation_revision_author")
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    original_revision = content_requests.calculate_revision(row, "audiobook", {})
    chapter = db.get_chapter(row["id"], 0)
    import hashlib
    changed_text = chapter["text"] + " source changed"
    db.execute("UPDATE chapters SET text=?, text_hash=? WHERE id=?", (
        changed_text, hashlib.sha256(changed_text.encode("utf-8")).hexdigest()[:16], chapter["id"],
    ))
    job = {"job_type": "audio_generate_all", "source_revision": original_revision,
           "chapter_id": None, "payload": '{"requestPayload": {}}'}
    with pytest.raises(generation_pipeline.ag.GenerationConflictError, match="source revision"):
        generation_pipeline._ensure_source_revision_current(job, row, chapter, {"requestPayload": {}})


def test_running_generation_source_change_cannot_activate_result():
    author = new_author("gen_source_race")
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    generation, _ = audio_generation_service.create_or_reuse_generation(
        book=dict(row), chapter=dict(chapter), source_text_hash=chapter["text_hash"],
        voice_id="voice-source-race", tts_provider={"id": None, "config_version": 1},
    )
    job_id = db.create_generation_job(
        "audio_single", book_id=row["id"], chapter_id=chapter["id"], service_type="TTS",
        source_revision=chapter["text_hash"], source_text_hash=chapter["text_hash"],
        payload={"generationId": generation["id"]},
    )
    claimed = db.claim_generation_job(_identity("source-race-worker"), service_type="TTS")
    assert claimed and claimed["id"] == job_id
    import hashlib
    changed_text = chapter["text"] + " changed during synthesis"
    db.execute("UPDATE chapters SET text=?, text_hash=? WHERE id=?", (
        changed_text, hashlib.sha256(changed_text.encode("utf-8")).hexdigest()[:16], chapter["id"],
    ))
    with pytest.raises(audio_generation_service.GenerationConflictError, match="source revision"):
        audio_generation_service.mark_generation_ready_and_activate(
            book=dict(row), chapter=dict(chapter), generation_id=generation["id"],
            audio_path=storage.generation_audio_path(book["id"], generation["id"]),
            timing_path=storage.generation_timing_path(book["id"], generation["id"]),
            job_id=job_id, claim_token=claimed["worker_claim_token"],
        )
    assert db.get_audio_generation(generation["id"])["status"] == "failed"
    assert db.get_chapter(row["id"], chapter["seq"])["active_audio_generation_id"] is None


def test_fake_providers_cover_deterministic_failure_contract():
    ai = FakeAIProvider(outcome="schema_failure")
    with pytest.raises(FakeProviderError) as ai_error:
        ai.analyze("text")
    assert ai_error.value.code == "schema_failure"
    tts = FakeTTSProvider(outcome="provider_busy")
    with pytest.raises(FakeProviderError) as tts_error:
        tts.synthesize("text", segment_id="s-1")
    assert tts.calls == ["s-1"]
    assert tts_error.value.retryable is True


def test_multi_speaker_job_waits_without_consuming_tts_attempt(monkeypatch):
    author = new_author("gen_dep_author")
    book = upload_book(author)
    db.update_book(book["id"], {"audio_mode": "multi"})
    row = db.get_book_row(book["id"])
    job_id = db.create_generation_job(
        "audio_generate_all", book_id=row["id"], service_type="TTS",
        payload={"bid": book["id"]},
    )
    assert orchestration.dependencies_ready(db.get_generation_job(job_id)) is False
    assert db.mark_generation_job_waiting_dependency(job_id, "等待 analysis") is True
    assert db.claim_generation_job(_identity("dependency-worker"), service_type="TTS") is None
    assert db.list_generation_attempts(job_id) == []
    monkeypatch.setattr(orchestration, "dependencies_ready", lambda job: True)
    claimed = db.claim_generation_job(_identity("dependency-worker-2"), service_type="TTS")
    assert claimed and claimed["id"] == job_id


def test_newer_generation_remains_active_when_older_worker_finishes_late():
    author = new_author("generation_supersede_author")
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    book_dict = dict(row)
    chapter_dict = dict(chapter)
    provider = {"id": None, "name": "fake", "config_version": 1, "adapter_key": "fake"}
    older, _ = audio_generation_service.create_or_reuse_generation(
        book=book_dict, chapter=chapter_dict, source_text_hash=chapter["text_hash"],
        voice_id="voice-old", tts_provider=provider,
    )
    newer, _ = audio_generation_service.create_or_reuse_generation(
        book=book_dict, chapter=chapter_dict, source_text_hash=chapter["text_hash"],
        voice_id="voice-new", tts_provider=provider,
    )
    audio_generation_service.mark_generation_ready_and_activate(
        book=book_dict, chapter=chapter_dict, generation_id=newer["id"],
        audio_path=storage.generation_audio_path(book["id"], newer["id"]),
        timing_path=storage.generation_timing_path(book["id"], newer["id"]),
    )
    audio_generation_service.mark_generation_ready_and_activate(
        book=book_dict, chapter=chapter_dict, generation_id=older["id"],
        audio_path=storage.generation_audio_path(book["id"], older["id"]),
        timing_path=storage.generation_timing_path(book["id"], older["id"]),
    )
    assert db.get_chapter(row["id"], chapter["seq"])["active_audio_generation_id"] == newer["id"]
