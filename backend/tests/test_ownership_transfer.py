"""Ownership Transfer lifecycle, authorization, migration and race coverage."""
from concurrent.futures import ThreadPoolExecutor

from backend import db, settings
from .conftest import new_admin, new_author, new_user, publish_book, upload_book


def _row_id(book):
    return db.get_book_row(book["id"])["id"]


def _request(owner, book, target, reason="移交作品"):
    return owner.post(f"/api/books/{book['id']}/ownership-transfers", json={
        "targetAccountId": db.get_user_by_name(target.username)["id"], "reason": reason,
    })


def _accept_review(reviewer, request_id):
    assert reviewer.post(f"/api/review/ownership-transfers/{request_id}/start").status_code == 200
    return reviewer.post(f"/api/review/ownership-transfers/{request_id}/approve")


def test_owner_target_review_completion_preserves_attribution_and_history():
    owner = new_author("transfer_owner")
    target = new_user("transfer_target")
    reviewer = new_user("transfer_reviewer", role="reviewer")
    book = upload_book(owner)
    before = db.get_book_row(book["id"])
    response = _request(owner, book, target)
    assert response.status_code == 200, response.text
    request = response.json()
    assert request["status"] == "REQUESTED" and request["expiresAt"]
    assert owner.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 403
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 200
    approved = _accept_review(reviewer, request["id"])
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["status"] == "COMPLETED"
    after = db.get_book_row(book["id"])
    assert after["owner_id"] == db.get_user_by_name(target.username)["id"]
    assert after["author_profile_id"] == before["author_profile_id"]
    assert after["id"] == before["id"] and after["bid"] == before["bid"]
    events = db.query(
        "SELECT event_type, actor_account_id, from_status, to_status FROM ownership_transfer_events "
        "WHERE request_id=? ORDER BY id", (request["id"],))
    assert [item["event_type"] for item in events] == ["submitted", "target_accepted", "review_started", "approved"]
    assert events[-1]["actor_account_id"] == db.get_user_by_name(reviewer.username)["id"]
    assert db.query_one("SELECT action FROM audit_logs WHERE action='ownership_transfer_completed' ORDER BY id DESC LIMIT 1")


def test_published_transfer_preserves_visibility_and_reader_relationships():
    owner = new_author("transfer_published_owner")
    target = new_author("transfer_published_target")
    reviewer = new_user("transfer_published_reviewer", role="reviewer")
    reader = new_user("transfer_published_reader")
    book = publish_book(owner)
    row = db.get_book_row(book["id"])
    before = db.get_book_row(book["id"])
    reader_id = db.get_user_by_name(reader.username)["id"]
    target_id = db.get_user_by_name(target.username)["id"]
    db.add_library_item(reader_id, row["id"], "favorite")
    db.execute("INSERT INTO follows(user_id, book_id, created_at) VALUES(?,?,?)", (reader_id, row["id"], db.ts()))
    db.save_progress(reader_id, row["id"], 0, 12, 0.25, 3.5, "read")
    db.add_bookmark(reader_id, row["id"], 0, 42, "保留書籤")
    db.execute("INSERT INTO book_events(book_id, chapter_seq, user_id, session_key, event_type, duration, created_at) VALUES(?,?,?,?,?,?,?)",
               (row["id"], 0, reader_id, "session-transfer", "read", 15, db.ts()))
    db.execute("INSERT INTO book_metrics_daily(book_id, metric_date, unique_readers, valid_reads, completions, follows, favorites, audio_plays) VALUES(?,?,?,?,?,?,?,?)",
               (row["id"], "2026-09-04", 1, 2, 0, 1, 1, 0))
    db.execute("INSERT INTO ranking_snapshots(ranking_type, ranking_window, category_id, book_id, rank, score, delta, generated_at) VALUES(?,?,?,?,?,?,?,?)",
               ("hot", "all", row["category_id"], row["id"], 1, 9.5, 0, db.ts()))
    snapshots = {
        "library": db.query("SELECT * FROM library_items WHERE book_id=?", (row["id"],)),
        "follows": db.query("SELECT * FROM follows WHERE book_id=?", (row["id"],)),
        "progress": db.query("SELECT * FROM reading_progress WHERE book_id=?", (row["id"],)),
        "history": db.query("SELECT * FROM reading_history WHERE book_id=?", (row["id"],)),
        "bookmarks": db.query("SELECT * FROM bookmarks WHERE book_id=?", (row["id"],)),
        "events": db.query("SELECT * FROM book_events WHERE book_id=?", (row["id"],)),
        "metrics": db.query("SELECT * FROM book_metrics_daily WHERE book_id=?", (row["id"],)),
        "ranking": db.query("SELECT * FROM ranking_snapshots WHERE book_id=?", (row["id"],)),
    }
    request = _request(owner, book, target).json()
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 200
    assert reviewer.post(f"/api/review/ownership-transfers/{request['id']}/start").status_code == 200
    assert reviewer.post(f"/api/review/ownership-transfers/{request['id']}/approve").status_code == 200
    after = db.get_book_row(book["id"])
    assert after["status"] == "approved" and after["published_at"] == before["published_at"]
    assert after["owner_id"] == target_id and after["author_profile_id"] == before["author_profile_id"]
    tables = {
        "library": "library_items", "follows": "follows", "progress": "reading_progress",
        "history": "reading_history", "bookmarks": "bookmarks", "events": "book_events",
        "metrics": "book_metrics_daily", "ranking": "ranking_snapshots",
    }
    for key, expected in snapshots.items():
        actual = db.query(f"SELECT * FROM {tables[key]} WHERE book_id=?", (row["id"],))
        assert actual == expected, key


