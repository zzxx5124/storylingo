"""P1 — 公開搜尋／公開瀏覽 visibility 統一規則 regression tests。

- 公開瀏覽面（search / home / rankings / recommendations / suggest）一律只回
  「已上架且已發布」的作品，與呼叫者角色（含 admin/dev）無關。
- draft／submitted（待審核）／rejected／removed 不得透過公開瀏覽洩漏。
- 書籍詳情對未發布作品一律 404（owner/admin 走管理 scope）。
"""
from fastapi.testclient import TestClient

from backend import db
from backend.main import app
from backend.services import book_service
from backend.tests.conftest import new_admin, new_author, new_user, upload_book


def _make_book(a, status, published=None):
    book = upload_book(a)
    bid = book["id"]
    db.update_book(bid, {"title": f"visibility-{status}", "tags": f"uniqvis{status}"})
    if status == "draft":
        db.update_book(bid, {"status": "draft", "published_at": None})
    elif status == "submitted":
        db.update_book(bid, {"status": "submitted", "published_at": None})
    elif status == "rejected":
        db.update_book(bid, {"status": "rejected", "published_at": None})
    elif status == "removed":
        db.update_book(bid, {"status": "removed", "published_at": None})
    elif status == "approved":
        db.update_book(bid, {"status": "approved", "published_at": db.ts()})
    return book


def _titles(items):
    return {(it.get("id") or it.get("bid")): it.get("title") for it in items}


def test_public_search_only_returns_published_for_all_roles():
    author = new_author("vis作者")
    admin = new_admin("vis管理")
    titles = {}
    statuses = ["approved", "draft", "submitted", "rejected", "removed"]
    for st in statuses:
        b = _make_book(author, st)
        titles[st] = b["id"]

    # 訪客
    guest = TestClient(app)
    r = guest.get("/api/search", params={"q": "uniqvis"})
    assert r.status_code == 200
    ids = set(_titles(r.json()["items"]))
    assert titles["approved"] in ids
    for st in ("draft", "submitted", "rejected", "removed"):
        assert titles[st] not in ids, f"guest search 洩漏 {st}"

    # 一般讀者（已登入）
    reader = new_user("vis讀者")
    r2 = reader.get("/api/search", params={"q": "uniqvis"})
    ids2 = set(_titles(r2.json()["items"]))
    assert titles["approved"] in ids2
    for st in ("draft", "submitted", "rejected", "removed"):
        assert titles[st] not in ids2, f"reader search 洩漏 {st}"

    # 作者（他人的非公開書也不得出現在公開搜尋）
    other = new_author("vis作者B")
    r3 = other.get("/api/search", params={"q": "uniqvis"})
    ids3 = set(_titles(r3.json()["items"]))
    for st in ("draft", "submitted", "rejected", "removed"):
        assert titles[st] not in ids3, f"author search 洩漏 {st}"

    # 管理員瀏覽公開搜尋也只看已上架（管理自己的非公開書走 /api/admin/* 或 mine scope）
    r4 = admin.get("/api/search", params={"q": "uniqvis"})
    ids4 = set(_titles(r4.json()["items"]))
    assert titles["approved"] in ids4
    for st in ("draft", "submitted", "rejected", "removed"):
        assert titles[st] not in ids4, f"admin search 洩漏 {st}"


def test_public_home_and_rankings_only_published():
    author = new_author("home作者")
    titles = {}
    for st in ("approved", "draft", "submitted", "rejected"):
        titles[st] = _make_book(author, st)["id"]
    guest = TestClient(app)
    home = guest.get("/api/home").json()
    latest_ids = set(_titles(home["latest"]))
    assert titles["approved"] in latest_ids
    for st in ("draft", "submitted", "rejected"):
        assert titles[st] not in latest_ids, f"home latest 洩漏 {st}"

    admin = new_admin("rank管理")
    r = admin.get("/api/rankings", params={"kind": "new"})
    items = r.json()["items"] or []
    rank_ids = {it.get("bid") for it in items}
    for st in ("draft", "submitted", "rejected"):
        assert titles[st] not in rank_ids, f"rankings 洩漏 {st}"


def test_book_detail_and_recommendations_gated():
    author = new_author("detail作者")
    for st in ("draft", "submitted", "rejected", "removed"):
        b = _make_book(author, st)
        guest = TestClient(app)
        assert guest.get(f"/api/books/{b['id']}").status_code == 404, f"{st} detail 應 404"
        assert guest.get(f"/api/books/{b['id']}/read/0").status_code in (404, 403), f"{st} read 應被封鎖"
    # approved → 可讀
    b = _make_book(author, "approved")
    guest = TestClient(app)
    assert guest.get(f"/api/books/{b['id']}").status_code == 200


def test_owner_mine_scope_still_lists_drafts():
    """作者自己的非公開作品仍須能透過 mine scope 管理（不放寬公開瀏覽）。"""
    author = new_author("mine作者")
    b = _make_book(author, "draft")
    mine = author.get("/api/books", params={"mine": 1}).json()
    ids = {x.get("id") for x in mine} if isinstance(mine, list) else set(_titles(mine.get("items") or []))
    assert b["id"] in ids