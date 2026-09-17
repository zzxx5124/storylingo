"""Admin Console 的 bounded read models 與安全投影。

Admin UI 不是另一套業務真源；本模組只從 canonical SQLite tables 推導
受限的 list/summary DTO，並集中處理分頁、排序、關聯摘要與敏感欄位遮蔽。
"""
from __future__ import annotations

import json
import re
import csv
import io
from datetime import datetime, timedelta
from typing import Any

from .. import db
from . import ai_provider, audit as audit_service, tts_provider

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_AUDIT_PAGE_SIZE = 50
MAX_AUDIT_PAGE_SIZE = 200
MAX_AUDIT_EXPORT_ROWS = 1000
MAX_AUDIT_EXPORT_BYTES = 1_000_000
MAX_AUDIT_EXPORT_DAYS = 366
DEFAULT_AUDIT_EXPORT_DAYS = 31
STALE_WORKER_SECONDS = 90


def paging(page: Any = 1, page_size: Any = DEFAULT_PAGE_SIZE, *, maximum: int = MAX_PAGE_SIZE) -> tuple[int, int]:
    """Normalize untrusted pagination without ever allowing an unbounded query."""
    try:
        current = max(1, int(page or 1))
    except (TypeError, ValueError):
        current = 1
    try:
        size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), maximum))
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    return current, size


def page_result(items: list[dict], total: int, page: int, page_size: int, *, sort: str = "id", order: str = "asc") -> dict:
    pages = (int(total) + page_size - 1) // page_size if total else 0
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "pageSize": page_size,
        "total": int(total),
        "total_pages": pages,
        "totalPages": pages,
        "has_next": bool(page < pages),
        "hasNext": bool(page < pages),
        "sort": sort,
        "order": order,
    }


def _order(value: str, allowed: dict[str, str], default: str, direction: str = "desc") -> tuple[str, str]:
    key = value if value in allowed else default
    order = "asc" if str(direction or "").lower() == "asc" else "desc"
    return allowed[key], order


def _like(value: str) -> str:
    return f"%{str(value or '').strip()}%"


def _audit_day_start(value: Any) -> str:
    """Convert an ISO date filter into an index-friendly timestamp bound."""
    return f"{str(value or '').strip()[:10]}T00:00:00"


def _audit_day_end(value: Any) -> str:
    """Use an inclusive end-of-day bound for the second-precision audit clock."""
    return f"{str(value or '').strip()[:10]}T23:59:59"


def _bool(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"0", "false", "no", "off"}


def _json(raw: Any, fallback: Any = None) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    try:
        value = json.loads(raw or "")
        return value
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _safe_text(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _safe_error(value: Any, limit: int = 500) -> str:
    """Keep operator-facing errors useful while masking credential-shaped text."""
    text = _safe_text(value, limit)
    return re.sub(
        r"(?i)(api[_ -]?key|authorization|bearer|token|secret|password)\s*[:=]\s*[^\s,;]+",
        r"\1=[已遮蔽]",
        text,
    )


def _safe_audit_details(raw: Any) -> dict:
    """Return bounded details while dropping credential/token-shaped keys."""
    return audit_service.sanitize_details(raw)


def _audit_category(action: str) -> str:
    name = str(action or "").lower()
    if any(word in name for word in ("role", "user", "author_application", "super_admin", "password")):
        return "governance"
    if any(word in name for word in ("provider", "generation", "worker", "job")):
        return "operations"
    if any(word in name for word in ("request", "review", "publish", "unpublish", "hide")):
        return "content"
    return "system"


def _audit_actor(row: dict) -> tuple[str, str]:
    """Return a privacy-safe label and opaque reference for an actor."""
    actor_id = row.get("actor_id")
    if actor_id is None:
        return "系統", "system"
    if str(row.get("account_status") or "active") == "deleted":
        return "已刪除帳號", f"actor:{int(actor_id)}"
    username = str(row.get("username") or "").strip()
    if not username:
        return "已刪除帳號", f"actor:{int(actor_id)}"
    return username[:80], f"actor:{int(actor_id)}"


def _audit_item(row: dict) -> dict:
    details = _safe_audit_details(row.get("details"))
    actor_label, actor_reference = _audit_actor(row)
    action = row.get("action") or ""
    return {
        "id": row["id"], "actorId": row.get("actor_id"), "actorReference": actor_reference,
        "actorLabel": actor_label, "username": actor_label,
        "action": action, "category": _audit_category(action),
        "eventClass": row.get("event_class") or audit_service.event_class(action),
        "policyVersion": row.get("policy_version") or "legacy-v1",
        "correctionOfId": row.get("correction_of_id"),
        "targetType": row.get("target_type") or "", "targetId": row.get("target_id") or "",
        "details": details, "createdAt": row["created_at"],
        # compatibility fields used by the existing audit renderer/tests.
        "target_type": row.get("target_type") or "", "target_id": row.get("target_id") or "",
        "created_at": row["created_at"], "detailsText": json.dumps(details, ensure_ascii=False),
    }


def list_accounts(*, q: str = "", role: str = "", status: str = "", author_status: str = "",
                  created_from: str = "", created_to: str = "", page: Any = 1, page_size: Any = 20,
                  sort: str = "id", order: str = "asc") -> dict:
    pg, per = paging(page, page_size)
    where: list[str] = []
    params: list[Any] = []
    if q and str(q).strip():
        where.append("(u.username LIKE ? COLLATE NOCASE OR u.email LIKE ? COLLATE NOCASE OR pp.display_name LIKE ? COLLATE NOCASE)")
        params.extend([_like(q), _like(q), _like(q)])
    if role in db.SUPPORTED_ROLES:
        where.append("u.role=?")
        params.append(role)
    if status in ("active", "disabled", "deleted"):
        where.append("u.account_status=?")
        params.append(status)
    if author_status in ("none", "pending", "approved", "rejected"):
        where.append("COALESCE(aa.status, 'none')=?")
        params.append(author_status)
    if created_from:
        where.append("date(u.created_at)>=?")
        params.append(str(created_from)[:10])
    if created_to:
        where.append("date(u.created_at)<=?")
        params.append(str(created_to)[:10])
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {
        "id": "u.id", "username": "u.username COLLATE NOCASE", "created_at": "u.created_at",
        "last_activity": "COALESCE(u.last_login_at, u.created_at)", "owned_books": "COALESCE(ob.owned_books, 0)",
    }
    order_sql, direction = _order(sort, allowed, "id", order)
    base = (
        "FROM users u "
        "LEFT JOIN public_profiles pp ON pp.account_id=u.id "
        "LEFT JOIN author_applications aa ON aa.id=(SELECT MAX(a2.id) FROM author_applications a2 WHERE a2.user_id=u.id) "
        "LEFT JOIN author_profiles ap ON ap.id=(SELECT MAX(ap2.id) FROM author_profiles ap2 WHERE ap2.owner_id=u.id AND ap2.status<>'tombstone') "
        "LEFT JOIN (SELECT owner_id, COUNT(*) AS owned_books FROM books GROUP BY owner_id) ob ON ob.owner_id=u.id"
    )
    total = db.query_one("SELECT COUNT(*) AS n " + base + cond, tuple(params))["n"]
    rows = db.query(
        "SELECT u.id,u.username,u.email,u.role,u.account_status,u.created_at,u.last_login_at,"
        "pp.display_name,pp.bio,COALESCE(ob.owned_books,0) AS owned_books,"
        "aa.id AS application_id,aa.status AS application_status,aa.reject_reason AS application_reject_reason,"
        "aa.updated_at AS application_updated_at,ap.id AS author_profile_id,ap.public_id AS author_profile_public_id,"
        "ap.slug AS author_profile_slug,ap.display_name AS author_profile_name,ap.status AS author_profile_status "
        + base + cond + f" ORDER BY {order_sql} {direction}, u.id ASC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per),
    )
    items = []
    for row in rows:
        item = {
            "id": row["id"], "username": row["username"], "email": row.get("email") or "",
            "role": row["role"], "account_status": row["account_status"], "created_at": row["created_at"],
            "last_login_at": row.get("last_login_at"), "displayName": row.get("display_name") or row["username"],
            "bio": _safe_text(row.get("bio"), 1000), "ownedBookCount": int(row.get("owned_books") or 0),
            "books": int(row.get("owned_books") or 0),
            "authorStatus": row.get("application_status") or "none",
            "authorApplication": {
                "id": row.get("application_id"), "status": row.get("application_status") or "none",
                "reason": _safe_text(row.get("application_reject_reason")), "updatedAt": row.get("application_updated_at"),
            } if row.get("application_id") else None,
            "authorProfile": {
                "id": row.get("author_profile_id"), "publicId": row.get("author_profile_public_id"),
                "slug": row.get("author_profile_slug"), "displayName": row.get("author_profile_name"),
                "status": row.get("author_profile_status"),
            } if row.get("author_profile_id") else None,
        }
        items.append(item)
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "id", order=direction)


