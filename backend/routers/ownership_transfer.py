"""Ownership Transfer API with strict actor and capability boundaries."""
from __future__ import annotations

import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request

from .. import db
from .. import exceptions as exc
from ..security import get_current_user
from ..services import book_service, ownership_transfer as transfer_service, policy

router = APIRouter(prefix="/api", tags=["ownership-transfer"])
RECENT_AUTH_SECONDS = 10 * 60


def _user(user: Optional[dict]) -> dict:
    if not user:
        raise exc.unauthorized()
    account = policy.current_account(user)
    if not account:
        raise exc.unauthorized()
    return account


def _handle_error(error: Exception):
    if isinstance(error, transfer_service.TransferNotFound):
        raise exc.not_found("找不到 ownership transfer")
    if isinstance(error, transfer_service.TransferConflict):
        message = str(error)
        if error.request_id:
            message += f"（申請 #{error.request_id}）"
        raise exc.conflict(message, code=error.code)
    if isinstance(error, PermissionError):
        raise exc.forbidden(str(error))
    if isinstance(error, ValueError):
        raise exc.bad_request(str(error))
    raise error


def _book(bid: str) -> dict:
    return book_service.load_book(bid)


def _reviewer(user: dict):
    if not policy.can_review_content_request(user):
        raise exc.forbidden()


def _detail_allowed(row: dict, user: dict) -> bool:
    return (int(row["requester_account_id"]) == int(user["id"])
            or int(row["target_account_id"]) == int(user["id"])
            or policy.can_review_content_request(user))


def _recent_auth(request: Request, user: dict):
    if user.get("dev"):
        raise exc.forbidden("開發虛擬帳號不支援此安全操作")
    auth_time = int(user.get("auth_time") or 0)
    if not auth_time or time.time() - auth_time > RECENT_AUTH_SECONDS:
        raise exc.unauthorized("此操作需要近期重新登入")


@router.get("/ownership-transfers/targets")
def transfer_targets(query: str = "", user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    if not policy.can_manage_owned_book(user, {"owner_id": user["id"]}):
        raise exc.forbidden()
    return {"items": transfer_service.list_targets(account_id=user["id"], query=query)}


@router.post("/books/{bid}/ownership-transfers")
def submit_transfer(bid: str, payload: dict,
                    user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    book = _book(bid)
    if not policy.can_manage_owned_book(user, book) or int(book["owner_id"]) != int(user["id"]):
        raise exc.forbidden()
    target_id = (payload or {}).get("targetAccountId")
    try:
        row = transfer_service.submit(book_id=int(book["_rowId"] if "_rowId" in book else db.get_book_row(bid)["id"]),
                                      requester_id=user["id"], target_account_id=target_id,
                                      reason=(payload or {}).get("reason", ""))
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.get("/ownership-transfers")
def list_transfers(page: int = 1, page_size: int = 20,
                   user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    return transfer_service.list_for_user(account_id=user["id"], page=page, page_size=page_size)


@router.get("/ownership-transfers/{request_id}")
def transfer_detail(request_id: int,
                    user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    row = transfer_service.get_request(request_id)
    if not row or not _detail_allowed(row, user):
        raise exc.not_found("找不到 ownership transfer")
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/ownership-transfers/{request_id}/accept")
def accept_transfer(request_id: int,
                    user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    try:
        row = transfer_service.accept_target(request_id=request_id, actor_id=user["id"])
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/ownership-transfers/{request_id}/reject")
def reject_target(request_id: int, payload: dict,
                  user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    try:
        row = transfer_service.reject_target(request_id=request_id, actor_id=user["id"],
                                             reason=(payload or {}).get("reason", ""))
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/ownership-transfers/{request_id}/cancel")
def cancel_transfer(request_id: int,
                    user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    try:
        row = transfer_service.cancel(request_id=request_id, actor_id=user["id"])
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.get("/review/ownership-transfers")
def review_transfer_queue(page: int = 1, page_size: int = 20, status: str = "",
                           date_from: str = "", date_to: str = "",
                           user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    _reviewer(user)
    try:
        return transfer_service.list_queue(page=page, page_size=page_size, status=status,
                                           date_from=date_from, date_to=date_to)
    except Exception as error:
        _handle_error(error)


@router.get("/review/ownership-transfers/{request_id}")
def review_transfer_detail(request_id: int,
                           user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    _reviewer(user)
    row = transfer_service.get_request(request_id)
    if not row:
        raise exc.not_found("找不到 ownership transfer")
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/review/ownership-transfers/{request_id}/start")
def start_transfer_review(request_id: int,
                          user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    _reviewer(user)
    try:
        row = transfer_service.start_review(request_id=request_id, actor_id=user["id"])
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/review/ownership-transfers/{request_id}/approve")
def approve_transfer(request_id: int,
                     user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    _reviewer(user)
    try:
        row = transfer_service.approve(request_id=request_id, actor_id=user["id"])
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/review/ownership-transfers/{request_id}/reject")
def reject_transfer(request_id: int, payload: dict,
                    user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    user = _user(user)
    _reviewer(user)
    try:
        row = transfer_service.reject_review(request_id=request_id, actor_id=user["id"],
                                             reason=(payload or {}).get("reason", ""))
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)


@router.post("/admin/books/{bid}/ownership-transfer/emergency")
def emergency_transfer(bid: str, payload: dict, request: Request,
                       user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    session_user = user
    user = _user(user)
    _recent_auth(request, session_user)
    if not policy.is_super_admin(user):
        raise exc.forbidden()
    book = _book(bid)
    target_id = (payload or {}).get("targetAccountId")
    try:
        row = transfer_service.emergency(
            book_id=int(db.get_book_row(bid)["id"]), actor_id=user["id"],
            target_account_id=target_id, reason=(payload or {}).get("reason", ""))
    except Exception as error:
        _handle_error(error)
    return transfer_service.serialize_request(row, include_events=True)
