"""R18 Audit Governance runtime, privacy and recovery regression tests."""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend import db, settings
from backend.main import app
from backend.services import audit
from backend.tests.conftest import new_admin, new_user
from scripts import backup as backup_mod


def _super_admin(name: str = "r18super"):
    return new_user(name, role="super_admin")


def _audit_row(audit_id: int):
    return db.query_one("SELECT * FROM audit_logs WHERE id=?", (audit_id,))


def test_canonical_writer_redacts_nested_secrets_and_bounds_payload():
    admin = new_admin("r18writer")
    actor = db.get_user_by_name(admin.username)
    audit_id = db.add_audit_log(actor["id"], "update_ai_provider", "ai_provider", 7, {
        "name": "safe-provider", "secret_ciphertext": "encrypted-secret",
        "nested": {"apiKey": "sk-test", "status": "ok"},
        "events": [{"Authorization": "Bearer raw-token", "message": "safe"}],
        "fields": ["name", "apiKey", "max_concurrency"],
        "note": "authorization=Bearer raw-token",
    })
    raw = _audit_row(audit_id)
    stored = json.loads(raw["details"])
    encoded = raw["details"]
    assert stored == {
        "name": "safe-provider", "nested": {"status": "ok"},
        "events": [{"message": "safe"}], "fields": ["name", "max_concurrency"],
        "note": "authorization=[已遮蔽]",
    }
    assert "encrypted-secret" not in encoded
    assert "sk-test" not in encoded
    assert "raw-token" not in encoded
    assert raw["event_class"] == "operations"
    assert raw["policy_version"] == audit.AUDIT_POLICY_VERSION

    huge_id = db.add_audit_log(actor["id"], "provider_update", "provider", 8,
                               {f"field_{index}": "x" * 500 for index in range(20)})
    huge = json.loads(_audit_row(huge_id)["details"])
    assert huge == {"_redacted": True, "reason": "payload_exceeds_bound"}


def test_writer_rejects_unknown_action_and_correction_is_append_only():
    admin = new_admin("r18correction")
    actor = db.get_user_by_name(admin.username)
    with pytest.raises(ValueError, match="不支援"):
        db.add_audit_log(actor["id"], "client_supplied_action", "user", actor["id"], {})
    original_id = db.add_audit_log(actor["id"], "set_user_status", "user", actor["id"], {"status": "disabled"})
    original_before = dict(_audit_row(original_id))
    correction_id = audit.record_correction(actor["id"], original_id, "修正安全欄位名稱", {"field": "status"})
    original_after = dict(_audit_row(original_id))
    correction = _audit_row(correction_id)
    assert original_after == original_before
    assert correction["action"] == "audit_event_corrected"
    assert correction["correction_of_id"] == original_id
    assert json.loads(correction["details"])["originalAuditId"] == original_id


def test_database_append_only_guard_and_dev_reset_reinstalls_guard():
    admin = new_admin("r18append")
    actor = db.get_user_by_name(admin.username)
    audit_id = db.add_audit_log(actor["id"], "set_user_status", "user", actor["id"], {})
    with pytest.raises(sqlite3.IntegrityError, match="audit_logs_append_only"):
        db.execute("UPDATE audit_logs SET details='{}' WHERE id=?", (audit_id,))
    with pytest.raises(sqlite3.IntegrityError, match="audit_logs_append_only"):
        db.execute("DELETE FROM audit_logs WHERE id=?", (audit_id,))
    db.clear_tables()
    triggers = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"audit_logs_no_update", "audit_logs_no_delete"}.issubset(triggers)


def test_audit_browse_detail_tombstone_and_export_role_boundaries():
    admin = new_admin("r18browse")
    reader = new_user("r18reader")
    reviewer = new_user("r18reviewer", role="reviewer")
    author = new_user("r18author", role="author")
    tombstone = new_user("r18tombstone")
    actor = db.get_user_by_name(tombstone.username)
    audit_id = db.add_audit_log(actor["id"], "set_user_status", "user", actor["id"], {"safe": "value"})
    db.execute("UPDATE users SET account_status='deleted' WHERE id=?", (actor["id"],))
    listed = admin.get("/api/admin/audit-logs", params={"target_id": actor["id"]})
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json()["items"] if row["id"] == audit_id)
    assert item["actorLabel"] == "已刪除帳號"
    assert item["actorReference"] == f"actor:{actor['id']}"
    assert "@" not in listed.text
    detail = admin.get(f"/api/admin/audit-logs/{audit_id}")
    assert detail.status_code == 200
    assert detail.json()["details"] == {"safe": "value"}
    assert admin.get("/api/admin/audit-logs/999999").status_code == 404

    for client in (reader, reviewer, author):
        assert client.get("/api/admin/audit-logs").status_code == 403
        assert client.get(f"/api/admin/audit-logs/{audit_id}").status_code == 403
        assert client.post("/api/admin/audit-logs/export", json={}).status_code == 403
    assert admin.post("/api/admin/audit-logs/export", json={}).status_code == 403


