"""分析 job 重啟收斂與作者取消分析的最小回歸。"""

import json

import pytest

from backend import db, jobs, settings
from backend.services import analysis as analysis_svc
from backend.services import analysis_executor
from backend.tests.conftest import new_admin, new_author, upload_book


def _make_ai_provider(admin):
    response = admin.post("/api/admin/ai/providers", json={
        "name": "取消分析測試 AI",
        "providerType": "openai_compatible",
        "baseUrl": "https://ai.example.com/v1",
        "model": "test-model",
        "apiKey": "sk-test-only",
        "isDefault": True,
    })
    assert response.status_code == 200, response.text


def _create_analysis(author):
    book = upload_book(author, text="第一章 取消測試。\n" + "這是一段測試文字。\n" * 8)
    db.update_book(book["id"], {"audio_mode": "multi"})
    response = author.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert response.status_code == 200, response.text
    return book, response.json()


def _make_tts_provider():
    return db.create_tts_provider({
        "name": "取消分析測試 TTS",
        "provider_type": "generic_http",
        "base_url": "https://tts.example.com",
        "enabled": True,
        "is_default": True,
    })


def test_author_can_cancel_queued_analysis_and_retry_creates_new_history(client):
    admin = new_admin("取消分析管理員")
    _make_ai_provider(admin)
    _make_tts_provider()
    author = new_admin("取消分析操作員")
    book, created = _create_analysis(author)

    response = author.post(f"/api/books/{book['id']}/chapters/0/analysis/cancel")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "analysisId": created["analysisId"],
        "jobId": created["jobId"],
        "status": "cancelled",
        "analysisStatus": "failed",
    }

    job = db.get_generation_job(created["jobId"])
    analysis = db.get_chapter_analysis(created["analysisId"])
    assert job["status"] == "cancelled"
    assert analysis["status"] == "failed"
    assert analysis_svc.parse_failure_error(analysis["error"])["code"] == "analysis_cancelled"
    progress = analysis_svc.parse_analysis_progress(analysis)
    assert progress["currentStage"] == "failed"
    assert progress["runningChunks"] == 0

    workflow = author.get(f"/api/books/{book['id']}").json()["workflow"]["chapters"]["0"]
    assert workflow["state"] == "analysis_failed"
    assert workflow["nextAction"] == "analyze"

    response = author.post(f"/api/books/{book['id']}/chapters/0/analysis/cancel")
    assert response.status_code == 409
    assert response.json()["code"] == "analysis_not_cancellable"

    retried = author.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert retried.status_code == 200, retried.text
    assert retried.json()["analysisId"] != created["analysisId"]
    assert retried.json()["jobId"] != created["jobId"]


def test_cancel_requires_owner_and_existing_csrf_contract(client):
    admin = new_admin("取消分析權限管理員")
    _make_ai_provider(admin)
    owner = new_admin("取消分析作品操作員")
    book, created = _create_analysis(owner)
    stranger = new_author("取消分析陌生作者")

    response = stranger.post(f"/api/books/{book['id']}/chapters/0/analysis/cancel")
    assert response.status_code in (403, 404)
    assert db.get_generation_job(created["jobId"])["status"] == "pending"

    original_env = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        response = owner.post(f"/api/books/{book['id']}/chapters/0/analysis/cancel")
        assert response.status_code == 403
        assert "CSRF" in response.text
    finally:
        settings.APP_ENV = original_env


def test_cancelled_claimed_job_is_not_executed(client, monkeypatch):
    admin = new_admin("取消分析競態管理員")
    _make_ai_provider(admin)
    author = new_admin("取消分析競態操作員")
    book, created = _create_analysis(author)
    claimed = db.claim_generation_job({
        "instance_id": "test-worker",
        "worker_version": "test-version",
        "build_sha": "test-build",
    })
    assert claimed["id"] == created["jobId"]
    assert author.post(f"/api/books/{book['id']}/chapters/0/analysis/cancel").status_code == 200

    monkeypatch.setattr(
        "backend.services.analysis_executor.analyzer.analyze_source_faithful",
        lambda *args, **kwargs: pytest.fail("取消後不得呼叫 AI"),
    )
    with pytest.raises(analysis_svc.AnalysisCancelledError):
        jobs._run(claimed)
    assert db.get_generation_job(created["jobId"])["status"] == "cancelled"


