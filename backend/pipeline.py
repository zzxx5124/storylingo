"""生成管線共用 helpers（V4 canonical jobs 見 services/generation_pipeline）。"""
import logging
import os

from . import db, storage, tts
from .services import book_service

_log = logging.getLogger("pipeline")


def build_book_dict(row: dict) -> dict:
    """DB books 行 → analyzer/tts 所需的 book dict。"""
    d = book_service.to_book_dict(row)
    return {
        "id": row["bid"],
        "title": d["title"],
        "category": d["category"],
        "categories": d["categories"],
        "vocabLevel": d["vocabLevel"],
        "voices": d["voices"],
        "voicePrefs": d["voicePrefs"],
        "speakerInfo": d["speakerInfo"],
        "speakerChapters": d["speakerChapters"],
        "settings": d["settings"],
    }


def recover_stuck_states():
    """重啟時把卡在 analyzing/generating 的章節復原成可重試狀態（legacy 相容）。"""
    for b in db.query("SELECT bid FROM books"):
        bid = b["bid"]
        row = db.get_book_row(bid)
        if not row:
            continue
        for c in db.list_chapters(row["id"]):
            changed = {}
            if c["status"] == "analyzing":
                data = storage.load_chapter_analysis(bid, c["seq"]) or {}
                ok = bool(data.get("segments"))
                changed["status"] = "analyzed" if ok else "pending"
                changed["error"] = ""
                if not ok:
                    changed["audio"] = "none"
            if c["audio"] == "generating":
                p = storage.chapter_audio_path(bid, c["seq"])
                valid = False
                try:
                    valid = os.path.exists(p) and tts._probe_duration(p) > 0.5
                except Exception:
                    valid = False
                changed["audio"] = "ready" if valid else "none"
                changed["error"] = ""
            if changed:
                db.update_chapter(row["id"], c["seq"], changed)
