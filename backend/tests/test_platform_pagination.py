"""Integration coverage for normalized public/private platform collections."""

from backend import db
from backend.services import book_service

from .conftest import new_admin, new_author, new_user, publish_book, upload_book


def _publish_many(owner, admin, count=5):
    return [publish_book(owner, admin, filename=f"分頁作品{i}.txt") for i in range(count)]


def test_public_search_filters_before_page_and_returns_navigation(client):
    author = new_author("分頁作者")
    admin = new_admin("分頁管理")
    books = _publish_many(author, admin, 5)
    db.execute("UPDATE books SET tags=? WHERE bid=?", ("有聲標籤", books[0]["id"]))
    db.execute("UPDATE chapters SET audio='ready' WHERE book_id=(SELECT id FROM books WHERE bid=?)", (books[0]["id"],))

    result = client.get("/api/search", params={"q": "分頁作品", "has_audio": "true", "page": 1, "page_size": 1})
    assert result.status_code == 200
    data = result.json()
    assert data["total"] == 1
    assert data["total_pages"] == 1
    assert len(data["items"]) == 1
    assert data["has_next"] is False
    assert data["has_prev"] is False


def test_search_beyond_final_and_invalid_bounds(client):
    author = new_author("邊界作者")
    admin = new_admin("邊界管理")
    _publish_many(author, admin, 2)
    data = client.get("/api/search", params={"page": 3, "page_size": 1}).json()
    assert data["items"] == []
    assert data["page"] == 3
    assert data["total"] == 2
    assert data["total_pages"] == 2
    assert data["has_next"] is False
    assert data["has_prev"] is True
    assert client.get("/api/search", params={"page": 0}).status_code == 400
    assert client.get("/api/search", params={"page_size": 101}).status_code == 400
    assert client.get("/api/search", params={"sort": "title; DROP TABLE books"}).status_code == 400
    assert client.get("/api/search", params={"q": "%"}).json()["total"] == 0


def test_private_collections_are_paged_and_principal_scoped():
    author = new_author("私人作者")
    admin = new_admin("私人管理")
    books = _publish_many(author, admin, 3)
    reader = new_user("分頁讀者")
    other = new_user("其他讀者")
    for book in books:
        reader.post(f"/api/books/{book['id']}/favorite")
        reader.post(f"/api/books/{book['id']}/follow")
    other.post(f"/api/books/{books[0]['id']}/favorite")
    reader.put("/api/me/progress", json={"bookId": books[0]["id"], "chapterSeq": 0, "position": 1, "percent": 2})
    reader.post("/api/me/bookmarks", json={"bookId": books[0]["id"], "chapterSeq": 0, "position": 1, "note": "頁"})

    library_response = reader.get("/api/me/library", params={"kind": "favorite", "page": 2, "page_size": 2})
    library = library_response.json()
    assert library_response.headers["cache-control"] == "private, no-store"
    assert library["total"] == 3
    assert len(library["items"]) == 1
    assert other.get("/api/me/library", params={"kind": "favorite"}).json()["total"] == 1
    assert reader.get("/api/me/library", params={"kind": "followed", "page": 1, "page_size": 2}).json()["total"] == 3
    assert reader.get("/api/me/history", params={"page": 1, "page_size": 20}).json()["total"] == 1
    assert reader.get("/api/me/bookmarks", params={"page": 1, "page_size": 20}).json()["total"] == 1


def test_my_works_page_is_owner_scoped_and_legacy_omission_remains_compatible():
    author = new_author("作品分頁作者")
    admin = new_admin("作品分頁管理")
    books = _publish_many(author, admin, 3)
    data = author.get("/api/books", params={"mine": 1, "page": 2, "page_size": 2}).json()
    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert all(item["ownerId"] == db.get_user_by_name(author.username)["id"] for item in data["items"])
    assert author.get("/api/books", params={"mine": 1, "page": 1}).json()["page_size"] == 20
    legacy = author.get("/api/books", params={"mine": 1}).json()
    assert isinstance(legacy, list)
    assert {item["id"] for item in legacy} == {book["id"] for book in books}


def test_author_page_is_public_and_paged():
    author = new_author("公開作者分頁")
    admin = new_admin("公開作者管理")
    books = _publish_many(author, admin, 3)
    profile = db.create_author_profile(
        db.get_user_by_name(author.username)["id"], public_id="page-author",
        slug="page-author", display_name="公開作者分頁")
    db.execute("UPDATE books SET author_profile_id=? WHERE owner_id=?", (profile["id"], db.get_user_by_name(author.username)["id"]))
    data = admin.get(f"/api/authors/{profile['slug']}", params={"page": 2, "page_size": 2}).json()
    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["works"] == data["items"]
    assert all(item["id"] in {book["id"] for book in books} for item in data["items"])


def test_paged_book_cards_use_batched_relationship_reads(monkeypatch):
    author = new_author("查詢計數作者")
    admin = new_admin("查詢計數管理")
    _publish_many(author, admin, 4)
    calls = []
    original = db.query

    def traced(sql, params=()):
        calls.append(sql)
        return original(sql, params)

    monkeypatch.setattr(db, "query", traced)
    result = book_service.list_books(None, page=1, page_size=100, public=True)
    assert len(result["items"]) == 4
    # count + page + chapter stats + users + categories + author profiles;
    # relationship reads remain constant as page size grows.
    assert len(calls) <= 7


def test_pagination_indexes_are_additive_idempotent_and_fk_safe():
    expected = {
        "idx_library_items_user_kind_created",
        "idx_follows_user_created",
        "idx_reading_history_user_recent",
        "idx_bookmarks_user_created",
        "idx_books_author_public_order",
    }
    db.init_db()
    db.init_db()
    actual = set()
    for table in ("library_items", "follows", "reading_history", "bookmarks", "books"):
        actual.update(row["name"] for row in db.query(f"PRAGMA index_list({table})"))
    assert expected <= actual
    assert db.query("PRAGMA foreign_key_check") == []
