"""V4 book-level character identity registry and reconciliation service.

本模組只在 validated ready v3 artifact 之上建立 identity view。ready artifact
不可改寫；registry、append-only events 與 compatibility projection 才承接後續
merge/split。AI proposal 永遠先進 proposal table，不直接變成永久 merge。
"""
import hashlib
import json
import re
import unicodedata
import uuid

from .. import db, settings

POLICY_VERSION = "entity-resolution-v1"
AUTO_ACCEPT_THRESHOLD = 0.90
PENDING_THRESHOLD = 0.60
CHARACTER_ID_PATTERN = re.compile(r"^char_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
GENERIC_SURFACES = frozenset({
    "哥哥", "姐姐", "老師", "教授", "殿下", "師父", "父親", "母親", "老闆",
    "他", "她", "你", "我",
})


class CharacterResolutionError(ValueError):
    """Identity resolution contract violation."""


class RegistryConflictError(CharacterResolutionError):
    """Mutation used a stale registry revision."""


def is_valid_character_id(value: str) -> bool:
    return isinstance(value, str) and bool(CHARACTER_ID_PATTERN.fullmatch(value))


def normalize_surface(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).strip().casefold()


def _json(value, fallback):
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def _book(bid):
    row = db.get_book_row(str(bid))
    if not row:
        raise CharacterResolutionError("找不到書")
    return row


def _new_character_id(book_id: int, name: str) -> str:
    return "char_" + hashlib.sha256(f"{book_id}\0{name}".encode("utf-8")).hexdigest()[:16]


def _ensure_meta_locked(book_id: int):
    row = db.query_one("SELECT * FROM character_registry_meta WHERE book_id=?", (book_id,))
    if row:
        return row
    now = db.ts()
    db._conn().execute(
        "INSERT INTO character_registry_meta(book_id, revision, policy_version, state_hash, updated_at) VALUES(?,0,?,?,?)",
        (book_id, POLICY_VERSION, "", now),
    )
    return {"book_id": book_id, "revision": 0, "policy_version": POLICY_VERSION, "state_hash": "", "updated_at": now}


def _state_hash_locked(book_id: int) -> str:
    rows = db.query(
        "SELECT character_id, canonical_name, aliases_json, status, merged_into, voice_status, registry_revision "
        "FROM character_registry WHERE book_id=? ORDER BY character_id", (book_id,))
    aliases = db.query(
        "SELECT surface, normalized_surface, relation_type, scope_type, scope_key, target_character_id, "
        "candidates_json, status, confidence, registry_revision FROM character_aliases WHERE book_id=? "
        "ORDER BY id", (book_id,))
    payload = json.dumps({"characters": rows, "aliases": aliases}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bump_locked(book_id: int) -> tuple[int, str, str]:
    meta = _ensure_meta_locked(book_id)
    revision = int(meta["revision"]) + 1
    state_hash = _state_hash_locked(book_id)
    now = db.ts()
    db._conn().execute(
        "UPDATE character_registry_meta SET revision=?, state_hash=?, updated_at=? WHERE book_id=?",
        (revision, state_hash, now, book_id),
    )
    return revision, state_hash, meta["state_hash"] or ""


def _check_revision_locked(book_id: int, expected_revision):
    meta = _ensure_meta_locked(book_id)
    if expected_revision is not None and int(expected_revision) != int(meta["revision"]):
        raise RegistryConflictError(
            f"registry revision 已更新：expected={expected_revision}, current={meta['revision']}"
        )
    return meta


def _event_locked(book_id: int, event_type: str, *, source=None, target=None, payload=None,
                  actor_id=None, expected_revision=None, resulting_revision: int,
                  before_hash: str, after_hash: str) -> dict:
    event_id = "cre_" + uuid.uuid4().hex
    now = db.ts()
    db._conn().execute(
        "INSERT INTO character_resolution_events(event_id, book_id, event_type, source_character_id, "
        "target_character_id, payload_json, actor_id, expected_revision, resulting_revision, policy_version, "
        "before_state_hash, after_state_hash, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, book_id, event_type, source, target,
         json.dumps(payload or {}, ensure_ascii=False), actor_id, expected_revision,
         resulting_revision, POLICY_VERSION, before_hash, after_hash, now),
    )
    return {"eventId": event_id, "eventType": event_type, "resultingRevision": resulting_revision,
            "afterStateHash": after_hash, "createdAt": now}


def get_registry(bid: str) -> dict:
    book = _book(bid)
    meta = db.query_one("SELECT * FROM character_registry_meta WHERE book_id=?", (book["id"],))
    rows = db.query("SELECT * FROM character_registry WHERE book_id=? ORDER BY canonical_name, character_id", (book["id"],))
    aliases = db.query("SELECT * FROM character_aliases WHERE book_id=? ORDER BY id", (book["id"],))
    for row in rows:
        row["aliases"] = _json(row.pop("aliases_json", "[]"), [])
    for row in aliases:
        row["candidates"] = _json(row.pop("candidates_json", "[]"), [])
        row["evidence"] = _json(row.pop("evidence_json", "[]"), [])
    return {"bookId": bid, "revision": int((meta or {}).get("revision", 0)),
            "policyVersion": (meta or {}).get("policy_version", POLICY_VERSION),
            "stateHash": (meta or {}).get("state_hash", ""), "characters": rows, "aliases": aliases}


def list_history(bid: str) -> list[dict]:
    book = _book(bid)
    rows = db.query("SELECT * FROM character_resolution_events WHERE book_id=? ORDER BY id", (book["id"],))
    for row in rows:
        row["payload"] = _json(row.pop("payload_json", "{}"), {})
    return rows


def list_proposals(bid: str) -> list[dict]:
    book = _book(bid)
    rows = db.query("SELECT * FROM character_resolution_proposals WHERE book_id=? ORDER BY id DESC", (book["id"],))
    for row in rows:
        row["evidence"] = _json(row.pop("evidence_json", "[]"), [])
    return rows


def list_voice_conflicts(bid: str) -> list[dict]:
    book = _book(bid)
    rows = db.query("SELECT * FROM character_voice_conflicts WHERE book_id=? ORDER BY id DESC", (book["id"],))
    for row in rows:
        row["voices"] = _json(row.pop("voices_json", "[]"), [])
    return rows


def resolve_surface(bid: str, surface: str, *, scope_type="chapter", scope_key="") -> dict:
    """Return candidates without mutating canonical identity."""
    book = _book(bid)
    normalized = normalize_surface(surface)
    candidates = db.query(
        "SELECT target_character_id, status, confidence, evidence_json, scope_type, scope_key FROM character_aliases "
        "WHERE book_id=? AND normalized_surface=? ORDER BY CASE scope_type WHEN 'scene' THEN 0 WHEN 'chapter' THEN 1 "
        "WHEN 'book' THEN 2 ELSE 3 END, id DESC", (book["id"], normalized),
    )
    exact = [x for x in candidates if x["status"] == "accepted" and x.get("target_character_id") and
             (x["scope_type"] == "book" or (x["scope_type"] == scope_type and x["scope_key"] == scope_key))]
    if surface.strip() in GENERIC_SURFACES:
        exact = [x for x in exact if x["scope_type"] != "book"]
    return {
        "surface": surface,
        "scope": {"type": scope_type, "key": scope_key},
        "status": "accepted" if len({x["target_character_id"] for x in exact}) == 1 else ("ambiguous" if candidates else "unresolved"),
        "candidates": [{"characterId": x.get("target_character_id"), "confidence": x.get("confidence", 0),
                        "scopeType": x.get("scope_type"), "scopeKey": x.get("scope_key"),
                        "evidence": _json(x.get("evidence_json"), [])} for x in candidates],
    }


def assign_alias(bid: str, *, surface: str, target_character_id: str | None, scope_type="book",
                 scope_key="", relation_type="alias", confidence=1.0, evidence=None,
                 actor_id=None, expected_revision=None) -> dict:
    book = _book(bid)
    if target_character_id and not is_valid_character_id(target_character_id):
        raise CharacterResolutionError("alias target character_id namespace 不合法")
    if scope_type not in ("scene", "chapter", "book", "unresolved"):
        raise CharacterResolutionError("alias scope 不合法")
    if scope_type == "book" and surface.strip() in GENERIC_SURFACES:
        raise CharacterResolutionError("generic alias 不得直接建立 book-global mapping")
    with db.WRITE_LOCK:
        meta = _check_revision_locked(book["id"], expected_revision)
        if target_character_id and not db.query_one("SELECT 1 FROM character_registry WHERE book_id=? AND character_id=?",
                                                     (book["id"], target_character_id)):
            raise CharacterResolutionError("找不到 alias target identity")
        before = meta["state_hash"]
        revision = int(meta["revision"]) + 1
        status = "accepted" if target_character_id and scope_type != "unresolved" else "unresolved"
        _upsert_alias_locked(book["id"], surface, target_character_id, scope_type=scope_type, scope_key=scope_key,
                             relation_type=relation_type, status=status, confidence=confidence, evidence=evidence,
                             actor_id=actor_id, revision=revision)
        db._conn().execute("UPDATE character_registry_meta SET revision=?,updated_at=? WHERE book_id=?",
                           (revision, db.ts(), book["id"]))
        after = _state_hash_locked(book["id"])
        db._conn().execute("UPDATE character_registry_meta SET state_hash=? WHERE book_id=?", (after, book["id"]))
        event = _event_locked(book["id"], "alias_assigned", target=target_character_id,
                              payload={"surface": surface, "scopeType": scope_type, "scopeKey": scope_key,
                                       "relationType": relation_type, "evidence": evidence or []}, actor_id=actor_id,
                              expected_revision=expected_revision, resulting_revision=revision,
                              before_hash=before, after_hash=after)
        db._conn().commit()
    return {"surface": surface, "targetCharacterId": target_character_id, "status": status,
            "registryRevision": revision, "event": event}


def _insert_character_locked(book_id: int, character_id: str, name: str, aliases=None, *, status="provisional", actor_id=None):
    if not is_valid_character_id(character_id):
        raise CharacterResolutionError("character_id namespace 不合法")
    existing = db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book_id, character_id))
    if existing:
        return existing, False
    now = db.ts()
    db._conn().execute(
        "INSERT INTO character_registry(book_id, character_id, canonical_name, aliases_json, status, "
        "registry_revision, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (book_id, character_id, name.strip() or character_id, json.dumps(aliases or [], ensure_ascii=False),
         status, 0, actor_id, now, now),
    )
    return db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book_id, character_id)), True


