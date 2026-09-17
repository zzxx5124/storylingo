"""V4 Phase 5：Analyzer V4 jobs & analysis API regression tests.

驗證：
- Analyze request 建立持久化 job（speaker_analysis）。
- 以 job 綁定的 provider 執行，default 變更後 binding 仍穩定。
- 完成前文字變更 → 舊 analysis 不會成為 current。
- Ready analysis 可依章節取回。
- 無 default provider → 明確 unavailable，不影響 app 啟動。
- Role/ownership/CSRF API 測試。
"""
import json
import os

import pytest

from backend import analyzer, db, jobs, settings
from backend import v4_contracts as c
from backend.services import analysis as analysis_svc
from backend.services import analysis_executor
from backend.services import workflow
from backend.services.analysis_executor import ProviderUnavailableError
from backend.services import source_faithful
from backend.services import source_faithful_migration
from backend.services import voice as voice_svc
from backend import storage
from backend.tests.conftest import new_admin, new_author, upload_book, add_chapter


def _make_provider(admin, *, name="測試AI", is_default=True):
    r = admin.post("/api/admin/ai/providers", json={
        "name": name, "providerType": "openai_compatible",
        "baseUrl": "https://ai.example.com/v1", "model": "m-test",
        "fallbackModel": "m-fallback", "apiKey": "sk-x", "isDefault": is_default,
    })
    assert r.status_code == 200, r.text
    return r.json()


def _book_with_chapters(a, n=2):
    book = upload_book(a)
    for i in range(2, n + 1):
        add_chapter(a, book["id"], title=f"第{i}章",
                    text="正文內容描述著日常與敘述，用了長句和多個標點。\n" * 6)
    return book


def _mock_llm(monkeypatch, data=None):
    data = data or {"speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}],
                    "segments": [{"type": "narration", "speaker": "旁白", "text": "正文內容描述著日常與敘述，用了長句和多個標點。"}]}
    monkeypatch.setattr("backend.analyzer._call_llm", lambda *a, **k: data)
    # Current author-path tests use the v4 writer. Keep the legacy payload
    # fixture for v3 tests, but provide a source-owned v4 fixture explicitly so
    # tests do not accidentally fall back to the old text-returning path.
    dialogue_names = [
        str(item.get("speaker")) for item in data.get("segments", [])
        if item.get("type") == "dialogue" and item.get("speaker")
    ]
    dialogue_name = dialogue_names[0] if dialogue_names else None

    def source_faithful_fixture(text, **kwargs):
        segmentation = source_faithful.segment_source(text)
        annotations = []
        for span in segmentation["segments"]:
            if span["type"] == "dialogue" and dialogue_name:
                annotations.append({
                    "segment_id": span["segment_id"], "type": "dialogue",
                    "speaker_id": "char_mock_speaker",
                    "speaker_surface": dialogue_name,
                })
            else:
                annotations.append({
                    "segment_id": span["segment_id"], "type": span["type"],
                    "speaker_id": "speaker:narrator",
                })
        artifact = source_faithful.apply_annotations(segmentation, annotations, source=text)
        artifact["metrics"] = {
            "totalChunks": len(source_faithful.group_segments(segmentation, max_chars=analyzer.MAX_CHUNK)),
            "completedChunks": len(source_faithful.group_segments(segmentation, max_chars=analyzer.MAX_CHUNK)),
            "providerRequestCount": 0, "requestedChunks": 0, "reusedChunks": 0,
            "chunkRetryCount": 0, "savedProviderRequests": 0,
        }
        return artifact

    monkeypatch.setattr("backend.analyzer.analyze_source_faithful", source_faithful_fixture)
    # 避免分段並行依賴執行緒
    monkeypatch.setattr("backend.analyzer.MAX_CHUNK", 10 ** 9)


def _zh_mock_data():
    return {
        "speakers": [
            {"name": "旁白", "gender": "男", "age": "中年"},
            {"name": "主角", "gender": "女", "age": "青年"},
        ],
        "segments": [
            {"type": "narration", "speaker": "旁白", "text": "正文內容描述著日常與敘述，用了長句和多個標點。", "emotion": None},
            {"type": "dialogue", "speaker": "主角", "text": "正文內容描述著日常與敘述，用了長句和多個標點。", "emotion": None},
        ],
    }


def _force_legacy_v3(analysis_id):
    """Mark a fixture explicitly legacy; production author routes are v4."""
    db.update_chapter_analysis(analysis_id, {"schema_version": 3})


