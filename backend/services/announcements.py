"""公告 aggregate、輸入驗證與 active delivery read model。

公告不是 banner，也不是 recipient-specific notification。這個模組只處理
公告自身的治理資料、固定角色 audience 與已登入帳號的版本 acknowledgement；
所有外部輸入在進入 router/DB 前先經過同一套 validator。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .. import db

AUDIENCE_MODES = ("everyone", "guest", "all_authenticated", "specific_roles")
DISPLAY_MODES = ("once_per_version", "once_per_session")
ROLES = db.SUPPORTED_ROLES
MAX_ACTIVE_CANDIDATES = 50
MAX_TITLE = 200
MAX_BODY = 10000
MAX_CTA_LABEL = 80
MAX_CTA_TARGET = 500
MAX_PRIORITY = 100
_MARKUP_RE = re.compile(r"<[^>]*>|(^|\n)\s{0,3}#{1,6}\s|\*\*|__|`", re.MULTILINE)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class AnnouncementVersionConflict(ValueError):
    """Optimistic config_version mismatch."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any, *, field: str, allow_empty: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_empty:
            return None
        raise ValueError(f"{field} 不可為空")
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError(f"{field} 必須是 UTC 時間")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} 必須是有效的 UTC 時間") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} 必須包含時區資訊")
    return utc_text(parsed)


def _text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必須是文字")
    result = value.strip()
    if required and not result:
        raise ValueError(f"{field} 不可為空")
    if len(result) > maximum:
        raise ValueError(f"{field} 不得超過 {maximum} 字")
    if _CONTROL_RE.search(result):
        raise ValueError(f"{field} 含有不允許的控制字元")
    return result


def plain_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    result = _text(value, field, maximum, required=required)
    if _MARKUP_RE.search(result):
        raise ValueError(f"{field} 僅支援純文字，不接受 HTML/Markdown 標記")
    return result


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


def safe_cta_target(value: Any) -> str:
    target = _text(value, "CTA 連結", MAX_CTA_TARGET)
    if not target:
        return ""
    lowered = target.casefold()
    if lowered.startswith(("javascript:", "data:", "vbscript:")):
        raise ValueError("CTA 連結 scheme 不安全")
    if target.startswith("#/"):
        if "//" in target or "\\" in target or any(ord(char) < 0x20 for char in target):
            raise ValueError("CTA 站內路由不合法")
        return target
    parsed = urlsplit(target)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("CTA 只允許站內 hash route 或 HTTPS 連結")
    return target


def _roles(payload: dict[str, Any], audience_mode: str) -> list[str]:
    raw = payload.get("roles", payload.get("audienceRoles", []))
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > len(ROLES):
        raise ValueError("specific_roles 必須提供固定角色清單")
    result = []
    for role in raw:
        if not isinstance(role, str) or role not in ROLES:
            raise ValueError("audience role 不合法")
        if role not in result:
            result.append(role)
    if audience_mode != "specific_roles" and result:
        raise ValueError("非 specific_roles audience 不可帶指定角色")
    if audience_mode == "specific_roles" and not result:
        raise ValueError("specific_roles 至少需要一個角色")
    return result if audience_mode == "specific_roles" else []


