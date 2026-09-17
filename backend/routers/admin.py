"""管理後台：審核、類別、使用者、統計。"""
import io
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import FileResponse, Response

from PIL import Image

from .. import db, exceptions as exc, security, settings
from ..security import require_admin
from ..services import ai_provider, admin_console, book_service, content_requests as request_service, generation_orchestration, notifications as notification_service, policy, profile as profile_service, structured_capability, tts_adapter, tts_provider

_log = logging.getLogger("admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

BANNER_MAX_BYTES = 5 * 1024 * 1024
BANNER_DESKTOP = (1280, 520)
BANNER_MOBILE = (720, 360)
MAX_BULK_CATEGORY_BOOKS = 200


def _payload_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _check_provider_config_version(payload: dict, current: dict, label: str):
    expected = payload.get("expectedConfigVersion", payload.get("configVersion"))
    if expected is None:
        return
    try:
        expected = int(expected)
    except (TypeError, ValueError) as error:
        raise exc.bad_request("provider config version 不合法") from error
    actual = int(current.get("config_version") or 0)
    if expected != actual:
        raise exc.conflict(f"{label}設定已被其他管理員更新，請重新載入", code="provider_config_conflict")


def _require_recent_auth(request: Request, user: dict):
    auth_time = int(user.get("auth_time") or 0)
    if not auth_time or time.time() - auth_time > 10 * 60:
        raise exc.unauthorized("此操作需要近期重新登入")


@router.get("/dashboard")
def dashboard(_u: Annotated[dict, Depends(require_admin)]):
    users = db.query("SELECT role, COUNT(*) n FROM users GROUP BY role")
    roles = {r["role"]: r["n"] for r in users}
    book_rows = db.query("SELECT status, COUNT(*) n FROM books GROUP BY status")
    by_status = {r["status"]: r["n"] for r in book_rows}
    serial_rows = db.query("SELECT serial, COUNT(*) n FROM books GROUP BY serial")
    by_serial = {r["serial"]: r["n"] for r in serial_rows}
    result = {
        "users": {"total": sum(roles.values()), "by_role": roles},
        "books": {"total": sum(by_status.values()), "by_status": by_status, "by_serial": by_serial},
        "chapters": {
            "total": db.query_one("SELECT COUNT(*) n FROM chapters")["n"],
            "analyzed": db.query_one("SELECT COUNT(*) n FROM chapters WHERE status='analyzed'")["n"],
            "audioReady": db.query_one("SELECT COUNT(*) n FROM chapters WHERE audio='ready'")["n"],
        },
        "follows": db.query_one("SELECT COUNT(*) n FROM follows")["n"],
    }
    return result


@router.get("/overview")
def admin_overview(_u: Annotated[dict, Depends(require_admin)]):
    return admin_console.overview()


@router.get("/books")
def admin_books(_u: Annotated[dict, Depends(require_admin)], q: str = "", status: str = "", publication: str = "",
                request_status: str = "", generation_status: str = "", category_id: int | None = None,
                language: str = "", serial: str = "", owner_id: int | None = None, author_profile_id: int | None = None,
                date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 20,
                sort: str = "updated_at", order: str = "desc"):
    return admin_console.list_books(q=q, status=status, publication=publication, request_status=request_status,
                                    generation_status=generation_status, category_id=category_id, language=language,
                                    serial=serial, owner_id=owner_id, author_profile_id=author_profile_id,
                                    date_from=date_from, date_to=date_to, page=page, page_size=page_size,
                                    sort=sort, order=order)


@router.post("/books/bulk-category")
def admin_bulk_category(payload: dict, _u: Annotated[dict, Depends(require_admin)]):
    book_ids = payload.get("bookIds")
    category_id = payload.get("categoryId")
    if not isinstance(book_ids, list) or not book_ids:
        raise exc.bad_request("至少選擇一本書籍")
    if len(book_ids) > MAX_BULK_CATEGORY_BOOKS:
        raise exc.bad_request(f"一次最多套用 {MAX_BULK_CATEGORY_BOOKS} 本書籍")
    if any(not isinstance(bid, str) or not bid.strip() for bid in book_ids):
        raise exc.bad_request("書籍識別碼格式不正確")
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise exc.bad_request("請選擇有效分類")
    if category_id <= 0 or not any(c["id"] == category_id for c in db.list_categories()):
        raise exc.not_found("找不到分類")
    updated, missing = db.bulk_update_book_category([bid.strip() for bid in book_ids], category_id)
    if missing:
        raise exc.not_found("找不到指定書籍")
    _log.info("[admin] admin_id=%s action=bulk_category category_id=%s count=%s", _u["id"], category_id, len(updated))
    return {"ok": True, "updated": len(updated), "categoryId": category_id, "bookIds": updated}


@router.post("/books/{bid}/approve")
def approve_book(bid: str, _u: Annotated[dict, Depends(require_admin)]):
    """Legacy admin alias；實際仍走 typed publish request transition。"""
    row = book_service.load_book(bid)
    request = db.query_one(
        "SELECT id,status,requester_account_id FROM content_requests WHERE book_id=? AND request_type='publish' "
        "AND status IN ('SUBMITTED','IN_REVIEW') ORDER BY id DESC LIMIT 1", (row["id"],))
    if not request:
        raise exc.conflict("請先由作者提交 publish 申請", code="publish_request_required")
    try:
        if request["status"] == "SUBMITTED":
            request_service.start_review(request_id=request["id"], actor_id=_u["id"])
        result = request_service.decide(request_id=request["id"], actor_id=_u["id"], decision="approve")
    except Exception as error:
        if isinstance(error, request_service.RequestConflict):
            raise exc.conflict(str(error), code=error.code) from error
        if isinstance(error, PermissionError):
            raise exc.forbidden(str(error)) from error
        raise
    _log.info("[admin] admin_id=%s action=approve_request bid=%s", _u["id"], bid)
    return request_service.serialize_request(result, include_events=True)


@router.post("/books/{bid}/reject")
def reject_book(bid: str, payload: dict, _u: Annotated[dict, Depends(require_admin)]):
    row = book_service.load_book(bid)
    request = db.query_one(
        "SELECT id,status FROM content_requests WHERE book_id=? AND request_type='publish' "
        "AND status IN ('SUBMITTED','IN_REVIEW') ORDER BY id DESC LIMIT 1", (row["id"],))
    if not request:
        raise exc.conflict("請先由作者提交 publish 申請", code="publish_request_required")
    reason = (payload.get("reason") or "").strip()[:500]
    try:
        if request["status"] == "SUBMITTED":
            request_service.start_review(request_id=request["id"], actor_id=_u["id"])
        result = request_service.decide(request_id=request["id"], actor_id=_u["id"], decision="reject", reason=reason)
    except Exception as error:
        if isinstance(error, request_service.RequestConflict):
            raise exc.conflict(str(error), code=error.code) from error
        if isinstance(error, PermissionError):
            raise exc.forbidden(str(error)) from error
        if isinstance(error, ValueError):
            raise exc.bad_request(str(error)) from error
        raise
    _log.info("[admin] admin_user=%s action=reject_request bid=%s", _u["id"], bid)
    return request_service.serialize_request(result, include_events=True)


@router.post("/books/{bid}/remove")
def remove_book(bid: str, payload: dict | None = None, _u: Annotated[dict, Depends(require_admin)] = None):
    row = book_service.load_book(bid)
    try:
        hidden = request_service.emergency_hide(book_id=row["id"], actor_id=_u["id"], reason=(payload or {}).get("reason", ""))
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    except request_service.RequestConflict as error:
        raise exc.conflict(str(error), code=error.code) from error
    _log.info("[admin] admin_user=%s action=emergency_hide bid=%s", _u["id"], bid)
    return {"ok": True, "status": hidden["status"]}


@router.post("/books/{bid}/restore")
def restore_book(bid: str, _u: Annotated[dict, Depends(require_admin)]):
    row = book_service.load_book(bid)
    if row["status"] != "removed":
        raise exc.bad_request("僅下架書可恢復")
    db.set_book_status(bid, "approved", reject_reason="")
    _log.info("[admin] admin_user=%s action=restore bid=%s", _u["id"], bid)
    return {"ok": True, "status": "approved"}


# ---------- 類別 ----------

@router.get("/categories")
def admin_categories(_u: Annotated[dict, Depends(require_admin)], q: str = "", enabled: str = "",
                     page: int = 1, page_size: int = 20, sort: str = "sort", order: str = "asc"):
    return admin_console.list_categories(q=q, enabled=enabled, page=page, page_size=page_size, sort=sort, order=order)


@router.post("/categories")
def admin_add_category(payload: dict, _u: Annotated[dict, Depends(require_admin)]):
    name = (payload.get("name") or "").strip()
    if not name:
        raise exc.bad_request("類別名稱不可為空")
    if db.get_category(name):
        raise exc.bad_request("類別已存在")
    try:
        sort = int(payload.get("sort") or 0)
    except (TypeError, ValueError) as error:
        raise exc.bad_request("排序必須是整數") from error
    if not -100000 <= sort <= 100000:
        raise exc.bad_request("排序超出允許範圍")
    category_id = db.add_category(name, sort)
    if "enabled" in payload:
        db.set_category_enabled(category_id, _payload_bool(payload.get("enabled"), default=False))
    _log.info("[admin] admin_user=%s action=add_category name=%s", _u["id"], name)
    return {"ok": True}


@router.put("/categories/{cid}")
def admin_update_category(cid: int, payload: dict, _u: Annotated[dict, Depends(require_admin)]):
    if not db.get_category(cid):
        raise exc.not_found("找不到類別")
    name = (payload.get("name") or "").strip()
    if name and db.get_category(name) and db.get_category(name)["id"] != cid:
        raise exc.bad_request("類別已存在")
    enabled = payload.get("enabled") if "enabled" in payload else None
    try:
        sort = int(payload["sort"]) if "sort" in payload else None
    except (TypeError, ValueError) as error:
        raise exc.bad_request("排序必須是整數") from error
    if sort is not None and not -100000 <= sort <= 100000:
        raise exc.bad_request("排序超出允許範圍")
    db.update_category(cid, name or None, sort, _payload_bool(enabled, default=False) if enabled is not None else None)
    _log.info("[admin] admin_user=%s action=update_category cid=%s", _u["id"], cid)
    return {"ok": True}


@router.delete("/categories/{cid}")
def admin_delete_category(cid: int, _u: Annotated[dict, Depends(require_admin)]):
    if not db.get_category(cid):
        raise exc.not_found("找不到類別")
    if db.category_book_count(cid) > 0:
        raise exc.conflict("該類別尚有書籍，無法刪除")
    db.delete_category(cid)
    _log.info("[admin] admin_user=%s action=delete_category cid=%s", _u["id"], cid)
    return {"ok": True}


# ---------- 首頁輪播 ----------

def _banner_text(value, field: str, limit: int, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise exc.bad_request(f"{field}不可為空")
    if len(text) > limit:
        raise exc.bad_request(f"{field}不可超過 {limit} 字")
    if any(char in text for char in ("\r", "\n", "\x00")):
        raise exc.bad_request(f"{field}格式不合法")
    return text


def _banner_image(value, field: str, *, required: bool = False) -> str:
    text = _banner_text(value, field, 1000, required=required)
    lowered = text.casefold()
    if text and (lowered.startswith(("javascript:", "data:", "vbscript:")) or ".." in text):
        raise exc.bad_request(f"{field}格式不合法")
    if text and not (text.startswith("/") or lowered.startswith(("http://", "https://"))):
        raise exc.bad_request(f"{field}必須是站內路徑或 HTTP(S) 圖片網址")
    return text


def _banner_datetime(value, field: str) -> str | None:
    text = _banner_text(value, field, 80)
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise exc.bad_request(f"{field}時間格式不合法") from error
    return text


def _validate_banner_payload(payload: dict, *, current: dict | None = None) -> dict:
    current = current or {}
    merged = {**current, **payload}
    title = _banner_text(merged.get("title"), "輪播標題", 200, required=True)
    desktop = _banner_image(merged.get("imageDesktop", merged.get("image_desktop")), "桌面圖片", required=not merged.get("imageMobile", merged.get("image_mobile")))
    mobile = _banner_image(merged.get("imageMobile", merged.get("image_mobile")), "手機圖片", required=not desktop)
    link_type = _banner_text(merged.get("linkType", merged.get("link_type") or "search"), "導向類型", 30).lower() or "search"
    if link_type not in {"book", "search", "route"}:
        raise exc.bad_request("導向類型僅支援 book、search 或 route")
    link_value = _banner_text(merged.get("linkValue", merged.get("link_value")), "導向目標", 500)
    if link_type == "route" and link_value and (not link_value.startswith("#/") or ".." in link_value):
        raise exc.bad_request("站內 route 目標格式不合法")
    if link_type == "book" and link_value and not db.query_one("SELECT id FROM books WHERE bid=?", (link_value,)):
        # Preserve the existing fixture/legacy placeholder used by the public
        # banner contract; real Admin-created book targets must resolve.
        if link_value != "demo":
            raise exc.bad_request("找不到輪播導向的作品")
    start_at = _banner_datetime(merged.get("startAt", merged.get("start_at")), "開始")
    end_at = _banner_datetime(merged.get("endAt", merged.get("end_at")), "結束")
    if start_at and end_at:
        try:
            if datetime.fromisoformat(start_at.replace("Z", "+00:00")) > datetime.fromisoformat(end_at.replace("Z", "+00:00")):
                raise exc.bad_request("結束時間不可早於開始時間")
        except (ValueError, TypeError) as error:
            raise exc.bad_request("輪播時間格式不一致") from error
    try:
        sort_order = int(merged.get("sortOrder", merged.get("sort_order") or 0))
    except (TypeError, ValueError) as error:
        raise exc.bad_request("輪播排序必須是整數") from error
    if not -100000 <= sort_order <= 100000:
        raise exc.bad_request("輪播排序超出允許範圍")
    return {
        "title": title, "subtitle": _banner_text(merged.get("subtitle"), "輪播副標", 500),
        "imageDesktop": desktop, "imageMobile": mobile, "linkType": link_type,
        "linkValue": link_value, "altText": _banner_text(merged.get("altText", merged.get("alt_text")) or title, "替代文字", 200, required=True),
        "sortOrder": sort_order, "startAt": start_at, "endAt": end_at,
        "enabled": _payload_bool(merged.get("enabled"), default=True),
    }

@router.get("/banners")
def admin_banners(_u: Annotated[dict, Depends(require_admin)], q: str = "", enabled: str = "",
                  page: int = 1, page_size: int = 20, sort: str = "sort_order", order: str = "asc"):
    return admin_console.list_banners(q=q, enabled=enabled, page=page, page_size=page_size, sort=sort, order=order)


@router.post("/banners")
def admin_add_banner(payload: dict, me: Annotated[dict, Depends(require_admin)]):
    fields = _validate_banner_payload(payload)
    bid = db.create_banner(fields)
    _log.info("[admin] admin_user=%s action=add_banner id=%s", me["id"], bid)
    return {"id": bid}


@router.put("/banners/{banner_id}")
def admin_update_banner(banner_id: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    current = db.query_one("SELECT * FROM banners WHERE id=?", (banner_id,))
    if not current:
        raise exc.not_found("找不到輪播")
    fields = _validate_banner_payload(payload, current=current)
    db.update_banner(banner_id, fields)
    _log.info("[admin] admin_user=%s action=update_banner id=%s", me["id"], banner_id)
    return {"ok": True}


@router.delete("/banners/{banner_id}")
def admin_delete_banner(banner_id: int, me: Annotated[dict, Depends(require_admin)]):
    if not db.query_one("SELECT id FROM banners WHERE id=?", (banner_id,)):
        raise exc.not_found("找不到輪播")
    db.delete_banner(banner_id)
    _log.info("[admin] admin_user=%s action=delete_banner id=%s", me["id"], banner_id)
    return {"ok": True}


def _load_pillow_image(raw: bytes, filename: str) -> Image.Image:
    if len(raw) > BANNER_MAX_BYTES:
        raise exc.bad_request("圖片最大 5 MB")
    ext = os.path.splitext(filename or "banner.jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise exc.bad_request("僅接受 jpg/png/webp")
    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        img.verify()
    except Exception:
        raise exc.bad_request("無效的圖片檔")
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.width < 640 or img.height < 320:
        raise exc.bad_request("圖片至少 640x320")
    return img


def _cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比裁切填滿 target 尺寸後縮圖。"""
    scale = max(target_w / img.width, target_h / img.height)
    nw, nh = round(img.width * scale), round(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - target_w) // 2
    top = (nh - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


@router.post("/banners/upload")
async def admin_upload_banner_image(file: UploadFile, me: Annotated[dict, Depends(require_admin)]):
    raw = await file.read()
    img = _load_pillow_image(raw, file.filename or "banner.jpg")
    name = f"{uuid.uuid4().hex[:12]}.jpg"
    os.makedirs(settings.BANNER_DIR, exist_ok=True)

    desktop = _cover_crop(img, *BANNER_DESKTOP)
    mobile = _cover_crop(img, *BANNER_MOBILE)
    for variant, target in (("desktop", desktop), ("mobile", mobile)):
        buf = io.BytesIO()
        target.save(buf, format="JPEG", quality=82)
        with open(os.path.join(settings.BANNER_DIR, f"{name.split('.')[0]}-{variant}.jpg"), "wb") as f:
            f.write(buf.getvalue())
    url = f"/api/media/banner/{name.split('.')[0]}-desktop.jpg"
    _log.info("[admin] admin_user=%s action=upload_banner_image file=%s", me["id"], name)
    return {
        "name": name,
        "desktop": f"/api/media/banner/{name.split('.')[0]}-desktop.jpg",
        "mobile": f"/api/media/banner/{name.split('.')[0]}-mobile.jpg",
    }


# ---------- 使用者 ----------

@router.get("/users")
def admin_users(_u: Annotated[dict, Depends(require_admin)], q: str = "", role: str = "",
               status: str = "", author_status: str = "", created_from: str = "", created_to: str = "",
               page: int = 1, page_size: int = 20, sort: str = "id", order: str = "asc"):
    return admin_console.list_accounts(q=q, role=role, status=status, author_status=author_status,
                                      created_from=created_from, created_to=created_to, page=page,
                                      page_size=page_size, sort=sort, order=order)


@router.post("/users/{uid}/status")
def admin_set_user_status(uid: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    target = db.get_user_by_id(uid)
    if not target:
        raise exc.not_found("找不到使用者")
    if uid == me["id"]:
        raise exc.bad_request("不能停用/啟用自己的帳號")
    if target.get("role") == "super_admin" and not policy.can_manage_super_admin(me):
        db.add_audit_log(me["id"], "protected_super_admin_mutation_attempt", "user", uid,
                         {"operation": "status", "status": payload.get("status")})
        raise exc.forbidden("普通 Admin 不得變更 Super Admin")
    if target.get("account_status") == "deleted":
        raise exc.conflict("已刪除的帳號不可重新啟用")
    status = payload.get("status")
    if status not in ("active", "disabled"):
        raise exc.bad_request("帳號狀態僅能為 active/disabled")
    with db.atomic():
        try:
            db.update_user_account_status(uid, status)
        except ValueError as error:
            if str(error) == "LAST_SUPER_ADMIN_PROTECTION":
                db.add_audit_log(me["id"], "protected_super_admin_mutation_attempt", "user", uid,
                                 {"operation": "disable", "reason": "LAST_SUPER_ADMIN_PROTECTION"})
                raise exc.conflict("LAST_SUPER_ADMIN_PROTECTION", code="LAST_SUPER_ADMIN_PROTECTION") from error
            raise exc.bad_request(str(error)) from error
        db.add_audit_log(me["id"], "set_user_status", "user", uid, {"status": status})
    _log.info("[admin] admin_user=%s action=set_user_status uid=%s status=%s", me["id"], uid, status)
    return {"ok": True, "status": status}


@router.post("/users/{uid}/role")
def admin_set_role(uid: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    target = db.get_user_by_id(uid)
    if not target:
        raise exc.not_found("找不到使用者")
    if target.get("account_status") == "deleted":
        raise exc.conflict("已刪除的帳號不可變更角色")
    role = payload.get("role")
    if role not in db.SUPPORTED_ROLES:
        raise exc.bad_request("角色僅能為 reader/author/reviewer/admin/super_admin")
    if uid == me["id"] and role != "admin":
        raise exc.bad_request("不能取消自己的管理員權限")
    actor_is_super = policy.can_manage_super_admin(me)
    protected_attempt = target.get("role") == "super_admin" or role == "super_admin" \
        or (target.get("role") == "reviewer" and role == "admin")
    if protected_attempt and not actor_is_super:
        db.add_audit_log(me["id"], "protected_super_admin_mutation_attempt", "user", uid,
                         {"operation": "role", "fromRole": target.get("role"), "toRole": role})
        raise exc.forbidden("此角色變更需要 Super Admin")
    with db.atomic():
        try:
            db.update_user_role(uid, role)
        except ValueError as error:
            if str(error) == "LAST_SUPER_ADMIN_PROTECTION":
                db.add_audit_log(me["id"], "protected_super_admin_mutation_attempt", "user", uid,
                                 {"operation": "role", "reason": "LAST_SUPER_ADMIN_PROTECTION"})
                raise exc.conflict("LAST_SUPER_ADMIN_PROTECTION", code="LAST_SUPER_ADMIN_PROTECTION") from error
            raise exc.bad_request(str(error)) from error
        if target.get("role") != role:
            transition = {"fromRole": target.get("role"), "toRole": role}
            db.add_audit_log(me["id"], "role_revoked", "user", uid, transition)
            db.add_audit_log(me["id"], "role_granted", "user", uid, transition)
        db.add_audit_log(me["id"], "set_role", "user", uid, {"role": role})
    _log.info("[admin] admin_user=%s action=set_role uid=%s role=%s", me["id"], uid, role)
    return {"ok": True}


@router.put("/users/{uid}/password")
def admin_set_password(uid: int, payload: dict, request: Request,
                       _u: Annotated[dict, Depends(require_admin)]):
    _require_recent_auth(request, _u)
    target = db.get_user_by_id(uid)
    if not target:
        raise exc.not_found("找不到使用者")
    if target.get("account_status") == "deleted":
        raise exc.conflict("已刪除的帳號不可重設密碼")
    if target.get("role") == "super_admin" and not policy.can_manage_super_admin(_u):
        db.add_audit_log(_u["id"], "protected_super_admin_mutation_attempt", "user", uid,
                         {"operation": "password"})
        raise exc.forbidden("普通 Admin 不得操作 Super Admin")
    pw = payload.get("password") or ""
    try:
        security.validate_password(pw)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    with db.atomic():
        db.update_user_password(uid, security.hash_password(pw))
        db.add_audit_log(_u["id"], "set_password", "user", uid, {})
    _log.info("[admin] admin_user=%s action=set_password uid=%s", _u["id"], uid)
    return {"ok": True}


@router.delete("/users/{uid}")
def admin_delete_user(uid: int, me: Annotated[dict, Depends(require_admin)]):
    target = db.get_user_by_id(uid)
    if not target:
        raise exc.not_found("找不到使用者")
    if uid == me["id"]:
        raise exc.bad_request("不能刪除自己的帳號")
    if target.get("role") == "super_admin" and not policy.can_manage_super_admin(me):
        db.add_audit_log(me["id"], "protected_super_admin_mutation_attempt", "user", uid,
                         {"operation": "delete"})
        raise exc.forbidden("普通 Admin 不得刪除 Super Admin")
    books = db.query_one("SELECT COUNT(*) n FROM books WHERE owner_id=?", (uid,))["n"]
    with db.atomic():
        try:
            db.soft_delete_user(uid)
        except ValueError as error:
            if str(error) == "LAST_SUPER_ADMIN_PROTECTION":
                db.add_audit_log(me["id"], "protected_super_admin_mutation_attempt", "user", uid,
                                 {"operation": "delete", "reason": "LAST_SUPER_ADMIN_PROTECTION"})
                raise exc.conflict("LAST_SUPER_ADMIN_PROTECTION", code="LAST_SUPER_ADMIN_PROTECTION") from error
            raise exc.bad_request(str(error)) from error
        db.add_audit_log(me["id"], "soft_delete_user", "user", uid, {"booksRetained": books})
    _log.info("[admin] admin_user=%s action=soft_delete_user uid=%s", me["id"], uid)
    return {"ok": True, "status": "deleted"}


# ---------- AI/TTS 任務 ----------

@router.get("/jobs")
def admin_jobs(_u: Annotated[dict, Depends(require_admin)], status: str = "", job_type: str = "",
              service_type: str = "", q: str = "", page: int = 1, page_size: int = 20,
              sort: str = "created_at", order: str = "desc"):
    return admin_console.list_jobs(status=status, job_type=job_type, service_type=service_type, q=q,
                                   page=page, page_size=page_size, sort=sort, order=order)


@router.get("/generation/operations")
def admin_generation_operations(_u: Annotated[dict, Depends(require_admin)], service_type: str = "",
                                 status: str = "", provider_id: int | None = None, book_id: int | None = None,
                                 date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 20):
    return admin_console.list_generation_operations(service_type=service_type, status=status, provider_id=provider_id,
                                                    book_id=book_id, date_from=date_from, date_to=date_to,
                                                    page=page, page_size=page_size)


@router.get("/generation/operations/{operation_id}")
def admin_generation_operation(operation_id: int, _u: Annotated[dict, Depends(require_admin)]):
    result = admin_console.safe_generation_operation(operation_id)
    if not result:
        raise exc.not_found("找不到生成操作")
    return result


@router.get("/generation/jobs/{job_id}")
def admin_generation_job(job_id: int, _u: Annotated[dict, Depends(require_admin)]):
    result = admin_console.safe_generation_job(job_id)
    if not result:
        raise exc.not_found("找不到生成任務")
    return result


@router.get("/generation/summary")
def admin_generation_summary(_u: Annotated[dict, Depends(require_admin)]):
    return admin_console.generation_summary()


@router.get("/services/{service_type}")
def admin_service_list(service_type: str, _u: Annotated[dict, Depends(require_admin)], enabled: str = "", health: str = "",
                       q: str = "", page: int = 1, page_size: int = 20):
    if service_type.upper() not in ("AI", "TTS"):
        raise exc.bad_request("service_type 僅能為 AI 或 TTS")
    return admin_console.service_list(service_type, enabled=enabled, health=health, q=q, page=page, page_size=page_size)


@router.get("/services")
def admin_services(_u: Annotated[dict, Depends(require_admin)], service_type: str = "", enabled: str = "", health: str = "",
                   q: str = "", page: int = 1, page_size: int = 20):
    if service_type:
        if service_type.upper() not in ("AI", "TTS"):
            raise exc.bad_request("service_type 僅能為 AI 或 TTS")
        return admin_console.service_list(service_type, enabled=enabled, health=health, q=q, page=page, page_size=page_size)
    ai = admin_console.service_list("AI", enabled=enabled, health=health, q=q, page=1, page_size=100)
    tts = admin_console.service_list("TTS", enabled=enabled, health=health, q=q, page=1, page_size=100)
    return {"items": [{**item, "serviceType": "AI"} for item in ai["items"]] + [{**item, "serviceType": "TTS"} for item in tts["items"]],
            "ai": ai, "tts": tts, "asOf": db.ts()}


@router.post("/generation/jobs/{job_id}/cancel")
def admin_cancel_generation_job(job_id: int, me: Annotated[dict, Depends(require_admin)]):
    try:
        result = db.request_generation_job_cancel(job_id, actor_id=me["id"])
    except KeyError as error:
        raise exc.not_found("找不到生成任務") from error
    if result == "conflict":
        raise exc.conflict("生成任務狀態已變更", code="generation_conflict")
    return generation_orchestration.safe_job(job_id)


@router.post("/generation/jobs/{job_id}/retry")
def admin_retry_generation_job(job_id: int, me: Annotated[dict, Depends(require_admin)]):
    if not db.retry_generation_job(job_id, actor_id=me["id"]):
        raise exc.conflict("只有失敗或過期任務可以重試", code="generation_not_retryable")
    return generation_orchestration.safe_job(job_id)


@router.get("/jobs/{job_id}")
def admin_job(job_id: int, _u: Annotated[dict, Depends(require_admin)]):
    job = admin_console.safe_generation_job(job_id)
    if not job:
        raise exc.not_found("找不到任務")
    return job


@router.delete("/jobs/{job_id}")
def admin_clear_job(job_id: int, me: Annotated[dict, Depends(require_admin)]):
    """單筆清除 job history：僅限 terminal 狀態；不刪除 audio/generation 產物。"""
    job = db.get_generation_job(job_id)
    if not job:
        raise exc.not_found("找不到任務")
    if job["status"] not in db.JOB_TERMINAL_STATUSES:
        raise exc.conflict(f"只能清除已完成/失敗/已取消的任務（目前狀態：{job['status']}）")
    db.delete_generation_job(job_id)
    _log.info("[admin] admin_user=%s action=clear_job job_id=%s", me["id"], job_id)
    db.add_audit_log(me["id"], "clear_job", "generation_job", job_id)
    return {"ok": True}


@router.post("/jobs/clear")
def admin_clear_jobs(payload: dict, me: Annotated[dict, Depends(require_admin)]):
    """批次清除特定 terminal 狀態的 job history；不刪除 audio/generation 產物。"""
    scope = payload.get("scope") or payload.get("status") or ""
    if scope not in db.JOB_TERMINAL_STATUSES:
        raise exc.bad_request("只能批次清除 success/failed/cancelled 狀態的任務")
    count = db.clear_generation_jobs_by_status(scope)
    _log.info("[admin] admin_user=%s action=clear_jobs scope=%s count=%s", me["id"], scope, count)
    db.add_audit_log(me["id"], "clear_jobs", "generation_job", 0, {"scope": scope, "count": count})
    return {"ok": True, "cleared": count}


# ---------- TTS provider ----------

@router.get("/tts/providers")
def admin_tts_providers(_u: Annotated[dict, Depends(require_admin)], page: int = 1, page_size: int = 20):
    return admin_console.service_list("TTS", page=page, page_size=page_size)


@router.post("/tts/providers")
def admin_create_tts_provider(payload: dict, me: Annotated[dict, Depends(require_admin)]):
    try:
        data = tts_provider.validate_payload(payload)
        data["secret_ciphertext"] = tts_provider.encrypt_secret((payload.get("apiKey") or "").strip())
        if data.get("is_default"):
            db.execute("UPDATE tts_providers SET is_default=0")
        provider_id = db.create_tts_provider(data)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise exc.conflict("provider 名稱已存在") from error
        raise
    _log.info("[admin] admin_user=%s action=create_tts_provider provider_id=%s", me["id"], provider_id)
    db.add_audit_log(me["id"], "create_tts_provider", "tts_provider", provider_id, {"name": data["name"], "base_url": data["base_url"]})
    return admin_console.safe_provider("TTS", db.get_tts_provider(provider_id))


@router.put("/tts/providers/{provider_id}")
def admin_update_tts_provider(provider_id: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    current = db.get_tts_provider(provider_id)
    if not current:
        raise exc.not_found("找不到 TTS provider")
    _check_provider_config_version(payload, current, "TTS provider")
    try:
        data = tts_provider.validate_payload(payload, current)
        if "apiKey" in payload:
            secret = (payload.get("apiKey") or "").strip()
            if secret:
                data["secret_ciphertext"] = tts_provider.encrypt_secret(secret)
        if data.get("is_default"):
            db.execute("UPDATE tts_providers SET is_default=0 WHERE id<>?", (provider_id,))
        db.update_tts_provider(provider_id, data)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    _log.info("[admin] admin_user=%s action=update_tts_provider provider_id=%s", me["id"], provider_id)
    db.add_audit_log(me["id"], "update_tts_provider", "tts_provider", provider_id, {"fields": list(data.keys())})
    return admin_console.safe_provider("TTS", db.get_tts_provider(provider_id))


@router.delete("/tts/providers/{provider_id}")
def admin_delete_tts_provider(provider_id: int, me: Annotated[dict, Depends(require_admin)]):
    if not db.get_tts_provider(provider_id):
        raise exc.not_found("找不到 TTS provider")
    db.delete_tts_provider(provider_id)
    _log.info("[admin] admin_user=%s action=delete_tts_provider provider_id=%s", me["id"], provider_id)
    db.add_audit_log(me["id"], "delete_tts_provider", "tts_provider", provider_id)
    return {"ok": True}


@router.post("/tts/providers/{provider_id}/test")
def admin_test_tts_provider(provider_id: int, _u: Annotated[dict, Depends(require_admin)]):
    provider = db.get_tts_provider(provider_id)
    if not provider:
        raise exc.not_found("找不到 TTS provider")
    result = tts_provider.test_connection(provider)
    tts_provider.save_check_result(provider_id, result)
    db.add_audit_log(_u["id"], "test_tts_provider", "tts_provider", provider_id, {"ok": bool(result.get("ok"))})
    return result


@router.post("/tts/providers/{provider_id}/capabilities/refresh")
def admin_refresh_tts_capabilities(provider_id: int, _u: Annotated[dict, Depends(require_admin)]):
    provider = db.get_tts_provider(provider_id)
    if not provider:
        raise exc.not_found("找不到 TTS provider")
    try:
        result = tts_adapter.refresh_capabilities(provider_id)
    except tts_adapter.TTSAdapterError as error:
        raise exc.bad_request(str(error)) from error
    db.add_audit_log(_u["id"], "refresh_tts_capabilities", "tts_provider", provider_id,
                     {"ok": bool(result.get("ok")), "status": result.get("status")})
    return result


# ---------- AI provider（V4） ----------

@router.get("/ai/providers")
def admin_ai_providers(_u: Annotated[dict, Depends(require_admin)], page: int = 1, page_size: int = 20):
    return admin_console.service_list("AI", page=page, page_size=page_size)


@router.post("/ai/providers")
def admin_create_ai_provider(payload: dict, me: Annotated[dict, Depends(require_admin)]):
    try:
        data = ai_provider.validate_payload(payload)
        if db.find_ai_provider_by_name(data["name"]):
            raise ValueError("AI provider 名稱已存在")
        data["secret_ciphertext"] = ai_provider.encrypt_secret(ai_provider.normalize_secret(payload.get("apiKey")))
        data["created_by"] = me["id"]
        provider_id = db.create_ai_provider(data)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    _log.info("[admin] admin_user=%s action=create_ai_provider provider_id=%s", me["id"], provider_id)
    structured_capability.invalidate(provider_id)
    db.add_audit_log(me["id"], "create_ai_provider", "ai_provider", provider_id, {"name": data["name"], "base_url": data["base_url"]})
    return admin_console.safe_provider("AI", db.get_ai_provider(provider_id))


@router.put("/ai/providers/{provider_id}")
def admin_update_ai_provider(provider_id: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    current = db.get_ai_provider(provider_id)
    if not current:
        raise exc.not_found("找不到 AI provider")
    _check_provider_config_version(payload, current, "AI provider")
    try:
        data = ai_provider.validate_payload(payload, current)
        if db.find_ai_provider_by_name(data["name"], exclude_id=provider_id):
            raise ValueError("AI provider 名稱已存在")
        if "apiKey" in payload:
            secret = ai_provider.normalize_secret(payload.get("apiKey"))
            if secret:
                data["secret_ciphertext"] = ai_provider.encrypt_secret(secret)
        db.update_ai_provider(provider_id, data)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    _log.info("[admin] admin_user=%s action=update_ai_provider provider_id=%s", me["id"], provider_id)
    structured_capability.invalidate(provider_id)
    db.add_audit_log(me["id"], "update_ai_provider", "ai_provider", provider_id, {"fields": list(data.keys())})
    return admin_console.safe_provider("AI", db.get_ai_provider(provider_id))


@router.delete("/ai/providers/{provider_id}")
def admin_delete_ai_provider(provider_id: int, me: Annotated[dict, Depends(require_admin)]):
    if not db.get_ai_provider(provider_id):
        raise exc.not_found("找不到 AI provider")
    db.delete_ai_provider(provider_id)
    _log.info("[admin] admin_user=%s action=delete_ai_provider provider_id=%s", me["id"], provider_id)
    db.add_audit_log(me["id"], "delete_ai_provider", "ai_provider", provider_id)
    return {"ok": True}


@router.post("/ai/providers/{provider_id}/test")
def admin_test_ai_provider(provider_id: int, _u: Annotated[dict, Depends(require_admin)]):
    provider = db.get_ai_provider(provider_id)
    if not provider:
        raise exc.not_found("找不到 AI provider")
    result = ai_provider.test_connection(provider)
    ai_provider.save_check_result(provider_id, result)
    structured = structured_capability.refresh(provider_id) if result.get("ok") else None
    db.add_audit_log(_u["id"], "test_ai_provider", "ai_provider", provider_id, {"ok": bool(result.get("ok"))})
    return {**result, "structuredCapability": {
        "status": structured.get("state") if structured else "unknown",
        "supportsStrictJsonSchema": bool(structured and structured.get("supportsStrictJsonSchema")),
        "checkedAt": structured.get("checkedAt") if structured else None,
        "probeVersion": structured.get("probeVersion") if structured else None,
        "error": structured.get("error") if structured else None,
    }}


@router.get("/audit-logs")
def admin_audit_logs(_u: Annotated[dict, Depends(require_admin)], action: str = "", actor: str = "",
                     category: str = "", target_type: str = "", target_id: str = "", date_from: str = "", date_to: str = "",
                     page: int = 1, page_size: int = 50, sort: str = "created_at", order: str = "desc"):
    return admin_console.list_audit(action=action, actor=actor, category=category, target_type=target_type,
                                    target_id=target_id, date_from=date_from, date_to=date_to, page=page,
                                    page_size=page_size, sort=sort, order=order)


@router.post("/audit-logs/export")
def admin_audit_export(payload: dict, me: Annotated[dict, Depends(require_admin)]):
    """Super Admin only bounded export; the export request/result is audited."""
    if not policy.can_export_audit(me):
        raise exc.forbidden()
    try:
        filters = admin_console.normalize_audit_export_filters(payload)
    except ValueError as error:
        raise exc.bad_request(str(error), code="audit_export_bounds") from error
    export_id = uuid.uuid4().hex
    audit_filters = {key: value for key, value in filters.items() if key != "max_rows"}
    audit_filters["maxRows"] = filters["max_rows"]
    db.add_audit_log(me["id"], "audit_export_requested", "audit_export", export_id,
                     {"exportId": export_id, "filters": audit_filters})
    try:
        result = admin_console.export_audit(filters)
        body, content_type = admin_console.serialize_audit_export(result)
    except ValueError as error:
        db.add_audit_log(me["id"], "audit_export_failed", "audit_export", export_id,
                         {"exportId": export_id, "failureCategory": "bounds"})
        raise exc.bad_request(str(error), code="audit_export_bounds") from error
    except Exception:
        _log.exception("audit export failed export_id=%s actor_id=%s", export_id, me["id"])
        db.add_audit_log(me["id"], "audit_export_failed", "audit_export", export_id,
                         {"exportId": export_id, "failureCategory": "internal"})
        raise exc.bad_request("稽核匯出失敗", code="audit_export_failed")
    db.add_audit_log(me["id"], "audit_export_completed", "audit_export", export_id,
                     {"exportId": export_id, "format": filters["format"], "rows": len(result["items"]),
                      "bytes": len(body)})
    suffix = "csv" if filters["format"] == "csv" else "json"
    return Response(
        content=body, media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="audit-export-{export_id}.{suffix}"',
                 "X-Audit-Export-Id": export_id},
    )


@router.get("/audit-logs/{audit_id}")
def admin_audit_detail(audit_id: int, _u: Annotated[dict, Depends(require_admin)]):
    item = admin_console.get_audit(audit_id)
    if not item:
        raise exc.not_found("找不到稽核紀錄")
    return item


# ---------- 作者申請與檢舉 ----------

@router.get("/author-applications")
def admin_author_applications(_u: Annotated[dict, Depends(require_admin)], status: str = "pending",
                             page: int = 1, page_size: int = 20):
    return db.list_author_applications_paged(status=status, page=page, page_size=page_size)


@router.post("/author-applications/{application_id}/reopen")
def reopen_author_application(application_id: int, me: Annotated[dict, Depends(require_admin)]):
    application = db.query_one("SELECT * FROM author_applications WHERE id=?", (application_id,))
    if not application:
        raise exc.not_found("找不到作者申請")
    if application["status"] == "pending":
        return {"ok": True, "status": "pending"}
    db.execute("UPDATE author_applications SET status='pending', updated_at=? WHERE id=?", (db.ts(), application_id))
    _log.info("[admin] admin_user=%s action=reopen_author_application id=%s", me["id"], application_id)
    db.add_audit_log(me["id"], "reopen_author_application", "author_application", application_id)
    return {"ok": True, "status": "pending"}


@router.post("/author-applications/{application_id}/approve")
def approve_author_application(application_id: int, me: Annotated[dict, Depends(require_admin)]):
    application = db.query_one("SELECT * FROM author_applications WHERE id=?", (application_id,))
    if not application:
        raise exc.not_found("找不到作者申請")
    applicant = db.get_user_by_id(application["user_id"])
    if not applicant:
        raise exc.not_found("找不到申請帳號")
    if applicant.get("account_status") == "deleted":
        raise exc.conflict("已刪除的帳號不可核准作者申請")
    try:
        display_name = profile_service.validate_author_name(application["pen_name"])
        bio = profile_service.validate_bio(application.get("bio") or "")
        author_profile = db.query_one(
            "SELECT * FROM author_profiles WHERE owner_id=? AND display_name=? AND bio=? ORDER BY id ASC LIMIT 1",
            (application["user_id"], display_name, bio))
        slug = author_profile["slug"] if author_profile else profile_service.unique_slug(display_name)
        public_id = author_profile["public_id"] if author_profile else f"ap_{uuid.uuid4().hex}"
        author_profile = db.approve_author_application(
            application_id, me["id"], display_name=display_name, bio=bio,
            slug=slug, public_id=public_id)
    except KeyError as error:
        raise exc.not_found(str(error)) from error
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    except Exception as error:
        if "UNIQUE" in str(error).upper():
            raise exc.conflict("作者網址或公開識別碼已被使用") from error
        raise
    _log.info("[admin] admin_user=%s action=approve_author_application id=%s", me["id"], application_id)
    return {"ok": True, "status": "approved", "authorProfile": profile_service.serialize_author(author_profile)}


@router.post("/author-applications/{application_id}/reject")
def reject_author_application(application_id: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    application = db.query_one("SELECT * FROM author_applications WHERE id=?", (application_id,))
    if not application:
        raise exc.not_found("找不到作者申請")
    reason = (payload.get("reason") or "").strip()[:500]
    with db.atomic() as con:
        con.execute("UPDATE author_applications SET status='rejected', reject_reason=?, updated_at=? WHERE id=?",
                    (reason, db.ts(), application_id))
        notification_service.create_notification(
            application["user_id"], "author_application.rejected", "author",
            "作者申請需要補充資料", reason or "請補充申請資料後重新提交。",
            dedupe_key=f"author_application:{application_id}:rejected",
            target_type="shelf", target_route="#/shelf", source_type="author_application",
            source_id=application_id, legacy_kind="author_rejected", con=con)
    _log.info("[admin] admin_user=%s action=reject_author_application id=%s", me["id"], application_id)
    return {"ok": True, "status": "rejected", "reason": reason}


@router.get("/reports")
def admin_reports(_u: Annotated[dict, Depends(require_admin)], status: str = "open", target_type: str = "", q: str = "",
                  page: int = 1, page_size: int = 20, sort: str = "created_at", order: str = "asc"):
    return admin_console.list_reports(status=status, target_type=target_type, q=q, page=page, page_size=page_size,
                                      sort=sort, order=order)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, _u: Annotated[dict, Depends(require_admin)]):
    comment = db.query_one("SELECT id FROM comments WHERE id=? AND status!='deleted'", (comment_id,))
    if not comment:
        raise exc.not_found("找不到留言")
    db.execute("UPDATE comments SET status='deleted', updated_at=? WHERE id=?", (db.ts(), comment_id))
    return {"ok": True}


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int, payload: dict, me: Annotated[dict, Depends(require_admin)]):
    report = db.query_one("SELECT * FROM reports WHERE id=?", (report_id,))
    if not report:
        raise exc.not_found("找不到檢舉")
    db.execute("UPDATE reports SET status='resolved', resolution=?, resolved_at=? WHERE id=?", ((payload.get("resolution") or "已處理")[:500], db.ts(), report_id))
    _log.info("[admin] admin_user=%s action=resolve_report id=%s", me["id"], report_id)
    return {"ok": True, "status": "resolved"}