def list_books(*, q: str = "", status: str = "", publication: str = "", request_status: str = "",
               generation_status: str = "", category_id: Any = None, language: str = "", serial: str = "",
               owner_id: Any = None, author_profile_id: Any = None, date_from: str = "", date_to: str = "",
               page: Any = 1, page_size: Any = 20, sort: str = "updated_at", order: str = "desc") -> dict:
    pg, per = paging(page, page_size)
    where: list[str] = []
    params: list[Any] = []
    if q and str(q).strip():
        where.append("(b.title LIKE ? COLLATE NOCASE OR b.synopsis LIKE ? COLLATE NOCASE OR b.tags LIKE ? COLLATE NOCASE OR owner.username LIKE ? COLLATE NOCASE OR ap.display_name LIKE ? COLLATE NOCASE)")
        params.extend([_like(q)] * 5)
    if status:
        where.append("b.status=?")
        params.append(status)
    if publication in ("published", "unpublished"):
        where.append("(b.status='approved' AND b.published_at IS NOT NULL)" if publication == "published" else "NOT (b.status='approved' AND b.published_at IS NOT NULL)")
    if request_status in ("SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED", "CANCELLED", "INVALIDATED"):
        where.append("EXISTS (SELECT 1 FROM content_requests crf WHERE crf.book_id=b.id AND crf.status=?)")
        params.append(request_status)
    if generation_status in ("queued", "running", "retrying", "waiting_dependency", "stale", "failed", "cancelled", "ready"):
        if generation_status == "ready":
            where.append("EXISTS (SELECT 1 FROM generation_jobs gjf WHERE gjf.book_id=b.id AND gjf.status='success')")
        else:
            target_status = "pending" if generation_status in ("queued", "retrying") else generation_status
            where.append("EXISTS (SELECT 1 FROM generation_jobs gjf WHERE gjf.book_id=b.id AND gjf.status=?)")
            params.append(target_status)
    try:
        if category_id not in (None, ""):
            where.append("b.category_id=?")
            params.append(int(category_id))
    except (TypeError, ValueError):
        pass
    if language:
        where.append("b.category=?")
        params.append(str(language)[:40])
    if serial:
        where.append("b.serial=?")
        params.append(str(serial)[:40])
    try:
        if owner_id not in (None, ""):
            where.append("b.owner_id=?")
            params.append(int(owner_id))
    except (TypeError, ValueError):
        pass
    try:
        if author_profile_id not in (None, ""):
            where.append("b.author_profile_id=?")
            params.append(int(author_profile_id))
    except (TypeError, ValueError):
        pass
    if date_from:
        where.append("date(b.created_at)>=?")
        params.append(str(date_from)[:10])
    if date_to:
        where.append("date(b.created_at)<=?")
        params.append(str(date_to)[:10])
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {
        "id": "b.id", "title": "b.title COLLATE NOCASE", "created_at": "b.created_at",
        "updated_at": "b.updated_at", "published_at": "COALESCE(b.published_at, b.created_at)",
    }
    order_sql, direction = _order(sort, allowed, "updated_at", order)
    base = (
        "FROM books b JOIN users owner ON owner.id=b.owner_id "
        "LEFT JOIN author_profiles ap ON ap.id=b.author_profile_id "
        "LEFT JOIN categories cat ON cat.id=b.category_id "
    )
    total = db.query_one("SELECT COUNT(*) AS n " + base + cond, tuple(params))["n"]
    rows = db.query(
        "SELECT b.id AS book_row_id,b.bid,b.title,b.synopsis,b.category,b.vocab_level,b.serial,b.status,b.reject_reason,"
        "b.cover_path,b.created_at,b.updated_at,b.published_at,b.category_id,b.visibility,b.access_policy,b.age_rating,"
        "b.content_warning,b.audio_mode,b.default_voice_id,owner.id AS owner_id,owner.username AS owner_username,"
        "ap.id AS author_profile_id,ap.public_id AS author_profile_public_id,ap.slug AS author_profile_slug,"
        "ap.display_name AS author_profile_name,cat.name AS category_name,"
        "(SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id) AS chapter_count,"
        "(SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.audio='ready') AS audio_ready_count,"
        "(SELECT status FROM content_requests cr WHERE cr.book_id=b.id AND cr.request_type='publish' ORDER BY cr.id DESC LIMIT 1) AS publish_request_status,"
        "(SELECT status FROM content_requests cr WHERE cr.book_id=b.id AND cr.request_type='unpublish' ORDER BY cr.id DESC LIMIT 1) AS unpublish_request_status,"
        "(SELECT status FROM content_requests cr WHERE cr.book_id=b.id AND cr.request_type='audiobook' ORDER BY cr.id DESC LIMIT 1) AS audiobook_request_status,"
        "(SELECT COUNT(*) FROM generation_jobs gj WHERE gj.book_id=b.id AND gj.status='running') AS generation_running,"
        "(SELECT COUNT(*) FROM generation_jobs gj WHERE gj.book_id=b.id AND gj.status IN ('pending','waiting_dependency')) AS generation_queued,"
        "(SELECT COUNT(*) FROM generation_jobs gj WHERE gj.book_id=b.id AND gj.status='failed') AS generation_failed "
        + base + cond + f" ORDER BY {order_sql} {direction}, b.id ASC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per),
    )
    items = []
    for row in rows:
        if row.get("generation_running"):
            generation_state = "running"
        elif row.get("generation_queued"):
            generation_state = "queued"
        elif row.get("generation_failed"):
            generation_state = "failed"
        elif row.get("audio_ready_count"):
            generation_state = "ready"
        else:
            generation_state = "none"
        published = row["status"] == "approved" and bool(row.get("published_at"))
        items.append({
            "id": row["bid"], "bid": row["bid"], "bookRowId": row["book_row_id"], "title": row["title"],
            "synopsis": _safe_text(row.get("synopsis"), 500), "language": row.get("category") or "",
            "category": row.get("category") or "", "categoryName": row.get("category_name") or "",
            "categoryId": row.get("category_id"), "serial": row.get("serial") or "", "status": row.get("status"),
            "publicationState": "published" if published else (row.get("status") or "unpublished"),
            "publishedAt": row.get("published_at"), "visibility": row.get("visibility") or "public",
            "accessPolicy": row.get("access_policy") or "free", "ageRating": row.get("age_rating") or "general",
            "contentWarning": _safe_text(row.get("content_warning"), 500), "cover": row.get("cover_path") or None,
            "owner": row.get("owner_username") or "", "ownerId": row["owner_id"],
            "ownerAccount": {"id": row["owner_id"], "username": row.get("owner_username") or ""},
            "authorProfile": {"id": row.get("author_profile_id"), "publicId": row.get("author_profile_public_id"),
                              "slug": row.get("author_profile_slug"), "displayName": row.get("author_profile_name")}
                if row.get("author_profile_id") else None,
            "chapterCount": int(row.get("chapter_count") or 0), "audioReadyCount": int(row.get("audio_ready_count") or 0),
            "requestState": {"publish": row.get("publish_request_status"), "unpublish": row.get("unpublish_request_status"),
                             "audiobook": row.get("audiobook_request_status")},
            "generationState": generation_state, "generation": {"running": int(row.get("generation_running") or 0),
                                                                    "queued": int(row.get("generation_queued") or 0),
                                                                    "failed": int(row.get("generation_failed") or 0)},
            "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
        })
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "updated_at", order=direction)


