"""V4 Blackbox UAT remediation regression tests。

覆蓋：
- audio_single 400 root cause：EN 書 single 模式使用選定聲線（非 Edge fallback id）。
- worker 端聲線目錄驗證：缺聲線以明確錯誤失敗，不送未知 voice 給 provider。
- AI 匹配語者：語言感知、真實計數、0 指派不回報成功。
"""
import json
import os
from types import SimpleNamespace

import pytest

from backend import db, settings, tts
from backend.routers.books import _preflight_voices
from backend.services import voice as voice_svc
from backend.tests.conftest import new_admin, new_author, upload_book


def _en_book_dict():
    return {
        "id": 1,
        "category": "en", "categories": ["en"],
        "voices": {"旁白": "af_bella", "_english": "af_heart"},
        "settings": {},
        "default_voice_id": "af_bella",
        "audio_mode": "single",
    }


def test_build_jobs_en_single_uses_selected_voice_not_edge_fallback():
    """EN 書 single 模式：_build_jobs 必須使用選定的旁白聲線（af_bella），
    而不是 Edge fallback id（en-US-JennyNeural）——後者會讓遠端 provider 回 400。"""
    book = _en_book_dict()
    analysis = {"segments": [{"type": "narration", "speaker": "旁白",
                              "text": "The rain fell on the old shop window.", "emotion": None}]}
    jobs = tts._build_jobs(book, analysis)
    assert jobs and jobs[0][2] == "af_bella"
    assert "JennyNeural" not in jobs[0][2]


def test_build_jobs_multi_en_uses_per_speaker_voices():
    book = _en_book_dict()
    book["voices"] = {"旁白": "af_heart", "店主": "am_adam", "_english": "af_heart"}
    analysis = {"segments": [
        {"type": "dialogue", "speaker": "旁白", "text": "The door opened.", "emotion": None},
        {"type": "dialogue", "speaker": "店主", "text": "Welcome in.", "emotion": None},
    ]}
    jobs = tts._build_jobs(book, analysis)
    voices = [j[2] for j in jobs]
    assert voices == ["af_heart", "am_adam"]


def test_build_jobs_zh_does_not_require_english_teaching_voice():
    """純中文普通朗讀只使用實際語者聲線，不應依賴 _english。"""
    book = {
        "id": 1, "category": "zh", "categories": ["zh"],
        "voices": {"旁白": "zh_voice"}, "settings": {},
        "audio_mode": "multi",
    }
    analysis = {"segments": [{"type": "narration", "speaker": "旁白",
                               "text": "這是一段中文內容。", "emotion": None}]}
    jobs = tts._build_jobs(book, analysis)
    assert jobs and jobs[0][2] == "zh_voice"


