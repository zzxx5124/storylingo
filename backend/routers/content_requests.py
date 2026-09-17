"""Author request and Reviewer workflow API."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from .. import exceptions as exc
from ..security import get_current_user
from ..services import book_service, content_requests as request_service, generation_orchestration, policy

router = APIRouter(prefix="/api", tags=["content-requests"])


def _user(user: Optional[dict]) -> dict:
    if not user:
        raise exc.unauthorized()
    account = policy.current_account(user)
    if not account:
        raise exc.unauthorized()
    return account


def _request_or_404(request_id: int) -> dict:
    row = request_service.get_request(request_id)
    if not row:
        raise exc.not_found("找不到申請")
    return row


def _detail(row: dict, user: dict) -> dict:
    allowed = row["requester_account_id"] == user["id"] or policy.can_review_content_request(user)
    if not allowed:
        # Do not disclose whether another account's private request exists.
        raise exc.not_found("找不到申請")
    return request_service.serialize_request(row, include_events=True, include_book=policy.can_review_content_request(user))


def _handle_error(error: Exception):
    if isinstance(error, request_service.RequestNotFound):
        raise exc.not_found("找不到申請")
    if isinstance(error, request_service.RequestConflict):
        message = str(error)
        if error.request_id:
            message += f"（既有申請 #{error.request_id}）"
        raise exc.conflict(message, code=error.code)
    if isinstance(error, PermissionError):
        raise exc.forbidden(str(error))
    if isinstance(error, ValueError):
        raise exc.bad_request(str(error))
    raise error


@router.post("/books/{bid}/requests")
@router.post("/books/{bid}/content-requests")
def submit_request(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    book = book_service.load_book(bid)
    request_type = payload.get("requestType") or payload.get("type")
    request_policy = {
        "publish": policy.can_request_publish,
        "unpublish": policy.can_request_unpublish,
        "audiobook": policy.can_request_audiobook,
    }.get(request_type)
    if not request_policy or not request_policy(user, book):
        raise exc.forbidden()
    try:
        row = request_service.submit(book=book, requester_id=user["id"], request_type=request_type, payload=payload)
    except Exception as error:
        _handle_error(error)
    return request_service.serialize_request(row, include_events=True)


@router.get("/requests")
@router.get("/content-requests")
def list_own_requests(user: Annotated[Optional[dict], Depends(get_current_user)], page: int = 1, page_size: int = 20):
    user = _user(user)
    per = max(1, min(int(page_size or 20), 100))
    pg = max(1, int(page or 1))
    total = db.query_one("SELECT COUNT(*) AS n FROM content_requests WHERE requester_account_id=?", (user["id"],))["n"]
    rows = db.query(
        "SELECT r.*, b.bid, b.title AS book_title, b.owner_id, b.status AS book_status, b.published_at, "
        "u.username AS requester_username, rv.username AS reviewer_username "
        "FROM content_requests r JOIN books b ON b.id=r.book_id JOIN users u ON u.id=r.requester_account_id "
        "LEFT JOIN users rv ON rv.id=r.reviewer_account_id WHERE r.requester_account_id=? "
        "ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?",
        (user["id"], per, (pg - 1) * per),
    )
    return {"items": [request_service.serialize_request(row) for row in rows], "total": total,
            "page": pg, "pageSize": per, "totalPages": (total + per - 1) // per if total else 0}


@router.get("/requests/{request_id}")
@router.get("/content-requests/{request_id}")
def get_own_request(request_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    return _detail(_request_or_404(request_id), user)


@router.post("/requests/{request_id}/cancel")
@router.post("/content-requests/{request_id}/cancel")
def cancel_request(request_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    row = _request_or_404(request_id)
    if not policy.can_cancel_own_request(user, row["requester_account_id"]):
        raise exc.forbidden()
    try:
        result = request_service.cancel(request_id=request_id, actor_id=user["id"])
    except Exception as error:
        _handle_error(error)
    return request_service.serialize_request(result, include_events=True)


@router.get("/review/requests")
@router.get("/content-requests/queue")
def review_queue(user: Annotated[Optional[dict], Depends(get_current_user)], request_type: str = "",
                status: str = "", date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 20):
    user = _user(user)
    if not policy.can_review_content_request(user):
        raise exc.forbidden()
    return request_service.list_queue(request_type=request_type, status=status, date_from=date_from,
                                       date_to=date_to, page=page, page_size=page_size)


@router.get("/review/requests/{request_id}")
def review_request_detail(request_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    if not policy.can_review_content_request(user):
        raise exc.forbidden()
    return _detail(_request_or_404(request_id), user)


@router.post("/review/requests/{request_id}/start")
@router.post("/content-requests/{request_id}/start-review")
def start_review(request_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    if not policy.can_review_content_request(user):
        raise exc.forbidden()
    try:
        row = request_service.start_review(request_id=request_id, actor_id=user["id"])
    except Exception as error:
        _handle_error(error)
    return request_service.serialize_request(row, include_events=True)


@router.post("/review/requests/{request_id}/approve")
@router.post("/content-requests/{request_id}/approve")
def approve_request(request_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    if not policy.can_review_content_request(user):
        raise exc.forbidden()
    try:
        row = request_service.decide(request_id=request_id, actor_id=user["id"], decision="approve")
    except Exception as error:
        _handle_error(error)
    return request_service.serialize_request(row, include_events=True)


@router.post("/review/requests/{request_id}/reject")
@router.post("/content-requests/{request_id}/reject")
def reject_request(request_id: int, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    if not policy.can_review_content_request(user):
        raise exc.forbidden()
    try:
        row = request_service.decide(request_id=request_id, actor_id=user["id"], decision="reject",
                                     reason=payload.get("reason") if isinstance(payload, dict) else "")
    except Exception as error:
        _handle_error(error)
    return request_service.serialize_request(row, include_events=True)


@router.post("/review/requests/{request_id}/generation")
@router.post("/content-requests/{request_id}/generation")
def start_generation(request_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    if not policy.can_operate_generation(user):
        raise exc.forbidden()
    row = _request_or_404(request_id)
    if row["requester_account_id"] == user["id"]:
        raise exc.forbidden("申請人不可操作自己的核准請求")
    if row["request_type"] != "audiobook" or row["status"] != "APPROVED" or not row["generation_authorized"]:
        raise exc.conflict("只有已核准的 audiobook 申請才能啟動生成", code="generation_not_authorized")
    provider = db.get_active_tts_provider()
    if not provider:
        raise HTTPException(503, "尚未設定可用的 TTS provider，無法生成音訊")
    try:
        row = request_service.start_generation_operation(request_id, user["id"], provider)
    except Exception as error:
        _handle_error(error)
    return request_service.serialize_request(row, include_events=True)


def _generation_access(user: dict, operation_id: int | None = None, job_id: int | None = None):
    if not policy.can_operate_generation(user):
        raise exc.forbidden()
    operation = generation_orchestration.safe_operation(operation_id) if operation_id else None
    if job_id and not operation:
        job = db.get_generation_job(job_id)
        operation = generation_orchestration.safe_operation(job.get("operation_id")) if job and job.get("operation_id") else None
    if policy.can_manage_provider_config(user):
        return operation
    # Reviewer access is limited to an approved audiobook operation.  The
    # operation projection contains no request payload or provider secret.
    if not operation or not operation.get("requestId"):
        raise exc.not_found("找不到生成操作")
    request = request_service.get_request(operation["requestId"])
    if not request or request.get("request_type") != "audiobook" or request.get("status") != "APPROVED":
        raise exc.not_found("找不到生成操作")
    return operation


@router.get("/review/generation/operations")
def review_generation_operations(user: Annotated[Optional[dict], Depends(get_current_user)],
                                 service_type: str = "", status: str = "", page: int = 1, page_size: int = 20):
    user = _user(user)
    if not policy.can_operate_generation(user):
        raise exc.forbidden()
    return db.list_generation_operations(
        service_type=service_type, status=status, page=page, page_size=page_size,
        approved_only=not policy.can_manage_provider_config(user),
    )


@router.get("/review/generation/operations/{operation_id}")
def review_generation_operation(operation_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    operation = _generation_access(user, operation_id=operation_id)
    if not operation:
        raise exc.not_found("找不到生成操作")
    return operation


def _generation_job_mutation(job_id: int, action: str, user: dict):
    job = db.get_generation_job(job_id)
    if not job:
        raise exc.not_found("找不到生成任務")
    _generation_access(user, job_id=job_id)
    if action == "cancel":
        result = db.request_generation_job_cancel(job_id, actor_id=user["id"])
        if result == "conflict":
            raise exc.conflict("生成任務狀態已變更", code="generation_conflict")
        return generation_orchestration.safe_job(job_id)
    if action == "retry":
        if job.get("status") not in ("failed", "stale"):
            raise exc.conflict("只有失敗或過期任務可以重試", code="generation_not_retryable")
        if not db.retry_generation_job(job_id, actor_id=user["id"]):
            raise exc.conflict("生成任務狀態已變更", code="generation_conflict")
        return generation_orchestration.safe_job(job_id)
    raise exc.bad_request("不支援的生成操作")


@router.post("/review/generation/jobs/{job_id}/cancel")
def cancel_review_generation_job(job_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    return _generation_job_mutation(job_id, "cancel", _user(user))


@router.post("/review/generation/jobs/{job_id}/retry")
def retry_review_generation_job(job_id: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    return _generation_job_mutation(job_id, "retry", _user(user))


@router.post("/admin/books/{bid}/emergency-hide")
def emergency_hide(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _user(user)
    if not policy.can_emergency_hide(user):
        raise exc.forbidden()
    book = book_service.load_book(bid)
    try:
        row = request_service.emergency_hide(book_id=book["id"], actor_id=user["id"], reason=(payload or {}).get("reason", ""))
    except Exception as error:
        _handle_error(error)
    return {"ok": True, "status": row["status"], "reason": (payload or {}).get("reason", "").strip()[:500]}
