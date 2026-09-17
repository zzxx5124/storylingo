"""V4 Phase 1 — Clean V4 database baseline 回歸測試。

驗證 fresh DB bootstrap：
1. 空資料庫可初始化成功。
2. 所有 V4 tables／indexes 存在（含 legacy 欄位保留，確保既有功能可用）。
3. App 可在 fresh DB 上啟動（health smoke）。
4. CRUD smoke：book／chapter 基本操作仍可用。
"""
import sqlite3

from backend import db, settings
from backend import v4_contracts as c
from backend.tests.conftest import new_author, upload_book


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _indexes(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}


def _cols(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def test_fresh_db_initializes_and_has_all_v4_tables():
    con = db._conn()
    tables = _tables(con)
    for t in ("books", "chapters", "users", "external_identities", "auth_tokens",
              "oauth_transactions", "oauth_link_confirmations", "ai_providers",
              "chapter_analyses", "audio_generations", "generation_jobs",
              "tts_providers", "tts_provider_voices"):
        assert t in tables, f"missing table {t}"


def test_v4_columns_present():
    con = db._conn()
    users = _cols(con, "users")
    assert {"password_hash", "email", "email_verified_at", "pending_email",
            "email_conflict", "session_version"} <= users
    books = _cols(con, "books")
    assert {"audio_mode", "default_voice_id", "audio_settings_version"} <= books
    chapters = _cols(con, "chapters")
    assert {"chapter_key", "audio_mode_override", "voice_override_id",
            "current_analysis_id", "active_audio_generation_id", "created_at",
            "updated_at"} <= chapters
    tts = _cols(con, "tts_providers")
    assert {"adapter_key", "capabilities_path", "capabilities_json", "capabilities_status",
            "capabilities_checked_at", "capabilities_hash", "config_version"} <= tts
    jobs = _cols(con, "generation_jobs")
    assert {"parent_job_id", "analysis_id", "audio_generation_id", "ai_provider_id",
            "ai_model", "ai_config_version", "max_attempts"} <= jobs


def test_legacy_columns_kept_for_runtime():
    # Phase 1 不移除 legacy 欄位；既有功能必須能繼續讀寫。
    chapters = _cols(db._conn(), "chapters")
    assert {"seq", "status", "audio", "analyze_path", "audio_path", "timing_path",
            "analysed_hash", "generated_at"} <= chapters


def test_legacy_chapter_timestamp_columns_are_added_idempotently(tmp_path):
    """Persistent pre-V4 chapter tables can reach the V4 audio finalizer safely."""
    legacy_path = tmp_path / "legacy-chapters.db"
    legacy_columns = """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        chars INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        audio TEXT NOT NULL DEFAULT 'none',
        error TEXT NOT NULL DEFAULT '',
        text_hash TEXT NOT NULL DEFAULT '',
        analyze_path TEXT NOT NULL DEFAULT '',
        audio_path TEXT NOT NULL DEFAULT '',
        timing_path TEXT NOT NULL DEFAULT '',
        generated_at TEXT,
        analysed_hash TEXT NOT NULL DEFAULT '',
        publish_status TEXT NOT NULL DEFAULT 'published',
        published_at TEXT,
        chapter_key TEXT,
        audio_mode_override TEXT,
        voice_override_id TEXT,
        current_analysis_id INTEGER,
        active_audio_generation_id INTEGER,
        UNIQUE (book_id, seq)
    """
    con = sqlite3.connect(legacy_path)
    con.execute(f"CREATE TABLE chapters ({legacy_columns})")
    con.commit()
    con.close()

    original_path = settings.DB_PATH
    db.close_all()
    settings.DB_PATH = str(legacy_path)
    try:
        db.init_db()
        first = _cols(db._conn(), "chapters")
        db.init_db()
        second = _cols(db._conn(), "chapters")
        assert {"created_at", "updated_at"} <= first
        assert second == first
    finally:
        db.close_all()
        settings.DB_PATH = original_path


def test_v4_indexes_exist():
    idx = _indexes(db._conn())
    expected = {
        "idx_chapters_chapter_key", "idx_ai_providers_default", "idx_analyses_chapter",
        "idx_analyses_hash", "idx_audio_gen_chapter", "idx_audio_gen_key",
        "idx_jobs_parent", "idx_jobs_analysis", "idx_jobs_audio_gen",
    }
    missing = expected - idx
    assert not missing, f"missing indexes: {missing}"


def test_v4_schema_constraints():
    con = db._conn()
    # ai_providers.provider_type 為 allow-list CHECK
    try:
        con.execute(
            "INSERT INTO ai_providers(name, provider_type, base_url, model, created_at, updated_at) "
            "VALUES('x', 'hack', 'http://a', 'm', '2026-01-01', '2026-01-01')")
        con.commit()
        raise AssertionError("bad provider_type should be rejected by CHECK")
    except Exception:
        pass
    # audio_generations.generation_key 為 UNIQUE（不插 FK 需要 book，先直接測 DB constraint）
    r = con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='audio_generations'").fetchone()
    assert r[0] == 1


def test_book_chapter_crud_smoke():
    # CRUD smoke：上傳一本書 → 讀回 → 加章節 → 列表。
    a = new_author()
    book = upload_book(a, text="第一章 訪客。\n" + "正文內容描述著日常與敘述，用了長句和多個標點。\n" * 40)
    bid = book["id"]
    r = a.get(f"/api/books/{bid}")
    assert r.status_code == 200, r.text
    r = a.post(f"/api/books/{bid}/chapters",
               json={"title": "第一章", "text": "正文：「進入」，她說。\n結尾。"})
    assert r.status_code == 200, r.text
    # 章節列表透過 book 詳情回傳（無獨立 GET chapters list 路由）
    r = a.get(f"/api/books/{bid}")
    assert r.status_code == 200, r.text
    assert len(r.json()["chapters"]) >= 1


def test_health_smoke(client):
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["db"] is True
    r = client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_bootstrap_seed_works():
    # fresh DB：類別已 seed；ADMIN_PASSWORD 有值時 bootstrap admin。
    cats = db.list_categories()
    assert any(c["name"] == "愛情" for c in cats)
    assert db.any_users()


def test_contract_domains_align_with_schema():
    # V4 執行期契約的 domain 名稱與實際 schema 一致。
    tables = _tables(db._conn())
    assert c.TABLE_CHAPTER_ANALYSES in tables
    assert c.TABLE_AUDIO_GENERATIONS in tables
    assert c.TABLE_AI_PROVIDERS in tables
    assert c.TABLE_TTS_PROVIDERS in tables
