"""書庫：可見性、搜尋篩選、追書、分頁。"""
import pytest

from backend.tests.conftest import reg, new_author, new_admin, upload_book, publish_book


def _make_pub(**kw):
    author = kw.pop("owner", None) or new_author()
    return publish_book(author, **kw)


def test_public_hides_unpublished(client):
    new_author()
    # 有人上傳但未審核 → 一般使用者看不到
    a = new_author("作者甲")
    book = upload_book(a)
    r = client.get("/api/books")
    assert book["id"] not in [b["id"] for b in r.json()]


def test_approved_visible_to_public(client):
    _make_pub()
    r = client.get("/api/books")
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "approved"


def test_search_by_q(client):
    _make_pub()
    r = client.get("/api/books", params={"q": "不存在標題"})
    assert r.json() == []
    _make_pub(filename="魁拔志.txt")
    r = client.get("/api/books", params={"q": "魁拔志"})
    assert len(r.json()) == 1


def test_admin_sees_private():
    a = new_author()
    book = upload_book(a)
    adm = new_admin()
    bids = [b["id"] for b in adm.get("/api/books").json()]
    assert book["id"] in bids


def test_upload_over_10mb_rejected():
    """L0-4：單檔超過 10MB → 413，且不建立書。"""
    from backend.routers import books as books_mod

    a = new_author()
    too_big = b"x" * (books_mod.MAX_UPLOAD_BYTES + 1)
    files = {"file": ("超大.txt", too_big, "text/plain")}
    r = a.post("/api/books", files=files, data={"category": "小说"})
    assert r.status_code == 413
    # 沒有任何書被建立
    assert a.get("/api/books", params={"mine": 1}).json() == []


def test_upload_content_length_precheck():
    """L0-4：Content-Length 即超過上限時，未讀檔即 413（直接測 _read_upload）。"""
    from backend.routers import books as books_mod

    class _FakeFile:
        def read(self, n):
            raise AssertionError("不應讀取檔案內容")

    class _FakeUpload:
        headers = {"content-length": str(books_mod.MAX_UPLOAD_BYTES + 100)}
        file = _FakeFile()

    try:
        books_mod._read_upload(_FakeUpload())
        assert False, "應拋 413"
    except Exception as e:
        assert getattr(e, "status_code", None) == 413


def test_owner_can_manage_own():
    a = new_author()
    book = upload_book(a)
    d = a.get(f"/api/books/{book['id']}").json()
    assert d["isOwner"] is True
    assert d["canManage"] is True


def test_follow_unfollow(client):
    _make_pub()
    reg(client, "書迷")
    bid = client.get("/api/books").json()[0]["id"]
    assert client.post(f"/api/books/{bid}/follow").status_code == 200
    r = client.get("/api/books", params={"followed": 1})
    assert [b["id"] for b in r.json()] == [bid]
    assert client.post(f"/api/books/{bid}/unfollow").status_code == 200
    assert client.get("/api/books", params={"followed": 1}).json() == []


def test_follow_needs_login(client):
    _make_pub()
    bid = client.get("/api/books").json()[0]["id"]
    r = client.post(f"/api/books/{bid}/follow")
    assert r.status_code == 401


def test_paging():
    a = new_author()
    for i in range(3):
        publish_book(a, filename=f"第{i}本.txt")
    r = a.get("/api/books", params={"page": 1, "page_size": 2})
    data = r.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2
    assert isinstance(data["items"][0]["chapterCount"], int)


def test_serial_filter(client):
    _make_pub()
    assert client.get("/api/books", params={"serial": "完結"}).json() == []
    assert len(client.get("/api/books", params={"serial": "連載"}).json()) == 1