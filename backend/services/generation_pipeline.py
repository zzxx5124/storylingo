"""V4 Generation pipeline：canonical single / multi audio jobs。

- 建立／重用 audio_generation（deterministic key）。
- 以 TTS adapter 合成到 generation-specific 路徑。
- 輸出驗證後才 atomic 啟動（active pointer）。
- multi 需要 matching ready analysis。
- batch 以 persisted child jobs 支援 partial success。
"""
import json
import logging
import os
import inspect
import tempfile

from .. import db, pipeline, storage, tts
from .. import v4_contracts as c
from . import audio_generation as ag
from . import tts_adapter
from . import analysis as analysis_svc
from . import expressive_tts

_log = logging.getLogger("generation_pipeline")


class GenerationUnavailableError(RuntimeError):
    """無可用的 TTS provider 或必要前置條件不足。"""


def _generate_audio_with_metadata(book, chapter, analysis, audio_path, timing_path, metadata, on_progress=None, provider=None):
    kwargs = {"final_audio_path": audio_path, "final_timing_path": timing_path}
    parameters = inspect.signature(tts.generate_chapter_audio).parameters
    if "generation_metadata" in parameters:
        kwargs["generation_metadata"] = metadata
    if "on_progress" in parameters:
        kwargs["on_progress"] = on_progress
    if "provider" in parameters:
        kwargs["provider"] = provider
    return tts.generate_chapter_audio(book, chapter, analysis, **kwargs)


def _generation_progress_callback(job: dict):
    """Persist real synthesis-unit progress when called from a queued job."""
    job_id = job.get("id")

    def update(done: int, total: int):
        if job_id is None:
            return
        db.update_generation_job(job_id, "running", int(done * 95 / max(1, total)),
                                 claim_token=job.get("worker_claim_token"))

    return update


def _resolve_tts_provider(job: dict | None = None):
    if job and job.get("provider_id"):
        provider = db.get_tts_provider(job["provider_id"])
        if not provider or not provider.get("enabled"):
            raise GenerationUnavailableError("已綁定的 TTS provider 已停用或不存在；請明確建立新的生成操作")
    else:
        provider = db.get_active_tts_provider()
    if not provider:
        raise GenerationUnavailableError("尚未設定可用的 TTS provider，無法生成音訊")
    return provider


def _ensure_source_revision_current(job: dict, row: dict, chapter: dict, payload: dict) -> None:
    """Reject an operation whose snapshotted source is no longer current."""
    expected = job.get("source_revision") or ""
    if not expected:
        return
    if job.get("job_type") == "audio_generate_all" or not job.get("chapter_id"):
        from .content_requests import calculate_revision
        request_payload = payload.get("requestPayload") if isinstance(payload, dict) else None
        actual = calculate_revision(row, "audiobook", request_payload if isinstance(request_payload, dict) else {})
    else:
        actual = chapter.get("text_hash") or ""
    if actual != expected:
        raise ag.GenerationConflictError("source revision 已變更，generation 必須重建")


def _build_book_dict(row):
    d = pipeline.build_book_dict(row)
    # generation 需要 integer book id 與 V4 audio 設定
    d["id"] = row["id"]
    d["audio_mode"] = row.get("audio_mode") or c.AUDIO_MODE_SINGLE
    d["default_voice_id"] = row.get("default_voice_id")
    d["audio_settings_version"] = row.get("audio_settings_version") or 1
    return d


def _effective_analysis(book: dict, chapter: dict, mode: str):
    """multi 需要 matching ready analysis；single 不需要。"""
    if mode != c.AUDIO_MODE_MULTI:
        return None, None
    record = db.get_ready_chapter_analysis(chapter["id"], chapter["text_hash"])
    if not record or not record.get("artifact_path"):
        raise GenerationUnavailableError("multi 模式需要相符的 ready analysis")
    p = os.path.join(settings_path(), record["artifact_path"])
    if not os.path.exists(p):
        raise GenerationUnavailableError("analysis artifact 不存在")
    with open(p, "r", encoding="utf-8") as f:
        artifact = json.load(f)
    return record, analysis_svc.legacy_compatibility_view(artifact)


