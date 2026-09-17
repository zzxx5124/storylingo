"""V4 Phase 3：AI provider foundation regression tests.

驗證：
- Admin 可建立／列出／更新／刪除／測試 AI provider 並選取 default。
- 非 admin 不可存取 mutation 或 secrets。
- Secret 永不透過 API 回傳。
- SSRF／private target 在 production 被拒絕。
- 單一 default invariant（含 DB 部分唯一索引）。
- App 以零個 AI provider 仍可啟動。
"""
import httpx

from backend import db, settings
from backend.services import ai_provider, netsec
from backend.tests.conftest import new_admin, new_user


def test_app_starts_with_zero_ai_providers(client):
    assert client.get("/api/health").status_code == 200
    assert db.list_ai_providers() == []
    assert db.get_default_ai_provider() is None


def test_admin_crud_ai_provider_without_exposing_secret():
    admin = new_admin("AI管理員")
    created = admin.post("/api/admin/ai/providers", json={
        "name": "測試 AI 服務",
        "providerType": "openai_compatible",
        "baseUrl": "https://ai.example.com/v1",
        "model": "gpt-test",
        "fallbackModel": "gpt-test-fallback",
        "apiKey": "sk-test-secret",
    })
    assert created.status_code == 200, created.text
    provider = created.json()
    assert provider["name"] == "測試 AI 服務"
    assert provider["hasSecret"] is True
    assert provider["configVersion"] == 1
    assert "sk-test-secret" not in created.text

    listed = admin.get("/api/admin/ai/providers").json()["items"]
    assert listed[0]["baseUrl"] == "https://ai.example.com/v1"
    assert listed[0]["model"] == "gpt-test"
    assert "sk-test-secret" not in admin.get("/api/admin/ai/providers").text

    updated = admin.put(f"/api/admin/ai/providers/{provider['id']}",
                        json={"model": "gpt-updated", "apiKey": "sk-new-secret"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["model"] == "gpt-updated"
    assert updated.json()["configVersion"] == 2
    assert "sk-new-secret" not in updated.text

    # 非設定欄位（狀態／檢查時間）更新不得提升 config_version
    ai_provider.save_check_result(provider["id"], {"ok": False, "error": "boom"})
    row = db.get_ai_provider(provider["id"])
    assert row["config_version"] == 2
    assert row["last_status"] == "error"
    assert row["last_error"] == "boom"

    audit = admin.get("/api/admin/audit-logs")
    assert any(item["action"] == "create_ai_provider" for item in audit.json()["items"])
    assert any(item["action"] == "update_ai_provider" for item in audit.json()["items"])

    assert admin.delete(f"/api/admin/ai/providers/{provider['id']}").status_code == 200
    assert admin.get("/api/admin/ai/providers").json()["items"] == []


def test_create_rejects_non_string_secret_and_duplicate_name():
    admin = new_admin("AI輸入驗證")
    payload = {
        "name": "唯一 AI 服務", "providerType": "openai",
        "baseUrl": "https://api.openai.com/v1", "model": "gpt-test",
    }
    bad_secret = admin.post("/api/admin/ai/providers", json={**payload, "apiKey": 123})
    assert bad_secret.status_code == 400
    assert "API key" in bad_secret.text
    missing_model = admin.post("/api/admin/ai/providers", json={
        "name": "缺少模型", "providerType": "openai",
        "baseUrl": "https://api.openai.com/v1",
    })
    assert missing_model.status_code == 400

    created = admin.post("/api/admin/ai/providers", json={**payload, "apiKey": "test-only-key"})
    assert created.status_code == 200, created.text
    duplicate = admin.post("/api/admin/ai/providers", json=payload)
    assert duplicate.status_code == 400
    assert "名稱已存在" in duplicate.text


def test_create_accepts_all_supported_provider_types():
    admin = new_admin("AI類型驗證")
    for index, provider_type in enumerate(("openai", "openai_compatible", "deepseek")):
        response = admin.post("/api/admin/ai/providers", json={
            "name": f"類型服務 {index}", "providerType": provider_type,
            "baseUrl": "https://ai.example.com/v1", "model": "test-model",
            "apiKey": "test-only-key",
        })
        assert response.status_code == 200, response.text


def test_non_admin_cannot_access_ai_provider_mutations():
    reader = new_user("AI普通使用者")
    assert reader.get("/api/admin/ai/providers").status_code in (401, 403)
    assert reader.post("/api/admin/ai/providers", json={"name": "X", "baseUrl": "https://ai.example.com", "model": "m"}).status_code in (401, 403)
    assert reader.put("/api/admin/ai/providers/1", json={"name": "X"}).status_code in (401, 403)
    assert reader.delete("/api/admin/ai/providers/1").status_code in (401, 403)
    assert reader.post("/api/admin/ai/providers/1/test").status_code in (401, 403)


def test_secret_never_returned_by_db_read():
    admin = new_admin("AI機密管理")
    created = admin.post("/api/admin/ai/providers", json={
        "name": "機密服務", "providerType": "openai_compatible", "baseUrl": "https://ai.example.com", "model": "m", "apiKey": "sk-topsecret",
    }).json()
    row = db.get_ai_provider(created["id"])
    assert row["secret_ciphertext"]
    assert "sk-topsecret" not in str(row)
    pub = ai_provider.public_provider(row)
    assert "secret" not in pub
    assert "sk-topsecret" not in str(pub)


def test_single_default_invariant():
    admin = new_admin("AI預設管理")
    a = admin.post("/api/admin/ai/providers", json={
        "name": "預設A", "providerType": "openai_compatible", "baseUrl": "https://ai-a.example.com", "model": "m", "isDefault": True,
    }).json()
    b = admin.post("/api/admin/ai/providers", json={
        "name": "預設B", "providerType": "openai_compatible", "baseUrl": "https://ai-b.example.com", "model": "m", "isDefault": True,
    }).json()
    # 建立 B 後，A 不再是 default
    assert db.get_ai_provider(a["id"])["is_default"] == 0
    assert db.get_ai_provider(b["id"])["is_default"] == 1
    assert db.get_default_ai_provider()["id"] == b["id"]
    # 單一 default invariant：只有一個
    defaults = [p for p in db.list_ai_providers() if p["is_default"]]
    assert len(defaults) == 1

    # 刪除 default 後，無 default（合法狀態，不得 fallback）
    admin.delete(f"/api/admin/ai/providers/{b['id']}")
    assert db.get_default_ai_provider() is None
    assert [p for p in db.list_ai_providers() if p["is_default"]] == []


def test_switching_default_does_not_bump_provider_capability_version():
    admin = new_admin("AI預設能力快取")
    first = admin.post("/api/admin/ai/providers", json={
        "name": "能力預設A", "providerType": "openai", "baseUrl": "https://a.example.com/v1",
        "model": "m-a", "isDefault": True,
    }).json()
    second = admin.post("/api/admin/ai/providers", json={
        "name": "能力預設B", "providerType": "openai", "baseUrl": "https://b.example.com/v1",
        "model": "m-b",
    }).json()
    before = db.get_ai_provider(second["id"])["config_version"]

    db.update_ai_provider(second["id"], {"is_default": True})

    assert db.get_ai_provider(first["id"])["is_default"] == 0
    assert db.get_ai_provider(second["id"])["is_default"] == 1
    assert db.get_ai_provider(second["id"])["config_version"] == before


def test_db_level_single_default_constraint():
    # 繞過 service 的自動清除，直接 SQL 插入兩個 default → DB 部分唯一索引應拒絕
    now = db.ts()
    sql = ("INSERT INTO ai_providers(name, provider_type, base_url, model, is_default, created_at, updated_at) "
           "VALUES(?, 'openai_compatible', ?, ?, 1, ?, ?)")
    db.execute(sql, ("DB預設A", "https://a.example.com", "m", now, now))
    try:
        db.execute(sql, ("DB預設B", "https://b.example.com", "m", now, now))
        assert False, "第二個 default 應被 DB 部分唯一索引拒絕"
    except Exception as error:
        assert "UNIQUE" in str(error)


def test_ssrf_private_target_rejected_in_production():
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        for url in ("http://127.0.0.1:9000", "http://localhost:9000"):
            try:
                ai_provider.validate_payload({
                    "name": "不合法", "providerType": "openai_compatible", "baseUrl": url, "model": "m",
                })
                assert False, f"production 不應允許 {url}"
            except ValueError as error:
                assert "HTTPS" in str(error) or "內部" in str(error) or "本機" in str(error)
    finally:
        settings.APP_ENV = original


def test_ai_provider_rejects_bad_type_and_invalid_url():
    try:
        ai_provider.validate_payload({"name": "壞型別", "baseUrl": "https://ai.example.com", "model": "m", "providerType": "banana"})
        assert False
    except ValueError:
        pass
    try:
        ai_provider.validate_payload({"name": "壞URL", "providerType": "openai_compatible", "baseUrl": "ftp://ai.example.com", "model": "m"})
        assert False
    except ValueError:
        pass
    try:
        ai_provider.validate_payload({"name": "無model", "providerType": "openai_compatible", "baseUrl": "https://ai.example.com"})
        assert False
    except ValueError:
        pass


def test_test_connection_success_and_failure(monkeypatch):
    real_client = ai_provider.httpx.Client

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer sk-conn"
        return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(ai_provider.httpx, "Client", lambda *args, **kwargs: real_client(transport=transport, **kwargs))
    row = {"base_url": "https://ai.example.com/v1", "secret_ciphertext": ai_provider.encrypt_secret("sk-conn")}
    result = ai_provider.test_connection(row)
    assert result["ok"] is True
    assert result["models"] == 2

    def failing_handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    transport2 = httpx.MockTransport(failing_handler)
    monkeypatch.setattr(ai_provider.httpx, "Client", lambda *args, **kwargs: real_client(transport=transport2, **kwargs))
    result2 = ai_provider.test_connection(row)
    assert result2["ok"] is False


def test_netsec_shared_helper_used_by_both_providers():
    from backend.services import tts_provider
    assert tts_provider.validate_base_url("https://tts.example.com") == "https://tts.example.com"
    assert netsec.validate_http_url("https://ai.example.com/v1") == "https://ai.example.com/v1"
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        try:
            netsec.validate_http_url("http://10.0.0.5:8000")
            assert False
        except ValueError:
            pass
    finally:
        settings.APP_ENV = original
