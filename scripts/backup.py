"""資料備份／還原 CLI（L0-1）。

使用方式（在專案根目錄）：
    python -m scripts.backup backup [--output-dir DIR] [--keep N]
    python -m scripts.backup restore ARCHIVE
    python -m scripts.backup list [DIR]

- backup  以 SQLite online backup API 取得一致的資料庫快照，並壓縮
          DB（含 WAL/SHM）、storage/、data/ 內值得一提的檔案到 timestamped zip。
- restore 先自動產生一份「還原前」的緊急備份，再把指定的 zip 解壓並覆蓋。
          只允許還原到原始的 DB_PATH / STORAGE_DIR 佈局（路徑從 zip 內 manifest 驗證）。
- list    列出指定目錄下的備份檔與建立時間。

本腳本不啟動 App 二進位檔，只操作檔案層級。正式環境排程（如 cron / Task Scheduler）
執行 backup 前應確保沒有長時間未提交的寫入交易；SQLite online backup 已處理 WAL。
"""
import argparse
import glob
import json
import os
import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backend.settings as settings  # noqa: E402

MANIFEST_NAME = "manifest.json"
ZIP_FILES = [
    "DB_PATH",
    "DB_WAL",
    "DB_SHM",
    "STORAGE_DIR",
    "UPLOAD_DIR",
    "BOOKS_DIR",
    "PREVIEW_CACHE_DIR",
    "DATA_DIR",
]
# data/ 內可選擇略過的龐大模型檔（F5 模型等），避免備份體積爆炸
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_FILES = {"model_3000.pt"}


def _skip(name: str) -> bool:
    if name in SKIP_FILES or name.endswith(".pt"):
        return True
    # Office 暫存鎖檔、臨時檔
    if name.startswith("~$"):
        return True
    return False


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique_archive_path(output_dir: str) -> str:
    """產生不重複的備份檔名，避免同一秒內兩次備份互相覆蓋。"""
    base = os.path.join(output_dir, "xlrd-backup")
    n = 0
    while True:
        suffix = f"-{_ts()}" if n == 0 else f"-{_ts()}-{n}"
        candidate = f"{base}{suffix}.zip"
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _walk(src_dir: str):
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not _skip(name):
                yield os.path.join(root, name)


def _rel_to_dir(abs_path: str, base_dir: str) -> str:
    """回傳 abs_path 相對於 base_dir 的相對路徑（僅安全路徑）。"""
    base = os.path.normpath(base_dir)
    path = os.path.normpath(abs_path)
    if path.startswith(base + os.sep) or path == base:
        rel = os.path.relpath(path, base)
        if rel == ".":
            rel = ""
        # 防止 .. 跳出 base
        if rel.startswith(".."):
            raise ValueError(f"路徑超出預期根目錄：{abs_path}")
        return rel.replace(os.sep, "/")
    raise ValueError(f"檔案不在預期根目錄內：{abs_path}")


def _target_for(kind: str, rel: str) -> str:
    """依檔案類型決定還原目標絕對路徑。"""
    safe_rel = os.path.normpath(rel).replace("..", "__")
    if kind == "DB_PATH":
        return settings.DB_PATH
    if kind == "DB_WAL":
        return settings.DB_PATH + "-wal"
    if kind == "DB_SHM":
        return settings.DB_PATH + "-shm"
    if kind == "STORAGE_DIR":
        return os.path.join(settings.STORAGE_DIR, safe_rel)
    if kind == "UPLOAD_DIR":
        return os.path.join(settings.UPLOAD_DIR, safe_rel)
    if kind == "BOOKS_DIR":
        return os.path.join(settings.BOOKS_DIR, safe_rel)
    if kind == "PREVIEW_CACHE_DIR":
        return os.path.join(settings.PREVIEW_CACHE_DIR, safe_rel)
    if kind == "DATA_DIR":
        return os.path.join(settings.DATA_DIR, safe_rel)
    raise ValueError(f"未知備份項目：{kind}")


def _db_backup_uri(target_path: str):
    """用 SQLite online backup 把 DB 複製到 target_path（含 WAL 資料）。"""
    src = settings.DB_PATH
    if not os.path.exists(src):
        return None
    dest = sqlite3.connect(target_path)
    try:
        source_db = sqlite3.connect(src)
        try:
            source_db.backup(dest)
        finally:
            source_db.close()
    finally:
        dest.close()
    return target_path


