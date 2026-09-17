"""書級名冊回填（backfill）regression tests。

驗證：
- 從 ready artifacts 重建 voices／speaker_info／speaker_chapters（跨章累積）。
- 既有已指派聲線保留；新增名冊鍵預設 ""。
- 子字串合併（「汐桐」與「汐桐學姐」併成一個角色）。
- speaker_info 非空的書預設略過；only_if_empty=False 才重建。
- 重跑結果一致（冪等）。
- 只採用 hash 相符的 ready analysis（stale 不用）。
- dry_run 不寫入。
- LLM 漏列在 speakers、但 segment 有出現的語者也要進名冊。
"""
import json

from backend import db
from backend.services import analysis as analysis_svc
from backend.services import backfill_roster
from backend.tests.conftest import add_chapter, new_author, upload_book


def _make_book_and_analyses(a, artifacts_by_seq):
    """建立一本書＋各章 ready analysis（以章節目前 text_hash 為 source hash）。"""
    book = upload_book(a)
    for i in range(2, len(artifacts_by_seq) + 1):
        add_chapter(a, book["id"], title=f"第{i}章", text=f"第 {i} 章正文內容。" * 6)
    row = db.get_book_row(book["id"])
    chapters = {c["seq"]: c for c in db.list_chapters(row["id"])}
    for seq, artifact in artifacts_by_seq.items():
        ch = chapters[seq]
        # save_ready_analysis 現在也執行 persistence source-hash gate；fixture
        # 先對齊目前章節 hash，再由專門 stale test 模擬後續文字變更。
        artifact["sourceTextHash"] = ch["text_hash"]
        aid = analysis_svc.create_analysis_row(
            book_id=row["id"], chapter_id=ch["id"], source_text_hash=ch["text_hash"],
        )
        analysis_svc.save_ready_analysis(aid, artifact)
    return book


def _artifact(speakers, segments):
    segs = []
    for i, s in enumerate(segments):
        segs.append({
            "id": f"seg-{i}",
            "text": s["text"],
            "speaker": s["speaker"],
            "emotion": {"value": "neutral", "intensity": 0.0, "source": "fallback"},
        })
    return {
        "schemaVersion": 2,
        "chapterKey": "ck-test",
        "sourceTextHash": "h",
        "speakers": speakers,
        "segments": segs,
    }


def test_backfill_rebuilds_roster_with_counts():
    a = new_author("回填A")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "旁白", "gender": "男", "age": "中年"}, {"name": "主角", "gender": "女", "age": "青年"}],
            [
                {"type": "narration", "speaker": "旁白", "text": "夜裡風聲。"},
                {"type": "dialogue", "speaker": "主角", "text": "你去哪裡？"},
            ],
        ),
        1: _artifact(
            [{"name": "旁白", "gender": "男", "age": "中年"}, {"name": "主角", "gender": "女", "age": "青年"}],
            [
                {"type": "narration", "speaker": "旁白", "text": "天亮了。"},
                {"type": "dialogue", "speaker": "主角", "text": "走吧。"},
                {"type": "dialogue", "speaker": "店主", "text": "歡迎光臨。"},
            ],
        ),
    })
    r = backfill_roster.backfill_book(book["id"], dry_run=False)
    assert not r["skipped"] and r["written"]
    assert r["chaptersUsed"] == 2

    row = db.get_book_row(book["id"])
    voices = json.loads(row["voices"] or "{}")
    info = json.loads(row["speaker_info"] or "{}")
    per_ch = json.loads(row["speaker_chapters"] or "{}")
    assert voices["旁白"] == "" and voices["主角"] == "" and voices["店主"] == ""
    assert voices["_english"]
    assert info["旁白"]["count"] == 2
    assert info["主角"]["count"] == 2
    assert info["店主"]["count"] == 1
    assert per_ch["0"] == {"旁白": 1, "主角": 1}
    assert per_ch["1"] == {"旁白": 1, "主角": 1, "店主": 1}


def test_backfill_preserves_existing_voice_assignments():
    a = new_author("回填B")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "旁白", "gender": "未知", "age": "未知"}, {"name": "主角", "gender": "女", "age": "青年"}],
            [{"type": "dialogue", "speaker": "主角", "text": "你好。"}],
        ),
    })
    db.update_book(book["id"], {"voices": json.dumps({"旁白": "v-zh-1"}, ensure_ascii=False)})
    r = backfill_roster.backfill_book(book["id"], dry_run=False)
    assert not r["skipped"] and r["written"]
    voices = json.loads(db.get_book_row(book["id"])["voices"] or "{}")
    assert voices["旁白"] == "v-zh-1"  # 既有指派保留
    assert voices["主角"] == ""        # 新增名冊鍵預設留空


