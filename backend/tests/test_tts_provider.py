"""遠端 TTS provider 設定與安全邊界。"""

import json
import httpx

from backend import settings
from backend import db
from backend.services import tts_provider
from backend.tests.conftest import new_admin, new_user


def test_admin_can_manage_tts_provider_without_exposing_secret():
    admin = new_admin("TTS管理員")
    created = admin.post("/api/admin/tts/providers", json={
        "name": "遠端測試服務",
        "providerType": "generic_http",
        "baseUrl": "https://tts.example.com",
        "synthPath": "/v1/synthesize",
        "voicesPath": "/v1/voices",
        "authScheme": "bearer",
        "apiKey": "test-secret-value",
    })
    assert created.status_code == 200, created.text
    provider = created.json()
    assert provider["name"] == "遠端測試服務"
    assert provider["hasSecret"] is True
    assert "test-secret-value" not in created.text

    guest = new_user("TTS普通使用者")
    assert guest.get("/api/admin/tts/providers").status_code in (401, 403)
    assert admin.get("/api/admin/tts/providers").json()["items"][0]["baseUrl"] == "https://tts.example.com"
    audit = admin.get("/api/admin/audit-logs")
    assert audit.status_code == 200
    assert any(item["action"] == "create_tts_provider" for item in audit.json()["items"])

    updated = admin.put(f"/api/admin/tts/providers/{provider['id']}", json={"enabled": False})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert admin.delete(f"/api/admin/tts/providers/{provider['id']}").status_code == 200


def test_provider_url_blocks_private_hosts_in_production():
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        try:
            tts_provider.validate_base_url("http://127.0.0.1:9000")
            assert False, "production 不應允許 HTTP/private provider URL"
        except ValueError as error:
            assert "HTTPS" in str(error) or "內部" in str(error) or "本機" in str(error)
    finally:
        settings.APP_ENV = original


def test_provider_paths_cannot_escape_base_url():
    try:
        tts_provider.validate_payload({
            "name": "不合法服務",
            "baseUrl": "https://tts.example.com",
            "voicesPath": "/../secrets",
        })
        assert False, "provider path 不應允許 .."
    except ValueError as error:
        assert "path" in str(error)


def test_remote_provider_can_write_audio_response(tmp_path, monkeypatch):
    real_client = tts_provider.httpx.Client

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/synthesize"
        assert request.headers["accept"].startswith("audio/mpeg")
        assert request.read().decode("utf-8").find("測試文字") >= 0
        return httpx.Response(200, content=b"ID3-test-audio", headers={"content-type": "audio/mpeg"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(tts_provider.httpx, "Client", lambda *args, **kwargs: real_client(transport=transport, **kwargs))
    row = {
        "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "synth_path": "/synthesize", "auth_scheme": "none", "secret_ciphertext": "",
    }
    output = tmp_path / "sample.mp3"
    tts_provider.synthesize_to_file(row, "測試文字", "voice-1", str(output))
    assert output.read_bytes() == b"ID3-test-audio"


def test_remote_voice_catalog_is_not_exposed_to_readers():
    admin = new_admin("TTS聲音管理")
    created = admin.post("/api/admin/tts/providers", json={
        "name": "聲音清單服務", "baseUrl": "https://tts.example.com",
    }).json()
    db.replace_tts_provider_voices(created["id"], [{"id": "voice-remote-1", "name": "遠端女聲", "lang": "zh", "languages": ["zh-TW", "zh-CN"]}])
    reader = new_user("TTS聲音讀者")
    assert reader.get("/api/voices").json()["voices"] == []
    voice = next(v for v in admin.get("/api/voices").json()["voices"] if v["id"] == "voice-remote-1")
    assert voice["languages"] == ["zh-TW", "zh-CN"]


def test_voice_catalog_preserves_provider_language_array():
    provider_id = db.create_tts_provider({"name": "語言 catalog", "provider_type": "generic_http", "base_url": "https://tts.example.com"})
    db.replace_tts_provider_voices(provider_id, [
        {"id": "af_heart", "name": "Heart", "languages": ["en-US", "en-GB"]},
        {"id": "cosy_mimi", "name": "Mimi", "languages": ["zh-TW", "zh-CN", "en-US", "en-GB"]},
    ])
    rows = db.list_tts_provider_voices(provider_id)
    assert json.loads(next(row["languages_json"] for row in rows if row["voice_id"] == "af_heart")) == ["en-US", "en-GB"]
    assert next(row["lang"] for row in rows if row["voice_id"] == "cosy_mimi") == "zh"


def test_voice_catalog_uses_provider_display_name_for_ui_and_matching():
    provider_id = db.create_tts_provider({"name": "display name catalog", "provider_type": "generic_http", "base_url": "https://tts.example.com"})
    db.replace_tts_provider_voices(provider_id, [
        {"id": "cosy_mimi", "display_name": "Mimi 中文女聲", "language": "zh-TW"},
    ])
    row = db.list_tts_provider_voices(provider_id)[0]
    assert row["voice_id"] == "cosy_mimi"
    assert row["name"] == "Mimi 中文女聲"


def test_voice_catalog_maps_v2_expressive_capability_and_age():
    admin = new_admin("TTSV2catalog")
    provider = admin.post("/api/admin/tts/providers", json={
        "name": "V2 catalog mapping", "baseUrl": "https://tts.example.com",
    }).json()
    expressive = {"supported_emotions": ["neutral", "happy", "sad"], "supports_intensity": False}
    db.replace_tts_provider_voices(provider["id"], [{
        "id": "cosy_mimi", "display_name": "Mimi", "language": "zh-TW",
        "age": "adult", "expressive": expressive,
    }])

    row = db.list_tts_provider_voices(provider["id"])[0]
    assert row["age"] == "adult"
    assert json.loads(row["capabilities_json"]) == expressive
    voice = next(item for item in admin.get("/api/voices").json()["voices"] if item["id"] == "cosy_mimi")
    assert voice["age"] == "adult"
    assert voice["expressiveCapability"] == expressive


def test_voice_catalog_uses_active_provider_only():
    admin = new_admin("ActiveProviderCatalog")
    active_id = db.create_tts_provider({
        "name": "Active catalog provider", "provider_type": "generic_http",
        "base_url": "https://active.example.com", "is_default": True,
    })
    other_id = db.create_tts_provider({
        "name": "Inactive catalog provider", "provider_type": "generic_http",
        "base_url": "https://other.example.com",
    })
    db.replace_tts_provider_voices(active_id, [{"id": "active-voice", "name": "Active"}])
    db.replace_tts_provider_voices(other_id, [{"id": "other-voice", "name": "Other"}])

    ids = {item["id"] for item in admin.get("/api/voices").json()["voices"]}
    assert ids == {"active-voice"}