def test_schema_v4_executor_persists_source_owned_artifact_before_projection(client, monkeypatch):
    admin = new_admin("v4sourceowner")
    _make_provider(admin)
    author = new_author("v4sourceauthor")
    book = _book_with_chapters(author, n=1)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    segmentation = source_faithful.segment_source(chapter["text"])
    annotations = [{"segment_id": span["segment_id"], "type": span["type"],
                    "speaker_id": "speaker:narrator" if span["type"] == "narration" else "speaker:unresolved"}
                   for span in segmentation["segments"]]
    artifact = source_faithful.apply_annotations(segmentation, annotations, source=chapter["text"])
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 4,
        "analysis_profile": "source-faithful", "status": "queued",
        "prompt_version": "structured-annotation-v1", "ai_provider_id": 1,
        "ai_model": "m-test", "ai_config_version": 1,
    })
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=analysis_id, provider={"id": 1, "model": "m-test", "config_version": 1},
        requested_by=None, payload={"bid": book["id"], "seq": 0},
    )
    monkeypatch.setattr(analysis_executor.analyzer, "analyze_source_faithful", lambda *args, **kwargs: artifact)
    result = analysis_executor.run_speaker_analysis(db.get_generation_job(job_id))
    assert result["status"] == "ready"
    record = db.get_chapter_analysis(analysis_id)
    assert record["status"] == "ready"
    assert record["schema_version"] == 4
    assert json.loads(record["progress_json"])["currentStage"] == "ready"
    with open(os.path.join(settings.ROOT_DIR, record["artifact_path"]), encoding="utf-8") as handle:
        saved = json.load(handle)
    assert saved["schemaVersion"] == 4
    assert saved["segments"][0]["text"] == chapter["text"][
        saved["segments"][0]["source_start"]:saved["segments"][0]["source_end"]
    ]


def test_analysis_job_binds_existing_analysis_atomically(client):
    admin = new_admin("atomicanalysisowner")
    _make_provider(admin)
    author = new_author("atomicanalysisauthor")
    book = _book_with_chapters(author, n=1)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 4,
        "analysis_profile": "source-faithful", "status": "queued",
    })

    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=analysis_id, provider={"id": 1, "model": "m-test", "config_version": 1},
        requested_by=None, payload={"bid": book["id"], "seq": 0},
    )

    assert db.get_generation_job(job_id)["analysis_id"] == analysis_id


def test_v3_shadow_rollout_promote_and_rollback_is_non_destructive(client):
    author = new_author("v3shadowauthor")
    book = _book_with_chapters(author, n=1)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 3,
        "analysis_profile": "speaker", "status": "ready", "prompt_version": "legacy",
    })
    old_path = storage.analysis_artifact_path(book["id"], analysis_id)
    absolute = os.path.join(settings.ROOT_DIR, old_path)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    with open(absolute, "w", encoding="utf-8") as handle:
        json.dump({"schemaVersion": 3, "segments": []}, handle)
    db.mark_analysis_ready(analysis_id, old_path)
    report = source_faithful_migration.dry_run_chapter(book["id"], 0)
    assert report["wouldWrite"] is False
    assert db.get_chapter_analysis(analysis_id)["artifact_path"] == old_path

    segmentation = source_faithful.segment_source(chapter["text"])
    artifact = source_faithful.apply_annotations(segmentation, [
        {"segment_id": item["segment_id"], "type": item["type"],
         "speaker_id": "speaker:narrator" if item["type"] == "narration" else "speaker:unresolved"}
        for item in segmentation["segments"]
    ], source=chapter["text"])
    shadow = source_faithful_migration.write_shadow_artifact(analysis_id, artifact, chapter["text"])
    assert db.get_chapter_analysis(analysis_id)["artifact_path"] == old_path
    source_faithful_migration.promote_shadow_artifact(analysis_id, shadow, chapter["text"])
    assert db.get_chapter_analysis(analysis_id)["schema_version"] == 4
    source_faithful_migration.rollback_to_v3(analysis_id, old_path)
    restored = db.get_chapter_analysis(analysis_id)
    assert restored["schema_version"] == 3
    assert restored["artifact_path"] == old_path