def _upsert_alias_locked(book_id: int, surface: str, target: str | None, *, scope_type="book", scope_key="",
                         relation_type="alias", status="accepted", confidence=1.0, evidence=None,
                         actor_id=None, revision=0):
    if not surface:
        return
    normalized = normalize_surface(surface)
    if surface.strip() in GENERIC_SURFACES and scope_type == "book":
        scope_type = "unresolved"
        status = "unresolved"
    existing = db.query_one(
        "SELECT id FROM character_aliases WHERE book_id=? AND normalized_surface=? AND scope_type=? AND scope_key=? "
        "AND COALESCE(target_character_id,'')=COALESCE(?, '')",
        (book_id, normalized, scope_type, scope_key, target),
    )
    now = db.ts()
    values = (book_id, surface.strip(), normalized, relation_type, scope_type, scope_key, target,
              json.dumps([], ensure_ascii=False), status, max(0.0, min(1.0, float(confidence))),
              json.dumps(evidence or [], ensure_ascii=False), POLICY_VERSION, revision, now, now)
    if existing:
        db._conn().execute(
            "UPDATE character_aliases SET status=?, confidence=?, evidence_json=?, registry_revision=?, updated_at=? WHERE id=?",
            (status, values[9], values[10], revision, now, existing["id"]),
        )
    else:
        db._conn().execute(
            "INSERT INTO character_aliases(book_id,surface,normalized_surface,relation_type,scope_type,scope_key,"
            "target_character_id,candidates_json,status,confidence,evidence_json,policy_version,registry_revision,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)


def register_ready_artifact(bid: str, analysis_id: int, artifact: dict, actor_id=None) -> dict:
    """Register v3 characters and segment mappings after the artifact is already ready."""
    book = _book(bid)
    with db.WRITE_LOCK:
        con = db._conn()
        _ensure_meta_locked(book["id"])
        changed = False
        source_to_resolved = {}
        for character in artifact.get("characters", []):
            cid = character.get("character_id")
            name = str(character.get("canonical_name") or cid or "").strip()
            if not cid or not name:
                continue
            existing_alias = db.query_one(
                "SELECT target_character_id FROM character_aliases WHERE book_id=? AND normalized_surface=? "
                "AND status='accepted' AND target_character_id IS NOT NULL AND (scope_type='book' OR scope_type='chapter') "
                "ORDER BY CASE scope_type WHEN 'chapter' THEN 0 ELSE 1 END, id DESC LIMIT 1",
                (book["id"], normalize_surface(name)),
            )
            resolved = existing_alias["target_character_id"] if existing_alias and name not in GENERIC_SURFACES else cid
            source_to_resolved[cid] = resolved
            _, inserted = _insert_character_locked(book["id"], resolved, name, character.get("aliases"), actor_id=actor_id)
            changed = changed or inserted
        if changed:
            revision, _, _ = _bump_locked(book["id"])
            for character in artifact.get("characters", []):
                cid = source_to_resolved.get(character.get("character_id"), character.get("character_id"))
                if not cid:
                    continue
                _upsert_alias_locked(book["id"], character.get("canonical_name", ""), cid, revision=revision)
                for alias in character.get("aliases") or []:
                    _upsert_alias_locked(book["id"], str(alias), cid, revision=revision,
                                         scope_type="chapter" if str(alias).strip() in GENERIC_SURFACES else "book")
            revision, state_hash, before_hash = _bump_locked(book["id"])
        else:
            meta = _ensure_meta_locked(book["id"])
            revision, state_hash, before_hash = int(meta["revision"]), meta["state_hash"], meta["state_hash"]
        for seg in artifact.get("segments", []):
            source_sid = str(seg.get("speaker_id") or "speaker:unresolved")
            resolved = source_to_resolved.get(source_sid, source_sid) if source_sid != "speaker:unresolved" else None
            status = "accepted" if resolved else "unresolved"
            db._conn().execute(
                "INSERT INTO character_segment_resolutions(book_id,analysis_id,segment_id,source_speaker_id,"
                "resolved_speaker_id,status,candidates_json,evidence_json,confidence,registry_revision) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(analysis_id,segment_id) DO UPDATE SET resolved_speaker_id=excluded.resolved_speaker_id,"
                "status=excluded.status,candidates_json=excluded.candidates_json,evidence_json=excluded.evidence_json,"
                "confidence=excluded.confidence,registry_revision=excluded.registry_revision",
                (book["id"], analysis_id, str(seg.get("segment_id")), source_sid, resolved, status,
                 json.dumps((seg.get("attribution") or {}).get("candidates", []), ensure_ascii=False),
                 json.dumps((seg.get("attribution") or {}).get("evidence", []), ensure_ascii=False),
                 float((seg.get("attribution") or {}).get("confidence", 1.0 if resolved else 0.0)), revision),
            )
        con.commit()
    return get_registry(bid)


def _voice_conflict_locked(book_id: int, source: dict, target: dict, revision: int):
    book = db.get_book_by_rowid(book_id)
    voices = _json(book.get("voices"), {})
    source_voice = voices.get(source["canonical_name"]) or ""
    target_voice = voices.get(target["canonical_name"]) or ""
    if source_voice and target_voice and source_voice != target_voice:
        now = db.ts()
        cur = db._conn().execute(
            "INSERT INTO character_voice_conflicts(book_id,character_id,voices_json,status,registry_revision,created_at) VALUES(?,?,?,?,?,?)",
            (book_id, target["character_id"], json.dumps([
                {"voice": source_voice, "source": source["character_id"], "name": source["canonical_name"]},
                {"voice": target_voice, "source": target["character_id"], "name": target["canonical_name"]},
            ], ensure_ascii=False), "open", revision, now),
        )
        return cur.lastrowid
    return None


def propose_reconciliation(bid: str, *, source_character_id: str, target_character_id: str,
                           confidence: float, evidence=None, reason="", actor_id=None,
                           expected_revision=None) -> dict:
    book = _book(bid)
    confidence = max(0.0, min(1.0, float(confidence)))
    with db.WRITE_LOCK:
        meta = _check_revision_locked(book["id"], expected_revision)
        if not is_valid_character_id(source_character_id) or not is_valid_character_id(target_character_id):
            raise CharacterResolutionError("proposal identity namespace 不合法")
        now = db.ts()
        cur = db._conn().execute(
            "INSERT INTO character_resolution_proposals(book_id,source_character_id,target_character_id,confidence,"
            "evidence_json,reason,status,policy_version,registry_revision,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (book["id"], source_character_id, target_character_id, confidence,
             json.dumps(evidence or [], ensure_ascii=False), reason[:1000], "pending", POLICY_VERSION,
             meta["revision"], actor_id, now, now),
        )
        con = db._conn()
        con.commit()
        proposal_id = cur.lastrowid
    return {"proposalId": proposal_id, "status": "pending", "confidence": confidence,
            "registryRevision": int(meta["revision"]), "aiPermanentMerge": False}


