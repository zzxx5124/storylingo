"""遷移：storage/books/{bid}（meta.json + source.txt）→ SQLite（冪等，可重跑）。"""
import json
import logging
import os
import secrets
import uuid

from .. import db, settings, storage as st
from .. import security
from . import profile as profile_service

_log = logging.getLogger("migration")


def ensure_admin_user() -> int:
    admins = db.query("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    if admins:
        return admins[0]["id"]
    # 無 admin：允許以「dev」owner 歸屬（先建 reader 名為 admin，但避免與 username 撞，用 uuid）
    import secrets
    name = settings.ADMIN_USER or "admin"
    if db.get_user_by_bname(name):
        uid = db.get_user_by_bname(name)["id"]
        return uid
    pw = "!" + secrets.token_hex(8)  # 隨機密碼（遷移專用，無法登入）
    uid = db.create_user(name, security.hash_password(pw), "admin")
    return uid


def migrate_book_dir(bid: str, owner_id: int) -> dict:
    """遷移單本書。回傳報告 dict。"""
    report = {"bid": bid, "chapters": 0, "ready": 0, "analyzed": 0, "errors": []}
    meta_path = st.meta_path(bid)
    if not os.path.exists(meta_path):
        report["errors"].append("缺 meta.json")
        return report
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        report["errors"].append(f"meta 讀取失敗：{e}")
        return report
    if db.get_book_row(bid):
        report["errors"].append("已存在，跳過")
        return report
    src = ""
    src_path = st.source_path(bid)
    if os.path.exists(src_path):
        try:
            with open(src_path, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception as e:
            report["errors"].append(f"source 讀取失敗：{e}")
    chapters = meta.get("chapters") or []
    new_bid = db.create_book(
        owner_id=owner_id,
        bid=bid,
        title=meta.get("title") or bid,
        category=meta.get("category") or settings.CAT_VOCAB,
        vocab_level=meta.get("vocabLevel") or "AUTO",
        categories=json.dumps(meta.get("categories") or [meta.get("category") or settings.CAT_VOCAB]),
        voices=json.dumps(meta.get("voices") or {}),
        voice_prefs=json.dumps(meta.get("voicePrefs") or {}),
        speaker_info=json.dumps(meta.get("speakerInfo") or {}),
        speaker_chapters=json.dumps(meta.get("speakerChapters") or {}),
        settings=json.dumps(meta.get("settings") or {}),
         cover_path=meta.get("coverImage") or "",
         legacy_author_name=meta.get("authorName") or meta.get("author") or "",
         serial="連載",
        status="approved",
        published_at=meta.get("created") or db.ts(),
        created_at=meta.get("created") or db.ts(),
        chars=meta.get("totalChars") or 0,
        synopsis="",
        tags="",
        category_id=None,
        _auto_author_profile=False,
    )
    row = db.get_book_row(new_bid)
    total = 0
    for i, c in enumerate(chapters):
        text = src[c["start"]:c["end"]] if c.get("start") is not None else ""
        if not text:
            report["errors"].append(f"ch{c['seq']} 無文字")
        import hashlib
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""
        analyzed = c.get("status") == "analyzed" and os.path.exists(st.chapter_json_path(bid, c["seq"]))
        ready = c.get("audio") == "ready" and os.path.exists(st.chapter_audio_path(bid, c["seq"]))
        db.execute(
            "INSERT INTO chapters(book_id, chapter_key, seq, title, text, chars, status, audio, error, text_hash, "
            "analyze_path, audio_path, timing_path, generated_at, analysed_hash) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], f"ck-{uuid.uuid4().hex[:12]}", i, c.get("title") or "", text, len(text),
             "analyzed" if analyzed else "pending",
             "ready" if ready else ("error" if c.get("audio") == "error" else "none"),
             c.get("error") or "",
             h,
             f"storage/books/{bid}/chapters/{c['seq']:04d}.json",
             f"storage/books/{bid}/audio/{c['seq']:04d}.mp3",
             f"storage/books/{bid}/audio/{c['seq']:04d}.timing.json",
             c.get("analyzedAt") if analyzed else None,
             h if analyzed else "",
            ))
        total += len(text)
        if analyzed:
            report["analyzed"] += 1
        if ready:
            report["ready"] += 1
        report["chapters"] += 1
    db.update_book(new_bid, {"chars": total})
    return report


def run_migrate() -> dict:
    """跑全部遷移，回傳報告。"""
    os.makedirs(settings.BOOKS_DIR, exist_ok=True)
    report = {"total_dirs": 0, "migrated": 0, "skipped": 0, "books": []}
    owner_id = None
    for name in sorted(os.listdir(settings.BOOKS_DIR)):
        p = os.path.join(settings.BOOKS_DIR, name)
        if not (os.path.isdir(p) and os.path.exists(os.path.join(p, "meta.json"))):
            continue
        report["total_dirs"] += 1
        if owner_id is None:
            owner_id = migrate_owner()
        r = migrate_book_dir(name, owner_id)
        report["books"].append(r)
        if any("已存在" in e for e in (r.get("errors") or [])):
            report["skipped"] += 1
        else:
            report["migrated"] += 1
    return report


def migrate_owner() -> int:
    """回傳可當歷史書 owner 的 admin id（不存在則建立）。"""
    admins = db.query("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    if admins:
        return admins[0]["id"]
    existing = db.get_user_by_bname(settings.ADMIN_USER or "admin")
    if existing:
        db.update_user_role(existing["id"], "admin")
        return existing["id"]
    import secrets
    pw = "!" + secrets.token_hex(8)
    return db.create_user(settings.ADMIN_USER or "admin", security.hash_password(pw), "admin")


def identity_profile_migration(*, apply: bool = False) -> dict:
    if apply:
        with db.atomic():
            return _identity_profile_migration(apply=True)
    return _identity_profile_migration(apply=False)


def _identity_profile_migration(*, apply: bool = False) -> dict:
    """Identity backfill：預設只盤點，只有明確 ``apply`` 才寫入。

    此 migration 不合併帳號、不改寫 owner_id，也不把不明作品指派給第一位管理員。
    """
    def table_exists(name: str) -> bool:
        return bool(db.query_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    users = db.query("SELECT * FROM users ORDER BY id ASC") if table_exists("users") else []
    applications = (db.query("SELECT * FROM author_applications WHERE status='approved' ORDER BY id ASC")
                    if table_exists("author_applications") else [])
    books = db.query("SELECT * FROM books ORDER BY id ASC") if table_exists("books") else []
    has_public_profiles = table_exists("public_profiles")
    has_author_profiles = table_exists("author_profiles")
    report = {
        "dryRun": not apply,
        "userCount": len(users),
        "authorCandidateCount": len(applications),
        "authorCandidates": [],
        "emailConflicts": [],
        "profileSlugConflicts": [],
        "ambiguousMappings": [],
        "legacyBookAttributionConflicts": [],
        "legacyAttributionBackfills": [],
        "missingOwners": [],
        "publicProfilesToCreate": 0,
        "authorProfilesToCreate": 0,
        "booksToAttribute": 0,
        "applied": False,
    }
    email_groups = {}
    for user in users:
        email = db.normalize_email(user.get("email") or "")
        if email:
            email_groups.setdefault(email, []).append(user["id"])
    report["emailConflicts"] = [
        {"normalizedEmail": email, "userIds": ids, "code": "EMAIL_CONFLICT"}
        for email, ids in sorted(email_groups.items()) if len(ids) > 1
    ]
    conflicting_user_ids = {
        user_id for item in report["emailConflicts"] for user_id in item["userIds"]
    }
    user_by_id = {user["id"]: user for user in users}
    profiles_by_owner = {user["id"]: (db.list_author_profiles(user["id"]) if has_author_profiles else []) for user in users}
    planned_slugs = set()
    report["publicProfilesToCreate"] = sum(
        1 for user in users if not has_public_profiles or not db.get_public_profile(user["id"])
    )
    for application in applications:
        owner_id = application["user_id"]
        raw_display = (application.get("pen_name") or "").strip()
        raw_bio = (application.get("bio") or "").strip()
        if owner_id not in user_by_id:
            report["missingOwners"].append({"applicationId": application["id"], "ownerId": owner_id, "code": "MISSING_OWNER"})
            continue
        try:
            display = profile_service.validate_author_name(raw_display)
            bio = profile_service.validate_bio(raw_bio)
        except ValueError as error:
            report["authorCandidates"].append({"applicationId": application["id"], "ownerId": owner_id,
                                                "displayName": raw_display, "bioLength": len(raw_bio)})
            report["ambiguousMappings"].append({"applicationId": application["id"],
                                                 "ownerId": owner_id, "code": "INVALID_AUTHOR_PROFILE",
                                                 "detail": str(error)})
            continue
        report["authorCandidates"].append({"applicationId": application["id"], "ownerId": owner_id,
                                            "displayName": display, "bioLength": len(bio)})
        existing = next((item for item in profiles_by_owner.get(owner_id, [])
                         if item["display_name"] == display and item.get("bio", "") == bio), None)
        if not existing:
            report["authorProfilesToCreate"] += 1
            base_slug = profile_service.slugify(display)
            slug = base_slug
            suffix = 2
            while db.get_author_profile_by_slug(slug) or slug in planned_slugs:
                slug = f"{base_slug[:max(1, 100 - len(str(suffix)) - 1)]}-{suffix}"
                suffix += 1
            if slug != base_slug:
                collision = db.get_author_profile_by_slug(base_slug)
                report["profileSlugConflicts"].append({
                    "applicationId": application["id"],
                    "requestedSlug": base_slug,
                    "assignedSlug": slug,
                    "conflictingProfileId": collision["id"] if collision else None,
                    "code": "AUTHOR_SLUG_COLLISION",
                })
            planned_slugs.add(slug)
            if apply:
                try:
                    author = db.create_author_profile(
                        owner_id, public_id=f"ap_{uuid.uuid4().hex}",
                        slug=slug,
                        display_name=profile_service.validate_author_name(display),
                        bio=profile_service.validate_bio(bio))
                except ValueError as error:
                    report["ambiguousMappings"].append({"applicationId": application["id"],
                                                         "code": "INVALID_AUTHOR_PROFILE", "detail": str(error)})
                    continue
                profiles_by_owner.setdefault(owner_id, []).append(author)
            else:
                profiles_by_owner.setdefault(owner_id, []).append({
                    "id": None, "display_name": display, "bio": bio, "status": "active",
                })

    for user in users:
        for book in [item for item in books if item["owner_id"] == user["id"] and not item.get("author_profile_id")]:
            legacy_name = (book.get("legacy_author_name") or book.get("author_name") or
                           book.get("authorName") or book.get("author") or "").strip()
            if legacy_name and not (book.get("legacy_author_name") or "").strip():
                report["legacyAttributionBackfills"].append({
                    "bid": book["bid"], "displayName": legacy_name,
                })
                if apply:
                    db.update_book(book["bid"], {"legacy_author_name": legacy_name})
            profiles = profiles_by_owner.get(user["id"], [])
            unique_profile = profiles[0] if len(profiles) == 1 else None
            legacy_matches_profile = (
                unique_profile is not None and
                (not legacy_name or legacy_name == unique_profile.get("display_name"))
            )
            if legacy_matches_profile:
                report["booksToAttribute"] += 1
                if apply:
                    db.update_book(book["bid"], {"author_profile_id": unique_profile["id"]})
            else:
                if not profiles:
                    reason = "沒有可唯一對應的 Author Profile"
                elif len(profiles) > 1:
                    reason = "帳號有多個 Author Profile"
                else:
                    reason = "legacy 作者文字與唯一 Author Profile 不一致"
                conflict = {"bid": book["bid"], "ownerId": user["id"],
                            "code": "AMBIGUOUS_AUTHOR_ATTRIBUTION",
                            "reason": reason,
                            "legacyAttribution": legacy_name or "missing"}
                report["ambiguousMappings"].append(conflict)
                report["legacyBookAttributionConflicts"].append(conflict)
    for book in books:
        if book["owner_id"] not in user_by_id:
            report["missingOwners"].append({"bid": book["bid"], "ownerId": book["owner_id"], "code": "MISSING_OWNER"})

    if apply:
        for user in users:
            db.ensure_public_profile(user["id"], user["username"])
            normalized = db.normalize_email(user.get("email") or "")
            if normalized != (user.get("email") or "") and user["id"] not in conflicting_user_ids:
                db.execute("UPDATE users SET email=? WHERE id=?", (normalized[:200], user["id"]))
            elif user["id"] in conflicting_user_ids:
                report.setdefault("preservedEmailConflicts", []).append(user["id"])
        report["applied"] = True
        report["dryRun"] = False
    return report
