"""逐書建立 speaker entity registry；預設 dry-run，--apply 才寫入。"""
import argparse
import json

from backend import db
from backend.services.character_resolution import migrate_book


def main():
    parser = argparse.ArgumentParser(description="Migrate one book to character identity registry")
    parser.add_argument("--bid", required=True, help="指定書籍 bid")
    parser.add_argument("--apply", action="store_true", help="實際寫入 registry/event")
    parser.add_argument("--rollback-snapshot", type=int, help="還原指定 migration snapshot")
    args = parser.parse_args()
    db.init_db()
    if args.rollback_snapshot:
        from backend.services.character_resolution import rollback_migration
        result = rollback_migration(args.bid, args.rollback_snapshot)
    else:
        result = migrate_book(args.bid, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