def _proposal_safe_locked(book_id: int, proposal: dict) -> tuple[bool, list[str]]:
    reasons = []
    confidence = float(proposal["confidence"])
    if confidence < AUTO_ACCEPT_THRESHOLD:
        reasons.append("confidence below auto-accept threshold")
    evidence = _json(proposal.get("evidence_json"), [])
    text = json.dumps(evidence, ensure_ascii=False).casefold()
    if any(word in text for word in ("contradiction", "矛盾", "different", "不同")):
        reasons.append("contradiction evidence")
    source = db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book_id, proposal["source_character_id"]))
    target = db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book_id, proposal["target_character_id"]))
    if not source or not target or source["status"] not in ("provisional", "active") or target["status"] != "active":
        reasons.append("identity lifecycle is not mergeable")
    for alias in db.query("SELECT * FROM character_aliases WHERE book_id=? AND target_character_id IN (?,?)", (book_id, proposal["source_character_id"], proposal["target_character_id"])):
        if alias["surface"].strip() in GENERIC_SURFACES or alias["relation_type"] in ("title", "relationship", "role_descriptor"):
            reasons.append("generic/title/relationship alias")
            break
    voices = db.query("SELECT 1 FROM character_voice_conflicts WHERE book_id=? AND character_id=? AND status='open'",
                      (book_id, proposal["target_character_id"]))
    if voices:
        reasons.append("unresolved voice conflict")
    return not reasons, reasons


