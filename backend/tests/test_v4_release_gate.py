"""V4 Phase 14：release gate — fresh install / bootstrap verification。

驗證 fresh DB 可 bootstrap（admin seed、category seed、health smoke）、
single-mode 在無 AI provider 時仍可運作、零 provider 啟動。
"""
import os
import tempfile

import pytest

from backend import db, settings
from backend.services import analysis_executor
from backend.services.analysis_executor import ProviderUnavailableError


def _fresh_env():
    base = os.path.join(tempfile.gettempdir(), "xlrd_v4_release")
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, "fresh_boot")
    os.makedirs(tmp, exist_ok=True)
    settings.ROOT_DIR = tmp
    settings.DB_PATH = os.path.join(tmp, "app.db")
    settings.BOOKS_DIR = os.path.join(tmp, "storage", "books")
    settings.SPROCKET_DIR = os.path.join(tmp, "sprocket")
    settings.SESSION_KEY_FILE = os.path.join(tmp, ".session_key")
    settings.ADMIN_PASSWORD = "adminpass"
    os.makedirs(settings.BOOKS_DIR, exist_ok=True)
    os.makedirs(settings.SPROCKET_DIR, exist_ok=True)
    db.close_all()
    db.init_db()


def test_fresh_db_bootstraps_categories_and_admin():
    _fresh_env()
    # categories seed
    cats = db.list_categories()
    assert cats, "fresh DB 應有 seed categories"
    # admin bootstrap（ADMIN_PASSWORD 有值時建立）
    admin = db.get_user_by_bname(settings.ADMIN_USER or "admin")
    assert admin is not None, "fresh DB 應 bootstrap admin"
    assert admin["role"] == "admin"
    db.close_all()


def test_fresh_db_has_v4_tables_and_indexes():
    _fresh_env()
    con = db._conn()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("books", "chapters", "chapter_analyses", "audio_generations", "ai_providers", "tts_providers"):
        assert t in tables, f"缺少 V4 table: {t}"
    indexes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for idx in ("idx_chapters_chapter_key", "idx_ai_providers_single_default"):
        assert idx in indexes, f"缺少 V4 index: {idx}"
    db.close_all()


def test_health_smoke_on_fresh_db(client):
    _fresh_env()
    from fastapi.testclient import TestClient
    from backend.main import app as built_app
    r = TestClient(built_app).get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    db.close_all()


def test_single_mode_requires_no_ai_provider():
    _fresh_env()
    # 無 AI provider → speaker analysis 明確 unavailable，不 fallback
    with pytest.raises(ProviderUnavailableError):
        analysis_executor.resolve_provider({"ai_provider_id": None})
    db.close_all()


def test_zero_providers_is_valid_state():
    _fresh_env()
    assert db.get_default_ai_provider() is None
    assert db.get_active_tts_provider() is None
    db.close_all()
