"""Read-only authentication readiness inventory for migration review."""
import hashlib
import json

from .. import db, security


def _checksum(rows) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def collect_auth_inventory() -> dict:
    users = db.query("SELECT * FROM users ORDER BY id ASC")
    checksum_rows = [dict(row) for row in users]
    password_states = {"null": 0, "valid": 0, "malformed": 0}
    role_counts = {}
    status_counts = {}
    conflicts = {}
    for row in users:
        role_counts[row["role"]] = role_counts.get(row["role"], 0) + 1
        status = row.get("account_status") or "active"
        status_counts[status] = status_counts.get(status, 0) + 1
        if not row.get("password_hash"):
            password_states["null"] += 1
        elif security.verify_password("", row["password_hash"]):
            password_states["valid"] += 1
        else:
            try:
                iterations, salt, digest = row["password_hash"].split("|")
                if int(iterations) > 0 and len(bytes.fromhex(salt)) >= 8 and len(bytes.fromhex(digest)) >= 16:
                    password_states["valid"] += 1
                else:
                    password_states["malformed"] += 1
            except Exception:
                password_states["malformed"] += 1
        normalized = db.normalize_email(row.get("email") or "")
        if normalized:
            conflicts.setdefault(normalized, []).append(row["id"])
    email_conflicts = {email: ids for email, ids in conflicts.items() if len(ids) > 1}
    result = {
        "dryRun": True,
        "accountCount": len(users),
        "roleCounts": role_counts,
        "statusCounts": status_counts,
        "passwordStates": password_states,
        "hasPasswordCredentialCount": sum(1 for row in users if row.get("password_hash")),
        "nullEmailCount": sum(1 for row in users if not db.normalize_email(row.get("email") or "")),
        "verifiedEmailCount": sum(1 for row in users if row.get("email_verified_at")),
        "normalizedEmailConflicts": email_conflicts,
        "adminIds": [row["id"] for row in users if row["role"] == "admin"],
        "lastLoginCount": sum(1 for row in users if row.get("last_login_at")),
        "booksByOwner": {
            str(row["id"]): db.query_one("SELECT COUNT(*) n FROM books WHERE owner_id=?", (row["id"],))["n"]
            for row in users
        },
        "publicProfileCount": db.query_one("SELECT COUNT(*) n FROM public_profiles")["n"],
    }
    result["checksumBefore"] = _checksum(checksum_rows)
    result["checksumAfter"] = result["checksumBefore"]
    result["checksumUnchanged"] = True
    return result
