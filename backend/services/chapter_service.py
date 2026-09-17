"""章節服務：文字存取、編輯、text_hash、破壞式重生成規則。"""
import os
import hashlib

from .. import db
from .. import exceptions as exc
from .. import settings


def get_chapter(row: dict, seq: int) -> dict:
    ch = db.get_chapter(row["id"], seq)
    if not ch:
        raise exc.not_found("找不到章節")
    return ch


def chapter_text(ch: dict) -> str:
    return ch["text"]


def _abs(rel: str) -> str:
    return os.path.join(settings.ROOT_DIR, rel) if rel else ""


def _file_exists(rel: str) -> bool:
    p = _abs(rel)
    return bool(p) and os.path.exists(p)


def remove_outputs(ch: dict) -> list[str]:
    """刪除已生成的產物檔；回傳實際有刪到的 list。"""
    removed = []
    for rel, name in ((ch.get("analyze_path"), "analyze"),
                      (ch.get("timing_path"), "timing"),
                      (ch.get("audio_path"), "audio")):
        p = _abs(rel)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                continue
            removed.append(name)
    return removed


def update_chapter(row: dict, ch: dict, *, title=None, text=None, saved_by: int = None) -> dict:
    """編輯章節。文字變更 → 破壞既有分析/音訊並重置狀態；變更前先保存舊版本快照。

    回傳 {"changed": bool, "removedOutputs": [...], "chapter": legacy, "revisionId": int|None}
    """
    current = ch["text"]
    new_text = current if text is None else text
    if text is not None and text != current:
        new_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()[:16]
        changed = new_hash != ch["text_hash"]
    else:
        changed = False
    # 更新文字 → 必定允許使用者直接改文；hash 未變才視為「未變更」
    fields = {}
    if title is not None:
        fields["title"] = title
    if text is not None and text != current:
        fields["text"] = new_text
    if text is not None:
        fields["chars"] = len(new_text)
    if text is not None and text != current:
        new_hash = hashlib.sha256(new_text.encode("utf-8")).hexdigest()[:16]
        if new_hash != ch["text_hash"]:
            fields["text_hash"] = new_hash
            fields["analysed_hash"] = ""
            fields["generated_at"] = None
            fields["status"] = "pending"
            fields["audio"] = "none"
            fields["error"] = ""
            if fields.setdefault("chars", 0) == len(new_text):
                pass
    revision_id = None
    if text is not None and text != current:
        # 保存「舊版本」（編輯前內容）到版本歷史
        revision_id = db.save_chapter_revision(row["id"], ch["seq"], ch["title"], current, saved_by=saved_by)
    removed = []
    if changed:
        removed = remove_outputs(ch)
        if fields.get("status", "pending") != "pending":
            fields["status"] = "pending"
        if fields.get("audio", "none") != "none":
            fields["audio"] = "none"
    if fields:
        db.update_chapter(row["id"], ch["seq"], fields)
        # 全書字數重算
        db.execute("UPDATE books SET chars = COALESCE((SELECT SUM(chars) FROM chapters WHERE book_id=?), chars), updated_at = ? WHERE id = ?",
                   (row["id"], db.ts(), row["id"]))
    updated = db.get_chapter(row["id"], ch["seq"])
    return {"changed": changed, "removedOutputs": removed, "chapter": updated, "revisionId": revision_id}


def record_analysis_row(book_row_id: int, seq: int, text_hash: str):
    """分析完成：記錄 analysed_hash（比對編輯後的變更）。"""
    db.update_chapter(book_row_id, seq, {
        "analysed_hash": text_hash or "",
        "generated_at": db.ts(),
    })


def reset_chapter(row_id: int, seq: int, *, status="pending", audio="none"):
    db.update_chapter(row_id, seq, {"status": status, "audio": audio, "error": ""})