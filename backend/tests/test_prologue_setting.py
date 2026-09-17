"""作品序章顯示設定的 persistence 與向後相容 regression。"""

from backend import db
from backend.services import book_service
from backend.tests.conftest import add_chapter, new_author, upload_book


def test_manual_book_without_prologue_keeps_zero_based_storage_and_first_chapter_number():
    author = new_author("無序章作品作者")
    response = author.post("/api/books/manual", json={
        "title": "沒有序章的作品",
        "category": "zh",
        "hasPrologue": False,
    })
    assert response.status_code == 200, response.text
    book = response.json()
    assert book["hasPrologue"] is False

    added = add_chapter(author, book["id"], title="第一章", text="正文第一段。")
    assert added["chapters"][0]["seq"] == 0
    detail = author.get(f"/api/books/{book['id']}")
    assert detail.json()["hasPrologue"] is False
    assert db.get_chapter(db.get_book_row(book["id"])["id"], 0)["title"] == "第一章"


def test_upload_and_book_update_persist_prologue_setting():
    author = new_author("序章設定作者")
    book = upload_book(author, category="zh", hasPrologue="false")
    assert book["hasPrologue"] is False

    changed = author.put(f"/api/books/{book['id']}", json={"hasPrologue": True})
    assert changed.status_code == 200, changed.text
    assert changed.json()["hasPrologue"] is True
    assert author.get(f"/api/books/{book['id']}").json()["hasPrologue"] is True


def test_legacy_book_defaults_to_prologue_without_data_migration():
    author = new_author("舊書序章作者")
    book = upload_book(author, category="zh")
    db.update_book(book["id"], {"settings": "{}"})
    assert author.get(f"/api/books/{book['id']}").json()["hasPrologue"] is True
    assert db.get_book_row(book["id"])["settings"] == "{}"


def test_switching_prologue_preserves_chapter_analysis_and_audio_references(client):
    author = new_author("序章資料保留作者")
    response = author.post("/api/books/manual", json={
        "title": "保留分析音訊的作品",
        "category": "zh",
        "hasPrologue": True,
    })
    assert response.status_code == 200, response.text
    book = response.json()
    add_chapter(author, book["id"], title="序章", text="已有資料的正文。")

    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"],
        "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"],
        "schema_version": 4,
        "status": "ready",
    })
    generation_id = db.create_audio_generation({
        "book_id": row["id"],
        "chapter_id": chapter["id"],
        "mode": "single",
        "source_text_hash": chapter["text_hash"],
        "analysis_id": analysis_id,
        "generation_key": "prologue-setting-reference-test",
        "status": "ready",
    })
    db.update_chapter(row["id"], 0, {
        "current_analysis_id": analysis_id,
        "active_audio_generation_id": generation_id,
    })

    before = db.get_chapter(row["id"], 0)
    book_service.update_meta(row, has_prologue=False)
    after = db.get_chapter(row["id"], 0)

    assert (after["id"], after["seq"]) == (before["id"], before["seq"])
    assert after["current_analysis_id"] == analysis_id
    assert after["active_audio_generation_id"] == generation_id
    assert db.get_chapter_analysis(analysis_id)["status"] == "ready"
    assert db.get_audio_generation(generation_id)["status"] == "ready"
