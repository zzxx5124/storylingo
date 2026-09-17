"""Audiobook Discovery 的公開可聆聽投影與查詢回歸。"""
import json
import os

import pytest

from backend import db, settings, storage
from backend.services import audio_generation as ag
from backend.services import book_service, generation_pipeline as gp
from backend.tests.conftest import add_chapter, new_admin, new_author, upload_book


def _ensure_tts():
    provider = db.get_active_tts_provider()
    if not provider:
        db.create_tts_provider({
            "name": "Discovery TTS", "provider_type": "generic_http",
            "base_url": "https://tts.example.test", "enabled": True, "is_default": True,
        })
    return db.get_active_tts_provider()


def _published_book(owner, *, title="公開有聲書", chapters=1, category="vocab", category_id=None):
    book = upload_book(owner, filename=f"{title}.txt", category=category)
    for seq in range(1, chapters):
        add_chapter(owner, book["id"], title=f"第{seq + 1}章", text=f"第 {seq + 1} 章正文。" * 8)
    if category_id is not None:
        db.update_book(book["id"], {"category_id": category_id})
    return _publish_existing(owner, book)


def _publish_existing(owner, book):
    response = owner.post(f"/api/books/{book['id']}/submit")
    assert response.status_code == 200, response.text
    admin = new_admin("公開管理員")
    response = admin.post(f"/api/admin/books/{book['id']}/approve")
    assert response.status_code == 200, response.text
    return book


def _make_generation(book, seq=0, *, status="ready", active=True, write_file=True, force_new=False):
    _ensure_tts()
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], seq)
    book_dict = gp._build_book_dict(row)
    generation, _ = ag.create_or_reuse_generation(
        book=book_dict, chapter=chapter, source_text_hash=chapter["text_hash"],
        tts_provider=db.get_active_tts_provider(), force_new=force_new,
    )
    audio_rel = storage.generation_audio_path(book["id"], generation["id"])
    timing_rel = storage.generation_timing_path(book["id"], generation["id"])
    if write_file:
        audio_path = os.path.join(settings.ROOT_DIR, audio_rel)
        timing_path = os.path.join(settings.ROOT_DIR, timing_rel)
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        with open(audio_path, "wb") as handle:
            handle.write(b"ID3 discovery")
        with open(timing_path, "w", encoding="utf-8") as handle:
            json.dump({"segments": [{"dur": 1.0}]}, handle)
    if status == "ready":
        ag.mark_generation_ready_and_activate(
            book=book_dict, chapter=chapter, generation_id=generation["id"],
            audio_path=audio_rel if write_file else "", timing_path=timing_rel if write_file else "",
        )
    else:
        db.update_audio_generation(generation["id"], {"status": status})
        if active:
            db.update_chapter(row["id"], seq, {"active_audio_generation_id": generation["id"]})
    if not active and status == "ready":
        db.update_chapter(row["id"], seq, {"active_audio_generation_id": None})
    return db.get_audio_generation(generation["id"])


def _legacy_audio(book, seq=0):
    row = db.get_book_row(book["id"])
    path = storage.chapter_audio_path(book["id"], seq)
    absolute = os.path.join(settings.ROOT_DIR, path)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    with open(absolute, "wb") as handle:
        handle.write(b"ID3 legacy")
    db.update_chapter(row["id"], seq, {"audio": "ready", "audio_path": path})
    return path


def test_public_audiobooks_include_only_resolvable_native_and_legacy_audio(client):
    owner = new_author("探索作者")
    native = _published_book(owner, title="原生作品")
    legacy = _published_book(owner, title="Legacy 作品")
    missing = _published_book(owner, title="缺檔作品")
    _make_generation(native)
    _legacy_audio(legacy)
    generation = _make_generation(missing)
    os.remove(os.path.join(settings.ROOT_DIR, generation["audio_path"]))

    response = client.get("/api/audiobooks")
    assert response.status_code == 200, response.text
    items = {item["title"]: item for item in response.json()["items"]}
    assert set(items) == {"原生作品", "Legacy 作品"}
    assert items["原生作品"]["availability"] == "available"
    assert items["Legacy 作品"]["availability"] == "available"


def test_public_audiobooks_exclude_request_only_running_and_failed_generation(client):
    owner = new_author("狀態作者")
    request_only = _published_book(owner, title="只有核准")
    running = _published_book(owner, title="執行中")
    failed = _published_book(owner, title="失敗")
    _make_generation(running, status="running")
    _make_generation(failed, status="failed")
    response = client.get("/api/audiobooks").json()
    assert {item["title"] for item in response["items"]} == set()
    assert request_only["id"]


def test_public_visibility_and_partial_count_exclude_hidden_chapters(client):
    owner = new_author("可見性作者")
    book = _published_book(owner, title="部分公開", chapters=3)
    _make_generation(book, seq=2)
    row = db.get_book_row(book["id"])
    db.update_chapter(row["id"], 1, {"publish_status": "hidden"})
    response = client.get("/api/audiobooks").json()
    item = next(item for item in response["items"] if item["id"] == book["id"])
    assert item["availability"] == "partial"
    assert item["playableChapterCount"] == 1
    assert item["publicChapterCount"] == 2
    assert item["listenRoute"].endswith("/2?mode=listen")


