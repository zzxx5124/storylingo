"""Private, deterministic TTS unit cache.

The cache is deliberately outside the canonical ``audio_generations`` record:
it only keeps provider output for an individual synthesis unit so a failed or
restarted chapter can resume without sending already completed text again.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading

from .. import settings


CACHE_VERSION = "tts-partial-v1"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _cache_dir() -> str:
    return os.path.join(settings.ROOT_DIR, "storage", "tts_partials")


def cache_key(*, source_text_hash: str, segment_id: str | None, text: str,
              voice_id: str | None, language: str, expression: dict | None,
              provider: dict | None, unit_version: str) -> str:
    """Return a reuse identity that does not contain generation/analysis IDs."""
    provider = provider or {}
    payload = {
        "cacheVersion": CACHE_VERSION,
        "sourceTextHash": source_text_hash,
        "segmentId": segment_id,
        "text": text,
        "voiceId": voice_id,
        "language": language,
        "expression": expression or {},
        "provider": {
            "id": provider.get("id"),
            "configVersion": provider.get("config_version"),
            "adapterKey": provider.get("adapter_key"),
            "adapterVersion": provider.get("adapter_version"),
            "capabilitiesHash": provider.get("capabilities_hash"),
        },
        "unitVersion": unit_version,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def path_for(key: str) -> str:
    if not key or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ValueError("invalid TTS partial cache key")
    return os.path.join(_cache_dir(), f"{key}.mp3")


def manifest_path_for(key: str) -> str:
    return path_for(key) + ".json"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def copy_if_valid(key: str, output_path: str) -> bool:
    """Copy a complete cached provider response into a generation temp file."""
    path = path_for(key)
    lock = _lock_for(key)
    with lock:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
        manifest = manifest_path_for(key)
        if os.path.isfile(manifest):
            try:
                with open(manifest, "r", encoding="utf-8") as handle:
                    info = json.load(handle)
                if info.get("key") != key or int(info.get("size") or 0) != os.path.getsize(path) \
                        or info.get("sha256") != _sha256(path):
                    return False
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return False
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        shutil.copyfile(path, output_path)
        return True


def publish(key: str, output_path: str) -> str:
    """Publish provider output atomically; never expose a half-written cache file."""
    target = path_for(key)
    lock = _lock_for(key)
    with lock:
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return target
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{key}.", suffix=".mp3", dir=os.path.dirname(target))
        os.close(fd)
        try:
            shutil.copyfile(output_path, temporary)
            if os.path.getsize(temporary) <= 0:
                raise ValueError("empty TTS partial output")
            os.replace(temporary, target)
            manifest_handle = tempfile.NamedTemporaryFile(prefix=f".{key}.", suffix=".json",
                                                           dir=os.path.dirname(target), delete=False,
                                                           mode="w", encoding="utf-8")
            manifest_tmp = manifest_handle.name
            try:
                with manifest_handle as handle:
                    json.dump({"key": key, "size": os.path.getsize(target), "sha256": _sha256(target),
                               "cacheVersion": CACHE_VERSION}, handle, ensure_ascii=False, separators=(",", ":"))
                os.replace(manifest_tmp, manifest_path_for(key))
            finally:
                try:
                    os.remove(manifest_tmp)
                except OSError:
                    pass
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass
    return target
