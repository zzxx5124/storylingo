"""V4 Phase 8：Generation pipeline regression tests.

驗證：
- single 模式不需 AI provider 即可建立 generation。
- multi 模式需要 matching ready analysis。
- 成功 generation atomic 啟動（active pointer）。
- duplicate call 重用 ready/pending generation。
- provider 失敗不會取代現有 active generation。
- batch 支援 partial success。
"""
import json
import os

import pytest

from backend import db, settings, storage
from backend import v4_contracts as c
from backend.services import generation_pipeline as gp
from backend.services import audio_generation as ag
from backend.services.analysis_executor import ProviderUnavailableError
from backend.tests.conftest import new_admin, new_author, upload_book, add_chapter


def _make_tts_provider():
    if not db.get_active_tts_provider():
        provider_id = db.create_tts_provider({
            "name": "測試TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
            "enabled": True, "is_default": True,
        })
        db.replace_tts_provider_voices(provider_id, [
            {"id": "v-zh-1", "name": "測試女聲", "lang": "zh", "gender": "女"},
            {"id": "v-zh-2", "name": "測試男聲", "lang": "zh", "gender": "男"},
        ])
    return dict(db.get_active_tts_provider())


def _book(a, n=2):
    book = upload_book(a)
    for i in range(2, n + 1):
        add_chapter(a, book["id"], title=f"第{i}章", text=f"第 {i} 章正文內容。" * 6)
    return book


def _fake_synth(monkeypatch, tmp_path):
    """mock tts.generate_chapter_audio：寫出有效 audio/timing 檔。"""
    def _synth(book, chapter, analysis, on_progress=None, final_audio_path=None, final_timing_path=None):
        os.makedirs(os.path.dirname(final_audio_path), exist_ok=True)
        with open(final_audio_path, "wb") as f:
            f.write(b"ID3fakeaudio")
        with open(final_timing_path, "w", encoding="utf-8") as f:
            json.dump({"segments": [{"dur": 1.0}]}, f)
        return final_audio_path
    monkeypatch.setattr("backend.tts.generate_chapter_audio", _synth)


def test_single_generation_works_without_ai_provider(client, monkeypatch, tmp_path):
    _make_tts_provider()
    a = new_author("作者A")
    book = _book(a)
    _fake_synth(monkeypatch, tmp_path)
    job = {"job_type": "audio_single", "requested_by": None,
           "payload": json.dumps({"bid": book["id"], "seq": 0})}
    gen = gp.run_single_generation(job)
    assert gen["status"] == "ready"
    assert gen["mode"] == "single"
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    assert ch["active_audio_generation_id"] == gen["id"]
    assert os.path.exists(os.path.join(settings.ROOT_DIR, gen["audio_path"]))


