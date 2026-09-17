"""V4 Phase 4：canonical analysis model & normalizer regression tests.

驗證：
- valid provider output 正規化成 schema v2。
- legacy v1 shape 可正規化。
- minimal shape 可正規化。
- 缺 emotion → neutral fallback（合法 canonical）。
- malformed／oversized 安全失敗（AnalysisValidationError）。
- analysis source hash 記錄。
- artifact 以 analysis identity 儲存；路徑安全（不可 traversal）。
"""
import os
import json
from types import SimpleNamespace

import pytest

from backend import analyzer, db, settings, storage
from backend import v4_contracts as c
from backend.services import analysis
from backend.services.analysis import AnalysisOffsetMismatchError, AnalysisValidationError
from backend.tests.conftest import new_author, upload_book

CK = "ck-abcdef123456"
HASH = "0123456789abcdef"


def test_malformed_json_retries_only_current_response():
    malformed = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"segments": [}'))])
    repaired = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"segments": []}'))])
    import threading
    metrics = {"lock": threading.Lock(), "request_count": 0, "retry_count": 0}
    result = analyzer._extract_with_repair(
        lambda: malformed,
        metrics,
        repair_create=lambda raw: repaired,
    )
    assert result == {"segments": []}
    assert metrics["request_count"] == 2
    assert metrics["retry_count"] == 1


def test_dedicated_repair_tokens_are_clamped_to_model_capability():
    assert analyzer._repair_max_tokens("x" * 50_000, "gpt-4o-mini") == 16_384
    assert analyzer._repair_max_tokens("{}", "gpt-4o-mini") == 4_000


def test_provider_completion_capability_overrides_model_default(monkeypatch):
    analyzer._bind_provider({"max_completion_tokens": 8192})
    try:
        assert analyzer._repair_max_tokens("x" * 50_000, "gpt-4o-mini") == 8192
    finally:
        analyzer._bind_provider(None)


def test_malformed_json_repair_exhausted_is_deterministic():
    import threading
    metrics = {"lock": threading.Lock(), "request_count": 0, "retry_count": 0}
    analyzer._request_budget_local.current = {
        "requests": 0, "max_requests": 6, "malformed_retries": 0,
        "transient_retries": 0, "retries": 0,
    }
    try:
        with pytest.raises(analyzer.MalformedJSONError) as exc:
            analyzer._extract_with_repair(
                lambda: SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"segments": [}'))]),
                metrics,
                repair_create=lambda raw: SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="{still malformed"))]),
            )
        assert "segments" not in str(exc.value)
        assert metrics["request_count"] == 2
        assert metrics["retry_count"] == 1
    finally:
        analyzer._request_budget_local.current = None


def _v2_raw(**over):
    data = {
        "schemaVersion": 2,
        "chapterKey": CK,
        "sourceTextHash": HASH,
        "speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}],
        "segments": [
            {"id": "seg-0", "text": " 你好。 ", "speaker": "旁白",
             "emotion": {"value": "happy", "intensity": 0.6, "source": "ai"}},
            {"id": "seg-1", "text": "誰在那？", "speaker": "小明",
             "emotion": {"value": "tense", "intensity": 0.8}},
        ],
    }
    data.update(over)
    return data


def _v1_legacy_raw():
    return {
        "seq": 0,
        "analyzedAt": "2026-01-01T00:00:00",
        "speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}],
        "segments": [
            {"type": "narration", "speaker": "旁白", "text": "夜裡，風聲大作。"},
            {"type": "dialogue", "speaker": "道不明", "text": "「來者何人？」"},
        ],
    }