def test_analyze_request_enqueues_persisted_job(client):
    admin = new_admin("AI管理A")
    _make_provider(admin)
    a = new_admin("作者A分析操作員")
    book = _book_with_chapters(a)
    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "queued"
    assert data["jobId"] > 0
    job = db.get_generation_job(data["jobId"])
    assert job["job_type"] == c.JOB_TYPE_SPEAKER_ANALYSIS
    assert job["ai_provider_id"] is not None
    assert job["ai_model"] == "m-test"
    analysis = db.get_chapter_analysis(data["analysisId"])
    assert analysis["schema_version"] == 4
    # persisted
    jobs_all = db.list_generation_jobs(100)
    assert any(j["id"] == data["jobId"] for j in jobs_all)


def test_terminal_failed_job_converges_stale_running_analysis(client):
    a = new_admin("分析收斂作者")
    book = _book_with_chapters(a, n=1)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 3,
        "status": "running",
    })
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=analysis_id, provider={"id": 1, "model": "test", "config_version": 1},
        requested_by=None,
    )
    # 使用正式 job claim/update path 建立 attempts=3 的 terminal failed fixture。
    for attempt in range(3):
        claimed = db.claim_generation_job()
        assert claimed["id"] == job_id
        db.update_generation_job(job_id, "failed", 100,
                                 '{"code":"malformed_json","message":"raw provider detail"}')
        if attempt < 2:
            db.requeue_generation_job(job_id)

    assert analysis_svc.converge_terminal_analysis(analysis_id) is True
    assert analysis_svc.converge_terminal_analysis(analysis_id) is False
    analysis = db.get_chapter_analysis(analysis_id)
    assert analysis["status"] == "failed"
    assert json.loads(analysis["progress_json"])["currentStage"] == "failed"
    failure = json.loads(analysis["error"])
    assert failure["code"] == "analysis_retry_exhausted"
    assert failure["message"] == "AI 回應格式無效"
    assert "raw provider detail" not in analysis["error"]


def test_orphan_exhausted_running_job_repairs_without_ai_or_requeue(client, monkeypatch):
    admin = new_admin("孤兒工作修復作者")
    _make_provider(admin)
    db.create_tts_provider({
        "name": "孤兒修復測試TTS", "provider_type": "generic_http",
        "base_url": "https://tts.example.com", "enabled": True, "is_default": True,
    })
    book = upload_book(admin, text="第一章 孤兒工作修復。\n" + "正文內容。\n" * 8)
    db.update_book(book["id"], {"audio_mode": "multi"})
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 3,
        "status": "running", "progress_json": "{}",
    })
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=analysis_id, provider={"id": 1, "model": "test", "config_version": 1},
        requested_by=None,
    )
    for _ in range(3):
        claimed = db.claim_generation_job()
        assert claimed["id"] == job_id
        if claimed["attempts"] < 3:
            db.requeue_generation_job(job_id)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("orphan repair must not call AI")

    monkeypatch.setattr(analysis_executor.analyzer, "analyze_chapter", fail_if_called)
    assert analysis_svc.repair_orphan_analysis_job(job_id) is True
    assert analysis_svc.repair_orphan_analysis_job(job_id) is False

    job = db.get_generation_job(job_id)
    analysis = db.get_chapter_analysis(analysis_id)
    assert job["status"] == "failed"
    assert job["attempts"] == 3
    assert analysis["status"] == "failed"
    progress = json.loads(analysis["progress_json"])
    assert progress["currentStage"] == "failed"
    assert progress["runningChunks"] == 0
    assert progress["lastError"] == "分析工作已中斷，請重新分析"
    failure = json.loads(analysis["error"])
    assert failure["code"] == "stale_job_claim"
    assert failure["failureType"] == "worker_lost"
    current = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    chapter_state = current["chapters"][0]
    assert chapter_state["state"] == c.WORKFLOW_ANALYSIS_FAILED
    assert chapter_state["nextAction"] == "analyze"


def test_running_legacy_empty_progress_is_marked_recovering_not_normal_queued():
    progress = analysis_svc.parse_analysis_progress({"progress_json": "{}"})
    assert progress["legacyProgress"] is True
    assert progress["currentStage"] == "queued"


def test_no_default_provider_returns_clear_unavailable():
    a = new_admin("作者B分析操作員")
    book = _book_with_chapters(a)
    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert r.status_code == 503, r.text
    # app 仍可啟動/健康
    # 且 executor 拋出明確錯誤
    with pytest.raises(ProviderUnavailableError):
        analysis_executor.resolve_provider({"ai_provider_id": None})


