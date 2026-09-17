"""FE-017：章節版本歷史 與 作者統計端點。"""
from backend import db
from backend.tests.conftest import new_author, new_user, upload_book, add_chapter


def test_revisions_created_on_edit_and_restore():
    a, book = _book()
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    row = db.get_book_row(book["id"])
    original = db.get_chapter(row["id"], seq)["text"]

    # 第一次編輯 → 建立版本（來源文字 = 編輯前內容）
    r = a.put(f"/api/books/{book['id']}/chapters/{seq}", json={"text": "  改版一。" * 10})
    assert r.status_code == 200, r.text
    assert r.json()["revisionId"] is not None

    # 第二次編輯 → 兩版，最新在前
    a.put(f"/api/books/{book['id']}/chapters/{seq}", json={"text": "  改版二。" * 10})
    revs = a.get(f"/api/books/{book['id']}/chapters/{seq}/revisions").json()["revisions"]
    assert len(revs) == 2
    assert revs[0]["id"] > revs[1]["id"]  # 最新（第一次編輯前的「改版一」）在前

    # 較舊的一筆 = 原文；還原後文字回到原文
    target = revs[1]
    r = a.post(f"/api/books/{book['id']}/chapters/{seq}/revisions/{target['id']}/restore")
    assert r.status_code == 200, r.text
    assert db.get_chapter(row["id"], seq)["text"] == original

    # 較新的一筆 = 第一次編輯前的「改版一」
    target2 = revs[0]
    a.post(f"/api/books/{book['id']}/chapters/{seq}/revisions/{target2['id']}/restore")
    ch = db.get_chapter(row["id"], seq)
    assert "改版一" in ch["text"]


def test_revision_only_saved_when_text_changes():
    a, book = _book()
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    row = db.get_book_row(book["id"])
    cur = db.get_chapter(row["id"], seq)["text"]
    r = a.put(f"/api/books/{book['id']}/chapters/{seq}", json={"text": cur})
    assert r.json()["revisionId"] is None
    revs = a.get(f"/api/books/{book['id']}/chapters/{seq}/revisions").json()["revisions"]
    assert len(revs) == 0


def test_revisions_capped_at_20():
    a, book = _book()
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    for i in range(25):
        a.put(f"/api/books/{book['id']}/chapters/{seq}", json={"text": f"  第 {i} 次修改。" * 5})
    revs = a.get(f"/api/books/{book['id']}/chapters/{seq}/revisions").json()["revisions"]
    assert len(revs) == 20


def test_non_owner_cannot_view_revisions():
    a, book = _book()
    other = new_author(_u("路人"))
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    a.put(f"/api/books/{book['id']}/chapters/{seq}", json={"text": "  別人的修改。" * 5})
    r = other.get(f"/api/books/{book['id']}/chapters/{seq}/revisions")
    assert r.status_code in (401, 403)


def test_author_stats_aggregate():
    a, book = _book()
    reader = new_user(_u("读者"))
    book_row = db.get_book_row(book["id"])
    uid = db.get_user_by_name(reader.username)["id"]
    db.execute("INSERT INTO follows(user_id, book_id, created_at) VALUES(?,?,?)", (uid, book_row["id"], db.ts()))
    db.execute("INSERT INTO book_events(user_id, book_id, event_type, created_at) VALUES(?,?,?,?)",
               (uid, book_row["id"], "read", db.ts()))
    db.execute("INSERT INTO book_events(user_id, book_id, event_type, created_at) VALUES(?,?,?,?)",
               (uid, book_row["id"], "complete", db.ts()))

    r = a.get(f"/api/books/{book['id']}/stats")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["reads"] >= 1
    assert s["completions"] >= 1
    assert s["chapters"] == 1
    assert 0 <= s["completionRate"] <= 1
    assert s["follows"] == 1


def test_author_stats_counts_ready_audio():
    a, book = _book()
    row = db.get_book_row(book["id"])
    db.update_chapter(row["id"], 0, {"audio": "ready"})

    result = a.get(f"/api/books/{book['id']}/stats")
    assert result.status_code == 200, result.text
    assert result.json()["audioReady"] == 1
    assert result.json()["audioRatio"] == 1


def _book():
    a = new_author()
    b = upload_book(a)
    return a, b


_counter = {"n": 0}


def _u(name):
    _counter["n"] += 1
    return f"{name}{_counter['n']}"
