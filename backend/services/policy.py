"""集中式固定角色 capability policy。

角色本身不是階級；每個敏感動作都經由明確 capability 判斷，且
`security.get_current_user` 已在每個 request 重新讀取 Account 狀態。
"""
from __future__ import annotations

from .. import db
from .. import exceptions as exc

ROLES = ("reader", "author", "reviewer", "admin", "super_admin")

CAPABILITIES = {
    "reader": frozenset(),
    "author": frozenset({
        "create_book", "manage_owned_book", "request_publish", "request_unpublish",
        "request_audiobook", "cancel_own_request", "view_own_request",
    }),
    # Reviewer intentionally has no user/provider/security administration.
    "reviewer": frozenset({
        "review_content_request", "operate_generation", "view_request_detail",
    }),
    # Phase 1 transitional compatibility: Admin keeps reviewer operations.
    "admin": frozenset({
        # Admin is also a legitimate owner of Books. These remain owner-scoped;
        # the helpers still require books.owner_id to match the Account.
        "create_book", "manage_owned_book", "request_publish", "request_unpublish",
        "request_audiobook", "cancel_own_request", "view_own_request",
        "review_content_request", "operate_generation",
        "manage_providers", "manage_users", "manage_roles", "emergency_hide",
        "manage_chapter_visibility", "view_request_detail", "manage_announcements",
    }),
    "super_admin": frozenset({
        "create_book", "manage_owned_book", "request_publish", "request_unpublish",
        "request_audiobook", "cancel_own_request", "view_own_request",
        "review_content_request", "operate_generation",
        "manage_providers", "manage_users", "manage_roles", "manage_super_admin", "export_audit",
        "emergency_hide", "manage_chapter_visibility", "view_request_detail", "bootstrap_super_admin",
        "manage_announcements",
    }),
}


def current_account(user: dict | None) -> dict | None:
    """以 DB 的 role/status 作 canonical authority；dev 虛擬帳號保留測試語意。"""
    if not user:
        return None
    if user.get("dev"):
        return user
    row = db.get_user_by_id(int(user.get("id") or 0))
    if not row or (row.get("account_status") or "active") != "active":
        return None
    return row


def has_capability(user: dict | None, capability: str, *, book: dict | None = None,
                   requester_id: int | None = None) -> bool:
    account = current_account(user)
    if not account:
        return False
    role = account.get("role")
    if role not in CAPABILITIES or capability not in CAPABILITIES[role]:
        return False
    if capability == "manage_owned_book" and book is not None and not account.get("dev"):
        return int(account["id"]) == int(book.get("owner_id") or -1) or role in ("admin", "super_admin")
    if capability == "view_own_request" and requester_id is not None and not account.get("dev"):
        return int(account["id"]) == int(requester_id)
    return True


def require_capability(user: dict | None, capability: str, *, book: dict | None = None,
                       requester_id: int | None = None) -> dict:
    account = current_account(user)
    if not account:
        raise exc.unauthorized()
    if not has_capability(account, capability, book=book, requester_id=requester_id):
        raise exc.forbidden()
    return account


def can_create_book(user: dict | None) -> bool:
    return has_capability(user, "create_book")


def can_manage_owned_book(user: dict | None, book: dict) -> bool:
    return has_capability(user, "manage_owned_book", book=book)


def can_request_publish(user: dict | None, book: dict) -> bool:
    return has_capability(user, "request_publish") and can_manage_owned_book(user, book)


def can_request_unpublish(user: dict | None, book: dict) -> bool:
    return has_capability(user, "request_unpublish") and can_manage_owned_book(user, book)


def can_request_audiobook(user: dict | None, book: dict) -> bool:
    return has_capability(user, "request_audiobook") and can_manage_owned_book(user, book)


def can_cancel_own_request(user: dict | None, requester_id: int) -> bool:
    return has_capability(user, "cancel_own_request", requester_id=requester_id)


def can_review_content_request(user: dict | None) -> bool:
    return has_capability(user, "review_content_request")


def can_operate_generation(user: dict | None) -> bool:
    return has_capability(user, "operate_generation")


def can_manage_provider_config(user: dict | None) -> bool:
    return has_capability(user, "manage_providers")


def can_manage_users(user: dict | None) -> bool:
    return has_capability(user, "manage_users")


def can_manage_roles(user: dict | None) -> bool:
    return has_capability(user, "manage_roles")


def can_manage_super_admin(user: dict | None) -> bool:
    return has_capability(user, "manage_super_admin")


def can_export_audit(user: dict | None) -> bool:
    """R18 v1 export is deliberately narrower than Admin audit browsing."""
    return has_capability(user, "export_audit")


def can_emergency_hide(user: dict | None) -> bool:
    return has_capability(user, "emergency_hide")


def can_manage_announcements(user: dict | None) -> bool:
    return has_capability(user, "manage_announcements")


def can_manage_chapter_visibility(user: dict | None) -> bool:
    return has_capability(user, "manage_chapter_visibility")


def is_super_admin(user: dict | None) -> bool:
    account = current_account(user)
    return bool(account and account.get("role") == "super_admin")
