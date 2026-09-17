"""Analysis executor with explicit v3 compatibility and v4 canonical gates.

- 依 job 綁定的 provider 快照（ai_provider_id/model/config_version）執行，不依賴 env。
- 結果正規化成 schema v3 artifact，以 analysis record identity 儲存。
- source hash 相符後才保存 ready；ready artifact 再獨立投影 roster。
"""
import json
import logging

from .. import analyzer, db, pipeline
from .. import v4_contracts as c
from . import analysis as analysis_svc
from . import analysis_version_policy as version_policy
from .analysis_partials import AnalysisPartialSession, PartialCacheError
from . import ai_provider as ai_provider_svc, ai_request_profile

_log = logging.getLogger("analysis_executor")


class ProviderUnavailableError(RuntimeError):
    """沒有可用的 AI provider 或已停用。"""


def resolve_provider(job: dict) -> dict:
    """以 job 綁定的 provider 優先；否則用目前 default。都不存在則拋出明確錯誤。"""
    if job.get("ai_provider_id"):
        provider = db.get_ai_provider(job["ai_provider_id"])
        if provider and provider.get("enabled"):
            return provider
        raise ProviderUnavailableError("已綁定的 AI provider 已停用或不存在；請明確建立新的生成操作")
    provider = db.get_default_ai_provider()
    if provider:
        return provider
    raise ProviderUnavailableError("尚未設定可用的 AI provider，無法執行分析")


def _progress_callback(analysis_id: int, job_id: int | None = None, claim_token: str | None = None):
    def callback(event: dict):
        try:
            metrics = {
                "cacheHitChunks": event.get("cache_hit_chunks", 0),
                "requestedChunks": event.get("requested_chunks", 0),
                "providerRequestCount": event.get("provider_request_count", 0),
                "reusedChunks": event.get("reused_chunks", 0),
                "chunkRetryCount": event.get("chunk_retry_count", 0),
                "savedProviderRequests": event.get("saved_provider_requests", 0),
                "totalDuration": event.get("total_duration", 0.0),
                "usageMetrics": event.get("usage_metrics") or event.get("usageMetrics") or {},
            }
            structured_event_fields = {
                "structuredModeRequested": "structured_mode_requested",
                "structuredModeApplied": "structured_mode_applied",
                "structuredSchemaVersion": "structured_schema_version",
                "structuredSchemaHash": "structured_schema_hash",
                "structuredFallbackReason": "structured_fallback_reason",
                "structuredCapabilitySource": "structured_capability_source",
                "structuredRepairCount": "structured_repair_count",
            }
            for output_key, event_key in structured_event_fields.items():
                if event_key in event:
                    metrics[output_key] = event[event_key]
            analysis_svc.update_analysis_progress(
                analysis_id,
                stage=event.get("stage"),
                total_chunks=event.get("total_chunks"),
                completed_chunks=event.get("completed_chunks"),
                running_chunks=event.get("running_chunks"),
                retry_count=event.get("retry_count"),
                metrics=metrics,
            )
            if job_id:
                total = event.get("total_chunks") or 0
                completed = event.get("completed_chunks") or 0
                progress = int(completed * 95 / total) if total else None
                if progress is not None:
                    db.update_generation_job(job_id, "running", progress, claim_token=claim_token)
            if job_id and metrics.get("usageMetrics"):
                db.update_generation_job_usage_metrics(job_id, metrics["usageMetrics"], claim_token=claim_token)
        except Exception as exc:  # noqa: BLE001 — progress 不得中斷分析主流程
            _log.warning("analysis progress update failed id=%s: %s", analysis_id, exc)
    return callback


def _initialize_progress(analysis_id: int, text: str, attempts: int = 1, *, schema_version: int = 4):
    total = (analyzer.count_source_faithful_chunks(text)
             if version_policy.is_native(schema_version) else analyzer.count_analysis_chunks(text))
    analysis_svc.initialize_analysis_progress(analysis_id, total)
    if attempts > 1:
        analysis_svc.update_analysis_progress(analysis_id, retry_count=attempts - 1)


def _partial_session(*, analysis_id: int, row: dict, chapter: dict, provider: dict, record: dict | None = None):
    record = record or {}
    profile = ai_request_profile.resolve(provider)
    return AnalysisPartialSession(
        analysis_id=analysis_id, book_id=row["id"], chapter_id=chapter["id"],
        source_text_hash=record.get("source_text_hash") or chapter["text_hash"],
        prompt_version=record.get("prompt_version") or analyzer.ANALYSIS_PROMPT_VERSION,
        schema_version=int(record.get("schema_version") or version_policy.CANONICAL_WRITE_VERSION),
        analysis_profile=record.get("analysis_profile") or "speaker",
        provider=provider, language=row.get("category") or "", category=row.get("category") or "",
        request_profile_version=profile.version, request_profile_hash=profile.hash,
        source_hash_getter=lambda: (db.get_chapter(row["id"], chapter["seq"]) or {}).get("text_hash"),
    )


