"""產品重審：防止編輯覆寫與收藏分頁狀態錯判。"""
import pytest

from backend import db
from backend.tests.conftest import new_author, new_user, upload_book


def test_home_resume_is_account_scoped_and_requires_public_chapter():
    author = new_author()
    book = upload_book(author)
    db.update_book(book["id"], {"status": "approved", "published_at": db.ts()})
    row = db.get_book_row(book["id"])
    user_id = db.get_user_by_name(author.get("/api/auth/me").json()["user"]["username"])["id"]
    db.save_progress(user_id, row["id"], 0, 0, 42, last_mode="listen")
    response = author.get("/api/home")
    assert response.status_code == 200
    saved = response.json()["continueReading"][0]["readingProgress"]
    assert saved["chapterSeq"] == 0
    assert saved["percent"] == 42
    assert saved["lastMode"] == "listen"
    other = new_user("另一位首頁讀者")
    assert other.get("/api/home").json()["continueReading"] == []
    db.execute("UPDATE chapters SET publish_status='hidden' WHERE book_id=? AND seq=0", (row["id"],))
    assert "readingProgress" not in author.get("/api/home").json()["continueReading"][0]
    db.save_progress(user_id, row["id"], 999, 0, 42)
    assert "readingProgress" not in author.get("/api/home").json()["continueReading"][0]
    db.update_book(book["id"], {"published_at": None})
    assert author.get("/api/home").json()["continueReading"] == []


def test_editor_snapshot_rejects_stale_write_and_preserves_revision():
    author = new_author()
    book = upload_book(author)
    path = f"/api/books/{book['id']}/chapters/0"
    original = author.get(path).json()["chapter"]
    conditions = {"expectedChapterKey": original["chapterKey"],
                  "expectedTextHash": original["textHash"], "expectedTitle": original["title"]}
    assert author.put(path, json={"text": "第一個分頁已儲存的內容", **conditions}).status_code == 200
    before = author.get(path + "/revisions").json()
    response = author.put(path, json={"text": "第二個分頁的舊內容", **conditions})
    assert response.status_code == 409
    assert author.get(path).json()["chapter"]["text"] == "第一個分頁已儲存的內容"
    assert author.get(path + "/revisions").json() == before


@pytest.mark.parametrize("field,value", [("expectedChapterKey", "another-chapter"), ("expectedTitle", "舊標題")])
def test_editor_rejects_wrong_identity_or_changed_title(field, value):
    author = new_author()
    book = upload_book(author)
    path = f"/api/books/{book['id']}/chapters/0"
    original = author.get(path).json()["chapter"]
    assert author.put(path, json={"text": "不可寫入", field: value}).status_code == 409
    assert author.get(path).json()["chapter"] == original


def test_book_metadata_snapshot_rejects_stale_write_and_preserves_newer_values():
    author = new_author()
    book = upload_book(author)
    path = f"/api/books/{book['id']}"
    original = author.get(path).json()
    conditions = {
        "expectedUpdatedAt": original["updated"],
        "expectedMetadataHash": original["metadataHash"],
    }
    first = author.put(path, json={"title": "第一個分頁的新書名", **conditions})
    assert first.status_code == 200, first.text
    assert first.json()["title"] == "第一個分頁的新書名"
    stale = author.put(path, json={"title": "第二個分頁的舊書名", **conditions})
    assert stale.status_code == 409
    assert stale.json()["code"] == "book_metadata_conflict"
    assert author.get(path).json()["title"] == "第一個分頁的新書名"


def test_book_metadata_revision_payload_must_be_text():
    author = new_author()
    book = upload_book(author)
    response = author.put(f"/api/books/{book['id']}", json={"expectedMetadataHash": []})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_book_revision"


@pytest.mark.parametrize("payload", [{"text": "  "}, {"text": []}, {"title": {}}])
def test_invalid_editor_content_leaves_original_intact(payload):
    author = new_author()
    book = upload_book(author)
    path = f"/api/books/{book['id']}/chapters/0"
    original = author.get(path).json()["chapter"]
    assert author.put(path, json=payload).status_code == 400
    assert author.get(path).json()["chapter"] == original


def test_favorite_detail_is_account_scoped_and_not_first_page_dependent():
    author = new_author()
    book = upload_book(author)
    db.update_book(book["id"], {"status": "approved", "published_at": db.ts()})
    reader = new_user("收藏測試")
    assert reader.post(f"/api/books/{book['id']}/favorite").status_code == 200
    db.execute("UPDATE library_items SET created_at='2020-01-01' WHERE user_id=?", (db.get_user_by_name("收藏測試")["id"],))
    for _ in range(22):
        extra = upload_book(author)
        db.update_book(extra["id"], {"status": "approved", "published_at": db.ts()})
        assert reader.post(f"/api/books/{extra['id']}/favorite").status_code == 200
    first_page = reader.get("/api/me/library?kind=favorite&page=1&page_size=20").json()
    assert all(item["id"] != book["id"] for item in first_page["items"])
    assert reader.get(f"/api/books/{book['id']}").json()["favorite"] is True
    public_headers = {"X-StoryLingo-Public": "1"}
    assert reader.get(f"/api/books/{book['id']}", headers=public_headers).json()["favorite"] is True
    assert author.get(f"/api/books/{book['id']}").json()["favorite"] is False
    assert reader.delete(f"/api/books/{book['id']}/favorite").status_code == 200
    assert reader.get(f"/api/books/{book['id']}").json()["favorite"] is False


def test_public_relationships_do_not_expose_owner_hidden_chapters():
    author = new_author()
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    db.update_book(book["id"], {"status": "approved", "published_at": db.ts()})
    db.update_chapter(row["id"], 0, {"publish_status": "hidden"})
    user = db.get_user_by_name(author.username)
    db.add_library_item(user["id"], row["id"], "favorite")
    db.add_follow(user["id"], row["id"])
    data = author.get(f"/api/books/{book['id']}", headers={"X-StoryLingo-Public": "1"}).json()
    assert data["favorite"] is True and data["follow"] is True
    assert data["chapters"] == []
    assert data["canManage"] is False
    assert "workflow" not in data and "ownerId" not in data