def build_expressive_snapshot(book: dict, chapter: dict, provider: dict, policy: str) -> dict:
    """Resolve all segment expression inputs before creating the deterministic key."""
    record, artifact = _effective_analysis(book, chapter, c.AUDIO_MODE_MULTI)
    profiles = expressive_tts.profiles_for_book(book["id"])
    capabilities = expressive_tts.capability_snapshot(provider)
    speaker_voices = dict(book.get("voices") or {})
    for segment in artifact.get("segments", []):
        if segment.get("speaker_id") and segment.get("speaker_surface") in speaker_voices:
            speaker_voices[segment["speaker_id"]] = speaker_voices[segment["speaker_surface"]]
    resolved = expressive_tts.resolve_segments(
        artifact.get("segments", []), speaker_voices=speaker_voices,
        profiles=profiles, capabilities=capabilities, policy=policy,
    )
    snapshot_hash = __import__("hashlib").sha256(
        json.dumps(resolved, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "snapshotHash": snapshot_hash,
        "capabilitySnapshotVersion": capabilities.get("snapshotVersion"),
        "capabilityHash": capabilities.get("hash"),
        "profileVersions": sorted({f"{p['profile_id']}:{int(p['version'])}" for p in profiles}),
        "policy": policy,
    }


def settings_path():
    from .. import settings
    return settings.ROOT_DIR


def validate_output(audio_path: str, timing_path: str) -> bool:
    """輸出驗證：音訊與 timing 檔存在且非空。"""
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 0:
        return False
    if not os.path.exists(timing_path) or os.path.getsize(timing_path) <= 0:
        return False
    return True


def _generate_and_finalize(book, chapter, analysis, audio_path, timing_path, metadata, *, on_progress=None, provider=None):
    """Render into same-directory temp files, validate, then atomically promote."""
    os.makedirs(os.path.dirname(audio_path), exist_ok=True)
    os.makedirs(os.path.dirname(timing_path), exist_ok=True)
    audio_tmp = tempfile.NamedTemporaryFile(prefix=".generation-", suffix=".mp3",
                                             dir=os.path.dirname(audio_path), delete=False)
    timing_tmp = tempfile.NamedTemporaryFile(prefix=".generation-", suffix=".json",
                                              dir=os.path.dirname(timing_path), delete=False)
    audio_tmp.close()
    timing_tmp.close()
    try:
        _generate_audio_with_metadata(book, chapter, analysis, audio_tmp.name, timing_tmp.name, metadata,
                                      on_progress=on_progress, provider=provider)
        if not validate_output(audio_tmp.name, timing_tmp.name):
            raise GenerationUnavailableError("輸出驗證失敗")
        os.replace(audio_tmp.name, audio_path)
        os.replace(timing_tmp.name, timing_path)
    finally:
        for path in (audio_tmp.name, timing_tmp.name):
            try:
                os.remove(path)
            except OSError:
                pass


def create_job(book: dict, chapter: dict, *, mode: str = None, voice_id: str = None,
               requested_by: int = None, tts_provider: dict = None) -> dict:
    """建立或重用 audio_generation，回傳 (record, created)。"""
    mode = mode or ag.effective_mode(book, chapter)
    tts_provider = tts_provider or _resolve_tts_provider()
    record, created = ag.create_or_reuse_generation(
        book=book, chapter=chapter, source_text_hash=chapter["text_hash"],
        mode=mode, voice_id=voice_id, analysis_id=None, tts_provider=tts_provider,
        emotion_policy=c.EMOTION_POLICY_BEST_EFFORT, requested_by=requested_by,
    )
    return record


def run_single_generation(job: dict) -> dict:
    """執行 audio_single job（不需 AI provider）。"""
    payload = json.loads(job.get("payload") or "{}")
    bid = payload.get("bid")
    seq = payload.get("seq")
    generation_id = payload.get("generationId")
    row = db.get_book_row(bid)
    if not row:
        raise GenerationUnavailableError("找不到書")
    ch = db.get_chapter(row["id"], int(seq))
    if not ch:
        raise GenerationUnavailableError("找不到章節")
    _ensure_source_revision_current(job, row, ch, payload)

    generation = db.get_audio_generation(generation_id) if generation_id else None
    provider = _resolve_tts_provider(job)
    if not generation:
        book = _build_book_dict(row)
        generation, _ = ag.create_or_reuse_generation(
            book=book, chapter=ch, source_text_hash=ch["text_hash"],
            mode=c.AUDIO_MODE_SINGLE, voice_id=None, analysis_id=None,
            tts_provider=provider, emotion_policy=c.EMOTION_POLICY_BEST_EFFORT,
            requested_by=job.get("requested_by"),
        )
    if generation["status"] == "ready":
        return generation

    book = _build_book_dict(row)
    # single 模式：以 V4 聲線設定（章節覆寫優先於書級 default_voice_id）注入旁白聲線
    voice_id = ag.effective_voice_id(book, ch)
    if voice_id:
        book["voices"] = {**(book.get("voices") or {}), "旁白": voice_id}
    analysis = {"segments": [{"type": "narration", "speaker": "旁白",
                              "text": ch["text"], "emotion": None}]}
    audio_rel = storage.generation_audio_path(bid, generation["id"])
    timing_rel = storage.generation_timing_path(bid, generation["id"])
    abs_audio = os.path.join(settings_path(), audio_rel)
    abs_timing = os.path.join(settings_path(), timing_rel)
    db.update_audio_generation(generation["id"], {"status": "running"})
    try:
        provider_metadata = []
        _generate_and_finalize(book, ch, analysis, abs_audio, abs_timing, provider_metadata,
                               on_progress=_generation_progress_callback(job), provider=provider)
        if provider_metadata:
            db.update_audio_generation(generation["id"], {
                "emotion_application_summary_json": json.dumps(provider_metadata, ensure_ascii=False),
            })
    except Exception as exc:  # noqa: BLE001
        db.mark_generation_failed(generation["id"], tts_adapter.error_envelope(exc))
        raise
    # atomic 啟動：僅 ready 且文字相符才更新 active pointer
    ag.mark_generation_ready_and_activate(book=book, chapter=ch, generation_id=generation["id"],
                                           audio_path=audio_rel, timing_path=timing_rel,
                                           job_id=job.get("id"), claim_token=job.get("worker_claim_token"))
    return db.get_audio_generation(generation["id"])


def run_multi_generation(job: dict) -> dict:
    """執行 audio_multi job（需要 matching ready analysis）。"""
    payload = json.loads(job.get("payload") or "{}")
    bid = payload.get("bid")
    seq = payload.get("seq")
    generation_id = payload.get("generationId")
    row = db.get_book_row(bid)
    if not row:
        raise GenerationUnavailableError("找不到書")
    ch = db.get_chapter(row["id"], int(seq))
    if not ch:
        raise GenerationUnavailableError("找不到章節")
    _ensure_source_revision_current(job, row, ch, payload)

    provider = _resolve_tts_provider(job)
    book = _build_book_dict(row)
    record, artifact = _effective_analysis(book, ch, c.AUDIO_MODE_MULTI)
    if not record:
        raise GenerationUnavailableError("multi 模式需要相符的 ready analysis")

    generation = db.get_audio_generation(generation_id) if generation_id else None
    requested_policy = (generation or {}).get("emotion_policy") or payload.get("emotionPolicy") or c.EMOTION_POLICY_BEST_EFFORT
    profiles = expressive_tts.profiles_for_book(book["id"])
    capabilities = expressive_tts.capability_snapshot(provider)
    speaker_voices = dict(book.get("voices") or {})
    for segment in artifact.get("segments", []):
        if segment.get("speaker_id") and segment.get("speaker_surface") in speaker_voices:
            speaker_voices[segment["speaker_id"]] = speaker_voices[segment["speaker_surface"]]
    expression_snapshot = expressive_tts.resolve_segments(
        artifact.get("segments", []), speaker_voices=speaker_voices,
        profiles=profiles, capabilities=capabilities,
        policy=requested_policy,
    )
    expression_hash = __import__("hashlib").sha256(
        json.dumps(expression_snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    snapshot_for_key = {
        "snapshotHash": expression_hash,
        "capabilitySnapshotVersion": capabilities.get("snapshotVersion"),
        "capabilityHash": capabilities.get("hash"),
        "policy": requested_policy,
    }
    if not generation:
        generation, _ = ag.create_or_reuse_generation(
            book=book, chapter=ch, source_text_hash=ch["text_hash"],
            mode=c.AUDIO_MODE_MULTI, voice_id=None, analysis_id=record["id"],
            tts_provider=provider, emotion_policy=requested_policy,
            requested_by=job.get("requested_by"), expressive_snapshot=snapshot_for_key,
        )
    if generation["status"] == "ready":
        return generation

    audio_rel = storage.generation_audio_path(bid, generation["id"])
    timing_rel = storage.generation_timing_path(bid, generation["id"])
    abs_audio = os.path.join(settings_path(), audio_rel)
    abs_timing = os.path.join(settings_path(), timing_rel)
    db.update_audio_generation(generation["id"], {
        "status": "running",
        "emotion_application_summary_json": json.dumps(expression_snapshot, ensure_ascii=False),
        "capabilities_hash": capabilities.get("hash") or "",
        "emotion_schema_version": c.EXPRESSIVE_CONTRACT_VERSION,
    })
    try:
        provider_metadata = []
        _generate_and_finalize(book, ch, artifact, abs_audio, abs_timing, provider_metadata,
                               on_progress=_generation_progress_callback(job), provider=provider)
        if provider_metadata:
            db.update_audio_generation(generation["id"], {
                "emotion_application_summary_json": json.dumps(provider_metadata, ensure_ascii=False),
            })
    except Exception as exc:  # noqa: BLE001
        db.mark_generation_failed(generation["id"], tts_adapter.error_envelope(exc))
        raise
    ag.mark_generation_ready_and_activate(book=book, chapter=ch, generation_id=generation["id"],
                                           audio_path=audio_rel, timing_path=timing_rel,
                                           job_id=job.get("id"), claim_token=job.get("worker_claim_token"))
    return db.get_audio_generation(generation["id"])


def run_generate_all(job: dict) -> dict:
    """批次生成全書（partial success 支援）。"""
    payload = json.loads(job.get("payload") or "{}")
    bid = payload.get("bid")
    row = db.get_book_row(bid)
    if not row:
        raise GenerationUnavailableError("找不到書")
    provider = _resolve_tts_provider(job)
    book = _build_book_dict(row)
    results = []
    for ch in db.list_chapters(row["id"]):
        _ensure_source_revision_current(job, row, ch, payload)
        mode = ag.effective_mode(book, ch)
        try:
            if mode == c.AUDIO_MODE_MULTI:
                gen = run_multi_generation({**job, "payload": json.dumps({"bid": bid, "seq": ch["seq"],
                                                                             "requestPayload": payload.get("requestPayload", {})})})
            else:
                gen = run_single_generation({**job, "payload": json.dumps({"bid": bid, "seq": ch["seq"],
                                                                              "requestPayload": payload.get("requestPayload", {})})})
            results.append({"chapterSeq": ch["seq"], "generationId": gen["id"], "status": gen["status"]})
        except Exception as exc:  # noqa: BLE001 — partial success
            results.append({"chapterSeq": ch["seq"], "status": "failed", "error": str(exc)})
            continue
    if results and all(item.get("status") == "failed" for item in results):
        raise GenerationUnavailableError("本批沒有任何章節生成成功")
    return {"items": results}
