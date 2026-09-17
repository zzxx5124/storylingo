"""密碼雜湊（PBKDF2-HMAC-SHA256，stdlib 零依賴）與 FastAPI 角色依賴。"""
import hashlib
import hmac
import os

from fastapi import Depends, HTTPException, Request
from typing import Annotated, Optional

from . import auth, db, settings

_PBKDF2_ITER = 600_000
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256


def hash_password(pw: str) -> str:
    validate_password(pw)
    salt = os.urandom(16)
    d = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"{_PBKDF2_ITER}|{salt.hex()}|{d.hex()}"


def validate_password(pw: str) -> None:
    if not isinstance(pw, str):
        raise ValueError("密碼格式不正確")
    if len(pw or "") < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密碼至少 {PASSWORD_MIN_LENGTH} 個字元")
    if len(pw) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"密碼不可超過 {PASSWORD_MAX_LENGTH} 個字元")


def needs_rehash(stored: str) -> bool:
    try:
        iterations = int((stored or "").split("|", 1)[0])
        return iterations < _PBKDF2_ITER
    except Exception:
        return False


def verify_password(pw: str, stored: str) -> bool:
    try:
        it, sh, hh = (stored or "").split("|")
        d = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(sh), int(it))
        return hmac.compare_digest(d.hex(), hh)
    except Exception:
        return False


def dev_bypass_enabled() -> bool:
    """dev 身份（DB 無使用者即視為 admin）需同時滿足：
    - 環境變數 DEV_AUTH_BYPASS=true 顯式開啟（防誤開後門）
    - APP_ENV 不是 production（正式環境禁止任何形式的全開）
    """
    return settings.DEV_AUTH_BYPASS and settings.APP_ENV != "production"


def get_current_user(request: Request) -> Optional[dict]:
    """回傳 {id, username, role, dev(bool)} 或 None。

    dev 模式：僅在「DEV_AUTH_BYPASS 顯式開啟且非 production」且資料庫尚無使用者時，
    視為已登入的虛擬 admin。預設完全不啟用（見 dev_bypass_enabled）。
    """
    if db.any_users():
        token = auth.verify_token_details(request.cookies.get(auth.COOKIE_NAME))
        if not token:
            return None
        uid = token["sub"]
        row = db.get_user_by_id(uid)
        if not row:
            return None
        if (row.get("account_status") or "active") != "active":
            return None
        if token.get("sv", 0) != int(row.get("session_version") or 0):
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"],
                "dev": False, "auth_time": token.get("auth_time", 0)}
    if dev_bypass_enabled():
        return {"id": 0, "username": "dev", "role": "admin", "dev": True}
    return None


def require_user(user: Annotated[Optional[dict], Depends(get_current_user)]) -> dict:
    if not user:
        raise HTTPException(401, "請先登入")
    return user


def require_roles(*roles):
    def dep(user: Annotated[Optional[dict], Depends(get_current_user)]) -> dict:
        if not user:
            raise HTTPException(401, "請先登入")
        if user["role"] not in roles:
            raise HTTPException(403, "權限不足")
        return user
    return dep


require_author = require_roles("author", "admin", "super_admin")
require_admin = require_roles("admin", "super_admin")
