"""公開小說平台 API：首頁、搜尋、閱讀、書架、進度、留言與通知。"""
import hashlib
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import db, exceptions as exc, settings
from ..pagination import page_result, parse_pagination
from ..security import get_current_user, require_user
from ..services import book_service, notifications as notification_service, profile as profile_service

router = APIRouter(prefix="/api", tags=["platform"])


@router.get("/tts/spec")
def download_tts_spec():
    """Download the public remote TTS provider contract."""
    path = os.path.join(settings.ROOT_DIR, "docs", "tts-provider-api.md")
    return FileResponse(path, media_type="text/markdown", filename="tts-provider-api.md")


@router.get("/media/banner/{filename}")
def banner_image(filename: str):
    """公開輪播圖片（管理員上傳產生，含尺寸驗證）。"""
    safe = os.path.basename(filename)
    if safe != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise exc.bad_request("檔名不合法")
    if not safe.endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise exc.bad_request("檔名不合法")
    p = os.path.join(settings.BANNER_DIR, safe)
    if not os.path.exists(p):
        raise exc.not_found("找不到圖片")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/media/author-avatar/{public_id}")
def author_avatar(public_id: str):
    path = profile_service.avatar_file(public_id)
    if not path:
        raise exc.not_found("找不到作者頭像")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def _member(user: dict):
    if not user or user.get("dev"):
        raise exc.unauthorized("此功能需要登入")
    return user


def _book_row_or_404(bid: str):
    row = db.get_book_row(bid)
    if not row:
        raise exc.not_found("找不到書")
    return row


def _card(row: dict):
    return book_service.book_cards([row], public=True)[0]


def _safe_rankings(items: list[dict]) -> list[dict]:
    safe_items = []
    for item in items:
        item = dict(item)
        book = db.get_book_row(item.get("bid")) if item.get("bid") else None
        item.pop("owner", None)
        if book:
            item["author"] = profile_service.author_for_book(book)
            item["owner"] = item["author"]["displayName"]
        else:
            item["author"] = {"displayName": "匿名作者", "legacy": True, "link": None}
            item["owner"] = "匿名作者"
        safe_items.append(item)
    return safe_items


@router.get("/home")
def home(user: Annotated[Optional[dict], Depends(get_current_user)]):
    latest = book_service.list_books(None, sort="updated", page=1, page_size=12, public=True)
    completed = book_service.list_books(None, serial="完結", sort="updated", page=1, page_size=12, public=True)
    popular = db.list_rankings("hot", "all", limit=10)
    if not popular:
        db.refresh_rankings()
        popular = db.list_rankings("hot", "all", limit=10)
    continue_reading = []
    if user and not user.get("dev"):
        for row in db.list_history(user["id"], 8):
            if not book_service.is_public(row):
                continue
            card = _card(row)
            saved = db.get_progress(user["id"], row["id"])
            chapter = db.get_chapter(row["id"], saved["chapter_seq"]) if saved else None
            # Resume is a public-reader projection, even when the account owns the book.
            if chapter and book_service.can_view_chapter(row, chapter, None):
                card["readingProgress"] = {
                    "chapterSeq": chapter["seq"], "chapterTitle": chapter["title"],
                    "percent": saved["percent"], "lastMode": saved["last_mode"],
                }
            continue_reading.append(card)
    return {
        "banners": db.list_banners(),
        "categories": db.list_categories(),
        "continueReading": continue_reading,
        "rankings": _safe_rankings(popular),
        "latest": latest.get("items", []),
        "completed": completed.get("items", []),
    }


@router.get("/banners")
def banners():
    return {"items": db.list_banners()}


@router.post("/banners/{banner_id}/impression")
def banner_impression(banner_id: int):
    db.execute("UPDATE banners SET impression_count=impression_count+1 WHERE id=?", (banner_id,))
    return {"ok": True}


@router.post("/banners/{banner_id}/click")
def banner_click(banner_id: int):
    db.execute("UPDATE banners SET click_count=click_count+1 WHERE id=?", (banner_id,))
    return {"ok": True}


@router.get("/genres")
def genres():
    return {"items": db.list_categories()}


