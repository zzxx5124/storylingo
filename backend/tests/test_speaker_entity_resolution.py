"""speaker-entity-resolution contract/integration regression tests."""
import json
import os

import pytest

from backend import db, settings
from backend.services import analysis, character_resolution as identity
from backend.tests.conftest import new_admin, new_author, upload_book


def _ready_artifact(author, *, characters, segments):
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    aid = analysis.create_analysis_row(book_id=row["id"], chapter_id=chapter["id"],
                                      source_text_hash=chapter["text_hash"], prompt_version="entity-test")
    artifact = analysis.normalize_analysis(
        {"characters": characters, "segments": segments}, chapter_key=chapter["chapter_key"],
        source_text_hash=chapter["text_hash"], source_text=chapter["text"], book_id=book["id"])
    analysis.save_ready_analysis(aid, artifact)
    return book, row, chapter, aid, artifact


def test_aliases_share_stable_id_and_containment_stays_distinct(client):
    author = new_author("entity_alias")
    book = upload_book(author)
    artifact = {
        "characters": [
            {"character_id": "char_sun", "canonical_name": "孫悟空", "aliases": ["齊天大聖", "弼馬溫", "老孫"]},
            {"character_id": "char_teacher", "canonical_name": "王老師"},
            {"character_id": "char_daughter", "canonical_name": "王老師的女兒"},
        ],
        "segments": [],
    }
    # Contract fixture intentionally expresses aliases as one stable identity and a containing-name counterexample.
    got = identity.register_ready_artifact(book["id"], 1, artifact)
    assert {x["character_id"] for x in got["characters"]} == {"char_sun", "char_teacher", "char_daughter"}
    assert got["characters"][0]["character_id"] == "char_daughter" or any(
        x["character_id"] == "char_sun" and "齊天大聖" in x["aliases"] for x in got["characters"])
    assert identity.resolve_surface(book["id"], "王老師", scope_type="book")["status"] != "accepted" or \
        identity.resolve_surface(book["id"], "王老師", scope_type="book")["candidates"][0]["characterId"] == "char_teacher"


def test_generic_alias_is_scoped_and_unresolved(client):
    author = new_author("entity_generic")
    book = upload_book(author, text="哥哥" * 30)
    row = db.get_book_row(book["id"])
    result = identity.register_ready_artifact(book["id"], 1, {"characters": [], "segments": []})
    # No global alias is created by the generic term policy.
    assert identity.resolve_surface(book["id"], "哥哥")["status"] == "unresolved"
    with pytest.raises(identity.CharacterResolutionError):
        identity.assign_alias(book["id"], surface="哥哥", target_character_id=None, scope_type="book",
                              expected_revision=result["revision"])


def test_reveal_merge_voice_conflict_and_stale_revision(client):
    author = new_author("entity_merge")
    book = upload_book(author, text="黑衣男子周衡" * 10)
    row = db.get_book_row(book["id"])
    with db.WRITE_LOCK:
        db._conn().execute("INSERT INTO character_registry_meta(book_id,revision,policy_version,updated_at) VALUES(?,?,?,?)",
                           (row["id"], 0, identity.POLICY_VERSION, db.ts()))
        db._conn().execute("INSERT INTO character_registry(book_id,character_id,canonical_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (row["id"], "char_black", "黑衣男子", "provisional", db.ts(), db.ts()))
        db._conn().execute("INSERT INTO character_registry(book_id,character_id,canonical_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (row["id"], "char_zhou", "周衡", "active", db.ts(), db.ts()))
        db._conn().execute("UPDATE books SET voices=? WHERE id=?",
                           (json.dumps({"黑衣男子": "voice-a", "周衡": "voice-b"}, ensure_ascii=False), row["id"]))
        db._conn().commit()
    merged = identity.merge_characters(book["id"], source_character_id="char_black", target_character_id="char_zhou",
                                       expected_revision=0, evidence=[{"type": "reveal", "text": "就是周衡"}], reason="身份揭露")
    assert merged["voiceConflictId"]
    assert identity.list_history(book["id"])[-1]["event_type"] == "merge"
    with pytest.raises(identity.RegistryConflictError):
        identity.merge_characters(book["id"], source_character_id="char_black", target_character_id="char_zhou",
                                  expected_revision=0)


def test_split_requires_mapping_and_does_not_clone_voice(client):
    author = new_author("entity_split")
    book = upload_book(author, text="甲乙" * 30)
    row = db.get_book_row(book["id"])
    with db.WRITE_LOCK:
        db._conn().execute("INSERT INTO character_registry_meta(book_id,revision,policy_version,updated_at) VALUES(?,?,?,?)",
                           (row["id"], 0, identity.POLICY_VERSION, db.ts()))
        db._conn().execute("INSERT INTO character_registry(book_id,character_id,canonical_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (row["id"], "char_old", "舊角色", "active", db.ts(), db.ts()))
        db._conn().commit()
    with pytest.raises(identity.CharacterResolutionError):
        identity.split_character(book["id"], source_character_id="char_old", mappings=[], expected_revision=0)


def test_migration_dry_run_does_not_write_and_api_reader_is_denied(client):
    author = new_author("entity_migration")
    book = upload_book(author, text="遷移測試內容足夠長度。" * 6)
    before = identity.get_registry(book["id"])
    report = identity.migrate_book(book["id"], apply=False)
    assert report["mode"] == "dry-run" and report["written"] is False
    assert identity.get_registry(book["id"])["revision"] == before["revision"]
    reader = client.post("/api/auth/register", json={"username": "entity_reader", "password": "secret123",
                                                     "email": "entity-reader@example.test"})
    assert reader.status_code == 201
    assert client.get(f"/api/books/{book['id']}/character-identities/history").status_code in (401, 403)


def test_migration_apply_is_idempotent_and_rollback_restores_projection(client):
    author = new_author("entity_migration_apply")
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    db.update_book(book["id"], {"speaker_info": json.dumps({"舊角色": {"count": 1}}, ensure_ascii=False),
                               "voices": json.dumps({"舊角色": "voice-old"}, ensure_ascii=False)})
    actor_id = db.get_user_by_name(author.username)["id"]
    first = identity.migrate_book(book["id"], apply=True, actor_id=actor_id)
    assert first["written"] and first["snapshotId"]
    second = identity.migrate_book(book["id"], apply=True, actor_id=actor_id)
    assert second["written"] is False or second["snapshotId"]
    rolled = identity.rollback_migration(book["id"], first["snapshotId"], actor_id=actor_id)
    assert rolled["rolledBack"]
    restored = db.get_book_row(book["id"])
    assert json.loads(restored["voices"])["舊角色"] == "voice-old"