def test_completed_transfer_requires_compensating_request_and_preserves_history():
    owner = new_author("transfer_compensate_owner")
    target = new_author("transfer_compensate_target")
    reviewer = new_user("transfer_compensate_reviewer", role="reviewer")
    book = upload_book(owner)
    first = _request(owner, book, target).json()
    assert target.post(f"/api/ownership-transfers/{first['id']}/accept").status_code == 200
    assert _accept_review(reviewer, first["id"]).status_code == 200
    second = _request(target, book, owner)
    assert second.status_code == 200, second.text
    assert second.json()["id"] != first["id"]
    assert db.query_one("SELECT COUNT(*) n FROM ownership_transfer_requests WHERE book_id=? AND status='COMPLETED'", (_row_id(book),))["n"] == 1
    assert db.query_one("SELECT owner_id FROM books WHERE id=?", (_row_id(book),))["owner_id"] == db.get_user_by_name(target.username)["id"]


def test_duplicate_self_target_and_exact_target_actor_boundaries():
    owner = new_author("transfer_duplicate_owner")
    target = new_user("transfer_duplicate_target")
    impostor = new_user("transfer_impostor")
    book = upload_book(owner)
    assert _request(owner, book, owner).status_code == 409
    first = _request(owner, book, target)
    assert first.status_code == 200
    duplicate = _request(owner, book, target)
    assert duplicate.status_code == 409 and "申請" in duplicate.text
    request_id = first.json()["id"]
    assert impostor.post(f"/api/ownership-transfers/{request_id}/accept").status_code == 403
    assert target.post(f"/api/ownership-transfers/{request_id}/reject", json={"reason": "暫不接受"}).status_code == 200
    assert target.post(f"/api/ownership-transfers/{request_id}/accept").status_code == 409


def test_owner_can_cancel_only_before_target_action_and_new_request_after_terminal():
    owner = new_author("transfer_cancel_owner")
    target = new_user("transfer_cancel_target")
    reviewer = new_user("transfer_cancel_reviewer", role="reviewer")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    assert owner.post(f"/api/ownership-transfers/{request['id']}/cancel").status_code == 200
    assert owner.post(f"/api/ownership-transfers/{request['id']}/cancel").status_code == 409
    second = _request(owner, book, target).json()
    assert target.post(f"/api/ownership-transfers/{second['id']}/accept").status_code == 200
    assert owner.post(f"/api/ownership-transfers/{second['id']}/cancel").status_code == 409
    assert _accept_review(reviewer, second["id"]).status_code == 200


