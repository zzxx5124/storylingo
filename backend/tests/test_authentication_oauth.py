"""Authentication/OAuth approved contract regressions with fake mail/OIDC."""
import pytest
import sqlite3
import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import db, settings
from backend.services.auth_inventory import collect_auth_inventory
from backend.services import auth_tokens, mail, oauth
from backend.tests.conftest import PW, reg


@pytest.fixture
def fake_oidc():
    provider = oauth.FakeOIDCProvider()
    old = (settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET,
           settings.GOOGLE_ISSUER, settings.GOOGLE_REDIRECT_URI)
    settings.GOOGLE_CLIENT_ID = "fake-client"
    settings.GOOGLE_CLIENT_SECRET = "fake-secret"
    settings.GOOGLE_ISSUER = "https://accounts.google.com"
    settings.GOOGLE_REDIRECT_URI = "http://localhost:8000/api/auth/oauth/google/callback"
    oauth.set_provider_for_tests(provider)
    yield provider
    oauth.set_provider_for_tests(None)
    (settings.GOOGLE_CLIENT_ID, settings.GOOGLE_CLIENT_SECRET,
     settings.GOOGLE_ISSUER, settings.GOOGLE_REDIRECT_URI) = old


def _prepare_code(provider, code, *, subject, email, email_verified=True):
    call = provider.authorization_calls[-1]
    provider.claims_by_code[code] = {
        "issuer": settings.GOOGLE_ISSUER,
        "subject": subject,
        "email": email,
        "email_verified": email_verified,
        "nonce": call["nonce"],
    }
    return call["state"]


def test_registration_requires_verified_email_and_recovery_revokes_sessions(client):
    response = client.post("/api/auth/register", json={
        "username": "verify_user", "password": PW, "email": "Verify@Example.test",
    })
    assert response.status_code == 201
    assert client.get("/api/auth/me").json()["authed"] is False
    message = mail.get_fake_mail_adapter().latest(purpose="email_verification", to="verify@example.test")
    assert message and "token=" in message["link"]
    token = message["link"].split("token=", 1)[1]
    assert client.get("/api/auth/verify-email", params={"token": token},
                      follow_redirects=False).status_code == 303
    assert token not in str(db.query("SELECT * FROM auth_tokens"))
    assert token not in str(db.query("SELECT * FROM audit_logs"))
    assert client.post("/api/auth/login", json={"username": "verify_user", "password": PW}).status_code == 200
    forgot = client.post("/api/auth/forgot-password", json={"email": "VERIFY@example.test"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "unknown@example.test"})
    assert forgot.status_code == unknown.status_code == 200
    reset = mail.get_fake_mail_adapter().latest(purpose="password_reset", to="verify@example.test")
    reset_token = reset["link"].split("token=", 1)[1]
    assert client.post("/api/auth/reset-password", json={
        "token": reset_token, "newPassword": "changed123",
    }).status_code == 200
    assert reset_token not in str(db.query("SELECT * FROM auth_tokens"))
    assert reset_token not in str(db.query("SELECT * FROM audit_logs"))
    assert client.post("/api/auth/reset-password", json={
        "token": reset_token, "newPassword": "changed456",
    }).status_code == 400
    assert client.get("/api/auth/me").json()["authed"] is False
    assert client.post("/api/auth/login", json={
        "username": "verify_user", "password": "changed123",
    }).status_code == 200


