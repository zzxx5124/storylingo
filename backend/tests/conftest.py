import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from backend import db, settings
from backend.main import app as built_app
from backend.services import mail

_counter = {"n": 0}
PW = "secret123"


def _unique(name: str) -> str:
    # 同一個測試內多次建同名帳號時自動加上編號，避免 users.username UNIQUE 撞名
    _counter["n"] += 1
    return f"{name}{_counter['n']}"


@pytest.fixture(autouse=True)
def clean_app():
    import uuid
    import tempfile

    base = os.path.join(tempfile.gettempdir(), "xlrd_tests")
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, uuid.uuid4().hex[:12])
    os.makedirs(tmp, exist_ok=True)
    settings.ROOT_DIR = tmp
    settings.DB_PATH = os.path.join(tmp, "test.db")
    settings.BOOKS_DIR = os.path.join(tmp, "storage", "books")
    settings.PROFILE_DIR = os.path.join(tmp, "storage", "profiles")
    settings.SPROCKET_DIR = os.path.join(tmp, "sprocket")
    settings.SESSION_KEY_FILE = os.path.join(tmp, ".session_key")
    settings.FILE_SERVER_SECRET = "test-secret"
    settings.SESSION_TTL_DAYS = 30
    settings.ADMIN_PASSWORD = "adminpass"
    settings.AUTH_MAIL_BACKEND = "fake"
    os.makedirs(settings.BOOKS_DIR, exist_ok=True)
    os.makedirs(settings.PROFILE_DIR, exist_ok=True)
    os.makedirs(settings.SPROCKET_DIR, exist_ok=True)
    db.close_all()
    mail.reset_mail_adapter()
    db.init_db()
    yield
    db.close_all()
    mail.reset_mail_adapter()


@pytest.fixture
def client(clean_app):
    return TestClient(built_app)


def reg(client, username, password=PW, email=None):
    _counter["n"] += 1
    email = email or f"fixture-{_counter['n']}@example.test"
    r = client.post("/api/auth/register", json={"username": username, "password": password, "email": email})
    assert r.status_code == 201, r.text
    message = mail.get_fake_mail_adapter().latest(purpose="email_verification", to=email)
    assert message, "測試 fixture 應收到 fake verification mail"
    parsed = urlsplit(message["link"])
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    verified = client.get(path, follow_redirects=False)
    assert verified.status_code == 303, verified.text
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return r


def new_user(username, password=PW, role=None):
    """註冊並開已登入（含 cookie）的新 client；角色可升成 author/admin。"""
    c = TestClient(built_app)
    reg(c, username, password)
    c.username = username
    if role:
        row = db.get_user_by_name(username)
        db.update_user_role(row["id"], role)
        # Role changes invalidate the old session; fixtures re-authenticate to
        # model the next request made by a real client.
        login = c.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
    return c


def new_author(username=None):
    return new_user(_unique(username or "作者"), role="author")


def new_admin(username=None):
    return new_user(_unique(username or "管理员"), role="admin")


def upload_book(client, text=None, filename="书.txt", **form):
    if text is None:
        body = ("第一章 訪客。\n" + "正文內容描述著日常與敘述，用了長句和多個標點。\n" * 40)
        text = body
    data = {"category": "小说", "vocabLevel": "AUTO"}
    data.update(form)
    r = client.post("/api/books",
                    files={"file": (filename, text.encode("utf-8"), "text/plain")},
                    data=data)
    assert r.status_code == 200, r.text
    return r.json()


def add_chapter(client, bid, title="第一章", text=None):
    if text is None:
        text = "第一章 訪客。\n" + "正文內容描述著日常與敘述，用了長句和多個標點。\n" * 10
    r = client.post(f"/api/books/{bid}/chapters", json={"title": title, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def publish_book(owner, adm=None, **kw):
    """author 上傳 → 送審 → admin 核可 → 公開，回傳 book dict。"""
    book = upload_book(owner, **kw)
    r = owner.post(f"/api/books/{book['id']}/submit")
    assert r.status_code == 200, r.text
    adm = adm or new_admin()
    r = adm.post(f"/api/admin/books/{book['id']}/approve")
    assert r.status_code == 200, r.text
    return book