def test_backfill_substring_merge_across_chapters():
    a = new_author("回填C")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "汐桐", "gender": "女", "age": "青年"}],
            [{"type": "dialogue", "speaker": "汐桐", "text": "第一章。"}],
        ),
        1: _artifact(
            [{"name": "汐桐學姐", "gender": "女", "age": "青年"}],
            [{"type": "dialogue", "speaker": "汐桐學姐", "text": "第二章。"}],
        ),
    })
    r = backfill_roster.backfill_book(book["id"], dry_run=False)
    assert not r["skipped"] and r["written"]
    row = db.get_book_row(book["id"])
    voices = json.loads(row["voices"] or "{}")
    info = json.loads(row["speaker_info"] or "{}")
    per_ch = json.loads(row["speaker_chapters"] or "{}")
    # 「汐桐學姐」與既有名冊「汐桐」為子字串關係 → 併成「汐桐」，segment 改名後計數
    assert "汐桐學姐" not in voices
    assert voices["汐桐"] == ""
    assert info["汐桐"]["count"] == 2
    assert per_ch["1"] == {"汐桐": 1}


def test_backfill_idempotent():
    a = new_author("回填D")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "旁白", "gender": "男", "age": "中年"}, {"name": "主角", "gender": "女", "age": "青年"}],
            [{"type": "dialogue", "speaker": "主角", "text": "你好。"}],
        ),
    })
    assert backfill_roster.backfill_book(book["id"], dry_run=False)["written"]
    first = db.get_book_row(book["id"])
    # 強制重建（only_if_empty=False）→ 結果一致
    assert backfill_roster.backfill_book(book["id"], dry_run=False, only_if_empty=False)["written"]
    second = db.get_book_row(book["id"])
    assert first["speaker_info"] == second["speaker_info"]
    assert first["speaker_chapters"] == second["speaker_chapters"]
    assert first["voices"] == second["voices"]


def test_backfill_skips_nonempty_speaker_info():
    a = new_author("回填E")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "旁白", "gender": "未知", "age": "未知"}],
            [{"type": "narration", "speaker": "旁白", "text": "夜。"}],
        ),
    })
    db.update_book(book["id"], {"speaker_info": json.dumps({"旁白": {"count": 1}})})
    r = backfill_roster.backfill_book(book["id"], dry_run=False)
    assert r["skipped"] and r["reason"] == "speaker_info 非空，略過"


def test_backfill_ignores_stale_analysis():
    a = new_author("回填F")
    book = upload_book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    # source hash 與章節目前文字不符 → 不採用
    aid = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=ch["id"], source_text_hash=ch["text_hash"],
    )
    artifact = _artifact(
        [{"name": "旁白", "gender": "未知", "age": "未知"}],
        [{"type": "narration", "speaker": "旁白", "text": "夜。"}],
    )
    artifact["sourceTextHash"] = ch["text_hash"]
    analysis_svc.save_ready_analysis(aid, artifact)
    a.put(f"/api/books/{book['id']}/chapters/0", json={"text": "章節文字已變更。" * 20})
    r = backfill_roster.backfill_book(book["id"], dry_run=False)
    assert r["skipped"] and r["reason"] == "無 ready 且 hash 相符的分析 artifact"


def test_backfill_dry_run_does_not_write():
    a = new_author("回填G")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "旁白", "gender": "未知", "age": "未知"}],
            [{"type": "narration", "speaker": "旁白", "text": "夜。"}],
        ),
    })
    r = backfill_roster.backfill_book(book["id"], dry_run=True)
    assert not r["skipped"] and not r["written"]
    row = db.get_book_row(book["id"])
    assert row["speaker_info"] == "{}"
    assert row["speaker_chapters"] == "{}"


def test_backfill_uses_artifact_segment_only_speakers():
    """LLM 漏列在 speakers array、但 segment 有出現的語者也要進名冊。"""
    a = new_author("回填H")
    book = _make_book_and_analyses(a, {
        0: _artifact(
            [{"name": "旁白", "gender": "未知", "age": "未知"}],
            [
                {"type": "narration", "speaker": "旁白", "text": "夜。"},
                {"type": "dialogue", "speaker": "店主", "text": "歡迎。"},
            ],
        ),
    })
    r = backfill_roster.backfill_book(book["id"], dry_run=False)
    assert not r["skipped"] and r["written"]
    row = db.get_book_row(book["id"])
    voices = json.loads(row["voices"] or "{}")
    info = json.loads(row["speaker_info"] or "{}")
    per_ch = json.loads(row["speaker_chapters"] or "{}")
    assert voices["店主"] == ""
    assert info["店主"]["count"] == 1
    assert per_ch["0"] == {"旁白": 1, "店主": 1}