def test_expiry_is_immutable_and_requires_new_request():
    owner = new_author("transfer_expiry_owner")
    target = new_user("transfer_expiry_target")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    db.execute("UPDATE ownership_transfer_requests SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00", request["id"]))
    expired = target.get(f"/api/ownership-transfers/{request['id']}")
    assert expired.status_code == 200 and expired.json()["status"] == "EXPIRED"
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 409
    replacement = _request(owner, book, target)
    assert replacement.status_code == 200
    assert db.query_one("SELECT COUNT(*) n FROM ownership_transfer_events WHERE request_id=? AND event_type='expired'", (request["id"],))["n"] == 1


def test_stale_revision_invalidates_before_target_acceptance():
    owner = new_author("transfer_stale_owner")
    target = new_user("transfer_stale_target")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    db.update_chapter(row["id"], 0, {"text": chapter["text"] + "變更", "text_hash": "new-transfer-hash"})
    result = target.post(f"/api/ownership-transfers/{request['id']}/accept")
    assert result.status_code == 409 and result.json().get("code") == "stale_revision"
    assert db.query_one("SELECT status FROM ownership_transfer_requests WHERE id=?", (request["id"],))["status"] == "INVALIDATED"
    assert db.query_one("SELECT event_type FROM ownership_transfer_events WHERE request_id=? ORDER BY id DESC LIMIT 1", (request["id"],))["event_type"] == "invalidated"


def test_active_generation_blocks_completion_and_terminal_generation_does_not():
    owner = new_author("transfer_generation_owner")
    target = new_user("transfer_generation_target")
    reviewer = new_user("transfer_generation_reviewer", role="reviewer")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 200
    assert reviewer.post(f"/api/review/ownership-transfers/{request['id']}/start").status_code == 200
    db.execute("INSERT INTO generation_jobs(book_id, job_type, status, created_at) VALUES(?,?,?,?)",
               (_row_id(book), "audio_generate_all", "waiting_dependency", db.ts()))
    blocked = reviewer.post(f"/api/review/ownership-transfers/{request['id']}/approve")
    assert blocked.status_code == 409 and blocked.json().get("code") == "active_generation"
    db.execute("UPDATE generation_jobs SET status='cancelled' WHERE book_id=?", (_row_id(book),))
    completed = reviewer.post(f"/api/review/ownership-transfers/{request['id']}/approve")
    assert completed.status_code == 200