def accept_proposal(bid: str, proposal_id: int, *, actor_id=None, expected_revision=None,
                    force_manual=False) -> dict:
    book = _book(bid)
    with db.WRITE_LOCK:
        proposal = db.query_one("SELECT * FROM character_resolution_proposals WHERE id=? AND book_id=?", (proposal_id, book["id"]))
        if not proposal:
            raise CharacterResolutionError("找不到 reconciliation proposal")
        meta = _check_revision_locked(book["id"], expected_revision if expected_revision is not None else proposal["registry_revision"])
        safe, reasons = _proposal_safe_locked(book["id"], proposal)
        if not safe and not force_manual:
            status = "pending" if float(proposal["confidence"]) >= PENDING_THRESHOLD else "unresolved"
            db._conn().execute("UPDATE character_resolution_proposals SET status=?,updated_at=? WHERE id=?", (status, db.ts(), proposal_id))
            db._conn().commit()
            return {"proposalId": proposal_id, "status": status, "reasons": reasons, "merged": False}
    result = merge_characters(bid, source_character_id=proposal["source_character_id"],
                              target_character_id=proposal["target_character_id"], actor_id=actor_id,
                              expected_revision=meta["revision"], evidence=_json(proposal.get("evidence_json"), []),
                              reason=proposal.get("reason", ""))
    db.update_character_proposal(proposal_id, {"status": "accepted"})
    return {"proposalId": proposal_id, "status": "accepted", "merged": True, **result}


