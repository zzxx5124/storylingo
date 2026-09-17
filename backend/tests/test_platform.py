"""公開小說平台：首頁、搜尋、閱讀、書架、留言與輪播。"""
import io

from fastapi.testclient import TestClient
from PIL import Image

from backend import db
from backend.main import app
from backend.services import book_service
from backend.tests.conftest import new_admin, new_author, new_user, publish_book, upload_book


def _banner_png_bytes(width: int = 1600, height: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (180, 60, 40)).save(buf, format="PNG")
    return buf.getvalue()


def test_public_home_search_and_text_reader():
    author = new_author("平台作者")
    admin = new_admin("平台管理")
    book = publish_book(author, admin, filename="公開作品.txt")

    guest = TestClient(app)
    home = guest.get("/api/home")
    assert home.status_code == 200
    assert any(item["id"] == book["id"] for item in home.json()["latest"])

    search = guest.get("/api/search", params={"q": "公開作品"})
    assert search.status_code == 200
    assert search.json()["total"] >= 1

    detail = guest.get(f"/api/books/{book['id']}")
    assert detail.status_code == 200
    chapter = guest.get(f"/api/books/{book['id']}/read/0")
    assert chapter.status_code == 200
    assert "正文內容描述" in chapter.json()["chapter"]["text"]


def test_reader_library_progress_bookmark_and_comment():
    author = new_author("讀者作者")
    admin = new_admin("讀者管理")
    book = publish_book(author, admin)
    reader = new_user("平台讀者")

    favorite = reader.post(f"/api/books/{book['id']}/favorite")
    assert favorite.status_code == 200
    assert reader.get("/api/me/library", params={"kind": "favorite"}).json()["items"]

    progress = reader.put("/api/me/progress", json={
        "bookId": book["id"], "chapterSeq": 0, "position": 120, "percent": 35,
    })
    assert progress.status_code == 200
    assert reader.get("/api/me/progress").json()["items"][0]["percent"] == 35

    bookmark = reader.post("/api/me/bookmarks", json={
        "bookId": book["id"], "chapterSeq": 0, "position": 120, "note": "重要段落",
    })
    assert bookmark.status_code == 200
    assert reader.get("/api/me/bookmarks", params={"book_id": book["id"]}).json()["items"]

    comment = reader.post(f"/api/books/{book['id']}/comments", json={"body": "這是一則測試留言"})
    assert comment.status_code == 200
    comments = reader.get(f"/api/books/{book['id']}/comments")
    assert comments.status_code == 200
    assert comments.json()["items"][0]["body"] == "這是一則測試留言"
    assert "username" not in comments.json()["items"][0]
    assert comments.json()["items"][0]["displayName"] == "讀者"


def test_admin_can_delete_comments_and_public_list_hides_them():
    author = new_author("刪留言作者")
    admin = new_admin("刪留言管理")
    book = publish_book(author, admin)
    reader = new_user("留言讀者甲")
    other = new_user("留言讀者乙")

    created = reader.post(f"/api/books/{book['id']}/comments", json={"body": "要刪除的留言"})
    assert created.status_code == 200
    comment_id = created.json()["id"]
    assert len(other.get(f"/api/books/{book['id']}/comments").json()["items"]) == 1

    # 非管理員不能刪除
    denied = other.delete(f"/api/admin/comments/{comment_id}")
    assert denied.status_code in (401, 403)
    assert len(other.get(f"/api/books/{book['id']}/comments").json()["items"]) == 1

    # 管理員可刪除，公開列表立即隱藏
    assert admin.delete(f"/api/admin/comments/{comment_id}").status_code == 200
    assert other.get(f"/api/books/{book['id']}/comments").json()["items"] == []
    # 重複刪除回 404
    assert admin.delete(f"/api/admin/comments/{comment_id}").status_code == 404


