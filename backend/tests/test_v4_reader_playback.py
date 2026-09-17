"""V4 Phase 11：Reader playback / progress / capability UX regression tests.

驗證：
- Public/private/unpublished chapter text+audio 授權矩陣（不可弱化 can_view_chapter）。
- 音訊 playback 使用 active ready generation 的 generation-specific 路徑。
- Progress save/restore 統一 read/listen 行為（audioPositionSeconds / lastMode）。
- 讀者不會被承諾無法取得的學習／跟讀能力（learning capability 隱藏）。
"""
import os
import json

from backend import db, settings, storage
from backend.services import audio_generation as ag
from backend.services import generation_pipeline as gp
from backend.tests.conftest import (new_author, new_admin, new_user,
                                    upload_book, add_chapter, publish_book)


def _ensure_tts():
    if not db.get_active_tts_provider():
        db.create_tts_provider({
            "name": "測試TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
            "enabled": True, "is_default": True,
        })


def _book_with_chapters(a, n=2):
    book = upload_book(a)
    for i in range(2, n + 1):
        add_chapter(a, book["id"], title=f"第{i}章", text=f"第 {i} 章正文內容。" * 6)
    return book


def _make_active_generation(a, book, seq):
    """建立 active ready generation 並寫出 generation-specific audio/timing 檔。"""
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], seq)
    book_dict = gp._build_book_dict(row)
    gen, _ = ag.create_or_reuse_generation(book=book_dict, chapter=ch,
                                           source_text_hash=ch["text_hash"], tts_provider=db.get_active_tts_provider())
    audio_rel = storage.generation_audio_path(book["id"], gen["id"])
    timing_rel = storage.generation_timing_path(book["id"], gen["id"])
    abs_audio = os.path.join(settings.ROOT_DIR, audio_rel)
    abs_timing = os.path.join(settings.ROOT_DIR, timing_rel)
    os.makedirs(os.path.dirname(abs_audio), exist_ok=True)
    with open(abs_audio, "wb") as f:
        f.write(b"ID3activegen")
    with open(abs_timing, "w", encoding="utf-8") as f:
        json.dump({"segments": [{"dur": 1.0}]}, f)
    ag.mark_generation_ready_and_activate(book=book_dict, chapter=ch, generation_id=gen["id"],
                                          audio_path=audio_rel, timing_path=timing_rel)
    return gen


def test_playback_uses_active_ready_generation():
    _ensure_tts()
    a = new_author("作者A")
    book = _book_with_chapters(a)
    gen = _make_active_generation(a, book, 0)
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    # active pointer 已設
    assert ch["active_audio_generation_id"] == gen["id"]
    # 播放 response 為 active generation 內容
    r = a.get(f"/api/books/{book['id']}/audio/0")
    assert r.status_code == 200
    assert r.content == b"ID3activegen"
    # timing 亦取自 active generation
    d = a.get(f"/api/books/{book['id']}/chapters/0").json()
    assert d["timing"] == {"segments": [{"dur": 1.0}]}


def test_playback_authorization_matrix(client):
    _ensure_tts()
    owner = new_author("作者B")
    book = _book_with_chapters(owner)
    _make_active_generation(owner, book, 0)
    stranger = new_author("陌生人")
    reader = new_user("讀者R")
    guest = client

    # 未 published（draft）→ 只有 owner/admin 可讀 text/audio
    assert stranger.get(f"/api/books/{book['id']}/audio/0").status_code in (403, 404)
    assert reader.get(f"/api/books/{book['id']}/audio/0").status_code in (403, 404)
    assert guest.get(f"/api/books/{book['id']}/audio/0").status_code in (403, 404)
    assert stranger.get(f"/api/books/{book['id']}/chapters/0").status_code in (403, 404)
    assert owner.get(f"/api/books/{book['id']}/audio/0").status_code == 200
    # admin 可讀
    admin = new_admin("管理員X")
    assert admin.get(f"/api/books/{book['id']}/audio/0").status_code == 200


def test_playback_authorization_published_book(client):
    _ensure_tts()
    owner = new_author("作者C")
    book = publish_book(owner, adm=new_admin("管理員P"))
    _make_active_generation(owner, book, 0)
    reader = new_user("讀者Q")
    # published → reader 可讀 audio
    assert reader.get(f"/api/books/{book['id']}/audio/0").status_code == 200
    assert reader.get(f"/api/books/{book['id']}/chapters/0").status_code == 200


