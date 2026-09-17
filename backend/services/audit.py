"""R18 canonical audit writer and policy helpers.

`audit_logs` is the security/governance evidence source.  Domain histories,
generation execution history and recipient notifications intentionally keep
their own storage and lifecycle.  This module is the single server-side
writer boundary for new canonical audit rows; callers never supply actor
identity from an HTTP payload.
"""
from __future__ import annotations

import json
import re
from typing import Any

AUDIT_POLICY_VERSION = "R18-v1"
AUDIT_PURGE_ENABLED = False
AUDIT_ARCHIVE_MODE = "primary_sqlite"

MAX_ACTION_LENGTH = 100
MAX_TARGET_TYPE_LENGTH = 50
MAX_TARGET_ID_LENGTH = 100
MAX_DETAILS_BYTES = 4000
MAX_REASON_LENGTH = 1000

_DROP = object()
_KEY_FORBIDDEN = (
    "secret", "token", "password", "authorization", "credential", "oauth",
    "environment", "header", "payload", "apikey", "api_key", "access_key",
    "refresh_key", "session_key", "private_key",
)
_FIELD_LIST_KEYS = {"fields", "changedfields", "changed_fields", "fieldnames", "field_names"}
_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|access[_ -]?token|refresh[_ -]?token|"
    r"client[_ -]?secret|password|secret)\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")

# Keep the compatibility writer useful for existing server call sites while
# rejecting arbitrary client-shaped event names.  Family prefixes cover
# ownership-transfer and announcement lifecycle events without permitting
# arbitrary data as an action.
ALLOWED_ACTIONS = frozenset({
    "legacy_admin_password_login", "password_reset", "email_verified", "email_changed",
    "password_changed", "password_set", "oauth_failure", "oauth_email_identity_conflict",
    "oauth_existing_account_requires_link", "oauth_account_created", "oauth_linked",
    "oauth_unlinked", "bootstrap_super_admin", "protected_super_admin_mutation_attempt",
    "set_user_status", "role_revoked", "role_granted", "set_role", "set_password",
    "soft_delete_user", "clear_job", "clear_jobs", "create_tts_provider",
    "update_tts_provider", "delete_tts_provider", "test_tts_provider",
    "refresh_tts_capabilities", "create_ai_provider", "update_ai_provider",
    "delete_ai_provider", "test_ai_provider", "reopen_author_application",
    "approve_author_application", "reject_author_application", "generation_job_cancelled",
    "generation_job_cancel_requested", "generation_job_retried", "content_request_submitted",
    "content_request_cancelled", "content_request_review_started", "content_request_invalidated",
    "content_request_approved", "content_request_rejected", "publish_transition",
    "unpublish_transition", "audiobook_authorized", "audiobook_generation_operation_started",
    "emergency_hide", "provider_update", "audit_event_corrected", "audit_export_requested",
    "audit_export_completed", "audit_export_failed",
})
ALLOWED_ACTION_PREFIXES = ("ownership_transfer_", "announcement_", "content_request_")


def validate_action(action: Any) -> str:
    """Validate a server event name against the stable R18 taxonomy."""
    value = str(action or "").strip()
    if not _ACTION_RE.fullmatch(value):
        raise ValueError("稽核動作名稱格式不正確")
    if value not in ALLOWED_ACTIONS and not any(value.startswith(prefix) for prefix in ALLOWED_ACTION_PREFIXES):
        raise ValueError(f"不支援的稽核動作：{value}")
    return value


def event_class(action: str) -> str:
    """Return the policy class, without changing the legacy UI category."""
    name = str(action or "").lower()
    if (name.startswith(("oauth_", "password_", "email_", "legacy_admin_password_login"))
            or "super_admin" in name or name in {"set_password", "protected_super_admin_mutation_attempt"}):
        return "security"
    if (name.startswith(("role_", "set_role", "set_user_status", "soft_delete_user",
                         "bootstrap_super_admin", "audit_export_", "ownership_transfer_",
                         "author_application")) or name in {"approve_author_application", "reject_author_application"}):
        return "governance"
    if name.startswith(("content_request_", "publish_", "unpublish_", "audiobook_", "emergency_hide")):
        return "content"
    if ("provider" in name or "generation" in name or "job" in name
            or name.startswith("clear_")):
        return "operations"
    return "system"


def _forbidden_key(key: Any) -> bool:
    lowered = str(key or "").replace("-", "_").lower()
    return any(word in lowered for word in _KEY_FORBIDDEN)


def _scrub_string(value: str, limit: int = 500) -> str:
    bounded = value[:limit]
    return _VALUE_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[已遮蔽]", bounded)


