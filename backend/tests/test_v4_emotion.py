"""V4 Phase 9：Emotion-to-TTS application regression tests.

驗證：
- Supported emotion 依設計改變 adapter request。
- Unsupported / unknown emotion 在 best-effort 下降級，不使整章生成失敗。
- Single-mode neutral/default 路徑維持不變。
- 不把 provider-native secret／request body 寫入 generation metadata。
"""
import json

from backend import v4_contracts as c
from backend.services import emotion
from backend.services import tts_adapter


def _supported_cap(adapter_key="generic_http"):
    return tts_adapter.canonicalize_capabilities(
        {"schemaVersion": 1, "emotion": {"supported": True, "controlMode": "native_emotion",
                                         "labels": ["neutral", "joy", "sorrow", "anger", "calm"]}},
        adapter_key)


def _unsupported_cap():
    return tts_adapter.canonicalize_capabilities({"emotion": {"supported": False}}, "generic_http")


def _segments(*values):
    segs = []
    for i, v in enumerate(values):
        if v is None:
            segs.append({"id": f"s{i}", "text": "x"})
        else:
            segs.append({"id": f"s{i}", "text": "x", "emotion": {"value": v, "intensity": 0.7, "source": "ai"}})
    return segs


def test_supported_emotion_maps_to_adapter_params():
    cap = _supported_cap()
    params = emotion.map_emotion_to_adapter("happy", cap, "generic_http")
    assert params is not None
    # provider-native label "joy" 對應 canonical happy（同義對映）
    assert params["emotion"] == "joy"
    params2 = emotion.map_emotion_to_adapter("sad", cap, "generic_http")
    assert params2["emotion"] == "sorrow"


def test_unsupported_emotion_returns_none_best_effort():
    cap = _unsupported_cap()
    assert emotion.map_emotion_to_adapter("happy", cap, "generic_http") is None
    result = emotion.apply_emotion(_segments("happy", None), cap, "generic_http")
    summary = result["applicationSummary"]
    assert summary["fallbackCount"] >= 1
    # 不使整章失敗：仍回傳完整 summary
    assert summary["application"] == "best_effort"


def test_neutral_emotion_uses_native_or_none_not_fallback():
    cap = _supported_cap()
    result = emotion.apply_emotion(_segments(None), cap, "generic_http")
    assert result["appliedSegments"][0]["emotion"] == "neutral"
    assert result["appliedSegments"][0]["application"] in ("native", "none")


def test_unknown_emotion_falls_back_to_neutral():
    cap = _supported_cap()
    # 未知 emotion 在 apply 層被降級為 neutral 並標記 fallback
    result = emotion.apply_emotion(_segments("banana"), cap, "generic_http")
    assert result["appliedSegments"][0]["emotion"] == "neutral"
    assert result["appliedSegments"][0]["application"] == "fallback"
    assert result["appliedSegments"][0]["fallbackReason"] == "unknown_emotion"


def test_no_provider_secret_or_request_body_in_metadata():
    cap = _supported_cap()
    segs = _segments("happy", "sad", "angry", None)
    result = emotion.apply_emotion(segs, cap, "generic_http")
    s = json.dumps(result, ensure_ascii=False)
    assert "sk-" not in s
    assert "apiKey" not in s
    assert "Authorization" not in s
    assert "requestBody" not in s
    # metadata 不含 provider secret 或完整 request body
    assert "secret" not in s.lower()


def test_single_mode_neutral_path_unchanged():
    # single mode：無 analysis emotion → neutral，adapterParams 為 None（不改變 request）
    cap = _supported_cap()
    params = emotion.map_emotion_to_adapter("neutral", cap, "generic_http")
    # neutral 不產生額外 adapter emotion 參數（維持 default path）
    result = emotion.apply_emotion(_segments(None), cap, "generic_http")
    assert result["appliedSegments"][0]["adapterParams"] is None


def test_emotion_application_summary_shape():
    cap = _supported_cap()
    result = emotion.apply_emotion(_segments("happy", "sad", None), cap, "generic_http")
    summary = result["applicationSummary"]
    assert summary["requestedCount"] == 3
    assert "supportedCount" in summary
    assert "fallbackCount" in summary
    assert summary["emotionPolicy"] == c.EMOTION_POLICY_BEST_EFFORT
