"""Focused security and lifecycle coverage for roles-review-workflow."""
from backend import db
from backend.services import policy
from .conftest import new_admin, new_author, new_user, upload_book


def _submit(client, bid, request_type="publish", **extra):
    payload = {"requestType": request_type, **extra}
    return client.post(f"/api/books/{bid}/requests", json=payload)


def _claim_and_approve(reviewer, request_id):
    claimed = reviewer.post(f"/api/review/requests/{request_id}/start")
    assert claimed.status_code == 200, claimed.text
    return reviewer.post(f"/api/review/requests/{request_id}/approve")


def test_author_request_is_canonical_and_duplicate_is_conflict():
    author = new_author()
    book = upload_book(author)
    first = _submit(author, book["id"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "SUBMITTED"
    assert body["requestType"] == "publish"
    assert body["submittedRevision"]
    assert [event["event_type"] for event in body["events"]] == ["submitted"]
    duplicate = _submit(author, book["id"])
    assert duplicate.status_code == 409
    assert "申請" in duplicate.text
    assert db.query_one("SELECT status FROM books WHERE bid=?", (book["id"],))["status"] == "draft"


def test_review_approval_publishes_and_keeps_request_history():
    author = new_author()
    reviewer = new_user("reviewer_life", role="reviewer")
    book = upload_book(author)
    submitted = _submit(author, book["id"]).json()
    approved = _claim_and_approve(reviewer, submitted["id"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    row = db.get_book_row(book["id"])
    assert row["status"] == "approved" and row["published_at"]
    events = db.query("SELECT event_type,from_status,to_status,actor_account_id FROM content_request_events WHERE request_id=? ORDER BY id", (submitted["id"],))
    assert [event["event_type"] for event in events] == ["submitted", "review_started", "approved"]
    assert events[-1]["actor_account_id"] == db.get_user_by_name("reviewer_life")["id"]


def test_author_can_cancel_only_unclaimed_submitted_request():
    author = new_author()
    reviewer = new_user("reviewer_cancel", role="reviewer")
    book = upload_book(author)
    request = _submit(author, book["id"]).json()
    cancelled = author.post(f"/api/requests/{request['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    replay = author.post(f"/api/requests/{request['id']}/cancel")
    assert replay.status_code == 409
    assert reviewer.post(f"/api/review/requests/{request['id']}/start").status_code == 409


def test_claim_blocks_author_cancel_and_second_reviewer():
    author = new_author()
    reviewer_a = new_user("reviewer_a", role="reviewer")
    reviewer_b = new_user("reviewer_b", role="reviewer")
    book = upload_book(author)
    request = _submit(author, book["id"]).json()
    assert reviewer_a.post(f"/api/review/requests/{request['id']}/start").status_code == 200
    assert author.post(f"/api/requests/{request['id']}/cancel").status_code == 409
    assert reviewer_b.post(f"/api/review/requests/{request['id']}/start").status_code == 409
    assert reviewer_a.post(f"/api/review/requests/{request['id']}/reject", json={"reason": "請補充內容"}).status_code == 200
    assert reviewer_a.post(f"/api/review/requests/{request['id']}/approve").status_code == 409


def test_stale_revision_invalidates_before_approval():
    author = new_author()
    reviewer = new_user("reviewer_stale", role="reviewer")
    book = upload_book(author)
    request = _submit(author, book["id"]).json()
    assert reviewer.post(f"/api/review/requests/{request['id']}/start").status_code == 200
    seq = book["chapters"][0]["seq"]
    chapter = db.get_chapter(db.get_book_row(book["id"])["id"], seq)
    db.update_chapter(chapter["book_id"], seq, {"text": chapter["text"] + " changed", "text_hash": "changed-hash"})
    decision = reviewer.post(f"/api/review/requests/{request['id']}/approve")
    assert decision.status_code == 409
    assert decision.json().get("code") == "stale_revision"
    assert db.query_one("SELECT status FROM content_requests WHERE id=?", (request["id"],))["status"] == "INVALIDATED"
    assert db.query_one("SELECT event_type FROM content_request_events WHERE request_id=? ORDER BY id DESC LIMIT 1", (request["id"],))["event_type"] == "invalidated"


def test_self_approval_and_idor_are_denied():
    author = new_author("dual_role")
    book = upload_book(author)
    request = _submit(author, book["id"]).json()
    row = db.get_user_by_name(author.username)
    db.update_user_role(row["id"], "reviewer")
    # The old session is intentionally stale after a privileged role mutation.
    assert author.get("/api/review/requests").status_code == 401

    other = new_author("other_owner")
    assert other.get(f"/api/requests/{request['id']}").status_code == 404


def test_reviewer_boundaries_and_reader_denial():
    author = new_author()
    reviewer = new_user("reviewer_boundary", role="reviewer")
    reader = new_user("reader_boundary")
    book = upload_book(author)
    request = _submit(author, book["id"]).json()
    assert reader.get("/api/review/requests").status_code == 403
    assert reader.post(f"/api/books/{book['id']}/requests", json={"requestType": "publish"}).status_code == 403
    assert reviewer.put(f"/api/books/{book['id']}/chapters/1", json={"text": "forged"}).status_code == 403
    assert reviewer.get("/api/admin/tts/providers").status_code == 403
    assert reviewer.post(f"/api/admin/books/{book['id']}/emergency-hide", json={"reason": "urgent"}).status_code == 403
    assert reviewer.post(f"/api/review/requests/{request['id']}/approve").status_code == 409
    assert author.post(f"/api/books/{book['id']}/voices/ai-match").status_code == 403
    assert author.put(f"/api/books/{book['id']}/voices", json={"voices": {"旁白": "provider-secret"}}).status_code == 403
    assert author.put(f"/api/books/{book['id']}/settings", json={"ttsCorrect": True}).status_code == 403
    assert author.put(f"/api/requests/{request['id']}", json={"status": "APPROVED"}).status_code == 405


def test_admin_owner_keeps_owner_request_actions_without_self_review():
    admin = new_admin("admin_owner")
    other = new_author("non_owner_requester")
    book = upload_book(admin)

    own_request = _submit(admin, book["id"])
    assert own_request.status_code == 200, own_request.text
    request_id = own_request.json()["id"]
    assert admin.post(f"/api/review/requests/{request_id}/start").status_code == 403
    assert db.query_one("SELECT status FROM content_requests WHERE id=?", (request_id,))["status"] == "SUBMITTED"

    other_row = db.get_user_by_name(other.username)
    book_row = db.get_book_row(book["id"])
    assert other_row["id"] != book_row["owner_id"]
    assert policy.can_request_publish(other_row, book_row) is False
    denied = other.post(f"/api/books/{book['id']}/requests", json={"requestType": "publish"})
    assert denied.status_code == 403


def test_unpublish_requires_review_and_preserves_first_publication():
    author = new_author()
    reviewer = new_user("reviewer_unpublish", role="reviewer")
    book = upload_book(author)
    publish = _submit(author, book["id"]).json()
    assert _claim_and_approve(reviewer, publish["id"]).status_code == 200
    first_published = db.get_book_row(book["id"])["published_at"]
    unpublish = _submit(author, book["id"], "unpublish")
    assert unpublish.status_code == 200
    request = unpublish.json()
    assert _claim_and_approve(reviewer, request["id"]).status_code == 200
    row = db.get_book_row(book["id"])
    assert row["status"] == "removed" and row["published_at"] == first_published


def test_audiobook_approval_is_not_tts_and_followup_is_explicit():
    author = new_author()
    reviewer = new_user("reviewer_audio", role="reviewer")
    book = upload_book(author)
    request = _submit(author, book["id"], "audiobook", audioMode="single").json()
    assert db.query_one("SELECT COUNT(*) n FROM generation_jobs WHERE book_id=?", (db.get_book_row(book["id"])["id"],))["n"] == 0
    assert _claim_and_approve(reviewer, request["id"]).status_code == 200
    approved = reviewer.get(f"/api/review/requests/{request['id']}").json()
    assert approved["generationAuthorized"] is True
    assert approved["operationJobId"] is None
    # No provider is configured in the isolated fixture; no job is created.
    assert reviewer.post(f"/api/review/requests/{request['id']}/generation").status_code == 503


def test_approved_audiobook_links_one_db_generation_operation_without_restarting_it():
    author = new_author("audiobook_operator_author")
    reviewer = new_user("reviewer_audiobook_operator", role="reviewer")
    provider_id = db.create_tts_provider({
        "name": "workflow-test-tts", "provider_type": "generic_http", "base_url": "https://tts.example.com",
        "enabled": True, "is_default": True,
    })
    db.replace_tts_provider_voices(provider_id, [{"id": "voice-1", "name": "測試聲線", "lang": "zh"}])
    book = upload_book(author)
    request = _submit(author, book["id"], "audiobook", audioMode="single").json()
    assert _claim_and_approve(reviewer, request["id"]).status_code == 200
    started = reviewer.post(f"/api/review/requests/{request['id']}/generation")
    assert started.status_code == 200, started.text
    job_id = started.json()["operationJobId"]
    assert job_id
    assert db.query_one("SELECT job_type FROM generation_jobs WHERE id=?", (job_id,))["job_type"] == "audio_generate_all"
    assert db.query_one("SELECT COUNT(*) n FROM generation_jobs WHERE book_id=?", (db.get_book_row(book["id"])["id"],))["n"] == 1
    repeated = reviewer.post(f"/api/review/requests/{request['id']}/generation")
    assert repeated.status_code == 200 and repeated.json()["operationJobId"] == job_id
    assert db.query_one("SELECT COUNT(*) n FROM generation_jobs WHERE id=?", (job_id,))["n"] == 1


def test_emergency_hide_is_reasoned_audited_and_invalidates_active_requests():
    author = new_author("emergency_author")
    reviewer = new_user("reviewer_emergency", role="reviewer")
    admin = new_admin("admin_emergency")
    book = upload_book(author)
    publish = _submit(author, book["id"]).json()
    assert _claim_and_approve(reviewer, publish["id"]).status_code == 200
    unpublish = _submit(author, book["id"], "unpublish").json()

    missing_reason = admin.post(f"/api/admin/books/{book['id']}/emergency-hide", json={})
    assert missing_reason.status_code == 400
    hidden = admin.post(f"/api/admin/books/{book['id']}/emergency-hide", json={"reason": "法律緊急處置"})
    assert hidden.status_code == 200, hidden.text
    assert db.get_book_row(book["id"])["status"] == "removed"
    assert db.query_one("SELECT status, decision_reason FROM content_requests WHERE id=?", (unpublish["id"],))["status"] == "INVALIDATED"
    events = db.query("SELECT event_type,actor_account_id,reason FROM content_request_events WHERE request_id=? ORDER BY id", (unpublish["id"],))
    assert [event["event_type"] for event in events] == ["submitted", "invalidated", "privileged_override"]
    assert events[-1]["reason"] == "法律緊急處置"
    assert db.query_one("SELECT action,actor_id FROM audit_logs WHERE action='emergency_hide' ORDER BY id DESC LIMIT 1")["actor_id"] == db.get_user_by_name(admin.username)["id"]
    assert reviewer.post(f"/api/review/requests/{unpublish['id']}/start").status_code == 409
    assert reviewer.post(f"/api/review/requests/{unpublish['id']}/approve").status_code == 409


def test_schema_migration_is_idempotent_and_preserves_legacy_publication_and_audio():
    author = new_author("legacy_migration_author")
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    db.set_book_status(book["id"], "approved")
    db.execute(
        "INSERT INTO audio_generations(book_id, chapter_id, mode, source_text_hash, generation_key, status, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (row["id"], chapter["id"], "single", "legacy-hash", "legacy-generation-key", "ready", "2026-08-17T00:00:00Z"),
    )
    audio_before = db.query_one("SELECT id, generation_key FROM audio_generations WHERE generation_key=?", ("legacy-generation-key",))

    db.init_db()
    db.init_db()

    preserved_book = db.get_book_row(book["id"])
    audio_after = db.query_one("SELECT id, generation_key FROM audio_generations WHERE generation_key=?", ("legacy-generation-key",))
    assert preserved_book["status"] == "approved" and preserved_book["published_at"]
    assert audio_after["id"] == audio_before["id"] and audio_after["generation_key"] == audio_before["generation_key"]
    assert db.query_one("SELECT COUNT(*) n FROM content_requests WHERE book_id=?", (row["id"],))["n"] == 0
    assert db.query_one("SELECT COUNT(*) n FROM content_request_events")["n"] == 0
    assert db.query("PRAGMA foreign_key_check") == []


def test_super_admin_bootstrap_and_last_account_protection():
    operator = new_admin("ordinary_admin")
    target = new_user("bootstrap_target")
    target_row = db.get_user_by_name(target.username)
    bootstrapped = db.bootstrap_super_admin(target_row["id"], actor_id=None)
    assert bootstrapped["role"] == "super_admin"
    assert db.bootstrap_super_admin(target_row["id"])["idempotent"] is True
    assert db.query_one("SELECT COUNT(*) n FROM audit_logs WHERE action='bootstrap_super_admin' AND target_id=?", (str(target_row["id"]),))["n"] == 1
    # Ordinary Admin cannot mutate the protected role through normal APIs.
    assert operator.post(f"/api/admin/users/{target_row['id']}/role", json={"role": "admin"}).status_code == 403
    assert operator.post(f"/api/admin/users/{target_row['id']}/status", json={"status": "disabled"}).status_code == 403
    assert operator.delete(f"/api/admin/users/{target_row['id']}").status_code == 403
    try:
        db.update_user_role(target_row["id"], "admin")
    except ValueError as error:
        assert str(error) == "LAST_SUPER_ADMIN_PROTECTION"
    else:
        raise AssertionError("last Super Admin must remain protected")


def test_role_freshness_invalidates_old_session():
    admin = new_admin("freshness_admin")
    row = db.get_user_by_name(admin.username)
    db.update_user_role(row["id"], "reader")
    assert admin.get("/api/admin/dashboard").status_code == 401
