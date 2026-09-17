"""Explicit, non-public Super Admin bootstrap for an existing Account.

Usage (development/staging only):
    python -m scripts.bootstrap_super_admin --account-id 12
"""
import argparse
import sys

from backend import db, settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="將指定既有 Account 升級為第一個 Super Admin")
    parser.add_argument("--account-id", required=True, type=int, help="canonical users.id；不得使用 username/email 猜測")
    parser.add_argument("--actor-id", type=int, default=None, help="可選的操作人 Account ID")
    args = parser.parse_args(argv)
    if settings.APP_ENV == "production":
        print("已拒絕：不得在 production 執行 Super Admin bootstrap", file=sys.stderr)
        return 2
    db.init_db()
    try:
        result = db.bootstrap_super_admin(args.account_id, actor_id=args.actor_id)
    except (ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Super Admin bootstrap 完成：account_id={result['id']} idempotent={bool(result.get('idempotent'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
