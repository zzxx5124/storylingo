"""Account-facing profile 與 public Author Profile 的集中服務層。"""
import io
import os
import re
import unicodedata
import uuid

from PIL import Image

from .. import db, settings

MAX_AVATAR_BYTES = 2 * 1024 * 1024
MIN_AVATAR_DIMENSION = 64
MAX_AVATAR_DIMENSION = 1024
ALLOWED_AVATAR_MIME = {"image/jpeg", "image/png", "image/webp"}
_SLUG_RE = re.compile(r"[^\w\-\u0080-\uffff]+", re.UNICODE)
_HTML_MARKUP_RE = re.compile(r"</?\s*[A-Za-z][^>]*>|<!|<\?")


def plain_text(value, *, field: str, maximum: int, minimum: int = 0) -> str:
    value = "" if value is None else str(value)
    value = unicodedata.normalize("NFC", value)
    value = "".join(ch for ch in value if ch in "\n\r\t" or ord(ch) >= 32).strip()
    if _HTML_MARKUP_RE.search(value):
        raise ValueError(f"{field}不得包含 HTML 標記")
    length = len(value)
    if length < minimum or length > maximum:
        raise ValueError(f"{field}需為 {minimum} 至 {maximum} 個字元")
    return value


def validate_public_display_name(value) -> str:
    return plain_text(value, field="公開顯示名稱", maximum=50, minimum=1)


def validate_author_name(value) -> str:
    return plain_text(value, field="作者顯示名稱", maximum=80, minimum=1)


def validate_bio(value) -> str:
    return plain_text(value, field="作者簡介", maximum=1000)


def slugify(value: str) -> str:
    value = plain_text(value, field="作者名稱", maximum=80, minimum=1).casefold()
    value = re.sub(r"\s+", "-", value)
    value = _SLUG_RE.sub("-", value).strip("-_")
    return value[:100] or "author"


def unique_slug(value: str, *, exclude_id: int = None) -> str:
    base = slugify(value)
    candidate = base
    suffix = 2
    while True:
        row = db.get_author_profile_by_slug(candidate)
        if not row or (exclude_id is not None and row["id"] == exclude_id):
            return candidate
        candidate = f"{base[:max(1, 100 - len(str(suffix)) - 1)]}-{suffix}"
        suffix += 1


def resolve_owned_author_profile(owner_id: int, profile_id) -> int | None:
    """Validate a book attribution against the Account that owns the book."""
    if profile_id in (None, "", 0, "0"):
        return None
    try:
        profile_id = int(profile_id)
    except (TypeError, ValueError) as error:
        raise ValueError("作者 profile 不正確") from error
    profile = db.get_author_profile(profile_id)
    if not profile or profile.get("owner_id") != owner_id:
        raise ValueError("只能選擇自己帳號的作者 profile")
    if profile.get("status") != "active":
        raise ValueError("停用或已刪除的作者 profile 不可用於新作品")
    return profile_id


def _avatar_url(profile: dict) -> str | None:
    if not profile or not profile.get("avatar_path"):
        return None
    return f"/api/media/author-avatar/{profile['public_id']}"


def serialize_public_profile(profile: dict) -> dict:
    return {
        "displayName": profile.get("display_name") or "",
        "bio": profile.get("bio") or "",
        "avatar": "/api/auth/profile/avatar" if profile.get("avatar_path") else None,
        "updatedAt": profile.get("updated_at"),
    }


def serialize_author(profile: dict, *, include_bio: bool = True, owner: dict | None = None) -> dict:
    if not profile:
        return {"displayName": "匿名作者", "legacy": True, "link": None}
    status = profile.get("status") or "active"
    owner = owner if owner is not None else (db.get_user_by_id(profile["owner_id"]) if profile.get("owner_id") else None)
    if owner and owner.get("account_status") == "deleted":
        status = "tombstone"
    elif owner and owner.get("account_status") == "disabled" and status == "active":
        status = "suspended"
    result = {
        "publicId": profile["public_id"],
        "slug": profile["slug"],
        "displayName": profile["display_name"],
        "avatar": _avatar_url(profile),
        "status": status,
        "link": f"/authors/{profile['slug']}",
        "canonicalLink": f"/authors/id/{profile['public_id']}",
        "legacy": False,
    }
    if include_bio:
        result["bio"] = profile.get("bio") or ""
    return result


