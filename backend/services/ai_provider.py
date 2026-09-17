"""V4 AI provider 設定、機密保存與測試。

- 機密以 Fernet 加密保存（AI_SECRET_KEY 環境變數或 data/.ai_secret_key 檔）。
- 讀取 API 永不回傳機密明文。
- 嚴格「單一 default」invariant：無 default 是合法狀態，不得有 implicit first-row fallback。
- URL／SSRF 驗證共用 netsec 安全 helpers。
"""
import os

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .. import db, settings
from . import netsec

AI_PROVIDER_TYPES = ("openai", "openai_compatible", "deepseek")


def normalize_secret(value) -> str:
    """Validate the API key boundary before encryption; never coerce objects."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("API key 必須是文字")
    return value.strip()


def _text(value, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} 必須是文字")
    return value.strip()


def _fernet() -> Fernet:
    raw = os.getenv("AI_SECRET_KEY", "").strip()
    if raw:
        try:
            return Fernet(raw.encode("ascii"))
        except Exception as error:
            raise RuntimeError("AI_SECRET_KEY 格式錯誤") from error

    path = os.path.join(settings.DATA_DIR, ".ai_secret_key")
    try:
        if os.path.exists(path):
            with open(path, "rb") as handle:
                return Fernet(handle.read().strip())
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return Fernet(key)
    except OSError as error:
        raise RuntimeError("無法建立 AI provider secret 儲存檔") from error


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as error:
        raise RuntimeError("AI provider secret 無法解密") from error


def public_provider(row: dict) -> dict:
    """只回傳管理員 UI 所需的非機密資料。"""
    return {
        "id": row["id"], "name": row["name"], "providerType": row["provider_type"],
        "baseUrl": row["base_url"], "model": row["model"],
        "fallbackModel": row.get("fallback_model"),
        "enabled": bool(row["enabled"]), "isDefault": bool(row.get("is_default", 0)),
        "maxConcurrency": max(1, int(row.get("max_concurrency") or 1)),
        "healthState": row.get("health_state", "unknown"),
        "cooldownUntil": row.get("cooldown_until"),
        "hasSecret": bool(row["secret_ciphertext"]), "configVersion": row["config_version"],
        "structuredCapabilityStatus": row.get("structured_capability_status", "unknown"),
        "structuredCapabilityCheckedAt": row.get("structured_capability_checked_at"),
        "structuredCapabilityProbeVersion": row.get("structured_capability_probe_version", ""),
        "lastStatus": row["last_status"], "lastError": row["last_error"],
        "lastCheckedAt": row["last_checked_at"], "lastUsedAt": row["last_used_at"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def validate_payload(payload: dict, current: dict | None = None) -> dict:
    name = _text(payload.get("name") if "name" in payload else (current or {}).get("name"), "provider 名稱")
    if len(name) < 2 or len(name) > 80:
        raise ValueError("provider 名稱需為 2 至 80 字元")
    provider_type = (payload.get("providerType") if "providerType" in payload else (current or {}).get("provider_type", ""))
    if provider_type not in AI_PROVIDER_TYPES:
        raise ValueError("不支援的 AI provider 類型")
    base_url = netsec.validate_http_url(
        _text(payload.get("baseUrl") if "baseUrl" in payload else (current or {}).get("base_url"), "AI provider URL"),
        label="AI provider URL",
    )
    model = _text(payload.get("model") if "model" in payload else (current or {}).get("model"), "model")
    if not model:
        raise ValueError("model 必填")
    try:
        max_concurrency = int(payload.get("maxConcurrency", (current or {}).get("max_concurrency", 1)))
    except (TypeError, ValueError) as error:
        raise ValueError("provider concurrency 不合法") from error
    if not 1 <= max_concurrency <= 100:
        raise ValueError("provider concurrency 必須介於 1 至 100")
    fallback_model = _text(payload.get("fallbackModel") if "fallbackModel" in payload else (current or {}).get("fallback_model"), "fallback model") or None
    return {
        "name": name, "provider_type": provider_type, "base_url": base_url,
        "model": model, "fallback_model": fallback_model,
        "max_concurrency": max_concurrency,
        "enabled": bool(payload.get("enabled", current.get("enabled", 1) if current else True)),
        "is_default": bool(payload.get("isDefault", current.get("is_default", 0) if current else False)),
    }


def _headers(row: dict) -> dict:
    secret = decrypt_secret(row.get("secret_ciphertext", ""))
    if not secret:
        return {}
    return {"Authorization": f"Bearer {secret}"}


def test_connection(row: dict) -> dict:
    """以 GET {base_url}/models 驗證連線與認證。"""
    base = row["base_url"].rstrip("/")
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
            response = client.get(f"{base}/models", headers=_headers(row))
        if response.status_code >= 400:
            raise RuntimeError(f"provider 回應 HTTP {response.status_code}")
        data = response.json()
        models = data.get("data", data) if isinstance(data, dict) else data
        count = len(models) if isinstance(models, list) else 0
        return {"ok": True, "models": count, "statusCode": response.status_code}
    except (httpx.HTTPError, ValueError, RuntimeError) as error:
        return {"ok": False, "error": str(error)[:500]}


def save_check_result(provider_id: int, result: dict):
    db.update_ai_provider(provider_id, {
        "last_status": "ok" if result.get("ok") else "error",
        "last_error": "" if result.get("ok") else (result.get("error") or "")[:500],
        "last_checked_at": db.ts(),
    })