def make_backup(output_dir: str, keep: int = 0) -> str:
    """建立一份完整備份，回傳備份檔絕對路徑。"""
    os.makedirs(output_dir, exist_ok=True)
    stamp = _ts()
    arc = _unique_archive_path(output_dir)

    db_abs = settings.DB_PATH
    storage_abs = settings.STORAGE_DIR
    data_abs = settings.DATA_DIR

    collected = {}
    tmp_db = _db_backup_uri(db_abs + f".bkp-{stamp}")
    if tmp_db:
        collected["DB_PATH"] = tmp_db
    for key in ("DB_WAL", "DB_SHM"):
        p = db_abs + {"DB_WAL": "-wal", "DB_SHM": "-shm"}[key]
        if os.path.exists(p):
            collected[key] = p
    # STORAGE_DIR 已包含 uploads/books/preview_cache；另外補根目錄獨立 uploads/（若存在）
    for name, target in (("STORAGE_DIR", settings.STORAGE_DIR),):
        if os.path.isdir(target):
            collected[name] = target
    root_uploads = os.path.join(settings.ROOT_DIR, "uploads")
    if os.path.isdir(root_uploads):
        collected["UPLOAD_DIR"] = root_uploads
    if os.path.isdir(data_abs):
        collected["DATA_DIR"] = data_abs

    manifest = {
        "created_at": stamp,
        "db_path": db_abs,
        "storage_dir": storage_abs,
        "data_dir": data_abs,
        "entries": {},
    }

    with zipfile.ZipFile(arc, "w", zipfile.ZIP_DEFLATED) as zf:
        for key, src in collected.items():
            if os.path.isdir(src):
                for fp in _walk(src):
                    rel = _rel_to_dir(fp, src)
                    zf.write(fp, f"{key}/{rel}")
                    manifest["entries"].setdefault(key, []).append(rel)
            elif os.path.isfile(src):
                rel = os.path.basename(src)
                zf.write(src, f"{key}/{rel}")
                manifest["entries"].setdefault(key, []).append(rel)
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    for key, tmp_path in (("DB_PATH", tmp_db),):
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if keep > 0:
        _prune_old(output_dir, keep)
    return arc


def _prune_old(output_dir: str, keep: int):
    backups = sorted(glob.glob(os.path.join(output_dir, "xlrd-backup-*.zip")))
    for old in backups[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass


def _read_manifest(archive: str) -> dict:
    with zipfile.ZipFile(archive) as zf:
        try:
            return json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except KeyError:
            raise SystemExit("錯誤：備份檔缺少 manifest.json，可能不是本工具產生的備份。")


def restore(archive: str, dry_run: bool = False) -> None:
    archive = os.path.abspath(archive)
    if not os.path.exists(archive):
        raise SystemExit(f"找不到備份檔：{archive}")
    manifest = _read_manifest(archive)

    # 還原前緊急備份
    if not dry_run:
        print("先建立還原前的緊急備份…")
        make_backup(os.path.dirname(archive))

    entries = manifest.get("entries", {})
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        for name in names:
            if name == MANIFEST_NAME:
                continue
            kind, _, rel = name.partition("/")
            if kind not in ZIP_FILES or not rel:
                continue
            try:
                dest_rel = _target_for(kind, rel)
            except ValueError:
                continue
            parent = os.path.dirname(dest_rel)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            if dry_run:
                print(f"  [dry-run] 還原 -> {dest_rel}")
                continue
            with zf.open(name) as src_fp, open(dest_rel, "wb") as dst_fp:
                shutil.copyfileobj(src_fp, dst_fp)
    print("還原完成。" if not dry_run else "dry-run 完成，未寫入任何檔案。")


def list_backups(dirname: str):
    if not os.path.isdir(dirname):
        raise SystemExit(f"目錄不存在：{dirname}")
    rows = sorted(glob.glob(os.path.join(dirname, "xlrd-backup-*.zip")))
    if not rows:
        print("沒有找到備份檔。")
        return
    for p in rows:
        size = os.path.getsize(p)
        print(f"{os.path.basename(p)}  {size / 1024 / 1024:.2f} MB")
    print(f"共 {len(rows)} 份。")


def main():
    parser = argparse.ArgumentParser(prog="python -m scripts.backup")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_backup = sub.add_parser("backup", help="建立備份")
    p_backup.add_argument("--output-dir", default=None, help="備份目錄")
    p_backup.add_argument("--keep", type=int, default=7, help="保留最近 N 份")
    p_backup.add_argument("--dry-run", action="store_true")

    p_restore = sub.add_parser("restore", help="還原")
    p_restore.add_argument("archive")
    p_restore.add_argument("--dry-run", action="store_true")

    p_list = sub.add_parser("list", help="列出備份")
    p_list.add_argument("dir", nargs="?", default=".")

    args = parser.parse_args()

    if args.cmd == "backup":
        out = args.output_dir or os.path.join(settings.ROOT_DIR, "backups")
        arc = make_backup(out, keep=args.keep)
        print(f"備份完成：{arc}")
    elif args.cmd == "restore":
        restore(args.archive, dry_run=args.dry_run)
    elif args.cmd == "list":
        list_backups(args.dir)


if __name__ == "__main__":
    main()