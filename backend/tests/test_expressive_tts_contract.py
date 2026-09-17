"""Expressive TTS contract regression tests."""
import pytest

from backend import v4_contracts as c
from backend.services import expressive_tts
from backend.services import analysis
from backend.services import audio_generation as ag


def _cap(*, emotion=True, intensity=False, style=False):
    return {"emotion": {"supported": emotion, "labels": ["neutral", "anger"],
                         "supportsIntensity": intensity, "supportsStyle": style}}


def _segment(value="angry", intensity=0.8, tone="命令"):
    return {"segment_id": "seg-1", "text": "出去！", "emotion": {"label": value, "intensity": intensity},
            "tone": tone, "speaking_style": "短促"}


def test_requested_angry_applied_neutral_is_traceable():
    result = expressive_tts.resolve_expression(
        segment=_segment(), speaker_id="char_x", voice_id="voice-1",
        capabilities=_cap(emotion=False), profiles=[],
    )
    assert result["requestedExpression"]["emotion"] == "angry"
    assert result["appliedExpression"]["emotion"] == "neutral"
    assert "unsupported_emotion" in result["fallbackReason"]


def test_tone_style_and_intensity_unsupported_are_best_effort():
    result = expressive_tts.resolve_expression(
        segment=_segment("neutral", 0.9), speaker_id="char_x", voice_id="voice-1",
        capabilities=_cap(emotion=False), profiles=[],
    )
    assert result["appliedExpression"]["emotion"] == "neutral"
    assert result["appliedExpression"]["intensity"] is None
    assert result["appliedExpression"]["tone"] is None
    assert "unsupported_style" in result["fallbackReason"]


def test_strict_missing_emotion_fails():
    with pytest.raises(expressive_tts.ExpressiveResolutionError):
        expressive_tts.resolve_expression(
            segment=_segment(), speaker_id="char_x", voice_id="voice-1",
            capabilities=_cap(emotion=False), profiles=[], policy=c.EMOTION_POLICY_STRICT,
        )


def test_profile_identity_and_version_are_stable_inputs():
    profile = {"profile_id": "profile-angry-v2", "speaker_id": "char_x", "voice_id": "voice-1",
               "emotion": "angry", "version": 2, "status": "active", "settings_json": "{}"}
    result = expressive_tts.resolve_expression(
        segment=_segment(), speaker_id="char_x", voice_id="voice-1",
        capabilities=_cap(emotion=True), profiles=[profile],
    )
    assert result["profile"]["profileId"] == "profile-angry-v2"
    assert result["profile"]["version"] == 2

    provider = {"id": 1, "config_version": 1, "adapter_key": "generic_http", "capabilities_hash": "h1"}
    key_v1 = ag.compute_generation_key(
        book={"id": 1, "audio_settings_version": 1}, chapter={"chapter_key": "ch"}, mode="multi",
        voice_id="voice-1", source_text_hash="hash", analysis_id=1, tts_provider=provider,
        emotion_policy="best_effort", profile_id="profile-angry-v1", profile_version=1,
    )
    key_v2 = ag.compute_generation_key(
        book={"id": 1, "audio_settings_version": 1}, chapter={"chapter_key": "ch"}, mode="multi",
        voice_id="voice-1", source_text_hash="hash", analysis_id=1, tts_provider=provider,
        emotion_policy="best_effort", profile_id="profile-angry-v2", profile_version=2,
    )
    assert key_v1 != key_v2


def test_preview_and_generation_share_resolver():
    kwargs = {"speaker_id": "char_x", "voice_id": "voice-1", "emotion_value": "angry",
              "intensity_value": 0.8, "tone": "命令", "speaking_style": "短促",
              "capabilities": _cap(emotion=False), "profiles": []}
    preview = expressive_tts.resolve_preview(**kwargs)
    generation = expressive_tts.resolve_expression(
        segment=_segment(), speaker_id="char_x", voice_id="voice-1",
        capabilities=kwargs["capabilities"], profiles=[],
    )
    assert preview["requestedExpression"] == generation["requestedExpression"]
    assert preview["appliedExpression"] == generation["appliedExpression"]
    assert preview["fallbackReason"] == generation["fallbackReason"]


def test_profile_conflict_uses_base_voice_but_voice_conflict_blocks():
    result = expressive_tts.resolve_expression(
        segment=_segment(), speaker_id="char_x", voice_id="voice-1",
        capabilities=_cap(emotion=True), profiles=[], profile_conflict=True,
    )
    assert result["appliedExpression"]["emotion"] == "neutral"
    with pytest.raises(expressive_tts.ExpressiveResolutionError):
        expressive_tts.resolve_expression(
            segment=_segment(), speaker_id="char_x", voice_id="voice-1",
            capabilities=_cap(emotion=True), profiles=[], voice_conflict=True,
        )


def test_analysis_rejects_provider_specific_expression_fields():
    artifact = {"schemaVersion": 3, "chapterKey": "ch", "sourceTextHash": "hash",
                "sourceLength": 1, "characters": [],
                "segments": [{"segment_id": "s", "type": "narration", "text": "a",
                               "speaker_id": c.SPEAKER_ID_NARRATOR, "source_start": 0, "source_end": 1,
                               "emotion": {"label": "neutral", "intensity": 0.5},
                               "provider_native": {"prompt": "no"}}]}
    with pytest.raises(analysis.AnalysisValidationError):
        analysis.validate_artifact(artifact)
