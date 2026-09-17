"""Canonical content-request aggregate and its atomic transition rules."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .. import db
from . import generation_orchestration as orchestration
from . import notifications as notification_service

REQUEST_TYPES = ("publish", "unpublish", "audiobook")
STATES = ("SUBMITTED", "IN_REVIEW", "APPROVED", "REJECTED", "CANCELLED", "INVALIDATED")
ACTIVE_STATES = ("SUBMITTED", "IN_REVIEW")


class RequestConflict(Exception):
    def __init__(self, message: str, *, request_id: int | None = None, code: str = "request_conflict"):
        super().__init__(message)
        self.request_id = request_id
        self.code = code


class RequestNotFound(Exception):
    pass


def _safe_json(value: Any, *, limit: int = 2000) -> str:
    if not isinstance(value, dict):
        value = {}
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"
    # Never persist a sliced JSON document: malformed history is harder to
    # audit and can make a later read fail.  Request metadata is deliberately
    # small; an oversized value is represented by a safe bounded marker.
    return raw if len(raw) <= limit else '{"truncated":true}'


def _book_payload(book: dict, chapters: list[dict], request_type: str, payload: dict | None) -> dict:
    try:
        categories = json.loads(book.get("categories") or "[]")
    except (TypeError, ValueError):
        categories = []
    try:
        settings = json.loads(book.get("settings") or "{}")
    except (TypeError, ValueError):
        settings = {}
    common = {
        "title": book.get("title") or "",
        "synopsis": book.get("synopsis") or "",
        "tags": book.get("tags") or "",
        "category": book.get("category") or "",
        "vocab_level": book.get("vocab_level") or "",
        "categories": categories,
        "serial": book.get("serial") or "",
        "cover_path": book.get("cover_path") or "",
        "author_profile_id": book.get("author_profile_id"),
        "legacy_author_name": book.get("legacy_author_name") or "",
        "age_rating": book.get("age_rating") or "general",
        "content_warning": book.get("content_warning") or "",
        "visibility": book.get("visibility") or "public",
        "access_policy": book.get("access_policy") or "free",
        "has_prologue": settings.get("hasPrologue", True),
    }
    if request_type == "audiobook":
        common.update({
            "audio_mode": book.get("audio_mode") or "single",
            "default_voice_id": book.get("default_voice_id") or "",
            "voice_prefs": book.get("voice_prefs") or "{}",
            "author_audio_config": {
                k: (payload or {}).get(k)
                for k in ("audioMode", "defaultVoiceId", "voicePreferences")
                if k in (payload or {})
            },
        })
        common["chapters"] = [
            {"seq": c.get("seq"), "title": c.get("title") or "", "text": c.get("text") or ""}
            for c in chapters
        ]
    else:
        common["chapters"] = [
            {"seq": c.get("seq"), "title": c.get("title") or "", "text": c.get("text") or "",
             "publish_status": c.get("publish_status") or "published"}
            for c in chapters
        ]
    return common


def calculate_revision(book: dict, request_type: str, payload: dict | None = None) -> str:
    """Stable content revision; operational timestamps/status fields are excluded."""
    if request_type not in REQUEST_TYPES:
        raise ValueError("不支援的 request type")
    chapters = sorted(db.list_chapters(book["id"]), key=lambda row: (int(row.get("seq") or 0), int(row["id"])))
    source = _book_payload(book, chapters, request_type, payload)
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _event(con, request_id: int, event_type: str, *, actor_id: int | None,
           from_status: str | None = None, to_status: str | None = None,
           reason: str = "", metadata: dict | None = None):
    con.execute(
        "INSERT INTO content_request_events(request_id,event_type,from_status,to_status,actor_account_id,reason,metadata_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (request_id, event_type, from_status, to_status, actor_id, (reason or "")[:500],
         _safe_json(metadata), db.ts()),
    )


def get_request(request_id: int) -> dict | None:
    return db.query_one(
        "SELECT r.*, b.bid, b.title AS book_title, b.owner_id, b.status AS book_status, b.published_at, "
        "u.username AS requester_username, rv.username AS reviewer_username "
        "FROM content_requests r JOIN books b ON b.id=r.book_id "
        "JOIN users u ON u.id=r.requester_account_id LEFT JOIN users rv ON rv.id=r.reviewer_account_id "
        "WHERE r.id=?", (request_id,))


def list_events(request_id: int) -> list[dict]:
    rows = db.query("SELECT * FROM content_request_events WHERE request_id=? ORDER BY id ASC", (request_id,))
    for row in rows:
        try:
            row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            row["metadata"] = {}
    return rows


def serialize_request(row: dict, *, include_events: bool = False, include_book: bool = False) -> dict:
    operation_job = db.get_generation_job(row["operation_job_id"]) if row.get("operation_job_id") else None
    result = {
        "id": row["id"], "bookId": row["book_id"], "bookBid": row.get("bid"),
        "bookTitle": row.get("book_title"), "requestType": row["request_type"],
        "requesterAccountId": row["requester_account_id"],
        "requester": row.get("requester_username"), "status": row["status"],
        "submittedRevision": row["submitted_revision"],
        "reviewerAccountId": row.get("reviewer_account_id"), "reviewer": row.get("reviewer_username"),
        "submittedAt": row["submitted_at"], "reviewStartedAt": row.get("review_started_at"),
        "reviewedAt": row.get("reviewed_at"), "decisionReason": row.get("decision_reason") or "",
        "generationAuthorized": bool(row.get("generation_authorized")),
        "operationJobId": row.get("operation_job_id"),
        "operationId": operation_job.get("operation_id") if operation_job else None,
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }
    if include_events:
        result["events"] = list_events(row["id"])
    if include_book:
        book = db.get_book_by_rowid(row["book_id"])
        chapters = db.list_chapters(row["book_id"])
        result["book"] = {
            "id": row["book_id"], "bid": row.get("bid"), "title": row.get("book_title"),
            "status": row.get("book_status"), "publishedAt": row.get("published_at"),
            "ownerId": row.get("owner_id"), "currentRevision": calculate_revision(book, row["request_type"], _payload(row)),
            "chapters": [{"seq": c["seq"], "title": c["title"], "text": c["text"],
                           "status": c.get("status"), "publishStatus": c.get("publish_status")} for c in chapters],
        }
        result["requestPayload"] = _payload(row)
    return result


def _payload(row: dict) -> dict:
    try:
        value = json.loads(row.get("request_payload_json") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def submit(*, book: dict, requester_id: int, request_type: str, payload: dict | None = None) -> dict:
    if request_type not in REQUEST_TYPES:
        raise ValueError("requestType 僅能為 publish、unpublish 或 audiobook")
    payload = payload if isinstance(payload, dict) else {}
    if request_type == "publish" and book.get("status") == "approved" and book.get("published_at"):
        raise RequestConflict("作品已公開，請提出 unpublish 申請", code="publish_not_eligible")
    safe_payload = {k: payload[k] for k in ("audioMode", "defaultVoiceId", "voicePreferences") if k in payload}
    if request_type == "unpublish" and not (book.get("status") == "approved" and book.get("published_at")):
        raise RequestConflict("只有已公開作品才能提出下架申請", code="unpublish_not_eligible")
    revision = calculate_revision(book, request_type, safe_payload)
    now = db.ts()
    with db.atomic() as con:
        existing = con.execute(
            "SELECT id FROM content_requests WHERE book_id=? AND request_type=? AND status IN ('SUBMITTED','IN_REVIEW')",
            (book["id"], request_type)).fetchone()
        if existing:
            raise RequestConflict("相同作品已有進行中的申請", request_id=existing["id"], code="active_request_exists")
        try:
            cur = con.execute(
                "INSERT INTO content_requests(book_id,request_type,requester_account_id,status,submitted_revision,submitted_at,"
                "decision_reason,request_payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (book["id"], request_type, requester_id, "SUBMITTED", revision, now, "",
                 _safe_json(safe_payload), now, now),
            )
        except sqlite3.IntegrityError as error:
            existing = con.execute(
                "SELECT id FROM content_requests WHERE book_id=? AND request_type=? AND status IN ('SUBMITTED','IN_REVIEW')",
                (book["id"], request_type)).fetchone()
            if existing:
                raise RequestConflict("相同作品已有進行中的申請", request_id=existing["id"], code="active_request_exists") from error
            raise
        request_id = cur.lastrowid
        _event(con, request_id, "submitted", actor_id=requester_id, to_status="SUBMITTED",
               metadata={"requestType": request_type, "submittedRevision": revision})
        db.add_audit_log(requester_id, "content_request_submitted", "content_request", request_id,
                         {"requestType": request_type, "bookId": book["id"]})
    return get_request(request_id)


def cancel(*, request_id: int, actor_id: int) -> dict:
    with db.atomic() as con:
        row = con.execute("SELECT * FROM content_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise RequestNotFound()
        if row["requester_account_id"] != actor_id:
            raise PermissionError("只能取消自己提交的申請")
        if row["status"] != "SUBMITTED" or row["reviewer_account_id"] is not None:
            raise RequestConflict("申請已進入審核或已結束，無法取消", request_id=request_id, code="request_not_cancellable")
        cur = con.execute(
            "UPDATE content_requests SET status='CANCELLED', reviewed_at=?, updated_at=? "
            "WHERE id=? AND requester_account_id=? AND status='SUBMITTED' AND reviewer_account_id IS NULL",
            (db.ts(), db.ts(), request_id, actor_id))
        if cur.rowcount != 1:
            raise RequestConflict("申請狀態已變更，無法取消", request_id=request_id, code="request_conflict")
        _event(con, request_id, "cancelled", actor_id=actor_id, from_status="SUBMITTED", to_status="CANCELLED")
        db.add_audit_log(actor_id, "content_request_cancelled", "content_request", request_id, {})
    return get_request(request_id)


def start_review(*, request_id: int, actor_id: int) -> dict:
    with db.atomic() as con:
        row = con.execute("SELECT * FROM content_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise RequestNotFound()
        if row["requester_account_id"] == actor_id:
            raise PermissionError("requester 不得審核自己的申請")
        now = db.ts()
        cur = con.execute(
            "UPDATE content_requests SET status='IN_REVIEW', reviewer_account_id=?, review_started_at=?, updated_at=? "
            "WHERE id=? AND status='SUBMITTED' AND reviewer_account_id IS NULL",
            (actor_id, now, now, request_id))
        if cur.rowcount != 1:
            raise RequestConflict("申請已被其他 Reviewer 認領或已變更", request_id=request_id, code="review_claim_conflict")
        _event(con, request_id, "review_started", actor_id=actor_id, from_status="SUBMITTED", to_status="IN_REVIEW")
        db.add_audit_log(actor_id, "content_request_review_started", "content_request", request_id, {})
    return get_request(request_id)


def decide(*, request_id: int, actor_id: int, decision: str, reason: str = "") -> dict:
    if decision not in ("approve", "reject"):
        raise ValueError("決策僅能為 approve 或 reject")
    reason = (reason or "").strip()[:500]
    if decision == "reject" and not reason:
        raise ValueError("退回申請必須填寫原因")
    with db.atomic() as con:
        row = con.execute("SELECT * FROM content_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise RequestNotFound()
        if row["requester_account_id"] == actor_id:
            raise PermissionError("requester 不得核准或退回自己的申請")
        if row["status"] != "IN_REVIEW":
            raise RequestConflict("申請不在可決策狀態", request_id=request_id, code="invalid_request_transition")
        if row["reviewer_account_id"] != actor_id:
            raise PermissionError("只有認領此申請的 Reviewer 可以決策")
        book = con.execute("SELECT * FROM books WHERE id=?", (row["book_id"],)).fetchone()
        if not book:
            raise RequestNotFound()
        book = dict(book)
        current_revision = calculate_revision(book, row["request_type"], _payload(dict(row)))
        if current_revision != row["submitted_revision"]:
            now = db.ts()
            cur = con.execute(
                "UPDATE content_requests SET status='INVALIDATED', reviewed_at=?, decision_reason=?, updated_at=? "
                "WHERE id=? AND status='IN_REVIEW' AND submitted_revision=?",
                (now, "作品內容已變更，請重新提交。", now, request_id, row["submitted_revision"]))
            if cur.rowcount == 1:
                _event(con, request_id, "invalidated", actor_id=actor_id, from_status="IN_REVIEW", to_status="INVALIDATED",
                       reason="作品內容已變更，請重新提交。", metadata={"submittedRevision": row["submitted_revision"], "currentRevision": current_revision})
                db.add_audit_log(actor_id, "content_request_invalidated", "content_request", request_id, {})
                notification_service.create_notification(
                    row["requester_account_id"], "content_request.invalidated", "review",
                    "內容申請已失效", "作品內容在審核期間發生變更，請重新提交。",
                    dedupe_key=f"content_request:{request_id}:invalidated",
                    target_type="content_request", target_id=request_id,
                    target_route=f"#/requests/{request_id}", source_type="content_request",
                    source_id=request_id, source_request_id=request_id, source_book_id=row["book_id"], con=con)
                # Invalidation is the durable domain result; persist it before
                # returning the 409 conflict to the client.
                con.commit()
            raise RequestConflict("作品內容已變更，申請已失效，請作者重新提交", request_id=request_id, code="stale_revision")
        now = db.ts()
        new_status = "APPROVED" if decision == "approve" else "REJECTED"
        authorized = 1 if decision == "approve" and row["request_type"] == "audiobook" else 0
        cur = con.execute(
            "UPDATE content_requests SET status=?, reviewed_at=?, decision_reason=?, generation_authorized=?, updated_at=? "
            "WHERE id=? AND status='IN_REVIEW' AND reviewer_account_id=?",
            (new_status, now, reason, authorized, now, request_id, actor_id))
        if cur.rowcount != 1:
            raise RequestConflict("申請已被其他操作完成", request_id=request_id, code="request_conflict")
        if decision == "approve" and row["request_type"] == "publish":
            con.execute("UPDATE books SET status='approved', reject_reason='', published_at=COALESCE(published_at, ?), "
                        "last_chapter_at=COALESCE(last_chapter_at, ?), updated_at=? WHERE id=?",
                        (now, now, now, row["book_id"]))
        elif decision == "approve" and row["request_type"] == "unpublish":
            con.execute("UPDATE books SET status='removed', updated_at=? WHERE id=?", (now, row["book_id"]))
        elif decision == "reject" and row["request_type"] == "publish":
            # Request decision and Book publication truth remain separate,
            # while the existing non-public rejection projection keeps the
            # reason visible to the Author workflow.
            con.execute("UPDATE books SET status='rejected', reject_reason=?, updated_at=? WHERE id=? AND status <> 'approved'",
                        (reason, now, row["book_id"]))
        _event(con, request_id, "approved" if decision == "approve" else "rejected", actor_id=actor_id,
               from_status="IN_REVIEW", to_status=new_status, reason=reason,
               metadata={"generationAuthorization": bool(authorized)})
        db.add_audit_log(actor_id, "content_request_" + decision, "content_request", request_id,
                         {"requestType": row["request_type"], "bookId": row["book_id"]})
        if decision == "approve" and row["request_type"] in ("publish", "unpublish"):
            db.add_audit_log(actor_id, "publish_transition" if row["request_type"] == "publish" else "unpublish_transition",
                             "book", row["book_id"], {"requestId": request_id})
        if decision == "approve" and row["request_type"] == "audiobook":
            db.add_audit_log(actor_id, "audiobook_authorized", "content_request", request_id, {})
            notification_service.create_notification(
                row["requester_account_id"], "audiobook.authorized", "audiobook",
                "有聲書申請已核准", "申請已核准，平台將依序開始處理有聲書生成。",
                dedupe_key=f"content_request:{request_id}:audiobook_authorized",
                target_type="content_request", target_id=request_id,
                target_route=f"#/requests/{request_id}", source_type="content_request",
                source_id=request_id, source_request_id=request_id, source_book_id=row["book_id"], con=con)
        elif decision == "approve":
            notification_service.create_notification(
                row["requester_account_id"], "content_request.approved", "review",
                "內容申請已核准", "你的內容申請已核准。",
                dedupe_key=f"content_request:{request_id}:approved",
                target_type="content_request", target_id=request_id,
                target_route=f"#/requests/{request_id}", source_type="content_request",
                source_id=request_id, source_request_id=request_id, source_book_id=row["book_id"], con=con)
        else:
            notification_service.create_notification(
                row["requester_account_id"], "content_request.rejected", "review",
                "內容申請已退回", reason or "申請需要補充資料後重新提交。",
                dedupe_key=f"content_request:{request_id}:rejected",
                target_type="content_request", target_id=request_id,
                target_route=f"#/requests/{request_id}", source_type="content_request",
                source_id=request_id, source_request_id=request_id, source_book_id=row["book_id"], con=con)
    return get_request(request_id)


def list_queue(*, request_type: str = "", status: str = "", date_from: str = "", date_to: str = "",
               page: int = 1, page_size: int = 20) -> dict:
    where, params = [], []
    if request_type in REQUEST_TYPES:
        where.append("r.request_type=?")
        params.append(request_type)
    if status in STATES:
        where.append("r.status=?")
        params.append(status)
    elif not status:
        where.append("r.status IN ('SUBMITTED','IN_REVIEW')")
    if date_from:
        where.append("date(r.submitted_at)>=?")
        params.append(date_from[:10])
    if date_to:
        where.append("date(r.submitted_at)<=?")
        params.append(date_to[:10])
    cond = " WHERE " + " AND ".join(where) if where else ""
    base = "FROM content_requests r JOIN books b ON b.id=r.book_id JOIN users u ON u.id=r.requester_account_id " \
           "LEFT JOIN users rv ON rv.id=r.reviewer_account_id"
    total = db.query_one("SELECT COUNT(*) n " + base + cond, tuple(params))["n"]
    per = max(1, min(int(page_size or 20), 100))
    pg = max(1, int(page or 1))
    rows = db.query(
        "SELECT r.*, b.bid, b.title AS book_title, b.owner_id, b.status AS book_status, b.published_at, "
        "u.username AS requester_username, rv.username AS reviewer_username " + base + cond +
        " ORDER BY r.submitted_at ASC, r.id ASC LIMIT ? OFFSET ?",
        (*params, per, (pg - 1) * per))
    return {"items": [serialize_request(row) for row in rows], "total": total, "page": pg,
            "pageSize": per, "totalPages": (total + per - 1) // per if total else 0}


def start_generation_operation(request_id: int, actor_id: int, provider: dict) -> dict:
    """原子建立 DB queue job + request linkage，避免 double-click 孤兒 job。"""
    with db.atomic() as con:
        row = con.execute("SELECT * FROM content_requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise RequestNotFound()
        if row["request_type"] != "audiobook" or row["status"] != "APPROVED" or not row["generation_authorized"]:
            raise RequestConflict("只有已核准的 audiobook 申請才能啟動生成", request_id=request_id, code="generation_not_authorized")
        if row["operation_job_id"]:
            return get_request(request_id)
        book = db.get_book_by_rowid(row["book_id"])
        if not book:
            raise RequestNotFound()
        revision = calculate_revision(book, "audiobook", _payload(dict(row)))
        operation_id, job_id = orchestration.enqueue_audiobook_operation(
            request=dict(row), book=book, actor_id=actor_id, provider=provider,
            source_revision_value=revision,
            payload={"bid": book.get("bid"), "seq": None, "requestPayload": _payload(dict(row))},
        )
        now = db.ts()
        updated = con.execute("UPDATE content_requests SET operation_job_id=?, updated_at=? WHERE id=? AND operation_job_id IS NULL",
                              (job_id, now, request_id))
        if updated.rowcount != 1:
            raise RequestConflict("生成操作已被其他請求建立", request_id=request_id, code="operation_conflict")
        _event(con, request_id, "privileged_override", actor_id=actor_id,
               metadata={"operationId": operation_id, "operationJobId": job_id, "kind": "audiobook_generation_started"})
        db.add_audit_log(actor_id, "audiobook_generation_operation_started", "content_request", request_id,
                         {"jobId": job_id, "operationId": operation_id})
    return get_request(request_id)


def emergency_hide(*, book_id: int, actor_id: int, reason: str) -> dict:
    reason = (reason or "").strip()[:500]
    if not reason:
        raise ValueError("緊急隱藏必須填寫原因")
    with db.atomic() as con:
        book = con.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not book:
            raise RequestNotFound()
        if book["status"] != "approved" or not book["published_at"]:
            raise RequestConflict("只有已公開作品才能緊急隱藏", code="emergency_hide_not_eligible")
        now = db.ts()
        con.execute("UPDATE books SET status='removed', updated_at=? WHERE id=?", (now, book_id))
        active = con.execute("SELECT id,status FROM content_requests WHERE book_id=? AND status IN ('SUBMITTED','IN_REVIEW')",
                             (book_id,)).fetchall()
        for request in active:
            updated = con.execute(
                "UPDATE content_requests SET status='INVALIDATED', reviewed_at=?, decision_reason=?, updated_at=? "
                "WHERE id=? AND status IN ('SUBMITTED','IN_REVIEW')",
                (now, "作品已因緊急治理而隱藏，請重新提交。", now, request["id"]))
            if updated.rowcount == 1:
                _event(con, request["id"], "invalidated", actor_id=actor_id,
                       from_status=request["status"], to_status="INVALIDATED",
                       reason="作品已因緊急治理而隱藏，請重新提交。",
                       metadata={"action": "EMERGENCY_HIDE", "bookId": book_id})
                _event(con, request["id"], "privileged_override", actor_id=actor_id,
                       reason=reason, metadata={"action": "EMERGENCY_HIDE", "bookId": book_id})
        db.add_audit_log(actor_id, "emergency_hide", "book", book_id, {"reason": reason})
        return db.get_book_by_rowid(book_id)