def test_legacy_v2_output_normalizes_to_schema_v3():
    out = analysis.normalize_analysis(_v2_raw(), chapter_key=CK, source_text_hash=HASH)
    assert out["schemaVersion"] == 3
    assert out["chapterKey"] == CK
    assert out["sourceTextHash"] == HASH
    compat = analysis.legacy_compatibility_view(out)
    assert compat["speakers"][0]["name"] == "旁白"
    assert len(out["segments"]) == 2
    seg0 = out["segments"][0]
    assert seg0["segment_id"] == "seg-0"
    assert seg0["text"] == "你好。"
    assert seg0["emotion"]["label"] == "happy"
    assert seg0["emotion"]["intensity"] == 0.6
    assert seg0["emotion"]["source"] == "ai"


def test_legacy_v1_output_normalizes():
    out = analysis.normalize_analysis(_v1_legacy_raw(), chapter_key=CK, source_text_hash=HASH)
    assert out["schemaVersion"] == 3
    assert out["segments"][0]["speaker_id"] == c.SPEAKER_ID_NARRATOR
    assert out["segments"][1]["text"] == "「來者何人？」"


def test_analyzer_preserves_inner_dialogue_quotes_for_source_alignment():
    raw = "猴子們紛紛說：「你好。」喊完一聲"
    assert analyzer._normalize_segment({"type": "dialogue", "text": raw})["text"] == raw
    assert analyzer._normalize_segment({"type": "dialogue", "text": "「你好。」"})["text"] == "你好。"


def test_analyzer_drops_noncanonical_empty_vocab_before_normalize(monkeypatch):
    monkeypatch.setattr(analyzer, "_split_text", lambda text: [text])
    monkeypatch.setattr(analyzer, "_call_llm", lambda *args, **kwargs: {
        "segments": [
            {"type": "vocab", "vocab": {}, "utterances": []},
            {"type": "narration", "text": "正文。"},
        ],
    })
    result = analyzer.analyze_chapter({"id": "fixture", "voices": {}}, 0, "正文。")
    assert [segment["type"] for segment in result["segments"]] == ["narration"]


def test_vocab_prompt_requires_verbatim_source_segments():
    prompt = analyzer._make_prompt({"category": "vocab", "vocabLevel": "AUTO"})
    assert "逐字複製章節原文" in prompt
    assert "不得改寫、摘要、翻譯" in prompt


def test_minimal_output_normalizes():
    out = analysis.normalize_analysis({"segments": [{"text": "只有一句。"}]},
                                      chapter_key=CK, source_text_hash=HASH)
    assert out["segments"][0]["speaker_id"] == c.SPEAKER_ID_NARRATOR
    assert out["segments"][0]["emotion"]["label"] == "neutral"
    assert out["segments"][0]["emotion"]["intensity"] == 0.5
    assert out["segments"][0]["emotion"]["source"] == "fallback"


def test_missing_emotion_falls_back_to_neutral():
    raw = {"segments": [{"text": "沒有情緒。"}]}
    out = analysis.normalize_analysis(raw, chapter_key=CK, source_text_hash=HASH)
    seg = out["segments"][0]
    assert seg["emotion"] == {"label": "neutral", "intensity": 0.5, "source": "fallback"}


def test_invalid_emotion_value_falls_back_to_neutral():
    raw = {"segments": [{"text": "奇怪情緒。", "emotion": {"value": "banana", "intensity": 9.9}}]}
    out = analysis.normalize_analysis(raw, chapter_key=CK, source_text_hash=HASH)
    assert out["segments"][0]["emotion"]["label"] == "neutral"
    assert out["segments"][0]["emotion"]["intensity"] == 0.5


def test_out_of_range_intensity_is_clamped():
    raw = {"segments": [{"text": "太強。", "emotion": {"value": "angry", "intensity": 5.0}}]}
    out = analysis.normalize_analysis(raw, chapter_key=CK, source_text_hash=HASH)
    assert out["segments"][0]["emotion"]["intensity"] == 1.0


