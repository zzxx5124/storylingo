"""既有書書級名冊回填（backfill）。

名冊寫回（analysis_executor._persist_book_roster）只對「之後」的分析生效；
在此修正之前已分析、但 books.speaker_info 仍為 {} 的書，可從 ready 的
chapter_analyses artifact 重建 voices／speaker_info／speaker_chapters，
不需要重新消耗 AI 額度重新分析。

彙整規則與 analyzer.analyze_chapter 完全一致（mirror）：
- voices 的 key 即名冊；以既有指派為種子（保留已綁定聲線），新增鍵預設 ""
- 子字串合併（跨章節名冊累積，例如「汐桐」與「汐桐學姐」併成一個角色）
- 語者預設留空（voices[name] = ""），供後續手動／AI 匹配聲線
- 旁白缺省補入（firstCh 取首次出現章節）
- speakerChapters 依章記錄各語者 segment 數；speakerInfo.count 為跨章總和
- 只採用「ready 且 source_text_hash 與章節目前文字相符」的分析（canonical 資格）
"""
import json
import logging
import os
from collections import Counter

from .. import analyzer, db, settings
from .. import v4_contracts as c
from .. import voices as voices_mod
from . import analysis as analysis_svc
from . import analysis_version_policy as version_policy

_log = logging.getLogger("backfill_roster")


def _load_json(value):
    if isinstance(value, dict):
        return dict(value)
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def _read_artifact(record: dict) -> dict | None:
    """讀取 ready analysis 的 artifact 檔案（相對 ROOT_DIR 的 artifact_path）。"""
    rel = record.get("artifact_path") or ""
    if not rel:
        return None
    p = os.path.join(settings.ROOT_DIR, rel)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        _log.warning("artifact 讀取失敗 %s：%s", rel, e)
        return None


def eligible_analysis(chapter_row: dict) -> dict | None:
    """該章目前可採用的 ready analysis：hash 與章節目前文字相符。

    current_analysis_id 優先（必然 hash 相符）；否則回退
    get_ready_chapter_analysis（該 query 本身以 text_hash 過濾）。
    """
    if chapter_row.get("current_analysis_id"):
        rec = db.get_chapter_analysis(chapter_row["current_analysis_id"])
        if rec and rec.get("status") == "ready" and rec.get("source_text_hash") == chapter_row.get("text_hash"):
            return rec
    return db.get_ready_chapter_analysis(chapter_row["id"], chapter_row.get("text_hash") or "")


def _merge_chapter(voices: dict, info: dict, per_ch: dict, seq: int, artifact: dict):
    """把一章 artifact 併入書級名冊（mirror analyzer.analyze_chapter 的彙整順序）。"""
    if (version_policy.is_native(artifact.get("schemaVersion"))
            or (version_policy.is_compatibility(artifact.get("schemaVersion"))
                and not artifact.get("legacySource"))):
        labels = {c.SPEAKER_ID_NARRATOR: "旁白"}
        for character in artifact.get("characters") or []:
            if isinstance(character, dict) and character.get("character_id"):
                labels[character["character_id"]] = character.get("canonical_name") or character["character_id"]
        counts = Counter(labels.get(seg.get("speaker_id")) for seg in artifact.get("segments") or [])
        counts.pop(None, None)
        counts.pop("未解析語者", None)
        per_ch[str(seq)] = dict(counts)
        for name in counts:
            info.setdefault(name, {"gender": "未知", "age": "未知", "firstCh": seq,
                                   "judgements": [], "conflicts": []})
            voices.setdefault(name, "")
        for character in artifact.get("characters") or []:
            name = character.get("canonical_name") if isinstance(character, dict) else None
            if name and name in info:
                info[name]["gender"] = character.get("gender", info[name].get("gender", "未知"))
                info[name]["age"] = character.get("age_group", info[name].get("age", "未知"))
                info[name]["speaker_id"] = character.get("character_id")
        return
    speakers = []
    segments = list(artifact.get("segments") or [])
    for sp in artifact.get("speakers") or []:
        name = (sp.get("name") or "").strip()
        if not name:
            continue
        if name not in voices:
            merged = None
            for existing in list(voices.keys()):
                if existing == "_english":
                    continue
                if len(name) >= 2 and len(existing) >= 2 and (name in existing or existing in name):
                    merged = existing
                    break
            if merged:
                for seg in segments:
                    if seg.get("speaker") == name:
                        seg["speaker"] = merged
                name = merged
        analyzer._merge_speaker_info(info, seq, name, sp.get("gender"), sp.get("age"))
        voices.setdefault(name, "")
        speakers.append(name)

    # 旁白缺省（mirror analyze_chapter：本章沒出現旁白也補入）
    if "旁白" not in speakers:
        info.setdefault("旁白", {
            "gender": "未知", "age": "未知", "firstCh": seq, "judgements": [], "conflicts": [],
        })
        speakers.insert(0, "旁白")

    # LLM 漏列在 speakers、但 segment 有出現的語者（segment 已先完成子字串合併改名）
    for seg in segments:
        sp = seg.get("speaker")
        if not sp or sp == "_english" or sp in info:
            continue
        analyzer._merge_speaker_info(info, seq, sp, "未知", "未知")
        voices.setdefault(sp, "")
        if sp not in speakers:
            speakers.append(sp)

    # 各章各語者出現次數（segment 數）：跨章累加供「誰是主要角色」判斷
    counts = Counter(seg.get("speaker") for seg in segments if seg.get("speaker"))
    per_ch[str(seq)] = dict(counts)


