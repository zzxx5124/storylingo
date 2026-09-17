"""章節管理：增／編輯重置產物／刪除／重排序。"""
import os
import hashlib

from backend import db, storage
from backend.tests.conftest import new_author, upload_book, add_chapter, reg


def _book(a=None):
    a = a or new_author()
    book = upload_book(a)
    return a, book


def _simp_outputs(row, bid, seq):
    """模擬分析＋音訊已產出：寫檔並設定 DB 狀態。"""
    os.makedirs(storage.chapter_dir(bid), exist_ok=True)
    os.makedirs(storage.audio_dir(bid), exist_ok=True)
    text = db.get_chapter(row["id"], seq)["text"]
    chash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    storage.save_chapter_analysis(bid, seq, {"segments": []})
    with open(storage.chapter_timing_path(bid, seq), "w", encoding="utf-8") as f:
        f.write("[]")
    with open(storage.chapter_audio_path(bid, seq), "wb") as f:
        f.write(b"MPG\x01fake")
    db.update_chapter(row["id"], seq, {
        "status": "analyzed", "audio": "ready", "generated_at": db.ts(),
        "analysed_hash": chash, "text_hash": chash,
    })


def test_add_and_edit_chapter():
    a, book = _book()
    d0 = a.get(f"/api/books/{book['id']}").json()
    n0 = len(d0["chapters"])
    add_chapter(a, book["id"])
    d1 = a.get(f"/api/books/{book['id']}").json()
    assert len(d1["chapters"]) == n0 + 1


def test_edit_text_invalidates_outputs():
    a, book = _book()
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    row = db.get_book_row(book["id"])
    _simp_outputs(row, book["id"], seq)
    assert os.path.exists(storage.chapter_audio_path(book["id"], seq))

    r = a.put(f"/api/books/{book['id']}/chapters/{seq}",
              json={"text": "  全新的正文內容，" + "修改了很多句子。" * 8})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["changed"] is True
    assert set(data["removedOutputs"]) >= {"timing", "audio"}

    ch = db.get_chapter(row["id"], seq)
    assert ch["status"] == "pending"
    assert ch["audio"] == "none"
    # 檔案真的被移除
    assert not os.path.exists(storage.chapter_audio_path(book["id"], seq))


def test_edit_unchanged_no_invalidate():
    a, book = _book()
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    row = db.get_book_row(book["id"])
    _simp_outputs(row, book["id"], seq)
    r = a.put(f"/api/books/{book['id']}/chapters/{seq}",
              json={"text": db.get_chapter(row["id"], seq)["text"]})
    data = r.json()
    assert data["changed"] is False
    assert data["removedOutputs"] == []


def test_delete_chapter_and_reorder():
    a, book = _book()
    for i in range(2, 5):
        add_chapter(a, book["id"], title=f"第{i}章", text=f" 第 {i} 章正文內容。" * 5)
    d = a.get(f"/api/books/{book['id']}").json()
    seqs = [c["seq"] for c in d["chapters"]]
    assert seqs == [0, 1, 2, 3]

    # 重排序：0,2,1,3（把第2/第3章對調）
    r = a.put(f"/api/books/{book['id']}/chapters/reorder", json={"order": [0, 2, 1, 3]})
    assert r.status_code == 200
    titles = [c["title"] for c in r.json()["chapters"]]
    assert titles == ["訪客。", "第3章", "第2章", "第4章"]

    # 刪除某一章後 seq 重新連續
    r = a.delete(f"/api/books/{book['id']}/chapters/3")
    assert r.status_code == 200
    seqs = [c["seq"] for c in r.json()["chapters"]]
    assert len(seqs) == 3
    assert seqs == sorted(seqs)


def test_cannot_delete_last_chapter():
    a, book = _book()
    seq = a.get(f"/api/books/{book['id']}").json()["chapters"][0]["seq"]
    r = a.delete(f"/api/books/{book['id']}/chapters/{seq}")
    assert r.status_code == 400


def test_guest_cannot_access_private_chapter(client):
    a, book = _book()
    r = client.get(f"/api/books/{book['id']}/chapters/0")
    assert r.status_code == 404
