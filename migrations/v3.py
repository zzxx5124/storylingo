"""CLI：python -m migrations.v3 [--force] 執行 storage→SQLite 遷移。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db  # noqa: E402
from backend.services import migration  # noqa: E402


def main():
    argv = sys.argv[1:]
    force = "--force" in argv
    db.init_db()
    report = migration.run_migrate()
    if force:
        print("--force：重新掃描並遷移尚未入庫的書（冪等）。")
    print("遷移完成：")
    print(f"  掃描資料夾：{report['total_dirs']}　成功：{report['migrated']}　跳過：{report['skipped']}")
    total_ch = total_an = total_ready = 0
    for r in report["books"]:
        errs = r.get("errors") or []
        flag = "OK" if not errs else "!!"
        total_ch += r.get("chapters", 0)
        total_an += r.get("analyzed", 0)
        total_ready += r.get("ready", 0)
        print(f"  [{flag}] {r['bid']}  章={r['chapters']} 分析={r['analyzed']} 就緒={r['ready']}"
              + (f"  問題：{'；'.join(errs)}" if errs else ""))
    print(f"  對帳：總章={total_ch} 分析={total_an} 就緒={total_ready}")
    problems = [r for r in report["books"] if r.get("errors")]
    print("  => " + ("PASS" if not problems else f"有 {len(problems)} 本書需人工檢視"))


if __name__ == "__main__":
    main()