def merge_characters(bid: str, *, source_character_id: str, target_character_id: str,
                     actor_id=None, expected_revision=None, evidence=None, reason="") -> dict:
    book = _book(bid)
    if source_character_id == target_character_id:
        raise CharacterResolutionError("不可 merge 相同 identity")
    with db.WRITE_LOCK:
        meta = _check_revision_locked(book["id"], expected_revision)
        source = db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book["id"], source_character_id))
        target = db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book["id"], target_character_id))
        if not source or not target:
            raise CharacterResolutionError("找不到 merge identity")
        if source["status"] == "merged" or target["status"] != "active":
            raise CharacterResolutionError("identity lifecycle 不允許 merge")
        before = meta["state_hash"] or _state_hash_locked(book["id"])
        revision = int(meta["revision"]) + 1
        db._conn().execute("UPDATE character_registry SET status='merged',merged_into=?,registry_revision=?,updated_at=? WHERE book_id=? AND character_id=?",
                           (target_character_id, revision, db.ts(), book["id"], source_character_id))
        db._conn().execute("UPDATE character_aliases SET target_character_id=?,registry_revision=?,updated_at=? WHERE book_id=? AND target_character_id=?",
                           (target_character_id, revision, db.ts(), book["id"], source_character_id))
        db._conn().execute("UPDATE character_segment_resolutions SET resolved_speaker_id=?,registry_revision=? WHERE book_id=? AND resolved_speaker_id=?",
                           (target_character_id, revision, book["id"], source_character_id))
        db._conn().execute("UPDATE character_registry_meta SET revision=?,updated_at=? WHERE book_id=?",
                           (revision, db.ts(), book["id"]))
        conflict_id = _voice_conflict_locked(book["id"], source, target, revision)
        if conflict_id:
            db._conn().execute("UPDATE character_registry SET voice_status='needs_manual_voice_resolution',updated_at=? "
                               "WHERE book_id=? AND character_id=?", (db.ts(), book["id"], target_character_id))
        after = _state_hash_locked(book["id"])
        db._conn().execute("UPDATE character_registry_meta SET state_hash=? WHERE book_id=?", (after, book["id"]))
        event = _event_locked(book["id"], "merge", source=source_character_id, target=target_character_id,
                              payload={"evidence": evidence or [], "reason": reason, "voiceConflictId": conflict_id},
                              actor_id=actor_id, expected_revision=expected_revision, resulting_revision=revision,
                              before_hash=before, after_hash=after)
        db._conn().commit()
    return {"sourceCharacterId": source_character_id, "targetCharacterId": target_character_id,
            "registryRevision": revision, "voiceConflictId": conflict_id, "event": event}


