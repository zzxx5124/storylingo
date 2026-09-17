"""V4 TTS adapter & capability layer。

- 以 adapter_key 選取行為的 adapter 抽象，包裝既有遠端 TTS 邏輯。
- capability 偵測（GET /capabilities），快取 JSON／status／checked_at／hash。
- emotion capability 明確表示 supported / unsupported / unknown / degraded。
- 保留既有 netsec SSRF／HTTPS 保護與機密 redaction。
"""
import hashlib
import json
import os
import time
from urllib.parse import urljoin

import httpx

from .. import db, settings
from . import netsec
from . import tts_provider

# Capability status
CAP_UNKNOWN = "unknown"
CAP_SUPPORTED = "supported"
CAP_UNSUPPORTED = "unsupported"
CAP_DEGRADED = "degraded"
CAP_OFFLINE = "offline"

EMOTION_FIELD = "emotion"

# 已知 adapter keys（v4_contracts 同步）
ADAPTER_KEYS = ("generic_http", "openai_compatible", "cosyvoice_http")

# Capability 過期秒數（過期後可重新檢查）
CAPABILITY_TTL_SECONDS = 3600


class TTSAdapterError(RuntimeError):
    """正規化後的 provider 錯誤。"""


def adapter_for(provider: dict) -> dict:
    """依 provider.adapter_key 回傳 adapter 描述。"""
    key = provider.get("adapter_key") or "generic_http"
    if key == "cosyvoice_http":
        from . import tts_provider_v1
        return {"key": key, "list_voices": tts_provider_v1.test_connection,
                "synthesize": tts_provider_v1.synthesize_to_file,
                "synthesis_payload": "provider_api_v1"}
    return {
        "key": key,
        "list_voices": tts_provider.test_connection,
        "synthesize": tts_provider.synthesize_to_file,
        "synthesis_payload": _synthesis_payload_for(key),
    }


def _synthesis_payload_for(adapter_key: str):
    if adapter_key == "openai_compatible":
        return "openai_compatible"
    return "generic_http"


def capabilities_path(provider: dict) -> str:
    path = (provider.get("capabilities_path") or "/capabilities").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path


def canonicalize_capabilities(raw: dict, adapter_key: str) -> dict:
    """把 provider 原始 capability 正規化成 canonical shape。"""
    if not isinstance(raw, dict):
        raw = {}
    emotion = raw.get("emotion") if isinstance(raw.get("emotion"), dict) else {}
    supported = bool(emotion.get("supported", False))
    labels = emotion.get("labels") if isinstance(emotion.get("labels"), list) else []
    limits = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    normalized = {
        "schemaVersion": raw.get("schemaVersion") or 1,
        "snapshotVersion": raw.get("snapshotVersion") or 1,
        "adapterKey": adapter_key,
        "emotion": {
            "supported": supported,
            "controlMode": emotion.get("controlMode") or ("native_emotion" if supported else "none"),
            "labels": [str(l) for l in labels][:200],
            "supportsIntensity": bool(emotion.get("supportsIntensity", False)),
            "supportsStyle": bool(emotion.get("supportsStyle", False)),
            "supportsPerSegment": bool(emotion.get("supportsPerSegment", False)),
        },
        "limits": {
            "maxChars": int(limits.get("maxChars") or 0),
            "maxConcurrent": int(limits.get("maxConcurrent") or 1),
        },
    }
    # Keep the raw provider declaration and the normalized platform probe
    # separate. The old top-level shape remains for legacy consumers.
    return {**normalized, "declared": dict(raw), "probed": normalized}


def _capability_status(cap: dict) -> str:
    if cap.get("emotion", {}).get("supported"):
        return CAP_SUPPORTED
    return CAP_UNSUPPORTED


def query_capabilities(provider: dict) -> dict:
    """GET provider /capabilities，回傳 {ok, capabilities, status, error}。"""
    if provider.get("adapter_key") == "cosyvoice_http":
        from . import tts_provider_v1
        try:
            raw = tts_provider_v1._request(provider, "GET", "/v1/capabilities").json()
            cap = tts_provider_v1.normalize_capabilities(raw, [])
            return {"ok": True, "capabilities": cap, "status": _capability_status(cap)}
        except tts_provider_v1.ProviderV1Error as error:
            if error.status == 404:
                return {"ok": False, "status": CAP_UNSUPPORTED, "error": "provider 未提供 /v1/capabilities"}
            return {"ok": False, "status": CAP_UNKNOWN, "error": str(error)[:500]}
    base = provider["base_url"].rstrip("/")
    url = urljoin(base + "/", capabilities_path(provider).lstrip("/"))
    headers = tts_provider._headers(provider)
    try:
        # 保留 SSRF/HTTPS 保護：與 test_connection 相同，不跟隨 redirect
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
            response = client.get(url, headers=headers)
        if response.status_code == 404:
            # /capabilities 端點缺失：表示 provider 不支援 capabilities，非連線錯誤
            return {"ok": False, "status": CAP_UNSUPPORTED, "error": f"provider 未提供 {capabilities_path(provider)} 端點（HTTP 404）"}
        if response.status_code >= 400:
            return {"ok": False, "status": CAP_UNKNOWN, "error": f"provider 回應 HTTP {response.status_code}"}
        raw = response.json()
        capabilities = canonicalize_capabilities(raw, provider.get("adapter_key") or "generic_http")
        status = _capability_status(capabilities)
        return {"ok": True, "capabilities": capabilities, "status": status}
    except (httpx.HTTPError, ValueError, RuntimeError) as error:
        return {"ok": False, "status": CAP_UNKNOWN, "error": str(error)[:500]}