def test_preflight_checks_unassigned_language_fallback_against_catalog(client, monkeypatch, tmp_path):
    """未綁定中文語者的 fallback 不得繞過 API preflight 留給 worker 才失敗。"""
    provider_id = db.create_tts_provider({
        "name": "fallback 目錄TTS", "provider_type": "generic_http",
        "base_url": "https://tts.example.com", "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(provider_id, [{"id": "cosy_mimi", "name": "Mimi", "lang": "zh"}])
    artifact_path = tmp_path / "analysis.json"
    artifact_path.write_text(json.dumps({
        "schemaVersion": 2,
        "speakers": [{"name": "主角"}],
        "segments": [{"type": "dialogue", "speaker": "主角", "text": "這是一句中文。"}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(db, "get_ready_chapter_analysis", lambda _chapter_id, _source_hash: {
        "artifact_path": "analysis.json",
    })
    book = {"category": "zh", "categories": ["zh"], "voices": {}}
    chapter = {"id": 1, "text_hash": "hash"}
    with pytest.raises(Exception) as error:
        _preflight_voices(dict(db.get_tts_provider(provider_id)), book, chapter, "multi")
    assert "未設定的旁白聲線" in str(error.value)
    assert "zh-CN-XiaoxiaoNeural" not in str(error.value)
    assert "語者清單" in str(error.value)


def test_generate_chapter_audio_rejects_voice_not_in_provider_catalog(client, monkeypatch, tmp_path):
    """worker 端聲線目錄驗證：不在 catalog 的聲線必須以明確錯誤失敗（不送給 provider）。"""
    provider_id = db.create_tts_provider({
        "name": "目錄TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(provider_id, [{"id": "af_bella", "name": "Bella", "lang": "en"}])
    book = {
        "id": 2, "category": "en", "categories": ["en"],
        "voices": {"旁白": "en-US-JennyNeural", "_english": "en-US-JennyNeural"},
        "settings": {},
    }
    analysis = {"segments": [{"type": "narration", "speaker": "旁白", "text": "Hello world.", "emotion": None}]}
    ch = {"seq": 0}
    with pytest.raises(RuntimeError) as err:
        tts.generate_chapter_audio(book, ch, analysis, final_audio_path=str(tmp_path / "a.mp3"),
                                   final_timing_path=str(tmp_path / "t.json"))
    assert "語者清單" in str(err.value)
    assert "JennyNeural" in str(err.value)


def test_ai_match_en_book_uses_selected_default_ai_provider(client, monkeypatch):
    """AI 匹配語者：EN 書必須用 EN 聲線池（不再硬編碼 zh 而 0 指派）。"""
    provider_id = db.create_tts_provider({
        "name": "匹配TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(provider_id, [
        {"id": "af_bella", "name": "Bella", "lang": "en", "gender": "female"},
        {"id": "am_adam", "name": "Adam", "lang": "en", "gender": "male"},
    ])
    book = {
        "category": "en", "categories": ["en"],
        "speakerInfo": {"店主": {"gender": "男", "age": "中年", "count": 5}},
        "voices": {}, "voicePrefs": {},
    }
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            class Message:
                content = '{"matches":[{"speaker":"店主","voice_id":"am_adam","reason":"男性角色"}]}'
            class Choice:
                message = Message()
            class Response:
                choices = [Choice()]
            return Response()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(db, "get_default_ai_provider", lambda: {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    })
    monkeypatch.setattr(voice_svc, "_openai_client", lambda provider: FakeClient())
    res = voice_svc.ai_match(book)
    assert res["total"] == 1
    assert res["assigned"] == 1
    assert res["method"] == "ai"
    assert res["matches"][0]["voice_id"] == "am_adam"
    assert captured["model"] == "gpt-4o-mini"
    assert captured["max_tokens"] == 1200
    assert captured["temperature"] == 0.2


def test_ai_match_provider_failure_does_not_silently_assign_heuristic_voice(client, monkeypatch):
    provider_id = db.create_tts_provider({
        "name": "AI failure TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(provider_id, [
        {"id": "af_bella", "name": "Bella", "lang": "en", "gender": "female"},
        {"id": "am_adam", "name": "Adam", "lang": "en", "gender": "male"},
    ])
    monkeypatch.setattr(db, "get_default_ai_provider", lambda: {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    })
    monkeypatch.setattr(voice_svc, "_openai_client", lambda provider: (_ for _ in ()).throw(ConnectionError("offline")))
    res = voice_svc.ai_match({
        "category": "en", "categories": ["en"],
        "speakerInfo": {"店主": {"gender": "男", "count": 5}},
        "voices": {}, "voicePrefs": {},
    })
    assert res["method"] == "ai"
    assert res["assigned"] == 0
    assert res["matches"][0]["voice_id"] == ""
    assert "暫時無法使用" in res["matches"][0]["reason"]


def test_ai_match_uses_strict_snapshot_and_repairs_malformed_once(monkeypatch):
    """已驗證 strict capability 時，匹配不再依賴不受約束的 json_object。"""
    db.create_tts_provider({
        "name": "strict 匹配 TTS", "provider_type": "generic_http",
        "base_url": "https://tts.example.com", "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(1, [{
        "id": "v-zh-1", "name": "男聲", "lang": "zh", "gender": "male",
    }])
    provider = {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    }
    monkeypatch.setattr(db, "get_default_ai_provider", lambda: provider)
    monkeypatch.setattr(voice_svc.structured_capability, "load", lambda _provider: SimpleNamespace(
        state="supported", supports_strict_json_schema=True,
    ))
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            content = '{"matches":[{"speaker":"甲","voice_id":"v-zh-1"}' if len(calls) == 1 else (
                '{"matches":[{"speaker":"甲","voice_id":"v-zh-1",'
                '"alternatives":[],"reason":"男性角色"}]}'
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr(voice_svc, "_openai_client", lambda _provider: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    ))
    result = voice_svc.ai_match({
        "category": "zh", "categories": ["zh"], "voices": {}, "voicePrefs": {},
        "speakerInfo": {"甲": {"gender": "男", "age": "成年", "count": 3}},
        "speakerChapters": {"0": {"甲": {}}},
    })

    assert result["assigned"] == 1
    assert result["repairCount"] == 1
    assert result["structuredModeApplied"] == "strict_json_schema"
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_schema"


def test_ai_match_groups_speakers_and_completes_missing_group_entry(monkeypatch):
    """角色超過單次輸出安全大小時，所有分組都處理且只補缺漏角色。"""
    db.create_tts_provider({
        "name": "分組匹配 TTS", "provider_type": "generic_http",
        "base_url": "https://tts.example.com", "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(1, [
        {"id": f"v-zh-{i}", "name": f"聲線{i}", "lang": "zh", "gender": "unknown"}
        for i in range(10)
    ])
    monkeypatch.setattr(db, "get_default_ai_provider", lambda: {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    })
    speakers = {
        f"角色{i}": {"gender": "未知", "age": "未知", "count": 1}
        for i in range(10)
    }
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                items = [
                    {"speaker": f"角色{i}", "voice_id": f"v-zh-{i}"}
                    for i in range(7)
                ]
            elif len(calls) == 2:
                items = [{"speaker": "角色7", "voice_id": "v-zh-7"}]
            else:
                items = [
                    {"speaker": f"角色{i}", "voice_id": f"v-zh-{i}"}
                    for i in (8, 9)
                ]
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"matches": items}))
            )])

    monkeypatch.setattr(voice_svc, "_openai_client", lambda _provider: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    ))
    result = voice_svc.ai_match({
        "category": "zh", "categories": ["zh"], "voices": {}, "voicePrefs": {},
        "speakerInfo": speakers,
        "speakerChapters": {"0": {name: {} for name in speakers}},
    })

    assert result["assigned"] == 10
    assert result["total"] == 10
    assert result["targetedCompletionCount"] == 1
    assert len(calls) == 3
    assert {m["speaker"] for m in result["matches"] if m["voice_id"]} == set(speakers)


