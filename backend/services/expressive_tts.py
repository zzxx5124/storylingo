"""Provider-neutral expressive TTS contract and resolver.

This module deliberately stops at the platform contract boundary. Provider-native
payloads remain owned by the selected TTS adapter; analysis artifacts only carry
canonical expression intent.
"""
import hashlib
import json

from .. import db, v4_contracts as c
from . import emotion, tts_adapter


class ExpressiveResolutionError(ValueError):
    """Strict expression or unsafe identity/profile resolution failure."""


def _stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def canonical_emotion(segment: dict) -> str:
    obj = segment.get("emotion") if isinstance(segment, dict) else None
    raw = obj.get("label") or obj.get("value") or obj.get("id") if isinstance(obj, dict) else None
    return raw if raw in c.CANONICAL_EMOTIONS else c.EMOTION_NEUTRAL


def intensity(segment: dict) -> float:
    obj = segment.get("emotion") if isinstance(segment, dict) else None
    raw = obj.get("intensity") if isinstance(obj, dict) else None
    try:
        value = c.EXPRESSIVE_INTENSITY_DEFAULT if raw is None else float(raw)
    except (TypeError, ValueError):
        value = c.EXPRESSIVE_INTENSITY_DEFAULT
    return max(c.EXPRESSIVE_INTENSITY_MIN, min(c.EXPRESSIVE_INTENSITY_MAX, value))


def validate_profile(profile: dict) -> dict:
    required = ("profile_id", "speaker_id", "voice_id", "emotion", "version", "status")
    missing = [key for key in required if profile.get(key) in (None, "")]
    if missing:
        raise ValueError("expressive profile 缺少欄位：" + ", ".join(missing))
    if profile["emotion"] not in c.CANONICAL_EMOTIONS:
        raise ValueError("不支援的 canonical emotion")
    if profile["status"] not in c.PROFILE_STATUSES:
        raise ValueError("不支援的 profile status")
    if int(profile["version"]) < 1:
        raise ValueError("profile version 必須為正整數")
    settings = profile.get("settings_json", {})
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except json.JSONDecodeError as error:
            raise ValueError("expressive profile settings 格式錯誤") from error
    if not isinstance(settings, dict):
        raise ValueError("expressive profile settings 格式錯誤")
    forbidden = {"reference_audio", "reference_audio_path", "reference_text", "embedding",
                 "provider_prompt", "cosyvoice", "native_payload", "model_path"}
    if forbidden.intersection(settings):
        raise ValueError("expressive profile 不得保存 provider-native 欄位")
    return profile


def profile_snapshot(profile: dict | None) -> dict:
    if not profile:
        return {}
    validate_profile(profile)
    settings = profile.get("settings_json", {})
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except json.JSONDecodeError:
            settings = {}
    # Keep only platform-neutral fields in generation metadata.
    return {
        "profileId": profile["profile_id"],
        "speakerId": profile["speaker_id"],
        "voiceId": profile["voice_id"],
        "emotion": profile["emotion"],
        "version": int(profile["version"]),
        "status": profile["status"],
        "availabilityStatus": profile.get("availability_status", "available"),
        "settings": {key: settings[key] for key in ("intensity", "tone", "speaking_style") if key in settings},
    }


def _profile_map(profiles: list[dict] | None) -> dict[tuple[str, str], dict]:
    result = {}
    for profile in profiles or []:
        validate_profile(profile)
        key = (profile["speaker_id"], profile["emotion"])
        # Highest version wins only among explicitly active profiles.
        if profile["status"] == c.PROFILE_STATUS_ACTIVE and profile.get("availability_status", "available") == "available" and (
            key not in result or int(profile["version"]) > int(result[key]["version"])
        ):
            result[key] = profile
    return result


