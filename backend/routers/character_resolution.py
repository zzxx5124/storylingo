"""Book-level character identity correction API（最小受保護入口）。"""
from typing import Annotated, Optional

from fastapi import APIRouter, Depends

from .. import exceptions as exc
from ..security import get_current_user, require_admin, require_author
from ..services import book_service, character_resolution as identity

router = APIRouter(prefix="/api", tags=["character-resolution"])


def _managed_book(bid: str, user: dict):
    row = book_service.load_book(bid)
    book_service.ensure_can_manage(row, user)
    return row


def _revision(payload: dict):
    if "expectedRevision" not in payload:
        raise exc.bad_request("缺少 expectedRevision，請重新讀取角色 registry")
    try:
        return int(payload["expectedRevision"])
    except (TypeError, ValueError) as error:
        raise exc.bad_request("expectedRevision 不合法") from error


@router.get("/books/{bid}/character-identities")
def get_character_identities(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    if not book_service.can_view_content(row, user):
        raise exc.not_found("找不到書")
    return identity.get_registry(bid)


@router.get("/books/{bid}/character-identities/history")
def get_character_history(bid: str, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return {"items": identity.list_history(bid)}


@router.get("/books/{bid}/character-identities/proposals")
def get_character_proposals(bid: str, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return {"items": identity.list_proposals(bid)}


@router.get("/books/{bid}/character-identities/candidates")
def resolve_character_candidates(bid: str, surface: str, scope_type: str = "chapter", scope_key: str = "",
                                  user: Annotated[dict, Depends(require_author)] = None):
    _managed_book(bid, user)
    return identity.resolve_surface(bid, surface, scope_type=scope_type, scope_key=scope_key)


@router.post("/books/{bid}/character-identities/aliases")
def assign_character_alias(bid: str, payload: dict, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return identity.assign_alias(
        bid, surface=str(payload.get("surface") or ""),
        target_character_id=payload.get("targetCharacterId"), scope_type=str(payload.get("scopeType") or "book"),
        scope_key=str(payload.get("scopeKey") or ""), relation_type=str(payload.get("relationType") or "alias"),
        confidence=payload.get("confidence", 1.0), evidence=payload.get("evidence") or [], actor_id=user["id"],
        expected_revision=_revision(payload),
    )


@router.post("/books/{bid}/character-identities/proposals")
def create_character_proposal(bid: str, payload: dict, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return identity.propose_reconciliation(
        bid,
        source_character_id=str(payload.get("sourceCharacterId") or ""),
        target_character_id=str(payload.get("targetCharacterId") or ""),
        confidence=payload.get("confidence", 0), evidence=payload.get("evidence") or [],
        reason=str(payload.get("reason") or ""), actor_id=user["id"],
        expected_revision=_revision(payload),
    )


@router.post("/books/{bid}/character-identities/proposals/{proposal_id}/accept")
def accept_character_proposal(bid: str, proposal_id: int, payload: dict, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return identity.accept_proposal(bid, proposal_id, actor_id=user["id"], expected_revision=_revision(payload),
                                    force_manual=bool(payload.get("forceManual")))


@router.post("/books/{bid}/character-identities/merge")
def merge_character(bid: str, payload: dict, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return identity.merge_characters(
        bid, source_character_id=str(payload.get("sourceCharacterId") or ""),
        target_character_id=str(payload.get("targetCharacterId") or ""), actor_id=user["id"],
        expected_revision=_revision(payload), evidence=payload.get("evidence") or [],
        reason=str(payload.get("reason") or ""),
    )


@router.post("/books/{bid}/character-identities/split")
def split_character(bid: str, payload: dict, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    try:
        mappings = payload.get("mappings") or []
        if not isinstance(mappings, list):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise exc.bad_request("mappings 不合法") from error
    return identity.split_character(
        bid, source_character_id=str(payload.get("sourceCharacterId") or ""), mappings=mappings,
        actor_id=user["id"], expected_revision=_revision(payload), reason=str(payload.get("reason") or ""),
    )


@router.get("/books/{bid}/character-identities/voice-conflicts")
def get_voice_conflicts(bid: str, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return {"items": identity.list_voice_conflicts(bid)}


@router.post("/books/{bid}/character-identities/voice-conflicts/{conflict_id}/resolve")
def resolve_voice_conflict(bid: str, conflict_id: int, payload: dict, user: Annotated[dict, Depends(require_author)]):
    _managed_book(bid, user)
    return identity.resolve_voice_conflict(
        bid, conflict_id, selected_voice=str(payload.get("selectedVoice") or ""), actor_id=user["id"],
        reason=str(payload.get("reason") or ""), expected_revision=_revision(payload),
    )


@router.post("/admin/books/{bid}/character-migration")
def migrate_character_registry(bid: str, payload: dict, user: Annotated[dict, Depends(require_admin)]):
    if payload.get("rollbackSnapshotId") is not None:
        return identity.rollback_migration(bid, int(payload["rollbackSnapshotId"]), actor_id=user["id"])
    return identity.migrate_book(bid, apply=bool(payload.get("apply", False)), actor_id=user["id"])
