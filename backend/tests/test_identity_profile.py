"""Identity Profile approved contract integration regression。"""
import io
from urllib.parse import urlsplit

from PIL import Image

from backend import db
from backend.services import mail
from backend.services.migration import identity_profile_migration
from backend.tests.conftest import new_admin, new_author, new_user, publish_book, reg, upload_book


def test_profile_self_service_and_case_insensitive_email(client):
    reg(client, "profile_user", password="secret123", email="old@example.com")
    user = client.get("/api/auth/me").json()
    assert user["profile"]["displayName"] == "profile_user"
    result = client.patch("/api/auth/profile", json={"displayName": "閱讀者甲", "email": "Reader@Example.com"})
    assert result.status_code == 200, result.text
    assert result.json()["publicProfile"]["displayName"] == "閱讀者甲"
    assert result.json()["account"]["email"] == "old@example.com"
    assert result.json()["account"]["pendingEmail"] == "reader@example.com"
    message = mail.get_fake_mail_adapter().latest(purpose="email_verification", to="reader@example.com")
    parsed = urlsplit(message["link"])
    assert client.get(parsed.path + "?" + parsed.query, follow_redirects=False).status_code == 303
    assert client.get("/api/auth/profile").status_code == 401
    assert client.post("/api/auth/login", json={"username": "profile_user", "password": "secret123"}).status_code == 200
    assert client.get("/api/auth/profile").json()["account"]["email"] == "reader@example.com"

    duplicate = new_user("profile_user_2")
    assert duplicate.patch("/api/auth/profile", json={"email": "READER@example.com"}).status_code == 409
    normalized = client.patch("/api/auth/profile", json={"displayName": "Cafe\u0301"})
    assert normalized.status_code == 200
    assert normalized.json()["publicProfile"]["displayName"] == "Café"
    markup = client.patch("/api/auth/profile", json={"displayName": "<b>不應儲存</b>"})
    assert markup.status_code == 400
    assert client.get("/api/auth/profile").json()["publicProfile"]["displayName"] == "Café"
    login = new_user("temporary_login")
    login.post("/api/auth/logout")
    assert login.post("/api/auth/login", json={"username": "READER@example.com", "password": "secret123"}).status_code == 200


def test_profile_snapshot_rejects_stale_settings_write(client):
    reg(client, "profile_snapshot", password="secret123", email="snapshot@example.com")
    original = client.get("/api/auth/profile").json()
    revision = original["profileRevision"]
    first = client.patch("/api/auth/profile", json={
        "displayName": "第一個分頁",
        "bio": "先儲存的內容",
        "email": "snapshot@example.com",
        "expectedProfileRevision": revision,
    })
    assert first.status_code == 200, first.text
    stale = client.patch("/api/auth/profile", json={
        "displayName": "第二個分頁",
        "bio": "不應覆寫",
        "email": "snapshot@example.com",
        "expectedProfileRevision": revision,
    })
    assert stale.status_code == 409
    current = client.get("/api/auth/profile").json()
    assert current["publicProfile"]["displayName"] == "第一個分頁"
    assert current["publicProfile"]["bio"] == "先儲存的內容"
    invalid = client.patch("/api/auth/profile", json={"expectedProfileRevision": []})
    assert invalid.status_code == 400