def capability_snapshot(provider: dict | None, *, used: dict | None = None) -> dict:
    """Return declared/probed/used capability without treating 404 as health failure."""
    cap = tts_adapter.load_capabilities(provider or {}) if provider else {}
    declared = cap.get("declared") or cap
    probed = cap.get("probed") or cap
    return {
        "snapshotVersion": int((cap or {}).get("snapshotVersion", 1)),
        "providerId": (provider or {}).get("id"),
        "providerConfigVersion": (provider or {}).get("config_version"),
        "declared": declared,
        "probed": probed,
        "emotion": cap.get("emotion", {}),
        "voiceCapabilities": cap.get("voiceCapabilities") or cap.get("voice_capabilities") or {},
        "used": used or {},
        "hash": (provider or {}).get("capabilities_hash") or _hash({"declared": declared, "probed": probed}),
    }


def _supports(capabilities: dict, field: str, voice_id: str | None = None) -> bool:
    voice_caps = capabilities.get("voiceCapabilities") or capabilities.get("voice_capabilities") or {}
    voice = voice_caps.get(voice_id) if voice_id and isinstance(voice_caps, dict) else None
    if isinstance(voice, dict):
        if field == "emotion":
            return bool(voice.get("supported_emotions") or voice.get("supportedEmotions"))
        names = {"intensity": ("supports_intensity", "supportsIntensity"),
                 "style": ("supports_style", "supportsStyle")}.get(field, (field,))
        return any(bool(voice.get(name)) for name in names)
    emotion_cap = capabilities.get("emotion", {}) if isinstance(capabilities, dict) else {}
    if field == "emotion":
        return bool(emotion_cap.get("supported"))
    return bool(emotion_cap.get({"intensity": "supportsIntensity", "style": "supportsStyle"}.get(field, field), False))


def resolve_expression(*, segment: dict, speaker_id: str, voice_id: str | None,
                       profiles: list[dict] | None = None, capabilities: dict | None = None,
                       policy: str = c.EMOTION_POLICY_BEST_EFFORT,
                       voice_conflict: bool = False, profile_conflict: bool = False) -> dict:
    if policy not in c.EMOTION_POLICIES:
        raise ExpressiveResolutionError("不支援的 expressive policy")
    requested = canonical_emotion(segment)
    requested_intensity = intensity(segment)
    tone = segment.get("tone")
    style = segment.get("speaking_style")
    profile_map = _profile_map(profiles)
    requested_profile = profile_map.get((speaker_id, requested))
    neutral_profile = profile_map.get((speaker_id, c.EMOTION_NEUTRAL))
    reasons = []

    if voice_conflict or profile_conflict:
        if voice_conflict:
            reasons.append("voice_conflict")
        if profile_conflict:
            reasons.append("profile_conflict")
        if voice_conflict:
            # A conflicted base voice has no safe best-effort fallback.
            raise ExpressiveResolutionError("voice conflict 未解決")
        # A profile conflict may safely fall back to an explicitly resolved base voice.
        if policy == c.EMOTION_POLICY_STRICT:
            raise ExpressiveResolutionError("profile conflict 未解決")
        requested_profile = None
        neutral_profile = None

    applied = requested
    profile = requested_profile
    if requested != c.EMOTION_NEUTRAL and not requested_profile:
        reasons.append("missing_template")
        applied = c.EMOTION_NEUTRAL
        profile = neutral_profile
    cap = capabilities or {}
    if requested != c.EMOTION_NEUTRAL and not _supports(cap, "emotion", voice_id):
        reasons.append("unsupported_emotion")
        applied = c.EMOTION_NEUTRAL
        profile = neutral_profile
    if policy == c.EMOTION_POLICY_STRICT and (applied != requested or not voice_id):
        raise ExpressiveResolutionError("strict expressive request 無法滿足")
    if applied == c.EMOTION_NEUTRAL and not profile and not voice_id:
        if policy == c.EMOTION_POLICY_STRICT:
            raise ExpressiveResolutionError("strict expressive request 缺少 base voice")
        reasons.append("missing_base_voice")
    used = {"emotion": applied != c.EMOTION_NEUTRAL and _supports(cap, "emotion", voice_id)}
    if tone and not _supports(cap, "style", voice_id):
        reasons.append("unsupported_style")
        used["style"] = False
    else:
        used["style"] = bool(tone)
    if requested_intensity != c.EXPRESSIVE_INTENSITY_DEFAULT and not _supports(cap, "intensity", voice_id):
        reasons.append("unsupported_intensity")
        used["intensity"] = False
    else:
        used["intensity"] = True
    result = _result(segment, speaker_id, voice_id, requested, applied, requested_intensity,
                     tone, style, profile, reasons, capabilities, used)
    if applied != c.EMOTION_NEUTRAL and used.get("emotion"):
        # This is an adapter-neutral mapping hint only; the provider payload
        # remains owned by the adapter and never enters the analysis artifact.
        result["providerNative"] = emotion.map_emotion_to_adapter(applied, capabilities)
    return result


