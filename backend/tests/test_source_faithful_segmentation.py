import pytest

from backend.services.source_faithful import (
    SCHEMA_VERSION,
    SourceFaithfulnessError,
    apply_annotations,
    build_annotation_request,
    group_segments,
    segment_source,
    validate_child_span,
    read_compatible_artifact,
)
from backend.services.analysis_partials import build_cache_identity
from backend.services.analysis import legacy_compatibility_view


def test_source_slice_preserves_omitted_prefix_case():
    source = "旁白。\n「你如今還敢回來？」"
    result = segment_source(source)
    dialogue = next(item for item in result["segments"] if item["type"] == "dialogue")
    assert dialogue["text"] == "「你如今還敢回來？」"
    assert source[dialogue["source_start"]:dialogue["source_end"]] == dialogue["text"]


def test_chinese_english_quotes_and_continuous_coverage():
    source = "前文。\n「中文。」後文。\n\"English.\"結尾。"
    result = segment_source(source)
    assert [item["type"] for item in result["segments"]].count("dialogue") == 2
    assert "English." in result["segments"][-2]["text"]
    assert result["schemaVersion"] == SCHEMA_VERSION


def test_english_prose_keeps_words_together_and_splits_sentences():
    source = 'Alice looked at the sky. Bob smiled and answered: "Indeed."'
    result = segment_source(source)

    assert [item["text"] for item in result["segments"]] == [
        "Alice looked at the sky.",
        'Bob smiled and answered:',
        '"Indeed."',
    ]
    assert result["segments"][0]["text"] != "Alice"
    assert result["segments"][1]["text"] != "Bob"


@pytest.mark.parametrize("source", [
    "Alice looked at the sky and smiled.",
    "Alice looked at the sky\nand smiled.",
    "Alice looked at the sky\r\nand smiled.",
    "Alice\n\nBob",
    "A single word",
    "一個中文句子。",
    "English and 中文 mixed.\n第二句。",
])
def test_segmentation_never_creates_word_units_from_formatting(source):
    result = segment_source(source)
    assert result["segments"]
    assert all(item["text"].strip() for item in result["segments"])
    assert all(source[item["source_start"]:item["source_end"]] == item["text"] for item in result["segments"])
    if " " in source:
        # 一般空格屬於正文，不應形成每個單字一個片段；句子或空白行邊界
        # 仍可形成多個語意片段，但本 fixture 每個區塊只有一個此類單位。
        assert all(len(item["text"].split()) > 1 for item in result["segments"] if " " in item["text"])


def test_quote_terminal_punctuation_stays_with_dialogue_span():
    source = "他沒有把「挪動」改成「竄改」。"
    result = segment_source(source)

    assert "".join(item["text"] for item in result["segments"]) == source
    assert [item["text"] for item in result["segments"]] == [
        "他沒有把", "「挪動」", "改成", "「竄改」。",
    ]
    assert all(item["text"] != "。" for item in result["segments"])
    assert result["segments"][-1]["type"] == "dialogue"


def test_paraphrase_and_invalid_id_are_rejected():
    result = segment_source("「你如今還敢回來？」")
    with pytest.raises(SourceFaithfulnessError):
        apply_annotations(result, [{"segment_id": "missing", "type": "dialogue"}], source="「你如今還敢回來？」")
    segment_id = result["segments"][0]["segment_id"]
    with pytest.raises(SourceFaithfulnessError):
        apply_annotations(result, [{"segment_id": segment_id, "text": "你敢回來？"}], source="「你如今還敢回來？」")


def test_annotation_can_never_change_backend_text():
    source = "「你如今還敢回來？」"
    segmentation = segment_source(source)
    segment_id = segmentation["segments"][0]["segment_id"]
    artifact = apply_annotations(segmentation, [{"segment_id": segment_id, "type": "dialogue", "emotion": {"label": "angry"}}], source=source)
    assert artifact["segments"][0]["text"] == source
    assert artifact["segments"][0]["emotion"] == {"label": "angry"}


