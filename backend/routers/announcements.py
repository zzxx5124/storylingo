"""公告公開 delivery 與 Admin governance API。"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Response

from .. import db, exceptions as exc
from ..security import get_current_user
from ..services import admin_console, announcements, policy

router = APIRouter(prefix="/api", tags=["announcements"])
admin_router = APIRouter(prefix="/api/admin/announcements", tags=["admin-announcements"])


def _admin(user: dict) -> dict:
    return policy.require_capability(user, "manage_announcements")


@router.get("/announcements/active")
def active_announcements(response: Response, user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": announcements.list_active(user), "limit": announcements.MAX_ACTIVE_CANDIDATES}


@router.post("/announcements/{announcement_id}/acknowledge")
def acknowledge_announcement(announcement_id: int, payload: dict, response: Response,
                             user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    response.headers["Cache-Control"] = "private, no-store"
    account = policy.current_account(user)
    if not account:
        raise exc.unauthorized()
    try:
        display_version = int(payload.get("displayVersion", payload.get("display_version")))
    except (TypeError, ValueError) as error:
        raise exc.bad_request("displayVersion 不合法") from error
    try:
        return announcements.acknowledge(announcement_id, int(account["id"]), display_version)
    except LookupError as error:
        raise exc.not_found(str(error)) from error
    except ValueError as error:
        raise exc.bad_request(str(error), code="announcement_acknowledgement_invalid") from error


def _safe_id(announcement_id: int) -> int:
    try:
        return int(announcement_id)
    except (TypeError, ValueError) as error:
        raise exc.not_found("找不到公告") from error


@admin_router.get("")
def list_admin_announcements(user: Annotated[dict, Depends(get_current_user)], q: str = "", status: str = "",
                             audience: str = "", schedule: str = "", active: bool | None = None,
                             page: int = 1, page_size: int = 20,
                             sort: str = "updated_at", order: str = "desc"):
    _admin(user)
    return admin_console.list_announcements(q=q[:200], status=status, audience=audience,
                                            schedule=schedule, active=active,
                                            page=page, page_size=page_size, sort=sort, order=order)


@admin_router.post("")
def create_admin_announcement(payload: dict, user: Annotated[dict, Depends(get_current_user)]):
    account = _admin(user)
    try:
        item = announcements.create(payload, int(account["id"]))
    except ValueError as error:
        raise exc.bad_request(str(error), code="announcement_validation_error") from error
    db.add_audit_log(account["id"], "announcement_created", "announcement", item["id"], {"display_version": item["displayVersion"]})
    return item


@admin_router.get("/{announcement_id}")
def get_admin_announcement(announcement_id: int, user: Annotated[dict, Depends(get_current_user)]):
    _admin(user)
    item = announcements.get(_safe_id(announcement_id))
    if not item:
        raise exc.not_found("找不到公告")
    return item


@admin_router.put("/{announcement_id}")
def update_admin_announcement(announcement_id: int, payload: dict, user: Annotated[dict, Depends(get_current_user)]):
    account = _admin(user)
    try:
        item = announcements.update(_safe_id(announcement_id), payload, int(account["id"]))
    except announcements.AnnouncementVersionConflict as error:
        raise exc.conflict(str(error), code="announcement_config_conflict") from error
    except ValueError as error:
        raise exc.bad_request(str(error), code="announcement_validation_error") from error
    if not item:
        raise exc.not_found("找不到公告")
    db.add_audit_log(account["id"], "announcement_updated", "announcement", item["id"], {"config_version": item["configVersion"]})
    return item


def _lifecycle(announcement_id: int, action: str, user: dict):
    account = _admin(user)
    before = announcements.get(_safe_id(announcement_id))
    try:
        item = announcements.lifecycle(_safe_id(announcement_id), action, int(account["id"]))
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    if not item:
        raise exc.not_found("找不到公告")
    db.add_audit_log(account["id"], f"announcement_{action}", "announcement", item["id"], {
        "display_version_before": before.get("displayVersion") if before else None,
        "config_version": item["configVersion"], "display_version": item["displayVersion"],
    })
    return item


@admin_router.post("/{announcement_id}/enable")
def enable_admin_announcement(announcement_id: int, user: Annotated[dict, Depends(get_current_user)]):
    return _lifecycle(announcement_id, "enable", user)


@admin_router.post("/{announcement_id}/disable")
def disable_admin_announcement(announcement_id: int, user: Annotated[dict, Depends(get_current_user)]):
    return _lifecycle(announcement_id, "disable", user)


@admin_router.post("/{announcement_id}/reannounce")
def reannounce_admin_announcement(announcement_id: int, user: Annotated[dict, Depends(get_current_user)]):
    return _lifecycle(announcement_id, "reannounce", user)


@admin_router.post("/{announcement_id}/archive")
def archive_admin_announcement(announcement_id: int, user: Annotated[dict, Depends(get_current_user)]):
    return _lifecycle(announcement_id, "archive", user)