def list_categories(*, q: str = "", enabled: str = "", page: Any = 1, page_size: Any = 20,
                    sort: str = "sort", order: str = "asc") -> dict:
    pg, per = paging(page, page_size)
    where, params = [], []
    if q:
        where.append("c.name LIKE ? COLLATE NOCASE")
        params.append(_like(q))
    if enabled in ("0", "1", "true", "false"):
        where.append("c.enabled=?")
        params.append(1 if _bool(enabled) else 0)
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {"id": "c.id", "name": "c.name COLLATE NOCASE", "sort": "c.sort", "book_count": "book_count"}
    order_sql, direction = _order(sort, allowed, "sort", order)
    base = "FROM categories c LEFT JOIN books b ON b.category_id=c.id"
    total = db.query_one("SELECT COUNT(DISTINCT c.id) AS n " + base + cond, tuple(params))["n"]
    rows = db.query(
        "SELECT c.id,c.name,c.sort,c.enabled,COUNT(b.id) AS book_count " + base + cond +
        f" GROUP BY c.id,c.name,c.sort,c.enabled ORDER BY {order_sql} {direction}, c.id ASC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per),
    )
    items = [{"id": r["id"], "name": r["name"], "sort": r["sort"], "enabled": bool(r["enabled"]),
              "bookCount": int(r["book_count"] or 0), "book_count": int(r["book_count"] or 0)} for r in rows]
    result = page_result(items, total, pg, per, sort=sort if sort in allowed else "sort", order=direction)
    result["categories"] = items
    return result


def list_banners(*, q: str = "", enabled: str = "", page: Any = 1, page_size: Any = 20,
                 sort: str = "sort_order", order: str = "asc") -> dict:
    pg, per = paging(page, page_size)
    where, params = [], []
    if q:
        where.append("(title LIKE ? COLLATE NOCASE OR subtitle LIKE ? COLLATE NOCASE OR alt_text LIKE ? COLLATE NOCASE)")
        params.extend([_like(q)] * 3)
    if enabled in ("0", "1", "true", "false"):
        where.append("enabled=?")
        params.append(1 if _bool(enabled) else 0)
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {"id": "id", "sort_order": "sort_order", "created_at": "created_at", "updated_at": "updated_at"}
    order_sql, direction = _order(sort, allowed, "sort_order", order)
    total = db.query_one("SELECT COUNT(*) AS n FROM banners" + cond, tuple(params))["n"]
    rows = db.query("SELECT id,title,subtitle,image_desktop,image_mobile,link_type,link_value,alt_text,sort_order,start_at,end_at,enabled,impression_count,click_count,created_at,updated_at FROM banners" + cond +
                    f" ORDER BY {order_sql} {direction}, id ASC LIMIT ? OFFSET ?", (*params, per, (pg - 1) * per))
    items = []
    for row in rows:
        item = dict(row)
        item.update({"imageDesktop": item["image_desktop"], "imageMobile": item["image_mobile"],
                     "linkType": item["link_type"], "linkValue": item["link_value"], "altText": item["alt_text"],
                     "sortOrder": item["sort_order"], "startAt": item["start_at"], "endAt": item["end_at"],
                     "isActiveNow": bool(item["enabled"]) and (not item["start_at"] or item["start_at"] <= db.ts()) and (not item["end_at"] or item["end_at"] >= db.ts())})
        items.append(item)
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "sort_order", order=direction)


