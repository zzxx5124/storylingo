"""認證 router：註冊／登入／登出／me。"""
import re
import os
import hashlib
import json
import secrets
import threading
import time
import logging
import sqlite3
from collections import defaultdict, deque
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, UploadFile
from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .. import auth, db, security, settings
from ..services import auth_tokens, oauth
from ..services import distributed
from ..services import profile as profile_service

router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,30}$")
POLICY_VERSION = "2026-08-11"

# --- L0-3 登入暴力防護：IP+帳號 5 次失敗 / 15 分鐘 → 鎖定 15 分鐘（記憶體，單 worker 語意） ---
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 15 * 60
_LOGIN_LOCKOUT = 15 * 60
_login_failures = defaultdict(deque)  # key -> deque[timestamp]
_login_lockouts = {}                  # key -> lockout_until
_login_guard = threading.Lock()
_log = logging.getLogger("auth")
_OAUTH_LINK_COOKIE = "novel_oauth_link_confirmation"
_RECENT_AUTH_SECONDS = 10 * 60


def _login_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{username or ''}"


def _login_blocked(key: str) -> bool:
    shared = distributed.login_blocked(key, _LOGIN_MAX_FAILS, _LOGIN_LOCKOUT)
    if shared is not None:
        return shared
    now = time.monotonic()
    with _login_guard:
        until = _login_lockouts.get(key)
        if until and now < until:
            return True
        if until:
            _login_lockouts.pop(key, None)
        hits = _login_failures[key]
        while hits and now - hits[0] > _LOGIN_WINDOW:
            hits.popleft()
        if len(hits) >= _LOGIN_MAX_FAILS:
            _login_lockouts[key] = now + _LOGIN_LOCKOUT
            _login_failures.pop(key, None)
            return True
    return False


def _login_failed(key: str):
    shared = distributed.login_failed(key, _LOGIN_MAX_FAILS, _LOGIN_WINDOW, _LOGIN_LOCKOUT)
    if shared is not None:
        return
    now = time.monotonic()
    with _login_guard:
        _login_failures[key].append(now)


def _login_ok(key: str):
    distributed.login_ok(key)
    if distributed.available():
        return
    with _login_guard:
        _login_failures.pop(key, None)
        _login_lockouts.pop(key, None)


def _user_json(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


def _require_recent_auth(request: Request, user: dict):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號不支援此安全操作")
    auth_time = int(user.get("auth_time") or 0)
    if not auth_time or time.time() - auth_time > _RECENT_AUTH_SECONDS:
        raise HTTPException(401, "此操作需要近期重新登入")


def _auth_error(code: str, status_code: int = 503):
    return JSONResponse({"detail": "目前無法使用此驗證方式，請稍後再試", "code": code}, status_code=status_code)


def _oauth_failure_redirect(return_path: str = "/", code: str = "OAUTH_FAILED"):
    # 只把粗略分類帶回瀏覽器；provider 詳情留在 server-side。
    return RedirectResponse(oauth.result_redirect(return_path, f"error_{code.lower()}"), status_code=303)


def _internal_google_username(issuer: str, subject: str) -> str:
    import hashlib
    stem = "google_" + hashlib.sha256(f"{issuer}\x00{subject}".encode("utf-8")).hexdigest()[:23]
    candidate = stem[:30]
    if not db.get_user_by_bname(candidate):
        return candidate
    for suffix in range(1, 10_000):
        candidate = f"{stem[:30-len(str(suffix))-1]}_{suffix}"
        if not db.get_user_by_bname(candidate):
            return candidate
    raise HTTPException(503, "目前無法建立帳號，請稍後再試")


def _identity_json(identity: dict) -> dict:
    return {"id": identity["id"], "provider": identity["provider"],
            "email": identity.get("email_snapshot") or "",
            "emailVerified": bool(identity.get("email_verified"))}


@router.post("/register", status_code=201)
def register(payload: dict, response: Response, request: Request):
    username_value = payload.get("username")
    username = username_value.strip() if isinstance(username_value, str) else ""
    password = payload.get("password") or ""
    email = db.normalize_email(payload.get("email") or "")
    policy_ok = bool(payload.get("termsAccepted")) and bool(payload.get("privacyAccepted")) and bool(payload.get("ageConfirmed"))
    role = "reader"
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "使用者名稱需為 2~30 位中英文/數字/底線")
    try:
        security.validate_password(password)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not email:
        raise HTTPException(400, "註冊需要電子郵件，完成驗證後才能登入")
    if len(email) > 200 or "@" not in email:
        raise HTTPException(400, "電子郵件格式不正確")
    if settings.APP_ENV == "production" and not policy_ok:
        raise HTTPException(400, "請先閱讀並同意服務條款、隱私權政策與年齡要求")
    if db.get_user_by_bname(username):
        raise HTTPException(409, "此使用者名稱已存在")
    if email and db.email_in_use(email):
        raise HTTPException(409, "此電子郵件已被使用")
    try:
        uid = db.create_user(username, security.hash_password(password), role, email)
        db.set_pending_email(uid, email)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if policy_ok:
        for policy_type in ("terms", "privacy", "age"):
            db.record_policy_consent(uid, policy_type, POLICY_VERSION)
    delivery = auth_tokens.issue_email_verification(uid, email, request)
    if not delivery.ok:
        _log.warning("email delivery unavailable code=%s purpose=email_verification", delivery.code)
        raise HTTPException(503, "驗證信暫時無法寄送，請稍後再試")
    u = db.get_user_by_id(uid)
    return {"id": u["id"], "username": u["username"], "role": u["role"],
            "verificationRequired": True, "email": email}


