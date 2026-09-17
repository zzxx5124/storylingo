"""Ownership Transfer aggregate and its atomic, auditable transitions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from .. import db

STATES = (
    "REQUESTED", "TARGET_ACCEPTED", "IN_REVIEW", "REJECTED",
    "CANCELLED", "EXPIRED", "INVALIDATED", "COMPLETED",
)
ACTIVE_STATES = ("REQUESTED", "TARGET_ACCEPTED", "IN_REVIEW")
TERMINAL_STATES = ("REJECTED", "CANCELLED", "EXPIRED", "INVALIDATED", "COMPLETED")
EXPIRY_DAYS = 7
MAX_REASON_LENGTH = 500
MAX_SEARCH_LENGTH = 80
EVENT_TYPES = {
    "submitted", "target_accepted", "target_rejected", "review_started",
    "approved", "rejected", "cancelled", "expired", "invalidated",
    "emergency_transfer",
}


class TransferError(Exception):
    def __init__(self, message: str, *, request_id: int | None = None, code: str = "transfer_conflict"):
        super().__init__(message)
        self.request_id = request_id
        self.code = code


class TransferConflict(TransferError):
    pass


class TransferNotFound(Exception):
    pass


class _CommitAfterWrite(Exception):
    """Internal signal for invalidating a request before returning 409."""
    def __init__(self, error: TransferConflict):
        super().__init__(str(error))
        self.error = error


def _safe_reason(value: Any, *, required: bool = False) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if required and not value:
        raise ValueError("請填寫原因")
    return value[:MAX_REASON_LENGTH]


def _safe_json(value: Any) -> str:
    if not isinstance(value, dict):
        value = {}
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"
    return raw if len(raw) <= 2000 else '{"truncated":true}'


def _now_plus_days(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return ""


def calculate_revision(book: dict, chapters: list[dict] | None = None) -> str:
    """Stable ownership-transfer source revision; owner/timestamps are excluded."""
    chapters = chapters if chapters is not None else db.list_chapters(int(book["id"]))
    chapter_values = []
    for chapter in sorted(chapters, key=lambda row: (int(row.get("seq") or 0), int(row["id"]))):
        text_hash = chapter.get("text_hash") or hashlib.sha256((chapter.get("text") or "").encode("utf-8")).hexdigest()
        chapter_values.append({
            "id": chapter.get("id"),
            "seq": chapter.get("seq"),
            "title": chapter.get("title") or "",
            "text_hash": text_hash,
            "publish_status": chapter.get("publish_status") or "published",
        })
    source = {
        "book_id": book.get("id"),
        "bid": book.get("bid"),
        "title": book.get("title") or "",
        "synopsis": book.get("synopsis") or "",
        "tags": book.get("tags") or "",
        "category": book.get("category") or "",
        "category_id": book.get("category_id"),
        "categories": book.get("categories") or "[]",
        "vocab_level": book.get("vocab_level") or "",
        "cover_path": book.get("cover_path") or "",
        "serial": book.get("serial") or "",
        "age_rating": book.get("age_rating") or "general",
        "content_warning": book.get("content_warning") or "",
        "visibility": book.get("visibility") or "public",
        "access_policy": book.get("access_policy") or "free",
        "author_profile_id": book.get("author_profile_id"),
        "chapters": chapter_values,
    }
    return hashlib.sha256(_canonical_json(source).encode("utf-8")).hexdigest()


@contextmanager
def _write_transaction():
    """Acquire an IMMEDIATE SQLite transaction for state/CAS operations."""
    deferred_error = None
    with db.atomic() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except _CommitAfterWrite as signal:
            # The invalidation is itself an auditable state transition.  Keep
            # it committed, then let the router return the domain conflict.
            con.commit()
            deferred_error = signal.error
    if deferred_error:
        raise deferred_error


def _fetch_request(con, request_id: int) -> dict | None:
    row = con.execute(
        "SELECT r.*, b.bid, b.title AS book_title, b.status AS book_status, "
        "b.published_at, b.owner_id AS book_owner_id, b.author_profile_id, "
        "owner.username AS owner_username, target.username AS target_username, "
        "requester.username AS requester_username, reviewer.username AS reviewer_username "
        "FROM ownership_transfer_requests r "
        "JOIN books b ON b.id=r.book_id "
        "JOIN users owner ON owner.id=r.current_owner_id "
        "JOIN users target ON target.id=r.target_account_id "
        "JOIN users requester ON requester.id=r.requester_account_id "
        "LEFT JOIN users reviewer ON reviewer.id=r.reviewer_account_id "
        "WHERE r.id=?", (request_id,)).fetchone()
    return dict(row) if row else None


def _fetch_events(con, request_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT e.*, actor.username AS actor_username, previous.username AS previous_owner_username, "
        "new_owner.username AS new_owner_username "
        "FROM ownership_transfer_events e "
        "LEFT JOIN users actor ON actor.id=e.actor_account_id "
        "LEFT JOIN users previous ON previous.id=e.previous_owner_id "
        "LEFT JOIN users new_owner ON new_owner.id=e.new_owner_id "
        "WHERE e.request_id=? ORDER BY e.created_at ASC, e.id ASC", (request_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["metadata"] = {}
        result.append(item)
    return result


def _event(con, request_id: int, event_type: str, *, actor_id: int | None,
           from_status: str | None, to_status: str | None, reason: str = "",
           previous_owner_id: int | None = None, new_owner_id: int | None = None,
           metadata: dict | None = None):
    if event_type not in EVENT_TYPES:
        raise ValueError("不支援的 ownership transfer event")
    con.execute(
        "INSERT INTO ownership_transfer_events(request_id,event_type,from_status,to_status,"
        "actor_account_id,previous_owner_id,new_owner_id,reason,metadata_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (request_id, event_type, from_status, to_status, actor_id, previous_owner_id,
         new_owner_id, _safe_reason(reason), _safe_json(metadata), db.ts()),
    )


def _audit(actor_id: int | None, action: str, request_id: int | None, book_id: int | None,
           details: dict | None = None):
    payload = {"requestId": request_id, "bookId": book_id, **(details or {})}
    db.add_audit_log(actor_id, action, "ownership_transfer", str(request_id or book_id or ""), payload)


def _active_generation(con, book_id: int) -> dict | None:
    row = con.execute(
        "SELECT j.id, j.status, j.job_type, j.service_type "
        "FROM generation_jobs j "
        "WHERE COALESCE(j.status,'') NOT IN ('success','failed','cancelled') "
        "AND (j.book_id=? OR EXISTS (SELECT 1 FROM chapters c WHERE c.id=j.chapter_id AND c.book_id=?)) "
        "ORDER BY j.id ASC LIMIT 1", (book_id, book_id)).fetchone()
    return dict(row) if row else None


def _book_revision_locked(con, book_id: int) -> str:
    book = con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise TransferNotFound("找不到作品")
    chapters = [dict(row) for row in con.execute("SELECT * FROM chapters WHERE book_id=?", (book_id,)).fetchall()]
    return calculate_revision(dict(book), chapters)


def _invalidate_locked(con, request: dict, *, actor_id: int | None, reason: str):
    updated = con.execute(
        "UPDATE ownership_transfer_requests SET status='INVALIDATED', reason=?, reviewed_at=?, updated_at=? "
        "WHERE id=? AND status IN ('REQUESTED','TARGET_ACCEPTED','IN_REVIEW')",
        (_safe_reason(reason), db.ts(), db.ts(), request["id"]),
    )
    if updated.rowcount:
        _event(con, request["id"], "invalidated", actor_id=actor_id,
               from_status=request["status"], to_status="INVALIDATED", reason=reason,
               metadata={"sourceRevision": request.get("source_revision") or ""})
        _audit(actor_id, "ownership_transfer_invalidated", request["id"], request["book_id"], {"reason": reason})


def _expire_due_locked(con) -> int:
    now = db.ts()
    rows = con.execute(
        "SELECT * FROM ownership_transfer_requests WHERE status='REQUESTED' AND expires_at<=? ORDER BY id",
        (now,)).fetchall()
    for raw in rows:
        request = dict(raw)
        if con.execute(
            "UPDATE ownership_transfer_requests SET status='EXPIRED', reviewed_at=?, updated_at=? "
            "WHERE id=? AND status='REQUESTED'", (now, now, request["id"])).rowcount:
            _event(con, request["id"], "expired", actor_id=None, from_status="REQUESTED",
                   to_status="EXPIRED", reason="轉移邀請已超過 7 天有效期限。",
                   metadata={"expiredAt": now})
            _audit(None, "ownership_transfer_expired", request["id"], request["book_id"], {})
    return len(rows)


def _ensure_active_account(con, account_id: int, *, message: str):
    row = con.execute("SELECT id, role, account_status FROM users WHERE id=?", (account_id,)).fetchone()
    if not row or row["account_status"] != "active":
        raise TransferConflict(message, code="account_not_eligible")
    return dict(row)


def submit(*, book_id: int, requester_id: int, target_account_id: int, reason: str = "") -> dict:
    reason = _safe_reason(reason)
    try:
        target_account_id = int(target_account_id)
    except (TypeError, ValueError):
        raise ValueError("targetAccountId 必須是明確的帳號 ID")
    with _write_transaction() as con:
        _expire_due_locked(con)
        book = con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not book:
            raise TransferNotFound("找不到作品")
        book = dict(book)
        if int(book["owner_id"]) != int(requester_id):
            raise PermissionError("只有目前作品所有者可以發起轉移")
        _ensure_active_account(con, requester_id, message="目前所有者帳號不可用")
        target = _ensure_active_account(con, target_account_id, message="找不到可用的目標帳號")
        if target["id"] == requester_id:
            raise TransferConflict("目標帳號不可與目前所有者相同", code="self_transfer")
        existing = con.execute(
            "SELECT id FROM ownership_transfer_requests WHERE book_id=? AND status IN ('REQUESTED','TARGET_ACCEPTED','IN_REVIEW') LIMIT 1",
            (book_id,)).fetchone()
        if existing:
            raise TransferConflict("此作品已有進行中的 ownership transfer", request_id=existing["id"], code="duplicate_active_transfer")
        now = db.ts()
        revision = _book_revision_locked(con, book_id)
        try:
            cur = con.execute(
                "INSERT INTO ownership_transfer_requests(book_id,requester_account_id,current_owner_id,target_account_id,"
                "status,transfer_mode,source_revision,reason,expires_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (book_id, requester_id, requester_id, target_account_id, "REQUESTED", "normal", revision,
                 reason, _now_plus_days(EXPIRY_DAYS), now, now),
            )
        except sqlite3.IntegrityError as error:
            existing = con.execute(
                "SELECT id FROM ownership_transfer_requests WHERE book_id=? AND status IN ('REQUESTED','TARGET_ACCEPTED','IN_REVIEW') LIMIT 1",
                (book_id,)).fetchone()
            if existing:
                raise TransferConflict("此作品已有進行中的 ownership transfer", request_id=existing["id"], code="duplicate_active_transfer") from error
            raise
        request_id = cur.lastrowid
        _event(con, request_id, "submitted", actor_id=requester_id, from_status=None,
               to_status="REQUESTED", reason=reason,
               previous_owner_id=requester_id, new_owner_id=target_account_id,
               metadata={"sourceRevision": revision})
        _audit(requester_id, "ownership_transfer_requested", request_id, book_id,
               {"targetAccountId": target_account_id, "sourceRevision": revision})
        return _fetch_request(con, request_id)


def _get_mutable_request(con, request_id: int) -> dict:
    row = _fetch_request(con, request_id)
    if not row:
        raise TransferNotFound("找不到 ownership transfer")
    return row


def accept_target(*, request_id: int, actor_id: int) -> dict:
    with _write_transaction() as con:
        _expire_due_locked(con)
        request = _get_mutable_request(con, request_id)
        if request["status"] != "REQUESTED":
            raise TransferConflict("轉移邀請狀態已變更", request_id=request_id, code="invalid_transition")
        if int(request["target_account_id"]) != int(actor_id):
            raise PermissionError("只有指定的目標帳號可以接受邀請")
        _ensure_active_account(con, actor_id, message="目標帳號不可用")
        if _book_revision_locked(con, request["book_id"]) != request["source_revision"]:
            _invalidate_locked(con, request, actor_id=actor_id, reason="作品內容已變更，請重新建立轉移申請。")
            raise _CommitAfterWrite(TransferConflict("作品內容已變更，轉移申請已失效", request_id=request_id, code="stale_revision"))
        now = db.ts()
        if not con.execute(
            "UPDATE ownership_transfer_requests SET status='TARGET_ACCEPTED', target_acted_at=?, updated_at=? "
            "WHERE id=? AND status='REQUESTED' AND target_account_id=?",
            (now, now, request_id, actor_id)).rowcount:
            raise TransferConflict("轉移邀請狀態已變更", request_id=request_id, code="invalid_transition")
        _event(con, request_id, "target_accepted", actor_id=actor_id, from_status="REQUESTED",
               to_status="TARGET_ACCEPTED", metadata={"sourceRevision": request["source_revision"]})
        _audit(actor_id, "ownership_transfer_target_accepted", request_id, request["book_id"], {})
        return _fetch_request(con, request_id)


def reject_target(*, request_id: int, actor_id: int, reason: str = "") -> dict:
    reason = _safe_reason(reason)
    with _write_transaction() as con:
        _expire_due_locked(con)
        request = _get_mutable_request(con, request_id)
        if request["status"] != "REQUESTED":
            raise TransferConflict("轉移邀請狀態已變更", request_id=request_id, code="invalid_transition")
        if int(request["target_account_id"]) != int(actor_id):
            raise PermissionError("只有指定的目標帳號可以拒絕邀請")
        _ensure_active_account(con, actor_id, message="目標帳號不可用")
        now = db.ts()
        if not con.execute(
            "UPDATE ownership_transfer_requests SET status='REJECTED', reason=?, target_acted_at=?, reviewed_at=?, updated_at=? "
            "WHERE id=? AND status='REQUESTED' AND target_account_id=?",
            (reason, now, now, now, request_id, actor_id)).rowcount:
            raise TransferConflict("轉移邀請狀態已變更", request_id=request_id, code="invalid_transition")
        _event(con, request_id, "target_rejected", actor_id=actor_id, from_status="REQUESTED",
               to_status="REJECTED", reason=reason)
        _audit(actor_id, "ownership_transfer_target_rejected", request_id, request["book_id"], {})
        return _fetch_request(con, request_id)


def cancel(*, request_id: int, actor_id: int) -> dict:
    with _write_transaction() as con:
        _expire_due_locked(con)
        request = _get_mutable_request(con, request_id)
        if request["status"] != "REQUESTED":
            raise TransferConflict("只有等待目標回應的申請可以取消", request_id=request_id, code="invalid_transition")
        if int(request["requester_account_id"]) != int(actor_id):
            raise PermissionError("只有申請人可以取消轉移")
        now = db.ts()
        if not con.execute(
            "UPDATE ownership_transfer_requests SET status='CANCELLED', reviewed_at=?, updated_at=? "
            "WHERE id=? AND status='REQUESTED' AND requester_account_id=?",
            (now, now, request_id, actor_id)).rowcount:
            raise TransferConflict("轉移申請狀態已變更", request_id=request_id, code="invalid_transition")
        _event(con, request_id, "cancelled", actor_id=actor_id, from_status="REQUESTED", to_status="CANCELLED")
        _audit(actor_id, "ownership_transfer_cancelled", request_id, request["book_id"], {})
        return _fetch_request(con, request_id)


def start_review(*, request_id: int, actor_id: int) -> dict:
    with _write_transaction() as con:
        _expire_due_locked(con)
        request = _get_mutable_request(con, request_id)
        if request["status"] != "TARGET_ACCEPTED":
            raise TransferConflict("只有目標已接受的申請可以開始審核", request_id=request_id, code="invalid_transition")
        if actor_id in (request["requester_account_id"], request["target_account_id"]):
            raise PermissionError("申請人或目標帳號不可審核自己的轉移")
        if _book_revision_locked(con, request["book_id"]) != request["source_revision"]:
            _invalidate_locked(con, request, actor_id=actor_id, reason="作品內容或署名已變更，請重新建立轉移申請。")
            raise _CommitAfterWrite(TransferConflict("作品內容已變更，轉移申請已失效", request_id=request_id, code="stale_revision"))
        now = db.ts()
        if not con.execute(
            "UPDATE ownership_transfer_requests SET status='IN_REVIEW', reviewer_account_id=?, review_started_at=?, updated_at=? "
            "WHERE id=? AND status='TARGET_ACCEPTED' AND reviewer_account_id IS NULL",
            (actor_id, now, now, request_id)).rowcount:
            raise TransferConflict("轉移申請已被其他 Reviewer 認領", request_id=request_id, code="review_conflict")
        _event(con, request_id, "review_started", actor_id=actor_id, from_status="TARGET_ACCEPTED",
               to_status="IN_REVIEW")
        _audit(actor_id, "ownership_transfer_review_started", request_id, request["book_id"], {})
        return _fetch_request(con, request_id)


def reject_review(*, request_id: int, actor_id: int, reason: str) -> dict:
    reason = _safe_reason(reason, required=True)
    with _write_transaction() as con:
        _expire_due_locked(con)
        request = _get_mutable_request(con, request_id)
        if actor_id in (request["requester_account_id"], request["target_account_id"]):
            raise PermissionError("申請人或目標帳號不可退回自己的轉移")
        if request["status"] != "IN_REVIEW" or request.get("reviewer_account_id") != actor_id:
            raise TransferConflict("只有目前認領的 Reviewer 可以退回申請", request_id=request_id, code="review_conflict")
        now = db.ts()
        if not con.execute(
            "UPDATE ownership_transfer_requests SET status='REJECTED', reason=?, reviewed_at=?, updated_at=? "
            "WHERE id=? AND status='IN_REVIEW' AND reviewer_account_id=?",
            (reason, now, now, request_id, actor_id)).rowcount:
            raise TransferConflict("轉移申請狀態已變更", request_id=request_id, code="review_conflict")
        _event(con, request_id, "rejected", actor_id=actor_id, from_status="IN_REVIEW",
               to_status="REJECTED", reason=reason)
        _audit(actor_id, "ownership_transfer_rejected", request_id, request["book_id"], {})
        return _fetch_request(con, request_id)


def approve(*, request_id: int, actor_id: int) -> dict:
    with _write_transaction() as con:
        _expire_due_locked(con)
        request = _get_mutable_request(con, request_id)
        if actor_id in (request["requester_account_id"], request["target_account_id"]):
            raise PermissionError("申請人或目標帳號不可核准自己的轉移")
        if request["status"] != "IN_REVIEW" or request.get("reviewer_account_id") != actor_id:
            raise TransferConflict("只有目前認領的 Reviewer 可以核准申請", request_id=request_id, code="review_conflict")
        _ensure_active_account(con, request["current_owner_id"], message="目前所有者帳號不可用")
        _ensure_active_account(con, request["target_account_id"], message="目標帳號不可用")
        current_revision = _book_revision_locked(con, request["book_id"])
        if current_revision != request["source_revision"]:
            _invalidate_locked(con, request, actor_id=actor_id, reason="作品內容或署名已變更，請重新建立轉移申請。")
            raise _CommitAfterWrite(TransferConflict("作品內容已變更，轉移申請已失效", request_id=request_id, code="stale_revision"))
        active = _active_generation(con, request["book_id"])
        if active:
            raise TransferConflict("作品仍有未完成的生成任務，完成前不可轉移所有權", request_id=request_id, code="active_generation")
        book = con.execute("SELECT owner_id, author_profile_id, status, published_at FROM books WHERE id=?", (request["book_id"],)).fetchone()
        if not book or int(book["owner_id"]) != int(request["current_owner_id"]):
            _invalidate_locked(con, request, actor_id=actor_id, reason="作品目前所有者已變更，請重新建立轉移申請。")
            raise _CommitAfterWrite(TransferConflict("作品目前所有者已變更，轉移申請已失效", request_id=request_id, code="owner_changed"))
        now = db.ts()
        changed = con.execute(
            "UPDATE books SET owner_id=? WHERE id=? AND owner_id=?",
            (request["target_account_id"], request["book_id"], request["current_owner_id"]))
        if changed.rowcount != 1:
            raise TransferConflict("作品所有者已變更，請重新整理後再試", request_id=request_id, code="owner_changed")
        if not con.execute(
            "UPDATE ownership_transfer_requests SET status='COMPLETED', previous_owner_id=?, new_owner_id=?, "
            "reviewed_at=?, completed_at=?, updated_at=? WHERE id=? AND status='IN_REVIEW' AND reviewer_account_id=?",
            (request["current_owner_id"], request["target_account_id"], now, now, now, request_id, actor_id)).rowcount:
            raise TransferConflict("轉移申請狀態已變更", request_id=request_id, code="review_conflict")
        _event(con, request_id, "approved", actor_id=actor_id, from_status="IN_REVIEW",
               to_status="COMPLETED", previous_owner_id=request["current_owner_id"],
               new_owner_id=request["target_account_id"], metadata={
                   "publishedContinuity": bool(book["published_at"]),
                   "authorProfileId": book["author_profile_id"],
                   "sourceRevision": current_revision,
               })
        _audit(actor_id, "ownership_transfer_completed", request_id, request["book_id"], {
            "previousOwnerId": request["current_owner_id"],
            "newOwnerId": request["target_account_id"],
            "publishedContinuity": bool(book["published_at"]),
        })
        return _fetch_request(con, request_id)


def emergency(*, book_id: int, actor_id: int, target_account_id: int, reason: str) -> dict:
    reason = _safe_reason(reason, required=True)
    try:
        target_account_id = int(target_account_id)
    except (TypeError, ValueError):
        raise ValueError("targetAccountId 必須是明確的帳號 ID")
    with _write_transaction() as con:
        book = con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not book:
            raise TransferNotFound("找不到作品")
        book = dict(book)
        _ensure_active_account(con, actor_id, message="Super Admin 帳號不可用")
        _ensure_active_account(con, target_account_id, message="找不到可用的目標帳號")
        if int(book["owner_id"]) == target_account_id:
            raise TransferConflict("目標帳號不可與目前所有者相同", code="self_transfer")
        active = _active_generation(con, book_id)
        if active:
            raise TransferConflict("作品仍有未完成的生成任務，完成前不可緊急轉移所有權", code="active_generation")
        now = db.ts()
        revision = _book_revision_locked(con, book_id)
        cur = con.execute(
            "INSERT INTO ownership_transfer_requests(book_id,requester_account_id,current_owner_id,target_account_id,"
            "status,transfer_mode,source_revision,reason,expires_at,previous_owner_id,new_owner_id,completed_at,reviewed_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (book_id, actor_id, book["owner_id"], target_account_id, "COMPLETED", "emergency", revision, reason,
             now, book["owner_id"], target_account_id, now, now, now, now),
        )
        request_id = cur.lastrowid
        if con.execute("UPDATE books SET owner_id=? WHERE id=? AND owner_id=?",
                       (target_account_id, book_id, book["owner_id"])).rowcount != 1:
            raise TransferConflict("作品所有者已變更，請重新整理後再試", code="owner_changed")
        _event(con, request_id, "emergency_transfer", actor_id=actor_id, from_status=None,
               to_status="COMPLETED", reason=reason, previous_owner_id=book["owner_id"],
               new_owner_id=target_account_id, metadata={"emergency": True, "sourceRevision": revision})
        _audit(actor_id, "ownership_transfer_emergency", request_id, book_id, {
            "previousOwnerId": book["owner_id"], "newOwnerId": target_account_id,
            "reason": reason, "emergency": True,
        })
        return _fetch_request(con, request_id)


def get_request(request_id: int) -> dict | None:
    with _write_transaction() as con:
        _expire_due_locked(con)
        return _fetch_request(con, request_id)


def list_for_user(*, account_id: int, page: int = 1, page_size: int = 20) -> dict:
    # Expiry is a state mutation, so perform it before the bounded read.
    with _write_transaction() as con:
        _expire_due_locked(con)
        per = max(1, min(int(page_size or 20), 100))
        pg = max(1, int(page or 1))
        where = "(r.requester_account_id=? OR r.target_account_id=?)"
        args = (account_id, account_id)
        total = con.execute(f"SELECT COUNT(*) AS n FROM ownership_transfer_requests r WHERE {where}", args).fetchone()["n"]
        rows = con.execute(
            "SELECT r.*, b.bid, b.title AS book_title, b.status AS book_status, b.published_at, "
            "b.owner_id AS book_owner_id, owner.username AS owner_username, target.username AS target_username, "
            "requester.username AS requester_username, reviewer.username AS reviewer_username "
            "FROM ownership_transfer_requests r JOIN books b ON b.id=r.book_id "
            "JOIN users owner ON owner.id=r.current_owner_id JOIN users target ON target.id=r.target_account_id "
            "JOIN users requester ON requester.id=r.requester_account_id LEFT JOIN users reviewer ON reviewer.id=r.reviewer_account_id "
            f"WHERE {where} ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?",
            (*args, per, (pg - 1) * per)).fetchall()
        total_pages = (total + per - 1) // per if total else 0
        return {"items": [serialize_request(dict(row)) for row in rows], "total": total,
                "page": pg, "pageSize": per, "page_size": per, "totalPages": total_pages,
                "total_pages": total_pages, "hasNext": pg < total_pages, "has_next": pg < total_pages}


def list_queue(*, page: int = 1, page_size: int = 20, status: str = "", date_from: str = "", date_to: str = "") -> dict:
    with _write_transaction() as con:
        _expire_due_locked(con)
        per = max(1, min(int(page_size or 20), 100))
        pg = max(1, int(page or 1))
        allowed = set(ACTIVE_STATES)
        statuses = [value for value in (status.split(",") if status else ACTIVE_STATES) if value]
        if any(value not in allowed for value in statuses):
            raise ValueError("不支援的轉移審核狀態")
        marks = ",".join("?" for _ in statuses)
        where = [f"r.status IN ({marks})"]
        args: list[Any] = list(statuses)
        if date_from:
            where.append("date(r.created_at)>=?"); args.append(date_from[:10])
        if date_to:
            where.append("date(r.created_at)<=?"); args.append(date_to[:10])
        condition = " AND ".join(where)
        total = con.execute(f"SELECT COUNT(*) AS n FROM ownership_transfer_requests r WHERE {condition}", tuple(args)).fetchone()["n"]
        rows = con.execute(
            "SELECT r.*, b.bid, b.title AS book_title, b.status AS book_status, b.published_at, "
            "b.owner_id AS book_owner_id, owner.username AS owner_username, target.username AS target_username, "
            "requester.username AS requester_username, reviewer.username AS reviewer_username "
            "FROM ownership_transfer_requests r JOIN books b ON b.id=r.book_id "
            "JOIN users owner ON owner.id=r.current_owner_id JOIN users target ON target.id=r.target_account_id "
            "JOIN users requester ON requester.id=r.requester_account_id LEFT JOIN users reviewer ON reviewer.id=r.reviewer_account_id "
            f"WHERE {condition} ORDER BY r.created_at ASC, r.id ASC LIMIT ? OFFSET ?",
            (*args, per, (pg - 1) * per)).fetchall()
        total_pages = (total + per - 1) // per if total else 0
        return {"items": [serialize_request(dict(row)) for row in rows], "total": total,
                "page": pg, "pageSize": per, "page_size": per, "totalPages": total_pages,
                "total_pages": total_pages, "hasNext": pg < total_pages, "has_next": pg < total_pages}


def list_targets(*, account_id: int, query: str = "") -> list[dict]:
    query = (query or "").strip()[:MAX_SEARCH_LENGTH]
    args: list[Any] = [account_id]
    condition = "id<>? AND account_status='active'"
    if query:
        condition += " AND username LIKE ? COLLATE NOCASE"
        args.append(f"%{query}%")
    rows = db.query(f"SELECT id, username, role FROM users WHERE {condition} ORDER BY username COLLATE NOCASE, id LIMIT 20", tuple(args))
    return [{"id": row["id"], "username": row["username"], "role": row["role"]} for row in rows]


def serialize_request(row: dict, *, include_events: bool = False) -> dict:
    result = {
        "id": row["id"], "bookId": row["book_id"], "bookBid": row.get("bid"),
        "bookTitle": row.get("book_title"), "status": row["status"],
        "transferMode": row.get("transfer_mode") or "normal", "requesterAccountId": row["requester_account_id"],
        "requester": row.get("requester_username"), "currentOwnerId": row["current_owner_id"],
        "currentOwner": row.get("owner_username"), "targetAccountId": row["target_account_id"],
        "target": row.get("target_username"), "reviewerAccountId": row.get("reviewer_account_id"),
        "reviewer": row.get("reviewer_username"), "sourceRevision": row.get("source_revision") or "",
        "reason": row.get("reason") or "", "expiresAt": row.get("expires_at"),
        "targetActedAt": row.get("target_acted_at"), "reviewStartedAt": row.get("review_started_at"),
        "reviewedAt": row.get("reviewed_at"), "previousOwnerId": row.get("previous_owner_id"),
        "newOwnerId": row.get("new_owner_id"), "completedAt": row.get("completed_at"),
        "bookOwnerId": row.get("book_owner_id"), "bookStatus": row.get("book_status"),
        "publishedAt": row.get("published_at"), "authorProfileId": row.get("author_profile_id"),
        "createdAt": row.get("created_at"), "updatedAt": row.get("updated_at"),
    }
    if include_events:
        result["events"] = _fetch_events(db._conn(), int(row["id"]))
    return result