def list_announcements(*, q: str = "", status: str = "", audience: str = "", schedule: str = "",
                       active: Any = None,
                       page: Any = 1, page_size: Any = 20, sort: str = "updated_at",
                       order: str = "desc") -> dict:
    """公告治理清單；所有篩選在 SQL 分頁前完成，且只回傳安全 DTO。"""
    from . import announcements

    pg, per = paging(page, page_size)
    now = announcements.utc_text(announcements.utc_now())
    where, params = [], []
    if q:
        where.append("(a.title LIKE ? COLLATE NOCASE OR a.body_text LIKE ? COLLATE NOCASE)")
        params.extend([_like(q), _like(q)])
    if audience in announcements.AUDIENCE_MODES:
        where.append("a.audience_mode=?")
        params.append(audience)
    if not status and schedule in {"active", "scheduled", "expired", "disabled", "archived"}:
        status = schedule
    if status in {"active", "scheduled", "expired", "disabled", "archived"}:
        expressions = {
            "active": "a.archived_at IS NULL AND a.enabled=1 AND (a.start_at IS NULL OR a.start_at<=?) AND (a.end_at IS NULL OR a.end_at>?)",
            "scheduled": "a.archived_at IS NULL AND a.enabled=1 AND a.start_at>?",
            "expired": "a.archived_at IS NULL AND a.enabled=1 AND a.end_at IS NOT NULL AND a.end_at<=?",
            "disabled": "a.archived_at IS NULL AND a.enabled=0",
            "archived": "a.archived_at IS NOT NULL",
        }
        where.append(expressions[status])
        params.extend([now, now] if status == "active" else [now])
    elif active is not None:
        active_value = _bool(active)
        if active_value:
            where.append("a.archived_at IS NULL AND a.enabled=1 AND (a.start_at IS NULL OR a.start_at<=?) AND (a.end_at IS NULL OR a.end_at>?)")
            params.extend([now, now])
        else:
            where.append("NOT (a.archived_at IS NULL AND a.enabled=1 AND (a.start_at IS NULL OR a.start_at<=?) AND (a.end_at IS NULL OR a.end_at>?))")
            params.extend([now, now])
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {
        "id": "a.id", "priority": "a.priority", "start_at": "a.start_at",
        "updated_at": "a.updated_at", "display_version": "a.display_version",
        "config_version": "a.config_version",
    }
    order_sql, direction = _order(sort, allowed, "updated_at", order)
    total = db.query_one("SELECT COUNT(*) AS n FROM announcements a" + cond, tuple(params))["n"]
    rows = db.query(
        "SELECT a.* FROM announcements a" + cond +
        f" ORDER BY {order_sql} {direction}, a.id {direction} LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per),
    )
    items = [announcements.admin_dto(dict(row)) for row in rows]
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "updated_at", order=direction)


def list_reports(*, status: str = "open", target_type: str = "", q: str = "", page: Any = 1,
                 page_size: Any = 20, sort: str = "created_at", order: str = "asc") -> dict:
    pg, per = paging(page, page_size)
    where, params = [], []
    if status in ("open", "resolved"):
        where.append("r.status=?")
        params.append(status)
    if target_type:
        where.append("r.target_type=?")
        params.append(str(target_type)[:50])
    if q:
        where.append("(r.reason LIKE ? OR u.username LIKE ? COLLATE NOCASE)")
        params.extend([_like(q), _like(q)])
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {"id": "r.id", "created_at": "r.created_at", "status": "r.status"}
    order_sql, direction = _order(sort, allowed, "created_at", order)
    base = "FROM reports r LEFT JOIN users u ON u.id=r.reporter_id"
    total = db.query_one("SELECT COUNT(*) AS n " + base + cond, tuple(params))["n"]
    rows = db.query("SELECT r.id,r.reporter_id,r.target_type,r.target_id,r.reason,r.status,r.resolution,r.created_at,r.resolved_at,u.username " + base + cond +
                    f" ORDER BY {order_sql} {direction}, r.id ASC LIMIT ? OFFSET ?", (*params, per, (pg - 1) * per))
    items = [{"id": r["id"], "reporterId": r["reporter_id"], "username": r.get("username") or "系統/未知帳號",
              "targetType": r["target_type"], "targetId": r["target_id"], "reason": _safe_text(r["reason"]),
              "status": r["status"], "resolution": _safe_text(r.get("resolution")), "createdAt": r["created_at"],
              "resolvedAt": r.get("resolved_at"),
              # legacy keys remain for existing moderation UI.
              "target_type": r["target_type"], "target_id": r["target_id"], "created_at": r["created_at"]} for r in rows]
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "created_at", order=direction)


def _safe_attempt(row: dict) -> dict:
    """Return bounded execution history without fencing tokens or diagnostics leaks."""
    diagnostics = _safe_audit_details(row.get("diagnostics"))
    return {
        "id": row.get("id"),
        "jobId": row.get("job_id"),
        "attemptNumber": row.get("attempt_number"),
        "serviceType": row.get("service_type") or "",
        "providerId": row.get("provider_id"),
        "providerLabel": _safe_text(row.get("provider_label"), 120),
        "providerModel": _safe_text(row.get("provider_model"), 120),
        "providerConfigVersion": _safe_text(row.get("provider_config_version"), 80),
        "workerInstanceId": _safe_text(row.get("worker_instance_id"), 120),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "outcome": _safe_text(row.get("outcome"), 80),
        "failureCategory": _safe_text(row.get("failure_category"), 100),
        "retryable": bool(row.get("retryable")),
        "diagnostics": diagnostics,
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def safe_provider(service_type: str, row: dict | None) -> dict:
    """Serialize a provider for Admin responses, including mutation results."""
    if not row:
        return {}
    item = ai_provider.public_provider(row) if service_type.upper() == "AI" else tts_provider.public_provider(row)
    item["lastError"] = _safe_error(item.get("lastError"), 500)
    return item


def safe_generation_job(job_id: Any) -> dict | None:
    """Admin-safe detail for legacy and vNext job routes."""
    try:
        job = db.get_generation_job(int(job_id))
    except (TypeError, ValueError):
        return None
    if not job:
        return None
    item = db._safe_generation_job(job)
    item["error"] = _safe_error(item.get("error"), 500)
    item["attemptsHistory"] = [_safe_attempt(row) for row in db.list_generation_attempts(job["id"])]
    item["attemptCount"] = len(item["attemptsHistory"])
    return item


def safe_generation_operation(operation_id: Any) -> dict | None:
    """Bound and sanitize the canonical operation detail projection."""
    try:
        operation = db.get_generation_operation(int(operation_id))
    except (TypeError, ValueError):
        return None
    if not operation:
        return None
    operation["metadata"] = _safe_audit_details(operation.get("metadata"))
    operation["error"] = _safe_error(operation.get("error"), 500)
    jobs = operation.get("jobs") or []
    # Detail pages show recent execution context; immutable history remains in
    # the database and can be queried by a future dedicated history surface.
    operation["jobs"] = []
    for job in jobs[-100:]:
        clean = {key: value for key, value in job.items() if key != "attemptsHistory"}
        clean["error"] = _safe_error(clean.get("error"), 500)
        clean["attemptsHistory"] = [_safe_attempt(row) for row in (job.get("attemptsHistory") or [])[-50:]]
        clean["attemptCount"] = len(job.get("attemptsHistory") or [])
        operation["jobs"].append(clean)
    operation["jobCount"] = len(jobs)
    return operation


def list_jobs(*, status: str = "", job_type: str = "", service_type: str = "", q: str = "",
              page: Any = 1, page_size: Any = 20, sort: str = "created_at", order: str = "desc") -> dict:
    """Bounded compatibility view for the older Admin jobs tab."""
    pg, per = paging(page, page_size)
    where, params = [], []
    if status in {"pending", "running", "waiting_dependency", "success", "failed", "cancelled", "stale"}:
        where.append("g.status=?")
        params.append(status)
    if job_type:
        where.append("g.job_type=?")
        params.append(str(job_type)[:80])
    if service_type in ("AI", "TTS"):
        where.append("COALESCE(g.service_type, CASE WHEN g.job_type LIKE 'speaker_analysis%' THEN 'AI' WHEN g.job_type LIKE 'audio_%' THEN 'TTS' ELSE '' END)=?")
        params.append(service_type)
    if q:
        where.append("(CAST(g.id AS TEXT)=? OR g.job_type LIKE ? OR g.provider_label LIKE ?)")
        params.extend([str(q).strip()[:40], _like(q), _like(q)])
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {"id": "g.id", "created_at": "g.created_at", "updated_at": "COALESCE(g.updated_at,g.created_at)", "status": "g.status"}
    order_sql, direction = _order(sort, allowed, "created_at", order)
    total = db.query_one("SELECT COUNT(*) AS n FROM generation_jobs g" + cond, tuple(params))["n"]
    rows = db.query(
        "SELECT g.* FROM generation_jobs g" + cond +
        f" ORDER BY {order_sql} {direction}, g.id {direction} LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per),
    )
    items = []
    for row in rows:
        item = db._safe_generation_job(row)
        # Keep compatibility keys for existing UI/test consumers, but never
        # copy payload/configuration JSON into the response.
        item.update({
            "job_type": row.get("job_type"), "book_id": row.get("book_id"), "chapter_id": row.get("chapter_id"),
            "created_at": row.get("created_at"), "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"), "error": _safe_error(row.get("error"), 500),
        })
        items.append(item)
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "created_at", order=direction)