def test_author_application_creates_profile_and_public_author_route(client):
    applicant = new_user("profile_applicant")
    admin = new_admin("profile_reviewer")
    applied = applicant.post("/api/authors/apply", json={
        "penName": "星河筆名", "bio": "作者簡介", "rightsConfirmed": True,
    })
    assert applied.status_code == 200, applied.text
    application_id = applied.json()["id"]
    approved = admin.post(f"/api/admin/author-applications/{application_id}/approve")
    assert approved.status_code == 200, approved.text
    author_profile = approved.json()["authorProfile"]
    assert author_profile["displayName"] == "星河筆名"
    assert author_profile["publicId"]

    # Role mutation revokes the old reader session; a real next request
    # re-authenticates before using the new Author capability.
    assert applicant.post("/api/auth/login", json={"username": "profile_applicant", "password": "secret123"}).status_code == 200
    profile = applicant.get("/api/auth/profile").json()
    assert profile["authorProfiles"][0]["slug"] == author_profile["slug"]
    book = publish_book(applicant, admin)
    public_book = client.get(f"/api/books/{book['id']}").json()
    assert "ownerId" not in public_book
    assert public_book["author"]["displayName"] == "星河筆名"
    assert public_book["author"]["link"].endswith(author_profile["slug"])
    author_page = client.get(f"/api/authors/{author_profile['slug']}")
    assert author_page.status_code == 200, author_page.text
    assert [item["id"] for item in author_page.json()["works"]] == [book["id"]]