def split_character(bid: str, *, source_character_id: str, mappings: list[dict], actor_id=None,
                    expected_revision=None, reason="") -> dict:
    book = _book(bid)
    if not mappings:
        raise CharacterResolutionError("split 需要 mappings")
    with db.WRITE_LOCK:
        meta = _check_revision_locked(book["id"], expected_revision)
        source = db.query_one("SELECT * FROM character_registry WHERE book_id=? AND character_id=?", (book["id"], source_character_id))
        if not source or source["status"] not in ("active", "merged"):
            raise CharacterResolutionError("identity lifecycle 不允許 split")
        target_ids = sorted({str(item.get("targetCharacterId") or "") for item in mappings})
        if any(not is_valid_character_id(cid) for cid in target_ids):
            raise CharacterResolutionError("split target character_id namespace 不合法")
        revision = int(meta["revision"]) + 1
        created = []
        for cid in target_ids:
            item = next(x for x in mappings if str(x.get("targetCharacterId")) == cid)
            row, inserted = _insert_character_locked(book["id"], cid, str(item.get("canonicalName") or cid),
                                                     item.get("aliases") or [], status="active", actor_id=actor_id)
            created.append(cid)
        source_book = db.get_book_by_rowid(book["id"])
        source_voices = _json(source_book.get("voices"), {})
        if source_voices.get(source["canonical_name"]):
            db._conn().execute(
                "UPDATE character_registry SET voice_status='voice_pending_confirmation',updated_at=? WHERE book_id=? AND character_id IN (%s)"
                % ",".join("?" for _ in target_ids), (db.ts(), book["id"], *target_ids))
        for item in mappings:
            if not item.get("segmentId") or not item.get("analysisId"):
                raise CharacterResolutionError("split mapping 缺少 analysisId/segmentId")
            db._conn().execute(
                "UPDATE character_segment_resolutions SET resolved_speaker_id=?,status='accepted',registry_revision=? "
                "WHERE book_id=? AND analysis_id=? AND segment_id=? AND source_speaker_id=?",
                (str(item["targetCharacterId"]), revision, book["id"], int(item["analysisId"]), str(item["segmentId"]), source_character_id),
            )
        db._conn().execute("UPDATE character_registry SET status='split',registry_revision=?,updated_at=? WHERE book_id=? AND character_id=?",
                           (revision, db.ts(), book["id"], source_character_id))
        db._conn().execute("UPDATE character_registry_meta SET revision=?,updated_at=? WHERE book_id=?", (revision, db.ts(), book["id"]))
        before = meta["state_hash"] or _state_hash_locked(book["id"])
        after = _state_hash_locked(book["id"])
        db._conn().execute("UPDATE character_registry_meta SET state_hash=? WHERE book_id=?", (after, book["id"]))
        event = _event_locked(book["id"], "split", source=source_character_id,
                              payload={"mappings": mappings, "createdCharacterIds": created, "reason": reason,
                                       "voicePolicy": "no_clone_pending_confirmation"}, actor_id=actor_id,
                              expected_revision=expected_revision, resulting_revision=revision,
                              before_hash=before, after_hash=after)
        db._conn().commit()
    return {"sourceCharacterId": source_character_id, "createdCharacterIds": created,
            "registryRevision": revision, "event": event, "voicePolicy": "pending_confirmation"}


