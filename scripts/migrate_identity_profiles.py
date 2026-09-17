"""既有帳號 identity/profile migration；預設 dry-run，--apply 才寫入。"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import db
from backend import settings
from backend.services.migration import identity_profile_migration


def main():
    parser = argparse.ArgumentParser(description="盤點或套用 StoryLingo identity/profile backfill")
    parser.add_argument("--apply", action="store_true", help="在已檢查 dry-run 報告後套用 additive migration")
    args = parser.parse_args()
    if args.apply:
        db.init_db()
    elif not os.path.isfile(settings.DB_PATH):
        parser.error("dry-run 需要既有資料庫；不會自動建立或修改資料庫")
    report = identity_profile_migration(apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