@router.get("/rankings")
def rankings(kind: str = "hot", window: str = "all", category_id: Optional[int] = None, limit: int = 20):
    allowed_kind = {"hot", "new", "rising", "completed", "audio"}
    allowed_window = {"day", "7d", "30d", "all"}
    if kind not in allowed_kind:
        raise exc.bad_request("不支援的排行榜類型")
    if window not in allowed_window:
        raise exc.bad_request("不支援的排行榜區間")
    items = db.list_rankings(kind, window, category_id, limit)
    if not items and kind == "hot" and window == "all":
        db.refresh_rankings()
        items = db.list_rankings(kind, window, category_id, limit)
    if not items and kind in {"new", "completed", "audio", "rising"}:
        sort = "created" if kind == "new" else "updated"
        serial = "完結" if kind == "completed" else None
        cards = book_service.list_books(None, category_id=category_id, serial=serial, sort=sort, page=1, page_size=max(1, min(limit, 100)), public=True).get("items", [])
        if kind == "audio":
            cards = [card for card in cards if card["audioReadyCount"] > 0]
        items = [{"rank": index + 1, "bid": card["id"], "title": card["title"], "synopsis": card["synopsis"],
                  "cover_path": card["cover"], "serial": card["serial"], "owner": card["owner"],
                  "category_name": card["litCount"], "score": 0} for index, card in enumerate(cards[:limit])]
    return {"kind": kind, "window": window, "items": _safe_rankings(items)}