def test_verification_and_reset_tokens_expire_without_changing_account(client):
    registered = client.post("/api/auth/register", json={
        "username": "expiry_user", "password": PW, "email": "expiry@example.test",
    })
    assert registered.status_code == 201
    verification = mail.get_fake_mail_adapter().latest(purpose="email_verification")
    verification_token = verification["link"].split("token=", 1)[1]
    db.execute("UPDATE auth_tokens SET expires_at=? WHERE purpose='email_verification'",
               ("2000-01-01T00:00:00",))
    assert client.get("/api/auth/verify-email", params={"token": verification_token},
                      follow_redirects=False).status_code == 303
    account = db.get_user_by_bname("expiry_user")
    assert account["email_verified_at"] is None

    db.execute("UPDATE auth_tokens SET expires_at=? WHERE purpose='email_verification'",
               ("2999-01-01T00:00:00",))
    message = auth_tokens.issue_email_verification(account["id"], account["email"], client)
    assert message.ok is True
    verification = mail.get_fake_mail_adapter().latest(purpose="email_verification")
    assert client.get("/api/auth/verify-email",
                      params={"token": verification["link"].split("token=", 1)[1]},
                      follow_redirects=False).status_code == 303
    assert client.post("/api/auth/login", json={"username": "expiry_user", "password": PW}).status_code == 200
    assert client.post("/api/auth/forgot-password", json={"email": "expiry@example.test"}).status_code == 200
    reset = mail.get_fake_mail_adapter().latest(purpose="password_reset")
    reset_token = reset["link"].split("token=", 1)[1]
    db.execute("UPDATE auth_tokens SET expires_at=? WHERE purpose='password_reset'",
               ("2000-01-01T00:00:00",))
    assert client.post("/api/auth/reset-password", json={
        "token": reset_token, "newPassword": "changed123",
    }).status_code == 400
    assert db.get_user_by_bname("expiry_user")["password_hash"]


def test_recovery_and_oauth_only_responses_remain_generic(client, fake_oidc):
    unknown = client.post("/api/auth/forgot-password", json={"email": "unknown@example.test"})
    assert unknown.status_code == 200
    client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "generic-oauth-code", subject="generic-oauth-sub",
                          email="generic-oauth@example.test")
    assert client.get("/api/auth/oauth/google/callback",
                      params={"state": state, "code": "generic-oauth-code"},
                      follow_redirects=False).status_code == 303
    oauth_only = client.post("/api/auth/forgot-password", json={"email": "generic-oauth@example.test"})
    assert oauth_only.status_code == unknown.status_code
    assert oauth_only.json() == unknown.json()


def test_state_changing_auth_route_keeps_production_csrf_guard(client, monkeypatch):
    reg(client, "csrf_auth_user", email="csrf-auth@example.test")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    response = client.post("/api/auth/password", json={"newPassword": "changed123"})
    assert response.status_code == 403


def test_auth_inventory_is_read_only_and_reports_nullable_password_state(client):
    before = db.query("SELECT id, username, email, account_status FROM users ORDER BY id")
    report = collect_auth_inventory()
    after = db.query("SELECT id, username, email, account_status FROM users ORDER BY id")
    assert report["dryRun"] is True
    assert report["checksumUnchanged"] is True
    assert report["checksumBefore"] == report["checksumAfter"]
    assert before == after
    assert "password_hash" not in str(report)


def test_legacy_users_migration_is_rerunnable_and_keeps_foreign_keys(tmp_path):
    path = str(tmp_path / "legacy.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, "
                "password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'reader', created_at TEXT NOT NULL)")
    con.execute("INSERT INTO users(username, password_hash, role, created_at) VALUES('legacy', '1|00|00', 'reader', '2026-01-01')")
    con.commit()
    con.close()
    db.close_all()
    settings.DB_PATH = path
    settings.ADMIN_PASSWORD = ""
    db.init_db()
    db.init_db()
    columns = {row["name"]: row for row in db.query("PRAGMA table_info(users)")}
    assert columns["password_hash"]["notnull"] == 0
    assert db.get_user_by_name("legacy")["password_hash"] == "1|00|00"
    assert db.query("PRAGMA foreign_key_check") == []


def test_legacy_email_conflict_is_marked_without_winner_or_auto_merge(client):
    first = db.create_user("legacy_one", "hash-one", "reader", "same@example.test")
    db.execute("DROP INDEX IF EXISTS idx_users_email_nocase")
    second = db.create_user("legacy_two", "hash-two", "reader", "other@example.test")
    db.execute("UPDATE users SET email='SAME@example.test' WHERE id=?", (second,))
    db._mark_email_conflicts()
    rows = db.get_users_by_email("same@example.test")
    assert {row["id"] for row in rows} == {first, second}
    assert all(row["email_conflict"] == 1 for row in rows)
    assert db.get_user_by_email("same@example.test") is None
    with pytest.raises(ValueError):
        db.create_user("new_conflict", "hash-three", "reader", "same@example.test")