def test_job_uses_bound_provider_snapshot_stable_after_default_change(client):
    admin = new_admin("AI管理B")
    p1 = _make_provider(admin, name="預設P1", is_default=True)
    a = new_admin("作者C分析操作員")
    book = _book_with_chapters(a)
    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    data = r.json()
    job = db.get_generation_job(data["jobId"])
    assert job["ai_provider_id"] == p1["id"]
    # 建立新的 default，再執行 job → 仍用 p1
    _make_provider(admin, name="新預設P2", is_default=True)
    assert db.get_default_ai_provider()["id"] != p1["id"]
    # executor 依 job 綁定的 provider 執行（resolve_provider 優先 ai_provider_id）
    resolved = analysis_executor.resolve_provider(job)
    assert resolved["id"] == p1["id"]


def test_ready_analysis_retrievable_by_chapter(client, monkeypatch):
    admin = new_admin("AI管理C")
    _make_provider(admin)
    a = new_admin("作者D分析操作員")
    book = _book_with_chapters(a)
    _mock_llm(monkeypatch)

    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    data = r.json()
    job = db.get_generation_job(data["jobId"])
    analysis_executor.run_speaker_analysis(job)

    got = a.get(f"/api/books/{book['id']}/chapters/0/analysis").json()
    assert got["status"] == "ready"
    assert got["schemaVersion"] == 4
    assert got["segments"][0]["text"] == "正文內容描述著日常與敘述，用了長句和多個標點。"
    assert got["segments"][0].get("emotion") is None
    assert got["progress"]["currentStage"] == "ready"
    assert got["progress"]["progressPercent"] == 100
    assert got["progress"]["completedChunks"] == got["progress"]["totalChunks"]
    # current_analysis_id pointer 已更新
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    assert ch["current_analysis_id"] == got["analysisId"]


def test_analysis_progress_persists_actual_parallel_chunk_state(client, monkeypatch):
    admin = new_admin("AI進度測試")
    _make_provider(admin)
    a = new_admin("進度分析操作員")
    book = _book_with_chapters(a)
    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    aid = r.json()["analysisId"]
    _force_legacy_v3(aid)
    observed = []

    def fake_analyze(book_data, seq, text, on_progress=None, partial_cache=None):
        on_progress({"stage": "analyzing", "total_chunks": 2,
                     "completed_chunks": 1, "running_chunks": 1, "retry_count": 1})
        row = db.get_chapter_analysis(aid)
        observed.append(json.loads(row["progress_json"]))
        on_progress({"stage": "analyzing", "total_chunks": 2,
                     "completed_chunks": 2, "running_chunks": 0, "retry_count": 1})
        return {"speakers": [{"name": "旁白"}],
                "segments": [{"type": "narration", "speaker": "旁白", "text": text}]}

    monkeypatch.setattr(analysis_executor.analyzer, "analyze_chapter", fake_analyze)
    analysis_executor.run_speaker_analysis(db.get_generation_job(r.json()["jobId"]))

    assert observed[0]["totalChunks"] == 2
    assert observed[0]["completedChunks"] == 1
    assert observed[0]["runningChunks"] == 1
    assert observed[0]["retryCount"] == 1
    assert observed[0]["progressPercent"] == 50
    got = a.get(f"/api/books/{book['id']}/chapters/0/analysis").json()
    assert got["progress"]["currentStage"] == "ready"
    assert got["progress"]["completedChunks"] == 2
    assert got["progress"]["retryCount"] == 1


def test_analysis_persistence_order_is_ready_before_roster(client, monkeypatch):
    admin = new_admin("AI管理Order")
    _make_provider(admin)
    a = new_admin("作者Order分析操作員")
    book = _book_with_chapters(a)
    _mock_llm(monkeypatch)
    events = []
    original_save = analysis_executor.analysis_svc.save_ready_analysis
    original_derive = analysis_executor.analysis_svc.derive_roster_from_ready_artifact

    def save(*args, **kwargs):
        events.append("save_ready")
        return original_save(*args, **kwargs)

    def derive(*args, **kwargs):
        events.append("derive_roster")
        row = db.get_book_row(args[0])
        chapter = db.get_chapter(row["id"], args[1])
        assert db.get_ready_chapter_analysis(chapter["id"], chapter["text_hash"])
        return original_derive(*args, **kwargs)

    monkeypatch.setattr(analysis_executor.analysis_svc, "save_ready_analysis", save)
    monkeypatch.setattr(analysis_executor.analysis_svc, "derive_roster_from_ready_artifact", derive)
    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    _force_legacy_v3(r.json()["analysisId"])
    analysis_executor.run_speaker_analysis(db.get_generation_job(r.json()["jobId"]))
    assert events == ["save_ready", "derive_roster"]