def list_audit(*, action: str = "", actor: str = "", category: str = "", target_type: str = "",
               target_id: str = "", date_from: str = "", date_to: str = "", page: Any = 1,
               page_size: Any = DEFAULT_AUDIT_PAGE_SIZE, sort: str = "created_at", order: str = "desc") -> dict:
    pg, per = paging(page, page_size, maximum=MAX_AUDIT_PAGE_SIZE)
    where, params = [], []
    if action:
        where.append("a.action LIKE ?")
        params.append(_like(action))
    if actor:
        where.append("u.username LIKE ? COLLATE NOCASE")
        params.append(_like(actor))
    if category in ("security", "governance", "operations", "content", "system"):
        # Category is a safe derived classification; use a bounded allowlist of action families.
        family = {"security": ["%oauth%", "%password%", "%email%", "%super_admin%"],
                  "governance": ["%role%", "%user%", "%super_admin%", "%author_application%", "%ownership_transfer%", "%audit_export%"],
                  "operations": ["%provider%", "%generation%", "%worker%", "%job%"],
                  "content": ["%request%", "%review%", "%publish%", "%unpublish%", "%hide%"],
                  "system": []}[category]
        if family:
            where.append("(" + " OR ".join("a.action LIKE ?" for _ in family) + ")")
            params.extend(family)
        else:
            where.append("NOT (a.action LIKE '%role%' OR a.action LIKE '%user%' OR a.action LIKE '%provider%' OR a.action LIKE '%generation%' OR a.action LIKE '%request%' OR a.action LIKE '%publish%' OR a.action LIKE '%hide%')")
    if target_type:
        where.append("a.target_type=?")
        params.append(str(target_type)[:50])
    if target_id:
        where.append("a.target_id=?")
        params.append(str(target_id)[:100])
    if date_from:
        where.append("a.created_at>=?")
        params.append(_audit_day_start(date_from))
    if date_to:
        where.append("a.created_at<=?")
        params.append(_audit_day_end(date_to))
    cond = " WHERE " + " AND ".join(where) if where else ""
    allowed = {"id": "a.id", "created_at": "a.created_at", "action": "a.action"}
    order_sql, direction = _order(sort, allowed, "created_at", order)
    base = "FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id"
    total = db.query_one("SELECT COUNT(*) AS n " + base + cond, tuple(params))["n"]
    rows = db.query("SELECT a.id,a.actor_id,a.action,a.target_type,a.target_id,a.details,a.event_class,a.policy_version,a.correction_of_id,a.created_at,u.username,u.account_status " + base + cond +
                    f" ORDER BY {order_sql} {direction}, a.id {direction} LIMIT ? OFFSET ?", (*params, per, (pg - 1) * per))
    items = [_audit_item(row) for row in rows]
    return page_result(items, total, pg, per, sort=sort if sort in allowed else "created_at", order=direction)


def get_audit(audit_id: Any) -> dict | None:
    """Return one bounded audit projection; no raw details or account secrets."""
    try:
        audit_id = int(audit_id)
    except (TypeError, ValueError):
        return None
    row = db.query_one(
        "SELECT a.id,a.actor_id,a.action,a.target_type,a.target_id,a.details,a.event_class,"
        "a.policy_version,a.correction_of_id,a.created_at,u.username,u.account_status "
        "FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id WHERE a.id=?", (audit_id,))
    return _audit_item(row) if row else None


def _parse_export_date(value: Any, label: str):
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} 必須是 YYYY-MM-DD") from error


def normalize_audit_export_filters(payload: dict | None) -> dict:
    """Validate a bounded export request before it reaches SQL or a writer."""
    data = payload if isinstance(payload, dict) else {}
    fmt = str(data.get("format") or "json").strip().lower()
    if fmt not in {"json", "csv"}:
        raise ValueError("匯出格式只支援 JSON 或 CSV")
    try:
        max_rows = int(data.get("max_rows", data.get("maxRows", MAX_AUDIT_EXPORT_ROWS)))
    except (TypeError, ValueError) as error:
        raise ValueError("匯出筆數上限必須是整數") from error
    if max_rows < 1 or max_rows > MAX_AUDIT_EXPORT_ROWS:
        raise ValueError(f"匯出筆數上限必須介於 1 至 {MAX_AUDIT_EXPORT_ROWS}")

    def bounded_text(key: str, limit: int = 200) -> str:
        value = str(data.get(key) or "").strip()
        if len(value) > limit:
            raise ValueError(f"{key} 篩選過長")
        return value

    today = datetime.now().date()
    raw_from = bounded_text("date_from", 10)
    raw_to = bounded_text("date_to", 10)
    start = _parse_export_date(raw_from, "date_from") if raw_from else today - timedelta(days=DEFAULT_AUDIT_EXPORT_DAYS)
    end = _parse_export_date(raw_to, "date_to") if raw_to else today
    if end < start:
        raise ValueError("匯出日期範圍不正確")
    if (end - start).days > MAX_AUDIT_EXPORT_DAYS:
        raise ValueError(f"匯出日期範圍不可超過 {MAX_AUDIT_EXPORT_DAYS} 天")
    return {
        "action": bounded_text("action"), "actor": bounded_text("actor"),
        "category": bounded_text("category", 40), "target_type": bounded_text("target_type", 50),
        "target_id": bounded_text("target_id", 100), "date_from": start.isoformat(),
        "date_to": end.isoformat(), "format": fmt, "max_rows": max_rows,
    }


