"""Phase 15a — Creator Journey 後端回歸測試。

覆蓋：
- POST /api/books/manual：權限（guest 401／reader 403）、必填與長度驗證、
  category/serial 正規化、ownership、mine 隔離
- 章節建立：非 owner author 與 reader 拒絕；chapter_key 產生（V4 身份）
- 作者申請 Journey：尚未申請 → pending → 去重 → rejected（含原因）→ 重新申請
  → approved → 角色生效
- 完整無上傳 Journey：reader 申請 → admin 通過 → 手動建立作品 → 建立第一章
- CSRF（production）：/api/books/manual 為 state-changing 受保護
"""
from fastapi.testclient import TestClient

from backend import db, settings
from backend.main import app as built_app
from backend.tests.conftest import new_admin, new_author, new_user


def _apply(reader, pen_name="筆名甲", rights=True):
    return reader.post("/api/authors/apply", json={
        "penName": pen_name, "bio": "我想分享合法作品", "rightsConfirmed": rights,
    })


def _manual(author, payload):
    return author.post("/api/books/manual", json=payload)


# ---------- POST /api/books/manual ----------

def test_manual_book_requires_author():
    guest = TestClient(built_app)
    assert guest.post("/api/books/manual", json={"title": "書"}).status_code == 401
    reader = new_user("手動讀者")
    assert _manual(reader, {"title": "書"}).status_code == 403


def test_manual_book_validation():
    author = new_author("手動作者")
    assert _manual(author, {}).status_code == 400
    assert _manual(author, {"title": "   "}).status_code == 400
    assert _manual(author, {"title": "長" * 201}).status_code == 400
    assert _manual(author, {"title": "長" * 200}).status_code == 200


def test_manual_book_creates_empty_owned_draft():
    author = new_author("手動作者")
    r = _manual(author, {
        "title": "我的第一本書", "synopsis": "簡介",
        "category": "en", "serial": "完結",
    })
    assert r.status_code == 200, r.text
    book = r.json()
    assert book["title"] == "我的第一本書"
    assert book["synopsis"] == "簡介"
    assert book["status"] == "draft"
    assert book["chapters"] == []
    assert book["category"] == "en"
    assert book["serial"] == "完結"
    row = db.get_book_row(book["id"])
    assert row["owner_id"] == db.get_user_by_name(author.username)["id"]


def test_manual_book_normalizes_invalid_category_and_serial():
    author = new_author("正規化作者")
    r = _manual(author, {
        "title": "B", "category": "nope", "serial": "???",
        "synopsis": "x" * 3000,
    })
    assert r.status_code == 200, r.text
    book = r.json()
    assert book["category"] == settings.CAT_VOCAB
    assert book["serial"] == "連載"
    assert len(book["synopsis"]) <= 2000


def test_manual_book_mine_isolation():
    owner = new_author("擁有者")
    other = new_author("另作者")
    book = _manual(owner, {"title": "私人書"}).json()
    mine = other.get("/api/books?mine=1").json()
    assert all(item["id"] != book["id"] for item in mine)
    owner_mine = owner.get("/api/books?mine=1").json()
    assert any(item["id"] == book["id"] for item in owner_mine)


# ---------- 章節建立（既有正式路徑，Phase 15a 要求走 add_chapter） ----------

def test_owner_can_add_first_chapter_with_chapter_key():
    author = new_author("章作者")
    book = _manual(author, {"title": "要寫章的書"}).json()
    r = author.post(f"/api/books/{book['id']}/chapters",
                    json={"title": "序章", "text": "  正文內容。"})
    assert r.status_code == 200, r.text
    chapters = r.json()["chapters"]
    assert len(chapters) == 1
    assert chapters[0]["title"] == "序章"
    row = db.get_book_row(book["id"])
    ch = db.get_chapter(row["id"], 0)
    assert ch and ch["chapter_key"]


def test_chapter_creation_forbidden_for_non_owner_or_reader():
    owner = new_author("擁有者")
    intruder = new_author("入侵者")
    reader = new_user("章讀者")
    book = _manual(owner, {"title": "受保護的書"}).json()
    for client in (intruder, reader):
        r = client.post(f"/api/books/{book['id']}/chapters",
                        json={"title": "x", "text": "內容"})
        assert r.status_code == 403, r.text
    r = owner.post(f"/api/books/{book['id']}/chapters", json={"title": "x", "text": "  "})
    assert r.status_code == 400


# ---------- 作者申請 Journey ----------

def test_apply_flow_status_dedupe_reject_reason_reapply_and_role():
    reader = new_user("申請者")
    admin = new_admin("審核員")
    # 尚未申請
    assert reader.get("/api/authors/application").json()["application"] is None
    # 送出 → pending；pending/approved 期間去重
    first = _apply(reader)
    assert first.json()["status"] == "pending"
    dup = _apply(reader, pen_name="換個筆名")
    assert dup.json()["id"] == first.json()["id"]
    status = reader.get("/api/authors/application").json()["application"]
    assert status["status"] == "pending"
    # 拒絕附原因 → 申請人可看到原因並可重新申請
    assert admin.post(
        f"/api/admin/author-applications/{first.json()['id']}/reject",
        json={"reason": "請補作者簡介"},
    ).status_code == 200
    status = reader.get("/api/authors/application").json()["application"]
    assert status["status"] == "rejected"
    assert status["reject_reason"] == "請補作者簡介"
    second = _apply(reader, pen_name="改進筆名")
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["status"] == "pending"
    # 通過 → 角色生效
    assert admin.post(
        f"/api/admin/author-applications/{second.json()['id']}/approve"
    ).status_code == 200
    assert db.get_user_by_name("申請者")["role"] == "author"
    assert reader.post("/api/auth/login", json={"username": "申請者", "password": "secret123"}).status_code == 200
    status = reader.get("/api/authors/application").json()["application"]
    assert status["status"] == "approved"


def test_apply_rejects_bad_payload():
    reader = new_user("壞申請")
    assert _apply(reader, pen_name="", rights=True).status_code == 400
    assert _apply(reader, pen_name="正常筆名", rights=False).status_code == 400


# ---------- 完整 Journey（無上傳） ----------

def test_full_creator_journey_without_upload():
    reader = new_user("旅程讀者")
    admin = new_admin("旅程審核")
    # 讀者 → 申請 → admin 通過 → 角色生效，下一次 request 重新登入
    _apply(reader)
    application = reader.get("/api/authors/application").json()["application"]
    admin.post(f"/api/admin/author-applications/{application['id']}/approve")
    assert reader.post("/api/auth/login", json={"username": "旅程讀者", "password": "secret123"}).status_code == 200
    me = reader.get("/api/auth/me").json()
    assert me["user"]["role"] == "author"
    # 建立第一本作品（不上傳檔案）
    book = _manual(reader, {"title": "旅程之書", "synopsis": "第一本", "category": "vocab"}).json()
    # 建立第一章
    r = reader.post(f"/api/books/{book['id']}/chapters",
                    json={"title": "第一章 開始", "text": "故事從這裡開始。"})
    assert r.status_code == 200, r.text
    assert len(r.json()["chapters"]) == 1
    assert r.json()["chapters"][0]["title"] == "第一章 開始"


# ---------- CSRF（production） ----------

def test_manual_book_blocked_without_csrf_in_production():
    owner = new_author("CSRF作者")
    login = TestClient(built_app)
    login.post("/api/auth/login", json={"username": owner.username, "password": "secret123"})
    orig = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        r = login.post("/api/books/manual", json={"title": "書"})
        assert r.status_code == 403, r.text
        assert "CSRF" in r.text
    finally:
        settings.APP_ENV = orig