@pytest.mark.parametrize("raw", [
    None,
    "not-a-dict",
    [1, 2, 3],
    {"speakers": []},
    {"segments": "not-a-list"},
    {"segments": [{"text": ""}]},
    {"segments": [{"text": 123}]},
    {"segments": [123, 456]},
    {"segments": [{"text": "x" * (analysis.MAX_TEXT_CHARS + 1)}]},
    {"segments": [{"text": "ok"} for _ in range(analysis.MAX_SEGMENTS + 1)]},
    {"segments": [{"text": "ok"}], "speakers": [{"name": "s"} for _ in range(analysis.MAX_SPEAKERS + 1)]},
])
def test_malformed_or_oversized_output_fails_safely(raw):
    with pytest.raises(AnalysisValidationError):
        analysis.normalize_analysis(raw, chapter_key=CK, source_text_hash=HASH)


def test_offset_mismatch_has_bounded_safe_diagnostics_and_still_fails():
    raw = {
        "segments": [{
            "type": "dialogue",
            "speaker_id": "speaker:unresolved",
            "text": "Authorization: Bearer supersecret 改寫後文字",
            "_analysis_chunk_index": 3,
            "_analysis_segment_index": 7,
        }],
    }
    with pytest.raises(AnalysisOffsetMismatchError) as exc_info:
        analysis.normalize_analysis(
            raw,
            chapter_key=CK,
            source_text_hash=HASH,
            source_text="前文 Authorization: Bearer supersecret 後文",
            analysis_id=42,
            chapter_id=99,
        )

    diagnostics = exc_info.value.diagnostics
    assert diagnostics["analysisId"] == 42
    assert diagnostics["chapterId"] == 99
    assert diagnostics["chunkIndex"] == 3
    assert diagnostics["segmentIndex"] == 7
    assert diagnostics["segmentType"] == "dialogue"
    assert diagnostics["speakerId"] == "speaker:unresolved"
    assert diagnostics["failureCode"] == "analysis_offset_mismatch"
    assert diagnostics["comparison"]["exact"] is False
    assert len(diagnostics["segmentText"]) <= analysis.OFFSET_DIAGNOSTIC_TEXT_LIMIT
    assert len(diagnostics["sourceExcerpt"]) <= analysis.OFFSET_DIAGNOSTIC_EXCERPT_LIMIT
    serialized = json.dumps(diagnostics, ensure_ascii=False)
    assert "supersecret" not in serialized
    assert "Authorization: Bearer [REDACTED]" in serialized
    failure = json.loads(analysis.build_failure_error(exc_info.value))
    assert failure["code"] == "analysis_validation_failed"
    assert failure["diagnostics"] == diagnostics


@pytest.mark.parametrize("source, segment", [
    ("「你好。」", "你好。"),
    ("『你好』", "你好"),
    ("你好。", "「你好。」"),
])
def test_dialogue_outer_quote_wrapper_alignment_is_deterministic(source, segment):
    out = analysis.normalize_analysis(
        {"segments": [{"type": "dialogue", "text": segment}]},
        chapter_key=CK, source_text_hash=HASH, source_text=source,
    )
    item = out["segments"][0]
    assert source[item["source_start"]:item["source_end"]] == segment.strip("「」『』“”\"")


@pytest.mark.parametrize("source, segment", [
    ("你好。", "你好"),
    ("王老師的女兒", "王老師"),
    ("「你去哪裡？」", "你要去哪裡？"),
])
def test_offset_alignment_does_not_allow_punctuation_partial_name_or_paraphrase(source, segment):
    with pytest.raises(AnalysisOffsetMismatchError):
        analysis.normalize_analysis(
            {"segments": [{"type": "dialogue", "text": segment}]},
            chapter_key=CK, source_text_hash=HASH, source_text=source,
        )


def test_oversized_raw_fails_safely():
    big = {"segments": [{"text": "x" * 1000} for _ in range(2000)]}
    with pytest.raises(AnalysisValidationError):
        analysis.normalize_analysis(big, chapter_key=CK, source_text_hash=HASH)


def test_all_invalid_segments_fail():
    raw = {"segments": [{"text": ""}, {"text": ""}]}
    with pytest.raises(AnalysisValidationError):
        analysis.normalize_analysis(raw, chapter_key=CK, source_text_hash=HASH)