def test_notifications_require_auth_are_scoped_and_can_be_marked_read():
    reader = new_user("通知讀者")
    other = new_user("其他讀者")
    reader_id = db.get_user_by_name("通知讀者")["id"]
    other_id = db.get_user_by_name("其他讀者")["id"]
    db.add_notification(reader_id, "system", "自己的通知", "通知內容", "#/notifications")
    db.add_notification(other_id, "system", "別人的通知")

    guest = TestClient(app)
    assert guest.get("/api/notifications").status_code in (401, 403)

    notifications = reader.get("/api/notifications")
    assert notifications.status_code == 200
    assert notifications.json()["unread"] == 1
    items = notifications.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "自己的通知"

    notification_id = items[0]["id"]
    marked = reader.post("/api/notifications/read", json={"id": notification_id})
    assert marked.status_code == 200
    assert reader.get("/api/notifications", params={"unread_only": True}).json()["items"] == []


def test_home_latest_only_shows_published_even_for_admin():
    """首頁最新更新是公開內容，admin 登入也不應出現未送審作品。"""
    author = new_author("首頁未審作者")
    admin = new_admin("首頁未審管理")
    book = upload_book(author, filename="未審核作品.txt")
    author.post(f"/api/books/{book['id']}/submit")
    assert db.get_book_row(book["id"])["status"] == "draft"
    assert db.query_one("SELECT status FROM content_requests WHERE book_id=? AND request_type='publish' ORDER BY id DESC LIMIT 1",
                        (db.get_book_row(book["id"])["id"],))["status"] == "SUBMITTED"

    guest = TestClient(app).get("/api/home").json()
    assert not any(item["id"] == book["id"] for item in guest["latest"])
    assert not any(item["id"] == book["id"] for item in guest["completed"])

    home = admin.get("/api/home").json()
    assert not any(item["id"] == book["id"] for item in home["latest"])
    assert not any(item["id"] == book["id"] for item in home["completed"])


def test_public_discovery_suggest_rankings_and_recommendations():
    author = new_author("探索作者")
    first = publish_book(author, filename="探索星海.txt")
    second = publish_book(author, filename="探索月港.txt")
    guest = TestClient(app)

    assert guest.get("/api/search/suggest", params={"q": "星"}).json()["items"] == []
    suggestions = guest.get("/api/search/suggest", params={"q": "探索"}).json()["items"]
    assert any(item["bid"] == first["id"] for item in suggestions)

    rankings = guest.get("/api/rankings", params={"kind": "new", "limit": 10})
    assert rankings.status_code == 200
    assert any(item["bid"] in {first["id"], second["id"]} for item in rankings.json()["items"])

    recommendations = guest.get(f"/api/books/{first['id']}/recommendations")
    assert recommendations.status_code == 200
    assert any(item["id"] == second["id"] for item in recommendations.json()["items"])


def test_admin_banner_and_ranking():
    admin = new_admin("輪播管理")
    created = admin.post("/api/admin/banners", json={
        "title": "今日推薦", "subtitle": "開始閱讀", "imageDesktop": "/api/books/demo/cover",
        "linkType": "book", "linkValue": "demo", "altText": "今日推薦",
    })
    assert created.status_code == 200
    banner_id = created.json()["id"]
    assert admin.get("/api/admin/banners").json()["items"]
    assert admin.put(f"/api/admin/banners/{banner_id}", json={"enabled": False}).status_code == 200
    assert admin.delete(f"/api/admin/banners/{banner_id}").status_code == 200

    author = new_author("排行作者")
    book = upload_book(author, filename="排行作品.txt")
    assert db.get_book_row(book["id"])


def test_admin_banner_image_upload_and_public_serving():
    admin = new_admin("輪播圖管理")
    upload = admin.post("/api/admin/banners/upload",
                        files={"file": ("banner.png", _banner_png_bytes(), "image/png")})
    assert upload.status_code == 200, upload.text
    data = upload.json()
    assert data["desktop"].endswith("-desktop.jpg")
    assert data["mobile"].endswith("-mobile.jpg")

    guest = TestClient(app)
    for kind in ("desktop", "mobile"):
        url = data[kind]
        resp = guest.get(url)
        assert resp.status_code == 200, url
        assert resp.headers["content-type"].startswith("image/jpeg")
    # 路徑穿越防護（FastAPI 會先 decode → 路由不匹配 404，或命中後被 basename 檢查擋下 400）
    traversal = guest.get("/api/media/banner/..%2F..%2Fdata%2Fapp.db")
    assert traversal.status_code in (400, 404)
    assert guest.get("/api/media/banner/nonexistent.jpg").status_code == 404
    # 太小 / 非圖片檔會被拒絕
    too_small = admin.post("/api/admin/banners/upload",
                           files={"file": ("small.png", _banner_png_bytes(300, 200), "image/png")})
    assert too_small.status_code == 400
    bad = admin.post("/api/admin/banners/upload",
                     files={"file": ("x.txt", b"hello", "text/plain")})
    assert bad.status_code == 400
    # 訪客無法上傳
    guest_upload = TestClient(app).post("/api/admin/banners/upload",
                                        files={"file": ("b.png", _banner_png_bytes(), "image/png")})
    assert guest_upload.status_code in (401, 403)