def test_startup_recovery_closes_lost_worker_before_attempt_exhaustion(client, monkeypatch):
    author = new_admin("啟動孤兒分析操作員")
    book = upload_book(author, text="第一章 啟動修復。\n" + "內容。\n" * 20)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 4,
        "analysis_profile": "speaker", "status": "running",
        "progress_json": "{}",
    })
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=analysis_id,
        provider={"id": 1, "model": "test-model", "config_version": 1},
        requested_by=None, payload={"bid": book["id"], "seq": 0},
    )
    now = db.ts()
    db.execute(
        "UPDATE generation_jobs SET status='running', attempts=1, started_at=?, claimed_at=?, "
        "worker_instance_id=?, worker_version=?, worker_build_sha=?, worker_claim_token=? "
        "WHERE id=?",
        (now, now, "dead-worker", "old-version", "old-build", "old-token", job_id),
    )
    monkeypatch.setattr(db, "_process_is_alive", lambda _pid: False)

    orphaned = db.list_orphaned_analysis_jobs()
    assert [item["id"] for item in orphaned] == [job_id]
    assert analysis_svc.recover_orphaned_analysis_jobs() == [job_id]
    assert db.get_generation_job(job_id)["status"] == "failed"
    record = db.get_chapter_analysis(analysis_id)
    assert record["status"] == "failed"
    assert json.loads(record["error"])["code"] == "stale_job_claim"
    assert analysis_svc.parse_analysis_progress(record)["currentStage"] == "failed"


def test_live_worker_process_is_not_marked_orphaned(client, monkeypatch):
    author = new_admin("啟動活躍分析操作員")
    book = upload_book(author, text="第一章 活躍 worker。\n" + "內容。\n" * 20)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 4,
        "analysis_profile": "speaker", "status": "running",
        "progress_json": "{}",
    })
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=analysis_id,
        provider={"id": 1, "model": "test-model", "config_version": 1},
        requested_by=None, payload={"bid": book["id"], "seq": 0},
    )
    now = db.ts()
    db.execute(
        "INSERT INTO worker_instances(instance_id, worker_version, build_sha, process_id, db_path, "
        "started_at, heartbeat_at, active, worker_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
        ("live-worker", "current", "current-build", 12345, settings.DB_PATH,
         now, now, 1, 1),
    )
    db.execute(
        "UPDATE generation_jobs SET status='running', attempts=1, started_at=?, claimed_at=?, "
        "worker_instance_id=?, worker_version=?, worker_build_sha=?, worker_claim_token=? WHERE id=?",
        (now, now, "live-worker", "current", "current-build", "live-token", job_id),
    )
    monkeypatch.setattr(db, "_process_is_alive", lambda _pid: True)
    assert db.list_orphaned_analysis_jobs() == []


def test_startup_recovery_closes_lost_batch_worker_without_requeue(client, monkeypatch):
    admin = new_admin("批次孤兒管理員")
    _make_ai_provider(admin)
    author = new_admin("批次孤兒操作員")
    book = upload_book(
        author,
        text=(
            "第一章 批次孤兒。\n"
            + "第一章的測試正文，用來驗證批次主工作遺失後能安全收斂。\n" * 3
            + "\n第二章 後續章節。\n"
            + "第二章的測試正文，用來確認 active 子分析會被一併標記失敗。\n" * 3
        ),
    )
    row = db.get_book_row(book["id"])
    chapters = db.list_chapters(row["id"])
    created = author.post(f"/api/books/{book['id']}/analysis/batch")
    assert created.status_code == 200, created.text
    job_id = created.json()["jobId"]
    child_id = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=chapters[0]["id"],
        source_text_hash=chapters[0]["text_hash"], ai_provider_id=1,
        ai_model="test-model", ai_config_version=1, batch_job_id=job_id,
    )
    db.update_chapter_analysis(child_id, {"status": "running"})
    now = db.ts()
    db.execute(
        "INSERT INTO worker_instances(instance_id, worker_version, build_sha, process_id, db_path, "
        "started_at, heartbeat_at, active, worker_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
        ("dead-batch-worker", "old-version", "old-build", 54321, settings.DB_PATH,
         now, now, 1, 1),
    )
    db.execute(
        "UPDATE generation_jobs SET status='running', attempts=1, started_at=?, claimed_at=?, "
        "worker_instance_id=?, worker_version=?, worker_build_sha=?, worker_claim_token=? WHERE id=?",
        (now, now, "dead-batch-worker", "old-version", "old-build", "dead-batch-token", job_id),
    )
    monkeypatch.setattr(db, "_process_is_alive", lambda _pid: False)
    assert [item["id"] for item in db.list_orphaned_analysis_jobs()] == [job_id]
    assert analysis_svc.recover_orphaned_analysis_jobs() == [job_id]
    assert db.get_generation_job(job_id)["status"] == "failed"
    assert db.get_chapter_analysis(child_id)["status"] == "failed"
    assert json.loads(db.get_chapter_analysis(child_id)["error"])["code"] == "stale_job_claim"
    assert not db.claim_generation_job({
        "instance_id": "new-worker", "worker_version": "current", "build_sha": "current",
    })


