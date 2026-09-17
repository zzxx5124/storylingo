"""Provider API v1 adapter contract tests; live cases require injected env only."""
import json
import os
from pathlib import Path

import pytest

from backend.services import tts_provider_v1
from backend.services import tts_adapter
from backend.services import audio_generation
from backend import db
from backend.tests.conftest import reg


class _Response:
    def __init__(self, status=200, body=b"audio", headers=None, payload=None):
        self.status_code = status
        self.content = body
        self.headers = headers or {"content-type": "audio/mpeg", "X-TTS-API-Version": "1", "X-Request-ID": "rid-1",
                                   "X-Applied-Expression": "neutral"}
        self._payload = payload

    def json(self):
        if self._payload is not None:
            return self._payload
        return json.loads(self.content)


class _Client:
    response = _Response()
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def request(self, *args, **kwargs):
        return self.response


def _row():
    return {"base_url": "http://127.0.0.1:8100", "secret_ciphertext": "", "auth_scheme": "none"}


def _author_book(client):
    response = reg(client, "profileauthor", password="secret123")
    user = db.get_user_by_name("profileauthor")
    db.update_user_role(user["id"], "author")
    login = client.post("/api/auth/login", json={"username": "profileauthor", "password": "secret123"})
    assert login.status_code == 200, login.text
    response = client.post("/api/books/manual", json={"title": "Profile Book", "category": "vocab"})
    assert response.status_code == 200, response.text
    return response.json()


def test_binary_metadata_and_request_id(monkeypatch, tmp_path):
    monkeypatch.setattr(tts_provider_v1.httpx, "Client", _Client)
    out = tmp_path / "preview.mp3"
    metadata = tts_provider_v1.synthesize_to_file(_row(), {"request_id": "rid-1", "text": "hello", "voice_id": "v", "language": "en-US", "emotion": "angry"}, str(out))
    assert out.read_bytes() == b"audio"
    assert metadata["requestId"] == "rid-1"
    assert metadata["appliedExpression"] == "neutral"


def test_request_builder_rejects_private_fields_and_limits():
    with pytest.raises(tts_provider_v1.ProviderV1Error) as private:
        tts_provider_v1.synthesize_to_file(_row(), {"text": "x", "voice_id": "v", "language": "en-US",
            "speaker_id": "char:1"}, "unused.mp3")
    assert private.value.code == "invalid_request"
    with pytest.raises(tts_provider_v1.ProviderV1Error) as oversized:
        tts_provider_v1.build_request(text="x" * (tts_provider_v1.MAX_SYNTHESIS_TEXT + 1),
            voice_id="v", language="en-US")
    assert oversized.value.status == 413


def test_invalid_audio_and_request_id_are_rejected(monkeypatch, tmp_path):
    class Invalid(_Client):
        response = _Response(body=b"{}", headers={"content-type": "application/json",
            "X-TTS-API-Version": "1", "X-Request-ID": "rid-1"})
    monkeypatch.setattr(tts_provider_v1.httpx, "Client", Invalid)
    with pytest.raises(tts_provider_v1.ProviderV1Error) as invalid:
        tts_provider_v1.synthesize_to_file(_row(), {"request_id": "rid-1", "text": "x", "voice_id": "v", "language": "en-US"}, str(tmp_path / "x"))
    assert invalid.value.code == "invalid_response"
    class MissingID(_Client):
        response = _Response(headers={"content-type": "audio/mpeg", "X-TTS-API-Version": "1"})
    monkeypatch.setattr(tts_provider_v1.httpx, "Client", MissingID)
    with pytest.raises(tts_provider_v1.ProviderV1Error) as missing:
        tts_provider_v1.synthesize_to_file(_row(), {"request_id": "rid-1", "text": "x", "voice_id": "v", "language": "en-US"}, str(tmp_path / "y"))
    assert missing.value.code == "invalid_response"


def test_machine_error_and_retryable(monkeypatch):
    class Busy(_Client):
        response = _Response(status=429, payload={"error": {"code": "provider_busy", "message": "busy", "retryable": True}},
                             headers={"X-Request-ID": "busy-1"})
    monkeypatch.setattr(tts_provider_v1.httpx, "Client", Busy)
    with pytest.raises(tts_provider_v1.ProviderV1Error) as caught:
        tts_provider_v1.synthesize_to_file(_row(), {"request_id": "r", "text": "hello", "voice_id": "v", "language": "en-US"}, "unused.mp3")
    assert caught.value.code == "provider_busy"
    assert caught.value.retryable is True
    assert caught.value.request_id == "busy-1"


