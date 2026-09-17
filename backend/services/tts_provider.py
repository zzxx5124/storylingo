"""遠端 TTS provider 設定、機密保存與 HTTP adapter。"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urljoin

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .. import db, settings
from . import netsec


def _fernet() -> Fernet:
    raw = os.getenv("TTS_SECRET_KEY", "").strip()
    if raw:
        try:
            return Fernet(raw.encode("ascii"))
        except Exception as error:
            raise RuntimeError("TTS_SECRET_KEY 格式錯誤") from error

    path = os.path.join(settings.DATA_DIR, ".tts_secret_key")
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
        raise RuntimeError("無法建立 TTS provider secret 儲存檔") from error


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
        raise RuntimeError("TTS provider secret 無法解密") from error


def validate_base_url(value: str) -> str:
    """驗證 TTS provider base URL（共用 netsec 安全檢查）。"""
    return netsec.validate_http_url(value, label="provider URL")


def _path(value: str, default: str) -> str:
    value = (value or default).strip()
    if not value.startswith("/") or ".." in value or "?" in value or "#" in value:
        raise ValueError("provider path 不合法")
    return value


def public_provider(row: dict) -> dict:
    """只回傳管理員 UI 所需的非機密資料。"""
    return {
        "id": row["id"], "name": row["name"], "providerType": row["provider_type"],
        "baseUrl": row["base_url"], "synthPath": row["synth_path"], "voicesPath": row["voices_path"],
        "authScheme": row["auth_scheme"], "adapterKey": row.get("adapter_key", "generic_http"),
        "enabled": bool(row["enabled"]), "isDefault": bool(row.get("is_default", 0)),
        "hasSecret": bool(row["secret_ciphertext"]), "lastStatus": row["last_status"],
        "lastError": row["last_error"], "lastCheckedAt": row["last_checked_at"],
        "configVersion": row.get("config_version", 1),
        "maxConcurrency": max(1, int(row.get("max_concurrency") or 1)),
        "healthState": row.get("health_state", "unknown"),
        "cooldownUntil": row.get("cooldown_until"),
        "timeoutSeconds": row.get("timeout_seconds", 95),
        "capabilitiesStatus": row.get("capabilities_status", "unknown"),
        "capabilitiesHash": row.get("capabilities_hash", ""),
        "capabilitySnapshotVersion": row.get("capability_snapshot_version", 1),
        "capabilitiesDeclared": _safe_capability_summary(row.get("capabilities_declared_json")),
        "capabilitiesProbed": _safe_capability_summary(row.get("capabilities_probed_json")),
        "capabilitiesCheckedAt": row.get("capabilities_checked_at"),
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def _safe_capability_summary(raw) -> dict:
    """Expose non-secret capability summary only; never provider payload/credentials."""
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def validate_payload(payload: dict, current: dict | None = None) -> dict:
    name = (payload.get("name") if "name" in payload else (current or {}).get("name") or "").strip()
    if len(name) < 2 or len(name) > 80:
        raise ValueError("provider 名稱需為 2 至 80 字元")
    base_url = validate_base_url(payload.get("baseUrl") if "baseUrl" in payload else (current or {}).get("base_url", ""))
    provider_type = (payload.get("providerType") if "providerType" in payload else (current or {}).get("provider_type", "generic_http"))
    if provider_type not in ("generic_http", "openai_compatible"):
        raise ValueError("不支援的 provider 類型")
    auth_scheme = (payload.get("authScheme") if "authScheme" in payload else (current or {}).get("auth_scheme", "bearer"))
    if auth_scheme not in ("none", "bearer", "x-api-key"):
        raise ValueError("不支援的認證方式")
    adapter_key = (payload.get("adapterKey") if "adapterKey" in payload else (current or {}).get("adapter_key", "generic_http"))
    if adapter_key not in ("generic_http", "openai_compatible", "cosyvoice_http"):
        raise ValueError("不支援的 adapter")
    timeout = payload.get("timeoutSeconds", (current or {}).get("timeout_seconds", 95))
    try:
        timeout = float(timeout)
    except (TypeError, ValueError) as error:
        raise ValueError("provider timeout 不合法") from error
    if not 1 <= timeout <= 600:
        raise ValueError("provider timeout 必須介於 1 至 600 秒")
    try:
        max_concurrency = int(payload.get("maxConcurrency", (current or {}).get("max_concurrency", 1)))
    except (TypeError, ValueError) as error:
        raise ValueError("provider concurrency 不合法") from error
    if not 1 <= max_concurrency <= 100:
        raise ValueError("provider concurrency 必須介於 1 至 100")
    return {
        "name": name, "base_url": base_url, "provider_type": provider_type,
        "synth_path": _path(payload.get("synthPath") if "synthPath" in payload else (current or {}).get("synth_path"), "/synthesize"),
        "voices_path": _path(payload.get("voicesPath") if "voicesPath" in payload else (current or {}).get("voices_path"), "/voices"),
        "auth_scheme": auth_scheme,
        "adapter_key": adapter_key,
        "timeout_seconds": timeout,
        "max_concurrency": max_concurrency,
        "enabled": bool(payload.get("enabled", current.get("enabled", 1) if current else True)),
        "is_default": bool(payload.get("isDefault", current.get("is_default", 0) if current else False)),
    }


def _headers(row: dict) -> dict:
    secret = decrypt_secret(row.get("secret_ciphertext", ""))
    if not secret or row.get("auth_scheme") == "none":
        return {}
    if row.get("auth_scheme") == "x-api-key":
        return {"X-API-Key": secret}
    return {"Authorization": f"Bearer {secret}"}


def test_connection(row: dict) -> dict:
    if row.get("adapter_key") == "cosyvoice_http":
        from . import tts_provider_v1
        return tts_provider_v1.test_connection(row)
    url = urljoin(row["base_url"].rstrip("/") + "/", row["voices_path"].lstrip("/"))
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
            response = client.get(url, headers=_headers(row))
        if response.status_code >= 400:
            raise RuntimeError(f"provider 回應 HTTP {response.status_code}")
        data = response.json()
        voices = data.get("voices", data.get("data", data)) if isinstance(data, dict) else data
        if not isinstance(voices, list):
            voices = []
        return {"ok": True, "voices": voices[:500], "statusCode": response.status_code}
    except (httpx.HTTPError, ValueError, RuntimeError) as error:
        return {"ok": False, "error": str(error)[:500]}


def save_check_result(provider_id: int, result: dict):
    db.update_tts_provider(provider_id, {
        "last_status": "ok" if result.get("ok") else "error",
        "last_error": "" if result.get("ok") else result.get("error", ""),
        "last_checked_at": db.ts(),
    })
    if result.get("ok"):
        db.replace_tts_provider_voices(provider_id, result.get("voices") or [])
        if result.get("capabilities"):
            from . import tts_provider_v1
            normalized = tts_provider_v1.normalize_capabilities(result["capabilities"], result.get("voices") or [])
            import hashlib
            db.update_tts_provider(provider_id, {
                "capabilities_json": json.dumps(normalized, ensure_ascii=False),
                "capabilities_declared_json": json.dumps(normalized.get("declared", {}), ensure_ascii=False),
                "capabilities_probed_json": json.dumps(normalized.get("probed", {}), ensure_ascii=False),
                "capabilities_hash": hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest(),
                "capabilities_status": "supported" if normalized["emotion"]["supported"] else "unsupported",
                "capabilities_checked_at": db.ts(),
            })


def _synthesis_payload(row: dict, text: str, voice: str) -> dict:
    if row["provider_type"] == "openai_compatible":
        return {"input": text, "voice": voice, "response_format": "mp3"}
    return {"text": text, "voice": voice, "format": "mp3"}


def _synthesize_once(row: dict, text: str, voice: str, output_path: str):
    """呼叫同步遠端 TTS API，將結果安全寫入暫存檔。"""
    url = urljoin(row["base_url"].rstrip("/") + "/", row["synth_path"].lstrip("/"))
    headers = {**_headers(row), "Accept": "audio/mpeg, application/json"}
    with httpx.Client(timeout=httpx.Timeout(95.0, connect=10.0), follow_redirects=False) as client:
        response = client.post(url, headers=headers, json=_synthesis_payload(row, text, voice))
    if response.status_code >= 400:
        raise RuntimeError(f"provider 回應 HTTP {response.status_code}")
    content_type = (response.headers.get("content-type") or "").lower()
    if "audio" in content_type or response.content[:3] == b"ID3":
        data = response.content
    else:
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise RuntimeError("provider 回應不是有效音訊或 JSON") from error
        encoded = payload.get("audio_base64") or payload.get("audio")
        if encoded:
            import base64 as _base64
            try:
                data = _base64.b64decode(encoded, validate=True)
            except Exception as error:
                raise RuntimeError("provider audio base64 格式錯誤") from error
        else:
            audio_url = payload.get("audio_url") or payload.get("url")
            if not audio_url:
                raise RuntimeError("provider 回應缺少 audio/audio_url")
            validate_base_url(audio_url)
            with httpx.Client(timeout=httpx.Timeout(95.0, connect=10.0), follow_redirects=False) as client:
                result = client.get(audio_url, headers=_headers(row))
            if result.status_code >= 400:
                raise RuntimeError(f"provider 音訊下載失敗 HTTP {result.status_code}")
            data = result.content
    if not data or len(data) > 50 * 1024 * 1024:
        raise RuntimeError("provider 音訊內容為空或超過 50 MB")
    with open(output_path, "wb") as handle:
        handle.write(data)


def _split_text_for_remote(text: str, max_chars: int = 30) -> list[str]:
    """Split long remote requests at sentence boundaries before hard limits."""
    from .tts_text import split_text_for_synthesis
    return split_text_for_synthesis(text, max_chars)


def _synthesize_job_once(row: dict, text: str, voice: str, output_path: str) -> bool:
    """Use the optional Qwen async contract; return False when unsupported."""
    base = row["base_url"].rstrip("/")
    headers = {**_headers(row), "Accept": "application/json"}
    payload = {"text": text, "voice": voice, "format": "mp3"}
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=False) as client:
            response = client.post(f"{base}/jobs", headers=headers, json=payload)
            if response.status_code == 404:
                return False
            if response.status_code >= 400:
                raise RuntimeError(f"provider async job HTTP {response.status_code}")
            job_id = response.json().get("jobId")
            if not job_id:
                raise RuntimeError("provider async job response missing jobId")
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                status = client.get(f"{base}/jobs/{job_id}", headers=headers)
                if status.status_code >= 400:
                    raise RuntimeError(f"provider async status HTTP {status.status_code}")
                data = status.json()
                if data.get("status") == "failed":
                    raise RuntimeError(str(data.get("error") or "remote TTS job failed")[:500])
                if data.get("status") == "completed":
                    audio = client.get(f"{base}/jobs/{job_id}/audio", headers={**headers, "Accept": "audio/mpeg"})
                    if audio.status_code >= 400:
                        raise RuntimeError(f"provider async audio HTTP {audio.status_code}")
                    with open(output_path, "wb") as handle:
                        handle.write(audio.content)
                    return True
                time.sleep(2)
            raise RuntimeError("provider async job timed out")
    except httpx.HTTPError as error:
        raise RuntimeError(f"provider async request failed: {error}") from error


def synthesize_to_file(row: dict, text: str, voice: str, output_path: str):
    """Synthesize remote TTS, splitting long text and joining valid audio."""
    if row.get("adapter_key") == "cosyvoice_http":
        from . import tts_provider_v1
        request = {
            "request_id": f"storylingo-{int(time.time() * 1000)}", "text": text,
            "voice_id": voice, "language": "zh-TW", "emotion": "neutral",
            "intensity": 0.5, "mode": "best_effort",
        }
        tts_provider_v1.synthesize_to_file(row, request, output_path)
        return
    chunks = _split_text_for_remote(text)
    if len(chunks) <= 1:
        return _synthesize_once(row, text, voice, output_path)
    temp_dir = tempfile.mkdtemp(prefix="tts_remote_chunks_")
    try:
        parts: list[str] = []
        for index, chunk in enumerate(chunks):
            part = os.path.join(temp_dir, f"part-{index:04d}.mp3")
            if not _synthesize_job_once(row, chunk, voice, part):
                _synthesize_once(row, chunk, voice, part)
            parts.append(part)
        list_path = os.path.join(temp_dir, "concat.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for part in parts:
                handle.write(f"file '{os.path.basename(part)}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c:a", "libmp3lame", "-q:a", "3", output_path],
            cwd=temp_dir, check=True, capture_output=True, timeout=300,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