def export_audit(filters: dict) -> dict:
    """Read a bounded, redacted audit export using the same SQL projection."""
    data = normalize_audit_export_filters(filters)
    where, params = [], []
    if data["action"]:
        where.append("a.action LIKE ?")
        params.append(_like(data["action"]))
    if data["actor"]:
        where.append("u.username LIKE ? COLLATE NOCASE")
        params.append(_like(data["actor"]))
    category = data["category"]
    if category in ("security", "governance", "operations", "content", "system"):
        family = {"security": ["%oauth%", "%password%", "%email%", "%super_admin%"],
                  "governance": ["%role%", "%user%", "%super_admin%", "%author_application%", "%ownership_transfer%", "%audit_export%"],
                  "operations": ["%provider%", "%generation%", "%worker%", "%job%"],
                  "content": ["%request%", "%review%", "%publish%", "%unpublish%", "%hide%"],
                  "system": []}[category]
        if family:
            where.append("(" + " OR ".join("a.action LIKE ?" for _ in family) + ")")
            params.extend(family)
        else:
            where.append("NOT (a.action LIKE '%role%' OR a.action LIKE '%user%' OR a.action LIKE '%provider%' OR a.action LIKE '%generation%' OR a.action LIKE '%request%' OR a.action LIKE '%publish%' OR a.action LIKE '%hide%')")
    elif category:
        raise ValueError("不支援的稽核類別")
    if data["target_type"]:
        where.append("a.target_type=?")
        params.append(data["target_type"])
    if data["target_id"]:
        where.append("a.target_id=?")
        params.append(data["target_id"])
    where.extend(["a.created_at>=?", "a.created_at<=?"])
    params.extend([_audit_day_start(data["date_from"]), _audit_day_end(data["date_to"])])
    cond = " WHERE " + " AND ".join(where)
    total = int(db.query_one("SELECT COUNT(*) AS n FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id" + cond, tuple(params))["n"])
    if total > data["max_rows"]:
        raise ValueError(f"符合條件的稽核紀錄超過 {data['max_rows']} 筆，請縮小日期或篩選範圍")
    rows = db.query(
        "SELECT a.id,a.actor_id,a.action,a.target_type,a.target_id,a.details,a.event_class,"
        "a.policy_version,a.correction_of_id,a.created_at,u.username,u.account_status "
        "FROM audit_logs a LEFT JOIN users u ON u.id=a.actor_id" + cond +
        " ORDER BY a.created_at DESC, a.id DESC LIMIT ?", (*params, data["max_rows"]))
    items = [_audit_item(row) for row in rows]
    return {"items": items, "total": total, "filters": data, "policy": audit_service.retention_policy()}


