"""V4 Phase 10/15b：Author workflow V4 - derived workflow state regression tests.

驗證：
- derived workflow state 附加於 owner book payload（讀者不可見）。
- single mode：不需 analysis；voice → generate → ready 的 next-step 推導。
- multi mode：analyze → voice mapping → generate 的 next-step 推導。
- provider outage 以 platform state 呈現（provider_unavailable）。
- 狀態一律由 canonical 紀錄推導（chapter_analyses／audio_generations）。
- 書籍音訊設定 endpoint（audioMode／defaultVoiceId）與 version bump。
- chapter_legacy 投影：canonical ready 對外呈現 audio=ready。
"""
import json
import os

from backend import db, settings
from backend import v4_contracts as c
from backend.services import workflow
from backend.tests.conftest import new_author, new_user, upload_book, add_chapter


def _ensure_tts():
    if not db.get_active_tts_provider():
        db.create_tts_provider({
            "name": "測試TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
            "enabled": True, "is_default": True,
        })


def _ensure_ai():
    if not db.get_default_ai_provider():
        db.create_ai_provider({
            "name": "測試AI", "provider_type": "openai_compatible", "base_url": "https://ai.example.com",
            "model": "m", "enabled": True, "is_default": True, "created_at": db.ts(), "updated_at": db.ts(),
        })


def _book(a, n=1):
    _ensure_tts()
    book = upload_book(a)
    for i in range(2, n + 1):
        add_chapter(a, book["id"], title=f"第{i}章", text=f"第 {i} 章正文內容。" * 6)
    return book


def _compute(book, *, mode=None, default_voice=None):
    row = db.get_book_row(book["id"])
    if mode:
        db.update_book(book["id"], {"audio_mode": mode})
    if default_voice is not None:
        db.update_book(book["id"], {"default_voice_id": default_voice})
    row = db.get_book_row(book["id"])
    chapters = db.list_chapters(row["id"])
    return workflow.compute_book_workflow(row, chapters)


def _make_ready_analysis(row, ch, speakers=("旁白",)):
    aid = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": ch["id"], "source_text_hash": ch["text_hash"],
        "analysis_type": "speaker", "schema_version": 2, "analysis_profile": "speaker",
        "status": "queued", "prompt_version": "v2-speaker-1",
    })
    rel = f"tests/analyses/{aid}.json"
    p = os.path.join(settings.ROOT_DIR, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({
            "schemaVersion": 2, "chapterKey": ch["chapter_key"], "sourceTextHash": ch["text_hash"],
            "speakers": [{"name": n} for n in speakers],
            "segments": [{"id": "s1", "text": "內容", "speaker": speakers[0], "emotion": {"value": "neutral"}}],
        }, f, ensure_ascii=False)
    db.mark_analysis_ready(aid, rel)
    return aid


def _make_generation(row, ch, *, status="ready", activate=True):
    gid = db.create_audio_generation({
        "book_id": row["id"], "chapter_id": ch["id"], "mode": "single",
        "source_text_hash": ch["text_hash"], "generation_key": f"k-{ch['id']}-{gid_counter()}",
        "status": "queued",
    })
    if status == "ready":
        db.mark_generation_ready(gid, "x.mp3", "x.json")
    return gid


_gid = {"n": 0}


def gid_counter():
    _gid["n"] += 1
    return _gid["n"]


def test_workflow_only_in_owner_payload(client):
    a = new_author("作者A")
    book = _book(a)
    owner = a.get(f"/api/books/{book['id']}").json()
    assert "workflow" in owner
    assert owner["workflow"]["total"] >= 1
    # reader 看不到 workflow
    reader = new_user("讀者R")
    d = reader.get(f"/api/books/{book['id']}").json()
    assert "workflow" not in d


def test_single_mode_needs_voice_then_generate():
    a = new_author("作者B")
    book = _book(a)
    w = _compute(book, mode="single", default_voice=None)
    assert w["mode"] == "single"
    # 未設 voice → needs_voice_configuration / set_voice
    ch0 = w["chapters"][0]
    assert ch0["nextAction"] == "set_voice"
    assert w["needsAction"] is True

    # 設 voice 後 → ready_to_generate / generate
    w2 = _compute(book, mode="single", default_voice="voice-1")
    assert w2["chapters"][0]["nextAction"] == "generate"
    assert w2["chapters"][0]["state"] == c.WORKFLOW_READY_TO_GENERATE


def test_single_mode_does_not_require_analysis():
    """single 不需 AI analysis：無 AI provider 也保持 set_voice/generate 而非 provider_unavailable。"""
    a = new_author("作者S")
    book = _book(a)
    if db.get_default_ai_provider():
        db.delete_ai_provider(db.get_default_ai_provider()["id"])
    w = _compute(book, mode="single", default_voice="voice-1")
    assert w["aiProviderAvailable"] is False
    assert w["chapters"][0]["nextAction"] == "generate"


def test_multi_mode_requires_analysis_first():
    _ensure_ai()
    a = new_author("作者C")
    book = _book(a)
    w = _compute(book, mode="multi", default_voice="voice-1")
    # 未分析 → needs_analysis / analyze
    assert w["chapters"][0]["nextAction"] == "analyze"
    assert w["chapters"][0]["state"] == c.WORKFLOW_NEEDS_ANALYSIS


def test_multi_mode_analysis_running_then_voice_mapping_then_generate():
    _ensure_ai()
    a = new_author("作者M")
    book = _book(a)
    db.update_book(book["id"], {"audio_mode": "multi"})
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    # queued analysis → analysis_running
    aid = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": ch["id"], "source_text_hash": ch["text_hash"],
        "analysis_type": "speaker", "schema_version": 2, "analysis_profile": "speaker",
        "status": "queued", "prompt_version": "v2-speaker-1",
    })
    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    assert w["chapters"][ch["seq"]]["state"] == c.WORKFLOW_ANALYSIS_RUNNING
    # ready analysis、聲線未指定 → configure_voices
    _make_ready_analysis(row, ch, speakers=("旁白", "主角"))
    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    assert w["chapters"][ch["seq"]]["state"] == c.WORKFLOW_NEEDS_VOICE_CONFIGURATION
    assert w["chapters"][ch["seq"]]["nextAction"] == "configure_voices"
    # 指定全部聲線 → generate
    db.update_book(book["id"], {"voices": json.dumps({"旁白": "v1", "主角": "v2"}, ensure_ascii=False)})
    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    assert w["chapters"][ch["seq"]]["state"] == c.WORKFLOW_READY_TO_GENERATE
    assert w["chapters"][ch["seq"]]["nextAction"] == "generate"