def test_failed_regeneration_does_not_hide_old_active_audio(client):
    owner = new_author("重生成作者")
    book = _published_book(owner, title="保留舊音訊")
    old = _make_generation(book)
    new = _make_generation(book, force_new=True, status="failed", active=False)
    response = client.get("/api/audiobooks").json()
    item = next(item for item in response["items"] if item["id"] == book["id"])
    assert item["availability"] == "available"
    assert old["id"] != new["id"]


def test_stale_and_superseded_generations_are_not_public(client):
    owner = new_author("過期作者")
    stale = _published_book(owner, title="過期作品")
    generation = _make_generation(stale)
    row = db.get_book_row(stale["id"])
    chapter = db.get_chapter(row["id"], 0)
    db.update_chapter(row["id"], 0, {"text": chapter["text"] + "改動", "text_hash": "changed-hash"})
    superseded = _published_book(owner, title="被取代作品")
    generation2 = _make_generation(superseded)
    db.update_chapter(db.get_book_row(superseded["id"])["id"], 0, {"active_audio_generation_id": None})
    response = client.get("/api/audiobooks").json()
    assert {item["id"] for item in response["items"]}.isdisjoint({stale["id"], superseded["id"]})
    assert generation["id"] and generation2["id"]


def test_detail_projection_and_audio_route_keep_public_boundary(client):
    owner = new_author("細節作者")
    book = _published_book(owner, title="細節作品")
    _make_generation(book)
    detail = client.get(f"/api/books/{book['id']}").json()
    assert detail["audiobook"]["playableChapterCount"] == 1
    assert detail["audiobook"]["playableChapterSeqs"] == [0]
    forbidden = client.get(f"/api/books/{book['id']}/audio/0")
    assert forbidden.status_code == 200
    row = db.get_book_row(book["id"])
    db.update_chapter(row["id"], 0, {"publish_status": "hidden"})
    hidden_detail = client.get(f"/api/books/{book['id']}").json()
    assert hidden_detail["audiobook"]["playableChapterCount"] == 0
    assert client.get(f"/api/books/{book['id']}/audio/0").status_code in (403, 404)


def test_public_audiobook_dto_is_privacy_safe(client):
    owner = new_author("隱私作者")
    book = _published_book(owner, title="隱私投影")
    _make_generation(book)
    item = client.get("/api/audiobooks").json()["items"][0]
    assert set(item) == {
        "id", "title", "synopsis", "cover", "author", "category", "categoryName",
        "languageType", "serial", "availability", "playableChapterCount",
        "publicChapterCount", "detailRoute", "listenRoute",
    }
    assert "owner_id" not in item and "provider" not in json.dumps(item)
    assert "password_hash" not in json.dumps(item)


def test_first_playable_chapter_and_query_filters_pagination(client):
    owner = new_author("查詢作者")
    category = db.execute("INSERT INTO categories(name, sort, enabled) VALUES(?, 0, 1)", ("查詢分類",)).lastrowid
    first = _published_book(owner, title="甲作品", chapters=3, category="en", category_id=category)
    second = _published_book(owner, title="乙作品", category="vocab", category_id=category)
    _make_generation(first, seq=2)
    _make_generation(second)
    response = client.get("/api/audiobooks", params={"q": "甲", "language": "en", "category_id": category, "sort": "title", "page_size": 1})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["listenRoute"].endswith("/2?mode=listen")
    assert client.get("/api/audiobooks", params={"page_size": 101}).status_code == 400
    assert client.get("/api/audiobooks", params={"page": 9}).json()["items"] == []
    assert client.get("/api/audiobooks", params={"sort": "provider; DROP TABLE books"}).status_code == 400
    assert client.get("/api/audiobooks", params={"provider": "secret", "model": "secret", "job_id": "1"}).status_code == 200


def test_public_audiobook_query_uses_bounded_db_reads(monkeypatch, client):
    owner = new_author("查詢效能作者")
    book = _published_book(owner, title="批次作品")
    _make_generation(book)
    original = db.query
    calls = []

    def counted(sql, params=()):
        calls.append(sql)
        return original(sql, params)

    monkeypatch.setattr(db, "query", counted)
    response = client.get("/api/audiobooks", params={"page_size": 100})
    assert response.status_code == 200
    assert len(calls) <= 2
    assert book_service.explain_audiobook_query()


def test_public_audiobook_detail_without_audio_is_explicit_none(client):
    owner = new_author("無音訊作者")
    book = _published_book(owner, title="無音訊細節")
    detail = client.get(f"/api/books/{book['id']}").json()
    assert detail["audiobook"] == {
        "availability": "none", "playableChapterCount": 0,
        "publicChapterCount": 1, "firstPlayableChapter": None,
        "playableChapterSeqs": [],
    }
