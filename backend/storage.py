"""書本資料與檔案快取管理。"""
import json
import os
import threading
import time
import uuid

from . import settings

_lock = threading.Lock()

# 章節狀態
ST_PENDING = "pending"      # 尚未分析
ST_ANALYZING = "analyzing"
ST_ANALYZED = "analyzed"
ST_ERROR = "error"

# 音訊狀態
AU_NONE = "none"
AU_GENERATING = "generating"
AU_READY = "ready"


def book_dir(book_id: str) -> str:
    return os.path.join(settings.BOOKS_DIR, book_id)


def chapter_dir(book_id: str) -> str:
    return os.path.join(book_dir(book_id), "chapters")


def audio_dir(book_id: str) -> str:
    return os.path.join(book_dir(book_id), "audio")


def source_path(book_id: str) -> str:
    return os.path.join(book_dir(book_id), "source.txt")


def cover_dir(book_id: str) -> str:
    return os.path.join(book_dir(book_id), "cover")


def cover_path(book_id: str) -> str:
    return os.path.join(cover_dir(book_id), "cover.jpg")


def meta_path(book_id: str) -> str:
    return os.path.join(book_dir(book_id), "meta.json")


def chapter_json_path(book_id: str, seq: int) -> str:
    return os.path.join(chapter_dir(book_id), f"{seq:04d}.json")


def chapter_audio_path(book_id: str, seq: int) -> str:
    return os.path.join(audio_dir(book_id), f"{seq:04d}.mp3")


def chapter_timing_path(book_id: str, seq: int) -> str:
    return os.path.join(audio_dir(book_id), f"{seq:04d}.timing.json")


def analysis_dir(book_id: str) -> str:
    return os.path.join(book_dir(book_id), "analyses")


def analysis_artifact_path(book_id: str, analysis_id: int) -> str:
    """以 analysis record identity 為單位的 artifact 相對路徑。"""
    return f"storage/books/{book_id}/analyses/{int(analysis_id)}.json"


def generation_dir(book_id: str, generation_id: int) -> str:
    return os.path.join(book_dir(book_id), "generations", str(int(generation_id)))


def generation_audio_path(book_id: str, generation_id: int) -> str:
    """以 generation record identity 為單位的音訊相對路徑。"""
    return f"storage/books/{book_id}/generations/{int(generation_id)}/audio.mp3"


def generation_timing_path(book_id: str, generation_id: int) -> str:
    return f"storage/books/{book_id}/generations/{int(generation_id)}/timing.json"


def new_book_id() -> str:
    return f"b{int(time.time())}{uuid.uuid4().hex[:6]}"


def load_meta(book_id: str):
    p = meta_path(book_id)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_book(book: dict) -> dict:
    """補全書本 meta 的新版欄位（分類／難度），缺省給向後相容預設值，不寫回磁碟。

    舊書沒有分類欄位：預設 vocab（保留原本「中文＋單字」行為），不觸發重分析。
    """
    cat = book.get("category")
    if cat not in settings.CATEGORIES:
        cat = settings.CAT_VOCAB
        book["category"] = cat
    if "categories" not in book or not isinstance(book["categories"], list) or not book["categories"]:
        book["categories"] = [cat]
    if not book.get("vocabLevel"):
        book["vocabLevel"] = "AUTO"
    if "settings" not in book or not isinstance(book.get("settings"), dict):
        book["settings"] = {}
    return book


def save_meta(meta: dict):
    bd = book_dir(meta["id"])
    os.makedirs(bd, exist_ok=True)
    with _lock:
        tmp = meta_path(meta["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, meta_path(meta["id"]))


def list_books():
    out = []
    if not os.path.isdir(settings.BOOKS_DIR):
        return out
    for name in os.listdir(settings.BOOKS_DIR):
        p = os.path.join(settings.BOOKS_DIR, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "meta.json")):
            m = load_meta(name)
            if m:
                out.append(normalize_book(m))
    out.sort(key=lambda m: m.get("created", ""), reverse=True)
    return out


def delete_book(book_id: str):
    import shutil
    d = book_dir(book_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


def get_chapter(book: dict, seq: int):
    for c in book.get("chapters", []):
        if c["seq"] == seq:
            return c
    return None


def load_chapter_analysis(book_id: str, seq: int):
    p = chapter_json_path(book_id, seq)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def backfill_speaker_counts(book: dict) -> bool:
    """舊版 meta 沒有 speakerChapters 時，從已存的分析檔統計各章語者出現次數並寫回。

    回傳是否真的有寫入；正常分析流程已會維護此資料，此函式只補舊資料。
    """
    analyzed = [c for c in book.get("chapters", []) if c.get("status") == ST_ANALYZED]
    if not analyzed:
        return False
    per_ch = dict(book.get("speakerChapters") or {})
    if all(per_ch.get(str(c["seq"])) is not None for c in analyzed):
        return False
    changed = False
    for c in analyzed:
        s = str(c["seq"])
        if per_ch.get(s) is not None:
            continue
        data = load_chapter_analysis(book["id"], c["seq"]) or {}
        counts = {}
        for seg in data.get("segments", []):
            sp = seg.get("speaker")
            if sp:
                counts[sp] = counts.get(sp, 0) + 1
        per_ch[s] = counts
        changed = True
    if not changed:
        return False
    book["speakerChapters"] = per_ch
    info = book.setdefault("speakerInfo", {})
    for name in list(info.keys()):
        info[name]["count"] = sum(per_ch[s].get(name, 0) for s in per_ch)
    save_meta(book)
    return True


def save_chapter_analysis(book_id: str, seq: int, data: dict):
    os.makedirs(chapter_dir(book_id), exist_ok=True)
    with _lock:
        p = chapter_json_path(book_id, seq)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)


def read_source(book: dict):
    p = source_path(book["id"])
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def update_chapter_status(book: dict, seq: int, **kw):
    c = get_chapter(book, seq)
    if not c:
        return
    for k, v in kw.items():
        c[k] = v
    save_meta(book)


def audio_status(book: dict):
    chapters = book.get("chapters", [])
    if not chapters:
        return 0.0
    done = sum(1 for c in chapters if c.get("audio") == AU_READY)
    return round(done / len(chapters), 4)


def analyze_status(book: dict):
    chapters = book.get("chapters", [])
    if not chapters:
        return 0.0
    done = sum(1 for c in chapters if c.get("status") == ST_ANALYZED)
    return round(done / len(chapters), 4)