def serialize_audit_export(result: dict) -> tuple[bytes, str]:
    """Serialize only safe audit projections and enforce the byte bound."""
    items = result.get("items") or []
    if result.get("filters", {}).get("format") == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["id", "action", "category", "event_class", "actor", "target_type", "target_id", "created_at", "details"])
        for item in items:
            writer.writerow([item.get("id"), item.get("action"), item.get("category"), item.get("eventClass"),
                             item.get("actorLabel"), item.get("targetType"), item.get("targetId"),
                             item.get("createdAt"), json.dumps(item.get("details") or {}, ensure_ascii=False)])
        body = output.getvalue().encode("utf-8")
        content_type = "text/csv; charset=utf-8"
    else:
        body = json.dumps({"items": items, "total": result.get("total", 0), "filters": result.get("filters", {}),
                           "policy": result.get("policy", {})}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        content_type = "application/json; charset=utf-8"
    if len(body) > MAX_AUDIT_EXPORT_BYTES:
        raise ValueError(f"匯出內容不可超過 {MAX_AUDIT_EXPORT_BYTES} bytes")
    return body, content_type


def _provider_rows(service_type: str) -> list[dict]:
    if service_type == "AI":
        raw = db.list_ai_providers()
        safe = [safe_provider("AI", row) for row in raw]
        table = "ai_providers"
    else:
        raw = db.list_tts_providers()
        safe = [safe_provider("TTS", row) for row in raw]
        table = "tts_providers"
    active = db.query(f"SELECT COALESCE(provider_id, ai_provider_id) AS provider_id, COUNT(*) AS n FROM generation_jobs WHERE service_type=? AND status='running' GROUP BY COALESCE(provider_id, ai_provider_id)", (service_type,))
    queued = db.query(f"SELECT COALESCE(provider_id, ai_provider_id) AS provider_id, COUNT(*) AS n FROM generation_jobs WHERE service_type=? AND status IN ('pending','waiting_dependency') GROUP BY COALESCE(provider_id, ai_provider_id)", (service_type,))
    active_map = {r["provider_id"]: r["n"] for r in active if r.get("provider_id") is not None}
    queued_map = {r["provider_id"]: r["n"] for r in queued if r.get("provider_id") is not None}
    items = []
    for item in safe:
        capacity = max(1, int(item.get("maxConcurrency") or 1))
        active_count = int(active_map.get(item["id"], 0))
        provider_scope = "local" if str(item.get("providerType") or "").lower() in ("local", "local_tts") or "local" in str(item.get("name") or "").lower() else "remote"
        item.update({"serviceType": service_type, "label": item.get("name") or "", "activeCount": active_count, "queueCount": int(queued_map.get(item["id"], 0)),
                     "availableCapacity": max(0, capacity - active_count), "capacitySaturated": active_count >= capacity,
                     "configurationValid": bool(item.get("enabled")) and (provider_scope == "local" or bool(item.get("hasSecret"))),
                     "providerScope": provider_scope})
        items.append(item)
    return items


def service_list(service_type: str, *, enabled: str = "", health: str = "", q: str = "",
                 page: Any = 1, page_size: Any = 20) -> dict:
    service = service_type.upper()
    items = _provider_rows("AI" if service == "AI" else "TTS")
    if enabled in ("0", "1", "true", "false"):
        expected = _bool(enabled)
        items = [item for item in items if bool(item.get("enabled")) == expected]
    if health:
        items = [item for item in items if str(item.get("healthState") or "unknown") == str(health)[:40]]
    if q:
        needle = str(q).casefold().strip()
        items = [item for item in items if needle in str(item.get("name") or "").casefold()
                 or needle in str(item.get("providerType") or "").casefold()]
    items.sort(key=lambda item: (str(item.get("name") or "").casefold(), int(item.get("id") or 0)))
    pg, per = paging(page, page_size)
    start = (pg - 1) * per
    return page_result(items[start:start + per], len(items), pg, per, sort="name", order="asc")


def generation_summary() -> dict:
    now = db.ts()
    counts = db.query("SELECT COALESCE(service_type, CASE WHEN job_type LIKE 'speaker_analysis%' THEN 'AI' WHEN job_type LIKE 'audio_%' THEN 'TTS' ELSE 'UNKNOWN' END) AS service_type,status,COUNT(*) AS n FROM generation_jobs GROUP BY service_type,status")
    queue_age = db.query("SELECT COALESCE(service_type, CASE WHEN job_type LIKE 'speaker_analysis%' THEN 'AI' WHEN job_type LIKE 'audio_%' THEN 'TTS' ELSE 'UNKNOWN' END) AS service_type, MIN(created_at) AS oldest FROM generation_jobs WHERE status IN ('pending','waiting_dependency') GROUP BY service_type")
    age_map = {row["service_type"]: row.get("oldest") for row in queue_age}
    providers = _provider_rows("AI") + _provider_rows("TTS")
    workers = []
    stale_before = datetime.now() - timedelta(seconds=STALE_WORKER_SECONDS)
    for row in db.list_worker_instances(active_only=False):
        heartbeat = row.get("heartbeat_at")
        try:
            stale = bool(row.get("active")) and datetime.fromisoformat(heartbeat) < stale_before
        except (TypeError, ValueError):
            stale = bool(row.get("active"))
        workers.append({"instanceId": row.get("instance_id"), "workerVersion": row.get("worker_version"),
                        "buildSha": row.get("build_sha"), "serviceTypes": row.get("service_types") or "",
                        "processId": row.get("process_id"), "startedAt": row.get("started_at"),
                        "heartbeatAt": heartbeat, "active": bool(row.get("active")), "stale": stale,
                        "workerEnabled": bool(row.get("worker_enabled"))})
    return {"asOf": now, "counts": counts, "queueAge": [{"serviceType": key, "oldestQueuedAt": value} for key, value in age_map.items()],
            "providers": providers, "workers": workers, "staleWorkerCount": sum(1 for item in workers if item["stale"])}


def _operation_status(stats: dict) -> str:
    if int(stats.get("running_count") or 0):
        return "running"
    if int(stats.get("waiting_count") or 0):
        return "waiting_dependency"
    if int(stats.get("pending_count") or 0):
        return "retrying" if int(stats.get("retrying_count") or 0) else "queued"
    if int(stats.get("stale_count") or 0):
        return "stale"
    if int(stats.get("failed_count") or 0):
        return "failed"
    if int(stats.get("cancelled_count") or 0):
        return "cancelled"
    if int(stats.get("job_count") or 0) and int(stats.get("success_count") or 0) == int(stats.get("job_count") or 0):
        return "ready"
    return "queued"


def list_generation_operations(*, service_type: str = "", status: str = "", provider_id: Any = None,
                               book_id: Any = None, date_from: str = "", date_to: str = "",
                               page: Any = 1, page_size: Any = 20) -> dict:
    pg, per = paging(page, page_size)
    where, params = [], []
    if service_type in ("AI", "TTS"):
        where.append("EXISTS (SELECT 1 FROM generation_jobs sf WHERE sf.operation_id=go.id AND sf.service_type=?)")
        params.append(service_type)
    try:
        if provider_id not in (None, ""):
            where.append("EXISTS (SELECT 1 FROM generation_jobs pf WHERE pf.operation_id=go.id AND COALESCE(pf.provider_id,pf.ai_provider_id)=?)")
            params.append(int(provider_id))
    except (TypeError, ValueError):
        pass
    try:
        if book_id not in (None, ""):
            where.append("go.book_id=?")
            params.append(int(book_id))
    except (TypeError, ValueError):
        pass
    if date_from:
        where.append("date(go.created_at)>=?")
        params.append(str(date_from)[:10])
    if date_to:
        where.append("date(go.created_at)<=?")
        params.append(str(date_to)[:10])
    if status in {"queued", "running", "retrying", "waiting_dependency", "stale", "failed", "cancelled", "ready"}:
        # The outer query filters the derived status, so filters execute before LIMIT/OFFSET.
        where.append("projected_status=?")
        params.append(status)
    cond = " WHERE " + " AND ".join(where) if where else ""
    base = (
        "WITH job_stats AS (SELECT operation_id,COUNT(*) AS job_count,"
        "SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_count,"
        "SUM(CASE WHEN status='waiting_dependency' THEN 1 ELSE 0 END) AS waiting_count,"
        "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending_count,"
        "SUM(CASE WHEN status='pending' AND next_attempt_at IS NOT NULL THEN 1 ELSE 0 END) AS retrying_count,"
        "SUM(CASE WHEN status='stale' THEN 1 ELSE 0 END) AS stale_count,"
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_count,"
        "SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled_count,"
        "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success_count,"
        "SUM(CASE WHEN status='running' OR status IN ('pending','waiting_dependency') THEN 1 ELSE 0 END) AS active_job_count,"
        "GROUP_CONCAT(DISTINCT service_type) AS service_types,MAX(COALESCE(finished_at,started_at,created_at)) AS jobs_updated_at FROM generation_jobs GROUP BY operation_id),"
        "attempt_stats AS (SELECT j.operation_id,COUNT(a.id) AS attempt_count FROM generation_jobs j LEFT JOIN generation_job_attempts a ON a.job_id=j.id GROUP BY j.operation_id),"
        "latest_jobs AS (SELECT j.* FROM generation_jobs j JOIN (SELECT operation_id,MAX(id) AS id FROM generation_jobs GROUP BY operation_id) latest ON latest.id=j.id),"
        "projected AS (SELECT go.id,go.request_id,go.book_id,go.chapter_id,go.operation_type,go.requested_by,"
        "go.source_revision,go.source_text_hash,go.status AS stored_status,go.error_category,go.error,go.created_at,go.updated_at,go.started_at,go.finished_at,"
        "b.bid,b.title AS book_title,u.username AS requester,cr.status AS request_status,"
        "COALESCE(js.job_count,0) AS job_count,COALESCE(js.running_count,0) AS running_count,COALESCE(js.waiting_count,0) AS waiting_count,"
        "COALESCE(js.pending_count,0) AS pending_count,COALESCE(js.retrying_count,0) AS retrying_count,COALESCE(js.stale_count,0) AS stale_count,"
        "COALESCE(js.failed_count,0) AS failed_count,COALESCE(js.cancelled_count,0) AS cancelled_count,COALESCE(js.success_count,0) AS success_count,"
        "COALESCE(js.active_job_count,0) AS active_job_count,COALESCE(js.service_types,'') AS service_types,"
        "COALESCE(ast.attempt_count,0) AS attempt_count,COALESCE(js.jobs_updated_at,go.updated_at) AS jobs_updated_at,"
        "lj.id AS latest_job_id,lj.status AS latest_job_status,lj.progress AS latest_job_progress,lj.attempts AS latest_job_attempts,"
        "lj.max_attempts AS latest_job_max_attempts,lj.provider_label AS latest_provider_label,lj.provider_model AS latest_provider_model,"
        "lj.failure_category AS latest_failure_category,lj.error AS latest_job_error,lj.next_attempt_at AS latest_next_attempt_at,"
        "CASE WHEN COALESCE(js.running_count,0)>0 THEN 'running' WHEN COALESCE(js.waiting_count,0)>0 THEN 'waiting_dependency' "
        "WHEN COALESCE(js.pending_count,0)>0 THEN CASE WHEN COALESCE(js.retrying_count,0)>0 THEN 'retrying' ELSE 'queued' END "
        "WHEN COALESCE(js.stale_count,0)>0 THEN 'stale' WHEN COALESCE(js.failed_count,0)>0 THEN 'failed' "
        "WHEN COALESCE(js.cancelled_count,0)>0 THEN 'cancelled' WHEN COALESCE(js.job_count,0)>0 AND COALESCE(js.success_count,0)=COALESCE(js.job_count,0) THEN 'ready' ELSE 'queued' END AS projected_status "
        "FROM generation_operations go LEFT JOIN job_stats js ON js.operation_id=go.id LEFT JOIN attempt_stats ast ON ast.operation_id=go.id "
        "LEFT JOIN latest_jobs lj ON lj.operation_id=go.id LEFT JOIN books b ON b.id=go.book_id "
        "LEFT JOIN users u ON u.id=go.requested_by LEFT JOIN content_requests cr ON cr.id=go.request_id) "
        "SELECT * FROM projected"
    )
    total = db.query_one("SELECT COUNT(*) AS n FROM (" + base + cond + ")", tuple(params))["n"]
    rows = db.query(base + cond + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (*params, per, (pg - 1) * per))
    items = []
    for row in rows:
        services = sorted(value for value in (row.get("service_types") or "").split(",") if value)
        latest_job = None
        if row.get("latest_job_id") is not None:
            latest_job = {"id": row["latest_job_id"], "status": row.get("latest_job_status"), "progress": row.get("latest_job_progress"),
                          "attempts": row.get("latest_job_attempts"), "maxAttempts": row.get("latest_job_max_attempts"),
                          "providerLabel": row.get("latest_provider_label") or "平台路由", "providerModel": row.get("latest_provider_model") or "",
                          "failureCategory": row.get("latest_failure_category") or "", "error": _safe_error(row.get("latest_job_error")),
                          "nextAttemptAt": row.get("latest_next_attempt_at")}
        items.append({"id": row["id"], "requestId": row.get("request_id"), "bookId": row.get("bid") or row.get("book_id"),
                      "bookRowId": row.get("book_id"), "bookTitle": row.get("book_title") or "", "operationType": row.get("operation_type"),
                      "requestedBy": row.get("requested_by"), "requester": row.get("requester") or "", "requestStatus": row.get("request_status"),
                      "serviceTypes": services, "serviceType": services[0] if len(services) == 1 else ("AI+TTS" if services else ""),
                      "status": row["projected_status"], "sourceRevision": row.get("source_revision") or "",
                      "errorCategory": row.get("error_category") or "", "error": _safe_error(row.get("error")),
                      "jobCount": int(row.get("job_count") or 0), "activeJobCount": int(row.get("active_job_count") or 0),
                      "attemptCount": int(row.get("attempt_count") or 0),
                      "job": latest_job,
                      "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"), "startedAt": row.get("started_at"), "finishedAt": row.get("finished_at"),
                      "authorizationState": "AUTHORIZED_FOR_GENERATION" if row.get("request_status") == "APPROVED" and row.get("operation_type") == "audiobook" else ""})
    result = page_result(items, total, pg, per, sort="created_at", order="desc")
    return result