def run_speaker_analysis(job: dict) -> dict:
    """執行單章 speaker analysis job，回傳 analysis record。"""
    payload = json.loads(job.get("payload") or "{}")
    bid = payload.get("bid")
    seq = payload.get("seq")
    if bid is None or seq is None:
        raise RuntimeError("analysis job 缺少 bid/seq")

    row = db.get_book_row(bid)
    if not row:
        raise RuntimeError("找不到書")
    ch = db.get_chapter(row["id"], int(seq))
    if not ch:
        raise RuntimeError("找不到章節")

    provider = resolve_provider(job)
    analysis_id = job.get("analysis_id")
    record = db.get_chapter_analysis(analysis_id) if analysis_id else None
    if not record:
        analysis_id = analysis_svc.create_analysis_row(
            book_id=row["id"], chapter_id=ch["id"], source_text_hash=ch["text_hash"],
            analysis_profile="speaker", prompt_version=analyzer.ANALYSIS_PROMPT_VERSION,
            ai_provider_id=provider["id"], ai_model=provider["model"],
            ai_config_version=provider["config_version"],
            created_by=job.get("requested_by"), schema_version=4,
        )
        record = db.get_chapter_analysis(analysis_id)
    try:
        schema_version, _ = version_policy.require_version(
            record.get("schema_version"), context="analysis record")
    except version_policy.AnalysisVersionError as exc:
        raise RuntimeError(str(exc)) from exc
    _initialize_progress(analysis_id, ch["text"], int(job.get("attempts") or 1),
                         schema_version=schema_version)
    progress_callback = _progress_callback(analysis_id, job_id=job.get("id"), claim_token=job.get("worker_claim_token"))
    # 以 analysis record 建立時快照的 source_text_hash 為準（= enqueue 時的文字）
    source_hash = record["source_text_hash"]
    analysis_svc.raise_if_cancelled(analysis_id)
    db.mark_analysis_running_if_queued(analysis_id)
    analysis_svc.raise_if_cancelled(analysis_id)
    partial_cache = _partial_session(analysis_id=analysis_id, row=row, chapter=ch,
                                     provider=provider, record=record)

    try:
        analyzer._bind_provider(provider)
        d = pipeline.build_book_dict(row)
        if version_policy.is_native(schema_version):
            result = analyzer.analyze_source_faithful(
                ch["text"], on_progress=progress_callback, partial_cache=partial_cache,
                book=d,
            )
        else:
            result = analyzer.analyze_chapter(d, int(seq), ch["text"], on_progress=progress_callback,
                                              partial_cache=partial_cache)
        analysis_svc.raise_if_cancelled(analysis_id)
        result_metrics = dict(result.get("metrics") or {})
        if "repairCount" in result_metrics:
            result_metrics["structuredRepairCount"] = result_metrics["repairCount"]
        if result.get("structuredOutput"):
            structured = result["structuredOutput"]
            result_metrics.update({
                "structuredModeRequested": structured.get("requestedMode"),
                "structuredModeApplied": structured.get("appliedMode"),
                "structuredSchemaVersion": structured.get("schemaVersion"),
                "structuredSchemaHash": structured.get("schemaHash"),
                "structuredFallbackReason": structured.get("fallbackReason"),
                "structuredCapabilitySource": structured.get("capabilitySource"),
            })
        if job.get("id") and result_metrics.get("usageMetrics"):
            db.update_generation_job_usage_metrics(job["id"], result_metrics["usageMetrics"], claim_token=job.get("worker_claim_token"))
        result_metrics["jobRetryCount"] = max(0, int(job.get("attempts") or 1) - 1)
        existing_progress = analysis_svc.parse_analysis_progress(
            db.get_chapter_analysis(analysis_id).get("progress_json"))
        chunk_retry_count = result_metrics.get("chunkRetryCount", existing_progress.get("retryCount", 0))
        analysis_svc.update_analysis_progress(
            analysis_id,
            total_chunks=result_metrics.get("totalChunks"),
            completed_chunks=result_metrics.get("completedChunks"),
            running_chunks=result_metrics.get("runningChunks", 0),
            retry_count=chunk_retry_count + result_metrics["jobRetryCount"],
            metrics=result_metrics)
        if (db.get_chapter(row["id"], int(seq)) or {}).get("text_hash") != source_hash:
            raise analysis_svc.AnalysisSourceChangedError("章節來源在 aggregate 前已變更")
        analysis_svc.raise_if_cancelled(analysis_id)
        if version_policy.is_native(schema_version):
            artifact = result
            analysis_svc.update_analysis_progress(analysis_id, stage="validating")
        else:
            raw = {"speakers": result.get("speakers", []), "segments": result.get("segments", [])}
            analysis_svc.update_analysis_progress(analysis_id, stage="normalizing")
            artifact = analysis_svc.normalize_analysis(
                raw, chapter_key=ch["chapter_key"], source_text_hash=source_hash,
                source_text=ch["text"], book_id=bid,
                analysis_id=analysis_id, chapter_id=ch["id"],
                on_stage=lambda stage: analysis_svc.update_analysis_progress(analysis_id, stage=stage))
        analysis_svc.update_analysis_progress(analysis_id, stage="saving")
        analysis_svc.raise_if_cancelled(analysis_id)
        if version_policy.is_native(schema_version):
            analysis_svc.save_ready_source_faithful_analysis(analysis_id, artifact, ch["text"])
        else:
            analysis_svc.save_ready_analysis(analysis_id, artifact)
    except analysis_svc.AnalysisSourceChangedError:
        return db.get_chapter_analysis(analysis_id)
    except PartialCacheError as exc:
        if "source changed" in str(exc).lower():
            error = analysis_svc.AnalysisSourceChangedError(str(exc))
            analysis_svc.mark_analysis_progress_failed(analysis_id, error)
            db.mark_analysis_failed(analysis_id, analysis_svc.build_failure_error(error))
            return db.get_chapter_analysis(analysis_id)
        raise
    finally:
        analyzer._bind_provider(None)

    # ready artifact 是 durable boundary；projection 失敗時可由 retry_roster_projection 重試。
    analysis_svc.raise_if_cancelled(analysis_id)
    analysis_svc.update_analysis_progress(analysis_id, stage="deriving_roster")
    try:
        analysis_svc.retry_roster_projection(analysis_id)
    except Exception as exc:  # noqa: BLE001 — 不讓 ready artifact 因 projection 失敗遺失
        _log.warning("roster projection failed after ready analysis %s: %s", analysis_id, exc)
        analysis_svc.update_analysis_progress(analysis_id, last_error=exc)
    else:
        analysis_svc.update_analysis_progress(analysis_id, stage="ready", last_error=None)
    return db.get_chapter_analysis(analysis_id)