def test_reviewer_cannot_self_approve_target_or_mutate_private_detail():
    owner = new_author("transfer_boundary_owner")
    target = new_user("transfer_boundary_target", role="reviewer")
    reviewer = new_user("transfer_boundary_reviewer", role="reviewer")
    other = new_author("transfer_boundary_other")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 200
    assert target.post(f"/api/review/ownership-transfers/{request['id']}/start").status_code == 403
    assert reviewer.post(f"/api/review/ownership-transfers/{request['id']}/start").status_code == 200
    assert target.post(f"/api/review/ownership-transfers/{request['id']}/approve").status_code == 403
    assert other.get(f"/api/ownership-transfers/{request['id']}").status_code == 404
    detail = reviewer.get(f"/api/review/ownership-transfers/{request['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert "email" not in str(body) and "password_hash" not in str(body)


def test_role_downgrade_loses_review_capability_and_admin_has_transitional_access():
    owner = new_author("transfer_fresh_owner")
    target = new_user("transfer_fresh_target")
    reviewer = new_user("transfer_fresh_reviewer", role="reviewer")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    db.update_user_role(db.get_user_by_name(reviewer.username)["id"], "reader")
    assert reviewer.get("/api/review/ownership-transfers").status_code == 401
    admin = new_admin("transfer_transitional_admin")
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 200
    assert admin.post(f"/api/review/ownership-transfers/{request['id']}/start").status_code == 200


def test_emergency_transfer_is_super_admin_only_reasoned_recent_auth_and_audited():
    owner = new_author("transfer_emergency_owner")
    ordinary = new_admin("transfer_emergency_admin")
    target = new_user("transfer_emergency_target")
    super_client = new_user("transfer_emergency_super")
    super_row = db.get_user_by_name(super_client.username)
    db.bootstrap_super_admin(super_row["id"])
    book = upload_book(owner)
    assert ordinary.post(f"/api/admin/books/{book['id']}/ownership-transfer/emergency",
                         json={"targetAccountId": db.get_user_by_name(target.username)["id"], "reason": "bad"}).status_code == 403
    super_client.post("/api/auth/login", json={"username": super_client.username, "password": "secret123"})
    missing = super_client.post(f"/api/admin/books/{book['id']}/ownership-transfer/emergency",
                                json={"targetAccountId": db.get_user_by_name(target.username)["id"]})
    assert missing.status_code == 400
    completed = super_client.post(f"/api/admin/books/{book['id']}/ownership-transfer/emergency",
                                  json={"targetAccountId": db.get_user_by_name(target.username)["id"], "reason": "緊急治理"})
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["transferMode"] == "emergency" and result["status"] == "COMPLETED"
    assert db.get_book_row(book["id"])["owner_id"] == db.get_user_by_name(target.username)["id"]
    assert db.query_one("SELECT COUNT(*) n FROM ownership_transfer_events WHERE request_id=? AND event_type='emergency_transfer'", (result["id"],))["n"] == 1


def test_emergency_transfer_keeps_csrf_boundary_in_production(client):
    owner = new_author("transfer_csrf_owner")
    target = new_user("transfer_csrf_target")
    super_client = new_user("transfer_csrf_super")
    super_row = db.get_user_by_name(super_client.username)
    db.bootstrap_super_admin(super_row["id"])
    super_client.post("/api/auth/login", json={"username": super_client.username, "password": "secret123"})
    book = upload_book(owner)
    original = settings.APP_ENV
    settings.APP_ENV = "production"
    try:
        response = super_client.post(f"/api/admin/books/{book['id']}/ownership-transfer/emergency",
                                     json={"targetAccountId": db.get_user_by_name(target.username)["id"], "reason": "安全處置"})
        assert response.status_code == 403 and "CSRF" in response.text
    finally:
        settings.APP_ENV = original


def test_concurrent_duplicate_submission_has_one_active_request():
    owner = new_author("transfer_race_owner")
    target = new_user("transfer_race_target")
    book = upload_book(owner)
    target_id = db.get_user_by_name(target.username)["id"]
    owner_id = db.get_user_by_name(owner.username)["id"]
    book_id = _row_id(book)
    from backend.services import ownership_transfer

    def submit():
        try:
            row = ownership_transfer.submit(book_id=book_id, requester_id=owner_id, target_account_id=target_id)
            return row["id"]
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert len([item for item in results if item]) == 1
    assert db.query_one("SELECT COUNT(*) n FROM ownership_transfer_requests WHERE book_id=? AND status='REQUESTED'", (book_id,))["n"] == 1


def test_concurrent_reviewer_claim_has_one_winner_and_one_review_event():
    owner = new_author("transfer_claim_owner")
    target = new_author("transfer_claim_target")
    reviewer_a = new_user("transfer_claim_reviewer_a", role="reviewer")
    reviewer_b = new_user("transfer_claim_reviewer_b", role="reviewer")
    book = upload_book(owner)
    request = _request(owner, book, target).json()
    assert target.post(f"/api/ownership-transfers/{request['id']}/accept").status_code == 200
    from backend.services import ownership_transfer

    reviewer_ids = [db.get_user_by_name(reviewer_a.username)["id"], db.get_user_by_name(reviewer_b.username)["id"]]

    def claim(actor_id):
        try:
            return ownership_transfer.start_review(request_id=request["id"], actor_id=actor_id)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, reviewer_ids))
    winners = [item for item in claims if item]
    assert len(winners) == 1
    assert db.query_one(
        "SELECT COUNT(*) n FROM ownership_transfer_events WHERE request_id=? AND event_type='review_started'",
        (request["id"],),
    )["n"] == 1


def test_migration_is_idempotent_and_does_not_fabricate_transfer_history():
    owner = new_author("transfer_migration_owner")
    book = upload_book(owner)
    before = db.get_book_row(book["id"])
    db.init_db()
    db.init_db()
    after = db.get_book_row(book["id"])
    assert before["owner_id"] == after["owner_id"]
    assert before["author_profile_id"] == after["author_profile_id"]
    assert before["id"] == after["id"]
    assert db.query_one("SELECT COUNT(*) n FROM ownership_transfer_requests")["n"] == 0
    assert db.query_one("SELECT COUNT(*) n FROM ownership_transfer_events")["n"] == 0
    assert db.query("PRAGMA foreign_key_check") == []