def test_retry_is_bounded_and_error_is_redacted(monkeypatch):
    class Busy(_Client):
        attempts = 0
        response = _Response(status=429, payload={"error": {
            "code": "provider_busy", "message": "key=credential-test-value path=C:\\models\\voice.bin",
            "retryable": True}}, headers={"X-Request-ID": "busy-2"})

        def request(self, *args, **kwargs):
            type(self).attempts += 1
            return self.response

    monkeypatch.setattr(tts_provider_v1.httpx, "Client", Busy)
    monkeypatch.setattr(tts_provider_v1.time, "sleep", lambda _: None)
    with pytest.raises(tts_provider_v1.ProviderV1Error) as caught:
        tts_provider_v1.synthesize_to_file(_row(), {"request_id": "r", "text": "hello",
            "voice_id": "v", "language": "en-US"}, "unused.mp3")
    assert Busy.attempts == tts_provider_v1.MAX_RETRIES + 1
    assert "credential-test-value" not in str(caught.value)
    assert "models" not in str(caught.value)
    assert "credential-test-value" not in tts_adapter.normalize_error(RuntimeError("key=credential-test-value"))


def test_retry_after_is_preserved_and_used(monkeypatch, tmp_path):
    class RetryAfterClient(_Client):
        attempts = 0

        def request(self, *args, **kwargs):
            type(self).attempts += 1
            if type(self).attempts == 1:
                return _Response(status=429, payload={"error": {
                    "code": "queue_full", "message": "busy", "retryable": True}},
                    headers={"X-Request-ID": "r", "Retry-After": "15"})
            return _Response(headers={"content-type": "audio/mpeg", "X-TTS-API-Version": "1",
                                      "X-Request-ID": "r"})

    delays = []
    monkeypatch.setattr(tts_provider_v1.httpx, "Client", RetryAfterClient)
    monkeypatch.setattr(tts_provider_v1.time, "sleep", delays.append)
    result = tts_provider_v1.synthesize_to_file(
        _row(), {"request_id": "r", "text": "hello", "voice_id": "v", "language": "en-US"},
        str(tmp_path / "retry-after.mp3"))
    assert result["requestId"] == "r"
    assert RetryAfterClient.attempts == 2
    assert delays == [15.0]


@pytest.mark.parametrize("code,status", [("model_unavailable", 503), ("timeout", 504)])
def test_runtime_errors_are_retryable(monkeypatch, code, status):
    class RuntimeFailure(_Client):
        response = _Response(status=status, payload={"error": {"code": code, "retryable": True}},
                             headers={"X-Request-ID": f"{code}-1"})
    monkeypatch.setattr(tts_provider_v1.httpx, "Client", RuntimeFailure)
    with pytest.raises(tts_provider_v1.ProviderV1Error) as caught:
        tts_provider_v1.synthesize_to_file(_row(), {"request_id": "r", "text": "hello", "voice_id": "v", "language": "en-US"}, "unused.mp3")
    assert caught.value.code == code
    assert caught.value.retryable is True


def test_provider_busy_error_is_machine_readable_and_productized():
    error = tts_provider_v1.ProviderV1Error(
        "busy", code="provider_busy", status=429, retryable=True)
    envelope = json.loads(tts_adapter.error_envelope(error))
    assert envelope == {
        "code": "provider_busy", "message": "語音服務忙碌，請稍後重試",
        "retryable": True, "status": 429,
    }


def test_queue_full_error_is_machine_readable_and_productized():
    error = tts_provider_v1.ProviderV1Error(
        "full", code="queue_full", status=429, retryable=True, retry_after=15)
    envelope = json.loads(tts_adapter.error_envelope(error))
    assert envelope == {
        "code": "queue_full", "message": "語音服務佇列已滿，請稍後重試",
        "retryable": True, "status": 429,
    }


def test_provider_v1_normalizes_voice_capabilities():
    cap = tts_provider_v1.normalize_capabilities(
        {"supports_emotion": False, "voice_capabilities": {
            "af_heart": {"supported_emotions": ["neutral"]},
            "cosy_mimi": {"supported_emotions": ["neutral", "happy", "sad", "angry", "tense"]},
        }}, [])
    assert cap["emotion"]["supported"] is True
    assert cap["emotion"]["labels"] == ["neutral", "happy", "sad", "angry", "tense"]


def test_provider_v1_normalizes_probed_voice_capabilities():
    cap = tts_provider_v1.normalize_capabilities({
        "declared": {"supports_emotion": False, "supported_emotions": ["neutral"]},
        "probed": {"voice_capabilities": {
            "af_heart": {"supports_emotion": False, "supported_emotions": ["neutral"]},
            "cosy_mimi": {"supports_emotion": True,
                          "supported_emotions": ["neutral", "happy", "sad", "angry", "tense"]},
        }},
    }, [])
    assert cap["emotion"]["supported"] is True
    assert cap["voiceCapabilities"]["cosy_mimi"]["supported_emotions"][-1] == "tense"