def resolve_voice_conflict(bid: str, conflict_id: int, *, selected_voice: str, actor_id=None,
                           reason="", expected_revision=None) -> dict:
    book = _book(bid)
    with db.WRITE_LOCK:
        conflict = db.query_one("SELECT * FROM character_voice_conflicts WHERE id=? AND book_id=?", (conflict_id, book["id"]))
        if not conflict or conflict["status"] != "open":
            raise CharacterResolutionError("找不到未解決 voice conflict")
        meta = _check_revision_locked(book["id"], expected_revision)
        candidates = _json(conflict["voices_json"], [])
        allowed = {x.get("voice") for x in candidates if isinstance(x, dict)}
        if selected_voice not in allowed:
            raise CharacterResolutionError("selected voice 不在 conflict candidates")
        revision = int(meta["revision"]) + 1
        db._conn().execute("UPDATE character_voice_conflicts SET status='resolved',selected_voice=?,registry_revision=?,resolved_at=? WHERE id=?",
                           (selected_voice, revision, db.ts(), conflict_id))
        target = db.query_one("SELECT canonical_name FROM character_registry WHERE book_id=? AND character_id=?",
                              (book["id"], conflict["character_id"]))
        book_row = db.get_book_by_rowid(book["id"])
        voices = _json(book_row.get("voices"), {})
        if target:
            voices[target["canonical_name"]] = selected_voice
            db._conn().execute("UPDATE books SET voices=?,updated_at=? WHERE id=?", (json.dumps(voices, ensure_ascii=False), db.ts(), book["id"]))
            db._conn().execute("UPDATE character_registry SET voice_status='ready',updated_at=? WHERE book_id=? AND character_id=?",
                               (db.ts(), book["id"], conflict["character_id"]))
        db._conn().execute("UPDATE character_registry_meta SET revision=?,updated_at=? WHERE book_id=?", (revision, db.ts(), book["id"]))
        before = meta["state_hash"] or _state_hash_locked(book["id"])
        after = _state_hash_locked(book["id"])
        db._conn().execute("UPDATE character_registry_meta SET state_hash=? WHERE book_id=?", (after, book["id"]))
        event = _event_locked(book["id"], "voice_resolved", target=conflict["character_id"],
                              payload={"conflictId": conflict_id, "selectedVoice": selected_voice,
                                       "rejectedVoices": [v for v in allowed if v != selected_voice], "reason": reason},
                              actor_id=actor_id, expected_revision=expected_revision, resulting_revision=revision,
                              before_hash=before, after_hash=after)
        db._conn().commit()
    return {"conflictId": conflict_id, "selectedVoice": selected_voice, "registryRevision": revision, "event": event}


def migrate_book(bid: str, *, apply=False, actor_id=None) -> dict:
    """逐書 migration；dry-run 不寫 registry、projection 或 event。"""
    book = _book(bid)
    chapters = db.list_chapters(book["id"])
    ready = []
    for chapter in chapters:
        record = db.get_ready_chapter_analysis(chapter["id"], chapter["text_hash"])
        if record and record.get("artifact_path"):
            ready.append((chapter, record))
    report = {"bid": bid, "mode": "apply" if apply else "dry-run", "readyArtifacts": len(ready),
              "provisionalCharacters": 0, "unresolved": 0, "conflicts": [], "written": False, "snapshotId": None}
    legacy_info = _json(book.get("speaker_info"), {})
    legacy_voices = _json(book.get("voices"), {})
    legacy_names = {str(name) for name in set(legacy_info) | set(legacy_voices)
                    if str(name).strip() and str(name) != "_english"}
    if not apply:
        names = set()
        for chapter, record in ready:
            try:
                import os
                with open(os.path.join(settings.ROOT_DIR, record["artifact_path"]), encoding="utf-8") as f:
                    artifact = json.load(f)
                names.update(x.get("canonical_name") for x in artifact.get("characters", []) if x.get("canonical_name"))
            except (OSError, ValueError):
                report["conflicts"].append({"chapter": chapter["seq"], "reason": "artifact unavailable"})
        report["provisionalCharacters"] = len(names | legacy_names)
        return report
    with db.WRITE_LOCK:
        report["snapshotId"] = db._conn().execute(
            "INSERT INTO character_migration_snapshots(book_id,registry_json,aliases_json,segment_resolutions_json,voices_json,"
            "speaker_info_json,speaker_chapters_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (book["id"], json.dumps(db.query("SELECT * FROM character_registry WHERE book_id=?", (book["id"],)), ensure_ascii=False),
             json.dumps(db.query("SELECT * FROM character_aliases WHERE book_id=?", (book["id"],)), ensure_ascii=False),
             json.dumps(db.query("SELECT * FROM character_segment_resolutions WHERE book_id=?", (book["id"],)), ensure_ascii=False),
             book.get("voices") or "{}", book.get("speaker_info") or "{}", book.get("speaker_chapters") or "{}", db.ts()),
        ).lastrowid
        db._conn().commit()
    for chapter, record in ready:
        try:
            import os
            with open(os.path.join(settings.ROOT_DIR, record["artifact_path"]), encoding="utf-8") as f:
                artifact = json.load(f)
            register_ready_artifact(bid, record["id"], artifact, actor_id=actor_id)
            from . import analysis as analysis_service
            analysis_service.retry_roster_projection(record["id"])
            report["written"] = True
        except (OSError, ValueError, CharacterResolutionError) as error:
            report["conflicts"].append({"chapter": chapter["seq"], "reason": str(error)})
    if legacy_names:
        book_row = _book(bid)
        with db.WRITE_LOCK:
            meta = _ensure_meta_locked(book_row["id"])
            before = meta["state_hash"]
            inserted = []
            for name in sorted(legacy_names):
                cid = _new_character_id(book_row["id"], name)
                _, did_insert = _insert_character_locked(book_row["id"], cid, name, [], status="provisional", actor_id=actor_id)
                if did_insert:
                    inserted.append(cid)
            if inserted:
                revision, after, _ = _bump_locked(book_row["id"])
                _event_locked(book_row["id"], "migration", payload={"bid": bid, "characterIds": inserted,
                             "legacyNames": sorted(legacy_names), "dryRun": False}, actor_id=actor_id,
                              resulting_revision=revision, before_hash=before, after_hash=after)
                db._conn().commit()
                report["written"] = True
    return report


