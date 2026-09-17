"""One-time email verification and password recovery tokens."""
from datetime import datetime, timedelta
import hashlib
import secrets

from .. import db, settings
from . import distributed, mail

EMAIL_TOKEN_TTL = timedelta(hours=24)
RESET_TOKEN_TTL = timedelta(hours=1)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _expires(ttl: timedelta) -> str:
    return (datetime.now() + ttl).isoformat(timespec="seconds")


def _link(path: str, raw: str) -> str:
    return f"{settings.AUTH_BASE_URL}{path}?token={raw}"


def _safe_delivery_link(path: str, raw: str, purpose: str):
    link = _link(path, raw)
    return link if mail.is_auth_link_safe(link, purpose=purpose) else None


def _request_ip(request) -> str:
    client = getattr(request, "client", None)
    return (getattr(client, "host", "") or "")[:80]


def issue_email_verification(account_id: int, email: str, request) -> mail.MailDeliveryResult:
    raw = secrets.token_urlsafe(32)
    link = _safe_delivery_link("/api/auth/verify-email", raw, "email_verification")
    if link is None:
        return mail.MailDeliveryResult(False, "NOT_CONFIGURED")
    db.invalidate_auth_tokens(account_id, "email_verification")
    db.create_auth_token(account_id, purpose="email_verification", token_digest=_digest(raw),
                         target_email=email, expires_at=_expires(EMAIL_TOKEN_TTL),
                         requested_ip=_request_ip(request))
    return mail.get_mail_adapter().send(
        to=db.normalize_email(email), purpose="email_verification",
        link=link)


def issue_password_reset(account_id: int, email: str, request) -> mail.MailDeliveryResult:
    raw = secrets.token_urlsafe(32)
    link = _safe_delivery_link("/api/auth/reset-password", raw, "password_reset")
    if link is None:
        return mail.MailDeliveryResult(False, "NOT_CONFIGURED")
    db.invalidate_auth_tokens(account_id, "password_reset")
    db.create_auth_token(account_id, purpose="password_reset", token_digest=_digest(raw),
                         target_email=email, expires_at=_expires(RESET_TOKEN_TTL),
                         requested_ip=_request_ip(request))
    return mail.get_mail_adapter().send(
        to=db.normalize_email(email), purpose="password_reset",
        link=link)


def consume_email_verification(raw: str):
    row = db.consume_auth_token(purpose="email_verification", token_digest=_digest(raw or ""))
    if not row:
        return None
    account = db.get_user_by_id(row["account_id"])
    if not account or (account.get("account_status") or "active") != "active":
        return None
    pending = db.normalize_email(account.get("pending_email") or "")
    target = db.normalize_email(row.get("target_email") or "")
    if not pending or pending != target:
        return None
    try:
        db.promote_verified_email(account["id"], pending, source="email")
    except ValueError:
        # The token is already consumed; the conflict is intentionally not
        # resolved by picking a winner or merging accounts.
        return {"status": "conflict", "account": account}
    return {"status": "verified", "account": db.get_user_by_id(account["id"]),
            "old_email": db.normalize_email(account.get("email") or ""), "new_email": pending}


def consume_password_reset(raw: str, new_password: str):
    row = db.consume_auth_token(purpose="password_reset", token_digest=_digest(raw or ""))
    if not row:
        return None
    account = db.get_user_by_id(row["account_id"])
    if not account or (account.get("account_status") or "active") != "active":
        return None
    if not account.get("password_hash"):
        return {"status": "oauth_only", "account": account}
    from .. import security
    db.update_user_password(account["id"], security.hash_password(new_password))
    db.invalidate_auth_tokens(account["id"], "password_reset")
    return {"status": "reset", "account": db.get_user_by_id(account["id"])}


def recovery_allowed(request, target: str) -> bool:
    normalized = db.normalize_email(target)
    ip = _request_ip(request)
    return (
        distributed.allow_window(f"password-recovery:ip:{ip}", 20, 3600)
        and distributed.allow_window(f"password-recovery:target:{normalized[:200]}", 5, 3600)
        and distributed.allow_window("password-recovery:global", 500, 3600)
    )
