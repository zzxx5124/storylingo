"""Deterministic TTS unit cache and platform throughput guards."""
import os

from backend import settings
from backend.services import tts_partials
from backend import tts


def _provider(**overrides):
    value = {
        "id": 7, "config_version": 2, "adapter_key": "cosyvoice_http",
        "adapter_version": "v1", "capabilities_hash": "cap-a", "enabled": True,
    }
    value.update(overrides)
    return value


def test_tts_partial_identity_excludes_generation_but_changes_on_inputs():
    args = {
        "source_text_hash": "source-a", "segment_id": "seg-1", "text": "你好。",
        "voice_id": "voice-a", "language": "zh-TW", "expression": {"emotion": "neutral"},
        "provider": _provider(), "unit_version": tts.TTS_UNIT_VERSION,
    }
    first = tts_partials.cache_key(**args)
    assert first == tts_partials.cache_key(**args)
    assert first != tts_partials.cache_key(**{**args, "source_text_hash": "source-b"})
    assert first != tts_partials.cache_key(**{**args, "voice_id": "voice-b"})
    assert first != tts_partials.cache_key(**{**args, "provider": _provider(config_version=3)})


def test_tts_partial_publish_is_atomic_and_reusable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ROOT_DIR", str(tmp_path))
    source = tmp_path / "unit.mp3"
    source.write_bytes(b"ID3-audio")
    key = "a" * 64
    tts_partials.publish(key, str(source))
    destination = tmp_path / "out.mp3"
    assert tts_partials.copy_if_valid(key, str(destination)) is True
    assert destination.read_bytes() == b"ID3-audio"
    assert not list((tmp_path / "storage" / "tts_partials").glob(".*.mp3"))


def test_large_synthesis_unit_is_split_without_reordering():
    text = "第一句。第二句。" * 400
    jobs = [("00000.mp3", text, "voice", 0, False, {"emotion": "neutral"}, "seg-1")]
    expanded = tts._expand_synthesis_jobs(jobs)
    assert len(expanded) > 1
    assert "".join(item[1] for item in expanded) == text
    assert all(len(item[1]) <= tts.TTS_SYNTHESIS_UNIT_MAX_CHARS for item in expanded)
    assert all(item[3] == 0 for item in expanded)


def test_unknown_provider_capacity_is_serial_and_declared_capacity_is_bounded(monkeypatch):
    assert tts._provider_concurrency({"enabled": True, "capabilities_json": "{}"}) == 1
    monkeypatch.setattr(
        "backend.services.tts_adapter.load_capabilities",
        lambda _provider: {"limits": {"maxConcurrent": 99}},
    )
    assert tts._provider_concurrency({"enabled": True}) == tts.MAX_CONCURRENT


def test_generation_reuses_completed_units_on_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ROOT_DIR", str(tmp_path))
    provider = _provider()
    jobs = [
        ("00000.mp3", "第一句。", "voice", 0, False, {"emotion": "neutral"}, "seg-1"),
        ("00001.mp3", "第二句。", "voice", 1, False, {"emotion": "neutral"}, "seg-2"),
    ]
    calls = []

    async def fake_synth(text, voice, out, sem, expression=None, segment_id=None, language="zh-TW"):
        calls.append(segment_id)
        with open(out, "wb") as handle:
            handle.write(b"ID3-audio")

    def fake_normalize(mp3, wav):
        with open(mp3, "rb") as source, open(wav, "wb") as target:
            target.write(source.read())

    def fake_probe(_path):
        return 1.0

    def fake_run(args, **kwargs):
        if "-f" in args and "lavfi" in args:
            output = args[-1]
            open(output, "wb").write(b"silence")
        elif "-f" in args and "concat" in args:
            output = args[-1]
            open(output, "wb").write(b"joined")

    monkeypatch.setattr(tts, "_build_jobs", lambda _book, _analysis: jobs)
    monkeypatch.setattr(tts, "_synth", fake_synth)
    monkeypatch.setattr(tts, "_normalize_loudness", fake_normalize)
    monkeypatch.setattr(tts, "_probe_duration", fake_probe)
    monkeypatch.setattr(tts, "_apply_boundary_fade", lambda *args, **kwargs: None)
    monkeypatch.setattr(tts, "subprocess", type("Subprocess", (), {"run": staticmethod(fake_run)}))
    monkeypatch.setattr(tts.db, "get_active_tts_provider", lambda: provider)
    monkeypatch.setattr(tts.db, "list_tts_provider_voices", lambda _provider_id: [{"voice_id": "voice"}])

    book = {"id": "book-1", "voices": {"旁白": "voice"}}
    chapter = {"seq": 0, "text_hash": "source-1"}
    analysis = {"segments": [{"text": "第一句。"}, {"text": "第二句。"}]}
    tts.generate_chapter_audio(
        book, chapter, analysis,
        final_audio_path=str(tmp_path / "first.mp3"),
        final_timing_path=str(tmp_path / "first.json"),
    )
    tts.generate_chapter_audio(
        book, chapter, analysis,
        final_audio_path=str(tmp_path / "second.mp3"),
        final_timing_path=str(tmp_path / "second.json"),
    )

    assert calls == ["seg-1", "seg-2"]
    assert (tmp_path / "second.mp3").read_bytes() == b"joined"