def test_progress_save_restore_read_and_listen():
    a = new_author("作者D")
    book = _book_with_chapters(a)
    reader = new_user("進度讀者")
    # read mode
    r = reader.put("/api/me/progress", json={
        "bookId": book["id"], "chapterSeq": 1, "position": 120, "percent": 0.35, "lastMode": "read",
    })
    assert r.status_code == 200, r.text
    prog = reader.get("/api/me/progress").json()["items"]
    item = next(p for p in prog if p["book_id"] == db.get_book_row(book["id"])["id"])
    assert item["chapter_seq"] == 1
    assert item["position"] == 120
    assert item["percent"] == 0.35
    assert item["last_mode"] == "read"
    # listen mode 更新同一本書 → last_mode / audio_position 更新
    r2 = reader.put("/api/me/progress", json={
        "bookId": book["id"], "chapterSeq": 1, "audioPositionSeconds": 42.5, "lastMode": "listen",
    })
    assert r2.status_code == 200
    prog2 = reader.get("/api/me/progress").json()["items"]
    item2 = next(p for p in prog2 if p["book_id"] == db.get_book_row(book["id"])["id"])
    assert item2["last_mode"] == "listen"
    assert item2["audio_position_seconds"] == 42.5


def test_unavailable_learning_capability_hidden_from_reader():
    # 若無 analysis（learning/read-along 依賴分析），讀者不應被承諾該能力
    _ensure_tts()
    owner = new_author("作者E")
    book = publish_book(owner, adm=new_admin("管理員L"))
    reader = new_user("讀者H")
    # chapter 資料不含 learning 承諾（無 segments 分析 → 無跟讀/學習）
    d = reader.get(f"/api/books/{book['id']}/chapters/0").json()
    assert d["timing"] is None  # 無分析產物
    # book payload 不暴露會誤導的 learning 旗標
    b = reader.get(f"/api/books/{book['id']}").json()
    assert "workflow" not in b  # 讀者看不到 workflow


def test_chapter_data_returns_v4_canonical_analysis_for_player():
    """播放器依賴的 chapter data 必須回傳 V4 canonical analysis（含逐句 segments），而非 legacy projection。"""
    from backend.services import analysis as analysis_svc
    _ensure_tts()
    a = new_author("作者F")
    book = _book_with_chapters(a)
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    aid = analysis_svc.create_analysis_row(book_id=row["id"], chapter_id=ch["id"],
                                           source_text_hash=ch["text_hash"], prompt_version="v1")
    artifact = analysis_svc.normalize_analysis(
        {"segments": [{"text": "第一句。"}, {"text": "第二句。"}],
         "speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}]},
        chapter_key=ch["chapter_key"], source_text_hash=ch["text_hash"])
    analysis_svc.save_ready_analysis(aid, artifact)

    d = a.get(f"/api/books/{book['id']}/chapters/0").json()
    assert d["analysis"]["status"] == "ready"
    assert d["analysis"]["analysisId"] == aid
    assert d["analysis"]["schemaVersion"] == 3
    assert len(d["analysis"]["segments"]) == 2
    assert d["analysis"]["segments"][0]["text"] == "第一句。"


def test_chapter_data_returns_empty_analysis_without_ready_record():
    _ensure_tts()
    a = new_author("作者G")
    book = _book_with_chapters(a)
    d = a.get(f"/api/books/{book['id']}/chapters/0").json()
    assert d["analysis"] == {}


def test_stale_v4_segmentation_returns_current_source_spans_without_old_timing():
    """舊 ready artifact 不得把 word-level units 冒充成目前句子分段。"""
    from backend.services import analysis as analysis_svc
    from backend.services import source_faithful

    _ensure_tts()
    source = "Alice looked at the sky. Bob smiled."
    a = new_author("作者H")
    book = upload_book(a)
    add_chapter(a, book["id"], title="English chapter", text=source)
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 1)
    aid = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=ch["id"], source_text_hash=ch["text_hash"],
        prompt_version="v1", schema_version=4,
    )
    current = source_faithful.segment_source(source)
    legacy_artifact = {
        **current,
        "segmentationVersion": "paragraph-quote-v1",
        "segments": [dict(current["segments"][0], source_end=5, text="Alice")],
    }
    rel = storage.analysis_artifact_path(book["id"], aid)
    absolute = os.path.join(settings.ROOT_DIR, rel)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    with open(absolute, "w", encoding="utf-8") as handle:
        json.dump(legacy_artifact, handle, ensure_ascii=False)
    db.mark_analysis_ready(aid, rel)

    response = a.get(f"/api/books/{book['id']}/chapters/1")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["analysis"]["status"] == "stale"
    assert data["analysis"]["storedSegmentationVersion"] == "paragraph-quote-v1"
    assert data["analysis"]["segmentationVersion"] == source_faithful.SEGMENTATION_VERSION
    assert [item["text"] for item in data["analysis"]["segments"]] == [
        "Alice looked at the sky.", "Bob smiled."
    ]
    assert data["timing"] is None