def test_ready_roster_projection_retry_does_not_call_ai_again(client, monkeypatch):
    admin = new_admin("AI管理Retry")
    _make_provider(admin)
    a = new_admin("作者Retry分析操作員")
    book = _book_with_chapters(a)
    calls = {"ai": 0, "derive": 0}
    _mock_llm(monkeypatch)
    original_call = analysis_executor.analyzer._call_llm
    def counted_call(*args, **kwargs):
        calls["ai"] += 1
        return original_call(*args, **kwargs)
    monkeypatch.setattr(analysis_executor.analyzer, "_call_llm", counted_call)
    original_derive = analysis_executor.analysis_svc.derive_roster_from_ready_artifact
    def fail_once(*args, **kwargs):
        calls["derive"] += 1
        if calls["derive"] == 1:
            raise RuntimeError("injected roster write failure")
        return original_derive(*args, **kwargs)
    monkeypatch.setattr(analysis_executor.analysis_svc, "derive_roster_from_ready_artifact", fail_once)

    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    aid = r.json()["analysisId"]
    _force_legacy_v3(aid)
    analysis_executor.run_speaker_analysis(db.get_generation_job(r.json()["jobId"]))
    assert calls == {"ai": 1, "derive": 1}
    assert db.get_chapter_analysis(aid)["status"] == "ready"
    assert "roster projection pending" in db.get_chapter_analysis(aid)["error"]
    analysis_executor.analysis_svc.retry_roster_projection(aid)
    assert calls == {"ai": 1, "derive": 2}
    assert db.get_chapter_analysis(aid)["error"] == ""
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    assert chapter["current_analysis_id"] == aid


def test_text_change_before_completion_cannot_make_stale_analysis_current(client, monkeypatch):
    admin = new_admin("AI管理D")
    _make_provider(admin)
    a = new_admin("作者E分析操作員")
    book = _book_with_chapters(a)
    _mock_llm(monkeypatch)

    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    data = r.json()
    _force_legacy_v3(data["analysisId"])
    job = db.get_generation_job(data["jobId"])
    # 模擬 provider 回應期間文字被變更；executor 仍持有原始 source snapshot。
    original_call = analysis_executor.analyzer._call_llm
    def mutate_source(*args, **kwargs):
        a.put(f"/api/books/{book['id']}/chapters/0", json={"text": "文字被改掉了，內容不同。" * 10})
        return original_call(*args, **kwargs)
    monkeypatch.setattr(analysis_executor.analyzer, "_call_llm", mutate_source)
    analysis_executor.run_speaker_analysis(job)

    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    record = db.get_chapter_analysis(data["analysisId"])
    # source hash 不符 → 不保存 ready artifact、不更新 current_analysis_id
    assert record["status"] == "failed"
    assert not record["artifact_path"]
    assert ch["current_analysis_id"] is None
    # 依新 hash 取回 → 無 ready analysis
    got = a.get(f"/api/books/{book['id']}/chapters/0/analysis").json()
    assert got["status"] == "none"


def test_analysis_endpoints_require_ownership_and_role(client):
    admin = new_admin("AI管理E")
    _make_provider(admin)
    a = new_author("作者F")
    book = _book_with_chapters(a)
    stranger = new_author("陌生人X")
    # 非擁有者不可建立/讀取
    assert stranger.post(f"/api/books/{book['id']}/chapters/0/analysis").status_code in (403, 404)
    assert stranger.get(f"/api/books/{book['id']}/chapters/0/analysis").status_code in (403, 404)
    assert stranger.post(f"/api/books/{book['id']}/analysis/batch").status_code in (403, 404)
    # reader（非 author）不可存取
    from backend.tests.conftest import new_user
    reader = new_user("讀者R")
    assert reader.post(f"/api/books/{book['id']}/chapters/0/analysis").status_code in (401, 403)


def test_analysis_batch_job(client):
    admin = new_admin("AI管理F")
    _make_provider(admin)
    a = new_admin("作者G分析操作員")
    book = _book_with_chapters(a)
    r = a.post(f"/api/books/{book['id']}/analysis/batch")
    assert r.status_code == 200, r.text
    job = db.get_generation_job(r.json()["jobId"])
    assert job["job_type"] == c.JOB_TYPE_SPEAKER_ANALYSIS_ALL