def test_newer_analysis_attempt_is_not_hidden_by_older_ready_analysis():
    """重分析期間與失敗後，workflow 不得被舊 ready artifact 偽裝成完成。"""
    _ensure_ai()
    a = new_author("重分析狀態作者")
    book = _book(a)
    db.update_book(book["id"], {"audio_mode": "multi"})
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    _make_ready_analysis(row, ch, speakers=("旁白",))

    latest_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": ch["id"], "source_text_hash": ch["text_hash"],
        "analysis_type": "speaker", "schema_version": 4, "analysis_profile": "speaker",
        "status": "queued", "prompt_version": "v4-speaker-1",
    })
    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    current = w["chapters"][ch["seq"]]
    assert current["state"] == c.WORKFLOW_ANALYSIS_RUNNING
    assert current["nextAction"] is None

    db.update_chapter_analysis(latest_id, {"status": "failed", "error": "coverage failed"})
    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    current = w["chapters"][ch["seq"]]
    assert current["state"] == c.WORKFLOW_ANALYSIS_FAILED
    assert current["nextAction"] == "analyze"


def test_newer_generation_is_not_hidden_by_older_active_ready_generation():
    """重生成 queued/running/failed 時，舊 active 音訊不得讓 UI 立即顯示完成。"""
    a = new_author("重生成狀態作者")
    book = _book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    old_gid = _make_generation(row, ch, status="ready")
    db.update_chapter(row["id"], ch["seq"], {"active_audio_generation_id": old_gid})
    new_gid = _make_generation(row, ch, status="queued")

    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    current = w["chapters"][ch["seq"]]
    assert current["state"] == c.WORKFLOW_AUDIO_GENERATING
    assert current["nextAction"] is None

    db.mark_generation_failed(new_gid, "provider failed")
    w = workflow.compute_book_workflow(db.get_book_row(book["id"]), db.list_chapters(row["id"]))
    current = w["chapters"][ch["seq"]]
    assert current["state"] == c.WORKFLOW_AUDIO_FAILED
    assert current["nextAction"] == "generate"


def test_multi_mode_without_ai_provider_is_platform_state():
    a = new_author("作者N")
    book = _book(a)
    if db.get_default_ai_provider():
        db.delete_ai_provider(db.get_default_ai_provider()["id"])
    w = _compute(book, mode="multi", default_voice="voice-1")
    assert w["chapters"][0]["state"] == c.WORKFLOW_PROVIDER_UNAVAILABLE
    assert w["chapters"][0]["nextAction"] == "configure_ai_provider"
    assert w["providerUnavailable"] is True


def test_provider_outage_presented_as_platform_state():
    a = new_author("作者D")
    book = _book(a)
    # 無 TTS provider → provider_unavailable / configure_tts_provider
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    if db.get_active_tts_provider():
        db.delete_tts_provider(db.get_active_tts_provider()["id"])
    w = workflow.compute_book_workflow(row, [ch])
    assert w["ttsProviderAvailable"] is False
    assert w["providerUnavailable"] is True
    assert w["chapters"][0]["nextAction"] == "configure_tts_provider"
    assert w["chapters"][0]["state"] == c.WORKFLOW_PROVIDER_UNAVAILABLE


def test_canonical_ready_generation_is_audio_ready():
    a = new_author("作者E")
    book = _book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    gid = _make_generation(row, ch)
    db.update_chapter(row["id"], ch["seq"], {"active_audio_generation_id": gid})
    w = workflow.compute_book_workflow(row, [db.get_chapter(row["id"], ch["seq"])])
    assert w["chapters"][0]["state"] == c.WORKFLOW_AUDIO_READY
    assert w["chapters"][0]["nextAction"] is None
    assert w["ready"] == 1


