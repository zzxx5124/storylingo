"""Notification System v1：typed events、私有 inbox、domain integration。"""
import pytest

from backend import db
from backend.services import notifications
from backend.tests.conftest import new_user, new_author, new_admin, upload_book


def test_typed_notification_is_deduplicated_and_snapshot_is_immutable():
    user = new_user("typed通知")
    uid = db.get_user_by_name("typed通知") ["id"]
    first = notifications.create_notification(
        uid, "content_request.rejected", "review", "申請已退回", "請修改內容",
        dedupe_key="content_request:42:rejected", target_type="content_request", target_id=42,
        target_route="#/requests/42", source_request_id=42)
    second = notifications.create_notification(
        uid, "content_request.rejected", "review", "不同標題不應覆蓋", "不同內容",
        dedupe_key="content_request:42:rejected", target_type="content_request", target_id=42,
        target_route="#/requests/42", source_request_id=42)
    assert first["id"] == second["id"]
    row = db.query_one("SELECT event_type, category, title_snapshot, body_snapshot, dedupe_key FROM notifications WHERE id=?", (first["id"],))
    assert row == {
        "event_type": "content_request.rejected", "category": "review",
        "title_snapshot": "申請已退回", "body_snapshot": "請修改內容",
        "dedupe_key": "content_request:42:rejected",
    }
    assert user.get("/api/notifications").json()["total"] == 1


def test_event_allowlist_and_target_safety_reject_unsafe_creation():
    new_user("通知安全")
    uid = db.get_user_by_name("通知安全") ["id"]
    with pytest.raises(notifications.NotificationValidationError):
        notifications.create_notification(uid, "arbitrary.event", "system", "x", dedupe_key="x")
    with pytest.raises(notifications.NotificationValidationError):
        notifications.create_notification(uid, "system.fake", "system", "x", dedupe_key="x")
    with pytest.raises(notifications.NotificationValidationError):
        notifications.create_notification(uid, "legacy.notice", "system", "x", dedupe_key="unsafe", target_route="javascript:alert(1)")
    assert db.query_one("SELECT COUNT(*) AS n FROM notifications")["n"] == 0