def test_vocab_learning_metadata_is_separate_from_source_owned_segments():
    source = "旁白。\n「書。」"
    segmentation = segment_source(source)
    dialogue = next(item for item in segmentation["segments"] if item["type"] == "dialogue")
    annotations = [{"segment_id": item["segment_id"], "type": item["type"]} for item in segmentation["segments"]]
    annotations[1]["vocab"] = {
        "zh": "書", "en": "book", "spelling": "B-O-O-K",
        "example": "This is a book.", "level": "A1",
    }

    artifact = apply_annotations(segmentation, annotations, source=source)

    assert artifact["segments"][1]["segment_id"] == dialogue["segment_id"]
    assert artifact["segments"][1]["text"] == source[dialogue["source_start"]:dialogue["source_end"]]
    assert "vocab" not in artifact["segments"][1]
    assert artifact["learning"]["vocab"] == [{
        "segment_id": dialogue["segment_id"],
        "zh": "書", "en": "book", "spelling": "B-O-O-K",
        "example": "This is a book.", "level": "A1",
    }]


def test_annotation_cannot_change_backend_owned_dialogue_type():
    source = "旁白。\n「你如今還敢回來？」"
    segmentation = segment_source(source)
    dialogue = next(item for item in segmentation["segments"] if item["type"] == "dialogue")
    artifact = apply_annotations(segmentation, [{
        "segment_id": dialogue["segment_id"],
        "type": "narration",
        "speaker": "char_001",
    }, *[
        {"segment_id": item["segment_id"], "type": item["type"]}
        for item in segmentation["segments"]
        if item["segment_id"] != dialogue["segment_id"]
    ]], source=source)
    result = next(item for item in artifact["segments"] if item["segment_id"] == dialogue["segment_id"])
    assert result["type"] == "dialogue"
    assert result["speaker"] == "char_001"


def test_dialogue_surface_gets_deterministic_provisional_character_id():
    source = "「你好。」"
    segmentation = segment_source(source)
    segment_id = segmentation["segments"][0]["segment_id"]
    artifact = apply_annotations(segmentation, [{
        "segment_id": segment_id,
        "type": "dialogue",
        "speaker": "甲",
        "speaker_candidate": None,
        "attribution": {"method": "explicit", "confidence": 0.9},
        "confidence": 0.9,
    }], source=source)
    item = artifact["segments"][0]
    assert item["speaker_id"].startswith("char_")
    assert artifact["characters"][0]["canonical_name"] == "甲"


def test_dialogue_speaker_profile_is_preserved_for_voice_matching():
    source = "「我是爸爸。」"
    segmentation = segment_source(source)
    segment_id = segmentation["segments"][0]["segment_id"]
    artifact = apply_annotations(segmentation, [{
        "segment_id": segment_id,
        "type": "dialogue",
        "speaker": "父親",
        "speaker_gender": "男",
        "speaker_age": "中年",
        "attribution": {"method": "explicit", "confidence": 0.9},
    }], source=source)

    assert artifact["characters"] == [{
        "character_id": artifact["characters"][0]["character_id"],
        "canonical_name": "父親",
        "aliases": [],
        "gender": "男",
        "age_group": "中年",
    }]


def test_v4_assembly_preserves_stable_annotated_character_identity():
    source = "「你好」"
    segmentation = segment_source(source)
    segment_id = segmentation["segments"][0]["segment_id"]
    artifact = apply_annotations(segmentation, [{
        "segment_id": segment_id, "type": "dialogue", "speaker_id": "char_001",
        "speaker": "主角",
    }], source=source)
    assert artifact["characters"][0]["character_id"] == "char_001"
    assert artifact["segments"][0]["speaker_id"] == "char_001"


def test_annotation_request_contains_ids_but_not_canonical_text():
    source = "旁白。\n「你好。」"
    request = build_annotation_request(segment_source(source), context="read-only context")
    assert request["context"] == "read-only context"
    assert all("text" not in item for item in request["segments"])


def test_grouping_keeps_whole_spans_without_cross_chunk_duplicates():
    segmentation = segment_source("甲甲甲。\n乙乙乙。\n丙丙丙。")
    groups = group_segments(segmentation, max_chars=4)
    ids = [span["segment_id"] for group in groups for span in group["segments"]]
    assert ids == [span["segment_id"] for span in segmentation["segments"]]
    assert len(ids) == len(set(ids))


