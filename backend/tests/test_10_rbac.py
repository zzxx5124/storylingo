"""RBAC：reader/author/admin 權限分界。"""
from backend import db
from backend.tests.conftest import reg, new_admin, new_author, upload_book


def test_reader_cannot_upload(client):
    reg(client, "讀者")
    r = client.post("/api/books", files={"file": ("n.txt", b"content...", "text/plain")})
    assert r.status_code == 403


def test_guest_cannot_upload(client):
    r = client.post("/api/books", files={"file": ("n.txt", b"content...", "text/plain")})
    assert r.status_code in (401, 403)


def test_author_can_upload():
    a = new_author()
    book = upload_book(a)
    assert book["id"]
    assert book["author"]["legacy"] is True
    assert book["owner"].startswith("作者")


def test_reader_cannot_admin(client):
    reg(client, "讀者")
    r = client.get("/api/admin/dashboard")
    assert r.status_code == 403


def test_admin_can_admin():
    adm = new_admin()
    r = adm.get("/api/admin/dashboard")
    assert r.status_code == 200


def test_author_cannot_manage_others_book():
    a1 = new_author("作者甲")
    a2 = new_author("作者乙")
    book = upload_book(a2)
    r = a1.put(f"/api/books/{book['id']}", json={"title": "被改"})
    assert r.status_code == 403
    # 作者乙自己可以
    r = a2.put(f"/api/books/{book['id']}", json={"title": "正確標題"})
    assert r.status_code == 200
    assert r.json()["title"] == "正確標題"


def test_reader_cannot_start_generation(client):
    adm = new_admin()
    book = upload_book(adm)
    reg(client, "讀者")
    seq = book["chapters"][0]["seq"]
    # V4 canonical endpoints 皆有 require_author；reader 於進入 service 前即被拒
    r = client.post(f"/api/books/{book['id']}/chapters/{seq}/analysis")
    assert r.status_code == 403
    r = client.post(f"/api/books/{book['id']}/chapters/{seq}/audio-generations", json={"mode": "single"})
    assert r.status_code == 403


def _ensure_providers():
    if not db.get_default_ai_provider():
        db.create_ai_provider({
            "name": "測試AI", "provider_type": "openai_compatible", "base_url": "https://ai.example.com",
            "model": "m", "enabled": True, "is_default": True, "created_at": db.ts(), "updated_at": db.ts(),
        })
    if not db.get_active_tts_provider():
        pid = db.create_tts_provider({
            "name": "測試TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
            "enabled": True, "is_default": True,
        })
        db.replace_tts_provider_voices(pid, [{"id": "v-zh-1", "name": "測試女聲", "lang": "zh", "gender": "女"}])


def test_author_cannot_start_privileged_canonical_generation_for_owned_book():
    _ensure_providers()
    a = new_author()
    book = upload_book(a)
    seq = book["chapters"][0]["seq"]
    db.update_book(book["id"], {"default_voice_id": "v-zh-1"})
    r = a.post(f"/api/books/{book['id']}/chapters/{seq}/analysis")
    assert r.status_code == 403, r.text
    r = a.post(f"/api/books/{book['id']}/chapters/{seq}/audio-generations", json={"mode": "single"})
    assert r.status_code == 403, r.text

    # The Phase 1 transitional operator path is available to Admin, while
    # Author retains request/content editing responsibilities only.
    operator = new_admin()
    r = operator.post(f"/api/books/{book['id']}/chapters/{seq}/analysis")
    assert r.status_code == 200, r.text


def test_admin_can_start_canonical_generation():
    _ensure_providers()
    adm = new_admin()
    book = upload_book(adm)
    seq = book["chapters"][0]["seq"]
    r = adm.post(f"/api/books/{book['id']}/chapters/{seq}/analysis")
    assert r.status_code == 200, r.text


def test_guest_cannot_read_private_book(client):
    a = new_author()
    book = upload_book(a)
    r = client.get(f"/api/books/{book['id']}")
    assert r.status_code == 404