def test_author_can_cancel_full_book_analysis_and_only_owned_active_child(client):
    admin = new_admin("批次取消管理員")
    _make_ai_provider(admin)
    author = new_admin("批次取消操作員")
    book = upload_book(
        author,
        text=(
            "第一章 批次取消。\n"
            + "第一章的測試正文，用來建立可供批次取消驗證的第一個章節。\n" * 3
            + "\n第二章 尚未執行。\n"
            + "第二章的測試正文，用來確認尚未建立的子分析不會被錯誤產生。\n" * 3
        ),
    )
    row = db.get_book_row(book["id"])
    chapters = db.list_chapters(row["id"])

    created = author.post(f"/api/books/{book['id']}/analysis/batch")
    assert created.status_code == 200, created.text
    batch_job_id = created.json()["jobId"]
    duplicate = author.post(f"/api/books/{book['id']}/analysis/batch")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "analysis_batch_in_progress"
    db.update_generation_job(batch_job_id, "running", 5)
    child_id = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=chapters[0]["id"],
        source_text_hash=chapters[0]["text_hash"], ai_provider_id=1,
        ai_model="test-model", ai_config_version=1,
        batch_job_id=batch_job_id,
    )
    db.update_chapter_analysis(child_id, {"status": "running"})
    unrelated_id = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=chapters[1]["id"],
        source_text_hash=chapters[1]["text_hash"], ai_provider_id=1,
        ai_model="test-model", ai_config_version=1,
    )

    response = author.post(f"/api/books/{book['id']}/analysis/batch/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["jobId"] == batch_job_id
    assert response.json()["cancelledAnalysisCount"] == 1
    assert db.get_generation_job(batch_job_id)["status"] == "cancelled"
    child = db.get_chapter_analysis(child_id)
    assert child["status"] == "failed"
    assert json.loads(child["error"])["code"] == "analysis_cancelled"
    assert db.get_chapter_analysis(unrelated_id)["status"] == "queued"

    # 取消只終止該批次；新的批次可建立新的主 job，不重用舊 job。
    retried = author.post(f"/api/books/{book['id']}/analysis/batch")
    assert retried.status_code == 200, retried.text
    assert retried.json()["jobId"] != batch_job_id


def test_cancelled_batch_executor_does_not_create_next_chapter_analysis(client, monkeypatch):
    admin = new_admin("批次取消邊界管理員")
    _make_ai_provider(admin)
    author = new_admin("批次取消邊界操作員")
    book = upload_book(
        author,
        text=(
            "第一章 批次取消邊界。\n"
            + "第一章的測試正文，用來驗證批次工作取消後不會呼叫分析器。\n" * 3
            + "\n第二章 尚未執行。\n"
            + "第二章的測試正文，用來驗證取消後不會建立下一章分析。\n" * 3
        ),
    )
    row = db.get_book_row(book["id"])
    response = author.post(f"/api/books/{book['id']}/analysis/batch")
    assert response.status_code == 200, response.text
    job_id = response.json()["jobId"]
    assert analysis_svc.cancel_analysis_batch_job(job_id)

    monkeypatch.setattr(
        "backend.analyzer.analyze_source_faithful",
        lambda *args, **kwargs: pytest.fail("批次取消後不得建立下一章或呼叫 AI"),
    )
    result = analysis_executor.run_speaker_analysis_all(db.get_generation_job(job_id))
    assert result["items"] == []
    assert not db.query("SELECT id FROM chapter_analyses WHERE book_id=?", (row["id"],))