def test_grouping_limits_annotation_count_without_changing_source_spans():
    source = "\n".join(f"「第{index}句。」" for index in range(5))
    segmentation = segment_source(source)
    groups = group_segments(segmentation, max_chars=10_000, max_segments=2)
    assert [len(group["segments"]) for group in groups] == [2, 2, 1]
    ids = [span["segment_id"] for group in groups for span in group["segments"]]
    assert ids == [span["segment_id"] for span in segmentation["segments"]]


def test_missing_annotation_and_v3_compatibility():
    segmentation = segment_source("甲。\n乙。")
    first = segmentation["segments"][0]["segment_id"]
    with pytest.raises(SourceFaithfulnessError):
        apply_annotations(segmentation, [{"segment_id": first, "type": "narration"}], source="甲。\n乙。")
    legacy = {"schemaVersion": 3, "segments": []}
    assert read_compatible_artifact(legacy) is legacy


def test_child_span_must_be_inside_parent():
    parent = {"source_start": 10, "source_end": 20}
    validate_child_span(parent, {"relative_start": 1, "relative_end": 9})
    with pytest.raises(SourceFaithfulnessError):
        validate_child_span(parent, {"relative_start": 1, "relative_end": 11})
    with pytest.raises(SourceFaithfulnessError):
        validate_child_span(parent, {"relative_start": 2, "relative_end": 2})


def test_non_formatting_gap_is_rejected():
    source = "甲乙"
    with pytest.raises(SourceFaithfulnessError):
        from backend.services.source_faithful import validate_spans
        validate_spans(source, [{"segment_id": "a", "source_start": 0, "source_end": 1, "text": "甲"}])


def test_cache_identity_includes_segmentation_and_span_hashes():
    common = dict(source_text_hash="s", chunk_index=0, chunk_input="x", context="", prompt_hash="p",
                  prompt_version="1", schema_version=4, analysis_profile="speaker", provider_identity="p",
                  model_identity="m", provider_config_version=1, fallback_models=[], language="zh", category="")
    a = build_cache_identity(**common, segmentation_version="seg-v1", span_hashes=["a"])
    b = build_cache_identity(**common, segmentation_version="seg-v2", span_hashes=["a"])
    c = build_cache_identity(**common, segmentation_version="seg-v1", span_hashes=["b"])
    assert a["cacheKey"] != b["cacheKey"]
    assert a["cacheKey"] != c["cacheKey"]


def test_v4_artifact_uses_compatibility_view_without_legacy_offset_lookup():
    source = "旁白。\n「你好」"
    segmentation = segment_source(source)
    artifact = apply_annotations(segmentation, [
        {"segment_id": span["segment_id"], "type": span["type"],
         "speaker_id": "speaker:narrator" if span["type"] == "narration" else "speaker:unresolved"}
        for span in segmentation["segments"]
    ], source=source)
    compat = legacy_compatibility_view(artifact)
    assert compat["schemaVersion"] == 2
    assert len(compat["segments"]) == len(artifact["segments"])
    assert compat["segments"][0]["text"] == "旁白。"


def test_mixed_v3_v4_artifacts_both_expose_playable_compatibility_segments():
    v3 = {"schemaVersion": 3, "chapterKey": "legacy", "sourceTextHash": "hash",
          "characters": [], "segments": [{"segment_id": "legacy-1", "text": "舊章",
          "type": "narration", "speaker_id": "speaker:narrator", "emotion": {"label": "neutral", "intensity": 0.5},
          "source_start": 0, "source_end": 2}]}
    v4_source = "新章。"
    segmentation = segment_source(v4_source)
    v4 = apply_annotations(segmentation, [{"segment_id": segmentation["segments"][0]["segment_id"],
                                           "type": "narration", "speaker_id": "speaker:narrator"}], source=v4_source)
    assert legacy_compatibility_view(v3)["segments"][0]["text"] == "舊章"
    assert legacy_compatibility_view(v4)["segments"][0]["text"] == "新章。"