def test_provider_v1_normalizes_all_neutral_or_missing_capabilities():
    neutral = tts_provider_v1.normalize_capabilities({
        "declared": {"supports_emotion": False},
        "probed": {"voice_capabilities": {
            "af_heart": {"supports_emotion": False, "supported_emotions": ["neutral"]},
        }},
    }, [])
    missing = tts_provider_v1.normalize_capabilities({}, [])
    assert neutral["emotion"]["supported"] is False
    assert missing["emotion"]["supported"] is False


def test_catalog_refresh_preserves_disappeared_voice_as_unavailable():
    provider_id = db.create_tts_provider({"name": "v1 catalog", "base_url": "http://127.0.0.1:8100",
        "adapter_key": "cosyvoice_http", "auth_scheme": "none"})
    db.replace_tts_provider_voices(provider_id, [{"id": "voice_a", "name": "A"}, {"id": "voice_b", "name": "B"}])
    db.replace_tts_provider_voices(provider_id, [{"id": "voice_a", "name": "A2"}])
    rows = {row["voice_id"]: row for row in db.list_tts_provider_voices(provider_id, include_unavailable=True)}
    assert rows["voice_a"]["catalog_status"] == "available"
    assert rows["voice_b"]["catalog_status"] == "unavailable"
    assert db.list_tts_provider_voices(provider_id)[0]["voice_id"] == "voice_a"


def test_expression_versions_invalidate_generation_key():
    book = {"id": 1, "audio_settings_version": 1}
    chapter = {"chapter_key": "ch-1"}
    provider = {"id": 1, "config_version": 1, "adapter_key": "cosyvoice_http", "capabilities_hash": "cap-1"}
    common = {"book": book, "chapter": chapter, "mode": "multi", "voice_id": "cosy_mimi",
              "source_text_hash": "src", "analysis_id": 1, "tts_provider": provider, "emotion_policy": "best_effort"}
    first = audio_generation.compute_generation_key(**common, profile_id="p1", profile_version=1,
                                                   capability_snapshot_version=1, expressive_snapshot_hash="e1")
    second = audio_generation.compute_generation_key(**common, profile_id="p1", profile_version=2,
                                                    capability_snapshot_version=2, expressive_snapshot_hash="e2")
    assert first != second


def test_author_profile_crud_version_and_native_payload_boundary(client):
    provider_id = db.create_tts_provider({"name": "profile provider", "base_url": "http://127.0.0.1:8100",
        "adapter_key": "cosyvoice_http", "auth_scheme": "none"})
    db.replace_tts_provider_voices(provider_id, [{"id": "voice_a", "name": "A"}])
    book = _author_book(client)
    bid = book["id"]
    created = client.post(f"/api/books/{bid}/expressive-profiles", json={
        "speakerId": "char:001", "voiceId": "voice_a", "emotion": "angry",
        "settings": {"tone": "forceful"},
    })
    assert created.status_code == 200, created.text
    profile_id = created.json()["profile_id"]
    assert created.json()["version"] == 1
    assert created.json()["availability_status"] == "available"
    updated = client.put(f"/api/books/{bid}/expressive-profiles/{profile_id}", json={
        "settings": {"speaking_style": "sharp"},
    })
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    db.replace_tts_provider_voices(provider_id, [])
    listed = client.get(f"/api/books/{bid}/expressive-profiles")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["availability_status"] == "stale"
    rejected = client.put(f"/api/books/{bid}/expressive-profiles/{profile_id}", json={
        "settings": {"cosyvoice": {"prompt": "native"}},
    })
    assert rejected.status_code == 400
    archived = client.delete(f"/api/books/{bid}/expressive-profiles/{profile_id}")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