def test_multi_generation_requires_matching_analysis(client, monkeypatch, tmp_path):
    _make_tts_provider()
    a = new_author("作者B")
    book = _book(a)
    _fake_synth(monkeypatch, tmp_path)
    row = db.get_book_row(book["id"])
    db.update_book(book["id"], {"audio_mode": "multi"})
    job = {"job_type": "audio_multi", "requested_by": None,
           "payload": json.dumps({"bid": book["id"], "seq": 0})}
    # 無 ready analysis → 明確失敗
    with pytest.raises(Exception):
        gp.run_multi_generation(job)
    # 建立 ready analysis
    ch = db.get_chapter(row["id"], 0)
    aid = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": ch["id"], "source_text_hash": ch["text_hash"],
        "analysis_type": "speaker", "schema_version": 2, "analysis_profile": "speaker",
        "status": "ready", "prompt_version": "v1", "created_at": db.ts(),
    })
    artifact = {
        "schemaVersion": 2, "chapterKey": ch["chapter_key"], "sourceTextHash": ch["text_hash"],
        "speakers": [{"name": "旁白"}],
        "segments": [{"id": "s0", "text": "旁白文字。", "speaker": "旁白",
                      "emotion": {"value": "neutral", "intensity": 0.0, "source": "fallback"}}],
    }
    rel = storage.analysis_artifact_path(book["id"], aid)
    abs_p = os.path.join(settings.ROOT_DIR, rel)
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    with open(abs_p, "w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False)
    db.mark_analysis_ready(aid, rel)
    gen = gp.run_multi_generation(job)
    assert gen["status"] == "ready"


def test_duplicate_call_reuses_ready_generation(client, monkeypatch, tmp_path):
    _make_tts_provider()
    a = new_author("作者C")
    book = _book(a)
    _fake_synth(monkeypatch, tmp_path)
    job = {"job_type": "audio_single", "requested_by": None,
           "payload": json.dumps({"bid": book["id"], "seq": 0})}
    gen1 = gp.run_single_generation(job)
    gen2 = gp.run_single_generation(job)
    assert gen1["id"] == gen2["id"]
    gens = db.list_audio_generations(db.get_chapter(db.get_book_row(book["id"])["id"], 0)["id"])
    assert len(gens) == 1


def test_explicit_regeneration_bypasses_ready_generation(client, monkeypatch, tmp_path):
    """作者明確重生成時要建立新 generation；普通重複呼叫仍維持 reuse。"""
    _make_tts_provider()
    a = new_author("作者Regenerate")
    book = _book(a)
    _fake_synth(monkeypatch, tmp_path)
    row = db.get_book_row(book["id"])
    book_dict = gp._build_book_dict(row)
    ch = db.get_chapter(row["id"], 0)
    first, created = ag.create_or_reuse_generation(
        book=book_dict, chapter=ch, source_text_hash=ch["text_hash"],
        tts_provider=_make_tts_provider(), requested_by=None,
    )
    assert created is True
    db.mark_generation_ready(first["id"], "audio/first.mp3", "audio/first.json")

    second, created = ag.create_or_reuse_generation(
        book=book_dict, chapter=ch, source_text_hash=ch["text_hash"],
        tts_provider=_make_tts_provider(), requested_by=None, force_new=True,
    )
    assert created is True
    assert second["id"] != first["id"]
    assert second["status"] == "queued"


def test_failed_generation_retry_gets_new_identity(client, monkeypatch, tmp_path):
    """failed generation 不得以舊 unique key 阻塞後續重試。"""
    _make_tts_provider()
    a = new_author("作者FailedRetry")
    book = _book(a, n=1)
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    book_dict = gp._build_book_dict(row)
    provider = _make_tts_provider()

    first, created = ag.create_or_reuse_generation(
        book=book_dict, chapter=ch, source_text_hash=ch["text_hash"],
        mode=c.AUDIO_MODE_SINGLE, tts_provider=provider, requested_by=None,
    )
    assert created is True
    db.mark_generation_failed(first["id"], "provider failed")

    second, created = ag.create_or_reuse_generation(
        book=book_dict, chapter=ch, source_text_hash=ch["text_hash"],
        mode=c.AUDIO_MODE_SINGLE, tts_provider=provider, requested_by=None,
    )
    assert created is True
    assert second["id"] != first["id"]
    assert second["status"] == "queued"


def test_provider_failure_does_not_replace_active(client, monkeypatch, tmp_path):
    _make_tts_provider()
    a = new_author("作者D")
    book = _book(a)
    _fake_synth(monkeypatch, tmp_path)
    job = {"job_type": "audio_single", "requested_by": None,
           "payload": json.dumps({"bid": book["id"], "seq": 0})}
    gen1 = gp.run_single_generation(job)
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    assert ch["active_audio_generation_id"] == gen1["id"]

    # 第二次（改文字 → 不同 key）失敗 → active 保留舊的
    db.update_chapter(row["id"], 0, {"text": "新的文字內容。" * 20, "text_hash": "abc123"})
    db.update_book(book["id"], {"audio_settings_version": 2})

    def _failing_synth(book, chapter, analysis, on_progress=None, final_audio_path=None, final_timing_path=None):
        raise RuntimeError("provider down")
    monkeypatch.setattr("backend.tts.generate_chapter_audio", _failing_synth)
    with pytest.raises(Exception):
        gp.run_single_generation(job)
    ch_after = db.get_chapter(row["id"], 0)
    assert ch_after["active_audio_generation_id"] == gen1["id"]


def test_batch_supports_partial_success(client, monkeypatch, tmp_path):
    _make_tts_provider()
    a = new_author("作者E")
    book = _book(a, n=3)
    _fake_synth(monkeypatch, tmp_path)
    # 讓第二章失敗
    row = db.get_book_row(book["id"])
    db.update_chapter(row["id"], 1, {"text": "x" * 200, "text_hash": "b1"})
    orig = gp.run_single_generation
    calls = {"n": 0}

    def patched(job):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("fail chapter 1")
        return orig(job)
    monkeypatch.setattr(gp, "run_single_generation", patched)
    result = gp.run_generate_all({"job_type": "audio_generate_all", "requested_by": None,
                                  "payload": json.dumps({"bid": book["id"]})})
    statuses = [item["status"] for item in result["items"]]
    assert "ready" in statuses
    assert "failed" in statuses


def test_batch_all_failed_does_not_report_success(client, monkeypatch):
    _make_tts_provider()
    a = new_author("作者BatchFail")
    book = _book(a, n=1)

    monkeypatch.setattr(gp, "run_single_generation", lambda job: (_ for _ in ()).throw(RuntimeError("provider down")))
    with pytest.raises(gp.GenerationUnavailableError, match="沒有任何章節生成成功"):
        gp.run_generate_all({"job_type": "audio_generate_all", "requested_by": None,
                             "payload": json.dumps({"bid": book["id"]})})


def test_api_enqueues_single_and_multi(client):
    _make_tts_provider()
    operator = new_admin("作者F生成管理員")
    book = _book(operator)
    # single 模式需先設定聲線（preflight 驗證，避免建立注定失敗的 job）
    db.update_book(book["id"], {"default_voice_id": "v-zh-1"})
    r = operator.post(f"/api/books/{book['id']}/chapters/0/audio-generations", json={"mode": "single"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["jobId"] > 0
    job = db.get_generation_job(data["jobId"])
    assert job["job_type"] in ("audio_single", "audio_multi")
    # list / get
    assert operator.get(f"/api/books/{book['id']}/chapters/0/audio-generations").status_code == 200
    assert operator.post(f"/api/books/{book['id']}/audio-generations/batch").status_code == 200


def test_api_rejects_single_without_voice_and_unknown_voice(client):
    """preflight：未選聲線或聲線不在 provider catalog → 422，不建立 job。"""
    _make_tts_provider()
    operator = new_admin("作者H生成管理員")
    book = _book(operator)
    r = operator.post(f"/api/books/{book['id']}/chapters/0/audio-generations", json={"mode": "single"})
    assert r.status_code == 422, r.text
    db.update_book(book["id"], {"default_voice_id": "en-US-JennyNeural"})
    r2 = operator.post(f"/api/books/{book['id']}/chapters/0/audio-generations", json={"mode": "single"})
    assert r2.status_code == 422, r2.text
    assert "語者清單" in r2.text
    jobs = db.query("SELECT COUNT(*) AS n FROM generation_jobs WHERE book_id=?", (db.get_book_row(book["id"])["id"],))
    assert jobs[0]["n"] == 0


def test_api_requires_owner(client):
    _make_tts_provider()
    a = new_author("作者G")
    book = _book(a)
    stranger = new_author("陌生人X")
    assert stranger.post(f"/api/books/{book['id']}/chapters/0/audio-generations", json={"mode": "single"}).status_code in (403, 404)
    assert stranger.get(f"/api/books/{book['id']}/chapters/0/audio-generations").status_code in (403, 404)