def rollback_migration(bid: str, snapshot_id: int, *, actor_id=None) -> dict:
    """Restore a per-book migration snapshot; ready artifacts are untouched."""
    book = _book(bid)
    with db.WRITE_LOCK:
        snap = db.query_one("SELECT * FROM character_migration_snapshots WHERE id=? AND book_id=?", (snapshot_id, book["id"]))
        if not snap or snap.get("restored_at"):
            raise CharacterResolutionError("找不到可 rollback 的 migration snapshot")
        con = db._conn()
        con.execute("DELETE FROM character_segment_resolutions WHERE book_id=?", (book["id"],))
        con.execute("DELETE FROM character_aliases WHERE book_id=?", (book["id"],))
        con.execute("DELETE FROM character_registry WHERE book_id=?", (book["id"],))
        for row in _json(snap["registry_json"], []):
            cols = ["book_id", "character_id", "canonical_name", "aliases_json", "status", "merged_into",
                    "voice_status", "registry_revision", "created_by", "created_at", "updated_at"]
            values = [book["id"]] + [row.get(col) for col in cols[1:]]
            con.execute("INSERT INTO character_registry(%s) VALUES(%s)" % (",".join(cols), ",".join("?" for _ in cols)), values)
        for row in _json(snap["aliases_json"], []):
            cols = ["book_id", "surface", "normalized_surface", "relation_type", "scope_type", "scope_key",
                    "target_character_id", "candidates_json", "status", "confidence", "evidence_json",
                    "policy_version", "registry_revision", "created_at", "updated_at"]
            values = [book["id"]] + [row.get(col) for col in cols[1:]]
            con.execute("INSERT INTO character_aliases(%s) VALUES(%s)" % (",".join(cols), ",".join("?" for _ in cols)), values)
        for row in _json(snap["segment_resolutions_json"], []):
            cols = ["book_id", "analysis_id", "segment_id", "source_speaker_id", "resolved_speaker_id", "status",
                    "candidates_json", "evidence_json", "confidence", "registry_revision"]
            values = [book["id"]] + [row.get(col) for col in cols[1:]]
            con.execute("INSERT INTO character_segment_resolutions(%s) VALUES(%s)" % (",".join(cols), ",".join("?" for _ in cols)), values)
        con.execute("UPDATE books SET voices=?,speaker_info=?,speaker_chapters=?,updated_at=? WHERE id=?",
                    (snap["voices_json"], snap["speaker_info_json"], snap["speaker_chapters_json"], db.ts(), book["id"]))
        con.execute("UPDATE character_migration_snapshots SET restored_at=? WHERE id=?", (db.ts(), snapshot_id))
        meta = _ensure_meta_locked(book["id"])
        before = meta["state_hash"]
        revision, after, _ = _bump_locked(book["id"])
        _event_locked(book["id"], "migration_rollback", payload={"snapshotId": snapshot_id}, actor_id=actor_id,
                      resulting_revision=revision, before_hash=before, after_hash=after)
        con.commit()
    return {"bid": bid, "snapshotId": snapshot_id, "rolledBack": True, "registryRevision": revision}


def replay(bid: str) -> dict:
    """回傳 append-only event projection；不呼叫 AI、不改 ready artifact。"""
    registry = get_registry(bid)
    events = list_history(bid)
    return {"registry": registry, "events": events, "replayable": True}