def test_production_unconfigured_mail_reports_internal_not_configured_without_public_detail():
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    settings.AUTH_MAIL_BACKEND = "console"
    mail.reset_mail_adapter()
    try:
        delivery = mail.get_mail_adapter().send(
            to="person@example.test", purpose="password_reset", link="not-used")
        assert delivery.ok is False
        assert delivery.code == "NOT_CONFIGURED"
    finally:
        settings.APP_ENV = original
        settings.AUTH_MAIL_BACKEND = "fake"
        mail.reset_mail_adapter()


def test_auth_me_reports_google_unavailable_without_configuration(client, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "")
    unavailable = client.get("/api/auth/me").json()
    assert unavailable["authed"] is False
    assert unavailable["authMethods"] == {"google": {"available": False}}


def test_auth_me_exposes_only_safe_google_availability_when_configured(client, fake_oidc):
    available = client.get("/api/auth/me").json()
    assert available["authMethods"] == {"google": {"available": True}}


def test_local_mail_renders_queryable_traditional_chinese_message():
    adapter = mail.get_local_mail_adapter()
    result = adapter.send(to="test@example.test", purpose="password_reset", link="http://localhost/reset?token=x")
    message = adapter.latest(purpose="password_reset", to="test@example.test")
    assert result.ok is True
    assert message["subject"] == "重設你的語閱 StoryLingo 密碼"
    assert "http://localhost/reset?token=x" in message["text"]
    assert "href=\"http://localhost/reset?token=x\"" in message["html"]


def test_profile_email_delivery_failure_restores_pending_state(client, monkeypatch):
    reg(client, "mail_failure_user", email="mail-failure@example.test")
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "unsupported")
    mail.reset_mail_adapter()
    response = client.patch("/api/auth/profile", json={"email": "new-mail-failure@example.test"})
    assert response.status_code == 503
    account = db.get_user_by_name("mail_failure_user")
    assert account["email"] == "mail-failure@example.test"
    assert not account.get("pending_email")


def test_registration_delivery_failure_keeps_account_pending_and_unverified(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "unsupported")
    mail.reset_mail_adapter()
    response = client.post("/api/auth/register", json={
        "username": "mail_pending_user", "password": PW, "email": "pending@example.test",
    })
    assert response.status_code == 503
    account = db.get_user_by_name("mail_pending_user")
    assert account["email"] == "pending@example.test"
    assert account["pending_email"] == "pending@example.test"
    assert account["email_verified_at"] is None


def test_microsoft_graph_adapter_is_safe_and_ready_without_live_delivery(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "microsoft_graph")
    monkeypatch.setattr(settings, "AUTH_BASE_URL", "https://example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_MAILBOX", "mailbox@example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_FROM", "mailbox@example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_FROM_NAME", "語閱 StoryLingo")
    mail.reset_mail_adapter()

    class FakeResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url.startswith("https://login.microsoftonline.com/"):
            return FakeResponse(200, {"access_token": "test-access-token"})
        return FakeResponse(202, {})

    monkeypatch.setattr(mail.httpx, "post", fake_post)
    status = mail.configuration_status()
    assert status["configured"] is True
    assert "client-secret" not in str(status)
    result = mail.get_mail_adapter().send(
        to="recipient@example.test", purpose="email_verification", link="https://example.com/api/auth/verify-email?token=x")
    assert result.ok is True
    assert len(calls) == 2
    graph_url, graph_kwargs = calls[-1]
    assert graph_url.endswith("/users/mailbox@example.com/sendMail")
    assert graph_kwargs["headers"]["Authorization"] == "Bearer test-access-token"
    assert graph_kwargs["json"]["message"]["toRecipients"] == [
        {"emailAddress": {"address": "recipient@example.test"}}
    ]