@pytest.mark.skipif(not os.getenv("UAT_TTS_API_KEY"), reason="live provider credential is injected at runtime")
def test_live_provider_v1_contract(tmp_path):
    """Real synthesis only; credential is read from runtime env and never persisted."""
    row = {"base_url": os.getenv("LIVE_TTS_BASE_URL", "http://127.0.0.1:8100"),
           "auth_scheme": "x-api-key", "secret_ciphertext": ""}
    from backend.services import tts_provider
    row["secret_ciphertext"] = tts_provider.encrypt_secret(os.environ["UAT_TTS_API_KEY"])
    result = tts_provider_v1.test_connection(row)
    assert result["ok"] is True
    voices = {voice.get("id") or voice.get("voice_id") for voice in result["voices"]}
    assert "af_heart" in voices
    assert "cosy_mimi" in voices
    for voice, emotion in [("af_heart", "neutral"), ("cosy_mimi", "neutral"), ("cosy_mimi", "happy"),
                           ("cosy_mimi", "sad"), ("cosy_mimi", "angry"), ("cosy_mimi", "tense")]:
        out = tmp_path / f"{voice}-{emotion}.mp3"
        language = "en-US" if voice == "af_heart" else "zh-TW"
        text = "This is a live synthesis test." if voice == "af_heart" else "這是一段即時合成測試。"
        meta = tts_provider_v1.synthesize_to_file(row, {"request_id": f"live-{voice}-{emotion}",
            "text": text, "voice_id": voice, "language": language,
            "emotion": emotion, "intensity": 0.5, "tone": "test", "speaking_style": "test",
            "mode": "best_effort"}, str(out))
        assert out.stat().st_size > 0
        assert meta["requestId"]
    preview_meta = tts_provider_v1.preview_to_file(row, {"request_id": "live-preview", "text": "preview",
        "voice_id": "af_heart", "language": "en-US", "emotion": "neutral", "mode": "best_effort"},
        str(tmp_path / "preview.mp3"))
    assert preview_meta["requestId"]
    fallback = tts_provider_v1.synthesize_to_file(row, {"request_id": "live-fallback", "text": "fallback",
        "voice_id": "af_heart", "language": "en-US", "emotion": "angry", "intensity": 0.9,
        "tone": "cold", "speaking_style": "sharp", "mode": "best_effort"}, str(tmp_path / "fallback.mp3"))
    assert fallback["appliedExpression"] == "neutral"
    assert fallback["fallbackReason"]
    assert "intensity" in fallback["ignoredParameters"] or fallback["fallbackReason"]
    with pytest.raises(tts_provider_v1.ProviderV1Error) as strict:
        tts_provider_v1.synthesize_to_file(row, {"request_id": "live-strict", "text": "strict",
            "voice_id": "af_heart", "language": "en-US", "emotion": "angry", "mode": "strict"},
            str(tmp_path / "strict.mp3"))
    assert strict.value.code == "unsupported_emotion"
    with pytest.raises(tts_provider_v1.ProviderV1Error) as missing:
        tts_provider_v1.synthesize_to_file(row, {"request_id": "live-missing", "text": "missing",
            "voice_id": "missing-voice", "language": "en-US", "emotion": "neutral"},
            str(tmp_path / "missing.mp3"))
    assert missing.value.code == "voice_not_found"


def test_long_v1_synthesis_splits_before_provider_limit(monkeypatch, tmp_path):
    calls = []

    def fake_request(row, method, path, **kwargs):
        calls.append(kwargs["json"])
        return _Response(body=b"ID3-part", headers={
            "content-type": "audio/mpeg",
            "X-Request-ID": kwargs["json"]["request_id"],
        })

    def fake_concat(parts, output_path, temp_dir):
        with open(output_path, "wb") as handle:
            handle.write(b"ID3-joined")

    monkeypatch.setattr(tts_provider_v1, "_request", fake_request)
    monkeypatch.setattr(tts_provider_v1, "_concat_audio_parts", fake_concat)
    row = {"base_url": "http://tts.example", "auth_scheme": "none", "secret_ciphertext": ""}
    request = {
        "request_id": "long-request", "text": ("這是一個測試句子。" * 3000),
        "voice_id": "cosy_mimi", "language": "zh-TW", "emotion": "neutral",
    }

    meta = tts_provider_v1.synthesize_to_file(row, request, str(tmp_path / "long.mp3"))

    assert len(calls) > 1
    assert all(len(item["text"]) <= tts_provider_v1.MAX_SYNTHESIS_TEXT for item in calls)
    assert "".join(item["text"] for item in calls) == request["text"]
    assert calls[0]["request_id"] == "long-request-part-0"
    assert calls[1]["request_id"] == "long-request-part-1"
    assert meta["chunkCount"] == len(calls)
    assert meta["providerRequestCount"] == len(calls)
    assert (tmp_path / "long.mp3").read_bytes() == b"ID3-joined"


def test_tts_splitter_prefers_punctuation_and_preserves_order():
    from backend.services.tts_text import split_text_for_synthesis

    text = "第一句很短。第二句包含對話：「你好嗎？」第三句繼續，還有補充內容。"
    chunks = split_text_for_synthesis(text, 12)

    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= 12 for chunk in chunks)
    assert chunks[0].endswith("。")
    assert any(chunk.endswith("？」") for chunk in chunks)


def test_tts_splitter_hard_splits_only_when_no_boundary_exists():
    from backend.services.tts_text import split_text_for_synthesis

    text = "沒有任何標點的長文字" * 4
    chunks = split_text_for_synthesis(text, 7)

    assert "".join(chunks) == text
    assert all(len(chunk) <= 7 for chunk in chunks)
