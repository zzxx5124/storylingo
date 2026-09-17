"""V4 Emotion-to-TTS application。

- 把 canonical segment emotion 對映到 adapter-specific 參數（當 provider 支援時）。
- `best_effort` fallback：不支援時降級為 neutral，不使整章生成失敗。
- 只記錄非機密的 application summary metadata（無 provider request body / secret）。
"""
from .. import v4_contracts as c
from ..services import tts_adapter


class EmotionApplicationError(ValueError):
    """emotion 應用過程的錯誤（不應使生成失敗，best-effort 會降級）。"""


def canonical_emotion_value(segment: dict) -> str:
    """取出 segment 的 canonical emotion value，缺省視為 neutral。"""
    emotion = segment.get("emotion")
    if isinstance(emotion, dict):
        value = emotion.get("value") or emotion.get("id")
        if value in c.CANONICAL_EMOTIONS:
            return value
    return c.EMOTION_NEUTRAL


def emotion_intensity(segment: dict, default: float = c.EXPRESSIVE_INTENSITY_DEFAULT) -> float:
    emotion = segment.get("emotion")
    if isinstance(emotion, dict):
        try:
            v = float(emotion.get("intensity", default))
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, v))
    return default


def emotion_source(segment: dict) -> str:
    emotion = segment.get("emotion")
    if isinstance(emotion, dict):
        src = emotion.get("source")
        if src in c.EMOTION_SOURCES:
            return src
    return c.EMOTION_SOURCE_FALLBACK


def map_emotion_to_adapter(value: str, capabilities: dict, adapter_key: str = "generic_http") -> dict | None:
    """把 canonical emotion 對映到 adapter-specific 參數。

    回傳 None 表示 provider 不支援 emotion（best-effort 時走 neutral fallback）。
    回傳 dict 為 adapter 應使用的參數（例如 native emotion label）。
    """
    value = value if value in c.CANONICAL_EMOTIONS else c.EMOTION_NEUTRAL
    support = tts_adapter.emotion_support({"capabilities_json": __import__("json").dumps(capabilities)}) \
        if capabilities else {"status": tts_adapter.CAP_UNSUPPORTED, "labels": [], "controlMode": "none"}
    if support["status"] != tts_adapter.CAP_SUPPORTED:
        return None
    labels = support.get("labels", [])
    control_mode = support.get("controlMode", "native_emotion")
    # provider-native label：依 canonical → native 對映；找不到就以 canonical 名稱嘗試
    if labels:
        native = _canonical_to_native(value, labels)
        if native:
            return {"emotion": native, "controlMode": control_mode}
    return None


def _canonical_to_native(value: str, labels: list[str]) -> str | None:
    """嘗試把 canonical value 對映到 provider-native label。"""
    lowered = [str(l).lower() for l in labels]
    if value.lower() in lowered:
        return labels[lowered.index(value.lower())]
    # 常見同義對映（provider 常以 joy/sorrow/anger 命名）
    synonym_map = {
        "happy": ["joy", "joyful", "cheerful"],
        "sad": ["sorrow", "sadness"],
        "angry": ["anger"],
        "afraid": ["fear", "fearful"],
        "surprised": ["surprise"],
        "calm": ["peaceful", "calmness"],
        "excited": ["excitement", "excited"],
        "tender": ["gentle", "tenderness"],
        "tense": ["tension", "tensed"],
    }
    for candidate in synonym_map.get(value, []):
        if candidate in lowered:
            return labels[lowered.index(candidate)]
    return None


def apply_emotion(segments: list[dict], capabilities: dict, adapter_key: str = "generic_http") -> dict:
    """對整章 segments 套用 emotion（best-effort）。

    回傳 {applicationSummary, appliedSegments}。
    applicationSummary 只含非機密 metadata。
    """
    applied = []
    supported_count = 0
    fallback_count = 0
    for seg in segments:
        raw_value = None
        emotion_obj = seg.get("emotion")
        if isinstance(emotion_obj, dict):
            raw_value = emotion_obj.get("value") or emotion_obj.get("id")
        value = canonical_emotion_value(seg)
        intensity = emotion_intensity(seg)
        source = emotion_source(seg)
        params = None
        application = "fallback"
        fallback_reason = None
        if value != c.EMOTION_NEUTRAL:
            params = map_emotion_to_adapter(value, capabilities, adapter_key)
            if params:
                application = "native"
                supported_count += 1
            else:
                fallback_count += 1
                fallback_reason = "unsupported_emotion"
        elif raw_value and raw_value != c.EMOTION_NEUTRAL:
            # 原始 emotion 值不合法（未知）→ 已降級為 neutral，標記 fallback
            fallback_count += 1
            fallback_reason = "unknown_emotion"
        else:
            application = "native" if capabilities and _supports_neutral(capabilities) else "none"
        applied.append({
            "id": seg.get("id"),
            "emotion": value,
            "requestedExpression": {"emotion": raw_value or value, "intensity": intensity},
            "appliedExpression": {"emotion": value, "intensity": intensity if params or value == c.EMOTION_NEUTRAL else None},
            "intensity": intensity,
            "source": source,
            "adapterParams": params,
            "application": application,
            "fallbackReason": fallback_reason,
        })
    summary = {
        "requestedCount": len(applied),
        "supportedCount": supported_count,
        "fallbackCount": fallback_count,
        "application": "best_effort",
        "emotionPolicy": c.EMOTION_POLICY_BEST_EFFORT,
        "requestedExpression": [item["requestedExpression"] for item in applied],
        "appliedExpression": [item["appliedExpression"] for item in applied],
        "applied": applied,
    }
    return {"applicationSummary": summary, "appliedSegments": applied}


def _supports_neutral(capabilities: dict) -> bool:
    support = tts_adapter.emotion_support({"capabilities_json": __import__("json").dumps(capabilities)}) \
        if capabilities else {"status": tts_adapter.CAP_UNSUPPORTED}
    return support["status"] == tts_adapter.CAP_SUPPORTED