def test_ai_match_malformed_repair_exhausted_is_explicit_failure(monkeypatch):
    db.create_tts_provider({
        "name": "malformed 匹配 TTS", "provider_type": "generic_http",
        "base_url": "https://tts.example.com", "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(1, [{"id": "v-zh-1", "name": "男聲", "lang": "zh", "gender": "male"}])
    monkeypatch.setattr(db, "get_default_ai_provider", lambda: {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    })

    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))])

    monkeypatch.setattr(voice_svc, "_openai_client", lambda _provider: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    ))
    result = voice_svc.ai_match({
        "category": "zh", "categories": ["zh"], "voices": {}, "voicePrefs": {},
        "speakerInfo": {"甲": {"gender": "男", "count": 1}},
    })

    assert result["errorCode"] == "ai_match_invalid_response"
    assert result["assigned"] == 0
    assert result["retryable"] is True


def test_ai_match_invalid_response_is_not_returned_as_http_200(monkeypatch):
    operator = new_admin("匹配錯誤回應操作員")
    book = upload_book(operator)
    monkeypatch.setattr(voice_svc, "ai_match", lambda _book: {
        "matches": [], "total": 1, "assigned": 0, "method": "ai",
        "errorCode": "ai_match_invalid_response", "retryable": True,
        "message": "AI 語者匹配回應格式無效，請稍後再試",
    })
    response = operator.post(f"/api/books/{book['id']}/voices/ai-match")
    assert response.status_code == 502
    assert response.json()["code"] == "ai_match_invalid_response"
    assert "格式無效" in response.json()["detail"]