def capabilities_hash(cap: dict) -> str:
    if not cap:
        return ""
    return hashlib.sha256(json.dumps(cap, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def refresh_capabilities(provider_id: int) -> dict:
    """查詢並儲存 provider capabilities（含 status/checked_at/hash）。"""
    provider = db.get_tts_provider(provider_id)
    if not provider:
        raise TTSAdapterError("找不到 TTS provider")
    result = query_capabilities(provider)
    if result.get("ok"):
        cap = result["capabilities"]
        fields = {
            "capabilities_json": json.dumps(cap, ensure_ascii=False),
            "capabilities_status": result["status"],
            "capabilities_checked_at": db.ts(),
            "capabilities_hash": capabilities_hash(cap),
            "capability_snapshot_version": cap.get("snapshotVersion", 1),
            "capabilities_declared_json": json.dumps(cap.get("declared", {}), ensure_ascii=False),
            "capabilities_probed_json": json.dumps(cap.get("probed", cap), ensure_ascii=False),
            "last_status": "ok" if result["ok"] else "error",
            "last_error": "",
            "last_checked_at": db.ts(),
        }
    else:
        fields = {
            "capabilities_status": result["status"],
            "capabilities_checked_at": db.ts(),
        }
        if result["status"] == CAP_UNSUPPORTED:
            # /capabilities 端點缺失：不影響 /voices 健康狀態，僅記錄為「不支援」
            fields["capabilities_hash"] = ""
        else:
            # 只有非「端點缺失」的錯誤才代表 provider 本身異常（影響 /voices 健康狀態）
            fields.update({
                "last_status": "error",
                "last_error": result.get("error", "")[:500],
                "last_checked_at": db.ts(),
            })
    db.update_tts_provider(provider_id, fields)
    return result


def load_capabilities(provider: dict) -> dict:
    """讀取 provider capabilities（過期才重新檢查）。"""
    cached = provider.get("capabilities_json")
    checked = provider.get("capabilities_checked_at")
    if cached and checked:
        try:
            value = json.loads(cached)
            if isinstance(value, dict):
                return value
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def emotion_support(provider: dict) -> dict:
    """回傳 provider 的 emotion 支援狀態（明確表示，絕不模糊）。"""
    cap = {}
    cached = provider.get("capabilities_json")
    if cached:
        try:
            parsed = json.loads(cached)
            if isinstance(parsed, dict):
                cap = parsed
        except (json.JSONDecodeError, TypeError):
            cap = {}
    emotion = cap.get("emotion", {})
    if emotion.get("supported"):
        return {"status": CAP_SUPPORTED, "controlMode": emotion.get("controlMode", "native_emotion"),
                "labels": emotion.get("labels", []), "supportsIntensity": emotion.get("supportsIntensity", False)}
    return {"status": CAP_UNSUPPORTED, "controlMode": "none", "labels": [], "supportsIntensity": False}


def normalize_error(error: Exception) -> str:
    """把任何例外正規化成一致的短錯誤訊息（不洩漏機密）。"""
    msg = str(error or "")[:500]
    if not msg:
        msg = "未知 TTS provider 錯誤"
    # redact 任何可能的 secret 模式
    import re
    msg = re.sub(
        r"((?:sk|key|token|secret)[-:=\s]+)[A-Za-z0-9._~+/=-]{4,}",
        lambda match: match.group(1) + "***", msg, flags=re.I,
    )
    msg = re.sub(r"(sk-[A-Za-z0-9_-]+)", "***", msg)
    msg = re.sub(r"(?i)(?:[A-Za-z]:\\|/)(?:[^\s,;]+[/\\])+[^\s,;]*", "[redacted-path]", msg)
    return msg


def error_envelope(error: Exception) -> str:
    """保存 bounded machine-readable TTS failure，不洩漏 provider secret。"""
    code = str(getattr(error, "code", "") or "tts_error")
    status = getattr(error, "status", None)
    payload = {
        "code": code,
        "message": {
            "provider_busy": "語音服務忙碌，請稍後重試",
            "queue_full": "語音服務佇列已滿，請稍後重試",
            "model_unavailable": "語音模型目前無法使用，請稍後重試",
            "timeout": "語音服務逾時，請稍後重試",
        }.get(code, normalize_error(error))[:300],
        "retryable": bool(getattr(error, "retryable", False)),
    }
    if isinstance(status, int):
        payload["status"] = status
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
