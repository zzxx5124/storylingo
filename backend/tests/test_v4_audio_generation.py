"""V4 Phase 6：Audio generation domain regression tests.

驗證：
- Generation record 可決定性建立／重用（same key 不重複）。
- Text/settings 變更 → 不同 desired key。
- Failed generation 不能成為 active。
- 只有 ready 且文字相符的 generation 才能 active；failure 保留舊 active。
- Effective mode/voice resolution（章節覆寫 > 書級）。
"""
import os

import pytest

from backend import db, storage
from backend import v4_contracts as c
from backend.services import audio_generation as ag
from backend.tests.conftest import new_author, upload_book, add_chapter


def _book(a, *, audio_mode=None):
    book = upload_book(a)
    if audio_mode:
        db.update_book(book["id"], {"audio_mode": audio_mode})
    return book


def _book_dict(book):
    row = db.get_book_row(book["id"])
    return dict(row)


def _chapter_dict(book, seq=0):
    row = db.get_book_row(book["id"])
    return dict(db.get_chapter(row["id"], seq))


def _provider():
    # 建立真實 tts_provider row 以滿足 FK（冪等）
    if not db.get_active_tts_provider():
        db.create_tts_provider({
            "name": "測試TTS", "provider_type": "generic_http", "base_url": "https://tts.example.com",
            "enabled": True, "is_default": True,
        })
    return dict(db.get_active_tts_provider())


def test_effective_mode_and_voice_resolution(author=None):
    a = author or new_author()
    book = _book(a, audio_mode="multi")
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    assert ag.effective_mode(book_dict, ch) == "multi"
    # 章節 override 優先
    row = db.get_book_row(book["id"])
    db.update_chapter(row["id"], 0, {"audio_mode_override": "single"})
    ch2 = _chapter_dict(book)
    assert ag.effective_mode(book_dict, ch2) == "single"
    # voice resolution
    db.update_book(book["id"], {"default_voice_id": "voice-book"})
    assert ag.effective_voice_id(_book_dict(book), _chapter_dict(book)) == "voice-book"
    db.update_chapter(row["id"], 0, {"voice_override_id": "voice-ch"})
    assert ag.effective_voice_id(_book_dict(book), _chapter_dict(book)) == "voice-ch"