def test_ai_match_no_language_voice_reports_zero_not_success(client):
    """AI 匹配語者：書籍語言沒有對應聲線時回傳 0 指派與明確訊息（不得假成功）。"""
    provider_id = db.create_tts_provider({
        "name": "無聲TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(provider_id, [{"id": "af_bella", "name": "Bella", "lang": "en"}])
    book = {
        "category": "zh", "categories": ["zh"],
        "speakerInfo": {"主角": {"gender": "女", "age": "青年", "count": 3}},
        "voices": {}, "voicePrefs": {},
    }
    res = voice_svc.ai_match(book)
    assert res["total"] == 1
    assert res["assigned"] == 0
    assert res["matches"] == []
    assert "沒有支援" in res["message"]


def test_ai_match_gender_normalized_from_provider_metadata():
    """provider 的 gender（female/male）應對映為中文性別，啟發式才能正確配對。"""
    from backend.services.voice import _gender_zh
    assert _gender_zh("female") == "女"
    assert _gender_zh("male") == "男"
    assert _gender_zh("女性") == "女"
    assert _gender_zh("男性") == "男"
    assert _gender_zh("女") == "女"
    assert _gender_zh("unknown") == "未知"


def test_ai_match_never_reuses_voice_for_speakers_in_same_chapter(client, monkeypatch):
    """同章不同角色不得因 AI 回傳相同 primary 而共用聲線；應依 alternatives 選下一個合法聲線。"""
    pid = db.create_tts_provider({
        "name": "同章唯一聲線", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(pid, [
        {"id": "v-zh-1", "name": "聲線一", "lang": "zh", "gender": "unknown"},
        {"id": "v-zh-2", "name": "聲線二", "lang": "zh", "gender": "unknown"},
    ])
    monkeypatch.setattr(db, "get_default_ai_provider", lambda: {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    })

    class FakeCompletions:
        def create(self, **kwargs):
            class Message:
                content = '{"matches":[' \
                    '{"speaker":"甲","voice_id":"v-zh-1","alternatives":["v-zh-2"]},' \
                    '{"speaker":"乙","voice_id":"v-zh-1","alternatives":["v-zh-2"]}' \
                    ']}'
            class Choice:
                message = Message()
            class Response:
                choices = [Choice()]
            return Response()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(voice_svc, "_openai_client", lambda provider: FakeClient())
    result = voice_svc.ai_match({
        "category": "zh", "categories": ["zh"], "voices": {},
        "voicePrefs": {"v-zh-1": {"reuse": True}, "v-zh-2": {"reuse": True}},
        "speakerInfo": {
            "甲": {"gender": "未知", "count": 8},
            "乙": {"gender": "未知", "count": 7},
        },
        "speakerChapters": {"0": {"甲": {}, "乙": {}}},
    })

    assigned = {item["speaker"]: item["voice_id"] for item in result["matches"]}
    assert assigned == {"甲": "v-zh-1", "乙": "v-zh-2"}
    assert result["assigned"] == 2


def test_ai_match_honors_one_time_voice_preference(client, monkeypatch):
    """UI 的「僅一次」偏好要在 AI 回傳重複 primary 時阻止第二個角色重用。"""
    pid = db.create_tts_provider({
        "name": "僅一次偏好", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(pid, [
        {"id": "v-zh-1", "name": "聲線一", "lang": "zh", "gender": "unknown"},
        {"id": "v-zh-2", "name": "聲線二", "lang": "zh", "gender": "unknown"},
    ])
    monkeypatch.setattr(db, "get_default_ai_provider", lambda: {
        "id": 32, "provider_type": "openai", "base_url": "https://ai.example.com",
        "model": "gpt-4o-mini", "secret_ciphertext": "secret",
    })

    class FakeCompletions:
        def create(self, **kwargs):
            class Message:
                content = '{"matches":[' \
                    '{"speaker":"甲","voice_id":"v-zh-1"},' \
                    '{"speaker":"乙","voice_id":"v-zh-1","alternatives":["v-zh-2"]}' \
                    ']}'
            class Choice:
                message = Message()
            class Response:
                choices = [Choice()]
            return Response()

    class FakeClient:
        chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(voice_svc, "_openai_client", lambda provider: FakeClient())
    result = voice_svc.ai_match({
        "category": "zh", "categories": ["zh"], "voices": {},
        "voicePrefs": {"v-zh-1": {"reuse": False}, "v-zh-2": {"reuse": True}},
        "speakerInfo": {
            "甲": {"gender": "未知", "count": 8},
            "乙": {"gender": "未知", "count": 7},
        },
        "speakerChapters": {"0": {"甲": {}, "乙": {}}},
    })

    assigned = {item["speaker"]: item["voice_id"] for item in result["matches"]}
    assert assigned == {"甲": "v-zh-1", "乙": "v-zh-2"}


def test_ai_match_endpoint_uses_and_persists_current_preferences(client, monkeypatch):
    """作者尚未按「儲存全部聲線」時，匹配請求也必須使用畫面當下偏好；reset voices 亦須落到 DB。"""
    operator = new_admin("匹配偏好操作員")
    book = upload_book(operator, text="第一章\n" + "角色甲說話，這是一段足夠長的測試內容。角色乙回答，這也是一段足夠長的測試內容。\n" * 5)
    db.update_book(book["id"], {
        "speaker_info": json.dumps({
            "甲": {"gender": "未知", "count": 2},
            "乙": {"gender": "未知", "count": 1},
        }, ensure_ascii=False),
        "speaker_chapters": json.dumps({"0": {"甲": {}, "乙": {}}}, ensure_ascii=False),
        "voices": json.dumps({"甲": "old-voice", "乙": "old-voice", "_english": "old-en"}),
    })
    captured = {}

    def fake_ai_match(book_dict):
        captured.update(book_dict)
        return {"matches": [{"speaker": "甲", "voice_id": "v-zh-1"}], "total": 2, "assigned": 1}

    monkeypatch.setattr(voice_svc, "ai_match", fake_ai_match)
    prefs = {"v-zh-1": {"exclude": True, "reuse": False}}
    r = operator.post(
        f"/api/books/{book['id']}/voices/ai-match",
        json={"prefs": prefs, "voices": {"_english": "new-en"}},
    )

    assert r.status_code == 200, r.text
    assert captured["voicePrefs"] == prefs
    assert captured["voices"] == {"_english": "new-en"}
    saved = db.get_book_row(book["id"])
    assert json.loads(saved["voice_prefs"]) == prefs
    assert json.loads(saved["voices"]) == {"_english": "new-en", "甲": "v-zh-1"}


def _ready_analysis(row, ch):
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
            "speakers": [{"name": "旁白"}, {"name": "主角"}],
            "segments": [{"id": "s1", "text": "內容", "speaker": "旁白", "emotion": {"value": "neutral"}}],
        }, f, ensure_ascii=False)
    db.mark_analysis_ready(aid, rel)


def test_api_multi_preflight_accepts_assigned_voices_and_rejects_unknown(client):
    """multi preflight：語者已指派且聲線在 catalog → 200 enqueue；
    指派到不在 catalog 的聲線 → 422，不建立 job。"""
    from backend.tests.conftest import new_admin
    pid = db.create_tts_provider({
        "name": "preflightTTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(pid, [
        {"id": "v-zh-1", "name": "女聲", "lang": "zh", "gender": "女"},
        {"id": "v-zh-2", "name": "男聲", "lang": "zh", "gender": "男"},
    ])
    a = new_admin("preflight生成操作員")
    book = upload_book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    db.update_book(book["id"], {"audio_mode": "multi"})
    _ready_analysis(row, ch)
    # 全部指派且合法 → 200
    db.update_book(book["id"], {"voices": json.dumps({"旁白": "v-zh-1", "主角": "v-zh-2"})})
    r = a.post(f"/api/books/{book['id']}/chapters/{ch['seq']}/audio-generations", json={"mode": "multi"})
    assert r.status_code == 200, r.text
    # 指派到 catalog 外的聲線 → 422 且無 job
    db.update_book(book["id"], {"voices": json.dumps({"旁白": "en-US-JennyNeural", "主角": "v-zh-2"})})
    r2 = a.post(f"/api/books/{book['id']}/chapters/{ch['seq']}/audio-generations", json={"mode": "multi"})
    assert r2.status_code == 422, r2.text
    assert "語者清單" in r2.text
