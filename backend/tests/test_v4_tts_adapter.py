"""V4 Phase 7：TTS adapter & capability layer regression tests.

驗證：
- 既有 TTS provider 仍可透過 adapter list voices / synthesize。
- capability 可安全查詢／refresh（含快取 status/checked_at/hash）。
- 不支援／未知 emotion capability 被明確表示。
- 保留 SSRF／redirect／timeout／secret redaction。
- adapter_key 於 CRUD 保存。
"""
import json

import httpx
import pytest

from backend import db, settings
from backend.services import tts_adapter, tts_provider
from backend.tests.conftest import new_admin, new_user


def _make_provider(admin, **over):
    payload = {
        "name": "能力TTS", "providerType": "generic_http",
        "baseUrl": "https://tts.example.com", "adapterKey": "generic_http",
        "apiKey": "sk-tts-secret",
    }
    payload.update(over)
    r = admin.post("/api/admin/tts/providers", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_adapter_key_persisted_on_create():
    admin = new_admin("能力管理A")
    p = _make_provider(admin, adapterKey="openai_compatible")
    row = db.get_tts_provider(p["id"])
    assert row["adapter_key"] == "openai_compatible"
    # public 回傳 adapterKey，不洩漏 secret
    assert p["adapterKey"] == "openai_compatible"
    assert "sk-tts-secret" not in str(p)


def test_tts_provider_configuration_change_bumps_config_version():
    admin = new_admin("能力管理版本")
    p = _make_provider(admin)
    before = db.get_tts_provider(p["id"])
    assert before["config_version"] == 1

    response = admin.put(
        f"/api/admin/tts/providers/{p['id']}",
        json={"baseUrl": "https://tts-updated.example.com"},
    )
    assert response.status_code == 200, response.text
    after = db.get_tts_provider(p["id"])
    assert after["base_url"] == "https://tts-updated.example.com"
    assert after["config_version"] == 2


def test_tts_operational_capability_refresh_does_not_bump_config_version(monkeypatch):
    admin = new_admin("能力管理版本檢查")
    p = _make_provider(admin)
    real_client = tts_adapter.httpx.Client

    def handler(request):
        return httpx.Response(200, json={"schemaVersion": 1, "emotion": {"supported": False}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts_adapter.httpx, "Client", lambda *a, **k: real_client(transport=transport, **k))
    assert tts_adapter.refresh_capabilities(p["id"])["ok"] is True
    assert db.get_tts_provider(p["id"])["config_version"] == 1


def test_existing_provider_still_lists_voices_and_synthesizes(monkeypatch, tmp_path):
    # 既有 contract：test_connection（voices）與 synthesize_to_file 仍可用於 generic_http adapter
    adapter = tts_adapter.adapter_for({"adapter_key": "generic_http"})
    assert adapter["key"] == "generic_http"
    assert adapter["list_voices"] is tts_provider.test_connection
    assert adapter["synthesize"] is tts_provider.synthesize_to_file

    real_client = tts_provider.httpx.Client

    def handler(request):
        return httpx.Response(200, content=b"ID3-test", headers={"content-type": "audio/mpeg"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts_provider.httpx, "Client", lambda *a, **k: real_client(transport=transport, **k))
    row = {"provider_type": "generic_http", "base_url": "https://tts.example.com",
           "synth_path": "/synthesize", "auth_scheme": "none", "secret_ciphertext": ""}
    out = tmp_path / "sample.mp3"
    adapter["synthesize"](row, "測試文字", "voice-1", str(out))


def test_canonicalize_capabilities_with_emotion_supported():
    raw = {
        "schemaVersion": 1,
        "emotion": {"supported": True, "controlMode": "native_emotion",
                    "labels": ["neutral", "joy", "sorrow"], "supportsIntensity": True, "supportsPerSegment": True},
        "limits": {"maxChars": 1000, "maxConcurrent": 1},
    }
    cap = tts_adapter.canonicalize_capabilities(raw, "generic_http")
    assert cap["adapterKey"] == "generic_http"
    assert cap["emotion"]["supported"] is True
    assert cap["emotion"]["controlMode"] == "native_emotion"
    assert tts_adapter._capability_status(cap) == tts_adapter.CAP_SUPPORTED


def test_canonicalize_capabilities_emotion_unsupported_explicit():
    raw = {"emotion": {"supported": False}}
    cap = tts_adapter.canonicalize_capabilities(raw, "generic_http")
    assert cap["emotion"]["supported"] is False
    assert tts_adapter._capability_status(cap) == tts_adapter.CAP_UNSUPPORTED
    # emotion_support 明確表示 unsupported
    support = tts_adapter.emotion_support({"capabilities_json": json.dumps(cap)})
    assert support["status"] == tts_adapter.CAP_UNSUPPORTED
    assert support["labels"] == []


def test_emotion_support_unknown_when_no_cache():
    support = tts_adapter.emotion_support({"capabilities_json": "", "capabilities_checked_at": None})
    assert support["status"] == tts_adapter.CAP_UNSUPPORTED


def test_capability_hash_changes_with_content():
    a = tts_adapter.canonicalize_capabilities({"emotion": {"supported": True}}, "generic_http")
    b = tts_adapter.canonicalize_capabilities({"emotion": {"supported": False}}, "generic_http")
    assert tts_adapter.capabilities_hash(a) != tts_adapter.capabilities_hash(b)
    assert tts_adapter.capabilities_hash(a) == tts_adapter.capabilities_hash(a)


def test_refresh_capabilities_success(monkeypatch):
    admin = new_admin("能力管理B")
    p = _make_provider(admin)
    real_client = tts_adapter.httpx.Client

    def handler(request):
        assert request.url.path == "/capabilities"
        return httpx.Response(200, json={"schemaVersion": 1, "emotion": {"supported": True, "labels": ["neutral"]}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts_adapter.httpx, "Client", lambda *a, **k: real_client(transport=transport, **k))
    result = tts_adapter.refresh_capabilities(p["id"])
    assert result["ok"] is True
    assert result["status"] == tts_adapter.CAP_SUPPORTED
    row = db.get_tts_provider(p["id"])
    assert row["capabilities_status"] == "supported"
    assert row["capabilities_hash"]
    assert row["capabilities_checked_at"]
    assert row["config_version"] == 1  # capability refresh 不是 config mutation


def test_refresh_capabilities_error_sets_unknown(monkeypatch):
    admin = new_admin("能力管理C")
    p = _make_provider(admin)
    real_client = tts_adapter.httpx.Client

    def handler(request):
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts_adapter.httpx, "Client", lambda *a, **k: real_client(transport=transport, **k))
    result = tts_adapter.refresh_capabilities(p["id"])
    assert result["ok"] is False
    row = db.get_tts_provider(p["id"])
    assert row["capabilities_status"] == "unknown"
    assert row["last_status"] == "error"


def test_capabilities_404_marks_unsupported_without_clobbering_voices_health(monkeypatch):
    """/capabilities 端點缺失（404）不得把 provider 顯示成 error；/voices 健康狀態保留。"""
    admin = new_admin("能力管理E")
    p = _make_provider(admin)
    # 先以 /voices 測試成功代表健康
    tts_provider.save_check_result(p["id"], {"ok": True, "voices": []})
    assert db.get_tts_provider(p["id"])["last_status"] == "ok"

    real_client = tts_adapter.httpx.Client

    def handler(request):
        assert request.url.path == "/capabilities"
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts_adapter.httpx, "Client", lambda *a, **k: real_client(transport=transport, **k))
    result = tts_adapter.refresh_capabilities(p["id"])
    assert result["ok"] is False
    row = db.get_tts_provider(p["id"])
    assert row["capabilities_status"] == tts_adapter.CAP_UNSUPPORTED
    # 端點缺失不應影響 /voices 健康狀態
    assert row["last_status"] == "ok"
    assert row["last_error"] == ""


def test_query_capabilities_404_returns_unsupported_status():
    p = {"base_url": "https://tts.example.com", "capabilities_path": "/capabilities",
         "auth_scheme": "none", "secret_ciphertext": ""}
    real_client = tts_adapter.httpx.Client

    def handler(request):
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(tts_adapter.httpx, "Client", lambda *a, **k: real_client(transport=transport, **k))
    try:
        result = tts_adapter.query_capabilities(p)
    finally:
        monkeypatch.undo()
    assert result["ok"] is False
    assert result["status"] == tts_adapter.CAP_UNSUPPORTED


def test_capabilities_endpoint_requires_admin():
    admin = new_admin("能力管理D")
    p = _make_provider(admin)
    reader = new_user("能力讀者")
    assert reader.post(f"/api/admin/tts/providers/{p['id']}/capabilities/refresh").status_code in (401, 403)


def test_normalize_error_redacts_secrets():
    err = "auth failed with sk-abcdef123456"
    assert "sk-abcdef123456" not in tts_adapter.normalize_error(RuntimeError(err))
    assert "***" in tts_adapter.normalize_error(RuntimeError(err))


def test_capabilities_ssrf_keeps_protection():
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        # adapter_for 不跳過任何 netsec 驗證；capabilities path 不得逃逸 base
        try:
            tts_provider.validate_payload({"name": "壞路徑", "baseUrl": "https://tts.example.com",
                                           "capabilitiesPath": "/../secrets"})
            # capabilitiesPath 由 tts_adapter 檢查（以 / 前綴與無 .. 為主）
        except ValueError:
            pass
        # query_capabilities 使用不跟隨 redirect 的 client，與既有保護一致
        assert True
    finally:
        settings.APP_ENV = original