def _result(segment, speaker_id, voice_id, requested, applied, requested_intensity,
            tone, style, profile, reasons, capabilities, used=None):
    snapshot = profile_snapshot(profile)
    return {
        "contractVersion": c.EXPRESSIVE_CONTRACT_VERSION,
        "segmentId": segment.get("segment_id") or segment.get("id"),
        "speakerId": speaker_id,
        "voiceId": voice_id,
        "requestedExpression": {"emotion": requested, "intensity": requested_intensity, "tone": tone, "speakingStyle": style},
        "appliedExpression": {"emotion": applied, "intensity": requested_intensity if (used or {}).get("intensity", True) else None,
                              "tone": tone if (used or {}).get("style", bool(tone)) else None,
                              "speakingStyle": style if (used or {}).get("style", bool(style)) else None},
        "fallbackReason": ",".join(dict.fromkeys(reasons)) if reasons else None,
        "profile": snapshot,
        "capabilityUsed": used or {},
        "providerNative": None,
    }


def resolve_segments(segments: list[dict], *, speaker_voices: dict | None = None,
                     profiles: list[dict] | None = None, capabilities: dict | None = None,
                     policy: str = c.EMOTION_POLICY_BEST_EFFORT,
                     voice_conflicts: set[str] | None = None,
                     profile_conflicts: set[str] | None = None) -> dict:
    results = []
    for segment in segments:
        speaker = segment.get("speaker_id") or segment.get("speaker") or c.SPEAKER_ID_NARRATOR
        voice_id = (speaker_voices or {}).get(speaker) or (speaker_voices or {}).get(segment.get("speaker_surface"))
        result = resolve_expression(
            segment=segment, speaker_id=speaker, voice_id=voice_id, profiles=profiles,
            capabilities=capabilities, policy=policy,
            voice_conflict=speaker in (voice_conflicts or set()),
            profile_conflict=speaker in (profile_conflicts or set()),
        )
        results.append(result)
    return {
        "contractVersion": c.EXPRESSIVE_CONTRACT_VERSION,
        "policy": policy,
        "requested": results,
        "fallbackCount": sum(1 for item in results if item["fallbackReason"]),
        "capabilityUsed": capabilities or {},
    }


def profiles_for_book(book_id: int, speaker_id: str | None = None) -> list[dict]:
    return [dict(row) for row in db.list_expressive_profiles(book_id, speaker_id=speaker_id)]


def resolve_preview(*, speaker_id: str, voice_id: str, emotion_value: str = c.EMOTION_NEUTRAL,
                    intensity_value: float = c.EXPRESSIVE_INTENSITY_DEFAULT,
                    tone: str | None = None, speaking_style: str | None = None,
                    profiles: list[dict] | None = None, capabilities: dict | None = None,
                    policy: str = c.EMOTION_POLICY_BEST_EFFORT) -> dict:
    segment = {
        "segment_id": "preview",
        "text": "",
        "emotion": {"label": emotion_value, "intensity": intensity_value},
        "tone": tone,
        "speaking_style": speaking_style,
    }
    return resolve_expression(
        segment=segment, speaker_id=speaker_id, voice_id=voice_id,
        profiles=profiles, capabilities=capabilities, policy=policy,
    )