def test_analysis_all_executor_processes_all_chapters(client, monkeypatch):
    admin = new_admin("AI管理G")
    _make_provider(admin)
    a = new_admin("作者H分析操作員")
    book = _book_with_chapters(a, n=3)
    _mock_llm(monkeypatch)
    job = {
        "job_type": "speaker_analysis_all",
        "payload": '{"bid": "' + book["id"] + '"}',
        "requested_by": a.id if hasattr(a, "id") else None,
    }
    result = analysis_executor.run_speaker_analysis_all(job)
    assert len(result["items"]) == 3
    assert all(item["status"] == "ready" for item in result["items"])


def test_analysis_persists_book_speaker_roster(client, monkeypatch):
    """單章分析後必須把書級名冊（voices 空名冊／speaker_info／speaker_chapters）寫回
    books 列；跨章再分析要累積，不能覆寫成只剩最後一章。"""
    admin = new_admin("AI管理I")
    _make_provider(admin)
    a = new_admin("作者J分析操作員")
    book = _book_with_chapters(a, n=2)
    _mock_llm(monkeypatch, _zh_mock_data())

    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    _force_legacy_v3(r.json()["analysisId"])
    job = db.get_generation_job(r.json()["jobId"])
    analysis_executor.run_speaker_analysis(job)

    row = db.get_book_row(book["id"])
    spk = json.loads(row["speaker_info"] or "{}")
    voices = json.loads(row["voices"] or "{}")
    spk_ch = json.loads(row["speaker_chapters"] or "{}")
    assert set(spk.keys()) >= {"旁白", "主角"}
    assert voices.get("旁白") == "" and voices.get("主角") == ""
    assert spk["旁白"]["count"] == 1 and spk["主角"]["count"] == 1
    assert spk_ch.get("0", {}).get("主角") == 1

    # 再分析第 2 章 → 名冊累積（不是覆寫）
    r2 = a.post(f"/api/books/{book['id']}/chapters/1/analysis")
    _force_legacy_v3(r2.json()["analysisId"])
    job2 = db.get_generation_job(r2.json()["jobId"])
    analysis_executor.run_speaker_analysis(job2)
    row = db.get_book_row(book["id"])
    spk_ch = json.loads(row["speaker_chapters"] or "{}")
    assert set(spk_ch.keys()) == {"0", "1"}
    assert json.loads(row["speaker_info"] or "{}")["旁白"]["count"] == 2
    assert json.loads(row["speaker_info"] or "{}")["主角"]["count"] == 2


def test_reanalysis_replaces_stale_same_chapter_roster_entries(client):
    """同章新 ready artifact 不應保留已被取代的相容角色鍵。"""
    author = new_admin("roster_reanalysis_cleanup")
    book = _book_with_chapters(author, n=1)
    db.update_book(book["id"], {
        "voices": json.dumps({"_english": "", "舊角色": "voice-old"}, ensure_ascii=False),
        "speaker_info": json.dumps({"舊角色": {"count": 1, "firstCh": 0}}, ensure_ascii=False),
        "speaker_chapters": json.dumps({"0": {"舊角色": 1}}, ensure_ascii=False),
    })
    source = db.get_chapter(db.get_book_row(book["id"])["id"], 0)["text"]
    artifact = {
        "schemaVersion": 3,
        "chapterKey": "ch0",
        "sourceTextHash": "test-source-hash",
        "sourceLength": len(source),
        "segments": [{
            "segment_id": "seg-1", "text": source[:1], "type": "dialogue",
            "speaker_id": "char_new", "source_start": 0, "source_end": 1,
            "emotion": {"label": "neutral", "intensity": 0.5},
            "attribution": {"method": "explicit", "confidence": 1.0},
        }],
        "characters": [{"character_id": "char_new", "canonical_name": "新角色"}],
    }
    result = analysis_svc.derive_roster_from_ready_artifact(book["id"], 0, artifact)
    assert set(result["voices"]) == {"_english", "新角色"}
    assert set(result["speakerInfo"]) == {"新角色"}