def test_validate_artifact_rejects_bad_shape():
    with pytest.raises(AnalysisValidationError):
        analysis.validate_artifact({"schemaVersion": 1, "segments": []})
    with pytest.raises(AnalysisValidationError):
        analysis.validate_artifact({"schemaVersion": 3, "chapterKey": "", "sourceTextHash": "h", "segments": []})
    with pytest.raises(AnalysisValidationError):
        analysis.validate_artifact({"schemaVersion": 3, "chapterKey": "k", "sourceTextHash": "h",
                                    "segments": [{"id": "s0", "text": "t", "emotion": {"value": "nope"}}]})


def test_v3_unresolved_dialogue_is_not_narration_and_keeps_evidence():
    out = analysis.normalize_analysis({
        "segments": [{"type": "dialogue", "text": "你是誰？",
                      "attribution": {"method": "unresolved", "confidence": 0.2,
                                       "evidence_text": "前後沒有明示語者",
                                       "candidates": ["char_a"]}}]},
        chapter_key=CK, source_text_hash=HASH, source_text="你是誰？")
    seg = out["segments"][0]
    assert seg["speaker_id"] == c.SPEAKER_ID_UNRESOLVED
    assert seg["type"] == "dialogue"
    assert seg["attribution"]["candidates"] == ["char_a"]
    assert seg["attribution"]["confidence"] == 0.2


def test_v3_affect_attribution_and_source_offsets_round_trip():
    source = "他猛地拍桌：你給我出去！"
    out = analysis.normalize_analysis({
        "segments": [{
            "type": "dialogue", "text": "你給我出去！", "speaker": "他",
            "emotion": {"label": "angry", "intensity": 0.9, "source": "ai"},
            "tone": "命令", "speaking_style": "激烈、短促",
            "attribution": {"method": "explicit", "confidence": 0.95,
                            "evidence_text": "他猛地拍桌",
                            "evidence_start": 0, "evidence_end": 5},
        }]}, chapter_key=CK, source_text_hash=HASH, source_text=source, book_id="book-1")
    seg = out["segments"][0]
    assert source[seg["source_start"]:seg["source_end"]] == seg["text"]
    assert seg["emotion"]["label"] == "angry"
    assert seg["tone"] == "命令"
    assert seg["speaking_style"] == "激烈、短促"
    assert seg["attribution"]["evidence_text"] == "他猛地拍桌"
    assert seg["attribution"]["evidence_start"] == 0


def test_v3_rejects_non_character_speaker_namespace():
    with pytest.raises(AnalysisValidationError):
        analysis.normalize_analysis({
            "segments": [{"type": "dialogue", "speaker_id": "speaker:bad", "text": "你好。"}],
        }, chapter_key=CK, source_text_hash=HASH, source_text="你好。")


def test_v3_aliases_and_containing_names_are_explicit_not_substring_merged():
    out = analysis.normalize_analysis({
        "characters": [
            {"character_id": "char_x", "canonical_name": "孫悟空",
             "aliases": ["齊天大聖", "弼馬溫"]},
            {"character_id": "char_teacher", "canonical_name": "王老師"},
            {"character_id": "char_daughter", "canonical_name": "王老師的女兒"},
        ],
        "segments": [
            {"type": "dialogue", "speaker_id": "char_x", "speaker_surface": "齊天大聖", "text": "俺老孫來也。"},
            {"type": "dialogue", "speaker_id": "char_teacher", "text": "請進。"},
            {"type": "dialogue", "speaker_id": "char_daughter", "text": "您好。"},
        ]}, chapter_key=CK, source_text_hash=HASH, source_text="俺老孫來也。請進。您好。", book_id="book-1")
    assert len(out["characters"]) == 3
    assert [s["speaker_id"] for s in out["segments"]] == ["char_x", "char_teacher", "char_daughter"]
    assert out["characters"][0]["aliases"] == ["齊天大聖", "弼馬溫"]