@router.get("/search")
def search(
    q: str = "",
    category_id: Optional[int] = None,
    language: Optional[str] = None,
    serial: Optional[str] = None,
    has_audio: Optional[bool] = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 20,
    user: Annotated[Optional[dict], Depends(get_current_user)] = None,
):
    try:
        page, page_size = parse_pagination(page, page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    if sort not in {"relevance", "updated", "created", "chars"}:
        raise exc.bad_request("不支援的搜尋排序")
    if language and language not in settings.CATEGORIES:
        raise exc.bad_request("不支援的語言類型")
    if serial and serial not in {"連載", "完結"}:
        raise exc.bad_request("不支援的連載狀態")
    q = q.strip()[:80]
    if not q:
        return book_service.list_books(user, category_id=category_id, language=language, serial=serial,
                                       sort="updated", page=page, page_size=page_size,
                                       public=True, has_audio=has_audio)
    result = book_service.list_books(user, q=q, category_id=category_id, language=language, serial=serial,
                                     sort="updated" if sort == "relevance" else sort,
                                     page=page, page_size=page_size, public=True, has_audio=has_audio)
    return result


@router.get("/audiobooks")
def audiobooks(
    q: str = "",
    category_id: Optional[str] = None,
    language: Optional[str] = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 20,
):
    """公開有聲書 discovery；只回傳目前可由既有 reader 播放的作品。"""
    try:
        result = book_service.list_audiobooks(
            q=q, category_id=category_id, language=language,
            sort=sort, page=page, page_size=page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/search/suggest")
def search_suggest(q: str = ""):
    q = q.strip()[:50]
    if len(q) < 2:
        return {"items": []}
    like = f"%{q}%"
    rows = db.query("SELECT bid, title FROM books WHERE status='approved' AND published_at IS NOT NULL AND (title LIKE ? OR synopsis LIKE ? OR tags LIKE ?) ORDER BY updated_at DESC LIMIT 8", (like, like, like))
    return {"items": rows}


@router.get("/books/{bid}/read/{seq}")
def read_chapter(bid: str, seq: int, user: Annotated[Optional[dict], Depends(get_current_user)]):
    row = _book_row_or_404(bid)
    if not book_service.can_view_content(row, user):
        raise exc.not_found("找不到章節")
    chapter = db.get_chapter(row["id"], seq)
    if not book_service.can_view_chapter(row, chapter, user):
        raise exc.not_found("找不到章節")
    chapters = db.list_chapters(row["id"])
    public_chapters = [c for c in chapters if c.get("publish_status", "published") == "published"]
    index = next((i for i, c in enumerate(public_chapters) if c["seq"] == seq), -1)
    return {
        "book": _card(row),
        "chapter": {"seq": chapter["seq"], "title": chapter["title"], "text": chapter["text"], "chars": chapter["chars"],
                    "audio": book_service.chapter_audio_state(chapter)},
        "navigation": {
            "previous": public_chapters[index - 1]["seq"] if index > 0 else None,
            "next": public_chapters[index + 1]["seq"] if 0 <= index < len(public_chapters) - 1 else None,
            "total": len(public_chapters),
        },
    }


@router.get("/books/{bid}/recommendations")
def recommendations(bid: str, limit: int = 8):
    row = _book_row_or_404(bid)
    if not book_service.is_public(row):
        raise exc.not_found("找不到書")
    rows = db.query("SELECT b.* FROM books b WHERE b.id<>? AND b.status='approved' AND b.published_at IS NOT NULL AND (b.category_id=? OR b.category=? OR b.tags LIKE ?) ORDER BY b.updated_at DESC LIMIT ?",
                    (row["id"], row["category_id"], row["category"], f"%{row['title'][:20]}%", max(1, min(limit, 20))))
    return {"items": [_card(item) for item in rows]}


@router.post("/books/{bid}/view")
def book_view(bid: str, request: Request, payload: dict = None,
              user: Annotated[Optional[dict], Depends(get_current_user)] = None):
    row = _book_row_or_404(bid)
    if not book_service.can_view_content(row, user):
        raise exc.not_found("找不到書")
    payload = payload or {}
    raw_key = f"{request.client.host if request.client else 'unknown'}:{request.headers.get('user-agent', '')}"
    session_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
    db.add_event(row["id"], payload.get("eventType", "read"), user["id"] if user and not user.get("dev") else None,
                 session_key, payload.get("chapterSeq"), int(payload.get("duration", 0) or 0))
    return {"ok": True}


@router.get("/me/library")
def library(kind: Optional[str] = None, page: int = 1, page_size: int = 20,
            user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    try:
        page, page_size = parse_pagination(page, page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    if kind not in (None, "favorite", "followed"):
        raise exc.bad_request("不支援的書架類型")
    rows, total = db.list_library_page(user["id"], kind=kind, page=page, page_size=page_size)
    return JSONResponse(page_result(book_service.book_cards(rows, public=True), total=total, page=page, page_size=page_size),
                        headers=_PRIVATE_COLLECTION_HEADERS)


@router.post("/books/{bid}/favorite")
def favorite(bid: str, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    row = _book_row_or_404(bid)
    if not book_service.is_public(row):
        raise exc.not_found("找不到書")
    db.add_library_item(user["id"], row["id"], "favorite")
    return {"ok": True, "favorite": True}


@router.delete("/books/{bid}/favorite")
def unfavorite(bid: str, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    row = _book_row_or_404(bid)
    if not book_service.is_public(row):
        raise exc.not_found("找不到書")
    db.remove_library_item(user["id"], row["id"], "favorite")
    return {"ok": True, "favorite": False}


@router.get("/me/progress")
def progress(user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    return {"items": db.get_progress(user["id"])}


@router.put("/me/progress")
def save_progress(payload: dict, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    row = _book_row_or_404(payload.get("bookId", ""))
    db.save_progress(user["id"], row["id"], int(payload.get("chapterSeq", 0)), float(payload.get("position", 0)), float(payload.get("percent", 0)),
                     float(payload.get("audioPositionSeconds", 0)), str(payload.get("lastMode", "read")))
    return {"ok": True}


@router.get("/me/history")
def history(page: int = 1, page_size: int = 20, user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    try:
        page, page_size = parse_pagination(page, page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    rows, total = db.list_history_page(user["id"], page=page, page_size=page_size)
    items = [card | {"lastChapterSeq": row["last_chapter_seq"], "lastReadAt": row["last_read_at"]}
             for row, card in zip(rows, book_service.book_cards(rows, public=True))]
    return JSONResponse(page_result(items, total=total, page=page, page_size=page_size),
                        headers=_PRIVATE_COLLECTION_HEADERS)


@router.get("/me/bookmarks")
def bookmarks(book_id: Optional[str] = None, page: int = 1, page_size: int = 20,
              user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    row = _book_row_or_404(book_id) if book_id else None
    try:
        page, page_size = parse_pagination(page, page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    rows, total = db.list_bookmarks_page(user["id"], book_id=row["id"] if row else None,
                                         page=page, page_size=page_size)
    return JSONResponse(page_result(rows, total=total, page=page, page_size=page_size),
                        headers=_PRIVATE_COLLECTION_HEADERS)


@router.post("/me/bookmarks")
def create_bookmark(payload: dict, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    row = _book_row_or_404(payload.get("bookId", ""))
    bookmark_id = db.add_bookmark(user["id"], row["id"], int(payload.get("chapterSeq", 0)), float(payload.get("position", 0)), payload.get("note", ""))
    return {"id": bookmark_id}


_PRIVATE_NOTIFICATION_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Cookie",
}
_PRIVATE_COLLECTION_HEADERS = _PRIVATE_NOTIFICATION_HEADERS


@router.get("/notifications")
def notifications(page: int = 1, page_size: int = 20, filter: str = "all", category: str = "",
                  unread_only: bool | None = None,
                  user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    if unread_only:
        filter = "unread"
    try:
        result = notification_service.list_notifications(user["id"], page=page, page_size=page_size,
                                                          filter_name=filter, category=category)
    except (ValueError, TypeError) as error:
        raise exc.bad_request(str(error))
    return JSONResponse(result, headers=_PRIVATE_NOTIFICATION_HEADERS)


@router.get("/notifications/unread-count")
def notification_unread_count(user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    return JSONResponse({"count": notification_service.unread_count(user["id"])},
                        headers=_PRIVATE_NOTIFICATION_HEADERS)


@router.post("/notifications/{notification_id}/read")
def notification_read(notification_id: int, user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    notification_service.mark_read(user["id"], notification_id)
    return JSONResponse({"ok": True}, headers=_PRIVATE_NOTIFICATION_HEADERS)


@router.post("/notifications/read")
def notifications_read(payload: dict = None, user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    notification_id = (payload or {}).get("id")
    if notification_id is not None:
        notification_service.mark_read(user["id"], notification_id)
    else:
        notification_service.mark_all_read(user["id"])
    return JSONResponse({"ok": True}, headers=_PRIVATE_NOTIFICATION_HEADERS)


@router.post("/notifications/read-all")
def notification_read_all(user: Annotated[dict, Depends(require_user)] = None):
    user = _member(user)
    notification_service.mark_all_read(user["id"])
    return JSONResponse({"ok": True}, headers=_PRIVATE_NOTIFICATION_HEADERS)


@router.get("/books/{bid}/comments")
def list_comments(bid: str, chapter_seq: Optional[int] = None):
    row = _book_row_or_404(bid)
    if not book_service.is_public(row):
        raise exc.not_found("找不到書")
    if chapter_seq is None:
        rows = db.query(
            "SELECT c.id, c.chapter_seq, c.body, c.spoiler, c.created_at "
            "FROM comments c WHERE c.book_id=? AND c.status='published' "
            "AND (c.chapter_seq IS NULL OR EXISTS (SELECT 1 FROM chapters ch "
            "WHERE ch.book_id=c.book_id AND ch.seq=c.chapter_seq AND ch.publish_status='published')) "
            "ORDER BY c.created_at DESC LIMIT 100", (row["id"],))
    else:
        chapter = db.get_chapter(row["id"], chapter_seq)
        if not chapter or chapter.get("publish_status", "published") != "published":
            raise exc.not_found("找不到章節")
        rows = db.query(
            "SELECT c.id, c.chapter_seq, c.body, c.spoiler, c.created_at "
            "FROM comments c WHERE c.book_id=? AND c.chapter_seq=? AND c.status='published' "
            "ORDER BY c.created_at DESC LIMIT 100", (row["id"], chapter_seq))
    for comment in rows:
        comment["displayName"] = "讀者"
    return {"items": rows}


@router.post("/books/{bid}/comments")
def create_comment(bid: str, payload: dict, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    row = _book_row_or_404(bid)
    if not book_service.is_public(row):
        raise exc.not_found("找不到書")
    chapter_seq = payload.get("chapterSeq")
    if chapter_seq is not None:
        try:
            chapter_seq = int(chapter_seq)
        except (TypeError, ValueError) as error:
            raise exc.bad_request("章節編號不正確") from error
        chapter = db.get_chapter(row["id"], chapter_seq)
        if not chapter or chapter.get("publish_status", "published") != "published":
            raise exc.not_found("找不到章節")
    body = (payload.get("body") or "").strip()
    if len(body) < 1 or len(body) > 2000:
        raise exc.bad_request("留言需為 1 至 2000 字")
    now = db.ts()
    cur = db.execute("INSERT INTO comments(user_id, book_id, chapter_seq, body, spoiler, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                     (user["id"], row["id"], chapter_seq, body, 1 if payload.get("spoiler") else 0, now, now))
    return {"id": cur.lastrowid}


@router.post("/reports")
def create_report(payload: dict, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    target_type = payload.get("targetType")
    target_id = payload.get("targetId")
    reason = (payload.get("reason") or "").strip()
    if target_type not in ("book", "chapter", "comment") or not target_id or not reason:
        raise exc.bad_request("檢舉資料不完整")
    cur = db.execute("INSERT INTO reports(reporter_id, target_type, target_id, reason, created_at) VALUES(?, ?, ?, ?, ?)",
                     (user["id"], target_type, int(target_id), reason[:500], db.ts()))
    return {"id": cur.lastrowid, "status": "open"}


@router.post("/authors/apply")
def apply_author(payload: dict, user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    pen_name = (payload.get("penName") or "").strip()
    bio = (payload.get("bio") or "").strip()
    try:
        pen_name = profile_service.validate_author_name(pen_name)
        bio = profile_service.validate_bio(bio)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    if not payload.get("rightsConfirmed"):
        raise exc.bad_request("請先確認作品權利聲明")
    existing = db.query_one("SELECT * FROM author_applications WHERE user_id=? AND status IN ('pending', 'approved') ORDER BY id DESC LIMIT 1", (user["id"],))
    if existing:
        return {"id": existing["id"], "status": existing["status"]}
    now = db.ts()
    cur = db.execute("INSERT INTO author_applications(user_id, pen_name, bio, rights_confirmed, created_at, updated_at) VALUES(?, ?, ?, 1, ?, ?)",
                     (user["id"], pen_name, bio, now, now))
    return {"id": cur.lastrowid, "status": "pending"}


@router.get("/authors/application")
def author_application(user: Annotated[dict, Depends(require_user)]):
    user = _member(user)
    application = db.query_one("SELECT * FROM author_applications WHERE user_id=? ORDER BY id DESC LIMIT 1", (user["id"],))
    return {"application": application}


@router.get("/authors/{slug}")
def public_author(slug: str, page: int = 1, page_size: int = 20):
    if not slug or len(slug) > 100:
        raise exc.not_found("找不到作者")
    try:
        page, page_size = parse_pagination(page, page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    result = profile_service.author_page(slug, page=page, page_size=page_size)
    if not result:
        raise exc.not_found("找不到作者")
    items = book_service.book_cards(result["items"], public=True)
    return {
        "author": result["author"], "works": items, "items": items,
        "page": result["page"], "page_size": result["page_size"],
        "total": result["total"], "total_pages": result["total_pages"],
        "has_next": result["has_next"], "has_prev": result["has_prev"],
    }


@router.get("/authors/id/{public_id}")
def public_author_by_id(public_id: str, page: int = 1, page_size: int = 20):
    if not public_id or len(public_id) > 100:
        raise exc.not_found("找不到作者")
    try:
        page, page_size = parse_pagination(page, page_size)
    except ValueError as error:
        raise exc.bad_request(str(error)) from error
    result = profile_service.author_page(public_id, page=page, page_size=page_size)
    if not result:
        raise exc.not_found("找不到作者")
    items = book_service.book_cards(result["items"], public=True)
    return {
        "author": result["author"], "works": items, "items": items,
        "page": result["page"], "page_size": result["page_size"],
        "total": result["total"], "total_pages": result["total_pages"],
        "has_next": result["has_next"], "has_prev": result["has_prev"],
    }