@pytest.mark.parametrize("base_url, link", [
    ("http://localhost:8000", "http://localhost:8000/api/auth/verify-email?token=x"),
    ("https://storylingo.test", "https://storylingo.test/api/auth/verify-email?token=x"),
    ("https://router.local", "https://router.local/api/auth/verify-email?token=x"),
    ("https://example.com", "https://other.example.com/api/auth/verify-email?token=x"),
    ("https://example.com", "https://example.com/api/auth/verify-email"),
])
def test_microsoft_graph_rejects_unsafe_auth_link_without_network(monkeypatch, base_url, link):
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "microsoft_graph")
    monkeypatch.setattr(settings, "AUTH_BASE_URL", base_url)
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_MAILBOX", "mailbox@example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_FROM", "mailbox@example.com")
    mail.reset_mail_adapter()

    def unexpected_network(*args, **kwargs):
        raise AssertionError("unsafe auth link must not call the network")

    monkeypatch.setattr(mail.httpx, "post", unexpected_network)
    result = mail.get_mail_adapter().send(
        to="recipient@example.test", purpose="email_verification", link=link)
    assert result.ok is False
    assert result.code in {"NOT_CONFIGURED", "INVALID_CONFIGURATION"}


def test_external_auth_flow_rejects_unsafe_base_before_token_creation(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "microsoft_graph")
    monkeypatch.setattr(settings, "AUTH_BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_MAILBOX", "mailbox@example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_FROM", "mailbox@example.com")
    mail.reset_mail_adapter()
    account_id = db.create_user("unsafe_mail_url", "hash", "reader", "unsafe-mail@example.test")

    result = auth_tokens.issue_email_verification(
        account_id, "unsafe-mail@example.test",
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))

    assert result.ok is False and result.code == "NOT_CONFIGURED"
    assert db.query("SELECT id FROM auth_tokens WHERE account_id=?", (account_id,)) == []


