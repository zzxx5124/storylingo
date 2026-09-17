"""送審／審核／下架／恢復 狀態機（上傳＝draft，須送審）。"""
import pytest

from backend import db
from backend.tests.conftest import new_author, new_admin, upload_book


def _make():
    a = new_author()
    book = upload_book(a)
    return a, book


def _submit(a, bid):
    r = a.post(f"/api/books/{bid}/submit")
    assert r.status_code == 200, r.text


def _approve(adm, bid):
    r = adm.post(f"/api/admin/books/{bid}/approve")
    assert r.status_code == 200, r.text


def test_upload_is_draft():
    a, book = _make()
    row = db.get_book_row(book["id"])
    assert row["status"] == "draft"
    assert not row["published_at"]


def test_full_flow(client):
    a, book = _make()
    adm = new_admin()

    _submit(a, book["id"])          # draft → submitted
    _approve(adm, book["id"])       # submitted → approved → 公開
    assert client.get(f"/api/books/{book['id']}").status_code == 200
    row = db.get_book_row(book["id"])
    assert row["status"] == "approved" and row["published_at"]

    # 下架 → private；不可再公開瀏覽
    assert adm.post(f"/api/admin/books/{book['id']}/remove", json={"reason": "緊急內容治理測試"}).status_code == 200
    assert client.get(f"/api/books/{book['id']}").status_code == 404
    assert db.get_book_row(book["id"])["status"] == "removed"

    # 恢復上架
    assert adm.post(f"/api/admin/books/{book['id']}/restore").status_code == 200
    assert db.get_book_row(book["id"])["status"] == "approved"


def test_submit_from_draft_and_rejected():
    a, book = _make()
    adm = new_admin()

    _submit(a, book["id"])          # draft → submitted ok
    # submitted 不可重複送
    assert a.post(f"/api/books/{book['id']}/submit").status_code == 409

    # rejected → 可再送審
    adm.post(f"/api/admin/books/{book['id']}/reject", json={"reason": "再修"})
    assert a.post(f"/api/books/{book['id']}/submit").status_code == 200


def test_submit_from_approved_refused():
    a, book = _make()
    adm = new_admin()
    _submit(a, book["id"])
    _approve(adm, book["id"])
    assert a.post(f"/api/books/{book['id']}/submit").status_code == 409


def test_reject_transitions(client):
    a, book = _make()
    adm = new_admin()

    # draft 不可退件
    assert adm.post(f"/api/admin/books/{book['id']}/reject", json={"reason": "x"}).status_code == 409

    _submit(a, book["id"])          # submitted → reject ok
    assert adm.post(f"/api/admin/books/{book['id']}/reject", json={"reason": "缺章"}).status_code == 200
    row = db.get_book_row(book["id"])
    assert row["status"] == "rejected" and row["reject_reason"] == "缺章"
    assert client.get(f"/api/books/{book['id']}").status_code == 404

    # rejected → 再 reject 不行
    assert adm.post(f"/api/admin/books/{book['id']}/reject", json={"reason": "y"}).status_code == 409
    # approved 後沒有 active publish request，不可偽造第二次 reject。
    assert adm.post(f"/api/admin/books/{book['id']}/reject", json={"reason": "改"}).status_code == 409


def test_remove_only_approved():
    a, book = _make()
    adm = new_admin()
    _submit(a, book["id"])
    adm.post(f"/api/admin/books/{book['id']}/reject", json={"reason": "x"})
    assert adm.post(f"/api/admin/books/{book['id']}/remove", json={"reason": "非公開作品不應緊急隱藏"}).status_code == 409
    _submit(a, book["id"])
    _approve(adm, book["id"])
    assert adm.post(f"/api/admin/books/{book['id']}/remove", json={"reason": "管理員緊急隱藏"}).status_code == 200


def test_restore_only_removed():
    a, book = _make()
    adm = new_admin()
    _submit(a, book["id"])
    _approve(adm, book["id"])
    assert adm.post(f"/api/admin/books/{book['id']}/restore").status_code == 400
    adm.post(f"/api/admin/books/{book['id']}/remove", json={"reason": "恢復流程測試"})
    assert adm.post(f"/api/admin/books/{book['id']}/restore").status_code == 200


def test_owner_delete_removes_book():
    a, book = _make()
    assert a.delete(f"/api/books/{book['id']}").status_code == 200
    assert db.get_book_row(book["id"]) is None
    assert a.get(f"/api/books/{book['id']}").status_code == 404


def test_admin_can_delete_other_book():
    a, book = _make()
    adm = new_admin()
    assert adm.delete(f"/api/books/{book['id']}").status_code == 200
    assert db.get_book_row(book["id"]) is None