def test_author_slug_alias_and_opaque_id_remain_resolvable_after_rename(client):
    author = new_author("stable_public_author")
    profile = db.create_author_profile(
        db.get_user_by_name(author.username)["id"], public_id="ap_stable_public",
        slug="stable-before", display_name="穩定作者", bio="")
    changed = author.patch(f"/api/auth/profile/authors/{profile['id']}", json={"slug": "stable-after"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["publicId"] == "ap_stable_public"
    assert client.get("/api/authors/stable-before").status_code == 200
    assert client.get("/api/authors/id/ap_stable_public").status_code == 200


def test_same_author_display_name_gets_deterministic_unique_slugs():
    first = new_author("same_name_a")
    second = new_author("same_name_b")
    one = db.create_author_profile(db.get_user_by_name(first.username)["id"],
                                   public_id="ap_same_one", slug="same-name", display_name="同名作者", bio="")
    two = db.create_author_profile(db.get_user_by_name(second.username)["id"], public_id="ap_same_two",
                                   slug="same-name-2", display_name="同名作者", bio="")
    assert one["display_name"] == two["display_name"]
    assert one["slug"] != two["slug"]


def test_one_account_can_create_multiple_personas_and_choose_book_attribution(client):
    author = new_author("multi_persona_author")
    first = db.create_author_profile(
        db.get_user_by_name(author.username)["id"], public_id="ap_multi_one",
        slug="multi-one", display_name="第一筆名", bio="第一個 persona")
    created = author.post("/api/auth/profile/authors", json={
        "displayName": "第二筆名", "bio": "第二個 persona",
    })
    assert created.status_code == 200, created.text
    second = created.json()
    assert second["publicId"] != first["public_id"]
    assert second["slug"] != first["slug"]

    one = upload_book(author, filename="persona-one.txt", authorProfileId=str(first["id"]))
    two = upload_book(author, filename="persona-two.txt", authorProfileId=str(second["profileId"]))
    assert db.get_book_row(one["id"])["author_profile_id"] == first["id"]
    assert db.get_book_row(two["id"])["author_profile_id"] == second["profileId"]

    other = new_author("other_persona_owner")
    denied = other.post("/api/books/manual", json={
        "title": "不應該越權的書", "authorProfileId": first["id"],
    })
    assert denied.status_code == 400


def test_disabled_session_is_revoked_and_deleted_account_keeps_public_work(client):
    author = new_author("lifecycle_author")
    admin = new_admin("lifecycle_admin")
    author_profile = db.create_author_profile(db.get_user_by_name(author.username)["id"],
                                              public_id="ap_lifecycle", slug="lifecycle-author",
                                              display_name="生命週期作者", bio="")
    book = publish_book(author, admin)
    target = db.get_user_by_name(author.username)
    assert admin.post(f"/api/admin/users/{target['id']}/status", json={"status": "disabled"}).status_code == 200
    assert author.get("/api/auth/profile").status_code == 401
    suspended = client.get(f"/api/authors/{author_profile['slug']}")
    assert suspended.status_code == 200
    assert suspended.json()["author"]["status"] == "suspended"
    assert admin.delete(f"/api/admin/users/{target['id']}").status_code == 200
    page = client.get(f"/api/books/{book['id']}")
    assert page.status_code == 200
    profile = db.query_one("SELECT * FROM author_profiles WHERE owner_id=?", (target["id"],))
    assert profile["status"] == "tombstone"
    public_author = client.get(f"/api/authors/{profile['slug']}")
    assert public_author.status_code == 200
    assert public_author.json()["author"]["status"] == "tombstone"
    assert admin.post(f"/api/admin/users/{target['id']}/status", json={"status": "active"}).status_code == 409


def test_avatar_pipeline_uses_decoded_image_and_isolated_server_name(client):
    reg(client, "avatar_user")
    image = Image.new("RGB", (64, 64), "orange")
    raw = io.BytesIO()
    image.save(raw, format="PNG")
    response = client.post("/api/auth/profile/avatar", files={"file": ("../../avatar.html", raw.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    assert response.json()["publicProfile"]["avatar"] == "/api/auth/profile/avatar"
    assert ".." not in db.get_public_profile(db.get_user_by_name("avatar_user")["id"])["avatar_path"]
    wrong_mime = client.post("/api/auth/profile/avatar", files={"file": ("avatar.png", raw.getvalue(), "application/octet-stream")})
    assert wrong_mime.status_code == 400
    mismatched_mime = client.post("/api/auth/profile/avatar", files={"file": ("avatar.jpg", raw.getvalue(), "image/jpeg")})
    assert mismatched_mime.status_code == 400


def test_public_author_avatar_and_followed_cards_are_redacted(client):
    author = new_author("avatar_author")
    admin = new_admin("avatar_admin")
    profile = db.create_author_profile(db.get_user_by_name(author.username)["id"],
                                      public_id="ap_avatar_author", slug="avatar-author",
                                      display_name="頭像作者", bio="")
    image = Image.new("RGB", (64, 64), "purple")
    raw = io.BytesIO()
    image.save(raw, format="PNG")
    upload = author.post(f"/api/auth/profile/authors/{profile['id']}/avatar",
                         files={"file": ("avatar.png", raw.getvalue(), "image/png")})
    assert upload.status_code == 200, upload.text
    book = publish_book(author, admin)
    assert client.get(f"/api/media/author-avatar/{profile['public_id']}").status_code == 200
    assert client.post(f"/api/books/{book['id']}/follow").status_code == 401
    reader = new_user("avatar_reader")
    assert reader.post(f"/api/books/{book['id']}/follow").status_code == 200
    followed = reader.get("/api/books", params={"followed": 1}).json()
    assert followed and "ownerId" not in followed[0]
    assert followed[0]["author"]["link"].endswith(profile["slug"])


def test_public_comments_are_allowlisted_and_hidden_chapter_comments_are_unavailable(client):
    author = new_author("comment_visibility_author")
    admin = new_admin("comment_visibility_admin")
    book = publish_book(author, admin)
    reader = new_user("comment_visibility_reader")
    created = reader.post(f"/api/books/{book['id']}/comments", json={"body": "公開留言", "chapterSeq": 0})
    assert created.status_code == 200, created.text
    comments = client.get(f"/api/books/{book['id']}/comments").json()["items"]
    assert comments[0]["body"] == "公開留言"
    assert "user_id" not in comments[0] and "book_id" not in comments[0] and "status" not in comments[0]

    row = db.get_book_row(book["id"])
    db.execute("UPDATE chapters SET publish_status='draft' WHERE book_id=? AND seq=0", (row["id"],))
    assert client.get(f"/api/books/{book['id']}/comments?chapter_seq=0").status_code == 404
    assert reader.post(f"/api/books/{book['id']}/comments", json={"body": "不應公開", "chapterSeq": 0}).status_code == 404


def test_identity_migration_is_dry_run_first_and_idempotent():
    user = db.get_user_by_name("admin")
    db.execute("DELETE FROM public_profiles WHERE account_id=?", (user["id"],))
    dry = identity_profile_migration()
    assert dry["dryRun"] is True
    assert dry["publicProfilesToCreate"] == 1
    assert db.get_public_profile(user["id"]) is None
    applied = identity_profile_migration(apply=True)
    assert applied["applied"] is True
    assert db.get_public_profile(user["id"])
    second = identity_profile_migration(apply=True)
    assert second["publicProfilesToCreate"] == 0


def test_identity_migration_preserves_unmapped_legacy_attribution():
    owner = new_user("legacy_attribution_owner")
    owner_row = db.get_user_by_name(owner.username)
    bid = db.create_book(owner_row["id"], title="legacy", legacy_author_name="舊作者",
                         _auto_author_profile=False, status="approved", published_at=db.ts())
    report = identity_profile_migration(apply=True)
    row = db.get_book_row(bid)
    assert row["author_profile_id"] is None
    assert row["legacy_author_name"] == "舊作者"
    assert any(item["bid"] == bid and item["code"] == "AMBIGUOUS_AUTHOR_ATTRIBUTION"
               for item in report["legacyBookAttributionConflicts"])


def test_identity_migration_preserves_conflicting_legacy_text_without_auto_attribution():
    author = new_author("legacy_conflict_author")
    owner = db.get_user_by_name(author.username)
    db.create_author_profile(owner["id"], public_id="ap_legacy_conflict", slug="new-author",
                             display_name="新作者", bio="")
    legacy_name = "舊作者" + "長" * 100
    bid = db.create_book(owner["id"], title="legacy conflict", legacy_author_name=legacy_name,
                         _auto_author_profile=False, status="approved", published_at=db.ts())
    report = identity_profile_migration(apply=True)
    row = db.get_book_row(bid)
    assert row["author_profile_id"] is None
    assert row["legacy_author_name"] == legacy_name
    assert any(item["bid"] == bid and item["code"] == "AMBIGUOUS_AUTHOR_ATTRIBUTION"
               and item["legacyAttribution"] == legacy_name
               for item in report["legacyBookAttributionConflicts"])


def test_author_summary_is_consistent_across_public_surfaces_and_never_exposes_login_identity(client):
    author = new_author("surface_login")
    admin = new_admin("surface_admin")
    author_row = db.get_user_by_name(author.username)
    profile = db.create_author_profile(
        author_row["id"], public_id="ap_surface", slug="surface-author",
        display_name="公開筆名", bio="作者介紹")
    book = publish_book(author, admin, filename="surface-book.txt")
    reader = new_user("surface_reader")
    reader.post(f"/api/books/{book['id']}/favorite")
    reader.put("/api/me/progress", json={
        "bookId": book["id"], "chapterSeq": 0, "position": 1, "percent": 1,
    })
    guest = client

    surfaces = [
        guest.get("/api/home").json()["latest"],
        guest.get("/api/search", params={"q": "surface-book"}).json()["items"],
        guest.get("/api/rankings", params={"kind": "new"}).json()["items"],
        guest.get(f"/api/books/{book['id']}/recommendations").json()["items"],
        [guest.get(f"/api/books/{book['id']}").json()],
        [guest.get(f"/api/books/{book['id']}/read/0").json()["book"]],
        reader.get("/api/me/library").json()["items"],
        reader.get("/api/me/history").json()["items"],
    ]
    summaries = []
    for items in surfaces:
        for item in items:
            if (item.get("id") or item.get("bid")) == book["id"]:
                assert "ownerId" not in item
                assert author.username not in str(item)
                assert item["author"]["displayName"] == profile["display_name"]
                assert item["author"]["publicId"] == profile["public_id"]
                summaries.append(item["author"])
    assert len(summaries) >= 6
