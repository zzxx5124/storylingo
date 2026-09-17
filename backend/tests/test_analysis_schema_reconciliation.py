"""ASR Option A contract tests.

These tests deliberately exercise the version boundary rather than relying on
numeric ordering.  v4 is native, v3 is compatibility, v1/v2 are legacy, and
unknown/malformed values fail closed.
"""
import pytest

from backend import db
from backend import v4_contracts as contracts
from backend.services import analysis
from backend.services import analysis_version_policy as policy
from backend.services import source_faithful
from backend.tests.conftest import new_author, upload_book


@pytest.mark.parametrize(("value", "expected"), [
    (4, policy.NATIVE),
    (3, policy.COMPATIBILITY),
    (2, policy.LEGACY),
    (1, policy.LEGACY),
    (None, policy.UNSUPPORTED),
    ("4", policy.UNSUPPORTED),
    (True, policy.UNSUPPORTED),
    (0, policy.UNSUPPORTED),
    (-1, policy.UNSUPPORTED),
    (5, policy.UNSUPPORTED),
    (999, policy.UNSUPPORTED),
])
def test_analysis_artifact_version_policy_is_explicit(value, expected):
    assert policy.classify_version(value) == expected


def test_analysis_contract_names_v4_as_canonical_and_v3_as_compatibility():
    assert contracts.ANALYSIS_SCHEMA_VERSION == 4
    assert contracts.ANALYSIS_SCHEMA_VERSION_V3 == 3
    assert policy.CANONICAL_WRITE_VERSION == 4
    assert policy.NATIVE_READ_VERSIONS == frozenset({4})
    assert policy.COMPATIBILITY_READ_VERSIONS == frozenset({3})


def test_execution_target_rejects_legacy_and_unknown_versions():
    assert policy.require_execution_target(3) == 3
    assert policy.require_execution_target(4) == 4
    for value in (1, 2, None, "4", 5, 999):
        with pytest.raises(policy.AnalysisVersionError):
            policy.require_execution_target(value)


@pytest.mark.parametrize("legacy_version", [1, 2, 3])
def test_legacy_normalizer_is_explicit_v3_adapter(legacy_version):
    raw = {
        "schemaVersion": legacy_version,
        "speakers": [{"name": "旁白"}],
        "segments": [{"type": "narration", "speaker": "旁白", "text": "一句話。"}],
    }
    result = analysis.normalize_analysis(
        raw, chapter_key="legacy", source_text_hash="hash", source_text="一句話。"
    )
    assert result["schemaVersion"] == contracts.ANALYSIS_SCHEMA_VERSION_V3
    if legacy_version in (1, 2):
        assert result["legacySource"] is True


def test_normalizer_never_downconverts_v4_artifact():
    with pytest.raises(analysis.AnalysisValidationError):
        analysis.normalize_analysis({
            "schemaVersion": 4,
            "segments": [{"type": "narration", "text": "不可降版。"}],
        }, chapter_key="v4", source_text_hash="hash", source_text="不可降版。")


def test_read_boundaries_accept_only_v3_v4_source_faithful_compatibility():
    v3 = {"schemaVersion": 3, "segments": []}
    v4 = {"schemaVersion": 4, "segments": []}
    assert source_faithful.read_compatible_artifact(v3) is v3
    assert source_faithful.read_compatible_artifact(v4) is v4
    for artifact in ({"schemaVersion": 2}, {"schemaVersion": 5}, {"schemaVersion": None}, {}):
        with pytest.raises(source_faithful.SourceFaithfulnessError):
            source_faithful.read_compatible_artifact(artifact)


def test_legacy_projection_is_read_only_and_unknown_versions_fail_closed():
    legacy = {
        "schemaVersion": 2,
        "chapterKey": "legacy",
        "sourceTextHash": "hash",
        "speakers": [{"name": "旁白"}],
        "segments": [{"id": "s1", "text": "舊內容", "speaker": "旁白"}],
    }
    projection = analysis.legacy_compatibility_view(legacy)
    assert projection["schemaVersion"] == 2
    assert projection["compatibilityOnly"] is True
    with pytest.raises(analysis.AnalysisValidationError):
        analysis.legacy_compatibility_view({"schemaVersion": 999, "segments": []})
    with pytest.raises(analysis.AnalysisValidationError):
        analysis.legacy_compatibility_view({"segments": []})


def test_v4_writer_rejects_legacy_downconversion(client):
    author = new_author("asr_writer_guard")
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = analysis.create_analysis_row(
        book_id=row["id"], chapter_id=chapter["id"], source_text_hash=chapter["text_hash"],
    )
    segmentation = source_faithful.segment_source(chapter["text"])
    artifact = source_faithful.apply_annotations(
        segmentation,
        [{"segment_id": item["segment_id"], "type": item["type"],
          "speaker_id": "speaker:narrator"} for item in segmentation["segments"]],
        source=chapter["text"],
    )
    with pytest.raises(analysis.AnalysisValidationError, match="source-faithful"):
        analysis.save_ready_analysis(analysis_id, artifact)
    assert db.get_chapter_analysis(analysis_id)["schema_version"] == 4


@pytest.mark.parametrize("source", [
    "繁體中文句子。",
    "An English sentence.",
    "繁體中文 and English mixed.",
])
def test_v4_native_artifact_is_valid_for_supported_language_inputs(source):
    segmentation = source_faithful.segment_source(source)
    artifact = source_faithful.apply_annotations(
        segmentation,
        [{"segment_id": item["segment_id"], "type": item["type"],
          "speaker_id": "speaker:narrator"} for item in segmentation["segments"]],
        source=source,
    )
    source_faithful.validate_v4_artifact(artifact, source)
    assert artifact["schemaVersion"] == 4


def test_cache_identity_isolated_between_v3_and_v4():
    common = dict(
        source_text_hash="source", chunk_index=0, chunk_input="chunk", context="",
        prompt_hash="prompt", prompt_version="p1", analysis_profile="speaker",
        provider_identity="provider", model_identity="model", provider_config_version=1,
        fallback_models=[], language="zh", category="小說",
    )
    v3 = policy.require_version(3)[0]
    v4 = policy.require_version(4)[0]
    from backend.services.analysis_partials import build_cache_identity
    first = build_cache_identity(**common, schema_version=v3)
    second = build_cache_identity(**common, schema_version=v4)
    assert first["cacheKey"] != second["cacheKey"]
    with pytest.raises(policy.AnalysisVersionError):
        build_cache_identity(**common, schema_version=5)