def test_analysis_all_persists_accumulated_roster(client, monkeypatch):
    """批次分析全書：逐章以最新書列累積名冊，最後 speaker_info 含全書角色與總次數。"""
    admin = new_admin("AI管理K")
    _make_provider(admin)
    a = new_admin("作者L分析操作員")
    book = _book_with_chapters(a, n=2)
    _mock_llm(monkeypatch, _zh_mock_data())
    job = {"job_type": "speaker_analysis_all", "payload": json.dumps({"bid": book["id"], "schemaVersion": 3}),
           "requested_by": None}
    result = analysis_executor.run_speaker_analysis_all(job)
    assert all(item["status"] == "ready" for item in result["items"])
    row = db.get_book_row(book["id"])
    spk_ch = json.loads(row["speaker_chapters"] or "{}")
    assert set(spk_ch.keys()) == {"0", "1"}
    spk = json.loads(row["speaker_info"] or "{}")
    assert spk["旁白"]["count"] == 2 and spk["主角"]["count"] == 2


def test_ai_match_after_executor_analysis_assigns_voices(client, monkeypatch):
    """黑箱殘留 P1：章節分析完成後，/voices/ai-match 必須看到未指定角色並自動指派
    （不得回「所有角色皆已指定語者」假成功）。"""
    pid = db.create_tts_provider({
        "name": "名冊TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(pid, [
        {"id": "v-zh-1", "name": "女聲", "lang": "zh", "gender": "female"},
        {"id": "v-zh-2", "name": "男聲", "lang": "zh", "gender": "male"},
    ])
    admin = new_admin("AI管理M")
    _make_provider(admin)
    a = new_admin("作者N分析操作員")
    book = _book_with_chapters(a)
    _mock_llm(monkeypatch, _zh_mock_data())
    class FakeCompletions:
        def create(self, **kwargs):
            class Message:
                content = '{"matches":[{"speaker":"旁白","voice_id":"v-zh-2","reason":"旁白使用男性聲線"},{"speaker":"主角","voice_id":"v-zh-1","reason":"主角使用女性聲線"}]}'
            class Choice:
                message = Message()
            class Response:
                choices = [Choice()]
            return Response()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(voice_svc, "_openai_client", lambda provider: FakeClient())
    r = a.post(f"/api/books/{book['id']}/chapters/0/analysis")
    _force_legacy_v3(r.json()["analysisId"])
    job = db.get_generation_job(r.json()["jobId"])
    analysis_executor.run_speaker_analysis(job)

    res = a.post(f"/api/books/{book['id']}/voices/ai-match")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total"] == 2, data
    assert data["assigned"] == 2, data
    assert "所有角色皆已指定語者" not in data.get("message", "")
    voices = json.loads(db.get_book_row(book["id"])["voices"] or "{}")
    assert voices["旁白"] and voices["主角"]


def test_provider_unavailable_does_not_break_health(client):
    # 不建立 provider
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_duplicate_analysis_request_reuses_active_analysis_job(client):
    admin = new_admin("AI重複分析防呆")
    _make_provider(admin)
    book = upload_book(
        admin,
        text="第一章 重複請求測試。\n" + "這是一段足夠長的測試內容，用來確認同一章的分析請求不會重複建立工作。\n" * 8,
    )
    db.update_book(book["id"], {"audio_mode": "multi"})

    first = admin.post(f"/api/books/{book['id']}/chapters/0/analysis")
    second = admin.post(f"/api/books/{book['id']}/chapters/0/analysis")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["deduplicated"] is True
    assert second.json()["analysisId"] == first.json()["analysisId"]
    assert second.json()["jobId"] == first.json()["jobId"]
    jobs_for_analysis = [job for job in db.list_generation_jobs(500)
                         if job.get("analysis_id") == first.json()["analysisId"]
                         and job.get("job_type") == "speaker_analysis"]
    assert len(jobs_for_analysis) == 1


