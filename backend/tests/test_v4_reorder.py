"""V4 Phase 2：stable chapter identity and reorder regression tests.

驗證：
- chapter_key 在建立章節時產生，且唯一。
- reorder 只更新 seq，保留 chapters.id / chapter_key 與文字內容。
- 重複 reorder 具有決定性（同一最終順序）。
- 刪除章節後剩餘章節身份不變。
- reorder/edit/delete 的 authorization/ownership 回歸。
"""
from backend import db
from backend.tests.conftest import new_author, upload_book, add_chapter


def _book_with_chapters(a, n=3):
    book = upload_book(a)
    for i in range(2, n + 1):
        add_chapter(a, book["id"], title=f"第{i}章", text=f"第 {i} 章正文內容，反覆敘述。" * 6)
    return book


def _snapshot(bid_id):
    return [
        {"id": c["id"], "key": c["chapter_key"], "seq": c["seq"],
         "title": c["title"], "text": c["text"], "chars": c["chars"]}
        for c in db.list_chapters(bid_id)
    ]


def test_chapter_key_generated_on_create(author=None):
    a = author or new_author()
    book = upload_book(a)
    add_chapter(a, book["id"], title="新增章")
    row = db.get_book_row(book["id"])
    keys = [c["chapter_key"] for c in db.list_chapters(row["id"])]
    assert keys and all(k and k.startswith("ck-") for k in keys)
    assert len(keys) == len(set(keys)), "chapter_key 必須全域唯一"


def test_reorder_preserves_identity_and_text(author=None):
    a = author or new_author()
    book = _book_with_chapters(a)
    row = db.get_book_row(book["id"])
    before = {c["id"]: (c["chapter_key"], c["title"], c["text"]) for c in db.list_chapters(row["id"])}

    r = a.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": [2, 0, 1]})
    assert r.status_code == 200, r.text

    after = db.list_chapters(row["id"])
    assert [c["seq"] for c in after] == [0, 1, 2]
    assert set(c["id"] for c in after) == set(before)
    for c in after:
        ident = before[c["id"]]
        assert c["chapter_key"] == ident[0], "reorder 不得變更 chapter_key"
        assert c["title"] == ident[1]
        assert c["text"] == ident[2]
        assert c["chars"] == len(ident[2])


def test_repeated_reorder_is_deterministic(author=None):
    a = author or new_author()
    book = _book_with_chapters(a, n=3)
    row = db.get_book_row(book["id"])
    ids0 = [c["id"] for c in db.list_chapters(row["id"])]

    def apply(order):
        r = a.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": order})
        assert r.status_code == 200, r.text
        return [c["id"] for c in db.list_chapters(row["id"])]

    # identity permutation 是 no-op
    assert apply([0, 1, 2]) == ids0

    # P 與其反 permutation 還原原始順序 → reorder 是純函數、無隨機漂移
    p = apply([2, 0, 1])
    assert sorted(p) == sorted(ids0)
    inv = {v: i for i, v in enumerate(p)}
    back = apply([inv[ids0[0]], inv[ids0[1]], inv[ids0[2]]])
    assert back == ids0

    # 相同初始狀態 + 相同 permutation → 相同結果（決定性，無隨機漂移）
    apply([0, 1, 2])
    r1 = apply([2, 0, 1])
    apply([1, 2, 0])  # [2,0,1] 的反 permutation，還原初始順序
    r2 = apply([2, 0, 1])
    assert r1 == r2

    # 連續 reorder 後 identity 仍完整
    for c in db.list_chapters(row["id"]):
        assert c["chapter_key"] and c["chapter_key"].startswith("ck-")
    assert sorted(c["id"] for c in db.list_chapters(row["id"])) == sorted(ids0)


def test_delete_chapter_preserves_remaining_identity(author=None):
    a = author or new_author()
    book = _book_with_chapters(a)
    row = db.get_book_row(book["id"])
    before = {c["id"]: c["chapter_key"] for c in db.list_chapters(row["id"])}
    target = db.list_chapters(row["id"])[1]

    r = a.delete(f"/api/books/{book['id']}/chapters/{target['seq']}")
    assert r.status_code == 200, r.text

    after = db.list_chapters(row["id"])
    assert [c["seq"] for c in after] == [0, 1]
    for c in after:
        assert c["chapter_key"] == before[c["id"]]


def test_reorder_rejects_invalid_order(author=None):
    a = author or new_author()
    book = _book_with_chapters(a)
    r = a.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": [0, 1]})
    assert r.status_code == 400
    r = a.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": [0, 1, 3]})
    assert r.status_code == 400
    r = a.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": "abc"})
    assert r.status_code == 400


def test_reorder_requires_ownership():
    a = new_author()
    book = _book_with_chapters(a)
    stranger = new_author("陌生人")
    r = stranger.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": [2, 0, 1]})
    assert r.status_code in (403, 404), r.text


def test_edit_chapter_requires_ownership():
    a = new_author()
    book = _book_with_chapters(a)
    stranger = new_author("陌生人2")
    r = stranger.put(f"/api/books/{book['id']}/chapters/0",
                     json={"text": "被竄改的內容，反覆。" * 20})
    assert r.status_code in (403, 404), r.text


def test_delete_chapter_requires_ownership():
    a = new_author()
    book = _book_with_chapters(a)
    stranger = new_author("陌生人3")
    r = stranger.delete(f"/api/books/{book['id']}/chapters/0")
    assert r.status_code in (403, 404), r.text


def test_chapter_key_survives_edit(author=None):
    a = author or new_author()
    book = _book_with_chapters(a)
    row = db.get_book_row(book["id"])
    ch0 = db.list_chapters(row["id"])[0]
    r = a.put(f"/api/books/{book['id']}/chapters/{ch0['seq']}",
              json={"text": "編輯後的正文內容，" + "修改了很多句子。" * 8})
    assert r.status_code == 200, r.text
    ch = db.get_chapter_by_id(ch0["id"])
    assert ch["chapter_key"] == ch0["chapter_key"]