def author_for_book(row: dict) -> dict:
    profile_id = row.get("author_profile_id")
    if profile_id:
        profile = db.get_author_profile(profile_id)
        if profile:
            return serialize_author(profile)
    # This is deliberately a text-only historical projection. It has no link and
    # never invents a public Author Profile from the Account owner id.
    return {
        "displayName": row.get("legacy_author_name") or "匿名作者",
        "legacy": True,
        "link": None,
    }


def author_projections_for_books(rows: list[dict]) -> dict:
    """Batch the public author projection used by card collection endpoints."""
    profile_ids = [row.get("author_profile_id") for row in rows if row.get("author_profile_id")]
    profiles = db.author_profiles_by_ids(profile_ids)
    result = {}
    for row in rows:
        profile = profiles.get(row.get("author_profile_id"))
        if profile:
            result[row["id"]] = serialize_author(
                profile, owner={"account_status": profile.get("owner_account_status")})
        else:
            result[row["id"]] = {
                "displayName": row.get("legacy_author_name") or "匿名作者",
                "legacy": True,
                "link": None,
            }
    return result


def author_page(slug: str, *, page: int = 1, page_size: int = 20):
    profile = db.get_author_profile_by_identifier(slug)
    if not profile:
        return None
    predicate = "author_profile_id=? AND status='approved' AND published_at IS NOT NULL"
    total = db.query_one(f"SELECT COUNT(*) AS total FROM books WHERE {predicate}", (profile["id"],))["total"]
    works = db.query(
        f"SELECT * FROM books WHERE {predicate} "
        "ORDER BY COALESCE(published_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
        (profile["id"], page_size, (page - 1) * page_size))
    author = serialize_author(profile)
    return {"author": author, "works": works, "items": works, "total": total,
            "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
            "has_next": page < ((total + page_size - 1) // page_size if total else 0),
            "has_prev": page > 1}


def avatar_file(public_id: str):
    profile = db.get_author_profile_by_public_id(public_id)
    return stored_avatar_file(profile)


def stored_avatar_file(profile: dict):
    if not profile or not profile.get("avatar_path"):
        return None
    stored = str(profile["avatar_path"]).replace("\\", "/")
    filename = os.path.basename(stored)
    if stored != "storage/profiles/" + filename or not filename.startswith("avatar-") or not filename.endswith(".jpg"):
        return None
    path = os.path.abspath(os.path.join(settings.PROFILE_DIR, filename))
    root = os.path.abspath(settings.PROFILE_DIR)
    if os.path.commonpath([path, root]) != root or not os.path.isfile(path):
        return None
    return path


def save_avatar(raw: bytes, content_type: str = "") -> str:
    if len(raw) > MAX_AVATAR_BYTES:
        raise ValueError("頭像檔案最大 2 MB")
    if content_type.lower() not in ALLOWED_AVATAR_MIME:
        raise ValueError("頭像僅接受 JPEG、PNG 或 WebP")
    try:
        probe = Image.open(io.BytesIO(raw))
        actual = (probe.format or "").upper()
        if actual not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("頭像格式不支援")
        expected_mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[actual]
        if content_type.lower() != expected_mime:
            raise ValueError("頭像 MIME 與實際格式不一致")
        probe.verify()
        image = Image.open(io.BytesIO(raw))
        if (min(image.width, image.height) < MIN_AVATAR_DIMENSION or
                max(image.width, image.height) > MAX_AVATAR_DIMENSION):
            raise ValueError("頭像尺寸需介於 64 至 1024 像素")
        if image.width * image.height > MAX_AVATAR_DIMENSION * MAX_AVATAR_DIMENSION:
            raise ValueError("頭像尺寸過大")
        image = image.convert("RGB")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("頭像內容無效") from error
    os.makedirs(settings.PROFILE_DIR, exist_ok=True)
    filename = f"avatar-{uuid.uuid4().hex}.jpg"
    path = os.path.join(settings.PROFILE_DIR, filename)
    image.save(path, format="JPEG", quality=86, optimize=True)
    return os.path.join("storage", "profiles", filename).replace("\\", "/")
