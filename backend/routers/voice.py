"""語音清單與試聽。"""
import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .. import db, exceptions as exc, settings
from ..security import get_current_user
from ..services import distributed, tts_provider, expressive_tts
from .. import v4_contracts as c
from .. import tts, voices

router = APIRouter(prefix="/api", tags=["voice"])
_preview_hits = defaultdict(deque)
_preview_lock = threading.Lock()


def _allow_preview(request: Request, text: str):
    if len(text) > 3000:
        raise exc.bad_request("試聽文字最多 3000 字")
    client = request.client.host if request.client else "unknown"
    key = hashlib.sha256(f"{client}:{request.headers.get('user-agent', '')}".encode()).hexdigest()[:24]
    now = time.monotonic()
    if distributed.available():
        if not distributed.allow_window(key, 3, 60):
            raise exc.too_many_requests("試聽請求過於頻繁，請稍後再試")
        return
    with _preview_lock:
        hits = _preview_hits[key]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= 3:
            raise exc.too_many_requests("試聽請求過於頻繁，請稍後再試")
        hits.append(now)


@router.get("/voices")
def list_voices(user: Annotated[Optional[dict], Depends(get_current_user)]):
    # 公開讀者不需要知道底層語音 catalog；作者/管理員工作台才可取得選擇清單。
    if not user or user.get("role") not in ("author", "admin", "super_admin"):
        return {"voices": []}
    active_provider = db.get_active_tts_provider()
    if not active_provider:
        return {"voices": []}
    remote = [{"id": row["voice_id"], "name": row["name"], "lang": row["lang"],
               "languages": _voice_languages(row), "gender": row["gender"],
               "age": row.get("age"),
               "region": row.get("region") or "remote", "status": row.get("catalog_status", "available"),
               "enabled": bool(row.get("enabled", 1)),
               "expressiveCapability": json.loads(row.get("capabilities_json") or "{}")}
              for row in db.list_tts_provider_voices(active_provider["id"], include_unavailable=True)]
    return {"voices": remote}


def _voice_languages(row: dict) -> list[str]:
    try:
        languages = json.loads(row.get("languages_json") or "[]")
    except (TypeError, ValueError):
        languages = []
    if isinstance(languages, list):
        values = [str(value).strip() for value in languages if str(value).strip()]
        if values:
            return values
    return [row["lang"]] if row.get("lang") else []


@router.post("/preview-tts")
async def preview_tts(payload: dict, request: Request):
    text = (payload.get("text") or "").strip()
    voice = (payload.get("voice") or "").strip()
    if not text:
        raise exc.bad_request("缺少 text")
    if not voice:
        active = db.get_active_tts_provider()
        catalog = db.list_tts_provider_voices(active["id"]) if active else []
        voice = catalog[0]["voice_id"] if catalog else voices.ENGLISH_VOICE_DEFAULT
    try:
        expression = expressive_tts.resolve_preview(
            speaker_id=(payload.get("speakerId") or "speaker:preview"),
            voice_id=voice,
            emotion_value=payload.get("emotion") or c.EMOTION_NEUTRAL,
            intensity_value=payload.get("intensity", c.EXPRESSIVE_INTENSITY_DEFAULT),
            tone=payload.get("tone"), speaking_style=payload.get("speakingStyle"),
            capabilities=expressive_tts.capability_snapshot(db.get_active_tts_provider()),
            policy=payload.get("emotionPolicy") or c.EMOTION_POLICY_BEST_EFFORT,
        )
    except expressive_tts.ExpressiveResolutionError as error:
        raise exc.bad_request(str(error)) from error
    _allow_preview(request, text)
    provider = db.get_active_tts_provider()
    if provider and provider.get("enabled"):
        fd, path = tempfile.mkstemp(prefix="tts_preview_", suffix=".mp3")
        os.close(fd)
        try:
            if provider.get("adapter_key") == "cosyvoice_http":
                from ..services import tts_provider_v1
                request_id = payload.get("requestId") or f"preview-{uuid.uuid4().hex}"
                provider_request = {
                    "request_id": request_id, "text": text, "voice_id": voice,
                    "language": payload.get("language") or "zh-TW",
                    "emotion": expression["requestedExpression"]["emotion"],
                    "intensity": expression["requestedExpression"]["intensity"],
                    "tone": expression["requestedExpression"].get("tone"),
                    "speaking_style": expression["requestedExpression"].get("speakingStyle"),
                    "mode": payload.get("emotionPolicy") or c.EMOTION_POLICY_BEST_EFFORT,
                }
                metadata = await asyncio.to_thread(tts_provider_v1.preview_to_file, provider, provider_request, path)
            else:
                metadata = {}
                await asyncio.to_thread(tts_provider.synthesize_to_file, provider, text, voice, path)
            with open(path, "rb") as handle:
                data = handle.read()
            headers = {
                "X-StoryLingo-Requested-Emotion": expression["requestedExpression"]["emotion"],
                "X-StoryLingo-Applied-Emotion": expression["appliedExpression"]["emotion"] or "",
                "X-StoryLingo-Fallback-Reason": expression["fallbackReason"] or "",
            }
            if metadata:
                headers.update({
                    "X-StoryLingo-Request-ID": metadata.get("requestId", ""),
                    "X-StoryLingo-Applied-Intensity": str(metadata.get("appliedIntensity") or provider_request.get("intensity", "")),
                    "X-StoryLingo-Applied-Tone": str(metadata.get("appliedTone") or provider_request.get("tone") or ""),
                    "X-StoryLingo-Applied-Style": str(metadata.get("appliedStyle") or provider_request.get("speaking_style") or ""),
                    "X-StoryLingo-Ignored-Parameters": ",".join(metadata.get("ignoredParameters", [])),
                    "X-StoryLingo-Duration": str(metadata.get("duration") or ""),
                    "X-StoryLingo-Duration-Status": metadata.get("durationStatus", "unavailable"),
                })
            content_type = str(metadata.get("contentType") or "audio/mpeg").split(";", 1)[0].strip().lower()
            if not content_type.startswith("audio/"):
                content_type = "audio/mpeg"
            return Response(content=data, media_type=content_type, headers=headers)
        except Exception as error:
            if provider.get("adapter_key") == "cosyvoice_http":
                from ..services.tts_provider_v1 import ProviderV1Error
                if isinstance(error, ProviderV1Error):
                    raise HTTPException(error.status, detail={"error": {"code": error.code, "message": str(error),
                                                                       "retryable": error.retryable, "request_id": error.request_id}}) from error
            raise
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    if False:
        raise exc.bad_request("目前尚未設定正式遠端 TTS provider")
    raise exc.bad_request("目前沒有可用的 F5 語者或啟用中的遠端 TTS provider")
    if not data:
        raise exc.bad_request("Edge TTS 暫時無回應，請稍後再試")
    return Response(content=data, media_type="audio/mpeg")
