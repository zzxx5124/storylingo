"""書庫／章節／生成路由（V4 canonical；含既有前端相容形狀）。"""
import io
import json
import os
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from PIL import Image

from .. import analyzer, db, pipeline, settings, state, storage, tts
from .. import exceptions as exc
from .. import v4_contracts as c
from ..security import get_current_user, require_author
from ..services import book_service, category as cat_service, chapter_service
from ..services import voice as voice_service
from ..services import analysis as analysis_svc
from ..services import audio_generation as ag_service
from ..services import generation_pipeline as gp
from ..services import generation_orchestration as orchestration
from ..services import ai_provider as ai_provider_svc
from ..services import expressive_tts
from ..services import content_requests as request_service, notifications as notification_service, policy
from ..services import source_faithful

router = APIRouter(prefix="/api", tags=["books"])

_PRIVATE_COLLECTION_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Cookie",
}


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # L0-4：單檔上限 10MB


def _read_upload(file: UploadFile) -> bytes:
    """讀取上傳檔，超過上限回 413。先檢查 Content-Length 再讀，避免整檔載入記憶體。"""
    declared = file.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise exc.payload_too_large("檔案超過 10MB 上限")
    raw = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise exc.payload_too_large("檔案超過 10MB 上限")
    return raw


def _require_401(user):
    if not user:
        raise exc.unauthorized()
    return user


# ---------- 類別 ----------

@router.get("/categories")
def get_categories():
    return {"categories": cat_service.list_categories()}


# ---------- 書庫 ----------

@router.get("/books")
def books_list(
    user: Annotated[Optional[dict], Depends(get_current_user)],
    q: str = "", category_id: Optional[str] = None, serial: Optional[str] = None,
    mine: int = 0, followed: int = 0, sort: Optional[str] = None,
    status: Optional[str] = None, page: Optional[int] = None, page_size: Optional[int] = 20,
):
    # The unpaged /api/books shape is a deliberate legacy boundary.  New
    # collection consumers opt into the canonical envelope explicitly.
    if page is not None:
        from ..pagination import parse_pagination
        try:
            page, page_size = parse_pagination(page, page_size)
        except ValueError as error:
            raise exc.bad_request(str(error)) from error
    result = book_service.list_books(
        user, q=q or None, category_id=category_id, serial=serial,
        mine=bool(mine), followed=bool(followed), sort=sort, status=status,
        page=page, page_size=page_size)
    if page is not None and mine and user and not user.get("dev"):
        return JSONResponse(result, headers=_PRIVATE_COLLECTION_HEADERS)
    return result


@router.post("/books")
def upload_book(
    file: UploadFile,
    user: Annotated[dict, Depends(require_author)],
    category: Annotated[str, Form()] = "vocab",
    genreId: Annotated[Optional[int], Form()] = None,
    vocabLevel: Annotated[str, Form()] = "AUTO",
    categories: Annotated[str, Form()] = "",
    splitMode: Annotated[str, Form()] = "title",
    splitChars: Annotated[int, Form()] = 3000,
    hasPrologue: Annotated[bool, Form()] = True,
    authorProfileId: Annotated[Optional[int], Form()] = None,
):
    raw = _read_upload(file)
    text = _decode(raw)
    if len(text.strip()) < 50:
        raise exc.bad_request("檔案內容太短，無法作為小說")
    cats = [c for c in categories.split(",") if c] if categories else []
    mode = splitMode if splitMode in ("title", "chars", "blank") else "title"
    try:
        return book_service.create_book_from_upload(
            user, file_name=file.filename or "未命名.txt", text=text,
            category=category, vocab_level=vocabLevel, categories=cats,
            category_id=genreId, split_mode=mode, split_chars=splitChars or 3000,
            has_prologue=hasPrologue, author_profile_id=authorProfileId)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error


@router.post("/books/manual")
def create_manual_book(payload: dict, user: Annotated[dict, Depends(require_author)]):
    """手動建立空書（Phase 15a Creator Journey；不需上傳檔案）。"""
    try:
        return book_service.create_book_manual(
            user,
            title=payload.get("title"),
            synopsis=payload.get("synopsis"),
            category=payload.get("category", "vocab"),
            category_id=payload.get("categoryId"),
            serial=payload.get("serial", "連載"),
            has_prologue=payload.get("hasPrologue", True),
            author_profile_id=payload.get("authorProfileId"),
        )
    except ValueError as error:
        raise exc.bad_request(str(error)) from error


