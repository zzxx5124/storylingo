"""迁移测试：旧 storage 书 -> SQLite，幂等，章节状态与产物档完整。"""
import json
import os

from backend import db, settings, storage as st
from backend.services import migration


def _write_legacy_book(bid):
    bd = os.path.join(settings.BOOKS_DIR, bid)
    os.makedirs(os.path.join(bd, "chapters"), exist_ok=True)
    os.makedirs(os.path.join(bd, "audio"), exist_ok=True)

    src = "第一章 标题。\n主角登场。\n他很惊讶。\n"
    src2 = "第二章 转场。\n事件发生。\n" + "这是后续说明。" * 20
    source = src + src2
    with open(st.source_path(bid), "w", encoding="utf-8") as f:
        f.write(source)

    chapters = [
        {"seq": 0, "title": "第一章", "start": 0, "end": len(src),
         "status": "analyzed", "audio": "ready"},
        {"seq": 1, "title": "第二章", "start": len(src), "end": len(source),
         "status": "analyzed", "audio": "ready"},
    ]
    meta = {"id": bid, "title": "测试旧书", "category": "vocab", "vocabLevel": "AUTO",
            "categories": ["vocab"], "created": "2024-03-01T00:00:00",
            "voices": {}, "voicePrefs": {}, "speakerInfo": {}, "settings": {},
            "totalChars": len(source), "chapters": chapters}
    with open(st.meta_path(bid), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)

    for c in chapters:
        s = c["seq"]
        text = source[c["start"]:c["end"]]
        with open(st.chapter_json_path(bid, s), "w", encoding="utf-8") as f:
            json.dump({"segments": [{"speaker": "旁白", "text": text[:5]}]}, f)
        with open(st.chapter_timing_path(bid, s), "w", encoding="utf-8") as f:
            f.write("[]")
        with open(st.chapter_audio_path(bid, s), "wb") as f:
            f.write(b"ID3")
    return bid


def test_migrate_legacy_book():
    bid = _write_legacy_book("legacy1")
    report = migration.run_migrate()
    assert report["total_dirs"] == 1
    assert report["migrated"] == 1
    row = db.get_book_row(bid)
    assert row["title"] == "测试旧书"
    assert row["status"] == "approved"
    assert row["published_at"]

    chs = db.list_chapters(row["id"])
    assert len(chs) == 2
    assert chs[0]["status"] == "analyzed"
    assert chs[0]["audio"] == "ready"
    # 產物檔在本書的 test storage（相對 ROOT_DIR 的 legacy 路徑不存在，因 test 用 tmp）
    assert os.path.exists(os.path.join(settings.BOOKS_DIR, bid, "chapters", "0000.json"))
    assert os.path.exists(os.path.join(settings.BOOKS_DIR, bid, "audio", "0000.mp3"))


def test_migrate_idempotent():
    _write_legacy_book("legacy2")
    migration.run_migrate()
    report = migration.run_migrate()
    assert report["migrated"] == 0
    assert report["skipped"] == 1


def test_migrate_no_audio():
    bid = "legacy3"
    bd = os.path.join(settings.BOOKS_DIR, bid)
    os.makedirs(bd, exist_ok=True)
    with open(st.source_path(bid), "w", encoding="utf-8") as f:
        f.write("正文内容。二段。三段。" * 30)
    meta = {"title": "无音档书", "chapters": [
        {"seq": 0, "title": "", "start": 0, "end": 45, "status": "pending", "audio": "none"}]}
    with open(st.meta_path(bid), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    report = migration.run_migrate()
    book = next(b for b in report["books"] if b["bid"] == bid)
    assert book["chapters"] == 1
    ch = db.list_chapters(db.get_book_row(bid)["id"])[0]
    assert ch["status"] == "pending"
    assert ch["audio"] == "none"