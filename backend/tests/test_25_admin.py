"""管理後台：類別 CRUD、使用者角色/密碼、dashboard 統計。"""
from fastapi.testclient import TestClient

from backend import db
from backend.main import app
from backend.tests.conftest import new_author, new_admin, new_user, upload_book


def _category_list(adm):
    return adm.get("/api/admin/categories").json()["categories"]


def test_category_crud_deletable_when_empty():
    adm = new_admin()
    assert adm.post("/api/admin/categories", json={"name": "恐怖"}).status_code == 200
    assert adm.post("/api/admin/categories", json={"name": "恐怖"}).status_code == 400
    cats = _category_list(adm)
    target = next(c for c in cats if c["name"] == "恐怖")
    assert adm.put(f"/api/admin/categories/{target['id']}", json={"name": "恐怖奇談"}).status_code == 200
    assert adm.delete(f"/api/admin/categories/{target['id']}").status_code == 200


def test_conflict_categories():
    adm = new_admin()
    assert adm.post("/api/admin/categories", json={"name": "冒險"}).status_code == 200
    assert adm.post("/api/admin/categories", json={"name": "冒險"}).status_code == 400


def test_category_with_book_cannot_delete():
    adm = new_admin()
    cat_id = db.add_category("測試類別", 0)
    a = new_author()
    book = upload_book(a)
    db.update_book(book["id"], {"category_id": cat_id})
    a.post(f"/api/books/{book['id']}/submit")
    adm.post(f"/api/admin/books/{book['id']}/approve")
    assert adm.delete(f"/api/admin/categories/{cat_id}").status_code == 409


def test_category_not_found_delete():
    adm = new_admin()
    assert adm.delete("/api/admin/categories/999999").status_code == 404


def test_admin_categories_include_book_count():
    adm = new_admin()
    cats = _category_list(adm)
    assert all("bookCount" in c for c in cats)
    empty = next(c for c in cats if not c.get("bookCount"))
    cat_id = db.add_category("計數類別", 0)
    a = new_author()
    book = upload_book(a)
    db.update_book(book["id"], {"category_id": cat_id})
    cats = _category_list(adm)
    target = next(c for c in cats if c["id"] == cat_id)
    assert target["bookCount"] == 1
    empty_id = next(c for c in cats if c["id"] == empty["id"])
    assert empty_id["bookCount"] == 0


def test_admin_can_bulk_assign_category_atomically():
    adm = new_admin()
    cat_id = db.add_category("批次類別", 0)
    author = new_author()
    first = upload_book(author, filename="first.txt")
    second = upload_book(author, filename="second.txt")

    result = adm.post("/api/admin/books/bulk-category", json={
        "bookIds": [first["id"], second["id"]], "categoryId": cat_id,
    })
    assert result.status_code == 200, result.text
    assert result.json()["updated"] == 2
    assert db.get_book_row(first["id"])["category_id"] == cat_id
    assert db.get_book_row(second["id"])["category_id"] == cat_id

    missing = adm.post("/api/admin/books/bulk-category", json={
        "bookIds": [first["id"], "missing-book"], "categoryId": cat_id,
    })
    assert missing.status_code == 404
    assert db.get_book_row(first["id"])["category_id"] == cat_id

    non_admin = new_author("非管理員")
    assert non_admin.post("/api/admin/books/bulk-category", json={
        "bookIds": [first["id"]], "categoryId": cat_id,
    }).status_code == 403


def test_admin_bulk_category_validates_empty_duplicate_limit_and_category():
    adm = new_admin()
    cat_id = db.add_category("批次驗證類別", 0)
    author = new_author()
    first = upload_book(author, filename="validation-first.txt")
    second = upload_book(author, filename="validation-second.txt")

    assert adm.post("/api/admin/books/bulk-category", json={"bookIds": [], "categoryId": cat_id}).status_code == 400
    duplicate = adm.post("/api/admin/books/bulk-category", json={
        "bookIds": [first["id"], first["id"], second["id"]], "categoryId": cat_id,
    })
    assert duplicate.status_code == 200
    assert duplicate.json()["updated"] == 2
    assert len(duplicate.json()["bookIds"]) == 2
    assert adm.post("/api/admin/books/bulk-category", json={
        "bookIds": [first["id"]] * 201, "categoryId": cat_id,
    }).status_code == 400

    before = db.get_book_row(first["id"])["category_id"]
    invalid_category = adm.post("/api/admin/books/bulk-category", json={
        "bookIds": [first["id"], second["id"]], "categoryId": 999999,
    })
    assert invalid_category.status_code == 404
    assert db.get_book_row(first["id"])["category_id"] == before
    assert db.get_book_row(second["id"])["category_id"] == cat_id


