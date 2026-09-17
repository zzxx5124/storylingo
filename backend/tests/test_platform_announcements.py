"""Platform announcements: aggregate, audience, acknowledgement and safety contract."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend import db, settings
from backend.main import app
from backend.services import announcements
from backend.tests.conftest import new_admin, new_user
from fastapi.testclient import TestClient


def announcement_payload(**overrides):
    payload = {
        "title": "平台維護通知",
        "bodyText": "平台將於今晚進行例行維護。",
        "audienceMode": "everyone",
        "displayMode": "once_per_version",
        "priority": 10,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def create(admin, **overrides):
    response = admin.post("/api/admin/announcements", json=announcement_payload(**overrides))
    assert response.status_code == 200, response.text
    return response.json()


def test_announcement_schema_is_additive_and_empty_without_backfill():
    names = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"announcements", "announcement_audience_roles", "announcement_acknowledgements"} <= names
    assert db.query_one("SELECT COUNT(*) AS n FROM announcements")["n"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM announcement_acknowledgements")["n"] == 0


def test_create_defaults_to_disabled_until_explicit_enable():
    admin = new_admin("announcedefault")
    payload = announcement_payload()
    payload.pop("enabled")
    created = admin.post("/api/admin/announcements", json=payload)
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["enabled"] is False and item["status"] == "disabled"
    assert admin.get("/api/announcements/active").json()["items"] == []
    assert admin.post(f"/api/admin/announcements/{item['id']}/enable").json()["enabled"] is True


def test_announcement_migration_rerun_is_idempotent():
    db.init_db()
    db.init_db()
    assert db.query_one("SELECT COUNT(*) AS n FROM announcements")["n"] == 0
    fk = db.query("PRAGMA foreign_key_check")
    assert fk == []


def test_only_admin_and_super_admin_can_govern_announcements():
    admin = new_admin("announceadmin")
    for role in ("reader", "author", "reviewer"):
        user = new_user(f"announce{role}", role=role)
        assert user.get("/api/admin/announcements").status_code == 403
        assert user.post("/api/admin/announcements", json=announcement_payload()).status_code == 403
    item = create(admin)
    super_admin = new_user("announcesuper", role="admin")
    super_row = db.get_user_by_name(super_admin.username)
    db.update_user_role(super_row["id"], "super_admin")
    assert super_admin.post("/api/auth/login", json={"username": super_admin.username, "password": "secret123"}).status_code == 200
    assert super_admin.get(f"/api/admin/announcements/{item['id']}").status_code == 200


def test_validation_rejects_markup_unsafe_cta_and_bad_schedule():
    admin = new_admin("announcevalidation")
    for payload in (
        announcement_payload(bodyText="<script>alert(1)</script>"),
        announcement_payload(ctaLabel="開啟", ctaTarget="javascript:alert(1)"),
        announcement_payload(startAt="2026-09-03T12:00:00Z", endAt="2026-09-03T12:00:00Z"),
        announcement_payload(audienceMode="specific_roles", roles=[]),
        announcement_payload(audienceMode="everyone", roles=["admin"]),
        announcement_payload(priority=101),
    ):
        assert admin.post("/api/admin/announcements", json=payload).status_code == 400
    assert db.query_one("SELECT COUNT(*) AS n FROM announcements")["n"] == 0


def test_active_endpoint_filters_audience_schedule_and_orders_deterministically():
    admin = new_admin("announceactive")
    create(admin, title="低優先", priority=1)
    create(admin, title="高優先", priority=10)
    create(admin, title="讀者限定", audienceMode="specific_roles", roles=["reader"], priority=9)
    create(admin, title="作者限定", audienceMode="specific_roles", roles=["author"], priority=99)
    create(admin, title="訪客限定", audienceMode="guest", priority=8)
    create(admin, title="尚未開始", startAt="2099-01-01T00:00:00Z", priority=100)
    reader = new_user("announceactivereader")
    reader_titles = [item["title"] for item in reader.get("/api/announcements/active").json()["items"]]
    assert reader_titles[:3] == ["高優先", "讀者限定", "低優先"]
    assert "作者限定" not in reader_titles and "訪客限定" not in reader_titles
    guest_titles = [item["title"] for item in TestClient(app).get("/api/announcements/active").json()["items"]]
    assert "訪客限定" in guest_titles


def test_specific_role_audience_matches_each_fixed_current_account_role():
    admin = new_admin("announceallroles")
    for role in db.SUPPORTED_ROLES:
        create(admin, title=f"角色 {role}", audienceMode="specific_roles", roles=[role], priority=0)
        user = new_user(f"announceaudience{role}", role=role)
        titles = [item["title"] for item in user.get("/api/announcements/active").json()["items"]]
        assert f"角色 {role}" in titles


def test_schedule_boundaries_are_utc_start_inclusive_and_end_exclusive():
    admin = new_admin("announceboundary")
    create(admin, title="邊界公告", startAt="2026-09-03T12:00:00Z", endAt="2026-09-03T13:00:00Z")
    row = db.query_one("SELECT * FROM announcements WHERE title=?", ("邊界公告",))
    at_start = announcements.list_active(None, now=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc))
    at_end = announcements.list_active(None, now=datetime(2026, 9, 3, 13, 0, tzinfo=timezone.utc))
    assert any(item["id"] == row["id"] for item in at_start)
    assert all(item["id"] != row["id"] for item in at_end)


def test_active_audience_uses_current_role_and_account_status_not_stale_cookie():
    admin = new_admin("announcestaleadmin")
    author = new_user("announcestaleauthor", role="author")
    create(admin, audienceMode="specific_roles", roles=["author"], title="作者當前可見")
    assert "作者當前可見" in [item["title"] for item in author.get("/api/announcements/active").json()["items"]]
    row = db.get_user_by_name(author.username)
    db.update_user_role(row["id"], "reader")
    assert "作者當前可見" not in [item["title"] for item in author.get("/api/announcements/active").json()["items"]]
    db.update_user_account_status(row["id"], "disabled")
    assert "作者當前可見" not in [item["title"] for item in author.get("/api/announcements/active").json()["items"]]


def test_authenticated_ack_is_account_and_version_scoped_and_idempotent():
    admin = new_admin("announceack")
    user = new_user("announceackreader")
    item = create(admin)
    response = user.get("/api/announcements/active")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["items"][0]["displayVersion"] == 1
    ack = user.post(f"/api/announcements/{item['id']}/acknowledge", json={"displayVersion": 1})
    assert ack.status_code == 200
    assert user.post(f"/api/announcements/{item['id']}/acknowledge", json={"displayVersion": 1}).status_code == 200
    assert user.get("/api/announcements/active").json()["items"] == []
    updated = admin.post(f"/api/admin/announcements/{item['id']}/reannounce")
    assert updated.status_code == 200 and updated.json()["displayVersion"] == 2
    assert user.get("/api/announcements/active").json()["items"][0]["displayVersion"] == 2
    assert db.query_one("SELECT COUNT(*) AS n FROM announcement_acknowledgements WHERE announcement_id=?", (item["id"],))["n"] == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM audit_logs WHERE action LIKE 'announcement_%' AND action LIKE '%acknowledge%'")["n"] == 0


def test_ack_endpoint_does_not_accept_forged_account_or_once_session_marker():
    admin = new_admin("announceidor")
    user = new_user("announceidorreader")
    item = create(admin, displayMode="once_per_session")
    response = user.post(f"/api/announcements/{item['id']}/acknowledge", json={"displayVersion": 1, "accountId": 99999})
    assert response.status_code == 400
    assert db.query_one("SELECT COUNT(*) AS n FROM announcement_acknowledgements")["n"] == 0


def test_edit_uses_config_version_and_reannounce_preserves_old_ack():
    admin = new_admin("announceversion")
    item = create(admin)
    stale = admin.put(f"/api/admin/announcements/{item['id']}", json={"expectedConfigVersion": 999, **announcement_payload(title="不應成功")})
    assert stale.status_code == 409
    updated = admin.put(f"/api/admin/announcements/{item['id']}", json={"expectedConfigVersion": 1, **announcement_payload(title="已更新", enabled=False)})
    assert updated.status_code == 200
    assert updated.json()["configVersion"] == 2
    assert updated.json()["displayVersion"] == 1
    assert admin.post(f"/api/admin/announcements/{item['id']}/archive").status_code == 200
    assert db.query_one("SELECT id FROM announcements WHERE id=?", (item["id"],))
    assert admin.get("/api/admin/announcements").json()["items"][0]["status"] == "archived"


def test_admin_list_is_bounded_filtered_and_safe():
    admin = new_admin("announcelist")
    for index in range(4):
        create(admin, title=f"公告 {index}", audienceMode="guest" if index % 2 else "everyone")
    body = admin.get("/api/admin/announcements", params={"q": "公告", "audience": "guest", "page_size": 10000, "sort": "not-a-column"}).json()
    assert body["page_size"] == 100
    assert body["total"] == 2
    assert all(item["audienceMode"] == "guest" for item in body["items"])
    active = admin.get("/api/admin/announcements", params={"status": "active"}).json()
    assert active["total"] == 4
    inactive = admin.get("/api/admin/announcements", params={"active": "false"}).json()
    assert inactive["total"] == 0
    for forbidden in ("password_hash", "session_token", "secret", "token"):
        assert forbidden not in str(body).lower()


def test_acknowledgement_concurrent_double_submit_creates_one_row():
    admin = new_admin("announceconcurrent")
    user = new_user("announceconcurrentreader")
    item = create(admin)
    def submit():
        return user.post(f"/api/announcements/{item['id']}/acknowledge", json={"displayVersion": 1}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: submit(), range(2)))
    assert statuses == [200, 200]
    assert db.query_one("SELECT COUNT(*) AS n FROM announcement_acknowledgements WHERE announcement_id=?", (item["id"],))["n"] == 1


def test_ack_requires_authenticated_current_account():
    admin = new_admin("announceguestack")
    item = create(admin)
    guest = TestClient(app)
    assert guest.post(f"/api/announcements/{item['id']}/acknowledge", json={"displayVersion": 1}).status_code == 401


def test_mutating_ack_requires_csrf_in_production_mode():
    admin = new_admin("announcecsrf")
    item = create(admin)
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        response = admin.post(f"/api/announcements/{item['id']}/acknowledge", json={"displayVersion": 1})
        assert response.status_code == 403
    finally:
        settings.APP_ENV = original