def test_super_admin_export_is_bounded_redacted_and_self_audited():
    super_client = _super_admin("r18export")
    actor = db.get_user_by_name(super_client.username)
    db.add_audit_log(actor["id"], "update_ai_provider", "ai_provider", 9, {
        "name": "export-safe", "api_key": "never-export", "status": "ok",
    })
    response = super_client.post("/api/admin/audit-logs/export", json={"format": "json", "max_rows": 50})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"]
    assert "never-export" not in response.text
    assert body["policy"]["purgeEnabled"] is False
    export_rows = db.query(
        "SELECT action, actor_id FROM audit_logs WHERE target_type='audit_export' ORDER BY id DESC LIMIT 2"
    )
    assert {row["action"] for row in export_rows} == {"audit_export_requested", "audit_export_completed"}
    assert all(row["actor_id"] == actor["id"] for row in export_rows)

    csv_response = super_client.post("/api/admin/audit-logs/export", json={"format": "csv", "max_rows": 50})
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    assert "audit-export-" in csv_response.headers["content-disposition"]
    assert "never-export" not in csv_response.text

    too_wide = super_client.post("/api/admin/audit-logs/export", json={
        "date_from": "2020-01-01", "date_to": "2026-09-05", "max_rows": 50,
    })
    assert too_wide.status_code == 400
    assert too_wide.json()["code"] == "audit_export_bounds"

    too_many = super_client.post("/api/admin/audit-logs/export", json={"max_rows": 1001})
    assert too_many.status_code == 400


def test_export_mutation_keeps_production_csrf_boundary(monkeypatch):
    super_client = _super_admin("r18csrf")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    response = super_client.post("/api/admin/audit-logs/export", json={"max_rows": 10})
    assert response.status_code == 403


def test_concurrent_exports_are_serialized_without_losing_audit_evidence():
    super_client = _super_admin("r18parallel")
    clients = [TestClient(app), TestClient(app)]
    for client in clients:
        client.cookies.update(super_client.cookies)

    def do_export(client):
        return client.post("/api/admin/audit-logs/export", json={"format": "json", "max_rows": 100})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(do_export, clients))
    assert [response.status_code for response in responses] == [200, 200]
    assert db.query_one(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE action='audit_export_completed' AND actor_id=?",
        (db.get_user_by_name(super_client.username)["id"],),
    )["n"] >= 2


def test_audit_policy_has_no_purge_or_expiry_and_schema_is_idempotent():
    policy = audit.retention_policy()
    assert policy["automaticExpiry"] is False
    assert policy["purgeEnabled"] is False
    before = db.query_one("SELECT COUNT(*) AS n FROM audit_logs")["n"]
    db.init_db()
    after = db.query_one("SELECT COUNT(*) AS n FROM audit_logs")["n"]
    assert after == before
    columns = {row["name"] for row in db.query("PRAGMA table_info(audit_logs)")}
    assert {"event_class", "policy_version", "correction_of_id"}.issubset(columns)
    indexes = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_audit_logs_action_queue", "idx_audit_logs_actor_queue", "idx_audit_logs_target_queue"}.issubset(indexes)
    assert db.query_one("SELECT COUNT(*) AS n FROM generation_job_attempts")["n"] == 0


def test_backup_restore_preserves_audit_evidence_tombstone_and_integrity(tmp_path, monkeypatch):
    admin = new_admin("r18backup")
    actor = db.get_user_by_name(admin.username)
    audit_id = db.add_audit_log(actor["id"], "set_user_status", "user", actor["id"], {"safe": "backup"})
    db.execute("UPDATE users SET account_status='deleted' WHERE id=?", (actor["id"],))
    archive_dir = tmp_path / "backup"
    archive = backup_mod.make_backup(str(archive_dir), keep=1)

    db.close_all()
    restored_root = tmp_path / "restored"
    monkeypatch.setattr(settings, "ROOT_DIR", str(restored_root))
    monkeypatch.setattr(settings, "DATA_DIR", str(restored_root / "data"))
    monkeypatch.setattr(settings, "STORAGE_DIR", str(restored_root / "storage"))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(restored_root / "uploads"))
    monkeypatch.setattr(settings, "BOOKS_DIR", str(restored_root / "storage" / "books"))
    monkeypatch.setattr(settings, "PREVIEW_CACHE_DIR", str(restored_root / "storage" / "preview_cache"))
    backup_mod.restore(archive)
    restored_db = sqlite3.connect(settings.DB_PATH)
    restored_db.row_factory = sqlite3.Row
    try:
        row = restored_db.execute("SELECT * FROM audit_logs WHERE id=?", (audit_id,)).fetchone()
        assert row is not None
        assert row["policy_version"] == audit.AUDIT_POLICY_VERSION
        assert restored_db.execute("SELECT account_status FROM users WHERE id=?", (actor["id"],)).fetchone()[0] == "deleted"
        assert restored_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        trigger_names = {item[0] for item in restored_db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"audit_logs_no_update", "audit_logs_no_delete"}.issubset(trigger_names)
    finally:
        restored_db.close()