@pytest.mark.parametrize("failure, expected_code", [
    ("invalid_credential", "AUTH_FAILED"),
    ("timeout", "TIMEOUT"),
    ("provider_rejected", "PROVIDER_REJECTED"),
    ("rate_limit", "RATE_LIMITED"),
])
def test_microsoft_graph_failure_modes_are_safe(monkeypatch, failure, expected_code):
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "microsoft_graph")
    monkeypatch.setattr(settings, "AUTH_BASE_URL", "https://example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_TENANT_ID", "tenant-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(settings, "AUTH_MAIL_M365_MAILBOX", "mailbox@example.com")
    monkeypatch.setattr(settings, "AUTH_MAIL_FROM", "mailbox@example.com")
    mail.reset_mail_adapter()

    class FakeResponse:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        if url.startswith("https://login.microsoftonline.com/"):
            if failure == "invalid_credential":
                return FakeResponse(401)
            return FakeResponse(200, {"access_token": "test-access-token"})
        if failure == "timeout":
            raise mail.httpx.ReadTimeout("provider timeout")
        return FakeResponse(429 if failure == "rate_limit" else 403)

    monkeypatch.setattr(mail.httpx, "post", fake_post)
    result = mail.get_mail_adapter().send(
        to="recipient@example.test", purpose="password_reset",
        link="https://example.com/api/auth/reset-password?token=x")
    assert result.ok is False
    assert result.code == expected_code


def test_incomplete_microsoft_graph_configuration_fails_closed_without_network(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MAIL_BACKEND", "microsoft_graph")
    for name in ("AUTH_MAIL_M365_TENANT_ID", "AUTH_MAIL_M365_CLIENT_ID",
                 "AUTH_MAIL_M365_CLIENT_SECRET", "AUTH_MAIL_M365_MAILBOX"):
        monkeypatch.setattr(settings, name, "")
    mail.reset_mail_adapter()

    def unexpected_network(*args, **kwargs):
        raise AssertionError("incomplete mail configuration must not call the network")

    monkeypatch.setattr(mail.httpx, "post", unexpected_network)
    assert mail.configuration_status()["configured"] is False
    result = mail.get_mail_adapter().send(
        to="recipient@example.test", purpose="password_reset", link="https://storylingo.test/reset?token=x")
    assert result.ok is False and result.code == "NOT_CONFIGURED"


def test_legacy_pbkdf2_hash_rehashes_only_after_successful_login(client):
    import hashlib
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", b"legacy123", salt, 1000).hex()
    uid = db.create_user("legacy_hash", f"1000|{salt.hex()}|{digest}", "reader")
    before = db.get_user_by_id(uid)["password_hash"]
    assert before.startswith("1000|")
    assert client.post("/api/auth/login", json={
        "username": "legacy_hash", "password": "wrong123",
    }).status_code == 401
    assert db.get_user_by_id(uid)["password_hash"] == before
    assert client.post("/api/auth/login", json={
        "username": "legacy_hash", "password": "legacy123",
    }).status_code == 200
    assert db.get_user_by_id(uid)["password_hash"].startswith("600000|")


def test_google_first_creates_oauth_only_account_without_email_autolink(client, fake_oidc):
    start = client.get("/api/auth/oauth/google/start", params={"return_path": "/#/home"},
                        follow_redirects=False)
    assert start.status_code == 303
    state = _prepare_code(fake_oidc, "new-code", subject="google-sub-1", email="new-google@example.test")
    callback = client.get("/api/auth/oauth/google/callback",
                          params={"state": state, "code": "new-code"}, follow_redirects=False)
    assert callback.status_code == 303
    assert "auth=success" in callback.headers["location"]
    assert client.get("/api/auth/me").json()["authed"] is True
    account = db.get_user_by_email("new-google@example.test")
    assert account and account["password_hash"] is None
    assert db.has_password_credential(account["id"]) is False
    assert db.get_external_identity(issuer=settings.GOOGLE_ISSUER, subject="google-sub-1")
    replayed = client.get("/api/auth/oauth/google/callback",
                          params={"state": state, "code": "new-code"}, follow_redirects=False)
    assert replayed.status_code == 303
    assert "error_oauth_failed" in replayed.headers["location"]

    reg(client, "existing_local", email="linked@example.test")
    start = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "collision-code", subject="google-sub-2", email="linked@example.test")
    blocked = client.get("/api/auth/oauth/google/callback",
                         params={"state": state, "code": "collision-code"}, follow_redirects=False)
    assert blocked.status_code == 303
    assert "existing_account_requires_link" in blocked.headers["location"]
    assert db.get_external_identity(issuer=settings.GOOGLE_ISSUER, subject="google-sub-2") is None
    start = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "existing-sub-code", subject="google-sub-1",
                          email="provider-changed@example.test")
    logged_back = client.get("/api/auth/oauth/google/callback",
                             params={"state": state, "code": "existing-sub-code"},
                             follow_redirects=False)
    assert logged_back.status_code == 303
    assert client.get("/api/auth/me").json()["user"]["username"].startswith("google_")
    assert db.get_user_by_email("new-google@example.test")["email"] == "new-google@example.test"


def test_oauth_external_return_path_falls_back_to_internal_home(client, fake_oidc):
    start = client.get("/api/auth/oauth/google/start",
                       params={"return_path": "https://evil.invalid/takeover"},
                       follow_redirects=False)
    assert start.status_code == 303
    state = _prepare_code(fake_oidc, "safe-redirect-code", subject="safe-redirect-sub",
                          email="safe-redirect@example.test")
    callback = client.get("/api/auth/oauth/google/callback",
                          params={"state": state, "code": "safe-redirect-code"},
                          follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/?auth=success"
    assert "evil.invalid" not in callback.headers["location"]


def test_oauth_link_requires_confirmation_and_unlink_needs_usable_credential(client, fake_oidc):
    reg(client, "link_local", email="link-local@example.test")
    start = client.post("/api/auth/oauth/google/link/start", json={"returnPath": "/#/home"})
    assert start.status_code == 200
    state = _prepare_code(fake_oidc, "link-code", subject="link-sub", email="link-google@example.test")
    callback = client.get("/api/auth/oauth/google/callback",
                          params={"state": state, "code": "link-code"}, follow_redirects=False)
    assert callback.status_code == 303
    assert "auth=link_pending" in callback.headers["location"]
    assert db.get_external_identity(issuer=settings.GOOGLE_ISSUER, subject="link-sub") is None
    confirmed = client.post("/api/auth/oauth/google/link/confirm")
    assert confirmed.status_code == 200
    identity = db.get_external_identity(issuer=settings.GOOGLE_ISSUER, subject="link-sub")
    assert identity and confirmed.json()["identity"]["provider"] == "google"
    assert client.delete(f"/api/auth/oauth/google/{identity['id']}").status_code == 200

    start = client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "oauth-only-code", subject="oauth-only-sub", email="oauth-only@example.test")
    assert client.get("/api/auth/oauth/google/callback",
                      params={"state": state, "code": "oauth-only-code"},
                      follow_redirects=False).status_code == 303
    account = db.get_user_by_email("oauth-only@example.test")
    identity = db.get_external_identity(issuer=settings.GOOGLE_ISSUER, subject="oauth-only-sub")
    assert account and identity
    assert client.delete(f"/api/auth/oauth/google/{identity['id']}").status_code == 409
    assert client.post("/api/auth/password", json={"newPassword": "localpass123"}).status_code == 200
    assert client.delete(f"/api/auth/oauth/google/{identity['id']}").status_code == 200


