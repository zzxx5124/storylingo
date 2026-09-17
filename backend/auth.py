"""多使用者 session：HMAC 簽名 cookie。claim = {"sub": user_id, "role": ..., "exp": ...}。
沿用 v3 既有 HMAC cookie 架構，簽名金鑰改用 data/.session_key（首建）。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Response

from . import settings

COOKIE_NAME = "novel_session"
CSRF_COOKIE = "novel_csrf"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 天

_session_key = None


def _key() -> bytes:
    global _session_key
    if _session_key is not None:
        return _session_key
    p = settings.SESSION_KEY_FILE
    try:
        if os.path.exists(p):
            with open(p, "rb") as f:
                k = f.read().strip()
            if len(k) >= 16:
                _session_key = k
                return _session_key
    except OSError:
        pass
    # 首建：隨機 32 bytes。絕不以管理員密碼衍生 session signing key。
    try:
        k = os.urandom(32)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(k)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        _session_key = k
    except OSError as error:
        raise RuntimeError("無法建立安全的 session key，已拒絕啟用認證") from error
    return _session_key


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return hmac.new(_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def derived_secret(purpose: str) -> bytes:
    """由獨立 session key 衍生用途限定的加密 key；不暴露原始 key。"""
    return hashlib.sha256(_key() + (purpose or "").encode("utf-8")).digest()


def make_token(user_id: int, role: str, max_age: int = COOKIE_MAX_AGE, session_version: int = 0,
               auth_time: int | None = None) -> str:
    now = int(time.time())
    body = _b64e(json.dumps({
        "sub": int(user_id),
        "role": role,
        "sv": int(session_version or 0),
        "iat": now,
        "auth_time": int(auth_time or now),
        "exp": now + max_age,
    }).encode("utf-8"))
    return f"{body}.{_sign(body)}"


def verify_token_details(token: str):
    """回傳 user_id（int）或 None。簽名錯誤／過期／格式錯 → None。"""
    if not token:
        return None
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except Exception:
        return None
    if "sub" not in payload:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return {"sub": int(payload["sub"]), "sv": int(payload.get("sv", 0)),
            "auth_time": int(payload.get("auth_time", payload.get("iat", 0)) or 0)}


def verify_token(token: str):
    details = verify_token_details(token)
    return details["sub"] if details else None


def set_session_cookie(response: Response, user_id: int, role: str, remember: bool = False,
                       session_version: int = 0, auth_time: int | None = None):
    max_age = COOKIE_MAX_AGE if remember else None
    response.set_cookie(
        COOKIE_NAME,
        make_token(user_id, role, COOKIE_MAX_AGE, session_version, auth_time=auth_time),
        max_age=max_age,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        path="/",
    )
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), max_age=max_age,
                        httponly=False, secure=settings.APP_ENV == "production",
                        samesite="lax", path="/")
    return response


def clear_session_cookie(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response