def test_force_reanalysis_bypasses_ready_analysis_without_replacing_it(client):
    admin = new_admin("AI強制重新分析")
    _make_provider(admin)
    author = new_admin("強制重分析操作員")
    book = upload_book(author, text="第一章 重新分析測試。\n" + "既有 ready analysis 必須保留，force 才建立新分析。\n" * 8)
    db.update_book(book["id"], {"audio_mode": "multi"})

    first = author.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert first.status_code == 200, first.text
    old_analysis_id = first.json()["analysisId"]
    old_job_id = first.json()["jobId"]
    db.mark_analysis_ready(old_analysis_id, "analysis/old-ready.json")

    normal = author.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert normal.status_code == 200, normal.text
    assert normal.json()["analysisId"] == old_analysis_id
    assert "jobId" not in normal.json()

    forced = author.post(f"/api/books/{book['id']}/chapters/0/analysis", json={"force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["analysisId"] != old_analysis_id
    assert forced.json()["jobId"] != old_job_id
    assert db.get_chapter_analysis(old_analysis_id)["status"] == "ready"
    assert db.get_generation_job(old_job_id)["status"] == "pending"


def test_force_reanalysis_still_deduplicates_active_job(client):
    admin = new_admin("AI強制去重")
    _make_provider(admin)
    author = new_admin("強制去重操作員")
    book = upload_book(author, text="第一章 強制去重測試。\n" + "分析進行中時不能建立第二個 job。\n" * 8)
    db.update_book(book["id"], {"audio_mode": "multi"})

    first = author.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert first.status_code == 200, first.text
    forced = author.post(f"/api/books/{book['id']}/chapters/0/analysis", json={"force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["deduplicated"] is True
    assert forced.json()["analysisId"] == first.json()["analysisId"]
    assert forced.json()["jobId"] == first.json()["jobId"]


def test_analysis_mutation_blocked_without_csrf_in_production(client):
    from fastapi.testclient import TestClient
    from backend.main import app as built_app
    admin = new_admin("AI管理H")
    _make_provider(admin)
    a = new_author("作者I")
    book = _book_with_chapters(a)
    # 取已登入 cookie
    login = TestClient(built_app)
    login.post("/api/auth/login", json={"username": a.username, "password": "secret123"})
    orig_env = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        r = login.post(f"/api/books/{book['id']}/chapters/0/analysis")
        assert r.status_code == 403, r.text
        assert "CSRF" in r.text
    finally:
        settings.APP_ENV = orig_env


def test_retry_exhausted_marks_analysis_failed_and_workflow_recoverable(client, monkeypatch):
    admin = new_admin("AI分析失敗收斂")
    _make_provider(admin)
    db.create_tts_provider({
        "name": "分析失敗測試TTS", "provider_type": "generic_http",
        "base_url": "https://tts.example.com", "enabled": True, "is_default": True,
    })
    book = upload_book(admin, text="第一章 失敗測試。\n" + "正文內容描述著日常與敘述，用了長句和多個標點。\n" * 8)
    db.update_book(book["id"], {"audio_mode": "multi"})
    request = admin.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert request.status_code == 200
    payload = request.json()

    def malformed(*args, **kwargs):
        raise RuntimeError("AI 服務無法完成分析：Expecting ',' delimiter")

    monkeypatch.setattr(analysis_executor.analyzer, "analyze_source_faithful", malformed)
    for _ in range(3):
        assert jobs.run_pending_once() is True

    record = db.get_chapter_analysis(payload["analysisId"])
    assert record["status"] == "failed"
    failure = analysis_executor.analysis_svc.parse_failure_error(record["error"])
    assert failure["code"] == "malformed_json"
    assert failure["attempts"] == 3
    assert db.get_generation_job(payload["jobId"])["status"] == "failed"
    current = admin.get(f"/api/books/{book['id']}").json()
    chapter_state = current["workflow"]["chapters"]["0"]
    assert chapter_state["state"] == "analysis_failed"
    assert chapter_state["error"] == "AI 回應格式無效"


def test_unsupported_parameter_fails_once_without_job_level_retry(client, monkeypatch):
    admin = new_admin("AI參數不相容")
    _make_provider(admin)
    book = upload_book(admin, text="第一章 參數測試。\n" + "正文內容用於驗證 AI 請求參數與失敗收斂。\n" * 8)
    request = admin.post(f"/api/books/{book['id']}/chapters/0/analysis")
    assert request.status_code == 200
    payload = request.json()

    class UnsupportedParameterError(RuntimeError):
        code = "unsupported_parameter"

    def fail(*args, **kwargs):
        raise UnsupportedParameterError("unsupported parameter: max_tokens")

    monkeypatch.setattr(analysis_executor.analyzer, "analyze_source_faithful", fail)
    assert jobs.run_pending_once() is True

    record = db.get_chapter_analysis(payload["analysisId"])
    job = db.get_generation_job(payload["jobId"])
    failure = analysis_executor.analysis_svc.parse_failure_error(record["error"])
    assert job["status"] == "failed"
    assert job["attempts"] == 1
    assert failure["code"] == "unsupported_parameter"
    assert failure["retryable"] is False
