"""列印 authentication read-only inventory；不修改資料庫。"""
import json

from backend import db
from backend.services.auth_inventory import collect_auth_inventory


def main():
    db.init_db()
    print(json.dumps(collect_auth_inventory(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