def test_private_list_is_bounded_filtered_and_mark_read_is_current_account_scoped():
    reader = new_user("通知分頁")
    other = new_user("通知他人")
    reader_id = db.get_user_by_name("通知分頁") ["id"]
    other_id = db.get_user_by_name("通知他人") ["id"]
    for index in range(25):
        notifications.create_notification(reader_id, "account.role_changed", "account", f"角色更新 {index}",
                                           dedupe_key=f"role:{index}", source_type="user", source_id=reader_id)
    other_item = notifications.create_notification(other_id, "account.role_changed", "account", "他人的通知",
                                                   dedupe_key="other:1", source_type="user", source_id=other_id)
    response = reader.get("/api/notifications", params={"page": 2, "page_size": 200, "filter": "unread"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    data = response.json()
    assert data["page_size"] == 100
    assert data["total"] == 25
    assert data["has_next"] is False
    assert all(item["recipientAccountId"] == reader_id for item in data["items"])
    assert reader.post(f"/api/notifications/{other_item['id']}/read", json={}).status_code == 200
    assert db.query_one("SELECT read_at FROM notifications WHERE id=?", (other_item["id"],))["read_at"] is None
    own = reader.get("/api/notifications", params={"filter": "unread", "page_size": 1}).json()["items"][0]
    assert reader.post(f"/api/notifications/{own['id']}/read", json={}).status_code == 200
    assert reader.get("/api/notifications/unread-count").json()["count"] == 24
    assert reader.post("/api/notifications/read-all", json={}).json()["ok"] is True
    assert reader.get("/api/notifications/unread-count").json()["count"] == 0


def test_no_generic_client_create_and_legacy_helper_uses_typed_adapter():
    reader = new_user("通知相容")
    uid = db.get_user_by_name("通知相容") ["id"]
    assert reader.post("/api/notifications", json={"eventType": "account.role_changed"}).status_code == 405
    db.add_notification(uid, "system", "舊通知", "保留相容文字", "#/notifications")
    row = db.query_one("SELECT event_type, category, recipient_account_id, dedupe_key FROM notifications WHERE user_id=?", (uid,))
    assert row["event_type"] == "legacy.notice"
    assert row["category"] == "system"
    assert row["recipient_account_id"] == uid
    assert row["dedupe_key"].startswith("legacy:")


def test_author_application_and_content_request_decisions_notify_only_applicant():
    applicant = new_user("通知作者申請")
    admin = new_admin("通知作者管理")
    applied = applicant.post("/api/authors/apply", json={"penName": "通知筆名", "bio": "簡介", "rightsConfirmed": True})
    assert applied.status_code == 200, applied.text
    application_id = applied.json()["id"]
    assert admin.post(f"/api/admin/author-applications/{application_id}/approve").status_code == 200
    applicant_id = db.get_user_by_name("通知作者申請") ["id"]
    rows = db.query("SELECT event_type FROM notifications WHERE recipient_account_id=?", (applicant_id,))
    assert [row["event_type"] for row in rows] == ["author_application.approved"]

    author = new_author("通知內容作者")
    reviewer = new_user("通知審核者", role="reviewer")
    book = upload_book(author, filename="通知審核.txt")
    submitted = author.post(f"/api/books/{book['id']}/requests", json={"requestType": "publish"})
    assert submitted.status_code == 200
    request_id = submitted.json()["id"]
    assert reviewer.post(f"/api/review/requests/{request_id}/start").status_code == 200
    assert reviewer.post(f"/api/review/requests/{request_id}/reject", json={"reason": "請補充摘要"}).status_code == 200
    author_id = db.get_user_by_name(author.username) ["id"]
    decision = db.query("SELECT event_type, body_snapshot, source_request_id FROM notifications WHERE recipient_account_id=? ORDER BY id", (author_id,))
    assert decision[-1]["event_type"] == "content_request.rejected"
    assert decision[-1]["body_snapshot"] == "請補充摘要"
    assert decision[-1]["source_request_id"] == request_id
    reviewer_id = db.get_user_by_name("通知審核者") ["id"]
    reviewer_events = db.query("SELECT event_type FROM notifications WHERE recipient_account_id=?", (reviewer_id,))
    assert all(row["event_type"] == "account.role_changed" for row in reviewer_events)


def test_audiobook_authorization_and_terminal_generation_notification_are_distinct_and_idempotent():
    author = new_author("通知有聲作者")
    reviewer = new_user("通知有聲審核", role="reviewer")
    book = upload_book(author, filename="通知有聲.txt")
    submitted = author.post(f"/api/books/{book['id']}/requests", json={"requestType": "audiobook", "audioMode": "single"})
    assert submitted.status_code == 200
    request_id = submitted.json()["id"]
    assert reviewer.post(f"/api/review/requests/{request_id}/start").status_code == 200
    approved = reviewer.post(f"/api/review/requests/{request_id}/approve")
    assert approved.status_code == 200
    author_id = db.get_user_by_name(author.username) ["id"]
    rows = db.query("SELECT event_type FROM notifications WHERE recipient_account_id=? ORDER BY id", (author_id,))
    assert [row["event_type"] for row in rows if row["event_type"] != "account.role_changed"] == ["audiobook.authorized"]

    request = db.query_one("SELECT * FROM content_requests WHERE id=?", (request_id,))
    reviewer_id = db.get_user_by_name("通知有聲審核") ["id"]
    operation_id = db.create_generation_operation(book_id=request["book_id"], operation_type="audiobook",
                                                   requested_by=reviewer_id, request_id=request_id,
                                                   source_revision=request["submitted_revision"], metadata={"authorizationState": "AUTHORIZED_FOR_GENERATION"})
    job_id = db.create_generation_job("audio_generate_all", book_id=request["book_id"], requested_by=reviewer_id,
                                      operation_id=operation_id, service_type="TTS", source_revision=request["submitted_revision"])
    db.update_generation_job(job_id, "running")
    assert db.update_generation_job(job_id, "success") is True
    assert notifications.notify_generation_terminal(job_id, "success") is True
    assert notifications.notify_generation_terminal(job_id, "success") is True
    final_rows = db.query("SELECT event_type FROM notifications WHERE recipient_account_id=? ORDER BY id", (author_id,))
    assert [row["event_type"] for row in final_rows if row["event_type"] != "account.role_changed"] == ["audiobook.authorized", "generation.ready"]
    assert db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE source_generation_operation_id=?", (operation_id,))["n"] == 1


def test_legacy_notification_rows_are_not_backfilled_on_rerun():
    reader = new_user("通知舊資料")
    uid = db.get_user_by_name("通知舊資料") ["id"]
    con = db._conn()
    con.execute("INSERT INTO notifications(user_id,kind,title,body,link,created_at) VALUES(?,?,?,?,?,?)",
                (uid, "legacy_kind", "歷史通知", "歷史內容", "#/shelf", db.ts()))
    con.commit()
    db.close_all()
    db.init_db()
    row = db.query_one("SELECT recipient_account_id,event_type,dedupe_key FROM notifications WHERE user_id=?", (uid,))
    assert row["recipient_account_id"] is None
    assert row["event_type"] is None
    assert row["dedupe_key"] is None


def test_disabled_account_cannot_read_inbox_and_role_change_does_not_create_new_notice():
    reader = new_user("通知停用帳號")
    uid = db.get_user_by_name("通知停用帳號")["id"]
    notifications.create_notification(
        uid, "account.role_changed", "account", "既有通知", "保留在帳號歷史中。",
        dedupe_key="disabled-account-existing", target_type="", source_type="user", source_id=uid,
    )
    admin = new_admin("通知停用管理員")
    assert admin.post(f"/api/admin/users/{uid}/status", json={"status": "disabled"}).status_code == 200
    before = db.query("SELECT id, event_type FROM notifications WHERE recipient_account_id=?", (uid,))
    db.update_user_role(uid, "author")
    after = db.query("SELECT id, event_type FROM notifications WHERE recipient_account_id=?", (uid,))
    assert after == before
    assert reader.get("/api/notifications").status_code in (401, 403)
    assert db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE recipient_account_id=?", (uid,))["n"] == 1