def run_speaker_analysis_all(job: dict) -> dict:
    """批次分析全書：逐章建立 analysis record 並執行。"""
    payload = json.loads(job.get("payload") or "{}")
    bid = payload.get("bid")
    if not bid:
        raise RuntimeError("analysis_all job 缺少 bid")
    row = db.get_book_row(bid)
    if not row:
        raise RuntimeError("找不到書")
    provider = resolve_provider(job)
    try:
        requested_schema_version = version_policy.require_execution_target(
            payload.get("schemaVersion") or version_policy.CANONICAL_WRITE_VERSION,
            context="analysis batch schemaVersion")
    except version_policy.AnalysisVersionError as exc:
        raise RuntimeError(str(exc)) from exc
    ids = []
    for ch in db.list_chapters(row["id"]):
        if job.get("id") and (db.get_generation_job(job["id"]) or {}).get("status") == "cancelled":
            break
        aid = analysis_svc.create_analysis_row(
            book_id=row["id"], chapter_id=ch["id"], source_text_hash=ch["text_hash"],
            analysis_profile="speaker", prompt_version=analyzer.ANALYSIS_PROMPT_VERSION,
            ai_provider_id=provider["id"], ai_model=provider["model"],
            ai_config_version=provider["config_version"],
            created_by=job.get("requested_by"), schema_version=requested_schema_version,
            batch_job_id=job.get("id"),
        )
        _initialize_progress(aid, ch["text"], int(job.get("attempts") or 1), schema_version=requested_schema_version)
        progress_callback = _progress_callback(
            aid, job_id=job.get("id"), claim_token=job.get("worker_claim_token"))
        source_hash = ch["text_hash"]
        db.update_chapter_analysis(aid, {"status": "running"})
        partial_cache = _partial_session(
            analysis_id=aid, row=row, chapter=ch, provider=provider,
            record=db.get_chapter_analysis(aid))
        try:
            analysis_svc.raise_if_batch_cancelled(job.get("id"))
            analyzer._bind_provider(provider)
            # 每章以「最新」書列重建 dict，讓書級名冊跨章累積
            row = db.get_book_row(bid)
            d = pipeline.build_book_dict(row)
            if version_policy.is_native(db.get_chapter_analysis(aid).get("schema_version")):
                result = analyzer.analyze_source_faithful(
                    ch["text"], on_progress=progress_callback,
                    partial_cache=partial_cache, book=d,
                )
            else:
                result = analyzer.analyze_chapter(
                    d, int(ch["seq"]), ch["text"], on_progress=progress_callback,
                    partial_cache=partial_cache)
            analysis_svc.raise_if_cancelled(aid)
            analysis_svc.raise_if_batch_cancelled(job.get("id"))
            result_metrics = dict(result.get("metrics") or {})
            if "repairCount" in result_metrics:
                result_metrics["structuredRepairCount"] = result_metrics["repairCount"]
            if result.get("structuredOutput"):
                structured = result["structuredOutput"]
                result_metrics.update({
                    "structuredModeRequested": structured.get("requestedMode"),
                    "structuredModeApplied": structured.get("appliedMode"),
                    "structuredSchemaVersion": structured.get("schemaVersion"),
                    "structuredSchemaHash": structured.get("schemaHash"),
                    "structuredFallbackReason": structured.get("fallbackReason"),
                    "structuredCapabilitySource": structured.get("capabilitySource"),
                })
            if job.get("id") and result_metrics.get("usageMetrics"):
                db.update_generation_job_usage_metrics(
                    job["id"], result_metrics["usageMetrics"],
                    claim_token=job.get("worker_claim_token"))
            result_metrics["jobRetryCount"] = max(0, int(job.get("attempts") or 1) - 1)
            existing_progress = analysis_svc.parse_analysis_progress(
                db.get_chapter_analysis(aid).get("progress_json"))
            chunk_retry_count = result_metrics.get("chunkRetryCount", existing_progress.get("retryCount", 0))
            analysis_svc.update_analysis_progress(
                aid,
                total_chunks=result_metrics.get("totalChunks"),
                completed_chunks=result_metrics.get("completedChunks"),
                running_chunks=result_metrics.get("runningChunks", 0),
                retry_count=chunk_retry_count + result_metrics["jobRetryCount"],
                metrics=result_metrics)
            if (db.get_chapter(row["id"], int(ch["seq"])) or {}).get("text_hash") != source_hash:
                raise analysis_svc.AnalysisSourceChangedError("章節來源在 aggregate 前已變更")
            if version_policy.is_native(db.get_chapter_analysis(aid).get("schema_version")):
                artifact = result
                analysis_svc.update_analysis_progress(aid, stage="validating")
            else:
                raw = {"speakers": result.get("speakers", []), "segments": result.get("segments", [])}
                analysis_svc.update_analysis_progress(aid, stage="normalizing")
                artifact = analysis_svc.normalize_analysis(
                    raw, chapter_key=ch["chapter_key"], source_text_hash=source_hash,
                    source_text=ch["text"], book_id=bid,
                    analysis_id=aid, chapter_id=ch["id"],
                    on_stage=lambda stage: analysis_svc.update_analysis_progress(aid, stage=stage))
            analysis_svc.update_analysis_progress(aid, stage="saving")
            analysis_svc.raise_if_cancelled(aid)
            analysis_svc.raise_if_batch_cancelled(job.get("id"))
            if version_policy.is_native(db.get_chapter_analysis(aid).get("schema_version")):
                analysis_svc.save_ready_source_faithful_analysis(aid, artifact, ch["text"])
            else:
                analysis_svc.save_ready_analysis(aid, artifact)
        except Exception as exc:  # noqa: BLE001 — 單章失敗不中斷整批
            error = analysis_svc.build_failure_error(exc)
            analysis_svc.mark_analysis_progress_failed(aid, exc)
            db.mark_analysis_failed(aid, error)
            ids.append({"analysisId": aid, "status": "failed", "error": error})
            if getattr(exc, "code", "") in {"analysis_cancelled", "batch_analysis_cancelled"}:
                break
            continue
        finally:
            analyzer._bind_provider(None)
        analysis_svc.update_analysis_progress(aid, stage="deriving_roster")
        try:
            analysis_svc.retry_roster_projection(aid)
        except Exception as exc:  # noqa: BLE001 — ready artifact 可獨立 retry
            _log.warning("roster projection failed after ready analysis %s: %s", aid, exc)
            analysis_svc.update_analysis_progress(aid, last_error=exc)
        else:
            analysis_svc.update_analysis_progress(aid, stage="ready", last_error=None)
        ids.append({"analysisId": aid, "status": "ready"})
    return {"items": ids}