@router.get("/books/{bid}")
def get_book(bid: str, request: Request, public_view: int = 0,
             user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    row = book_service.load_book(bid)
    requested_public = public_view or bool(request and request.headers.get("x-storylingo-public") == "1")
    if requested_public:
        if not book_service.is_public(row):
            raise exc.not_found("找不到書")
        # 公開投影仍以 visitor policy 過濾章節；僅附上目前帳號的關係，
        # 不可把 manager principal 傳入，否則會洩漏隱藏章節與工作流。
        out = book_service.get_book_legacy(bid, user=None)
        if user and not user.get("dev"):
            out["favorite"] = db.has_library_item(user["id"], row["id"], "favorite")
            out["follow"] = db.is_following(user["id"], row["id"])
        return out
    if not book_service.can_view_content(row, user):
        raise exc.not_found("找不到書")
    return book_service.get_book_legacy(bid, user=user)


@router.put("/books/{bid}")
def update_book(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    with state.book_lock(bid):
        # Metadata editing uses the same account-scoped lock and immediate
        # transaction as chapter editing.  The client sends a metadata snapshot
        # token so a second tab cannot silently overwrite newer metadata.
        with db.atomic() as con:
            con.execute("BEGIN IMMEDIATE")
            row = book_service.load_book(bid)
            book_service.ensure_can_manage(row, user)
            expected_updated_at = payload.get("expectedUpdatedAt")
            if expected_updated_at is not None and not isinstance(expected_updated_at, str):
                raise exc.bad_request("作品版本格式不正確", code="invalid_book_revision")
            expected_metadata_hash = payload.get("expectedMetadataHash")
            if expected_metadata_hash is not None and not isinstance(expected_metadata_hash, str):
                raise exc.bad_request("作品內容版本格式不正確", code="invalid_book_revision")
            if expected_metadata_hash is not None and expected_metadata_hash != book_service.metadata_hash(row):
                raise exc.conflict("作品資料已在其他分頁更新，請重新載入後再儲存。", code="book_metadata_conflict")
            if expected_metadata_hash is None and expected_updated_at is not None and expected_updated_at != row["updated_at"]:
                raise exc.conflict("作品資料已在其他分頁更新，請重新載入後再儲存。", code="book_metadata_conflict")
            audio_updates = {}
            if "audioMode" in payload:
                audio_updates["audio_mode"] = payload["audioMode"]
            if "defaultVoiceId" in payload:
                audio_updates["default_voice_id"] = payload["defaultVoiceId"]
            try:
                meta = {
                    "title": payload.get("title"),
                    "synopsis": payload.get("synopsis"),
                    "tags": payload.get("tags"),
                    "category": payload.get("category"),
                    "vocab_level": payload.get("vocabLevel"),
                    "categories": payload.get("categories"),
                    "serial": payload.get("serial"),
                    "category_id": payload.get("categoryId"),
                    **audio_updates,
                }
                if "hasPrologue" in payload:
                    meta["has_prologue"] = payload["hasPrologue"]
                if "authorProfileId" in payload:
                    meta["author_profile_id"] = payload["authorProfileId"]
                book_service.update_meta(row, **meta)
            except ValueError as e:
                raise exc.bad_request(str(e))
    return book_service.get_book_legacy(bid, user=user)


@router.delete("/books/{bid}")
def remove_book(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    with state.book_lock(bid):
        row = book_service.load_book(bid)
        book_service.ensure_can_manage(row, user)
        book_service.delete_book(bid)
    return {"ok": True}


# ---------- 封面 ----------

@router.post("/books/{bid}/cover")
def upload_cover(bid: str, file: UploadFile, user: Annotated[dict, Depends(require_author)]):
    row = book_service.load_book(bid)
    book_service.ensure_can_manage(row, user)
    declared = file.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > 2 * 1024 * 1024:
        raise exc.payload_too_large("圖片最大 2 MB")
    raw = file.file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise exc.payload_too_large("圖片最大 2 MB")
    ext = os.path.splitext(file.filename or "cover.jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise exc.bad_request("僅接受 jpg/png/webp")
    cover_dir = storage.cover_dir(bid)
    os.makedirs(cover_dir, exist_ok=True)
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.verify()
        img = Image.open(io.BytesIO(raw))
        if img.width > 10000 or img.height > 10000 or img.width * img.height > 50_000_000:
            raise ValueError("圖片尺寸過大")
        img = img.convert("RGB")
    except Exception as error:
        raise exc.bad_request("圖片內容無效或尺寸過大") from error
    w = min(img.size)
    left = (img.width - w) // 2
    top = (img.height - w) // 2
    img = img.crop((left, top, left + w, top + w)).resize((240, 240), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    with open(os.path.join(cover_dir, "cover.jpg"), "wb") as f:
        f.write(buf.getvalue())
    url = f"/api/books/{bid}/cover"
    db.update_book(bid, {"cover_path": url})
    return {"coverImage": url}


@router.get("/books/{bid}/cover")
def get_cover(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    if not book_service.can_view_content(row, user):
        raise exc.not_found("無封面")
    p = storage.cover_path(bid)
    if not os.path.exists(p):
        raise exc.not_found("無封面")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


# ---------- 章節 ----------

@router.post("/books/{bid}/chapters")
def add_chapter(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    with state.book_lock(bid):
        row = book_service.load_book(bid)
        book_service.ensure_can_manage(row, user)
        title = (payload.get("title") or "").strip() or "新章節"
        text = (payload.get("text") or "").strip()
        if not text:
            raise exc.bad_request("章節內容不能為空")
        book_service.add_chapter(row, title, text)
    return book_service.get_book_legacy(bid, user=user)


@router.put("/books/{bid}/chapters/reorder")
def reorder_chapters(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    with state.book_lock(bid):
        row = book_service.load_book(bid)
        book_service.ensure_can_manage(row, user)
        book_service.reorder_chapters(row, payload.get("order"))
    return book_service.get_book_legacy(bid, user=user)


@router.put("/books/{bid}/chapters/{seq}")
def edit_chapter(bid: str, seq: int, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    with state.book_lock(bid), db.atomic() as con:
        con.execute("BEGIN IMMEDIATE")
        row = book_service.load_book(bid)
        book_service.ensure_can_manage(row, user)
        ch = chapter_service.get_chapter(row, seq)
        # 舊 caller 可省略；新版編輯器以讀取快照防止多分頁覆寫與重排後寫錯章。
        for field, column in (("expectedChapterKey", "chapter_key"), ("expectedTextHash", "text_hash"), ("expectedTitle", "title")):
            if field in payload and payload[field] != ch[column]:
                raise exc.conflict("章節已被修改或重新排序。請保留你的文字，重新開啟章節後再合併修改。", "CHAPTER_EDIT_CONFLICT")
        for field in ("title", "text"):
            if field in payload and not isinstance(payload[field], str):
                raise exc.bad_request("章節標題與內容必須是文字")
        if "text" in payload and not payload["text"].strip():
            raise exc.bad_request("請輸入章節內容")
        res = chapter_service.update_chapter(row, ch, title=payload.get("title"), text=payload.get("text"), saved_by=user["id"] if user and not user.get("dev") else None)
    out = book_service.get_book_legacy(bid, user=user)
    out["changed"] = res["changed"]
    out["removedOutputs"] = res["removedOutputs"]
    out["revisionId"] = res["revisionId"]
    return out


@router.delete("/books/{bid}/chapters/{seq}")
def remove_chapter(bid: str, seq: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    with state.book_lock(bid):
        row = book_service.load_book(bid)
        book_service.ensure_can_manage(row, user)
        book_service.remove_chapter(row, seq)
    return book_service.get_book_legacy(bid, user=user)


@router.put("/books/{bid}/chapters/{seq}/publish")
def publish_chapter(bid: str, seq: int, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    # Chapter visibility is a content/publication boundary, not a Reviewer edit
    # surface. Authors continue to edit text; public release goes through the
    # typed Book request workflow.
    if not policy.can_manage_chapter_visibility(user):
        raise exc.forbidden("章節公開狀態必須透過作品審核流程")
    row = book_service.load_book(bid)
    book_service.ensure_can_manage(row, user)
    chapter = db.get_chapter(row["id"], seq)
    if not chapter:
        raise exc.not_found("找不到章節")
    publish = bool(payload.get("published", True))
    with db.atomic() as con:
        db.update_chapter(row["id"], seq, {"publish_status": "published" if publish else "hidden", "published_at": db.ts() if publish else None})
        if publish:
            followers = db.scalars("SELECT user_id FROM follows WHERE book_id=?", (row["id"],))
            chapter_title = chapter["title"] or f"第 {seq} 章"
            for follower_id in followers:
                notification_service.create_notification(
                    follower_id, "chapter.published", "content", f"{row['title']} 更新了", chapter_title,
                    dedupe_key=f"chapter:{row['id']}:{seq}:published",
                    target_type="chapter", target_id=f"{bid}:{seq}", target_route=f"#/read/{bid}/{seq}",
                    source_type="chapter", source_id=f"{row['id']}:{seq}", source_book_id=row["id"], con=con,
                    legacy_kind="chapter_published")
    return {"ok": True, "publishStatus": "published" if publish else "hidden"}


@router.get("/books/{bid}/chapters/{seq}")
def get_chapter_data(bid: str, seq: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    chapter = db.get_chapter(row["id"], seq)
    if not book_service.can_view_chapter(row, chapter, user):
        raise exc.not_found("找不到章節")
    analysis = {}
    analysis_stale = False
    record = db.get_ready_chapter_analysis(chapter["id"], chapter["text_hash"])
    if record:
        artifact = _analysis_artifact(record)
        if artifact:
            if _analysis_requires_source_refresh(record, artifact):
                analysis_stale = True
                analysis = _source_faithful_fallback_analysis(chapter, record, artifact)
            else:
                compat = analysis_svc.legacy_compatibility_view(artifact)
                analysis = {
                    "analysisId": record["id"], "status": "ready",
                    "schemaVersion": record["schema_version"], "chapterKey": chapter["chapter_key"],
                    "sourceTextHash": record["source_text_hash"],
                    "characters": artifact.get("characters", []),
                    "speakers": compat.get("speakers", []), "segments": artifact.get("segments", []),
                    "legacySegments": compat.get("segments", []),
                    "learning": artifact.get("learning", {}),
                    "aiModel": record.get("ai_model"),
                }
    timing = None
    # V4：優先使用 active ready generation 的 timing path
    # 舊 segmentation 的 timing 與目前來源句子分段不相容；不能交給
    # Reader 以不同數量的 segment 進行假同步。
    if not analysis_stale:
        book = gp._build_book_dict(row)
        gen = ag_service.resolve_active_generation(book, chapter)
        if gen and gen.get("timing_path"):
            tp = os.path.join(settings.ROOT_DIR, gen["timing_path"])
        else:
            tp = storage.chapter_timing_path(bid, seq)
        if os.path.exists(tp):
            try:
                with open(tp, "r", encoding="utf-8") as f:
                    timing = json.load(f)
            except Exception:
                timing = None
    result = {"analysis": analysis, "timing": timing}
    # 編輯器需要原文，但原文不可隨公開分析 API 洩漏給一般讀者。
    if book_service.can_manage(row, user):
        result["chapter"] = {"seq": chapter["seq"], "title": chapter["title"], "text": chapter["text"],
                             "chapterKey": chapter["chapter_key"], "textHash": chapter["text_hash"]}
    return JSONResponse(result, headers={"Cache-Control": "no-cache"})


@router.get("/books/{bid}/audio/{seq}")
def get_audio(bid: str, seq: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    chapter = db.get_chapter(row["id"], seq)
    if not book_service.can_view_chapter(row, chapter, user):
        raise exc.not_found("找不到音訊")
    # V4：優先使用 active ready generation 的 generation-specific 路徑
    book = gp._build_book_dict(row)
    gen = ag_service.resolve_active_generation(book, chapter)
    if gen and gen.get("audio_path"):
        p = os.path.join(settings.ROOT_DIR, gen["audio_path"])
    else:
        p = storage.chapter_audio_path(bid, seq)
    if not os.path.exists(p):
        raise exc.not_found("音訊尚未生成")
    mtime = int(os.path.getmtime(p))
    return FileResponse(p, media_type="audio/mpeg", filename=f"{seq:04d}.mp3",
                        headers={"Cache-Control": "no-cache", "ETag": f'"{mtime:x}"'})


# ---------- 追書 / 送審 ----------

@router.post("/books/{bid}/submit")
def submit_book(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    user = _require_401(user)
    row = book_service.load_book(bid)
    if not policy.can_request_publish(user, row):
        raise exc.forbidden()
    try:
        request = request_service.submit(book=row, requester_id=user["id"], request_type="publish", payload={})
    except request_service.RequestConflict as error:
        detail = str(error)
        if error.request_id:
            detail += f"（既有申請 #{error.request_id}）"
        raise exc.conflict(detail, code=error.code) from error
    return request_service.serialize_request(request, include_events=True)


@router.post("/books/{bid}/follow")
def follow_book(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    return book_service.follow_book(row, user)


@router.post("/books/{bid}/unfollow")
def unfollow_book(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    return book_service.unfollow_book(row, user)


# ---------- 聲線　/ 設定 ----------

def _profile_view(row):
    item = dict(row)
    raw = item.get("settings_json") or "{}"
    try:
        item["settings"] = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        item["settings"] = {}
    item.pop("settings_json", None)
    item["version"] = int(item.get("version") or 1)
    return item


def _profile_availability(voice_id: str) -> str:
    provider = db.get_active_tts_provider()
    if not provider or not voice_id:
        return "stale"
    catalog = db.list_tts_provider_voices(provider["id"], include_unavailable=True)
    row = next((item for item in catalog if item["voice_id"] == voice_id), None)
    return "available" if row and row.get("catalog_status") == "available" and row.get("enabled") else "stale"


@router.get("/books/{bid}/expressive-profiles")
def list_expressive_profiles(bid: str, user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    return {"items": [_profile_view(item) for item in db.list_expressive_profiles(row["id"])]}


@router.post("/books/{bid}/expressive-profiles")
def create_expressive_profile(bid: str, payload: dict, user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    speaker_id = str(payload.get("speakerId") or "").strip()
    voice_id = str(payload.get("voiceId") or "").strip()
    emotion = payload.get("emotion")
    if not speaker_id or not voice_id or not emotion:
        raise exc.bad_request("speakerId、voiceId、emotion 為必填")
    profile = {
        "profile_id": str(payload.get("profileId") or f"profile:{uuid.uuid4().hex}"),
        "book_id": row["id"], "speaker_id": speaker_id, "voice_id": voice_id,
        "emotion": emotion, "version": 1, "status": payload.get("status", "active"),
        "settings_json": payload.get("settings") or {},
    }
    try:
        expressive_tts.validate_profile(profile)
    except (TypeError, ValueError) as error:
        raise exc.bad_request(str(error)) from error
    profile["settings_json"] = json.dumps(profile["settings_json"], ensure_ascii=False)
    profile["availability_status"] = _profile_availability(voice_id)
    profile["created_by"] = user["id"]
    try:
        db.create_expressive_profile(profile)
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise exc.conflict("profile_id 或 profile version 已存在") from error
        raise
    return _profile_view(db.get_expressive_profile(profile["profile_id"]))


@router.put("/books/{bid}/expressive-profiles/{profile_id}")
def update_expressive_profile(bid: str, profile_id: str, payload: dict,
                              user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    current = db.get_expressive_profile(profile_id)
    if not current or current["book_id"] != row["id"]:
        raise exc.not_found("找不到 expressive profile")
    fields = {}
    for source, target in (("speakerId", "speaker_id"), ("voiceId", "voice_id"),
                           ("emotion", "emotion"), ("status", "status")):
        if source in payload:
            fields[target] = payload[source]
    if "settings" in payload:
        fields["settings_json"] = json.dumps(payload.get("settings") or {}, ensure_ascii=False)
    candidate = {**dict(current), **fields, "version": int(current["version"]) + 1}
    try:
        expressive_tts.validate_profile(candidate)
    except (TypeError, ValueError) as error:
        raise exc.bad_request(str(error)) from error
    fields["version"] = candidate["version"]
    fields["availability_status"] = _profile_availability(candidate["voice_id"])
    db.update_expressive_profile(profile_id, fields)
    return _profile_view(db.get_expressive_profile(profile_id))


@router.delete("/books/{bid}/expressive-profiles/{profile_id}")
def archive_expressive_profile(bid: str, profile_id: str,
                               user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    current = db.get_expressive_profile(profile_id)
    if not current or current["book_id"] != row["id"]:
        raise exc.not_found("找不到 expressive profile")
    db.update_expressive_profile(profile_id, {"status": "archived", "version": int(current["version"]) + 1})
    return _profile_view(db.get_expressive_profile(profile_id))

@router.put("/books/{bid}/voices")
def update_voices(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    user = _ensure_generation_operator(row, user, payload)
    fields = {}
    if "voices" in payload:
        fields["voices"] = json.dumps(payload["voices"] or {})
    if "prefs" in payload:
        fields["voice_prefs"] = json.dumps(payload["prefs"] or {})
    if fields:
        db.update_book(bid, fields)
    r = db.get_book_row(bid)
    return {"voices": json.loads(r["voices"] or "{}"),
            "prefs": json.loads(r["voice_prefs"] or "{}")}


@router.post("/books/{bid}/voices/ai-match")
def ai_match_voices(bid: str, payload: Optional[dict] = None,
                    user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    row = book_service.load_book(bid)
    user = _ensure_generation_operator(row, user, payload)
    d = pipeline.build_book_dict(row)
    requested_prefs = (payload or {}).get("prefs") if isinstance(payload, dict) else None
    requested_voices = (payload or {}).get("voices") if isinstance(payload, dict) else None
    if isinstance(requested_prefs, dict):
        d["voicePrefs"] = requested_prefs
    if isinstance(requested_voices, dict):
        d["voices"] = requested_voices
    res = voice_service.ai_match(d)
    if res.get("errorCode"):
        raise exc.CodedHTTPException(
            502,
            res.get("message") or "AI 語者匹配服務暫時無法使用，請稍後再試",
            res["errorCode"],
        )
    matches = {m["speaker"]: m["voice_id"] for m in res.get("matches", []) if m.get("voice_id")}
    updates = {}
    if isinstance(requested_prefs, dict):
        updates["voice_prefs"] = json.dumps(requested_prefs, ensure_ascii=False)
    if matches:
        voices_map = (dict(requested_voices) if isinstance(requested_voices, dict)
                      else json.loads(row["voices"] or "{}"))
        voices_map.update(matches)
        updates["voices"] = json.dumps(voices_map, ensure_ascii=False)
    elif isinstance(requested_voices, dict):
        updates["voices"] = json.dumps(requested_voices, ensure_ascii=False)
    if updates:
        db.update_book(bid, updates)
    return res


@router.put("/books/{bid}/settings")
def update_settings(bid: str, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    user = _ensure_generation_operator(row, user, payload)
    sd = json.loads(row["settings"] or "{}")
    if "ttsCorrect" in payload:
        sd["ttsCorrect"] = bool(payload["ttsCorrect"])
    db.update_book(bid, {"settings": json.dumps(sd, ensure_ascii=False)})
    return {"settings": sd}


# ---------- 章節版本歷史 / 作者統計（FE-017） ----------

def _ensure_book_owner(bid: str, user: dict):
    row = book_service.load_book(bid)
    book_service.ensure_can_manage(row, user)
    return row


@router.get("/books/{bid}/chapters/{seq}/revisions")
def list_revisions(bid: str, seq: int,
                   user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    return {"ok": True, "revisions": db.list_chapter_revisions(row["id"], seq)}


@router.post("/books/{bid}/chapters/{seq}/revisions/{revision_id}/restore")
def restore_revision(bid: str, seq: int, revision_id: int,
                     user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    rev = db.get_chapter_revision(revision_id)
    if not rev or rev["book_id"] != row["id"] or rev["seq"] != seq:
        raise exc.not_found("找不到該版本")
    ch = chapter_service.get_chapter(row, seq)
    res = chapter_service.update_chapter(row, ch, title=rev["title"], text=rev["text"],
                                         saved_by=user["id"] if not user.get("dev") else None)
    out = book_service.get_book_legacy(bid, user=user)
    out["changed"] = res["changed"]
    out["removedOutputs"] = res["removedOutputs"]
    out["revisionId"] = res["revisionId"]
    return out


@router.get("/books/{bid}/stats")
def author_book_stats(bid: str, user: Annotated[dict, Depends(require_author)]):
    """作者專屬：閱讀量／完成率／追蹤收藏／音訊產出。"""
    row = _ensure_book_owner(bid, user)
    store = db.query("SELECT book_id AS bid, substr(created_at,1,10) AS d, event_type, COUNT(*) AS n "
                     "FROM book_events WHERE book_id = ? GROUP BY substr(created_at,1,10), event_type",
                     (row["id"],))
    by_day = {}
    reads = completions = 0
    for s in store:
        by_day.setdefault(s["d"], {"reads": 0, "completions": 0, "clicks": 0})
        typ = s["event_type"]
        if typ in ("read", "audio"):
            reads += s["n"]
            by_day[s["d"]]["reads"] += s["n"]
        elif typ == "complete":
            completions += s["n"]
            by_day[s["d"]]["completions"] += s["n"]
        elif typ == "click":
            by_day[s["d"]]["clicks"] += s["n"]
    unique_readers = db.query_one("SELECT COUNT(DISTINCT user_id) AS c FROM book_events WHERE book_id=? AND event_type IN ('read','audio')", (row["id"],))["c"]
    ch_rows = db.query("SELECT seq, status, audio FROM chapters WHERE book_id=? ORDER BY seq", (row["id"],))
    ready = sum(1 for c in ch_rows if c["audio"] == "ready")
    total_ch = len(ch_rows)
    follows = db.query_one("SELECT COUNT(*) AS c FROM follows WHERE book_id=?", (row["id"],))["c"]
    favorites = db.query_one("SELECT COUNT(*) AS c FROM library_items WHERE book_id=? AND kind='favorite'", (row["id"],))["c"]
    return {
        "bid": bid,
        "reads": reads,
        "completions": completions,
        "completionRate": round(completions / reads, 3) if reads else 0,
        "uniqueReaders": unique_readers,
        "follows": follows,
        "favorites": favorites,
        "chapters": total_ch,
        "audioReady": ready,
        "audioRatio": round(ready / total_ch, 4) if total_ch else 0,
        "trend": [{"date": d, **v} for d, v in sorted(by_day.items())[-60:]],
    }


# ---------- V4 canonical analysis API ----------

def _ensure_ai_provider_or_404():
    provider = db.get_default_ai_provider()
    if not provider:
        raise HTTPException(503, "尚未設定可用的 AI provider，無法執行分析")
    return provider


def _ensure_generation_operator(row: dict, user: Optional[dict], payload: Optional[dict] = None):
    """Legacy generation endpoints are operator-only, never Author-only.

    Reviewers may use them only with an explicitly approved audiobook request;
    Admin retains the Phase 1 transitional operation capability.
    """
    user = _require_401(user)
    if policy.can_manage_provider_config(user):
        return user
    if not policy.can_review_content_request(user):
        raise exc.forbidden("生成操作必須由 Reviewer 或 Admin 執行")
    request_id = (payload or {}).get("contentRequestId")
    request = db.query_one(
        "SELECT id FROM content_requests WHERE id=? AND book_id=? AND request_type='audiobook' "
        "AND status='APPROVED' AND generation_authorized=1",
        (request_id, row["id"])) if request_id else None
    if not request:
        raise exc.forbidden("Reviewer 只能操作已核准的 audiobook 申請")
    return user


def _analysis_artifact(record: dict):
    if not record or record.get("status") != "ready" or not record.get("artifact_path"):
        return None
    import os
    from .. import settings as _s
    p = os.path.join(_s.ROOT_DIR, record["artifact_path"])
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _source_faithful_fallback_analysis(chapter: dict, record: dict, artifact: dict) -> dict:
    """舊版 v4 分段過期時，提供目前由原文產生的來源片段。

    已保存的 ready artifact 是 immutable，不因 deterministic segmentation
    contract 改變而改寫。若直接回傳舊 annotation，Reader 會繼續顯示過期的
    單字單位，因此公開 read model 改用來源片段，並明確不宣稱具有目前 AI
    annotation 或相符的 timing。
    """
    source = source_faithful.segment_source(chapter["text"])
    segments = []
    for span in source["segments"]:
        segment = dict(span)
        if span["type"] == "narration":
            segment.update({"speaker": "旁白", "speaker_id": "speaker:narrator"})
        else:
            segment.update({"speaker": None, "speaker_id": "speaker:unresolved"})
        segments.append(segment)
    return {
        "analysisId": record["id"],
        "status": "stale",
        "staleReason": "segmentation_version_outdated",
        "storedSegmentationVersion": artifact.get("segmentationVersion"),
        "segmentationVersion": source_faithful.SEGMENTATION_VERSION,
        "schemaVersion": source_faithful.SCHEMA_VERSION,
        "chapterKey": chapter["chapter_key"],
        "sourceTextHash": record["source_text_hash"],
        "characters": [],
        "speakers": [],
        "segments": segments,
        "legacySegments": [],
        "learning": {"vocab": [], "bilingual": []},
        "aiModel": record.get("ai_model"),
    }


def _analysis_requires_source_refresh(record: dict, artifact: dict) -> bool:
    """只對過期的 source-faithful v4 artifact 回傳 True。

    Schema v3 仍是支援的 compatibility projection。新的 v4 artifact 會帶有
    明確 segmentation version，因此缺少或較舊的版本都視為過期，不靜默當成
    最新資料呈現。
    """
    return bool(record and int(record.get("schema_version") or 0) == source_faithful.SCHEMA_VERSION
                and artifact
                and artifact.get("segmentationVersion") != source_faithful.SEGMENTATION_VERSION)


@router.post("/books/{bid}/chapters/{seq}/analysis")
def create_analysis(bid: str, seq: int, user: Annotated[Optional[dict], Depends(get_current_user)],
                    payload: Optional[dict] = None):
    row = book_service.load_book(bid)
    _ensure_generation_operator(row, user, payload)
    ch = db.get_chapter(row["id"], seq)
    if not ch:
        raise exc.not_found("找不到章節")

    # Idempotency guard: a double click, retrying browser request, or a second
    # tab must not create multiple analyses for the same chapter while one is
    # already queued/running.  Failed/terminal history remains retryable.
    active_jobs = [job for job in db.list_generation_jobs(500)
                   if job.get("book_id") == row["id"]
                   and job.get("chapter_id") == ch["id"]
                   and job.get("job_type") == "speaker_analysis"
                   and job.get("status") in ("pending", "running")
                   and job.get("analysis_id")]
    if active_jobs:
        active_job = max(active_jobs, key=lambda job: int(job.get("id") or 0))
        active_analysis = db.get_chapter_analysis(active_job["analysis_id"])
        if active_analysis and active_analysis.get("status") in ("queued", "running"):
            return {
                "analysisId": active_analysis["id"],
                "jobId": active_job["id"],
                "status": "queued" if active_job["status"] == "pending" else "running",
                "deduplicated": True,
                "chapterKey": ch["chapter_key"],
            }
    provider = _ensure_ai_provider_or_404()
    force = bool((payload or {}).get("force"))
    existing = db.get_ready_chapter_analysis(ch["id"], ch["text_hash"])
    if existing and not force:
        return {"analysisId": existing["id"], "status": existing["status"], "chapterKey": ch["chapter_key"]}
    analysis_id = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=ch["id"], source_text_hash=ch["text_hash"],
        analysis_profile="speaker", prompt_version=analyzer.ANALYSIS_PROMPT_VERSION,
        ai_provider_id=provider["id"], ai_model=provider["model"],
        ai_config_version=provider["config_version"], created_by=user["id"],
        schema_version=4,
    )
    analysis_svc.initialize_analysis_progress(
        analysis_id, analyzer.count_source_faithful_chunks(ch["text"]),
    )
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=ch["id"], analysis_id=analysis_id,
        provider=provider, requested_by=user["id"], payload={"bid": bid, "seq": seq},
    )
    return {"analysisId": analysis_id, "jobId": job_id, "status": "queued"}


@router.get("/books/{bid}/chapters/{seq}/analysis")
def get_analysis(bid: str, seq: int, user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    ch = db.get_chapter(row["id"], seq)
    if not ch:
        raise exc.not_found("找不到章節")
    record = db.get_ready_chapter_analysis(ch["id"], ch["text_hash"])
    artifact = _analysis_artifact(record) if record else None
    if record and artifact:
        if _analysis_requires_source_refresh(record, artifact):
            return {
                **_source_faithful_fallback_analysis(ch, record, artifact),
                "progress": analysis_svc.parse_analysis_progress(record),
            }
        compat = analysis_svc.legacy_compatibility_view(artifact)
        return {
            "analysisId": record["id"], "status": "ready",
            "schemaVersion": record["schema_version"], "chapterKey": ch["chapter_key"],
            "sourceTextHash": record["source_text_hash"],
            "characters": artifact.get("characters", []),
            "speakers": compat.get("speakers", []), "segments": artifact.get("segments", []),
            "legacySegments": compat.get("segments", []),
            "learning": artifact.get("learning", {}),
            "aiModel": record.get("ai_model"),
            "progress": analysis_svc.parse_analysis_progress(record),
        }
    result = {"analysisId": record["id"] if record else None,
              "status": record["status"] if record else "none",
              "progress": analysis_svc.parse_analysis_progress(record) if record else None}
    if record and record.get("status") == "failed":
        failure = analysis_svc.parse_failure_error(record.get("error"))
        failure["message"] = result["progress"].get("lastError") or "分析失敗"
        result["error"] = failure
    return result


@router.post("/books/{bid}/chapters/{seq}/analysis/cancel")
def cancel_analysis(bid: str, seq: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    """取消本章目前的 analysis job；歷史 job/analysis 保留供 audit。"""
    row = book_service.load_book(bid)
    _ensure_generation_operator(row, user)
    ch = db.get_chapter(row["id"], seq)
    if not ch:
        raise exc.not_found("找不到章節")
    job = db.get_active_analysis_job(ch["id"])
    if not job:
        raise exc.conflict("目前沒有可取消的分析", code="analysis_not_cancellable")
    if not analysis_svc.cancel_analysis_job(job["id"]):
        raise exc.conflict("分析已完成或已停止，無法取消", code="analysis_not_cancellable")
    return {
        "analysisId": job["analysis_id"],
        "jobId": job["id"],
        "status": "cancelled",
        "analysisStatus": "failed",
    }


@router.post("/books/{bid}/analysis/batch")
def create_analysis_batch(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    _ensure_generation_operator(row, user)
    provider = _ensure_ai_provider_or_404()
    if db.get_active_analysis_batch_job(row["id"]):
        raise exc.conflict("本書已有批次分析正在處理", code="analysis_batch_in_progress")
    job_id = db.create_analysis_job(
        "speaker_analysis_all", book_id=row["id"], chapter_id=None, analysis_id=None,
        provider=provider, requested_by=user["id"], payload={"bid": bid},
    )
    return {"jobId": job_id, "status": "queued"}


@router.post("/books/{bid}/analysis/batch/cancel")
def cancel_analysis_batch(bid: str, user: Annotated[Optional[dict], Depends(get_current_user)]):
    """取消本書目前的批次分析；已完成章節與歷史紀錄保留。"""
    row = book_service.load_book(bid)
    _ensure_generation_operator(row, user)
    job = db.get_active_analysis_batch_job(row["id"])
    if not job:
        raise exc.conflict("目前沒有可取消的批次分析", code="analysis_batch_not_cancellable")
    result = analysis_svc.cancel_analysis_batch_job(job["id"])
    if not result:
        raise exc.conflict("批次分析已完成或已停止，無法取消", code="analysis_batch_not_cancellable")
    return {
        "jobId": result["jobId"],
        "status": "cancelled",
        "cancelledAnalysisCount": result["cancelledAnalysisCount"],
    }


# ---------- V4 canonical audio generation API ----------

def _ensure_tts_provider_online():
    provider = db.get_active_tts_provider()
    if not provider:
        raise HTTPException(503, "尚未設定可用的 TTS provider，無法生成音訊")
    return provider


def _preflight_voices(provider: dict, book: dict, ch: dict, mode: str):
    """以與 worker 相同的規則解析本章會用到的聲線，檢查是否都在 provider catalog 內。

    - single：章節覆寫 > 書級 default_voice_id。
    - multi：各語者綁定聲線（未指定者以 en/_english 或 zh 預設 fallback）。
    任一缺失即 422，附產品化訊息；避免建立注定失敗的 job。
    """
    catalog = {v["voice_id"] for v in db.list_tts_provider_voices(provider["id"])}
    raw_voices = (book or {}).get("voices") or {}
    voices_map = raw_voices if isinstance(raw_voices, dict) else json.loads(raw_voices or "{}")
    if mode == c.AUDIO_MODE_MULTI:
        record = db.get_ready_chapter_analysis(ch["id"], ch["text_hash"])
        analysis = {"segments": []}
        if record and record.get("artifact_path"):
            p = os.path.join(settings.ROOT_DIR, record["artifact_path"])
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    artifact = json.load(f)
                analysis = analysis_svc.legacy_compatibility_view(artifact)
        # Reuse the worker's exact voice resolution, including language fallback.
        # Checking only explicit book mappings lets an unassigned speaker fall
        # through to the legacy Edge ID and fail much later in the worker.
        resolved_book = {**book, "voices": voices_map}
        used = tts.required_voice_ids(resolved_book, analysis)
        error = tts.format_missing_voice_error(resolved_book, analysis, catalog)
    else:
        eff = ag_service.effective_voice_id(book, ch) or ""
        if not eff:
            raise HTTPException(422, "請先選擇朗讀聲線，再生成音訊")
        used = {eff}
        missing = [voice for voice in used if voice not in catalog]
        error = ("選定的聲線不在目前朗讀服務的語者清單中：" + ", ".join(str(v) for v in missing)
                 + "。請重新選擇聲線，或請管理員重新測試 TTS 服務以更新語者清單") if missing else None
    if error:
        raise HTTPException(422, error)


@router.post("/books/{bid}/chapters/{seq}/audio-generations")
def create_audio_generation(bid: str, seq: int, payload: dict, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = book_service.load_book(bid)
    _ensure_generation_operator(row, user, payload)
    ch = db.get_chapter(row["id"], seq)
    if not ch:
        raise exc.not_found("找不到章節")
    _ensure_tts_provider_online()
    book = gp._build_book_dict(row)
    mode = payload.get("mode")
    if mode not in c.AUDIO_MODES:
        mode = ag_service.effective_mode(book, ch)
    voice_id = payload.get("voiceId")
    provider = db.get_active_tts_provider()
    if mode == c.AUDIO_MODE_MULTI:
        record = db.get_ready_chapter_analysis(ch["id"], ch["text_hash"])
        if not record:
            raise HTTPException(409, "multi 模式需要相符的 ready analysis")
    # preflight：以與 worker 相同的聲線解析規則驗證，缺失時立即回報產品化錯誤，
    # 不建立注定失敗的 job（避免 HTTP 400 / 靜默失敗）。
    _preflight_voices(provider, book, ch, mode)
    emotion_policy = payload.get("emotionPolicy", c.EMOTION_POLICY_BEST_EFFORT)
    expressive_snapshot = None
    if mode == c.AUDIO_MODE_MULTI:
        try:
            expressive_snapshot = gp.build_expressive_snapshot(book, ch, provider, emotion_policy)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    generation, created = ag_service.create_or_reuse_generation(
        book=book, chapter=ch, source_text_hash=ch["text_hash"], mode=mode, voice_id=voice_id,
        analysis_id=None, tts_provider=provider,
        emotion_policy=emotion_policy,
        requested_by=user["id"],
        expressive_snapshot=expressive_snapshot,
        force_new=bool(payload.get("force")),
    )
    job_type = "audio_multi" if mode == c.AUDIO_MODE_MULTI else "audio_single"
    job_id = db.create_generation_job(
        job_type, book_id=row["id"], chapter_id=ch["id"], requested_by=user["id"],
        provider=provider, service_type="TTS", source_revision=ch["text_hash"],
        source_text_hash=ch["text_hash"], voice_snapshot={"voiceId": voice_id, "mode": mode},
        configuration_snapshot={"generationKey": generation.get("generation_key"),
                                "emotionPolicy": generation["emotion_policy"],
                                "audioSettingsVersion": row.get("audio_settings_version") or 1},
        payload={"bid": bid, "seq": seq, "generationId": generation["id"],
                 "emotionPolicy": generation["emotion_policy"]},
    )
    return {"generationId": generation["id"], "jobId": job_id,
            "status": generation["status"] if not created else "queued"}


@router.get("/books/{bid}/chapters/{seq}/audio-generations")
def list_audio_generations(bid: str, seq: int, user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    ch = db.get_chapter(row["id"], seq)
    if not ch:
        raise exc.not_found("找不到章節")
    items = []
    for g in db.list_audio_generations(ch["id"]):
        items.append({
            "generationId": g["id"], "mode": g["mode"], "status": g["status"],
            "emotionPolicy": g["emotion_policy"], "createdAt": g["created_at"],
            "audioUrl": f"/api/books/{bid}/audio/{seq}" if g["status"] == "ready" else None,
        })
    return {"items": items}


@router.get("/books/{bid}/chapters/{seq}/audio-generations/{generation_id}")
def get_audio_generation(bid: str, seq: int, generation_id: int, user: Annotated[dict, Depends(require_author)]):
    row = _ensure_book_owner(bid, user)
    g = db.get_audio_generation(generation_id)
    if not g or g["book_id"] != row["id"]:
        raise exc.not_found("找不到 generation")
    return {
        "generationId": g["id"], "mode": g["mode"], "status": g["status"],
        "emotionPolicy": g["emotion_policy"], "error": g["error"], "createdAt": g["created_at"],
        "finishedAt": g["finished_at"],
        "audioUrl": f"/api/books/{bid}/audio/{seq}" if g["status"] == "ready" else None,
    }


@router.post("/books/{bid}/audio-generations/batch")
def create_audio_generation_batch(bid: str, payload: Optional[dict] = None,
                                  user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    row = book_service.load_book(bid)
    _ensure_generation_operator(row, user, payload)
    _ensure_tts_provider_online()
    provider = db.get_active_tts_provider()
    batch_revision = request_service.calculate_revision(row, "audiobook", payload or {})
    batch_text_hash = orchestration.book_text_revision(row)
    job_id = db.create_generation_job(
        "audio_generate_all", book_id=row["id"], chapter_id=None, requested_by=user["id"],
        provider=provider, service_type="TTS", source_revision=batch_revision,
        source_text_hash=batch_text_hash,
        configuration_snapshot={"operationType": "batch", "audioSettingsVersion": row.get("audio_settings_version") or 1},
        payload={"bid": bid},
    )
    return {"jobId": job_id, "status": "queued"}