def _safe_field_name(value: Any) -> Any:
    name = str(value or "")[:80]
    return _DROP if _forbidden_key(name) else name


def sanitize_details(details: Any) -> dict:
    """Produce valid, bounded JSON while removing credential-shaped data."""
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (TypeError, ValueError, json.JSONDecodeError):
            details = {}
    if not isinstance(details, dict):
        details = {}

    def clean(item: Any, depth: int = 0, parent_key: str = "") -> Any:
        if depth > 3:
            return "[省略]"
        if isinstance(item, dict):
            result = {}
            for raw_key, child in item.items():
                key = str(raw_key)[:80]
                if _forbidden_key(key):
                    continue
                cleaned = clean(child, depth + 1, key)
                if cleaned is not _DROP:
                    result[key] = cleaned
            return result
        if isinstance(item, list):
            result = []
            for child in item[:20]:
                if parent_key.replace("-", "_").lower() in _FIELD_LIST_KEYS:
                    cleaned = _safe_field_name(child)
                else:
                    cleaned = clean(child, depth + 1, parent_key)
                if cleaned is not _DROP:
                    result.append(cleaned)
            return result
        if isinstance(item, tuple):
            return clean(list(item), depth, parent_key)
        if isinstance(item, str):
            return _scrub_string(item)
        if isinstance(item, (bool, int, float)) or item is None:
            return item
        return _scrub_string(str(item), 200)

    result = clean(details)
    if not isinstance(result, dict):
        result = {}
    try:
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        encoded = "{}"
        result = {}
    if len(encoded.encode("utf-8")) > MAX_DETAILS_BYTES:
        result = {"_redacted": True, "reason": "payload_exceeds_bound"}
    return result


def _safe_reason(reason: Any) -> str:
    return _scrub_string(str(reason or ""), MAX_REASON_LENGTH)


def write_audit(actor_id: int | None, action: str, target_type: str = "", target_id: Any = "",
                details: Any = None, *, connection=None, created_at: str | None = None,
                correction_of_id: int | None = None) -> int:
    """Insert one immutable audit row using a trusted server actor.

    ``connection`` is used only by existing multi-write transactions such as
    bootstrap and author approval.  It deliberately does not commit; the
    caller owns that transaction boundary.
    """
    action_value = validate_action(action)
    try:
        actor_value = int(actor_id) if actor_id is not None else None
    except (TypeError, ValueError) as error:
        raise ValueError("稽核操作者識別不正確") from error
    if actor_value is not None and actor_value < 1:
        actor_value = None
    target_value = str(target_type or "")[:MAX_TARGET_TYPE_LENGTH]
    target_id_value = _scrub_string(str(target_id or ""), MAX_TARGET_ID_LENGTH)
    safe_details = sanitize_details(details)
    encoded = json.dumps(safe_details, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    params = (actor_value, action_value, target_value, target_id_value, encoded,
              event_class(action_value), AUDIT_POLICY_VERSION, correction_of_id,
              created_at or _now())
    sql = (
        "INSERT INTO audit_logs(actor_id, action, target_type, target_id, details, "
        "event_class, policy_version, correction_of_id, created_at) VALUES(?,?,?,?,?,?,?,?,?)"
    )
    if connection is not None:
        return int(connection.execute(sql, params).lastrowid)
    # Lazy import avoids making db.py depend on this module during import.
    from .. import db
    return int(db.execute(sql, params).lastrowid)


def record_correction(actor_id: int, original_audit_id: int, reason: str,
                      details: dict | None = None, *, connection=None) -> int:
    """Append a correction event; never mutate the original evidence row."""
    try:
        original_id = int(original_audit_id)
    except (TypeError, ValueError) as error:
        raise ValueError("原稽核紀錄識別不正確") from error
    payload = {"originalAuditId": original_id, "reason": _safe_reason(reason)}
    if details:
        payload["correction"] = details
    return write_audit(actor_id, "audit_event_corrected", "audit_log", original_id,
                       payload, connection=connection, correction_of_id=original_id)


def retention_policy() -> dict:
    """Read-only policy metadata; no expiry or purge worker exists in R18 v1."""
    return {
        "policyVersion": AUDIT_POLICY_VERSION,
        "archiveMode": AUDIT_ARCHIVE_MODE,
        "automaticExpiry": False,
        "purgeEnabled": AUDIT_PURGE_ENABLED,
        "purgeAuthority": "future_protected_child",
        "domainHistoriesRemainSeparate": True,
    }


def _now() -> str:
    from .. import db
    return db.ts()