def test_queued_generation_is_audio_generating():
    a = new_author("作者G")
    book = _book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    _make_generation(row, ch, status="queued")
    w = workflow.compute_book_workflow(row, [db.get_chapter(row["id"], ch["seq"])])
    assert w["chapters"][0]["state"] == c.WORKFLOW_AUDIO_GENERATING
    assert w["chapters"][0]["nextAction"] is None


def test_failed_generation_offers_retry_generate():
    a = new_author("作者H")
    book = _book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    gid = _make_generation(row, ch)
    db.mark_generation_failed(gid, "測試失敗")
    db.update_chapter(row["id"], ch["seq"], {"active_audio_generation_id": gid})
    w = workflow.compute_book_workflow(row, [db.get_chapter(row["id"], ch["seq"])])
    assert w["chapters"][0]["state"] == c.WORKFLOW_AUDIO_FAILED
    assert w["chapters"][0]["nextAction"] == "generate"
    assert w["chapters"][0]["error"] == "測試失敗"


def test_mode_switch_stales_ready_generation():
    """切換朗讀方式後，舊 mode 的 ready generation 不得再視為可播放（audio_stale 行為）。"""
    a = new_author("作者I")
    book = _book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    gid = _make_generation(row, ch, status="ready")  # mode=single
    db.update_chapter(row["id"], ch["seq"], {"active_audio_generation_id": gid})
    w_single = workflow.compute_book_workflow(row, [db.get_chapter(row["id"], ch["seq"])])
    assert w_single["chapters"][0]["state"] == c.WORKFLOW_AUDIO_READY
    # 切到 multi：single 的 ready generation 不再符合 effective mode
    db.update_book(book["id"], {"audio_mode": "multi"})
    row2 = db.get_book_row(book["id"])
    w_multi = workflow.compute_book_workflow(row2, [db.get_chapter(row2["id"], ch["seq"])])
    assert w_multi["chapters"][0]["state"] != c.WORKFLOW_AUDIO_READY
    assert w_multi["ready"] == 0


def test_next_action_uses_product_language():
    a = new_author("作者F")
    book = _book(a)
    w = _compute(book, mode="single")
    actions = {ch["nextAction"] for ch in w["chapters"].values()}
    assert "set_voice" in actions or "generate" in actions
    for ch in w["chapters"].values():
        assert ch["nextAction"] not in ("analyze_llm", "tts_api", "provider_call")


# ---------- 書籍音訊設定 endpoint（§5.4） ----------

def test_book_audio_settings_endpoint():
    _ensure_ai()
    a = new_author("設定作者")
    book = _book(a)
    row0 = db.get_book_row(book["id"])
    v0 = row0.get("audio_settings_version") or 1
    r = a.put(f"/api/books/{book['id']}", json={"audioMode": "multi", "defaultVoiceId": "voice-9"})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"]["mode"] == "multi"
    assert r.json()["workflow"]["hasDefaultVoice"] is True
    row1 = db.get_book_row(book["id"])
    assert row1["audio_mode"] == "multi"
    assert row1["default_voice_id"] == "voice-9"
    assert (row1.get("audio_settings_version") or 1) > v0
    # 非法 mode → 400
    assert a.put(f"/api/books/{book['id']}", json={"audioMode": "weird"}).status_code == 400
    # 清除 voice
    r = a.put(f"/api/books/{book['id']}", json={"defaultVoiceId": None})
    assert r.status_code == 200
    assert db.get_book_row(book["id"])["default_voice_id"] is None


# ---------- chapter_legacy 投影（canonical → 公開 payload） ----------

def test_chapter_payload_projects_canonical_ready_audio():
    a = new_author("投影作者")
    book = _book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    gid = _make_generation(row, ch)
    db.update_chapter(row["id"], ch["seq"], {"active_audio_generation_id": gid})
    payload = a.get(f"/api/books/{book['id']}").json()
    chapter = next(x for x in payload["chapters"] if x["seq"] == ch["seq"])
    assert chapter["audio"] == "ready"
    assert chapter["activeAudioGenerationId"] == gid
    # 公開讀者 payload 亦投影（書籍未公開時讀者看不到，改由公開章節 payload 驗證）
    from backend.tests.conftest import new_admin, publish_book
    admin = new_admin("投影管理")
    pub = publish_book(a, admin)
    pub_row = db.get_book_row(pub["id"])
    pub_ch = db.list_chapters(pub_row["id"])[0]
    gid2 = _make_generation(pub_row, pub_ch)
    db.update_chapter(pub_row["id"], pub_ch["seq"], {"active_audio_generation_id": gid2})
    guest = __import__("backend.main", fromlist=["app"]).app
    from fastapi.testclient import TestClient
    read = TestClient(guest).get(f"/api/books/{pub['id']}/read/{pub_ch['seq']}")
    assert read.status_code == 200
    assert read.json()["chapter"]["audio"] == "ready"
