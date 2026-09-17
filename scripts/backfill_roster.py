"""書級名冊回填 CLI（L0-1）。

在既有書名冊寫回修正「之前」已分析、books.speaker_info 仍為空的書上，
從 ready 的 chapter_analyses artifact 重建 voices／speaker_info／
speaker_chapters（不需重新消耗 AI 額度）。彙整規則與 analyze_chapter 一致，
已綁定聲線保留、只補缺失名冊鍵。

使用方式（在專案根目錄）：
    python -m scripts.backfill_roster [--bid BID ...] [--apply] [--force]

- 預設 dry-run：只列出會回填的書與內容摘要，不寫入。
- --apply   實際寫入（先跑一次 dry-run 確認）。
- --bid     只處理指定書（可重複）。
- --force   連 speaker_info 非空的書也重建（不建議，預設略過）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import settings  # noqa: E402
from backend.services import backfill_roster  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bid", action="append", default=None, help="只處理指定書（可重複）")
    parser.add_argument("--apply", action="store_true", help="實際寫入（預設只 dry-run）")
    parser.add_argument("--force", action="store_true", help="連 speaker_info 非空的書也重建")
    args = parser.parse_args()

    report = backfill_roster.run_backfill(
        bids=args.bid,
        dry_run=not args.apply,
        only_if_empty=not args.force,
    )
    print(f"dry_run={report['dryRun']}  scanned={report['scanned']}  "
          f"backfilled={report['backfilled']}  skipped={report['skipped']}")
    for b in report["books"]:
        if b.get("skipped"):
            print(f"  - {b['bid']}: SKIP ({b.get('reason')})")
        else:
            print(f"  - {b['bid']}: {'WRITE' if b.get('written') else 'WOULD-WRITE'} "
                  f"chaptersUsed={b.get('chaptersUsed')} speakers={b.get('speakers')}")
    if report["dryRun"]:
        print("（dry-run：未寫入任何資料。確認後加 --apply 實際執行。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())