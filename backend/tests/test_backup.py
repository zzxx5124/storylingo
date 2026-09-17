"""scripts.backup 的備份／還原測試（不碰正式資料）。

測試把 settings 指向臨時目錄，建立一份含 DB 與檔案的小環境，
然後驗證 backup → restore 到新的臨時根目錄後資料一致。
"""
import os
import sqlite3

import pytest

from scripts import backup as backup_mod
from backend import settings


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """建立一份假正式環境，並把 settings 指向它。"""
    root = tmp_path / "app"
    data = root / "data"
    storage = root / "storage"
    books = storage / "books" / "b1"
    (data / "refs").mkdir(parents=True)
    books.mkdir(parents=True)

    monkeypatch.setattr(settings, "ROOT_DIR", str(root))
    monkeypatch.setattr(settings, "DATA_DIR", str(data))
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(root / "uploads"))
    monkeypatch.setattr(settings, "BOOKS_DIR", str(books))
    monkeypatch.setattr(settings, "PREVIEW_CACHE_DIR", str(storage / "preview_cache"))
    monkeypatch.setattr(settings, "DB_PATH", str(data / "app.db"))

    con = sqlite3.connect(str(data / "app.db"))
    try:
        con.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO demo(name) VALUES ('hello')")
        con.commit()
    finally:
        con.close()
    (books / "meta.json").write_text('{"id": "b1", "title": "測試書"}', encoding="utf-8")
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    (root / "uploads" / "x.txt").write_text("uploaded", encoding="utf-8")
    return root


def test_backup_creates_zip(staged, tmp_path):
    out = tmp_path / "out"
    arc = backup_mod.make_backup(str(out), keep=2)
    assert os.path.exists(arc)
    assert arc.endswith(".zip")


def test_backup_restore_roundtrip(staged, tmp_path, monkeypatch):
    out = tmp_path / "out"
    arc = backup_mod.make_backup(str(out), keep=2)

    # 還原到「全新」的專案根目錄（模擬另一台機器/搬移）
    new_root = tmp_path / "restored"
    new_data = new_root / "data"
    new_storage = new_root / "storage"
    monkeypatch.setattr(settings, "ROOT_DIR", str(new_root))
    monkeypatch.setattr(settings, "DATA_DIR", str(new_data))
    monkeypatch.setattr(settings, "STORAGE_DIR", str(new_storage))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(new_root / "uploads"))
    monkeypatch.setattr(settings, "BOOKS_DIR", str(new_storage / "books"))
    monkeypatch.setattr(settings, "PREVIEW_CACHE_DIR", str(new_storage / "preview_cache"))
    monkeypatch.setattr(settings, "DB_PATH", str(new_data / "app.db"))

    backup_mod.restore(arc)

    # DB 資料一致
    assert os.path.exists(str(new_data / "app.db"))
    con = sqlite3.connect(str(new_data / "app.db"))
    try:
        name = con.execute("SELECT name FROM demo WHERE id=1").fetchone()[0]
        assert name == "hello"
    finally:
        con.close()
    # storage 檔案一致
    assert (new_storage / "books" / "b1" / "meta.json").read_text(encoding="utf-8") == '{"id": "b1", "title": "測試書"}'
    # 根目錄獨立 uploads/ 一致
    assert (new_root / "uploads" / "x.txt").read_text(encoding="utf-8") == "uploaded"


def test_restore_dry_run_writes_nothing(staged, tmp_path, monkeypatch):
    out = tmp_path / "out"
    arc = backup_mod.make_backup(str(out), keep=2)
    new_root = tmp_path / "dry"
    monkeypatch.setattr(settings, "ROOT_DIR", str(new_root))
    monkeypatch.setattr(settings, "DATA_DIR", str(new_root / "data"))
    monkeypatch.setattr(settings, "STORAGE_DIR", str(new_root / "storage"))
    monkeypatch.setattr(settings, "DB_PATH", str(new_root / "data" / "app.db"))
    backup_mod.restore(arc, dry_run=True)
    assert not os.path.exists(str(new_root / "data" / "app.db"))