def test_same_key_reuses_generation_no_duplicate(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    gen1, created1 = ag.create_or_reuse_generation(
        book=book_dict, chapter=ch, source_text_hash=ch["text_hash"], tts_provider=_provider())
    assert created1 is True
    gen2, created2 = ag.create_or_reuse_generation(
        book=book_dict, chapter=ch, source_text_hash=ch["text_hash"], tts_provider=_provider())
    assert created2 is False
    assert gen2["id"] == gen1["id"]
    assert gen2["generation_key"] == gen1["generation_key"]
    # 只有一個 generation
    gens = db.list_audio_generations(ch["id"])
    assert len(gens) == 1


def test_text_change_produces_different_key(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    k1 = ag.compute_generation_key(book=book_dict, chapter=ch, mode="single", voice_id=None,
                                   source_text_hash=ch["text_hash"], analysis_id=None,
                                   tts_provider=_provider(), emotion_policy="best_effort")
    k2 = ag.compute_generation_key(book=book_dict, chapter=ch, mode="single", voice_id=None,
                                   source_text_hash="different-hash", analysis_id=None,
                                   tts_provider=_provider(), emotion_policy="best_effort")
    assert k1 != k2


def test_settings_change_produces_different_key(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    args = dict(book=book_dict, chapter=ch, mode="single", voice_id="v1",
                source_text_hash=ch["text_hash"], analysis_id=None,
                tts_provider=_provider(), emotion_policy="best_effort")
    k1 = ag.compute_generation_key(**args)
    args2 = dict(args, voice_id="v2")
    k2 = ag.compute_generation_key(**args2)
    assert k1 != k2
    # provider config version 變更
    args3 = dict(args, tts_provider={"id": 1, "config_version": 2, "adapter_key": "generic_http"})
    assert ag.compute_generation_key(**args3) != k1


def test_voice_snapshot_and_provider_version_in_key(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    k = ag.compute_generation_key(book=book_dict, chapter=ch, mode="single", voice_id="voice-a",
                                  source_text_hash=ch["text_hash"], analysis_id=None,
                                  tts_provider=_provider(), emotion_policy="best_effort")
    k2 = ag.compute_generation_key(book=book_dict, chapter=ch, mode="single", voice_id="voice-b",
                                   source_text_hash=ch["text_hash"], analysis_id=None,
                                   tts_provider=_provider(), emotion_policy="best_effort")
    assert k != k2


def test_failed_generation_cannot_become_active(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    gen, _ = ag.create_or_reuse_generation(book=book_dict, chapter=ch,
                                           source_text_hash=ch["text_hash"], tts_provider=_provider())
    db.mark_generation_failed(gen["id"], "boom")
    # failed generation 不應被 resolve 為 active
    ch_failed = _chapter_dict(book)
    ch_failed["active_audio_generation_id"] = gen["id"]
    assert ag.resolve_active_generation(book_dict, ch_failed) is None


def test_activation_only_for_ready_matching(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    gen, _ = ag.create_or_reuse_generation(book=book_dict, chapter=ch,
                                           source_text_hash=ch["text_hash"], tts_provider=_provider())
    row = db.get_book_row(book["id"])
    audio_rel = storage.generation_audio_path(book["id"], gen["id"])
    timing_rel = storage.generation_timing_path(book["id"], gen["id"])
    ag.mark_generation_ready_and_activate(book=book_dict, chapter=ch, generation_id=gen["id"],
                                          audio_path=audio_rel, timing_path=timing_rel)
    ch_after = _chapter_dict(book)
    assert ch_after["active_audio_generation_id"] == gen["id"]
    # 文字變更後，原 active 不再 resolve
    db.update_chapter(row["id"], 0, {"text_hash": "changed", "text": "改。" * 30})
    ch_stale = _chapter_dict(book)
    assert ag.resolve_active_generation(_book_dict(book), ch_stale) is None


def test_failure_leaves_prior_active_pointer_unchanged(author=None):
    a = author or new_author()
    book = _book(a)
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    gen1, _ = ag.create_or_reuse_generation(book=book_dict, chapter=ch,
                                            source_text_hash=ch["text_hash"], tts_provider=_provider())
    ag.mark_generation_ready_and_activate(book=book_dict, chapter=ch, generation_id=gen1["id"],
                                          audio_path=storage.generation_audio_path(book["id"], gen1["id"]),
                                          timing_path=storage.generation_timing_path(book["id"], gen1["id"]))
    row = db.get_book_row(book["id"])
    ch_before = _chapter_dict(book)
    assert ch_before["active_audio_generation_id"] == gen1["id"]

    # 第二次 generation 失敗 → active 不變
    gen2, created = ag.create_or_reuse_generation(book=book_dict, chapter=ch,
                                                  source_text_hash="new-hash-2", tts_provider=_provider())
    assert created is True
    db.mark_generation_failed(gen2["id"], "tts error")
    ch_after = _chapter_dict(book)
    assert ch_after["active_audio_generation_id"] == gen1["id"]


def test_multi_mode_requires_analysis_id_in_key(author=None):
    a = author or new_author()
    book = _book(a, audio_mode="multi")
    book_dict = _book_dict(book)
    ch = _chapter_dict(book)
    k_no = ag.compute_generation_key(book=book_dict, chapter=ch, mode="multi", voice_id=None,
                                     source_text_hash=ch["text_hash"], analysis_id=None,
                                     tts_provider=_provider(), emotion_policy="best_effort")
    k_yes = ag.compute_generation_key(book=book_dict, chapter=ch, mode="multi", voice_id=None,
                                      source_text_hash=ch["text_hash"], analysis_id=42,
                                      tts_provider=_provider(), emotion_policy="best_effort")
    assert k_no != k_yes


def test_generation_artifact_path_is_identity_based(author=None):
    rel = storage.generation_audio_path("b-safe", 7)
    assert rel == "storage/books/b-safe/generations/7/audio.mp3"
    assert not os.path.isabs(rel)
    assert ".." not in rel
