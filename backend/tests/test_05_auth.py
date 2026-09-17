"""認證：註冊／登入／登出／me／重複名／舊式管理員登入。"""
from backend import db
from backend.main import app as built_app
from backend.tests.conftest import reg, new_user, PW


def test_register_me_logout_login(client):
    reg(client, "小明")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["authed"] is True
    assert r.json()["user"]["role"] == "reader"

    client.post("/api/auth/logout")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["authed"] is False

    r = client.post("/api/auth/login", json={"username": "小明", "password": PW})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "小明"


def test_register_duplicate_409(client):
    reg(client, "阿花")
    r = client.post("/api/auth/register", json={"username": "阿花", "password": PW,
                                                "email": "duplicate@example.test"})
    assert r.status_code == 409


def test_login_wrong_password_401(client):
    reg(client, "阿花")
    r = client.post("/api/auth/login", json={"username": "阿花", "password": "wrongpw"})
    assert r.status_code == 401


def test_short_password_400(client):
    r = client.post("/api/auth/register", json={"username": "短密碼", "password": "123"})
    assert r.status_code == 400


def test_legacy_admin_login_without_username(client):
    r = client.post("/api/auth/login", json={"password": "adminpass"})
    assert r.status_code == 200
    r = client.get("/api/auth/me")
    assert r.json()["authed"] is True
    assert r.json()["user"]["role"] == "admin"


def test_legacy_admin_login_wrong_pw_401(client):
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401


def test_unauth_me(client):
    r = client.get("/api/auth/me")
    assert r.json()["authed"] is False


def test_dev_mode_requires_explicit_bypass():
    """L0-2：DB 無使用者 ≠ admin。預設（DEV_AUTH_BYPASS 未開）未登入即訪客。"""
    from fastapi.testclient import TestClient
    from backend import settings

    with TestClient(built_app) as c:
        db.clear_tables()
        assert not db.any_users()
        me = c.get("/api/auth/me").json()
        assert me["authed"] is False
        # 不能以上傳當 admin（401）
        r = c.post("/api/books", files={"file": ("dev.txt", ("第一章 標題。\n內容。" * 30).encode("utf-8"), "text/plain")})
        assert r.status_code == 401

        # 顯式開啟 DEV_AUTH_BYPASS 且非 production → 維持 dev 全開行
        settings.DEV_AUTH_BYPASS = True
        try:
            me = c.get("/api/auth/me").json()
            assert me["authed"] is True
            assert me["user"]["role"] == "admin"
            assert me["user"]["dev"] is True
        finally:
            settings.DEV_AUTH_BYPASS = False


def test_dev_bypass_disabled_in_production():
    """L0-2：production 環境即使 DEV_AUTH_BYPASS=true 也不啟用。"""
    from fastapi.testclient import TestClient
    from backend import settings

    orig_env, orig_bypass, orig_key = settings.APP_ENV, settings.DEV_AUTH_BYPASS, settings.AI_API_KEY
    settings.APP_ENV = "production"
    settings.DEV_AUTH_BYPASS = True
    settings.AI_API_KEY = "test-key"
    try:
        with TestClient(built_app) as c:
            db.clear_tables()
            assert not db.any_users()
            me = c.get("/api/auth/me").json()
            assert me["authed"] is False
    finally:
        settings.APP_ENV, settings.DEV_AUTH_BYPASS, settings.AI_API_KEY = orig_env, orig_bypass, orig_key


def test_validate_env_blocks_weak_production():
    """L0-1：production 缺 AI key 或弱 Admin 密碼 → validate_env 拒絕啟動。"""
    from backend import settings

    orig_env, orig_pw, orig_key = settings.APP_ENV, settings.ADMIN_PASSWORD, settings.AI_API_KEY
    settings.APP_ENV = "production"
    try:
        settings.AI_API_KEY = ""
        settings.ADMIN_PASSWORD = "adminpass"
        try:
            settings.validate_env()
            assert False, "應因缺 AI_API_KEY 而拋錯"
        except RuntimeError as e:
            assert "AI_API_KEY" in str(e)

        settings.AI_API_KEY = "ok-key"
        settings.ADMIN_PASSWORD = "123"  # 太短
        try:
            settings.validate_env()
            assert False, "應因 ADMIN_PASSWORD 太短而拋錯"
        except RuntimeError as e:
            assert "ADMIN_PASSWORD" in str(e)

        settings.ADMIN_PASSWORD = "good-pass-1"
        settings.validate_env()  # 全合格 → 不拋
    finally:
        settings.APP_ENV, settings.ADMIN_PASSWORD, settings.AI_API_KEY = orig_env, orig_pw, orig_key


def test_login_without_admin_password_401(client):
    """L0-2：ADMIN_PASSWORD 未設定時，舊式（只送密碼）登入回 401 而非 200。"""
    from backend import settings

    orig = settings.ADMIN_PASSWORD
    settings.ADMIN_PASSWORD = ""
    try:
        r = client.post("/api/auth/login", json={"password": "anything"})
        assert r.status_code == 401
    finally:
        settings.ADMIN_PASSWORD = orig


def test_login_brute_force_lockout(client):
    """L0-3：同 IP+帳號失敗 5 次後鎖定，第 6 次回 429；成功登入清除計數。"""
    from backend.routers import auth as auth_mod
    from backend import settings

    reg(client, "防破")
    # 5 次錯誤密碼
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": "防破", "password": "wrongpw"})
        assert r.status_code == 401
    # 第 6 次即使密碼正確也被鎖 → 429
    r = client.post("/api/auth/login", json={"username": "防破", "password": PW})
    assert r.status_code == 429
    # 清空限流後可再試
    auth_mod._login_failures.clear()
    auth_mod._login_lockouts.clear()
    r = client.post("/api/auth/login", json={"username": "防破", "password": PW})
    assert r.status_code == 200

    # 成功登入應清除該鍵的失敗計數
    for _ in range(4):
        client.post("/api/auth/login", json={"username": "防破", "password": "wrongpw"})
    r = client.post("/api/auth/login", json={"username": "防破", "password": PW})
    assert r.status_code == 200
    fake_req = type("R", (), {"client": type("C", (), {"host": "testclient"})()})()
    key = auth_mod._login_key(fake_req, "防破")
    assert len(auth_mod._login_failures.get(key, [])) == 0
