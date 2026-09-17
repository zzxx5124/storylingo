"""Provider API v1 HTTP client; provider-native fields stay remote."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict
from urllib.parse import urljoin

import httpx

CANONICAL_EMOTIONS = ("neutral", "happy", "sad", "angry", "tense")
MODES = ("best_effort", "strict")
MAX_BODY_BYTES = 256 * 1024
MAX_PREVIEW_TEXT = 3000
# The Provider API permits 20,000 characters, but the local CosyVoice
# runtime's inference latency grows non-linearly for near-limit requests.
# Keep client-side synthesis units bounded so one slow inference cannot make
# an entire chapter time out; the provider contract limit remains unchanged.
MAX_SYNTHESIS_TEXT = 3000
MAX_RETRIES = 1
MAX_RETRY_AFTER_SECONDS = 60


def _redact_message(message):
    """Keep provider errors useful without exposing credentials or local paths."""
    value = str(message or "")[:300]
    value = re.sub(
        r"((?:sk|key|token|secret)[-:=\s]+)[A-Za-z0-9._~+/=-]{4,}",
        lambda match: match.group(1) + "***", value, flags=re.I,
    )
    value = re.sub(r"(?:sk|key|token|secret)[-_:=\s]+[A-Za-z0-9._~+/=-]{4,}", "\\g<0>***", value, flags=re.I)
    value = re.sub(r"sk-[A-Za-z0-9._~+/=-]{4,}", "sk-***", value, flags=re.I)
    value = re.sub(r"(?i)(?:[A-Za-z]:\\|/)(?:[^\s,;]+[/\\])+[^\s,;]*", "[redacted-path]", value)
    return value


@dataclass(frozen=True)
class ProviderV1Request:
    text: str
    voice_id: str
    language: str
    request_id: str
    emotion: str = "neutral"
    intensity: float = 0.5
    tone: str | None = None
    speaking_style: str | None = None
    mode: str = "best_effort"
    segment_id: str | None = None


@dataclass(frozen=True)
class ProviderV1Metadata:
    request_id: str
    content_type: str
    audio_format: str
    duration: str | None
    duration_status: str
    requested_expression: str
    applied_expression: str
    fallback_reason: str | None
    ignored_parameters: tuple[str, ...]
    provider_version: str
    model_version: str


def build_request(*, text, voice_id, language, request_id=None, emotion="neutral", intensity=0.5,
                  tone=None, speaking_style=None, mode="best_effort", segment_id=None,
                  endpoint="synthesize") -> dict:
    """Build and validate only the provider-neutral v1 request shape."""
    text = str(text or "")
    if not text or len(text) > (MAX_PREVIEW_TEXT if endpoint == "preview" else MAX_SYNTHESIS_TEXT):
        raise ProviderV1Error("text exceeds Provider API v1 limit", code="invalid_request", status=413)
    if not voice_id or not language:
        raise ProviderV1Error("voice_id and language are required", code="invalid_request", status=400)
    if emotion not in CANONICAL_EMOTIONS:
        raise ProviderV1Error("unsupported canonical emotion", code="invalid_request", status=400)
    try:
        intensity = float(intensity)
    except (TypeError, ValueError) as error:
        raise ProviderV1Error("intensity must be numeric", code="invalid_request", status=400) from error
    if not 0 <= intensity <= 1:
        raise ProviderV1Error("intensity must be between 0 and 1", code="invalid_request", status=400)
    if mode not in MODES:
        raise ProviderV1Error("invalid synthesis mode", code="invalid_request", status=400)
    request = asdict(ProviderV1Request(text=text, voice_id=str(voice_id), language=str(language),
                                       request_id=str(request_id or f"storylingo-{uuid.uuid4().hex}"),
                                       emotion=emotion, intensity=intensity, tone=tone,
                                       speaking_style=speaking_style, mode=mode, segment_id=segment_id))
    request = {key: value for key, value in request.items() if value is not None}
    if len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > MAX_BODY_BYTES:
        raise ProviderV1Error("request body exceeds Provider API v1 limit", code="invalid_request", status=413)
    return request


class ProviderV1Error(RuntimeError):
    def __init__(self, message, *, code="synthesis_failed", status=502, retryable=False,
                 request_id="", retry_after=None):
        super().__init__(message)
        self.code, self.status, self.retryable, self.request_id = code, status, bool(retryable), request_id
        self.retry_after = retry_after


def _url(row, path):
    return urljoin(row["base_url"].rstrip("/") + "/", path.lstrip("/"))


def _split_text_for_synthesis(text: str, max_chars: int = MAX_SYNTHESIS_TEXT) -> list[str]:
    """在 Provider API v1 的文字上限內，以句界切分長文。"""
    from .tts_text import split_text_for_synthesis
    return split_text_for_synthesis(text, max_chars)


def _concat_audio_parts(parts: list[str], output_path: str, temp_dir: str) -> None:
    """以 ffmpeg 串接 Provider v1 的多段音訊，保持單一輸出檔。"""
    list_path = os.path.join(temp_dir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as handle:
        for part in parts:
            handle.write(f"file '{os.path.basename(part)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c:a", "libmp3lame", "-q:a", "4", output_path],
        cwd=temp_dir, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1800, check=True,
    )


def _headers(row, accept="application/json"):
    from . import tts_provider
    return {**tts_provider._headers(row), "Accept": accept}


def _raise_response(response):
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = {}
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "provider_error")
    retryable = bool(error.get("retryable")) or response.status_code in (408, 429, 500, 502, 503, 504)
    message = _redact_message(error.get("message") or f"provider 回應 HTTP {response.status_code}")
    retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if retry_after is None:
        retry_after = error.get("retry_after_seconds")
    try:
        retry_after = min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(retry_after))) if retry_after is not None else None
    except (TypeError, ValueError):
        retry_after = None
    raise ProviderV1Error(message, code=code, status=response.status_code, retryable=retryable,
                          request_id=response.headers.get("X-Request-ID", ""), retry_after=retry_after)


def _request(row, method, path, **kwargs):
    configured = float(row.get("timeout_seconds") or 95)
    read_timeout = min(configured, 60 if path.endswith("/preview") else 300)
    request_body = kwargs.get("json") or {}
    request_id = request_body.get("request_id") if isinstance(request_body, dict) else None
    headers = {**_headers(row), **({"X-Request-ID": request_id} if request_id else {})}
    if request_id:
        headers["Idempotency-Key"] = request_id
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(max(1, read_timeout), connect=5), follow_redirects=False) as client:
                response = client.request(method, _url(row, path), headers=headers, **kwargs)
            if response.status_code >= 400:
                try:
                    _raise_response(response)
                except ProviderV1Error as error:
                    last_error = error
                    if not error.retryable or attempt >= MAX_RETRIES:
                        raise
            else:
                return response
        except httpx.TimeoutException as error:
            last_error = ProviderV1Error("provider timeout", code="timeout", status=504, retryable=True)
            if attempt >= MAX_RETRIES:
                raise last_error from error
        except httpx.HTTPError as error:
            last_error = ProviderV1Error("provider request failed", code="provider_unavailable", status=502, retryable=True)
            if attempt >= MAX_RETRIES:
                raise last_error from error
        if attempt < MAX_RETRIES:
            delay = getattr(last_error, "retry_after", None)
            if delay is None:
                delay = 0.1 * (attempt + 1)
            time.sleep(min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(delay))))
    raise last_error or ProviderV1Error("provider request failed", retryable=True)


def _json(response, label):
    try:
        value = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise ProviderV1Error(f"provider {label} 回應不是有效 JSON") from error
    if not isinstance(value, dict):
        raise ProviderV1Error(f"provider {label} 回應格式錯誤")
    return value


def test_connection(row):
    try:
        health = _json(_request(row, "GET", "/v1/health"), "health")
        version = _json(_request(row, "GET", "/v1/version"), "version")
        voices = _json(_request(row, "GET", "/v1/voices"), "voices")
        try:
            capabilities = _json(_request(row, "GET", "/v1/capabilities"), "capabilities")
        except ProviderV1Error as error:
            if error.status == 404:
                capabilities = {"capabilitiesUnavailable": True}
            else:
                raise
        items = voices.get("voices", voices.get("data", []))
        return {"ok": True, "health": health, "version": version,
                "voices": items if isinstance(items, list) else [], "capabilities": capabilities,
                "statusCode": 200}
    except ProviderV1Error as error:
        return {"ok": False, "error": str(error), "code": error.code, "retryable": error.retryable,
                "statusCode": error.status, "requestId": error.request_id}


def normalize_capabilities(raw, voices):
    raw = raw if isinstance(raw, dict) else {}
    declared = raw.get("declared") if isinstance(raw.get("declared"), dict) else raw
    probed = raw.get("probed") if isinstance(raw.get("probed"), dict) else {}
    voice_caps = (raw.get("voice_capabilities") or raw.get("voiceCapabilities") or
                  probed.get("voice_capabilities") or probed.get("voiceCapabilities") or {})
    if not isinstance(voice_caps, dict):
        voice_caps = {}
    def _voice_supports_expression(value):
        if not isinstance(value, dict):
            return False
        labels = value.get("supported_emotions") or value.get("supportedEmotions")
        if isinstance(labels, list) and labels:
            return any(str(label).lower() != "neutral" for label in labels)
        return bool(value.get("supports_emotion", value.get("supportsEmotion", False)))

    per_voice_emotion = any(_voice_supports_expression(value) for value in voice_caps.values())
    return {
        "schemaVersion": raw.get("schemaVersion", 1), "snapshotVersion": raw.get("snapshotVersion", 1),
        "adapterKey": "cosyvoice_http",
        "emotion": {
            "supported": bool(declared.get("supports_emotion", declared.get("supportsEmotion", False))) or per_voice_emotion,
            "controlMode": "provider_v1", "labels": ["neutral", "happy", "sad", "angry", "tense"],
            "supportsIntensity": bool(declared.get("supports_intensity", declared.get("supportsIntensity", False))),
            "supportsStyle": bool(declared.get("supports_style", declared.get("supportsStyle", False))),
            "supportsPerSegment": True,
        },
        "declared": declared, "probed": probed, "voiceCapabilities": voice_caps, "voices": voices,
        "limits": raw.get("limits", {}),
    }


def synthesize_to_file(row, request, output_path, endpoint="/v1/synthesize"):
    allowed = {"text", "voice_id", "language", "request_id", "emotion", "intensity", "tone",
               "speaking_style", "mode", "segment_id"}
    extra = set(request) - allowed
    if extra:
        raise ProviderV1Error("provider-native or private request fields are not allowed",
                              code="invalid_request", status=400)
    text = str(request.get("text") or "")
    if endpoint.endswith("/synthesize") and len(text) > MAX_SYNTHESIS_TEXT:
        chunks = _split_text_for_synthesis(text)
        if len(chunks) > 1:
            temp_dir = tempfile.mkdtemp(prefix="tts_provider_v1_chunks_")
            try:
                parts: list[str] = []
                metadata = []
                for index, chunk in enumerate(chunks):
                    part_path = os.path.join(temp_dir, f"part-{index:04d}.mp3")
                    part_request = {**request, "text": chunk,
                                    "request_id": f"{request.get('request_id') or 'storylingo'}-part-{index}"}
                    metadata.append(synthesize_to_file(row, part_request, part_path, endpoint=endpoint))
                    parts.append(part_path)
                _concat_audio_parts(parts, output_path, temp_dir)
                result = metadata[0] if metadata else {}
                return {**result, "requestId": request.get("request_id") or result.get("requestId"),
                        "chunkCount": len(chunks), "providerRequestCount": len(chunks)}
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
    request = build_request(endpoint="preview" if endpoint.endswith("/preview") else "synthesize", **request)
    response = _request(row, "POST", endpoint, json=request)
    content_type = (response.headers.get("content-type") or "audio/mpeg").split(";", 1)[0]
    if not content_type.startswith("audio/"):
        raise ProviderV1Error("provider response is not binary audio", code="invalid_response", status=502)
    response_request_id = response.headers.get("X-Request-ID", "")
    if not response_request_id or response_request_id != request["request_id"]:
        raise ProviderV1Error("provider request_id missing or mismatched", code="invalid_response", status=502)
    api_version = response.headers.get("X-TTS-API-Version", "")
    if api_version and api_version != "1":
        raise ProviderV1Error("provider API version incompatible", code="incompatible_version", status=502)
    data = response.content
    if not data or len(data) > 50 * 1024 * 1024:
        raise ProviderV1Error("provider audio content invalid")
    with open(output_path, "wb") as handle:
        handle.write(data)
    ignored = response.headers.get("X-Ignored-Parameters", "")
    typed = ProviderV1Metadata(
        request_id=response_request_id, content_type=content_type,
        audio_format=response.headers.get("X-Audio-Format", "mp3"),
        duration=response.headers.get("X-Audio-Duration"),
        duration_status=response.headers.get("X-Audio-Duration-Status", "unavailable"),
        requested_expression=response.headers.get("X-Requested-Expression", request.get("emotion", "neutral")),
        applied_expression=response.headers.get("X-Applied-Expression", request.get("emotion", "neutral")),
        fallback_reason=response.headers.get("X-Fallback-Reason", "") or None,
        ignored_parameters=tuple(item for item in response.headers.get("X-Ignored-Parameters", "").split(",") if item),
        provider_version=response.headers.get("X-Provider-Version", ""),
        model_version=response.headers.get("X-Model-Version", ""),
    )
    return {
        **asdict(typed),
        "requestId": response_request_id,
        "contentType": typed.content_type, "audioFormat": typed.audio_format,
        "durationStatus": typed.duration_status,
        "requestedExpression": typed.requested_expression,
        "appliedExpression": typed.applied_expression,
        "appliedIntensity": response.headers.get("X-Applied-Intensity"),
        "appliedTone": response.headers.get("X-Applied-Tone"),
        "appliedStyle": response.headers.get("X-Applied-Speaking-Style", response.headers.get("X-Applied-Style")),
        "fallbackReason": typed.fallback_reason,
        "ignoredParameters": list(typed.ignored_parameters),
        "providerVersion": typed.provider_version,
        "modelVersion": typed.model_version,
    }


def preview_to_file(row, request, output_path):
    return synthesize_to_file(row, request, output_path, endpoint="/v1/preview")
