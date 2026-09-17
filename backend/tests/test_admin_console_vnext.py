"""Admin Console vNext：bounded read model、privacy 與 canonical truth regression。"""

from backend import db
from backend.tests.conftest import new_admin, new_author, new_user, upload_book
from backend.services import admin_console


def test_admin_list_contract_clamps_page_and_filters_before_page():
    admin = new_admin("consolecontract")
    for index in range(3):
        new_user(f"consolereader{index}")
    response = admin.get("/api/admin/users", params={"role": "reader", "page": 1, "page_size": 1000000, "sort": "username", "order": "asc"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page_size"] == 100
    assert body["has_next"] is False
    assert body["total"] >= 3
    assert all(item["role"] == "reader" for item in body["items"])
    assert [item["username"] for item in body["items"]] == sorted((item["username"] for item in body["items"]), key=str.casefold)


def test_admin_user_projection_is_privacy_safe_and_grouped():
    admin = new_admin("consoleusers")
    author = new_author("consoleauthor")
    book = upload_book(author)
    owner = db.get_user_by_name(author.username)
    db.create_author_profile(owner["id"], public_id="console-profile", slug="console-profile", display_name="Console Author", bio="")
    profile = db.query_one("SELECT id FROM author_profiles WHERE owner_id=? ORDER BY id LIMIT 1", (owner["id"],))
    assert profile
    response = admin.get("/api/admin/users", params={"q": author.username, "author_status": "none"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["ownedBookCount"] == 1
    assert item["authorProfile"]["id"] == profile["id"]
    for forbidden in ("password_hash", "session_token", "oauth_subject", "secret_ciphertext"):
        assert forbidden not in item
    assert book["id"] == db.get_book_row(book["id"])["bid"]


def test_admin_books_projection_separates_owner_attribution_and_workflow():
    admin = new_admin("consolebooks")
    author = new_author("consolebookauthor")
    book = upload_book(author)
    owner = db.get_user_by_name(author.username)
    db.create_author_profile(owner["id"], public_id="console-book-profile", slug="console-book-profile", display_name="Console Book Author", bio="")
    db.update_book(book["id"], {"author_profile_id": db.query_one("SELECT id FROM author_profiles WHERE public_id=?", ("console-book-profile",))["id"]})
    response = admin.get("/api/admin/books", params={"q": "consolebookauthor", "page_size": 1})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == book["id"]
    assert item["ownerId"] == db.get_user_by_name(author.username)["id"]
    assert item["ownerAccount"]["id"] == item["ownerId"]
    assert item["authorProfile"] is not None
    assert set(item["requestState"]) == {"publish", "unpublish", "audiobook"}
    assert "chapters" not in item


def test_admin_categories_include_disabled_and_single_usage_projection():
    admin = new_admin("consolecategories")
    category_id = db.add_category("console-disabled", 999)
    db.set_category_enabled(category_id, False)
    response = admin.get("/api/admin/categories", params={"enabled": "0"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["enabled"] is False
    assert body["categories"] == body["items"]


def test_admin_audit_projection_redacts_sensitive_detail_keys():
    admin = new_admin("consoleaudit")
    actor = db.get_user_by_name(admin.username)
    db.add_audit_log(actor["id"], "provider_update", "provider", 1, {
        "name": "safe-provider", "secret_ciphertext": "encrypted-secret", "apiKey": "sk-test", "status": "ok",
    })
    response = admin.get("/api/admin/audit-logs", params={"action": "provider_update"})
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["category"] == "operations"
    assert item["details"] == {"name": "safe-provider", "status": "ok"}
    assert "encrypted-secret" not in response.text
    assert "sk-test" not in response.text


def test_reviewer_cannot_enter_admin_console_read_models():
    reviewer = new_user("consolereviewer", role="reviewer")
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/books", "/api/admin/services", "/api/admin/audit-logs"):
        assert reviewer.get(path).status_code == 403


def test_generation_summary_separates_provider_capacity_and_worker_health():
    admin = new_admin("consolegeneration")
    provider_id = db.create_ai_provider({
        "name": "console-capacity", "provider_type": "openai", "base_url": "https://example.test",
        "model": "fake", "max_concurrency": 2,
    })
    db.create_generation_job("speaker_analysis", provider={"id": provider_id, "name": "console-capacity", "model": "fake", "config_version": 1})
    response = admin.get("/api/admin/services/AI")
    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["id"] == provider_id)
    assert item["maxConcurrency"] == 2
    assert item["queueCount"] >= 1
    assert item["activeCount"] == 0
    assert item["availableCapacity"] == 2
    assert "secret_ciphertext" not in response.text


def test_admin_generation_job_routes_are_bounded_and_secret_safe():
    admin = new_admin("consolejobsafe")
    provider_id = db.create_ai_provider({
        "name": "console-job-provider", "provider_type": "openai", "base_url": "https://example.test",
        "model": "fake", "secret_ciphertext": "encrypted-secret",
    })
    job_id = db.create_generation_job(
        "speaker_analysis", provider={"id": provider_id, "name": "console-job-provider", "model": "fake"},
        payload={"apiKey": "sk-never-return", "nested": {"Authorization": "Bearer never-return"}},
    )
    job = db.get_generation_job(job_id)
    attempt_id = db.create_generation_attempt(
        job, worker_identity={"instance_id": "admin-safe-test"}, token="claim-never-return",
        service_type="AI", provider_id=provider_id, provider_label="console-job-provider",
        provider_model="fake", provider_config_version="1", started_at=db.ts(),
    )
    db.execute("UPDATE generation_job_attempts SET diagnostics=? WHERE id=?", ('{"Authorization":"Bearer never-return"}', attempt_id))

    list_response = admin.get("/api/admin/jobs", params={"page_size": 100000})
    detail_response = admin.get(f"/api/admin/generation/jobs/{job_id}")
    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert list_response.json()["page_size"] == 100
    for response in (list_response, detail_response):
        assert "payload" not in response.text
        assert "secret_ciphertext" not in response.text
        assert "sk-never-return" not in response.text
        assert "claim-never-return" not in response.text
        assert "Bearer never-return" not in response.text


def test_admin_generation_operation_list_uses_safe_bounded_projection():
    admin = new_admin("consoleoperation")
    actor = db.get_user_by_name(admin.username)
    operation_id = db.create_generation_operation(book_id=None, requested_by=actor["id"], metadata={"Authorization": "never"})
    db.create_generation_job("speaker_analysis", operation_id=operation_id, payload={"raw": "not returned"})
    response = admin.get("/api/admin/generation/operations", params={"status": "queued", "page_size": 1000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page_size"] == 100
    item = next(row for row in body["items"] if row["id"] == operation_id)
    assert item["status"] == "queued"
    assert "raw" not in response.text
    assert "Authorization" not in response.text


def test_admin_banner_validation_rejects_broken_targets_and_time_ranges():
    admin = new_admin("consolebanner")
    invalid_target = admin.post("/api/admin/banners", json={
        "title": "錯誤目標", "imageDesktop": "/banner.jpg", "linkType": "book", "linkValue": "missing-book",
    })
    assert invalid_target.status_code == 400
    invalid_time = admin.post("/api/admin/banners", json={
        "title": "錯誤時間", "imageDesktop": "/banner.jpg", "linkType": "search", "startAt": "2026-09-03T12:00:00Z", "endAt": "2026-09-02T12:00:00Z",
    })
    assert invalid_time.status_code == 400
    assert db.query_one("SELECT COUNT(*) AS n FROM banners")["n"] == 0


def test_admin_provider_capacity_update_uses_config_version_conflict_guard():
    admin = new_admin("consoleproviderversion")
    created = admin.post("/api/admin/ai/providers", json={
        "name": "versioned-ai", "providerType": "openai_compatible", "baseUrl": "https://example.test/v1",
        "model": "fake", "maxConcurrency": 2,
    })
    assert created.status_code == 200, created.text
    provider = created.json()
    updated = admin.put(f"/api/admin/ai/providers/{provider['id']}", json={
        "name": "versioned-ai", "providerType": "openai_compatible", "baseUrl": "https://example.test/v1",
        "model": "fake", "maxConcurrency": 3, "expectedConfigVersion": provider["configVersion"],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["maxConcurrency"] == 3
    conflict = admin.put(f"/api/admin/ai/providers/{provider['id']}", json={
        "name": "versioned-ai", "providerType": "openai_compatible", "baseUrl": "https://example.test/v1",
        "model": "fake", "maxConcurrency": 4, "expectedConfigVersion": provider["configVersion"],
    })
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "provider_config_conflict"


def test_admin_read_models_keep_related_queries_bounded_and_indexes_present(monkeypatch):
    author = new_author("consolequeryauthor")
    for index in range(4):
        upload_book(author, title=f"Query Book {index}")

    original_query = db.query
    queries = []

    def counted_query(sql, params=()):
        queries.append(sql)
        return original_query(sql, params)

    monkeypatch.setattr(db, "query", counted_query)
    accounts = admin_console.list_accounts(q=author.username, page_size=100)
    books = admin_console.list_books(page_size=100)
    assert len(accounts["items"]) == 1
    assert len(books["items"]) == 4
    # Each projection uses a count query plus one grouped/list query; related
    # summaries are scalar subqueries or joins, not per-row DB calls.
    assert len(queries) <= 4

    index_names = {row["name"] for row in original_query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_users_admin_status_role", "idx_books_admin_owner_status", "idx_audit_logs_admin_queue"}.issubset(index_names)