def validate_payload(payload: Any, current: dict | None = None, *, creating: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("公告資料格式不正確")
    current = current or {}
    def value(name: str, alternate: str | None = None, default: Any = None):
        if name in payload:
            return payload[name]
        if alternate and alternate in payload:
            return payload[alternate]
        return current.get(name, default)

    title = plain_text(value("title", default=""), "標題", MAX_TITLE, required=True)
    body = plain_text(value("bodyText", "body", current.get("body_text", "")), "內文", MAX_BODY, required=True)
    audience_mode = value("audienceMode", "audience_mode", "everyone")
    if audience_mode not in AUDIENCE_MODES:
        raise ValueError("audience mode 不合法")
    display_mode = value("displayMode", "display_mode", "once_per_version")
    if display_mode not in DISPLAY_MODES:
        raise ValueError("display mode 不合法")
    try:
        priority = int(value("priority", default=0))
    except (TypeError, ValueError) as error:
        raise ValueError("priority 必須是整數") from error
    if not -MAX_PRIORITY <= priority <= MAX_PRIORITY:
        raise ValueError(f"priority 必須介於 {-MAX_PRIORITY} 與 {MAX_PRIORITY}")
    start_at = parse_utc(value("startAt", "start_at"), field="start_at")
    end_at = parse_utc(value("endAt", "end_at"), field="end_at")
    if start_at and end_at and start_at >= end_at:
        raise ValueError("start_at 必須早於 end_at")
    cta_label = plain_text(value("ctaLabel", "cta_label", ""), "CTA 標籤", MAX_CTA_LABEL)
    cta_target = safe_cta_target(value("ctaTarget", "cta_target", ""))
    if bool(cta_label) != bool(cta_target):
        raise ValueError("CTA 標籤與連結必須成對提供")
    try:
        config_version = int(value("configVersion", "config_version", 1) or 1)
        display_version = int(value("displayVersion", "display_version", 1) or 1)
    except (TypeError, ValueError) as error:
        raise ValueError("公告版本不合法") from error
    if config_version < 1 or display_version < 1:
        raise ValueError("公告版本必須是正整數")
    return {
        "title": title,
        "body_text": body,
        "cta_label": cta_label,
        "cta_target": cta_target,
        "audience_mode": audience_mode,
        "display_mode": display_mode,
        "roles": _roles(payload, audience_mode),
        "priority": priority,
        "start_at": start_at,
        "end_at": end_at,
        "enabled": _bool(value("enabled", default=False if creating else bool(current.get("enabled")))) if creating or "enabled" in payload else bool(current.get("enabled")),
        "config_version": config_version,
        "display_version": display_version,
    }


def derived_status(row: dict, now: datetime | None = None) -> str:
    if row.get("archived_at"):
        return "archived"
    if not row.get("enabled"):
        return "disabled"
    now_value = utc_text(now or utc_now())
    if row.get("start_at") and row["start_at"] > now_value:
        return "scheduled"
    if row.get("end_at") and row["end_at"] <= now_value:
        return "expired"
    return "active"


def _role_list(announcement_id: int, con=None) -> list[str]:
    rows = (con or db._conn()).execute(
        "SELECT role FROM announcement_audience_roles WHERE announcement_id=? ORDER BY role", (announcement_id,)
    ).fetchall()
    return [row["role"] for row in rows]


def admin_dto(row: dict, *, con=None) -> dict:
    item = dict(row)
    roles = _role_list(int(item["id"]), con=con)
    return {
        "id": item["id"], "title": item["title"], "bodyText": item["body_text"],
        "ctaLabel": item.get("cta_label") or "", "ctaTarget": item.get("cta_target") or "",
        "audienceMode": item["audience_mode"], "roles": roles,
        "displayMode": item["display_mode"], "priority": int(item.get("priority") or 0),
        "startAt": item.get("start_at"), "endAt": item.get("end_at"),
        "enabled": bool(item.get("enabled")), "archivedAt": item.get("archived_at"),
        "displayVersion": int(item.get("display_version") or 1),
        "configVersion": int(item.get("config_version") or 1),
        "status": derived_status(item), "createdBy": item.get("created_by"),
        "updatedBy": item.get("updated_by"), "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
    }


def active_dto(row: dict) -> dict:
    return {
        "id": row["id"], "displayVersion": int(row.get("display_version") or 1),
        "title": row["title"], "bodyText": row["body_text"],
        "ctaLabel": row.get("cta_label") or "", "ctaTarget": row.get("cta_target") or "",
        "displayMode": row["display_mode"], "priority": int(row.get("priority") or 0),
        "startAt": row.get("start_at"), "endAt": row.get("end_at"),
    }


def _eligible_sql(user: dict | None) -> tuple[str, list[Any]]:
    account = None
    if user:
        account = db.get_user_by_id(int(user.get("id") or 0)) if not user.get("dev") else user
    if account and (account.get("account_status") or "active") == "active":
        role = account.get("role")
        return (
            "(a.audience_mode IN ('everyone','all_authenticated') OR "
            "(a.audience_mode='specific_roles' AND EXISTS "
            "(SELECT 1 FROM announcement_audience_roles ar WHERE ar.announcement_id=a.id AND ar.role=?)))",
            [role],
        )
    return ("a.audience_mode IN ('everyone','guest')", [])


def list_active(user: dict | None, *, now: datetime | None = None, limit: int = MAX_ACTIVE_CANDIDATES) -> list[dict]:
    now_value = utc_text(now or utc_now())
    limit = max(1, min(int(limit or MAX_ACTIVE_CANDIDATES), MAX_ACTIVE_CANDIDATES))
    audience_sql, audience_params = _eligible_sql(user)
    account = db.get_user_by_id(int(user.get("id") or 0)) if user and not user.get("dev") else user
    ack_sql = ""
    ack_params: list[Any] = []
    if account and (account.get("account_status") or "active") == "active":
        ack_sql = "AND NOT (a.display_mode='once_per_version' AND EXISTS (SELECT 1 FROM announcement_acknowledgements aa WHERE aa.announcement_id=a.id AND aa.account_id=? AND aa.announcement_version=a.display_version))"
        ack_params.append(account["id"])
    rows = db.query(
        "SELECT a.* FROM announcements a WHERE a.archived_at IS NULL AND a.enabled=1 "
        "AND (a.start_at IS NULL OR a.start_at<=?) AND (a.end_at IS NULL OR a.end_at>?) "
        f"AND {audience_sql} {ack_sql} "
        "ORDER BY a.priority DESC, CASE WHEN a.start_at IS NULL THEN 1 ELSE 0 END ASC, a.start_at ASC, a.id ASC LIMIT ?",
        (now_value, now_value, *audience_params, *ack_params, limit),
    )
    return [active_dto(dict(row)) for row in rows]


def create(payload: dict, actor_id: int) -> dict:
    data = validate_payload(payload, creating=True)
    now = utc_text(utc_now())
    with db.atomic() as con:
        cur = con.execute(
            "INSERT INTO announcements(title,body_text,cta_label,cta_target,audience_mode,display_mode,priority,start_at,end_at,enabled,display_version,config_version,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (data["title"], data["body_text"], data["cta_label"], data["cta_target"], data["audience_mode"], data["display_mode"], data["priority"], data["start_at"], data["end_at"], int(data["enabled"]), 1, 1, actor_id, actor_id, now, now),
        )
        announcement_id = cur.lastrowid
        for role in data["roles"]:
            con.execute("INSERT INTO announcement_audience_roles(announcement_id,role) VALUES(?,?)", (announcement_id, role))
        row = con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
        return admin_dto(dict(row), con=con)


def get(announcement_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM announcements WHERE id=?", (announcement_id,))
    return admin_dto(row) if row else None


def update(announcement_id: int, payload: dict, actor_id: int) -> dict:
    with db.atomic() as con:
        row = con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
        if not row:
            return None
        current = dict(row)
        expected = payload.get("expectedConfigVersion", payload.get("configVersion"))
        if expected is None:
            raise AnnouncementVersionConflict("請提供目前的 config_version")
        try:
            if int(expected) != int(current["config_version"]):
                raise AnnouncementVersionConflict("公告設定已被其他管理員更新，請重新載入")
        except (TypeError, ValueError) as error:
            raise AnnouncementVersionConflict("config_version 不合法") from error
        data = validate_payload(payload, current=current)
        now = utc_text(utc_now())
        con.execute(
            "UPDATE announcements SET title=?,body_text=?,cta_label=?,cta_target=?,audience_mode=?,display_mode=?,priority=?,start_at=?,end_at=?,enabled=?,config_version=config_version+1,updated_by=?,updated_at=? WHERE id=? AND config_version=?",
            (data["title"], data["body_text"], data["cta_label"], data["cta_target"], data["audience_mode"], data["display_mode"], data["priority"], data["start_at"], data["end_at"], int(data["enabled"]), actor_id, now, announcement_id, current["config_version"]),
        )
        con.execute("DELETE FROM announcement_audience_roles WHERE announcement_id=?", (announcement_id,))
        for role in data["roles"]:
            con.execute("INSERT INTO announcement_audience_roles(announcement_id,role) VALUES(?,?)", (announcement_id, role))
        return admin_dto(dict(con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()), con=con)


def lifecycle(announcement_id: int, action: str, actor_id: int) -> dict | None:
    now = utc_text(utc_now())
    with db.atomic() as con:
        row = con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
        if not row:
            return None
        if action == "enable":
            con.execute("UPDATE announcements SET enabled=1,config_version=config_version+1,updated_by=?,updated_at=? WHERE id=?", (actor_id, now, announcement_id))
        elif action == "disable":
            con.execute("UPDATE announcements SET enabled=0,config_version=config_version+1,updated_by=?,updated_at=? WHERE id=?", (actor_id, now, announcement_id))
        elif action == "reannounce":
            con.execute("UPDATE announcements SET display_version=display_version+1,config_version=config_version+1,updated_by=?,updated_at=? WHERE id=?", (actor_id, now, announcement_id))
        elif action == "archive":
            con.execute("UPDATE announcements SET archived_at=COALESCE(archived_at,?),archived_by=?,enabled=0,config_version=config_version+1,updated_by=?,updated_at=? WHERE id=?", (now, actor_id, actor_id, now, announcement_id))
        else:
            raise ValueError("不支援的公告 lifecycle action")
        updated = con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
        return admin_dto(dict(updated), con=con)


def acknowledge(announcement_id: int, account_id: int, display_version: int) -> dict:
    with db.atomic() as con:
        row = con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
        if not row or row["archived_at"]:
            raise LookupError("找不到公告")
        if row["display_mode"] != "once_per_version":
            raise ValueError("此公告不需要 authenticated acknowledgement")
        if display_version < 1 or display_version > int(row["display_version"]):
            raise ValueError("公告版本已失效")
        account = con.execute("SELECT role,account_status FROM users WHERE id=?", (account_id,)).fetchone()
        if not account or (account["account_status"] or "active") != "active":
            raise LookupError("帳號不可用")
        eligible = row["audience_mode"] in ("everyone", "all_authenticated")
        if row["audience_mode"] == "specific_roles":
            eligible = con.execute("SELECT 1 FROM announcement_audience_roles WHERE announcement_id=? AND role=?", (announcement_id, account["role"])).fetchone() is not None
        if not eligible:
            raise LookupError("找不到公告")
        con.execute("INSERT OR IGNORE INTO announcement_acknowledgements(announcement_id,account_id,announcement_version,acknowledged_at) VALUES(?,?,?,?)", (announcement_id, account_id, display_version, utc_text(utc_now())))
    return {"ok": True, "acknowledged": True, "announcementId": announcement_id, "displayVersion": display_version}