def test_list_cards_batch_matches_single_and_home_uses_batch():
    author = new_author("批次作者")
    admin = new_admin("批次管理")
    b1 = publish_book(author, admin, filename="批次甲.txt")
    b2 = publish_book(author, admin, filename="批次乙.txt")
    rows = [db.get_book_row(b1["id"]), db.get_book_row(b2["id"])]
    batch = book_service.book_cards(rows)
    assert len(batch) == 2
    single = [book_service.book_card(r) for r in rows]
    assert batch == single
    guest = TestClient(app)
    cards = guest.get("/api/home").json()["latest"]
    assert any(c["id"] == b1["id"] for c in cards)
    assert any(c["id"] == b2["id"] for c in cards)
    assert all("audioRatio" in c and "litCount" in c for c in cards)


def test_author_application_chapter_visibility_and_report():
    reader = new_user("申請讀者")
    admin = new_admin("申請管理")
    author = new_author("被檢舉作者")
    book = publish_book(author, admin)

    application = reader.post("/api/authors/apply", json={
        "penName": "新作者", "bio": "我想分享合法作品", "rightsConfirmed": True,
    })
    assert application.status_code == 200
    application_id = application.json()["id"]
    assert admin.post(f"/api/admin/author-applications/{application_id}/approve").status_code == 200
    assert db.get_user_by_name("申請讀者")["role"] == "author"
    # The approved application revokes the old reader session; continue as a
    # fresh reader session for the report assertion.
    assert reader.post("/api/auth/login", json={"username": "申請讀者", "password": "secret123"}).status_code == 200

    assert author.put(f"/api/books/{book['id']}/chapters/0/publish", json={"published": False}).status_code == 403
    hide = admin.put(f"/api/books/{book['id']}/chapters/0/publish", json={"published": False})
    assert hide.status_code == 200
    assert TestClient(app).get(f"/api/books/{book['id']}/read/0").status_code == 404
    assert admin.put(f"/api/books/{book['id']}/chapters/0/publish", json={"published": True}).status_code == 200

    report = reader.post("/api/reports", json={"targetType": "book", "targetId": 1, "reason": "測試檢舉"})
    assert report.status_code == 200
    report_id = report.json()["id"]
    assert admin.post(f"/api/admin/reports/{report_id}/resolve", json={"resolution": "已檢視"}).status_code == 200


def test_hidden_chapter_metadata_analysis_and_audio_are_not_publicly_visible():
    author = new_author("隱藏章節作者")
    admin = new_admin("隱藏章節管理")
    book = publish_book(author, admin)
    guest = TestClient(app)

    assert admin.put(f"/api/books/{book['id']}/chapters/0/publish", json={"published": False}).status_code == 200

    # 公開作品詳情不應暴露隱藏章節 metadata。
    detail = guest.get(f"/api/books/{book['id']}")
    assert detail.status_code == 200
    assert detail.json()["chapters"] == []

    # 分析資料與音訊即使知道 URL 也不能取得。
    assert guest.get(f"/api/books/{book['id']}/chapters/0").status_code == 404
    assert guest.get(f"/api/books/{book['id']}/audio/0").status_code == 404

    # 作者與管理員仍可預覽自己的隱藏章節。
    assert author.get(f"/api/books/{book['id']}/chapters/0").status_code == 200
    assert admin.get(f"/api/books/{book['id']}/chapters/0").status_code == 200
