"""作品語言型別編輯的 ownership、分析鎖定與 persistence regression。"""

import pytest

from backend import analyzer, db, settings
from backend.services import analysis
from backend.tests.conftest import add_chapter, new_author, upload_book


def _book_with_chapter(author):
    book = upload_book(author, category="vocab")
    chapter = add_chapter(author, book["id"], text="第一章內容。" * 20)
    row = db.get_book_row(book["id"])
    return book, row, db.get_chapter(row["id"], chapter["chapters"][0]["seq"])


def test_unanalysed_owner_can_change_language_type_and_reload():
    author = new_author("語言型別作者")
    book = upload_book(author, category="vocab")

    detail = author.get(f"/api/books/{book['id']}")
    assert detail.status_code == 200
    assert detail.json()["languageTypeEditable"] is True

    changed = author.put(f"/api/books/{book['id']}", json={"category": "zh"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["category"] == "zh"
    assert author.get(f"/api/books/{book['id']}").json()["category"] == "zh"


@pytest.mark.parametrize("status", ["queued", "running", "ready", "failed"])
def test_any_existing_analysis_state_locks_language_type(status):
    author = new_author("鎖定狀態作者")
    book, row, chapter = _book_with_chapter(author)
    aid = analysis.create_analysis_row(
        book_id=row["id"], chapter_id=chapter["id"],
        source_text_hash=chapter["text_hash"], created_by=row["owner_id"],
    )
    if status != "queued":
        db.update_chapter_analysis(aid, {"status": status})

    detail = author.get(f"/api/books/{book['id']}")
    assert detail.status_code == 200
    assert detail.json()["languageTypeEditable"] is False
    assert "分析資料" in detail.json()["languageTypeLockReason"]

    rejected = author.put(f"/api/books/{book['id']}", json={"category": "zh"})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["code"] == "language_type_locked_after_analysis"
    assert author.get(f"/api/books/{book['id']}").json()["category"] == "vocab"


def test_invalid_language_type_is_rejected():
    author = new_author("非法語言型別作者")
    book = upload_book(author, category="vocab")
    response = author.put(f"/api/books/{book['id']}", json={"category": "not-a-language"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_language_type"
    assert db.get_book_row(book["id"])["category"] == "vocab"


def test_other_language_type_is_valid_and_uses_plain_analysis_prompt():
    author = new_author("其它語言型別作者")
    book = upload_book(author, category="other")
    assert book["category"] == "other"
    prompt = analyzer._make_prompt({"category": "other"})
    assert "英文單字教學" not in prompt
    assert settings.effective_category({"category": "other"}) == "other"


def test_non_owner_cannot_change_language_type():
    owner = new_author("語言型別擁有者")
    other = new_author("語言型別非擁有者")
    book = upload_book(owner, category="vocab")
    response = other.put(f"/api/books/{book['id']}", json={"category": "zh"})
    assert response.status_code == 403
    assert db.get_book_row(book["id"])["category"] == "vocab"


def test_language_type_update_keeps_csrf_protection(monkeypatch):
    author = new_author("language_csrf")
    book = upload_book(author, category="vocab")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    response = author.put(f"/api/books/{book['id']}", json={"category": "zh"})
    assert response.status_code == 403
    assert db.get_book_row(book["id"])["category"] == "vocab"