@router.post("/login")
def login(payload: dict, response: Response, request: Request):
    username_value = payload.get("username")
    username = username_value.strip() if isinstance(username_value, str) else ""
    password = payload.get("password") or ""
    remember = bool(payload.get("remember"))
    if not username and not password:
        raise HTTPException(400, "缺少帳號或密碼")

    key = _login_key(request, username)
    if _login_blocked(key):
        raise HTTPException(429, "嘗試次數過多，請 15 分鐘後再試")

    if not username:
        # 舊前端只送 password：相容「ADMIN_PASSWORD 管理員」登入
        if settings.ADMIN_PASSWORD and not _secure_compare(password, settings.ADMIN_PASSWORD):
            _login_failed(key)
            raise HTTPException(401, "密碼錯誤")
        if settings.ADMIN_PASSWORD:
            user = db.get_user_by_bname(settings.ADMIN_USER)
            if user is None:
                uid = db.create_user(settings.ADMIN_USER, security.hash_password(password), "admin")
                user = db.get_user_by_id(uid)
            if user.get("role") != "admin":
                _login_failed(key)
                raise HTTPException(403, "此帳號不是管理員")
            if (user.get("account_status") or "active") != "active":
                _login_failed(key)
                raise HTTPException(403, "此帳號目前無法登入，請聯絡管理員")
            db.touch_user_login(user["id"])
            auth.set_session_cookie(response, user["id"], "admin", remember, user.get("session_version", 0))
            db.add_audit_log(user["id"], "legacy_admin_password_login", "user", user["id"], {})
        else:
            # 未設 ADMIN_PASSWORD ＝ 無法以舊式登入（L0-2：移除「空密碼直接回 200」後門）
            _login_failed(key)
            raise HTTPException(401, "密碼錯誤")
        _login_ok(key)
        return {"ok": True}

    user = db.get_user_by_bname(username) or db.get_user_by_email(username)
    if not user or not user.get("password_hash") or not security.verify_password(password, user["password_hash"]):
        _login_failed(key)
        raise HTTPException(401, "帳號或密碼錯誤")
    if (user.get("account_status") or "active") != "active":
        _login_failed(key)
        raise HTTPException(403, "此帳號目前無法登入，請聯絡管理員")
    if user.get("pending_email") and not user.get("email_verified_at"):
        _login_failed(key)
        raise HTTPException(403, "請先完成電子郵件驗證")
    if security.needs_rehash(user.get("password_hash")):
        db.update_user_password(user["id"], security.hash_password(password))
        user = db.get_user_by_id(user["id"])
    _login_ok(key)
    db.touch_user_login(user["id"])
    auth.set_session_cookie(response, user["id"], user["role"], remember, user.get("session_version", 0))
    return {"ok": True, "user": _user_json(user)}