def build_roster(book_row: dict, pairs: list) -> dict:
    """以各章 (seq, artifact) 重建書級名冊。

    voices 以既有指派為種子（保留已綁定聲線）；speaker_info／speaker_chapters
    由 artifact 重新建構（重跑結果一致，冪等）。
    """
    voices = _load_json(book_row.get("voices"))
    voices.setdefault("_english", voices_mod.ENGLISH_VOICE_DEFAULT)
    info: dict = {}
    per_ch: dict = {}
    for seq, artifact in pairs:
        _merge_chapter(voices, info, per_ch, seq, artifact)
    for name in list(info.keys()):
        info[name]["count"] = sum(per_ch[s].get(name, 0) for s in per_ch)
    return {"voices": voices, "speakerInfo": info, "speakerChapters": per_ch}


def collect_pairs(chapters: list) -> list:
    """依章節順序收集可採用的 (seq, artifact)。"""
    pairs = []
    for ch in sorted(chapters, key=lambda x: x.get("seq") or 0):
        rec = eligible_analysis(ch)
        if not rec:
            continue
        artifact = _read_artifact(rec)
        if not artifact or version_policy.classify_version(artifact.get("schemaVersion")) == version_policy.UNSUPPORTED:
            continue
        if ((version_policy.is_native(artifact.get("schemaVersion"))
                or version_policy.is_compatibility(artifact.get("schemaVersion")))
                and artifact.get("legacySource")):
            artifact = analysis_svc.legacy_compatibility_view(artifact)
        pairs.append((ch["seq"], artifact))
    return pairs


def backfill_book(bid: str, *, dry_run: bool = False, only_if_empty: bool = True) -> dict:
    """回填單本書，回傳報告 dict。"""
    row = db.get_book_row(bid)
    if not row:
        return {"bid": bid, "skipped": True, "reason": "找不到書"}
    if only_if_empty and _load_json(row.get("speaker_info")):
        return {"bid": bid, "skipped": True, "reason": "speaker_info 非空，略過"}
    chapters = db.list_chapters(row["id"])
    pairs = collect_pairs(chapters)
    if not pairs:
        return {"bid": bid, "skipped": True, "reason": "無 ready 且 hash 相符的分析 artifact"}
    roster = build_roster(row, pairs)
    if not dry_run:
        db.update_book(bid, {
            "voices": json.dumps(roster["voices"], ensure_ascii=False),
            "speaker_info": json.dumps(roster["speakerInfo"], ensure_ascii=False),
            "speaker_chapters": json.dumps(roster["speakerChapters"], ensure_ascii=False),
        })
    return {
        "bid": bid,
        "skipped": False,
        "written": not dry_run,
        "chapters": len(chapters),
        "chaptersUsed": len(pairs),
        "speakers": len(roster["speakerInfo"]),
    }


def run_backfill(*, bids: list | None = None, dry_run: bool = True, only_if_empty: bool = True) -> dict:
    """跑全部（或指定書）回填，回傳彙整報告。

    預設 dry_run=True（只報告不寫入）；實際寫入需明確傳 dry_run=False。
    """
    if bids:
        rows = []
        for b in bids:
            r = db.get_book_row(b)
            if r:
                rows.append(r)
    else:
        rows = db.query("SELECT bid FROM books ORDER BY id")
    report = {"dryRun": dry_run, "books": []}
    for r in rows:
        item = backfill_book(r["bid"], dry_run=dry_run, only_if_empty=only_if_empty)
        report["books"].append(item)
    done = [b for b in report["books"] if not b.get("skipped")]
    report["scanned"] = len(report["books"])
    report["backfilled"] = len(done)
    report["skipped"] = len(report["books"]) - len(done)
    return report