def overview() -> dict:
    summary = generation_summary()
    role_rows = db.query("SELECT role,COUNT(*) AS n FROM users GROUP BY role")
    user_roles = {r["role"]: int(r["n"]) for r in role_rows}
    book_rows = db.query("SELECT status,COUNT(*) AS n FROM books GROUP BY status")
    by_status = {r["status"]: int(r["n"]) for r in book_rows}
    pending_apps = db.query_one("SELECT COUNT(*) AS n FROM author_applications WHERE status='pending'")["n"]
    active_requests = db.query_one("SELECT COUNT(*) AS n FROM content_requests WHERE status IN ('SUBMITTED','IN_REVIEW')")["n"]
    queue_counts = {"AI": 0, "TTS": 0}
    for row in summary["counts"]:
        if row["service_type"] in queue_counts and row["status"] in ("pending", "waiting_dependency", "running"):
            queue_counts[row["service_type"]] += int(row["n"])
    alerts = []
    for provider in summary["providers"]:
        if provider.get("queueCount") and not provider.get("enabled"):
            alerts.append({"severity": "critical", "kind": "provider_disabled", "scope": provider["serviceType"], "count": provider["queueCount"], "label": f"{provider['name']} 已停用但仍有排隊工作", "href": "#/admin?tab=services"})
        elif provider.get("capacitySaturated") and provider.get("queueCount"):
            alerts.append({"severity": "warning", "kind": "provider_saturated", "scope": provider["serviceType"], "count": provider["queueCount"], "label": f"{provider['name']} 容量已滿", "href": "#/admin?tab=generation"})
    if summary["staleWorkerCount"]:
        alerts.append({"severity": "critical", "kind": "stale_worker", "scope": "workers", "count": summary["staleWorkerCount"], "label": "有 Worker heartbeat 過期，請檢查生成運維", "href": "#/admin?tab=generation"})
    return {"asOf": db.ts(), "users": {"total": sum(user_roles.values()), "byRole": user_roles},
            "books": {"total": sum(by_status.values()), "byStatus": by_status, "published": by_status.get("approved", 0)},
            "governance": {"pendingAuthorApplications": int(pending_apps), "activeContentRequests": int(active_requests)},
            "generation": {"queuedAI": queue_counts["AI"], "queuedTTS": queue_counts["TTS"], "summary": summary},
            "alerts": alerts, "allClear": not alerts}
