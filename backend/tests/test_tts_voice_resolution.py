import json

import pytest

from backend import tts
from backend.services import audio_generation


def _book(**overrides):
    book = {
        "id": 1,
        "category": "zh",
        "categories": ["zh"],
        "voices": {"旁白": "narrator-voice", "甲": "character-voice"},
        "settings": {},
    }
    book.update(overrides)
    return book


def test_unresolved_speaker_uses_canonical_narrator_voice():
    jobs = tts._build_jobs(_book(), {
        "segments": [{
            "type": "dialogue",
            "speaker": "未解析語者",
            "speaker_id": "speaker:unresolved",
            "text": "有人敲了敲門。",
        }],
    })

    assert jobs[0][2] == "narrator-voice"


def test_narration_uses_canonical_narrator_voice():
    jobs = tts._build_jobs(_book(), {
        "segments": [{
            "type": "narration",
            "speaker": "旁白",
            "speaker_id": "speaker:narrator",
            "text": "夜色降臨。",
        }],
    })

    assert jobs[0][2] == "narrator-voice"


def test_missing_narrator_mapping_does_not_use_retired_edge_voice():
    jobs = tts._build_jobs(_book(voices={}), {
        "segments": [{
            "type": "narration",
            "speaker": "旁白",
            "speaker_id": "speaker:narrator",
            "text": "夜色降臨。",
        }],
    })

    assert jobs[0][2] is None
    assert "Neural" not in str(jobs[0][2])


def test_missing_voice_error_includes_role_and_segment_count():
    message = tts.format_missing_voice_error(
        _book(voices={}),
        {"segments": [{
            "type": "dialogue",
            "speaker": "未解析語者",
            "speaker_id": "speaker:unresolved",
            "text": "有人敲了敲門。",
        }]},
        {"other-voice"},
    )

    assert "未設定的旁白聲線" in message
    assert "unresolved fallback" in message
    assert "影響1段" in message
    assert "zh-CN-XiaoxiaoNeural" not in message


def test_speed_factor_is_conservatively_bounded():
    assert tts._bounded_speed_factor(2.0, 10.0) == 0.85
    assert tts._bounded_speed_factor(10.0, 2.0) == 1.30
    assert tts._bounded_speed_factor(5.0, 5.0) == 1.0


def test_pacing_model_does_not_slow_short_or_over_speed_long_chinese_segments():
    short = tts._expected_duration("你好。", is_en=False)
    long = tts._expected_duration("你" * 100, is_en=False)

    assert short < 1.0
    assert 0.21 <= long / 100 <= 0.23


def test_match_duration_applies_only_bounded_time_stretch(monkeypatch):
    calls = []

    def fake_ffmpeg(args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(tts, "_probe_duration", lambda _path: 0.64)
    monkeypatch.setattr(tts.subprocess, "run", fake_ffmpeg)
    monkeypatch.setattr(tts.os, "replace", lambda _source, _target: None)

    assert tts._match_duration("segment.wav", 1.2) == pytest.approx(0.64 / 0.85)
    assert len(calls) == 1
    assert "atempo=0.8500" in calls[0]


def test_match_duration_skips_negligible_difference(monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "_probe_duration", lambda _path: 1.0)
    monkeypatch.setattr(tts.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    assert tts._match_duration("segment.wav", 1.02) == 1.0
    assert calls == []


def test_chapter_timing_uses_actual_provider_duration_without_speed_stretch(monkeypatch, tmp_path):
    jobs = [("00000.mp3", "短句。", "voice-1", 0, False, {}, "seg-1")]
    ffmpeg_calls = []

    async def fake_synth(text, voice, out, sem, expression=None, segment_id=None, language="zh-TW"):
        with open(out, "wb") as handle:
            handle.write(b"provider-audio")

    def fake_normalize(_mp3, wav):
        with open(wav, "wb") as handle:
            handle.write(b"normalized-audio")

    def fake_run(args, **kwargs):
        ffmpeg_calls.append(args)
        if "-f" in args and "concat" in args:
            output = args[-1]
            with open(output, "wb") as handle:
                handle.write(b"joined-audio")

    monkeypatch.setattr(tts, "_build_jobs", lambda _book, _analysis: jobs)
    monkeypatch.setattr(tts, "_synth", fake_synth)
    monkeypatch.setattr(tts, "_normalize_loudness", fake_normalize)
    monkeypatch.setattr(tts, "_probe_duration", lambda _path: 2.75)
    monkeypatch.setattr(tts, "_apply_boundary_fade", lambda *args, **kwargs: None)
    monkeypatch.setattr(tts, "subprocess", type("Subprocess", (), {"run": staticmethod(fake_run)}))
    monkeypatch.setattr(tts.db, "get_active_tts_provider", lambda: None)

    audio_path = tmp_path / "out.mp3"
    timing_path = tmp_path / "out.timing.json"
    tts.generate_chapter_audio(
        {"id": "book-1", "voices": {"旁白": "voice-1"}},
        {"seq": 0}, {"segments": [{"text": "短句。", "speaker": "旁白"}]},
        final_audio_path=str(audio_path), final_timing_path=str(timing_path),
    )

    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert timing["segments"] == [{"dur": 2.75}]
    assert not any("atempo=" in " ".join(map(str, call)) for call in ffmpeg_calls)


def test_render_profile_version_invalidates_generation_key(monkeypatch):
    args = {
        "book": _book(),
        "chapter": {"id": 2, "chapter_key": "ch-1"},
        "mode": "multi",
        "voice_id": "narrator-voice",
        "source_text_hash": "source-hash",
        "analysis_id": 1,
        "tts_provider": {"id": 1, "config_version": 1, "adapter_key": "test"},
        "emotion_policy": "best_effort",
    }
    original = audio_generation.compute_generation_key(**args)
    monkeypatch.setattr(audio_generation, "RENDER_PROFILE_VERSION", "tts-render-test")

    assert audio_generation.compute_generation_key(**args) != original