def test_oauth_state_and_unverified_provider_email_are_rejected(client, fake_oidc):
    invalid = client.get("/api/auth/oauth/google/callback",
                         params={"state": "bad", "code": "bad"}, follow_redirects=False)
    assert invalid.status_code == 303
    client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "unverified-code", subject="unverified-sub",
                          email="unverified@example.test", email_verified=False)
    rejected = client.get("/api/auth/oauth/google/callback",
                          params={"state": state, "code": "unverified-code"}, follow_redirects=False)
    assert rejected.status_code == 303
    assert db.get_user_by_email("unverified@example.test") is None


def test_disabled_linked_google_identity_cannot_reactivate_or_create_account(client, fake_oidc):
    client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "disable-code", subject="disable-sub", email="disable@example.test")
    assert client.get("/api/auth/oauth/google/callback",
                      params={"state": state, "code": "disable-code"},
                      follow_redirects=False).status_code == 303
    account = db.get_user_by_email("disable@example.test")
    db.update_user_account_status(account["id"], "disabled")
    client.get("/api/auth/logout")
    before = db.query_one("SELECT COUNT(*) n FROM users")["n"]
    client.get("/api/auth/oauth/google/start", follow_redirects=False)
    state = _prepare_code(fake_oidc, "disable-login", subject="disable-sub", email="disable@example.test")
    denied = client.get("/api/auth/oauth/google/callback",
                        params={"state": state, "code": "disable-login"},
                        follow_redirects=False)
    assert denied.status_code == 303
    assert db.query_one("SELECT COUNT(*) n FROM users")["n"] == before
    assert client.get("/api/auth/me").json()["authed"] is False


def test_google_oidc_uses_library_jwks_validation_and_rejects_claim_tampering(monkeypatch, fake_oidc):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setattr(oauth.jwt, "PyJWKClient", lambda _uri: SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=public_key)))
    nonce = "nonce-for-test"
    valid_claims = {
        "iss": settings.GOOGLE_ISSUER,
        "sub": "subject",
        "aud": settings.GOOGLE_CLIENT_ID,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "nonce": nonce,
        "email": "oidc@example.test",
        "email_verified": True,
    }
    token = jwt.encode(valid_claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    provider = oauth.GoogleOIDCProvider()
    claims = provider.validate_id_token(token, nonce_digest=oauth._digest(nonce))
    assert claims["subject"] == "subject"
    assert claims["email_verified"] is True
    for changes in (
        {"nonce": "wrong-nonce"},
        {"aud": "wrong-client"},
        {"iss": "https://attacker.invalid"},
        {"exp": int(time.time()) - 1},
    ):
        altered = {**valid_claims, **changes}
        altered_token = jwt.encode(altered, private_key, algorithm="RS256", headers={"kid": "test-key"})
        with pytest.raises(oauth.OAuthValidationError):
            provider.validate_id_token(altered_token, nonce_digest=oauth._digest(nonce))
