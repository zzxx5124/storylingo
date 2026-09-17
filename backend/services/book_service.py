"""書籍服務：DB row ⇄ 前端 legacy book dict、可見性、書架查詢、上傳、章節管理。"""
import json
import hashlib
import os

from .. import db, settings, splitter, storage as st
from .. import exceptions as exc
from ..pagination import page_result, parse_pagination
from .. import v4_contracts as c
from . import profile as profile_service


def _json(v, default=None):
    try:
        return json.loads(v) if v else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def book_has_prologue(row: dict) -> bool:
    """取得作品的章節編號設定；舊資料未設定時維持既有序章行為。"""
    return _json(row.get("settings"), {}).get("hasPrologue", True) is not False


def _validate_prologue_setting(value: bool) -> bool:
    if not isinstance(value, bool):
        raise exc.bad_request("序章設定不正確", code="invalid_prologue_setting")
    return value


def tags_value(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
    except (TypeError, ValueError):
        pass
    return [x.strip() for x in str(value).replace("，", ",").split(",") if x.strip()]


def metadata_hash(row: dict) -> str:
    """Stable optimistic-lock token for the fields edited in the book modal."""
    payload = {
        "title": row.get("title") or "",
        "synopsis": row.get("synopsis") or "",
        "tags": tags_value(row.get("tags")),
        "category": row.get("category") or "",
        "categoryId": row.get("category_id"),
        "serial": row.get("serial") or "連載",
        "hasPrologue": book_has_prologue(row),
        "vocabLevel": row.get("vocab_level") or "",
        "categories": _json(row.get("categories"), []),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def language_type_edit_state(row: dict) -> dict:
    """回傳語言型別是否仍可編輯的 canonical read model。

    只要作品曾建立過 chapter_analyses 紀錄，就代表已有分析相依資料
    （包含 queued/running/ready/failed 或未來新增的狀態），語言型別不再
    安全可變更。這裡不自行推導 workflow，也不依賴 current_analysis_id。
    """
    records = db.query(
        "SELECT status FROM chapter_analyses WHERE book_id=? LIMIT 1",
        (row["id"],),
    )
    locked = bool(records)
    return {
        "languageTypeEditable": not locked,
        "languageTypeLockReason": "作品已有分析資料，無法直接修改語言型別。" if locked else "",
    }


# ---------- 對映 ----------

def _canonical_analysis_status(ch: dict) -> str | None:
    """由 chapter_analyses 推導 legacy status 投影（僅當與目前文字相符）。"""
    latest = db.get_latest_chapter_analysis(ch["id"])
    if not latest or latest["source_text_hash"] != ch["text_hash"]:
        return None
    if latest["status"] == "ready":
        return "analyzed"
    if latest["status"] in ("queued", "running"):
        return "analyzing"
    if latest["status"] == "failed":
        return "error"
    return None


def _canonical_audio_status(ch: dict) -> str | None:
    """由 audio_generations／active pointer 推導 legacy audio 投影。"""
    aid = ch.get("active_audio_generation_id")
    gen = db.get_audio_generation(aid) if aid else None
    if gen and gen["status"] == "ready" and gen["source_text_hash"] == ch["text_hash"]:
        return "ready"
    if gen and gen["status"] in ("queued", "running"):
        return "generating"
    if gen and gen["status"] in ("failed", "failed_capability"):
        return "error"
    latest = db.list_audio_generations(ch["id"])
    if latest:
        head = latest[0]
        if head["source_text_hash"] != ch["text_hash"]:
            return None
        if head["status"] in ("queued", "running"):
            return "generating"
        if head["status"] in ("failed", "failed_capability"):
            return "error"
    return None


def chapter_audio_state(ch: dict) -> str:
    """公開 payload 用的 audio 狀態投影（canonical 優先）。"""
    return _canonical_audio_status(ch) or (ch.get("audio") or "none")


def chapter_legacy(ch: dict) -> dict:
    changed = bool(ch.get("generated_at")) and (ch.get("analysed_hash") != ch.get("text_hash"))
    # Phase 15b：canonical 紀錄優先；legacy 欄位僅作 fallback（舊資料相容）
    audio = _canonical_audio_status(ch) or ch.get("audio")
    status = _canonical_analysis_status(ch) or ch.get("status")
    return {
        "id": ch["id"],
        "seq": ch["seq"],
        "title": ch["title"],
        "chars": ch["chars"],
        "status": status,
        "audio": audio,
        "error": ch["error"],
        "textHash": ch["text_hash"],
        "generatedAt": ch["generated_at"],
        "changed": changed,
        "chapterKey": ch.get("chapter_key"),
        "activeAudioGenerationId": ch.get("active_audio_generation_id"),
        "currentAnalysisId": ch.get("current_analysis_id"),
    }


def to_book_dict(row: dict) -> dict:
    result = {
        "id": row["bid"],
        "bid": row["bid"],
        "title": row["title"],
        "created": row["created_at"],
        "updated": row["updated_at"],
        "metadataHash": metadata_hash(row),
        "published": row["published_at"],
        "totalChars": row["chars"],
        "category": row["category"],
        "vocabLevel": row["vocab_level"],
        "categories": _json(row["categories"], []),
        "voices": _json(row["voices"], {}),
        "voicePrefs": _json(row["voice_prefs"], {}),
        "speakerInfo": _json(row["speaker_info"], {}),
        "speakerChapters": _json(row["speaker_chapters"], {}),
        "settings": _json(row["settings"], {}),
        "hasPrologue": book_has_prologue(row),
        "coverImage": row["cover_path"] or None,
        "status": row["status"],
        "serial": row["serial"],
        "synopsis": row["synopsis"],
        "tags": tags_value(row["tags"]),
        "rejectReason": row["reject_reason"],
        "ownerId": row["owner_id"],
        "categoryId": row["category_id"],
        "litCount": db.book_category_name(row["id"]),
        "slug": row.get("slug") or row["bid"],
        "ageRating": row.get("age_rating", "general"),
        "contentWarning": row.get("content_warning", ""),
        "audioMode": row.get("audio_mode") or c.AUDIO_MODE_SINGLE,
        "defaultVoiceId": row.get("default_voice_id") or None,
        "audioSettingsVersion": row.get("audio_settings_version") or 1,
    }
    # 作者編輯頁與後端 PUT 共用同一個 analysis dependency 判斷；
    # 不以 legacy status 或 current_analysis_id 另建第二套規則。
    result.update(language_type_edit_state(row))
    return result


def _owner(row: dict) -> str:
    o = db.get_user_by_id(row["owner_id"])
    return o["username"] if o else ""


def _with_author_projection(card: dict, row: dict, *, public: bool, author: dict | None = None) -> dict:
    if public:
        author = author or profile_service.author_for_book(row)
        card["owner"] = author["displayName"]
        card["author"] = author
        card.pop("ownerId", None)
    return card


def book_card(row: dict, *, public: bool = False) -> dict:
    chs = db.list_chapters(row["id"])
    total = len(chs)
    ready = sum(1 for c in chs if c["audio"] == "ready")
    analyzed = sum(1 for c in chs if c.get("status") == "analyzed")
    return _with_author_projection({
        "id": row["bid"],
        "title": row["title"],
        "synopsis": row["synopsis"],
        "tags": tags_value(row["tags"]),
        "category": row["category"],
        "hasPrologue": book_has_prologue(row),
        "vocabLevel": row["vocab_level"],
        "categories": _json(row["categories"], []),
        "serial": row["serial"],
        "status": row["status"],
        "cover": row["cover_path"] or None,
        "chars": row["chars"],
        "chapterCount": total,
        "audioReadyCount": ready,
        "analyzedChapterCount": analyzed,
        "audioRatio": round(ready / total, 4) if total else 0,
        "published": row["published_at"],
        "owner": _owner(row),
        "ownerId": row["owner_id"],
        "author": profile_service.author_for_book(row),
        "rejectReason": row["reject_reason"],
        "created": row["created_at"],
        "updated": row["updated_at"],
        "metadataHash": metadata_hash(row),
        "litCount": db.book_category_name(row["id"]),
        "slug": row.get("slug") or row["bid"],
        "ageRating": row.get("age_rating", "general"),
        "contentWarning": row.get("content_warning", ""),
    }, row, public=public)


def book_cards(rows: list[dict], *, public: bool = False) -> list[dict]:
    """L1-4：批次版 book_card —— 章節統計／作者名／分類名一次查詢，消滅 N+1。"""
    ids = [r["id"] for r in rows]
    if not ids:
        return []
    stats = db.chapter_stats_all(ids)                 # id -> (total, ready, analyzed)
    owners = db.users_by_ids([r["owner_id"] for r in rows])  # uid -> username
    lits = db.category_names_by_books(ids)            # id -> category name
    authors = profile_service.author_projections_for_books(rows)
    return [_with_author_projection({
        "id": r["bid"],
        "title": r["title"],
        "synopsis": r["synopsis"],
        "tags": tags_value(r["tags"]),
         "category": r["category"],
         "hasPrologue": book_has_prologue(r),
        "vocabLevel": r["vocab_level"],
        "categories": _json(r["categories"], []),
        "serial": r["serial"],
        "status": r["status"],
        "cover": r["cover_path"] or None,
        "chars": r["chars"],
        "chapterCount": stats.get(r["id"], (0, 0, 0))[0],
        "audioReadyCount": stats.get(r["id"], (0, 0, 0))[1],
        "analyzedChapterCount": stats.get(r["id"], (0, 0, 0))[2],
        "audioRatio": (lambda t, k: round(k / t, 4) if t else 0)(*stats.get(r["id"], (0, 0, 0))[:2]),
        "published": r["published_at"],
         "owner": owners.get(r["owner_id"]) or "",
         "ownerId": r["owner_id"],
        "author": authors.get(r["id"]),
        "rejectReason": r["reject_reason"],
        "created": r["created_at"],
        "updated": r["updated_at"],
        "metadataHash": metadata_hash(r),
        "litCount": lits.get(r["id"]),
        "slug": r.get("slug") or r["bid"],
        "ageRating": r.get("age_rating", "general"),
        "contentWarning": r.get("content_warning", ""),
    }, r, public=public, author=authors.get(r["id"])) for r in rows]


def get_book_legacy(bid: str, user=None) -> dict:
    row = db.get_book_row(bid)
    if not row:
        raise exc.not_found("找不到書")
    if user is None and not is_public(row):
        raise exc.not_found("找不到書")
    d = to_book_dict(row)
    chapters = db.list_chapters(row["id"])
    # 公開讀者只能取得已發布章節；作者本人與 admin 才能看到草稿/隱藏章節。
    if not can_manage(row, user):
        chapters = [c for c in chapters if c.get("publish_status", "published") == "published"]
    d["chapters"] = [chapter_legacy(c) for c in chapters]
    author = profile_service.author_for_book(row)
    d["owner"] = author["displayName"] if not can_manage(row, user) else _owner(row)
    d["author"] = author
    if not can_manage(row, user):
        d.pop("ownerId", None)
    d["follow"] = bool(user and not user.get("dev") and db.is_following(user["id"], row["id"]))
    d["favorite"] = bool(user and not user.get("dev") and db.has_library_item(user["id"], row["id"], "favorite"))
    d["isOwner"] = bool(user and not user.get("dev") and user["id"] == row["owner_id"])
    d["canManage"] = can_manage(row, user)
    if not d["canManage"]:
        # Book detail uses the same public projection as Audiobook Discovery;
        # the reader/audio endpoint still performs its own click-time checks.
        d["audiobook"] = public_audiobook_availability(row)
    # V4：derived workflow state（§3.10）— 只給 owner/admin，不暴露給一般讀者
    if d["canManage"]:
        from . import workflow
        d["workflow"] = workflow.compute_book_workflow(row, chapters)
    return d


def is_public(row: dict) -> bool:
    return row["status"] == "approved" and bool(row["published_at"])


def load_book(bid: str) -> dict:
    row = db.get_book_row(bid)
    if not row:
        raise exc.not_found("找不到書")
    return row


def can_manage(row: dict, user) -> bool:
    if not user:
        return False
    if user.get("dev"):
        return True
    return user["role"] in ("admin", "super_admin") or (user["role"] == "author" and user["id"] == row["owner_id"])


def can_view_content(row: dict, user) -> bool:
    """章節分析/音訊可見性：公開全開放；未公開僅 owner/admin（訪客與 reader 不可見）。"""
    if can_manage(row, user):
        return True
    return is_public(row)


def can_view_chapters(row: dict, user) -> bool:
    return can_view_content(row, user)


def can_view_audio(row: dict, user) -> bool:
    return can_view_content(row, user)


def can_view_chapter(row: dict, chapter: dict, user) -> bool:
    """統一判斷章節本身是否可被讀取、分析或下載音訊。"""
    if not chapter or not can_view_content(row, user):
        return False
    if can_manage(row, user):
        return True
    return chapter.get("publish_status", "published") == "published"


# Audiobook Discovery deliberately has its own explicit public projection.  The
# older ``audioReadyCount``/``has_audio`` fields are retained for compatibility;
# they are not strong enough to answer whether the current public audio route
# can actually play a chapter.
PUBLIC_AUDIOBOOK_SORTS = {"newest", "created", "updated", "title"}
PUBLIC_AUDIOBOOK_LANGUAGES = frozenset(settings.CATEGORIES)


def _public_audio_mode(book_row: dict, chapter_row: dict) -> str:
    override = chapter_row.get("audio_mode_override")
    if override in c.AUDIO_MODES:
        return override
    mode = book_row.get("audio_mode") or c.AUDIO_MODE_SINGLE
    return mode if mode in c.AUDIO_MODES else c.AUDIO_MODE_SINGLE


def _native_audio_resolver_matches(row: dict) -> bool:
    """Batch equivalent of the active-generation playback resolver checks."""
    if not row.get("public_audio_generation_id"):
        return False
    if row.get("public_audio_generation_status") != "ready":
        return False
    if row.get("public_audio_source_hash") != row.get("public_audio_text_hash"):
        return False
    if row.get("public_audio_mode") != _public_audio_mode(row, row):
        return False
    registry_revision = row.get("public_registry_revision")
    generation_revision = row.get("public_audio_registry_revision")
    if registry_revision is not None and generation_revision is not None:
        try:
            if int(registry_revision) != int(generation_revision):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _safe_public_audio_file(path: str | None, book_bid: str) -> str | None:
    """Resolve an internal audio path without returning or trusting traversal."""
    if not path:
        return None
    root = os.path.abspath(settings.ROOT_DIR)
    book_root = os.path.abspath(os.path.join(settings.BOOKS_DIR, str(book_bid)))
    candidate = os.path.abspath(os.path.join(root, str(path).replace("/", os.sep)))
    try:
        if os.path.commonpath((candidate, book_root)) != book_root:
            return None
        if os.path.commonpath((candidate, root)) != root:
            return None
    except ValueError:
        return None
    return candidate


def _batch_resolvable_audio_files(specs: list[tuple[str, str]]) -> set[str]:
    """Validate one bounded candidate batch, never scan storage per card/chapter."""
    resolved = set()
    for token, path in specs:
        try:
            if os.path.isfile(path):
                resolved.add(token)
        except OSError:
            continue
    return resolved


def _public_audiobook_candidate_query(*, q: str = "", category_id=None,
                                      language: str | None = None,
                                      book_id: int | None = None) -> tuple[str, list]:
    """Return the one SQL snapshot used by both audiobook count and page data."""
    where = [
        "b.status='approved'",
        "b.published_at IS NOT NULL",
        "c.publish_status='published'",
    ]
    params: list = []
    if book_id is not None:
        where.append("b.id=?")
        params.append(book_id)
    if q:
        escaped = str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append(
            "(LOWER(b.title) LIKE LOWER(?) ESCAPE '\\' "
            "OR LOWER(b.synopsis) LIKE LOWER(?) ESCAPE '\\' "
            "OR LOWER(b.tags) LIKE LOWER(?) ESCAPE '\\' "
            "OR LOWER(COALESCE(ap.display_name, b.legacy_author_name, '')) "
            "LIKE LOWER(?) ESCAPE '\\')"
        )
        params.extend([like, like, like, like])
    if category_id not in (None, ""):
        value = category_id
        if not str(value).isdigit():
            category = db.get_category(value)
            value = category["id"] if category else None
        if value is not None:
            where.append("b.category_id=?")
            params.append(int(value))
    if language not in (None, ""):
        if language not in PUBLIC_AUDIOBOOK_LANGUAGES:
            raise ValueError("不支援的語言類型")
        where.append("b.category=?")
        params.append(language)
    sql = (
        "SELECT b.*, c.id AS public_audio_chapter_id, c.seq AS public_audio_seq, "
        "c.text_hash AS public_audio_text_hash, c.audio AS public_legacy_audio, "
        "c.audio_mode_override AS public_audio_mode_override, "
        "ag.id AS public_audio_generation_id, ag.status AS public_audio_generation_status, "
        "ag.source_text_hash AS public_audio_source_hash, ag.mode AS public_audio_mode, "
        "ag.audio_path AS public_audio_path, "
        "ag.character_registry_revision AS public_audio_registry_revision, "
        "crm.revision AS public_registry_revision, cat.name AS public_category_name "
        "FROM books b "
        "JOIN chapters c ON c.book_id=b.id "
        "LEFT JOIN author_profiles ap ON ap.id=b.author_profile_id "
        "LEFT JOIN categories cat ON cat.id=b.category_id "
        "LEFT JOIN audio_generations ag ON ag.id=c.active_audio_generation_id "
        "LEFT JOIN character_registry_meta crm ON crm.book_id=b.id "
        "WHERE " + " AND ".join(where) + " ORDER BY b.id ASC, c.seq ASC"
    )
    return sql, params


def _public_audiobook_snapshots(*, q: str = "", category_id=None,
                                language: str | None = None,
                                book_id: int | None = None) -> list[dict]:
    """Build exact public summaries from one chapter snapshot and one file batch."""
    sql, params = _public_audiobook_candidate_query(
        q=q, category_id=category_id, language=language, book_id=book_id)
    rows = db.query(sql, tuple(params))
    if not rows:
        return []

    grouped: dict[int, dict] = {}
    file_specs: list[tuple[str, str]] = []
    choices: list[tuple[str, int, int, str]] = []
    for row_index, row in enumerate(rows):
        bid_id = row["id"]
        item = grouped.setdefault(bid_id, {
            "row": row,
            "publicChapterCount": 0,
            "playableSeqs": [],
        })
        item["publicChapterCount"] += 1
        native_matches = _native_audio_resolver_matches({
            **row,
            "audio_mode_override": row.get("public_audio_mode_override"),
        })
        native_path = _safe_public_audio_file(row.get("public_audio_path"), row["bid"])
        if native_matches and row.get("public_audio_path"):
            # The existing audio route chooses this native path when it is set;
            # it does not fall back to legacy if that selected artifact is gone.
            token = f"{row_index}:native"
            if native_path:
                file_specs.append((token, native_path))
                choices.append((token, bid_id, int(row["public_audio_seq"]), "native"))
            continue
        if row.get("public_legacy_audio") == "ready":
            legacy_path = _safe_public_audio_file(
                st.chapter_audio_path(row["bid"], row["public_audio_seq"]), row["bid"])
            token = f"{row_index}:legacy"
            if legacy_path:
                file_specs.append((token, legacy_path))
                choices.append((token, bid_id, int(row["public_audio_seq"]), "legacy"))

    valid_files = _batch_resolvable_audio_files(file_specs)
    for token, bid_id, seq, _kind in choices:
        if token not in valid_files:
            continue
        grouped[bid_id]["playableSeqs"].append(int(seq))

    snapshots = []
    for item in grouped.values():
        public_count = int(item["publicChapterCount"])
        playable = sorted(set(item["playableSeqs"]))
        item["playableChapterCount"] = len(playable)
        item["firstPlayableChapter"] = playable[0] if playable else None
        item["availability"] = (
            "available" if playable and len(playable) == public_count else
            "partial" if playable else "none"
        )
        snapshots.append(item)
    return snapshots


def public_audiobook_availability(row: dict) -> dict:
    """Return a safe detail projection, including ``none`` for public zero-audio."""
    if not is_public(row):
        return {
            "availability": "none", "playableChapterCount": 0,
            "publicChapterCount": 0, "firstPlayableChapter": None,
            "playableChapterSeqs": [],
        }
    snapshots = _public_audiobook_snapshots(book_id=row["id"])
    if not snapshots:
        return {
            "availability": "none", "playableChapterCount": 0,
            "publicChapterCount": 0, "firstPlayableChapter": None,
            "playableChapterSeqs": [],
        }
    snapshot = snapshots[0]
    return {
        "availability": snapshot["availability"],
        "playableChapterCount": snapshot["playableChapterCount"],
        "publicChapterCount": snapshot["publicChapterCount"],
        "firstPlayableChapter": snapshot["firstPlayableChapter"],
        "playableChapterSeqs": list(snapshot["playableSeqs"]),
    }


def _public_audiobook_dto(snapshot: dict, author: dict) -> dict:
    row = snapshot["row"]
    bid = row["bid"]
    first = snapshot["firstPlayableChapter"]
    return {
        "id": bid,
        "title": row["title"],
        "synopsis": row["synopsis"],
        "cover": row["cover_path"] or None,
        "author": author,
        "category": row["category"],
        "categoryName": row.get("public_category_name") or row["category"],
        "languageType": row["category"],
        "serial": row["serial"],
        "availability": snapshot["availability"],
        "playableChapterCount": snapshot["playableChapterCount"],
        "publicChapterCount": snapshot["publicChapterCount"],
        "detailRoute": f"#/book/{bid}",
        "listenRoute": f"#/read/{bid}/{first}?mode=listen" if first is not None else None,
    }


def list_audiobooks(*, q: str = "", category_id=None, language: str | None = None,
                    sort: str = "newest", page: int = 1, page_size: int = 20) -> dict:
    """Public audiobook collection using one canonical, exact read-model snapshot."""
    pg, per = parse_pagination(page, page_size)
    sort = sort or "newest"
    if sort not in PUBLIC_AUDIOBOOK_SORTS:
        raise ValueError("不支援的有聲書排序")
    q = (q or "").strip()[:80]
    snapshots = [item for item in _public_audiobook_snapshots(
        q=q, category_id=category_id, language=language)
        if item["playableChapterCount"] > 0]
    if sort in ("newest", "created"):
        snapshots.sort(key=lambda item: (
            item["row"].get("published_at") or item["row"].get("created_at") or "",
            item["row"]["id"]), reverse=True)
    elif sort == "updated":
        snapshots.sort(key=lambda item: (item["row"].get("updated_at") or "", item["row"]["id"]), reverse=True)
    else:
        snapshots.sort(key=lambda item: (
            (item["row"].get("title") or "").casefold(), item["row"]["id"]))
    total = len(snapshots)
    page_rows = snapshots[(pg - 1) * per:pg * per]
    author_rows = [item["row"] for item in page_rows]
    authors = profile_service.author_projections_for_books(author_rows)
    items = [_public_audiobook_dto(item, authors.get(item["row"]["id"], {
        "displayName": "匿名作者", "legacy": True, "link": None,
    })) for item in page_rows]
    return page_result(items, total=total, page=pg, page_size=per)


def explain_audiobook_query(*, q: str = "", category_id=None,
                            language: str | None = None) -> list[dict]:
    """Test/operations hook for the final public candidate query plan."""
    sql, params = _public_audiobook_candidate_query(
        q=(q or "").strip()[:80], category_id=category_id, language=language)
    return db.query("EXPLAIN QUERY PLAN " + sql, tuple(params))


def ensure_can_manage(row: dict, user):
    if not can_manage(row, user):
        raise exc.forbidden()


# ---------- 列表 / 搜尋 ----------

def _public_cond(user):
    if user is None:
        return "b.status='approved' AND b.published_at IS NOT NULL", []
    if user.get("dev") or user["role"] == "admin":
        return "", []
    return "b.status='approved' AND b.published_at IS NOT NULL", []


def list_books(user, *, q=None, category_id=None, language=None, serial=None, mine=False, followed=False,
               sort=None, status=None, page=None, page_size=20, public=False, has_audio=None):
    where = []
    params = []
    if public:
        # 公開瀏覽面（首頁/搜尋/排行）一律只看「已上架且已發布」的作品，與呼叫者角色無關。
        # admin/dev 需要管理自己的非公開作品時走 mine / admin scope，不在公開瀏覽放寬，
        # 避免草稿／待審核／拒絕／下架作品被一般使用者或管理員瀏覽公開頁時洩漏。
        where.append("b.status='approved' AND b.published_at IS NOT NULL")
    # mine／followed 由本人帶出，不受「公開」條件限制；其餘依角色過濾
    elif mine and user and not user.get("dev"):
        where.append("b.owner_id = ?")
        params.append(user["id"])
    elif followed and user and not user.get("dev"):
        where.append("EXISTS (SELECT 1 FROM follows f WHERE f.book_id = b.id AND f.user_id = ?)")
        params.append(user["id"])
        where.append("b.status='approved' AND b.published_at IS NOT NULL")
    else:
        cond, cond_params = _public_cond(user)
        if cond:
            where.append(cond)
            params += cond_params
    if q:
        # Treat LIKE metacharacters as search text, not as an accidental
        # unbounded wildcard supplied by a client.
        escaped = str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        where.append("(LOWER(b.title) LIKE LOWER(?) ESCAPE '\\' OR LOWER(b.synopsis) LIKE LOWER(?) ESCAPE '\\' OR LOWER(b.tags) LIKE LOWER(?) ESCAPE '\\')")
        params += [like, like, like]
    if category_id:
        if not str(category_id).isdigit():
            c = db.get_category(category_id)
            category_id = c["id"] if c else None
        if category_id:
            where.append("b.category_id = ?")
            params.append(category_id)
    if language in settings.CATEGORIES:
        where.append("b.category = ?")
        params.append(language)
    if serial:
        where.append("b.serial = ?")
        params.append(serial)
    if status and user and (user.get("dev") or user["role"] == "admin"):
        where.append("b.status = ?")
        params.append(status)
    if has_audio is True:
        where.append("EXISTS (SELECT 1 FROM chapters audio_ch WHERE audio_ch.book_id=b.id AND audio_ch.audio='ready')")
    elif has_audio is False:
        where.append("NOT EXISTS (SELECT 1 FROM chapters audio_ch WHERE audio_ch.book_id=b.id AND audio_ch.audio='ready')")
    order = _sort(sort)
    predicate = " AND ".join(where) if where else "1=1"
    sql = "SELECT b.* FROM books b WHERE " + predicate
    sql += " " + order
    if page is not None:
        pg, per = parse_pagination(page, page_size)
        total = db.query_one(f"SELECT COUNT(*) AS total FROM books b WHERE {predicate}", tuple(params))["total"]
        rows = db.query(sql + " LIMIT ? OFFSET ?", tuple(params) + (per, (pg - 1) * per))
        public_view = public or followed or (not mine and not (user and (user.get("dev") or user.get("role") == "admin")))
        return page_result(book_cards(rows, public=public_view), total=total, page=pg, page_size=per)
    rows = db.query(sql, tuple(params))
    public_view = public or followed or (not mine and not (user and (user.get("dev") or user.get("role") == "admin")))
    return [book_legacy_list_item(r, public=public_view) for r in rows]


def book_legacy_list_item(row: dict, *, public: bool = False) -> dict:
    """書架用（舊前端相容）：完整 legacy dict（含章節）。"""
    d = to_book_dict(row)
    chapters = db.list_chapters(row["id"])
    if public:
        chapters = [c for c in chapters if c.get("publish_status", "published") == "published"]
    d["chapters"] = [chapter_legacy(c) for c in chapters]
    author = profile_service.author_for_book(row)
    d["owner"] = author["displayName"] if public else _owner(row)
    d["author"] = author
    if public:
        d.pop("ownerId", None)
    d["follow"] = False
    d["isOwner"] = False
    d["canManage"] = False
    return d


def _sort(sort: str) -> str:
    sort = sort or ""
    if sort == "chars":
        return "ORDER BY b.chars DESC, b.id DESC"
    if sort == "updated":
        return "ORDER BY b.updated_at DESC, b.id DESC"
    if sort == "created":
        return "ORDER BY b.created_at DESC, b.id DESC"
    if sort in ("new", "published", None, ""):
        return "ORDER BY COALESCE(b.published_at, b.created_at) DESC, b.id DESC"
    return "ORDER BY COALESCE(b.published_at, b.created_at) DESC, b.id DESC"


# ---------- 上傳 ----------

def create_book_from_upload(user, *, file_name: str, text: str, category: str,
                            vocab_level: str, categories: list[str],
                            category_id: int = None, split_mode: str, split_chars: int,
                            has_prologue: bool = True, author_profile_id=None) -> dict:
    has_prologue = _validate_prologue_setting(has_prologue)
    if category not in settings.CATEGORIES:
        category = settings.CAT_VOCAB
    if vocab_level not in settings.VOCAB_LEVELS:
        vocab_level = "AUTO"
    cats = [c for c in categories if c in settings.CATEGORIES] or [category]
    author_profile_id = profile_service.resolve_owned_author_profile(user["id"], author_profile_id)
    book_fields = dict(
        owner_id=user["id"],
        title=os.path.splitext(file_name)[0] or "未命名小說",
        category=category,
        vocab_level=vocab_level,
        categories=json.dumps(cats),
        serial="連載",
        category_id=category_id,
        settings=json_dumps({"hasPrologue": bool(has_prologue)}),
        status="draft",
        chars=len(text),
    )
    if author_profile_id is not None:
        book_fields["author_profile_id"] = author_profile_id
    bid = db.create_book(**book_fields)
    row = db.get_book_row(bid)
    chs = splitter.split_chapters(text, mode=split_mode, char_limit=split_chars)
    bid_id = row["id"]
    total = 0
    for c in chs:
        db.insert_chapter(bid_id, c["seq"], c["title"], c["text"])
        total += c["chars"]
    db.set_chapter_paths(bid_id)
    db.update_book(bid, {"chars": total})
    return get_book_legacy(bid, user=user)


def create_book_manual(user, *, title: str, synopsis: str = "",
                       category: str = "vocab", category_id: int = None,
                       serial: str = "連載", has_prologue: bool = True,
                       author_profile_id=None) -> dict:
    has_prologue = _validate_prologue_setting(has_prologue)
    """手動建立空書（不需上傳檔案）。作者顯示名稱沿用使用者 username。"""
    title = (title or "").strip()
    if not title:
        raise exc.bad_request("書名不能為空")
    if len(title) > 200:
        raise exc.bad_request("書名過長（上限 200 字）")
    if category not in settings.CATEGORIES:
        category = settings.CAT_VOCAB
    if serial not in ("連載", "完結"):
        serial = "連載"
    author_profile_id = profile_service.resolve_owned_author_profile(user["id"], author_profile_id)
    book_fields = dict(
        owner_id=user["id"],
        title=title,
        synopsis=(synopsis or "").strip()[:2000],
        category=category,
        vocab_level="AUTO",
        categories=json.dumps([category]),
        serial=serial,
        category_id=category_id,
        settings=json_dumps({"hasPrologue": bool(has_prologue)}),
        status="draft",
        chars=0,
    )
    if author_profile_id is not None:
        book_fields["author_profile_id"] = author_profile_id
    bid = db.create_book(**book_fields)
    return get_book_legacy(bid, user=user)


def add_chapter(row: dict, title: str, text: str) -> dict:
    seq = db.next_seq(row["id"])
    db.insert_chapter(row["id"], seq, title, text)
    db.set_chapter_paths(row["id"])
    db.update_book(row["bid"], {"chars": (row["chars"] or 0) + len(text)})
    return db.get_chapter(row["id"], seq)


def remove_chapter(row: dict, seq: int) -> None:
    if not db.get_chapter(row["id"], seq):
        raise exc.not_found("找不到章節")
    if db.book_chapter_count(row["id"]) <= 1:
        raise exc.bad_request("至少需保留一章")
    chs = db.list_chapters(row["id"])
    rest = [c["id"] for c in chs if c["seq"] != seq]
    db.delete_chapter_row(row["id"], seq)
    db.reorder_chapters(row["id"], rest)
    db.update_book(row["bid"], {"chars": sum(c["chars"] for c in chs if c["seq"] != seq)})


def reorder_chapters(row: dict, order: list) -> None:
    chs = db.list_chapters(row["id"])
    seqs = {c["seq"] for c in chs}
    if not isinstance(order, list) or len(order) != len(chs) or set(order) != seqs:
        raise exc.bad_request("章節順序不正確")
    by = {c["seq"]: c for c in chs}
    ordered_ids = [by[s]["id"] for s in order]
    db.reorder_chapters(row["id"], ordered_ids)


def delete_book(bid: str) -> None:
    st.delete_book(bid)
    db.delete_book_row(bid)


_UNSET = object()


def update_meta(row: dict, *, title=None, synopsis=None, tags=None, category=None,
                vocab_level=None, categories=None, serial=None, category_id=None,
                audio_mode=_UNSET, default_voice_id=_UNSET, has_prologue=_UNSET,
                author_profile_id=_UNSET):
    fields = {}
    if title is not None:
        fields["title"] = title
    if synopsis is not None:
        fields["synopsis"] = synopsis
    if tags is not None:
        fields["tags"] = json.dumps(tags_value(tags), ensure_ascii=False)
    if category is not None:
        if not isinstance(category, str) or category not in settings.CATEGORIES:
            raise exc.bad_request("語言型別不正確", code="invalid_language_type")
        if category != row.get("category") and not language_type_edit_state(row)["languageTypeEditable"]:
            raise exc.conflict(
                "作品已有分析資料，無法直接修改語言型別。",
                code="language_type_locked_after_analysis",
            )
        fields["category"] = category
    if vocab_level is not None:
        fields["vocab_level"] = vocab_level
    if categories is not None:
        fields["categories"] = json.dumps(categories)
    if serial is not None:
        if serial not in ("連載", "完結"):
            raise exc.bad_request("連載狀態僅能為『連載』或『完結』")
        fields["serial"] = serial
    if category_id is not None:
        fields["category_id"] = int(category_id) if category_id else None
    if author_profile_id is not _UNSET:
        fields["author_profile_id"] = profile_service.resolve_owned_author_profile(
            row["owner_id"], author_profile_id)
    if has_prologue is not _UNSET:
        if not isinstance(has_prologue, bool):
            raise exc.bad_request("序章設定不正確", code="invalid_prologue_setting")
        book_settings = _json(row.get("settings"), {})
        book_settings["hasPrologue"] = has_prologue
        fields["settings"] = json_dumps(book_settings)
    # V4 音訊設定（§5.4）：任何會改變生成輸出的設定都 increment audio_settings_version
    if audio_mode is not _UNSET:
        if audio_mode not in c.AUDIO_MODES:
            raise exc.bad_request("朗讀方式僅能為 single 或 multi")
        fields["audio_mode"] = audio_mode
        fields["audio_settings_version"] = (row.get("audio_settings_version") or 1) + 1
    if default_voice_id is not _UNSET:
        fields["default_voice_id"] = default_voice_id or None
        fields["audio_settings_version"] = (row.get("audio_settings_version") or 1) + 1
    db.update_book(row["bid"], fields)


def submit_book(row: dict) -> None:
    if row["status"] not in ("draft", "rejected"):
        raise exc.conflict("僅『草稿』或『退件』狀態可送審")
    db.set_book_status(row["bid"], "submitted", reject_reason="")


def follow_book(row: dict, user) -> dict:
    if not user or user.get("dev"):
        raise exc.unauthorized()
    if not is_public(row):
        raise exc.not_found("找不到書")
    db.add_follow(user["id"], row["id"])
    return {"ok": True}


def unfollow_book(row: dict, user: dict) -> dict:
    if not user or user.get("dev"):
        raise exc.unauthorized()
    if not is_public(row):
        raise exc.not_found("找不到書")
    db.remove_follow(user["id"], row["id"])
    return {"ok": True}
