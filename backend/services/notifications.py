"""Account-owned、typed、可去重的站內通知 persistence service。"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .. import db

EVENT_CATEGORIES = {
    "author_application.approved": "author",
    "author_application.rejected": "author",
    "content_request.approved": "review",
    "content_request.rejected": "review",
    "content_request.invalidated": "review",
    "content_request.cancelled_by_operator": "review",
    "audiobook.authorized": "audiobook",
    "generation.ready": "generation",
    "generation.failed": "generation",
    "generation.cancelled": "generation",
    "chapter.published": "content",
    "account.role_changed": "account",
    # 舊資料的相容轉接只能由 server-side adapter 使用，不是 client API。
    "legacy.notice": "system",
}
FILTERS = {"all", "unread", "read"}
MAX_TITLE = 120
MAX_BODY = 1000
MAX_TARGET = 120
MAX_SOURCE = 160
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
ALLOWED_TARGETS = {
    "", "book", "chapter", "content_request", "generation_operation",
    "shelf", "requests", "notifications",
}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class NotificationValidationError(ValueError):
    """通知 event/source/target 不符合 backend contract。"""


def _safe_text(value: Any, maximum: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise NotificationValidationError(f"{field} 必須是文字")
    value = value.strip()
    if _CONTROL_RE.search(value):
        raise NotificationValidationError(f"{field} 含有不允許的控制字元")
    return value[:maximum]


def _safe_ref(value: Any, field: str, maximum: int = MAX_SOURCE) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise NotificationValidationError(f"{field} 不合法")
    value = str(value)
    if len(value) > maximum or not re.fullmatch(r"[A-Za-z0-9:_-]+", value):
        raise NotificationValidationError(f"{field} 不合法")
    return value


def _safe_target(target_type: Any, target_id: Any, target_route: Any) -> tuple[str, str | None, str]:
    target_type = _safe_text(target_type, 40, "target_type")
    if target_type not in ALLOWED_TARGETS:
        raise NotificationValidationError("通知目標類型不合法")
    target_id = _safe_ref(target_id, "target_id", MAX_TARGET)
    target_route = _safe_text(target_route, 500, "target_route")
    if target_route:
        if (not target_route.startswith("#/") or "//" in target_route or "\\" in target_route
                or re.search(r"[\x00-\x1f]", target_route)):
            raise NotificationValidationError("通知目標路由不合法")
    return target_type, target_id, target_route


def _scope_sql(user_id: int, extra: str = "") -> tuple[str, tuple[int, int]]:
    return ("(recipient_account_id=? OR (recipient_account_id IS NULL AND user_id=?))" + extra,
            (int(user_id), int(user_id)))


def _row_item(row: dict) -> dict:
    title = row.get("title_snapshot") or row.get("title") or ""
    body = row.get("body_snapshot") if row.get("body_snapshot") is not None else row.get("body") or ""
    route = row.get("target_route") or row.get("link") or ""
    return {
        "id": row["id"],
        "recipientAccountId": row.get("recipient_account_id") or row.get("user_id"),
        "eventType": row.get("event_type") or row.get("kind") or "legacy.notice",
        "category": row.get("category") or "system",
        "title": title,
        "body": body,
        "titleSnapshot": title,
        "bodySnapshot": body,
        "targetType": row.get("target_type") or "",
        "targetId": row.get("target_id"),
        "targetRoute": route,
        "sourceType": row.get("source_type") or "",
        "sourceId": row.get("source_id"),
        "sourceRequestId": row.get("source_request_id"),
        "sourceGenerationOperationId": row.get("source_generation_operation_id"),
        "sourceBookId": row.get("source_book_id"),
        "createdAt": row.get("created_at"),
        "readAt": row.get("read_at"),
        # 相容既有前端與舊 API consumer 的 snake_case 欄位。
        "kind": row.get("kind") or row.get("event_type") or "system",
        "link": route,
        "created_at": row.get("created_at"),
        "read_at": row.get("read_at"),
    }


def _insert(con, *, recipient_account_id: int, event_type: str, category: str | None,
            title: str, body: str, dedupe_key: str, target_type: Any = "",
            target_id: Any = None, target_route: Any = "", source_type: Any = None,
            source_id: Any = None, source_request_id: Any = None,
            source_generation_operation_id: Any = None, source_book_id: Any = None,
            legacy_kind: str | None = None) -> dict:
    if event_type not in EVENT_CATEGORIES:
        raise NotificationValidationError("不支援的通知事件")
    expected_category = EVENT_CATEGORIES[event_type]
    if category and category != expected_category:
        raise NotificationValidationError("通知分類與事件不一致")
    title = _safe_text(title, MAX_TITLE, "通知標題")
    body = _safe_text(body, MAX_BODY, "通知內容")
    dedupe_key = _safe_text(dedupe_key, 240, "dedupe_key")
    if not dedupe_key:
        raise NotificationValidationError("通知必須有 dedupe_key")
    target_type, target_id, target_route = _safe_target(target_type, target_id, target_route)
    source_type = _safe_ref(source_type, "source_type")
    source_id = _safe_ref(source_id, "source_id")
    row = con.execute("SELECT id FROM users WHERE id=?", (recipient_account_id,)).fetchone()
    if not row:
        raise NotificationValidationError("通知收件帳號不存在")
    legacy_kind = _safe_text(legacy_kind or event_type, 80, "kind")
    now = db.ts()
    con.execute(
        "INSERT INTO notifications(user_id,kind,title,body,link,read_at,created_at,recipient_account_id,"
        "event_type,category,title_snapshot,body_snapshot,target_type,target_id,target_route,source_type,source_id,"
        "source_request_id,source_generation_operation_id,source_book_id,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(recipient_account_id,dedupe_key) DO NOTHING",
        (recipient_account_id, legacy_kind, title, body, target_route, None, now, recipient_account_id,
         event_type, expected_category, title, body, target_type, target_id, target_route, source_type,
         source_id, source_request_id, source_generation_operation_id, source_book_id, dedupe_key),
    )
    result = con.execute("SELECT * FROM notifications WHERE recipient_account_id=? AND dedupe_key=?",
                         (recipient_account_id, dedupe_key)).fetchone()
    return _row_item(dict(result))


def create_notification(recipient_account_id: int, event_type: str, category: str | None,
                        title: str, body: str = "", *, dedupe_key: str,
                        target_type: Any = "", target_id: Any = None, target_route: Any = "",
                        source_type: Any = None, source_id: Any = None,
                        source_request_id: Any = None, source_generation_operation_id: Any = None,
                        source_book_id: Any = None, con=None, legacy_kind: str | None = None) -> dict:
    kwargs = {
        "recipient_account_id": recipient_account_id, "event_type": event_type,
        "category": category, "title": title, "body": body, "dedupe_key": dedupe_key,
        "target_type": target_type, "target_id": target_id, "target_route": target_route,
        "source_type": source_type, "source_id": source_id,
        "source_request_id": source_request_id,
        "source_generation_operation_id": source_generation_operation_id,
        "source_book_id": source_book_id, "legacy_kind": legacy_kind,
    }
    if con is not None:
        return _insert(con, **kwargs)
    with db.atomic() as transaction:
        return _insert(transaction, **kwargs)


def create_notifications(recipient_account_ids: list[int], event_type: str, category: str | None,
                         title: str, body: str = "", *, dedupe_key: str, **kwargs) -> list[dict]:
    results = []
    with db.atomic() as con:
        for recipient in list(dict.fromkeys(int(item) for item in recipient_account_ids))[:100]:
            results.append(create_notification(recipient, event_type, category, title, body,
                                               dedupe_key=dedupe_key, con=con, **kwargs))
    return results


def create_legacy_notification(user_id: int, kind: str, title: str, body: str = "", link: str = "") -> dict:
    digest = hashlib.sha256(f"{user_id}|{kind}|{title}|{body}|{link}".encode("utf-8")).hexdigest()[:32]
    return create_notification(user_id, "legacy.notice", "system", title, body,
                               dedupe_key=f"legacy:{digest}", target_route=link, legacy_kind=kind)


def list_notifications(user_id: int, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE,
                       filter_name: str = "all", category: str = "") -> dict:
    if filter_name not in FILTERS:
        raise NotificationValidationError("通知篩選條件不合法")
    if category and category not in set(EVENT_CATEGORIES.values()):
        raise NotificationValidationError("通知分類不合法")
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    where, params = _scope_sql(user_id)
    clauses = [where]
    if filter_name == "unread":
        clauses.append("read_at IS NULL")
    elif filter_name == "read":
        clauses.append("read_at IS NOT NULL")
    if category:
        clauses.append("category=?")
        params += (category,)
    condition = " AND ".join(f"({clause})" for clause in clauses)
    total = db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE " + condition, params)["n"]
    rows = db.query("SELECT * FROM notifications WHERE " + condition +
                    " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                    (*params, page_size, (page - 1) * page_size))
    total_pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": [_row_item(row) for row in rows], "page": page, "page_size": page_size,
        "pageSize": page_size, "total": total, "total_pages": total_pages,
        "totalPages": total_pages, "has_next": page < total_pages, "hasNext": page < total_pages,
        "unread": unread_count(user_id),
    }


def unread_count(user_id: int) -> int:
    where, params = _scope_sql(user_id, " AND read_at IS NULL")
    return int(db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE " + where, params)["n"])


def mark_read(user_id: int, notification_id: int) -> bool:
    where, params = _scope_sql(user_id)
    cur = db.execute("UPDATE notifications SET read_at=COALESCE(read_at, ?) WHERE id=? AND " + where,
                     (db.ts(), int(notification_id), *params))
    return cur.rowcount == 1


def mark_all_read(user_id: int) -> int:
    where, params = _scope_sql(user_id, " AND read_at IS NULL")
    cur = db.execute("UPDATE notifications SET read_at=? WHERE " + where, (db.ts(), *params))
    return cur.rowcount


def recent_notifications(user_id: int, limit: int = 8) -> list[dict]:
    result = list_notifications(user_id, page=1, page_size=max(1, min(int(limit), 10)))
    return result["items"]


def notify_generation_terminal(job_id: int, terminal_status: str, *, actor_id: int | None = None) -> bool:
    """通知 audiobook requester after the canonical generation terminal write.

    This deliberately reads the operation projection after the fenced job
    finalization. A stale worker therefore has no notification authority, and
    a transient notification failure can be repaired by the startup recovery
    scan without changing the already-finalized artifact.
    """
    if terminal_status not in {"success", "failed", "cancelled"}:
        return False
    job = db.get_generation_job(job_id)
    if not job or not job.get("operation_id") or job.get("status") != terminal_status:
        return False
    if terminal_status == "failed" and job.get("retryable"):
        return False
    operation = db.query_one("SELECT * FROM generation_operations WHERE id=?", (job["operation_id"],))
    if not operation:
        return False
    projection = db.get_generation_operation(operation["id"])
    expected = {"success": "ready", "failed": "failed", "cancelled": "cancelled"}[terminal_status]
    if not projection or projection.get("status") != expected:
        return False
    request = db.query_one(
        "SELECT id, requester_account_id, book_id FROM content_requests WHERE id=? AND request_type='audiobook'",
        (operation.get("request_id"),),
    ) if operation.get("request_id") else None
    if not request:
        return False
    if actor_id is not None and int(actor_id) == int(request["requester_account_id"]):
        # Domain-level self actions do not create a redundant self-confirmation.
        return False
    if terminal_status == "success":
        event_type, title, body = "generation.ready", "有聲書已完成", "有聲書已完成，可以開始播放。"
    elif terminal_status == "cancelled":
        event_type, title, body = "generation.cancelled", "有聲書生成已取消", "這次有聲書生成已由平台取消。"
    else:
        event_type, title, body = "generation.failed", "有聲書生成未完成", "有聲書生成未能完成，請查看操作狀態或稍後重試。"
    create_notification(
        request["requester_account_id"], event_type, "generation", title, body,
        dedupe_key=f"generation_operation:{operation['id']}:{event_type}",
        target_type="content_request", target_id=request["id"],
        target_route=f"#/requests/{request['id']}", source_type="generation_operation",
        source_id=operation["id"], source_request_id=request["id"],
        source_generation_operation_id=operation["id"], source_book_id=request["book_id"],
    )
    return True


def recover_generation_notifications(limit: int = 100) -> int:
    rows = db.query(
        "SELECT id, status FROM generation_jobs WHERE operation_id IS NOT NULL "
        "AND status IN ('success','failed','cancelled') ORDER BY finished_at ASC, id ASC LIMIT ?",
        (max(1, min(int(limit), 500)),),
    )
    recovered = 0
    for row in rows:
        try:
            recovered += 1 if notify_generation_terminal(row["id"], row["status"]) else 0
        except Exception:
            # Recovery must never turn a healthy generation result into a
            # failed job merely because notification persistence is unavailable.
            continue
    return recovered