@router.post("/logout")
def logout(response: Response):
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/csrf")
def csrf(request: Request, response: Response):
    token = request.cookies.get(auth.CSRF_COOKIE) or secrets.token_urlsafe(32)
    response.set_cookie(auth.CSRF_COOKIE, token, httponly=False,
                        secure=settings.APP_ENV == "production", samesite="lax", path="/")
    return {"token": token}


@router.get("/me")
def me(request: Request):
    u = security.get_current_user(request)
    if not u:
        return {"authed": False, "authMethods": {"google": oauth.google_availability()}}
    account = db.get_user_by_id(u["id"]) if not u.get("dev") else None
    public_profile = db.ensure_public_profile(u["id"], u["username"]) if not u.get("dev") else None
    return {"authed": True, "user": {"id": u["id"], "username": u["username"],
                                      "role": u["role"], "dev": u.get("dev", False)},
            "profile": profile_service.serialize_public_profile(public_profile) if public_profile else None,
            "authMethods": {"google": oauth.google_availability()}}


def _profile_revision(account: dict, public_profile: dict) -> str:
    """Opaque snapshot token for the account-facing settings editor."""
    payload = {
        "displayName": public_profile.get("display_name") or "",
        "bio": public_profile.get("bio") or "",
        "avatarPath": public_profile.get("avatar_path") or "",
        "email": account.get("email") or "",
        "pendingEmail": account.get("pending_email") or "",
        "emailVerifiedAt": account.get("email_verified_at") or "",
        "sessionVersion": account.get("session_version") or 0,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _profile_response(user: dict) -> dict:
    account = db.get_user_by_id(user["id"])
    public_profile = db.ensure_public_profile(user["id"], account["username"])
    return {
        "account": {"id": account["id"], "username": account["username"], "email": account.get("email") or "",
                    "role": account["role"], "status": account.get("account_status") or "active",
                    "emailVerified": bool(account.get("email_verified_at")),
                    "pendingEmail": account.get("pending_email") or "",
                    "hasPasswordCredential": db.has_password_credential(account["id"]),
                    "externalIdentities": [_identity_json(item) for item in db.list_external_identities(account["id"])]},
        "authMethods": {"google": oauth.google_availability()},
        "profileRevision": _profile_revision(account, public_profile),
        "publicProfile": profile_service.serialize_public_profile(public_profile),
        "authorProfiles": [{**profile_service.serialize_author(item), "profileId": item["id"]}
                           for item in db.list_author_profiles(user["id"])],
    }


@router.get("/profile")
def get_profile(user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號沒有個人 profile")
    return _profile_response(user)


@router.patch("/profile")
def update_profile(payload: dict, request: Request, user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號沒有個人 profile")
    _check_profile_rate_limit(user, "update")
    expected_revision = payload.get("expectedProfileRevision")
    if expected_revision is not None and not isinstance(expected_revision, str):
        raise HTTPException(400, "個人設定版本格式不正確")
    fields = {}
    email = None
    email_to_verify = None
    previous_pending_email = ""
    if "displayName" in payload:
        try:
            fields["display_name"] = profile_service.validate_public_display_name(payload.get("displayName"))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    account = db.get_user_by_id(user["id"])
    current_profile = db.ensure_public_profile(user["id"], account["username"])
    if expected_revision is not None and expected_revision != _profile_revision(account, current_profile):
        raise HTTPException(409, "個人設定已在其他分頁更新，請重新載入後再儲存。", headers={"X-StoryLingo-Conflict": "profile"})
    if "email" in payload:
        email = db.normalize_email(payload.get("email") or "")
        current_email = db.normalize_email(account.get("email") or "")
        if not email and not current_email:
            email = None
        elif not email or len(email) > 200 or "@" not in email:
            raise HTTPException(400, "電子郵件格式不正確")
        if email and (email != current_email or not account.get("email_verified_at")):
            _require_recent_auth(request, user)
            previous_pending_email = db.normalize_email(account.get("pending_email") or "")
            email_to_verify = email
    if "bio" in payload:
        try:
            fields["bio"] = profile_service.validate_bio(payload.get("bio"))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    try:
        with db.atomic() as con:
            con.execute("BEGIN IMMEDIATE")
            current = db.get_user_by_id(user["id"])
            current_profile = db.ensure_public_profile(user["id"], current["username"])
            if expected_revision is not None and expected_revision != _profile_revision(current, current_profile):
                raise HTTPException(409, "個人設定已在其他分頁更新，請重新載入後再儲存。", headers={"X-StoryLingo-Conflict": "profile"})
            if email_to_verify:
                db.set_pending_email(user["id"], email_to_verify)
            db.update_account_profile(user["id"], display_name=fields.get("display_name"),
                                      bio=fields.get("bio"), email_provided=False)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if email_to_verify:
        delivery = auth_tokens.issue_email_verification(user["id"], email_to_verify, request)
        if not delivery.ok:
            _log.warning("email delivery unavailable code=%s purpose=email_verification", delivery.code)
            # Do not leave a half-applied email-change target when the provider
            # could not accept the verification message. Resend remains safe.
            if previous_pending_email:
                db.set_pending_email(user["id"], previous_pending_email)
            else:
                db.clear_pending_email(user["id"])
            raise HTTPException(503, "驗證信暫時無法寄送，請稍後再試")
    return _profile_response(user)


@router.post("/profile/authors")
def create_author_profile(payload: dict, user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號沒有作者 profile")
    _check_profile_rate_limit(user, "author-create")
    if user.get("role") not in ("author", "admin"):
        raise HTTPException(403, "請先完成作者申請並經核准")
    try:
        display_name = profile_service.validate_author_name(payload.get("displayName"))
        bio = profile_service.validate_bio(payload.get("bio", ""))
        raw_slug = payload.get("slug")
        slug = (profile_service.plain_text(raw_slug, field="作者網址", maximum=100, minimum=1)
                if raw_slug not in (None, "") else display_name)
        slug = profile_service.unique_slug(slug)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    try:
        profile = db.create_author_profile(
            user["id"], public_id=f"ap_{secrets.token_hex(16)}", slug=slug,
            display_name=display_name, bio=bio)
    except Exception as error:
        if "UNIQUE" in str(error).upper():
            raise HTTPException(409, "作者網址或公開識別碼已被使用") from error
        raise
    return {**profile_service.serialize_author(profile), "profileId": profile["id"]}


@router.patch("/profile/authors/{profile_id}")
def update_author_profile(profile_id: int, payload: dict, user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號沒有作者 profile")
    _check_profile_rate_limit(user, "author-update")
    author = db.get_author_profile(profile_id)
    if not author or author["owner_id"] != user["id"]:
        raise HTTPException(404, "找不到作者 profile")
    if author.get("status") == "tombstone":
        raise HTTPException(409, "已刪除的作者 profile 不可編輯")
    fields = {}
    try:
        if "displayName" in payload:
            fields["display_name"] = profile_service.validate_author_name(payload.get("displayName"))
        if "bio" in payload:
            fields["bio"] = profile_service.validate_bio(payload.get("bio"))
        if "slug" in payload:
            raw_slug = profile_service.plain_text(payload.get("slug"), field="作者網址", maximum=100, minimum=1)
            fields["slug"] = profile_service.unique_slug(raw_slug, exclude_id=profile_id)
        elif "display_name" in fields:
            fields["slug"] = profile_service.unique_slug(fields["display_name"], exclude_id=profile_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    try:
        updated = db.update_author_profile(profile_id, fields)
    except Exception as error:
        if "UNIQUE" in str(error).upper():
            raise HTTPException(409, "作者網址已被使用") from error
        raise
    return profile_service.serialize_author(updated)


@router.post("/profile/avatar")
def upload_profile_avatar(file: UploadFile, user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號沒有個人 profile")
    _check_profile_rate_limit(user, "avatar")
    raw = file.file.read(profile_service.MAX_AVATAR_BYTES + 1)
    try:
        rel_path = profile_service.save_avatar(raw, file.content_type or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    db.update_public_profile(user["id"], avatar_path=rel_path)
    return _profile_response(user)


@router.post("/profile/authors/{profile_id}/avatar")
def upload_author_avatar(profile_id: int, file: UploadFile, user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(403, "開發虛擬帳號沒有作者 profile")
    _check_profile_rate_limit(user, "author-avatar")
    author = db.get_author_profile(profile_id)
    if not author or author["owner_id"] != user["id"]:
        raise HTTPException(404, "找不到作者 profile")
    if author.get("status") == "tombstone":
        raise HTTPException(409, "已刪除的作者 profile 不可編輯")
    raw = file.file.read(profile_service.MAX_AVATAR_BYTES + 1)
    try:
        rel_path = profile_service.save_avatar(raw, file.content_type or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    updated = db.update_author_profile(profile_id, {"avatar_path": rel_path})
    return profile_service.serialize_author(updated)


@router.get("/profile/avatar")
def get_profile_avatar(user: dict = Depends(security.require_user)):
    if user.get("dev"):
        raise HTTPException(404, "找不到頭像")
    profile = db.get_public_profile(user["id"])
    if not profile or not profile.get("avatar_path"):
        raise HTTPException(404, "找不到頭像")
    path = profile_service.stored_avatar_file(profile)
    if not path:
        raise HTTPException(404, "找不到頭像")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/forgot-password")
def forgot_password(payload: dict, request: Request):
    """對所有輸入維持相同成功回應，避免 email enumeration。"""
    target = db.normalize_email(payload.get("email") or "")
    if target and len(target) <= 200 and "@" in target and auth_tokens.recovery_allowed(request, target):
        account = db.get_user_by_email(target)
        if (account and (account.get("account_status") or "active") == "active"
                and account.get("email_verified_at") and account.get("password_hash")):
            delivery = auth_tokens.issue_password_reset(account["id"], target, request)
            if not delivery.ok:
                # 不把 delivery state 暴露給 public caller；configured state 留在內部。
                _log.warning("email delivery unavailable code=%s purpose=password_reset", delivery.code)
    return {"ok": True, "message": "如果帳號符合條件，系統會寄出重設密碼說明"}


@router.post("/reset-password")
def reset_password(payload: dict, request: Request, response: Response):
    token = (payload.get("token") or "").strip()
    new_password = payload.get("newPassword") or payload.get("password") or ""
    try:
        security.validate_password(new_password)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    result = auth_tokens.consume_password_reset(token, new_password)
    if not result or result.get("status") != "reset":
        raise HTTPException(400, "重設連結無效或已過期")
    account = result["account"]
    db.add_audit_log(None, "password_reset", "user", account["id"], {})
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/reset-password")
def reset_password_page(token: str = ""):
    if not token or len(token) > 200:
        return _oauth_failure_redirect("/", "RESET_TOKEN_INVALID")
    # The token is handed directly to the reset form; it is never logged or
    # copied into an OAuth redirect/result parameter.
    return RedirectResponse(f"/#/reset-password?token={quote(token, safe='')}", status_code=303)


@router.get("/verify-email")
def verify_email(token: str = ""):
    result = auth_tokens.consume_email_verification(token)
    if not result or result.get("status") not in ("verified", "conflict"):
        return _oauth_failure_redirect("/", "EMAIL_VERIFICATION_FAILED")
    if result["status"] == "conflict":
        return _oauth_failure_redirect("/", "EMAIL_IDENTITY_CONFLICT")
    account = result["account"]
    db.add_audit_log(account["id"], "email_verified", "user", account["id"],
                     {"source": account.get("email_verified_source") or "email"})
    if result.get("old_email") and result.get("old_email") != result.get("new_email"):
        db.add_audit_log(account["id"], "email_changed", "user", account["id"], {})
    return RedirectResponse(oauth.result_redirect("/", "verified"), status_code=303)


@router.post("/email/resend-verification")
def resend_verification(payload: dict, request: Request):
    """公開 resend 也維持 generic response；不透露帳號/驗證狀態。"""
    target = db.normalize_email(payload.get("email") or "")
    ip = request.client.host if request.client else "unknown"
    if target and len(target) <= 200 and "@" in target and all((
            distributed.allow_window(f"email-verification:ip:{ip}", 20, 3600),
            distributed.allow_window(f"email-verification:target:{target[:200]}", 5, 3600),
            distributed.allow_window("email-verification:global", 500, 3600))):
        account = db.get_user_by_email(target)
        if (account and (account.get("account_status") or "active") == "active"
                and account.get("pending_email") and not account.get("email_verified_at")):
            delivery = auth_tokens.issue_email_verification(account["id"], account["pending_email"], request)
            if not delivery.ok:
                _log.warning("email delivery unavailable code=%s purpose=email_verification", delivery.code)
    return {"ok": True, "message": "如果帳號符合條件，系統會寄出驗證說明"}


@router.post("/password")
def set_password(payload: dict, request: Request, response: Response,
                 user: dict = Depends(security.require_user)):
    _require_recent_auth(request, user)
    account = db.get_user_by_id(user["id"])
    current_password = payload.get("currentPassword") or ""
    if account.get("password_hash") and not security.verify_password(current_password, account["password_hash"]):
        raise HTTPException(401, "目前密碼不正確")
    new_password = payload.get("newPassword") or payload.get("password") or ""
    try:
        security.validate_password(new_password)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    action = "password_changed" if account.get("password_hash") else "password_set"
    db.update_user_password(account["id"], security.hash_password(new_password))
    db.add_audit_log(account["id"], action, "user", account["id"], {})
    refreshed = db.get_user_by_id(account["id"])
    auth.set_session_cookie(response, refreshed["id"], refreshed["role"], True,
                            refreshed.get("session_version", 0), auth_time=int(time.time()))
    return {"ok": True, "hasPasswordCredential": True}


@router.get("/oauth/google/start")
def google_start(return_path: str = "/", termsAccepted: bool = False,
                 privacyAccepted: bool = False, ageConfirmed: bool = False):
    try:
        url = oauth.begin_google(
            purpose="login", account_id=None, return_path=return_path,
            policy_ok=termsAccepted and privacyAccepted and ageConfirmed)
    except oauth.OAuthNotConfigured as error:
        return _auth_error(error.code)
    except oauth.OAuthError:
        return _auth_error("OAUTH_FAILED")
    return RedirectResponse(url, status_code=303)


@router.post("/oauth/google/link/start")
def google_link_start(payload: dict, request: Request,
                      user: dict = Depends(security.require_user)):
    _require_recent_auth(request, user)
    return_path = oauth.validate_return_path(payload.get("returnPath") or "/")
    try:
        url = oauth.begin_google(purpose="link", account_id=user["id"],
                                 return_path=return_path, policy_ok=False)
    except oauth.OAuthNotConfigured as error:
        return _auth_error(error.code)
    except oauth.OAuthError:
        return _auth_error("OAUTH_FAILED")
    return {"url": url}


@router.get("/oauth/google/callback")
def google_callback(request: Request, response: Response, state: str = "",
                    code: str = "", error: str = ""):
    if error or not state or not code:
        db.add_audit_log(None, "oauth_failure", "oauth", 0, {"category": "OAUTH_FAILED"})
        return _oauth_failure_redirect("/", "OAUTH_FAILED")
    try:
        transaction, claims = oauth.complete_google(state=state, code=code)
        issuer = str(claims.get("issuer") or "").rstrip("/")
        subject = str(claims.get("subject") or "")
        email = db.normalize_email(claims.get("email") or "")
        if issuer != settings.GOOGLE_ISSUER.rstrip("/") or not subject or len(subject) > 300:
            raise oauth.OAuthValidationError("OIDC identity 不正確")
        if not email or len(email) > 200 or "@" not in email:
            raise oauth.OAuthValidationError("OIDC email 不正確")
        provider = claims.get("provider") or "google"
        if provider != "google":
            raise oauth.OAuthValidationError("OIDC provider 不正確")
        if transaction["purpose"] == "link":
            current = security.get_current_user(request)
            if not current or current.get("dev") or current["id"] != transaction.get("account_id"):
                raise oauth.OAuthValidationError("link session 無效")
            if db.get_external_identity(issuer=issuer, subject=subject):
                raise oauth.OAuthValidationError("external identity 已被使用")
            confirmation = secrets.token_urlsafe(32)
            db.create_oauth_link_confirmation(
                account_id=current["id"], provider=provider, issuer=issuer, subject=subject,
                email_snapshot=email, email_verified=bool(claims.get("email_verified")),
                confirmation_digest=oauth._digest(confirmation),
                expires_at=(db._dt.datetime.now() + db._dt.timedelta(seconds=600)).isoformat(timespec="seconds"))
            redirect = RedirectResponse(
                oauth.result_redirect(transaction["return_path"], "link_pending"), status_code=303)
            redirect.set_cookie(_OAUTH_LINK_COOKIE, confirmation, max_age=600, httponly=True,
                                secure=settings.APP_ENV == "production", samesite="lax", path="/")
            return redirect

        identity = db.get_external_identity(issuer=issuer, subject=subject)
        if identity:
            account = db.get_user_by_id(identity["account_id"])
            if not account or (account.get("account_status") or "active") != "active":
                raise oauth.OAuthValidationError("account disabled")
            db.touch_external_identity(identity["id"], email_snapshot=email,
                                       email_verified=bool(claims.get("email_verified")))
            db.touch_user_login(account["id"])
            redirect = RedirectResponse(oauth.result_redirect(transaction["return_path"], "success"),
                                        status_code=303, headers={"Cache-Control": "no-store"})
            auth.set_session_cookie(redirect, account["id"], account["role"], True,
                                    account.get("session_version", 0), auth_time=int(time.time()))
            return redirect

        if not claims.get("email_verified"):
            raise oauth.OAuthValidationError("provider email 未驗證")
        matches = db.get_users_by_email(email)
        if len(matches) > 1:
            db.add_audit_log(None, "oauth_email_identity_conflict", "external_identity", 0,
                             {"provider": provider, "category": "EMAIL_IDENTITY_CONFLICT"})
            raise oauth.OAuthValidationError("EMAIL_IDENTITY_CONFLICT")
        if len(matches) == 1:
            db.add_audit_log(None, "oauth_existing_account_requires_link", "user", matches[0]["id"],
                             {"provider": provider, "category": "EXISTING_ACCOUNT_REQUIRES_LINK"})
            raise oauth.OAuthValidationError("EXISTING_ACCOUNT_REQUIRES_LINK")
        if settings.APP_ENV == "production" and not transaction.get("policy_ok"):
            raise oauth.OAuthValidationError("POLICY_REQUIRED")
        username = _internal_google_username(issuer, subject)
        now = db.ts()
        with db.atomic():
            uid = db.create_user(username, None, "reader", email,
                                 email_verified_at=now, email_verified_source="google")
            db.create_external_identity(uid, provider=provider, issuer=issuer, subject=subject,
                                        email_snapshot=email, email_verified=True)
            db.add_audit_log(None, "oauth_account_created", "user", uid, {"provider": provider})
        account = db.get_user_by_id(uid)
        db.touch_user_login(uid)
        redirect = RedirectResponse(oauth.result_redirect(transaction["return_path"], "success"),
                                    status_code=303, headers={"Cache-Control": "no-store"})
        auth.set_session_cookie(redirect, uid, account["role"], True,
                                account.get("session_version", 0), auth_time=int(time.time()))
        return redirect
    except oauth.OAuthNotConfigured as oauth_error:
        return _oauth_failure_redirect("/", oauth_error.code)
    except oauth.OAuthError as oauth_error:
        code = str(oauth_error)
        category = "EMAIL_IDENTITY_CONFLICT" if "EMAIL_IDENTITY_CONFLICT" in code else (
            "EXISTING_ACCOUNT_REQUIRES_LINK" if "EXISTING_ACCOUNT_REQUIRES_LINK" in code else (
                "POLICY_REQUIRED" if "POLICY_REQUIRED" in code else "OAUTH_FAILED"))
        db.add_audit_log(None, "oauth_failure", "oauth", 0, {"category": category})
        return _oauth_failure_redirect("/", category)
    except sqlite3.IntegrityError:
        db.add_audit_log(None, "oauth_failure", "oauth", 0, {"category": "IDENTITY_COLLISION"})
        return _oauth_failure_redirect("/", "IDENTITY_COLLISION")


@router.post("/oauth/google/link/confirm")
def google_link_confirm(request: Request, response: Response,
                        user: dict = Depends(security.require_user)):
    _require_recent_auth(request, user)
    raw = request.cookies.get(_OAUTH_LINK_COOKIE) or ""
    pending = db.consume_oauth_link_confirmation(
        account_id=user["id"], confirmation_digest=oauth._digest(raw))
    if not pending:
        raise HTTPException(400, "Google 連結確認已失效")
    if db.get_external_identity(issuer=pending["issuer"], subject=pending["subject"]):
        raise HTTPException(409, "此 Google 身份已被其他帳號使用")
    with db.atomic():
        identity = db.create_external_identity(
            user["id"], provider=pending["provider"], issuer=pending["issuer"],
            subject=pending["subject"], email_snapshot=pending["email_snapshot"],
            email_verified=bool(pending["email_verified"]))
        db.add_audit_log(user["id"], "oauth_linked", "external_identity", identity["id"],
                         {"provider": pending["provider"]})
    refreshed = db.get_user_by_id(user["id"])
    auth.set_session_cookie(response, refreshed["id"], refreshed["role"], True,
                            refreshed.get("session_version", 0), auth_time=int(time.time()))
    response.delete_cookie(_OAUTH_LINK_COOKIE, path="/")
    return {"ok": True, "identity": _identity_json(identity)}


@router.post("/oauth/google/link/cancel")
def google_link_cancel(response: Response):
    response.delete_cookie(_OAUTH_LINK_COOKIE, path="/")
    return {"ok": True}


@router.delete("/oauth/google/{identity_id}")
def google_unlink(identity_id: int, request: Request, response: Response,
                  user: dict = Depends(security.require_user)):
    _require_recent_auth(request, user)
    identity = db.query_one("SELECT * FROM external_identities WHERE id=? AND account_id=?",
                            (identity_id, user["id"]))
    if not identity:
        raise HTTPException(404, "找不到 Google 身份")
    if identity.get("provider") != "google":
        raise HTTPException(404, "找不到 Google 身份")
    if not db.has_password_credential(user["id"]) and len(db.list_external_identities(user["id"])) <= 1:
        raise HTTPException(409, "OAuth-only 帳號請先設定密碼，才能解除最後一個登入方式")
    with db.atomic():
        db.delete_external_identity(identity_id)
        db.add_audit_log(user["id"], "oauth_unlinked", "external_identity", identity_id,
                         {"provider": identity["provider"]})
    refreshed = db.get_user_by_id(user["id"])
    auth.set_session_cookie(response, refreshed["id"], refreshed["role"], True,
                            refreshed.get("session_version", 0), auth_time=int(time.time()))
    return {"ok": True}


def _secure_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _check_profile_rate_limit(user: dict, operation: str):
    if not distributed.allow_window(f"profile:{operation}:{user['id']}", 20, 600):
        raise HTTPException(429, "個人設定操作太頻繁，請稍後再試")