@pytest.mark.parametrize("raw, expected_type", [
    ({"segments": [{"type": "narration", "text": "夜裡下雨。"},
                    {"type": "dialogue", "speaker": "小明", "text": "快走！",
                     "attribution": {"method": "explicit", "confidence": 0.9}}]}, "dialogue"),
    ({"segments": [{"type": "dialogue", "speaker": "甲", "text": "你來了。"},
                    {"type": "dialogue", "text": "嗯。",
                     "attribution": {"method": "continuation", "confidence": 0.6}}]}, "dialogue"),
    ({"characters": [{"character_id": "char_x", "canonical_name": "孫悟空",
                       "aliases": ["齊天大聖", "弼馬溫"]}],
      "segments": [{"type": "dialogue", "speaker_id": "char_x", "text": "俺老孫來也。"}]}, "dialogue"),
    ({"characters": [{"character_id": "char_a", "canonical_name": "王老師"},
                      {"character_id": "char_b", "canonical_name": "王老師的女兒"}],
      "segments": [{"type": "dialogue", "speaker_id": "char_a", "text": "請進。"},
                    {"type": "dialogue", "speaker_id": "char_b", "text": "謝謝。"}]}, "dialogue"),
    ({"characters": [{"character_id": "char_revealed", "canonical_name": "周衡",
                       "aliases": ["黑衣男子"]}],
      "segments": [{"type": "narration", "text": "黑衣男子走來。"},
                    {"type": "dialogue", "speaker_id": "char_revealed", "text": "是我。"}]}, "dialogue"),
])
def test_v3_regression_fixture_categories_are_representable(raw, expected_type):
    source_text = "".join(seg["text"] for seg in raw["segments"])
    out = analysis.normalize_analysis(raw, chapter_key=CK, source_text_hash=HASH,
                                      source_text=source_text, book_id="fixture-book")
    assert out["schemaVersion"] == 3
    assert any(seg["type"] == expected_type for seg in out["segments"])


def test_analysis_row_records_source_hash_and_ready_matches():
    a = new_author()
    book = upload_book(a)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    aid = analysis.create_analysis_row(book_id=row["id"], chapter_id=ch["id"],
                                       source_text_hash=ch["text_hash"], prompt_version="v1")
    rec = db.get_chapter_analysis(aid)
    assert rec["source_text_hash"] == ch["text_hash"]
    assert rec["status"] == "queued"
    # 未 ready 前，get_ready 回傳 None
    assert db.get_ready_chapter_analysis(ch["id"], ch["text_hash"]) is None
    # 準備 artifact 並標 ready
    artifact = analysis.normalize_analysis(
        {"segments": [{"text": "第一句。"}]}, chapter_key=ch["chapter_key"], source_text_hash=ch["text_hash"])
    rel = analysis.save_ready_analysis(aid, artifact)
    assert rel == storage.analysis_artifact_path(book["id"], aid)
    assert os.path.exists(os.path.join(settings.ROOT_DIR, rel))
    assert db.get_chapter_analysis(aid)["status"] == "ready"
    ready = db.get_ready_chapter_analysis(ch["id"], ch["text_hash"])
    assert ready is not None and ready["id"] == aid
    # 不同 hash 不 match
    assert db.get_ready_chapter_analysis(ch["id"], "deadbeef") is None


def test_analysis_artifact_path_is_identity_based_and_safe():
    # 以 integer analysis id 為單位，book_id 只能取自有權限的 bid
    rel = storage.analysis_artifact_path("b-safe-1", 42)
    assert rel == "storage/books/b-safe-1/analyses/42.json"
    # 任何目錄穿越嘗試都不應產生可逃脫路徑（analysis id 為 int，非字串）
    with pytest.raises(Exception):
        storage.analysis_artifact_path("../../etc", "passwd")
    assert ".." not in rel
    assert not os.path.isabs(rel)