def test_admin_bulk_category_keeps_production_csrf_guard():
    from backend import settings

    adm = new_admin()
    cat_id = db.add_category("批次 CSRF 類別", 0)
    author = new_author()
    book = upload_book(author, filename="csrf-bulk.txt")
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        response = adm.post("/api/admin/books/bulk-category", json={
            "bookIds": [book["id"]], "categoryId": cat_id,
        })
        assert response.status_code == 403
        assert db.get_book_row(book["id"])["category_id"] is None
    finally:
        settings.APP_ENV = original


def test_role_change_and_password():
    adm = new_admin()
    reader = new_user("讀者")
    users = adm.get("/api/admin/users").json()["items"]
    uid = next(u["id"] for u in users if u["username"] == reader.username)
    assert adm.post(f"/api/admin/users/{uid}/role", json={"role": "author"}).status_code == 200
    assert db.get_user_by_id(uid)["role"] == "author"
    assert adm.put(f"/api/admin/users/{uid}/password", json={"password": "short6"}).status_code == 400
    assert adm.put(f"/api/admin/users/{uid}/password", json={"password": "newpass6"}).status_code == 200
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": reader.username, "password": "newpass6"})
    assert r.status_code == 200


def test_admin_user_list_pagination_search_and_status():
    adm = new_admin()
    for i in range(3):
        new_user(f"頁用戶{i}")
    r1 = adm.get("/api/admin/users", params={"page": 1, "page_size": 2})
    body = r1.json()
    assert len(body["items"]) == 2
    assert body["total"] >= 3
    assert body["total_pages"] >= 2
    r2 = adm.get("/api/admin/users", params={"page": 2, "page_size": 2})
    assert len(r2.json()["items"]) >= 1
    assert r1.json()["items"][0]["id"] != r2.json()["items"][0]["id"] or len(r1.json()["items"]) != len(r2.json()["items"])

    # 搜尋 username
    rq = adm.get("/api/admin/users", params={"q": "頁用戶0"})
    names = [u["username"] for u in rq.json()["items"]]
    assert any("頁用戶0" in n for n in names)

    # 角色/狀態篩選
    target = db.get_user_by_name("頁用戶1")
    assert adm.post(f"/api/admin/users/{target['id']}/status", json={"status": "disabled"}).status_code == 200
    assert db.get_user_by_id(target["id"])["account_status"] == "disabled"
    rd = adm.get("/api/admin/users", params={"status": "disabled"})
    assert all(u["account_status"] == "disabled" for u in rd.json()["items"])
    # 停用帳號無法登入
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "頁用戶1", "password": "secret123"}).status_code == 403
    # 恢復後可登入
    adm.post(f"/api/admin/users/{target['id']}/status", json={"status": "active"})
    assert c.post("/api/auth/login", json={"username": "頁用戶1", "password": "secret123"}).status_code == 200


def test_admin_cannot_disable_self():
    adm = new_admin()
    me = adm.get("/api/auth/me").json()["user"]
    r = adm.post(f"/api/admin/users/{me['id']}/role", json={"role": "reader"})
    assert r.status_code == 400


def test_user_with_books_is_soft_deleted_without_cascade():
    adm = new_admin()
    author = new_author("作家")
    upload_book(author)
    target = db.get_user_by_name(author.username)
    response = adm.delete(f"/api/admin/users/{target['id']}")
    assert response.status_code == 200
    assert db.get_user_by_id(target["id"])["account_status"] == "deleted"
    assert db.query_one("SELECT COUNT(*) n FROM books WHERE owner_id=?", (target["id"],))["n"] == 1


def test_dashboard_counts():
    adm = new_admin()
    a1 = new_author("甲")
    a2 = new_author("乙")
    upload_book(a1)
    upload_book(a2)
    d = adm.get("/api/admin/dashboard").json()
    assert d["books"]["total"] >= 2
    assert d["users"]["total"] >= 3
