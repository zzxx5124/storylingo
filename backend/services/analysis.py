"""Analysis artifact persistence and compatibility boundaries.

- `normalize_analysis()` is an explicit legacy adapter that produces a
  validated v3 compatibility artifact.
- New source-faithful analysis is persisted only by the v4 writer below.
- emotion 為選用：缺省或值不合法時一律 fallback 成 neutral。
- malformed／oversized 輸入安全失敗（拋 AnalysisValidationError），不寫入任何狀態。
- artifact 以 analysis record identity 為儲存單位，不依賴 seq。
"""
import hashlib
import json
import os
import re
from datetime import datetime

from .. import db, settings, storage
from .. import v4_contracts as c
from . import source_faithful, usage_metrics
from . import analysis_version_policy as version_policy

MAX_SEGMENTS = 2000
MAX_TEXT_CHARS = 5000
MAX_SPEAKERS = 500
MAX_RAW_CHARS = 1_000_000
OFFSET_DIAGNOSTIC_TEXT_LIMIT = 240
OFFSET_DIAGNOSTIC_EXCERPT_LIMIT = 320
_DIALOGUE_QUOTE_PAIRS = {
    "「": "」", "『": "』", "“": "”", '"': '"',
}
_DIALOGUE_QUOTE_CLOSERS = set(_DIALOGUE_QUOTE_PAIRS.values())
_SOURCE_BOUNDARY_CHARS = set(" \t\r\n，。！？!?；;：:、…——\u2014\"「」『』“”()（）[]【】")
_TERMINAL_PUNCTUATION = set("。！？!?；;…")

ANALYSIS_PROGRESS_STAGES = (
    "queued", "analyzing", "normalizing", "validating", "saving",
    "deriving_roster", "ready", "failed",
)
_UNSET = object()


class AnalysisValidationError(ValueError):
    """分析輸出無法正規化為合法 artifact。"""


class AnalysisOffsetMismatchError(AnalysisValidationError):
    """segment 無法以嚴格 source slice 對應，附帶 bounded diagnostics。"""

    def __init__(self, message: str, diagnostics: dict):
        super().__init__(message)
        self.diagnostics = diagnostics


class AnalysisSourceChangedError(AnalysisValidationError):
    """分析期間章節文字變更，結果不得成為 ready artifact。"""


class AnalysisProviderTimeoutExhaustedError(RuntimeError):
    """單一 chunk 的 provider timeout budget 已耗盡，不應再重跑整章。"""

    code = "provider_timeout_exhausted"


class AnalysisCancelledError(RuntimeError):
    """作者已取消分析；不得把取消視為可自動重試的 provider failure。"""

    code = "analysis_cancelled"


class BatchAnalysisCancelledError(AnalysisCancelledError):
    """全書批次已由作者取消；不得再建立或保存後續章節結果。"""

    code = "batch_analysis_cancelled"


def build_failure_error(error, *, attempts: int | None = None) -> str:
    """把分析失敗保存成可供 API/workflow 使用的結構化錯誤。"""
    message = str(error or "分析失敗")[:1200]
    lowered = message.lower()
    error_code = str(getattr(error, "code", "") or "")
    if error_code == "analysis_cancelled":
        code, failure_type = "analysis_cancelled", "user_cancelled"
    elif error_code == "batch_analysis_cancelled":
        code, failure_type = "batch_analysis_cancelled", "user_cancelled"
    elif error_code == "provider_timeout_exhausted":
        code, failure_type = "provider_timeout_exhausted", "provider_request"
    elif error_code == "unsupported_parameter" or "unsupported parameter" in lowered:
        code, failure_type = "unsupported_parameter", "provider_request_configuration"
    elif error_code in {"annotation_coverage_incomplete", "annotation_coverage_low"}:
        code, failure_type = error_code, "structured_annotation"
    elif "expecting " in lowered or ("json" in lowered and "delimiter" in lowered):
        code, failure_type = "malformed_json", "provider_response"
    elif "timeout" in lowered or "timed out" in lowered:
        code, failure_type = "provider_timeout", "provider_request"
    elif isinstance(error, AnalysisSourceChangedError) or "source hash" in lowered:
        code, failure_type = "source_changed", "source_hash_gate"
    elif isinstance(error, AnalysisValidationError):
        code, failure_type = "analysis_validation_failed", "validation"
    else:
        code, failure_type = "analysis_failed", "provider_or_pipeline"
    payload = {"code": code, "failureType": failure_type, "message": message,
               "retryable": False}
    if attempts is not None:
        payload["attempts"] = int(attempts)
    diagnostics = getattr(error, "diagnostics", None)
    if isinstance(diagnostics, dict):
        payload["diagnostics"] = diagnostics
    return json.dumps(payload, ensure_ascii=False)


def parse_failure_error(value) -> dict:
    """讀取結構化分析錯誤；相容既有 plain-text error。"""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "")
        if isinstance(parsed, dict) and parsed.get("code"):
            return parsed
    except (TypeError, ValueError):
        pass
    return {"code": "analysis_failed", "failureType": "unknown",
            "message": str(value or "分析失敗"), "retryable": False}


def build_retry_error(error, *, attempts: int) -> str:
    return json.dumps({"code": "analysis_retrying", "failureType": "retry",
                       "message": "分析失敗，正在自動重試", "lastError": str(error or "")[:800],
                       "attempts": int(attempts), "retryable": True}, ensure_ascii=False)


def is_deterministic_failure(error) -> bool:
    """normalize/validation/source failures 不應由 job retry 無限重做相同 partial。"""
    return (
        isinstance(error, (AnalysisValidationError, AnalysisSourceChangedError))
        or getattr(error, "code", "") in {
            "annotation_coverage_incomplete",
            "annotation_coverage_low",
            "annotation_reference_invalid",
            "duplicate_segment_id",
            "unknown_segment_id",
            "invalid_segment_id",
            "structured_schema_invalid",
            "source_ownership_violation",
            "provider_timeout_exhausted",
            "unsupported_parameter",
            "analysis_cancelled",
            "batch_analysis_cancelled",
        }
        or "unsupported parameter" in str(error).lower()
    )


def safe_analysis_error(error) -> str:
    """給進度 read model 的產品化錯誤；不把 provider/raw error 送到前端。"""
    parsed = parse_failure_error(error) if not isinstance(error, Exception) else {}
    message_by_code = {
        "malformed_json": "AI 回應格式無效",
        "provider_timeout": "AI 服務逾時",
        "provider_timeout_exhausted": "AI 服務逾時，已停止重試",
        "source_changed": "章節內容已變更，請重新分析",
        "analysis_offset_mismatch": "分析結果無法對應原文",
        "analysis_validation_failed": "分析結果驗證失敗",
        "annotation_coverage_incomplete": "AI 分段標註不完整",
        "annotation_coverage_low": "AI 分段標註覆蓋率過低",
        "analysis_cancelled": "分析已取消",
        "batch_analysis_cancelled": "批次分析已取消",
    }
    code = parsed.get("code") or getattr(error, "code", "") or ""
    if code in message_by_code:
        return message_by_code[code]
    text = str(error or "")
    lowered = text.lower()
    if "expecting " in lowered or ("json" in lowered and "delimiter" in lowered):
        return message_by_code["malformed_json"]
    if "timeout" in text.lower() or "timed out" in text.lower():
        return message_by_code["provider_timeout"]
    if "offset" in lowered and "segment" in lowered:
        return message_by_code["analysis_offset_mismatch"]
    return "分析失敗"


def _publish_ready_artifact(analysis_id: int, rel: str, absolute: str) -> str:
    """以 analysis status CAS 發布 ready pointer，避免取消競態覆寫狀態。"""
    if db.mark_analysis_ready_if_running(analysis_id, rel):
        return rel
    try:
        os.remove(absolute)
    except FileNotFoundError:
        pass
    current = db.get_chapter_analysis(analysis_id) or {}
    failure = parse_failure_error(current.get("error"))
    if failure.get("code") == "analysis_cancelled":
        raise AnalysisCancelledError("分析已取消")
    raise AnalysisValidationError("分析狀態已變更，拒絕保存 ready artifact")


def default_analysis_progress(total_chunks: int = 0, *, stage: str = "queued") -> dict:
    total = max(0, int(total_chunks or 0))
    return {
        "totalChunks": total,
        "completedChunks": 0,
        "runningChunks": 0,
        "retryCount": 0,
        "currentStage": stage if stage in ANALYSIS_PROGRESS_STAGES else "queued",
        "progressPercent": 100 if stage == "ready" else 0,
        "lastError": None,
        "cacheHitChunks": 0,
        "requestedChunks": 0,
        "providerRequestCount": 0,
        "reusedChunks": 0,
        "chunkRetryCount": 0,
        "jobRetryCount": 0,
        "savedProviderRequests": 0,
        "totalDuration": 0.0,
        "structuredModeRequested": None,
        "structuredModeApplied": None,
        "structuredSchemaVersion": None,
        "structuredSchemaHash": None,
        "structuredFallbackReason": None,
        "structuredCapabilitySource": None,
        "structuredRepairCount": 0,
        "usageMetrics": usage_metrics.empty(),
        "legacyProgress": False,
    }


def parse_analysis_progress(record_or_value) -> dict:
    value = record_or_value
    legacy_progress = False
    if isinstance(record_or_value, dict) and "progress_json" in record_or_value:
        value = record_or_value.get("progress_json")
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except (TypeError, ValueError):
            value = {}
            legacy_progress = True
    if not isinstance(value, dict):
        value = {}
        legacy_progress = True
    if not value:
        legacy_progress = True
    out = default_analysis_progress()
    out.update({key: value[key] for key in out if key in value})
    out["totalChunks"] = max(0, int(out.get("totalChunks") or 0))
    out["completedChunks"] = max(0, min(out["totalChunks"], int(out.get("completedChunks") or 0)))
    out["runningChunks"] = max(0, int(out.get("runningChunks") or 0))
    out["retryCount"] = max(0, int(out.get("retryCount") or 0))
    if out["currentStage"] not in ANALYSIS_PROGRESS_STAGES:
        out["currentStage"] = "queued"
    out["progressPercent"] = max(0, min(100, int(out.get("progressPercent") or 0)))
    for key in ("cacheHitChunks", "requestedChunks", "providerRequestCount", "reusedChunks",
                 "chunkRetryCount", "jobRetryCount", "savedProviderRequests"):
        out[key] = max(0, int(out.get(key) or 0))
    out["structuredRepairCount"] = max(0, int(out.get("structuredRepairCount") or 0))
    out["totalDuration"] = max(0.0, float(out.get("totalDuration") or 0.0))
    out["usageMetrics"] = usage_metrics.merge({}, out.get("usageMetrics"))
    out["legacyProgress"] = bool(legacy_progress)
    if out["lastError"] is not None:
        out["lastError"] = str(out["lastError"])[:200]
    return out


def _progress_percent(stage: str, completed: int, total: int, previous: int) -> int:
    if stage == "ready":
        return 100
    if stage == "failed":
        return previous
    return round(completed / total * 100) if total else 0


def update_analysis_progress(analysis_id: int, *, stage: str | None = None,
                             total_chunks: int | None = None,
                             completed_chunks: int | None = None,
                             running_chunks: int | None = None,
                             retry_count: int | None = None,
                             metrics: dict | None = None,
                             last_error=_UNSET) -> dict:
    row = db.get_chapter_analysis(analysis_id)
    current = parse_analysis_progress(row or {})
    if stage is not None and stage in ANALYSIS_PROGRESS_STAGES:
        current["currentStage"] = stage
    if total_chunks is not None:
        current["totalChunks"] = max(0, int(total_chunks))
    if completed_chunks is not None:
        current["completedChunks"] = max(0, int(completed_chunks))
    if running_chunks is not None:
        current["runningChunks"] = max(0, int(running_chunks))
    if retry_count is not None:
        current["retryCount"] = max(0, int(retry_count))
    if metrics:
        for key in ("cacheHitChunks", "requestedChunks", "providerRequestCount", "reusedChunks",
                    "chunkRetryCount", "jobRetryCount", "savedProviderRequests", "totalDuration",
                    "structuredModeRequested", "structuredModeApplied", "structuredSchemaVersion",
                    "structuredSchemaHash", "structuredFallbackReason", "structuredCapabilitySource",
                    "structuredRepairCount", "usageMetrics"):
            if key in metrics:
                if key == "usageMetrics":
                    current[key] = usage_metrics.max_snapshot(current.get(key), metrics[key])
                else:
                    current[key] = metrics[key]
    if last_error is not _UNSET:
        current["lastError"] = safe_analysis_error(last_error) if last_error else None
    current = parse_analysis_progress(current)
    current["progressPercent"] = _progress_percent(
        current["currentStage"], current["completedChunks"], current["totalChunks"],
        current["progressPercent"],
    )
    db.update_chapter_analysis(analysis_id, {
        "progress_json": json.dumps(current, ensure_ascii=False),
        "usage_metrics_json": json.dumps(current["usageMetrics"], ensure_ascii=False,
                                           separators=(",", ":")),
    })
    return current


def initialize_analysis_progress(analysis_id: int, total_chunks: int) -> dict:
    return update_analysis_progress(analysis_id, stage="queued", total_chunks=total_chunks,
                                    completed_chunks=0, running_chunks=0, retry_count=0,
                                    last_error=None)


def mark_analysis_progress_failed(analysis_id: int, error) -> dict:
    return update_analysis_progress(analysis_id, stage="failed", running_chunks=0,
                                    last_error=error)


def reconcile_progress_from_partials(analysis_id: int) -> dict:
    """在 analyzer exception 後，從 partial metadata 補齊真實執行 metrics。"""
    current = parse_analysis_progress(db.get_chapter_analysis(analysis_id) or {})
    rows = db.list_analysis_partials(analysis_id=analysis_id)
    succeeded = sum(1 for row in rows if row.get("state") == "succeeded")
    running = sum(1 for row in rows if row.get("state") == "running")
    requested = sum(1 for row in rows if int(row.get("request_count") or 0) > 0)
    requests = sum(int(row.get("request_count") or 0) for row in rows)
    retries = sum(int(row.get("retry_count") or 0) for row in rows)
    usage = {}
    for row in rows:
        try:
            partial_usage = json.loads(row.get("usage_metrics_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            partial_usage = {}
        usage = usage_metrics.merge(usage, partial_usage)
    jobs = [job for job in db.list_generation_jobs(500)
            if job.get("analysis_id") == analysis_id
            and job.get("job_type") == "speaker_analysis"]
    job_retry_count = max(
        (max(0, int(job.get("attempts") or 0) - 1) for job in jobs),
        default=0,
    )

    # Failure callbacks can stop before the last progress event.  Partial rows
    # and terminal job history are the durable execution evidence, so they must
    # overwrite stale zero/one values in progress_json.
    metrics = {
        "requestedChunks": requested,
        "providerRequestCount": requests,
        "chunkRetryCount": retries,
        "jobRetryCount": job_retry_count,
        "usageMetrics": usage,
    }
    duration_candidates = [float(current.get("totalDuration") or 0.0)]
    for job in jobs:
        started = job.get("started_at")
        finished = job.get("finished_at")
        if not started or not finished:
            continue
        try:
            start_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            finish_dt = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            duration_candidates.append(max(0.0, (finish_dt - start_dt).total_seconds()))
        except (TypeError, ValueError):
            continue
    metrics["totalDuration"] = max(duration_candidates)
    return update_analysis_progress(analysis_id, completed_chunks=succeeded,
                                    running_chunks=running, metrics=metrics)


def converge_terminal_analysis(analysis_id: int) -> bool:
    """將已耗盡重試的 terminal job 與殘留 analysis 狀態收斂；不重新排隊。"""
    record = db.get_chapter_analysis(analysis_id)
    if not record or record.get("status") not in ("queued", "running"):
        return False
    jobs = [job for job in db.list_generation_jobs(500)
            if job.get("analysis_id") == analysis_id
            and job.get("job_type") == "speaker_analysis"]
    if not jobs:
        return False
    job = max(jobs, key=lambda item: item.get("id", 0))
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 3)
    if job.get("status") != "failed" or attempts < max_attempts:
        return False
    parsed = parse_failure_error(job.get("error"))
    cause = parsed.get("code") or "analysis_failed"
    safe_message = safe_analysis_error(job.get("error"))
    failure = json.dumps({
        "code": "analysis_retry_exhausted",
        "failureType": parsed.get("failureType") or "analysis_job",
        "message": safe_message,
        "cause": cause,
        "attempts": attempts,
        "retryable": False,
    }, ensure_ascii=False)
    mark_analysis_progress_failed(analysis_id, failure)
    db.mark_analysis_failed(analysis_id, failure)
    return True


def repair_orphan_analysis_job(job_id: int, *, allow_unexhausted: bool = False) -> bool:
    """Close a running job left by a lost worker; never requeue it.

    Manual/legacy repair keeps the original exhausted-attempt guard.  Startup
    recovery may explicitly close a job whose owner process is gone even when
    it had not reached the retry limit.
    """
    job = db.get_generation_job(job_id)
    if not job or job.get("job_type") != "speaker_analysis":
        return False
    analysis_id = job.get("analysis_id")
    record = db.get_chapter_analysis(analysis_id) if analysis_id else None
    if not record or job.get("status") != "running" or record.get("status") != "running":
        return False
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 3)
    if not allow_unexhausted and attempts < max_attempts:
        return False
    failure = {
        "code": "stale_job_claim",
        "failureType": "worker_lost",
        "message": "分析工作已中斷，請重新分析",
        "retryable": True,
        "autoRetry": False,
        "attempts": attempts,
    }
    failure_json = json.dumps(failure, ensure_ascii=False)
    progress = parse_analysis_progress(record)
    progress.update({
        "currentStage": "failed",
        "runningChunks": 0,
        "lastError": failure["message"],
    })
    progress = parse_analysis_progress(progress)
    progress["currentStage"] = "failed"
    progress["runningChunks"] = 0
    progress["lastError"] = failure["message"]
    progress_json = json.dumps(progress, ensure_ascii=False)
    return db.repair_orphan_analysis_job(
        job_id, analysis_id, failure_json, progress_json,
        require_exhausted=not allow_unexhausted,
    )


def recover_orphaned_analysis_jobs() -> list[int]:
    """Startup repair for analysis jobs whose worker process no longer exists."""
    recovered = []
    for job in db.list_orphaned_analysis_jobs():
        if job.get("job_type") == "speaker_analysis_all":
            if repair_orphan_analysis_batch_job(job["id"], allow_unexhausted=True):
                recovered.append(int(job["id"]))
        elif repair_orphan_analysis_job(job["id"], allow_unexhausted=True):
            recovered.append(int(job["id"]))
    return recovered


def repair_orphan_analysis_batch_job(job_id: int, *, allow_unexhausted: bool = False) -> bool:
    """Close a running full-book job left by a lost worker; never requeue it."""
    job = db.get_generation_job(job_id)
    if not job or job.get("job_type") != "speaker_analysis_all" or job.get("status") != "running":
        return False
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 3)
    if not allow_unexhausted and attempts < max_attempts:
        return False
    failure = {
        "code": "stale_job_claim",
        "failureType": "worker_lost",
        "message": "批次分析工作已中斷，請重新分析",
        "retryable": True,
        "autoRetry": False,
        "attempts": attempts,
    }
    failure_json = json.dumps(failure, ensure_ascii=False, separators=(",", ":"))
    child_failure = dict(failure)
    child_failure["code"] = "stale_job_claim"
    child_failure["batchJobId"] = job_id
    child_failure_json = json.dumps(child_failure, ensure_ascii=False, separators=(",", ":"))
    return db.repair_orphan_analysis_batch_job(
        job_id, failure_json, child_failure_json,
        require_exhausted=not allow_unexhausted,
    )


def cancel_analysis_job(job_id: int) -> bool:
    """Cancel an active analysis and converge its workflow without calling AI."""
    job = db.get_generation_job(job_id)
    if not job or job.get("job_type") != "speaker_analysis":
        return False
    analysis_id = job.get("analysis_id")
    record = db.get_chapter_analysis(analysis_id) if analysis_id else None
    if (not record or job.get("status") not in ("pending", "running")
            or record.get("status") not in ("queued", "running")):
        return False
    failure = {
        "code": "analysis_cancelled",
        "failureType": "user_cancelled",
        "message": "分析已取消",
        "retryable": True,
        "autoRetry": False,
        "newAnalysisRequired": True,
    }
    failure_json = json.dumps(failure, ensure_ascii=False, separators=(",", ":"))
    progress = parse_analysis_progress(record)
    progress.update({
        "currentStage": "failed",
        "runningChunks": 0,
        "lastError": failure["message"],
    })
    progress_json = json.dumps(parse_analysis_progress(progress), ensure_ascii=False)
    return db.cancel_analysis_job(job_id, analysis_id, failure_json, progress_json)


def cancel_analysis_batch_job(job_id: int) -> dict | None:
    """取消全書批次；只收斂由該 batch job 擁有的 active chapter analyses。"""
    job = db.get_generation_job(job_id)
    if not job or job.get("job_type") != "speaker_analysis_all":
        return None
    failure = {
        "code": "batch_analysis_cancelled",
        "failureType": "user_cancelled",
        "message": "批次分析已取消",
        "retryable": True,
        "autoRetry": False,
        "newAnalysisRequired": True,
    }
    job_failure_json = json.dumps(failure, ensure_ascii=False, separators=(",", ":"))
    child_failure = dict(failure)
    child_failure["code"] = "analysis_cancelled"
    child_failure["batchJobId"] = job_id
    analysis_failure_json = json.dumps(child_failure, ensure_ascii=False, separators=(",", ":"))
    return db.cancel_analysis_batch_job(job_id, job_failure_json, analysis_failure_json)


def raise_if_cancelled(analysis_id: int) -> None:
    """讓 worker 在取消競態點放棄結果，不進入 ready/projection。"""
    record = db.get_chapter_analysis(analysis_id) or {}
    failure = parse_failure_error(record.get("error"))
    if failure.get("code") == "analysis_cancelled":
        raise AnalysisCancelledError("分析已取消")


def raise_if_batch_cancelled(job_id: int | None) -> None:
    """在批次章節邊界檢查主 job；不讓取消後建立下一個 chapter analysis。"""
    if not job_id:
        return
    job = db.get_generation_job(job_id)
    if job and job.get("status") == "cancelled":
        raise BatchAnalysisCancelledError("批次分析已取消")


CHARACTER_ID_PATTERN = re.compile(r"^char_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def is_valid_character_id(value: str) -> bool:
    return isinstance(value, str) and bool(CHARACTER_ID_PATTERN.fullmatch(value))


def _clean_text(value) -> str:
    if not isinstance(value, str):
        raise AnalysisValidationError("segment text 必須是字串")
    text = value.strip()
    if not text:
        raise AnalysisValidationError("segment text 不可為空")
    if len(text) > MAX_TEXT_CHARS:
        raise AnalysisValidationError(f"segment text 超過 {MAX_TEXT_CHARS} 字")
    return text


def _emotion(value, source_override: str | None = None) -> dict:
    """正規化 segment emotion；缺省或不合法一律 fallback neutral。"""
    neutral = {
        "label": c.EMOTION_NEUTRAL,
        "intensity": c.EXPRESSIVE_INTENSITY_DEFAULT,
        "source": c.EMOTION_SOURCE_FALLBACK,
    }
    if not isinstance(value, dict):
        return neutral
    raw_value = value.get("label", value.get("value", c.EMOTION_NEUTRAL))
    if raw_value not in c.CANONICAL_EMOTIONS:
        return neutral
    intensity = value.get("intensity", c.EXPRESSIVE_INTENSITY_DEFAULT)
    try:
        intensity = float(intensity)
    except (TypeError, ValueError):
        intensity = c.EXPRESSIVE_INTENSITY_DEFAULT
    intensity = max(0.0, min(1.0, intensity))
    source = source_override or c.EMOTION_SOURCE_AI
    if source not in c.EMOTION_SOURCES:
        source = c.EMOTION_SOURCE_AI
    return {"label": raw_value, "intensity": intensity, "source": source}


def _stable_character_id(namespace: str, name: str) -> str:
    seed = f"{namespace}\0{name}".encode("utf-8")
    return "char_" + hashlib.sha256(seed).hexdigest()[:16]


def _speakers(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AnalysisValidationError("speakers 必須是陣列")
    if len(value) > MAX_SPEAKERS:
        raise AnalysisValidationError(f"speakers 超過 {MAX_SPEAKERS} 個")
    out = []
    for sp in value:
        if not isinstance(sp, dict):
            continue
        name = (sp.get("name") or sp.get("canonical_name") or sp.get("canonicalName") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "gender": str(sp.get("gender") or "未知"),
            "age": str(sp.get("age") or sp.get("age_group") or sp.get("ageGroup") or "未知"),
        })
    return out


def _redact_diagnostic_text(value: str) -> str:
    """只在 diagnostics 中遮罩常見 credential 文字；不改變分析內容。"""
    value = re.sub(r"(?i)((?:x[-_]?api[-_]?key|api[-_]?key|authorization)\s*[:=]\s*(?:bearer\s+)?)"
                   r"[^\s,;]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[REDACTED]", value)
    return value


def _bounded_diagnostic_text(value: str, limit: int) -> str:
    value = _redact_diagnostic_text(str(value or ""))
    return value[:limit]


def _diagnostic_normalize(value: str, mode: str) -> str:
    if mode == "unicode_nfc":
        import unicodedata
        return unicodedata.normalize("NFC", value)
    if mode == "quote_stripped":
        return re.sub(r"[「」『』]", "", value)
    if mode == "punctuation_normalized":
        return value.replace("—", "——")
    if mode == "whitespace_normalized":
        return re.sub(r"\s+", "", value)
    return value


def _strip_outer_dialogue_wrapper(value: str) -> str:
    """Remove exactly one known paired dialogue wrapper, if present."""
    value = str(value or "")
    if len(value) < 2:
        return value
    closing = _DIALOGUE_QUOTE_PAIRS.get(value[0])
    if closing and value[-1] == closing:
        return value[1:-1]
    return value


def _source_span_boundary_valid(source_text: str, start: int, end: int,
                                text: str) -> bool:
    """Reject deterministic partial-word/sentence matches at source edges."""
    if start > 0 and source_text[start - 1] not in _SOURCE_BOUNDARY_CHARS:
        return False
    if end < len(source_text):
        following = source_text[end]
        if following not in _SOURCE_BOUNDARY_CHARS:
            # Adjacent segments may follow a complete sentence without an
            # inserted whitespace/newline in the source fixture.
            # A segment ending in a source dialogue closer is also complete,
            # unless the whole segment is merely a wrapper around a prefix.
            return text[-1:] in _TERMINAL_PUNCTUATION or (
                text[-1:] in _DIALOGUE_QUOTE_CLOSERS
                and text[:1] not in _DIALOGUE_QUOTE_PAIRS
            )
        # A segment cannot silently omit terminal punctuation from the source.
        if following in _TERMINAL_PUNCTUATION and not text[-1:] in _TERMINAL_PUNCTUATION:
            return False
    return True


def _offset_diagnostic(text: str, source_text: str, cursor: int, *,
                       analysis_id=None, chapter_id=None, chapter_key=None,
                       chunk_index=None, segment_index=None, segment_type=None,
                       speaker_id=None) -> dict:
    excerpt_start = max(0, cursor - 120)
    excerpt_end = min(len(source_text), cursor + OFFSET_DIAGNOSTIC_EXCERPT_LIMIT)
    excerpt = source_text[excerpt_start:excerpt_end]
    comparisons = {"exact": excerpt.find(text) >= 0}
    for mode in ("unicode_nfc", "quote_stripped", "punctuation_normalized", "whitespace_normalized"):
        normalized_excerpt = _diagnostic_normalize(excerpt, mode)
        normalized_text = _diagnostic_normalize(text, mode)
        comparisons[mode] = normalized_excerpt.find(normalized_text) >= 0
    return {
        "analysisId": analysis_id,
        "chapterId": chapter_id,
        "chapterKey": chapter_key,
        "chunkIndex": chunk_index,
        "segmentIndex": segment_index,
        "segmentType": segment_type,
        "speakerId": speaker_id,
        "segmentText": _bounded_diagnostic_text(text, OFFSET_DIAGNOSTIC_TEXT_LIMIT),
        "segmentHash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sourceExcerpt": _bounded_diagnostic_text(excerpt, OFFSET_DIAGNOSTIC_EXCERPT_LIMIT),
        "sourceExcerptStart": excerpt_start,
        "comparison": comparisons,
        "failureCode": "analysis_offset_mismatch",
    }


def _offset_for(text: str, source_text: str, cursor: int, *, diagnostic_context=None) -> tuple[int, int]:
    start = source_text.find(text, cursor)
    if start >= 0:
        end = start + len(text)
        if _source_span_boundary_valid(source_text, start, end, text):
            return start, end

    # Only tolerate one deterministic outer dialogue wrapper.  The returned
    # offsets always point into the original source, never into a normalized copy.
    unwrapped = _strip_outer_dialogue_wrapper(text)
    if unwrapped != text:
        start = source_text.find(unwrapped, cursor)
        if start >= 0:
            end = start + len(unwrapped)
            if _source_span_boundary_valid(source_text, start, end, unwrapped):
                return start, end

    context = diagnostic_context or {}
    diagnostics = _offset_diagnostic(text, source_text, cursor, **context)
    raise AnalysisOffsetMismatchError(
        "segment text 無法在 normalized source 找到對應 slice", diagnostics)


def _attribution(raw, *, speaker_id: str, speaker_surface: str | None) -> dict:
    value = raw if isinstance(raw, dict) else {}
    method = value.get("method")
    if method not in c.ANALYSIS_ATTRIBUTION_METHODS:
        method = "unresolved" if speaker_id == c.SPEAKER_ID_UNRESOLVED else "contextual"
    try:
        confidence = float(value.get("confidence", 0.0 if method == "unresolved" else 0.5))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if method == "unresolved":
        confidence = min(confidence, 0.99)
    out = {"method": method, "confidence": confidence}
    if value.get("evidence_text"):
        out["evidence_text"] = str(value["evidence_text"])
    if value.get("note"):
        out["note"] = str(value["note"])
    candidates = value.get("candidates") or value.get("candidate_character_ids")
    if isinstance(candidates, list):
        out["candidates"] = [str(x) for x in candidates if str(x).strip()]
    for key in ("evidence_start", "evidence_end"):
        if key in value:
            out[key] = value[key]
    return out


def normalize_analysis(raw, *, chapter_key: str, source_text_hash: str,
                       source_text: str | None = None, book_id: str | int | None = None,
                       analysis_id: int | None = None, chapter_id: int | None = None,
                       on_stage=None) -> dict:
    """把 provider 原始輸出正規化成 canonical v3 artifact。

    raw 支援：
    - v2 shape：{schemaVersion, chapterKey, sourceTextHash, speakers, segments}
    - v1 legacy shape：{seq, speakers, segments, analyzedAt}
    - minimal shape：{segments: [...]}
    """
    if not isinstance(raw, dict):
        raise AnalysisValidationError("分析輸出必須是 JSON 物件")
    raw_version = raw.get("schemaVersion")
    if raw_version is not None:
        try:
            version, mode = version_policy.require_version(raw_version)
        except version_policy.AnalysisVersionError as exc:
            raise AnalysisValidationError(str(exc)) from exc
        if mode == version_policy.NATIVE:
            raise AnalysisValidationError("v4 artifact 必須使用 source-faithful writer")
        if mode not in (version_policy.LEGACY, version_policy.COMPATIBILITY):
            raise AnalysisValidationError("不支援的 legacy analysis schemaVersion")
    raw_text = json.dumps(raw, ensure_ascii=False)
    if len(raw_text) > MAX_RAW_CHARS:
        raise AnalysisValidationError(f"分析輸出超過 {MAX_RAW_CHARS} 字")

    segments_in = raw.get("segments")
    if segments_in is None:
        raise AnalysisValidationError("分析輸出缺少 segments")
    if not isinstance(segments_in, list):
        raise AnalysisValidationError("segments 必須是陣列")
    if len(segments_in) > MAX_SEGMENTS:
        raise AnalysisValidationError(f"segments 超過 {MAX_SEGMENTS} 個")

    namespace = str(book_id or chapter_key)
    source = source_text if isinstance(source_text, str) and source_text else ""
    if not source:
        source = "".join(str(s.get("text", s.get("content", s.get("zh", "")))) for s in segments_in if isinstance(s, dict))
    characters: dict[str, dict] = {}
    name_to_id: dict[str, str] = {}
    for sp in _speakers(raw.get("characters") or raw.get("speakers")):
        cid = None
        original = next((x for x in (raw.get("characters") or raw.get("speakers") or [])
                         if isinstance(x, dict) and (x.get("name") or x.get("canonical_name") or x.get("canonicalName") or "").strip() == sp["name"]), {})
        cid = original.get("character_id") or original.get("characterId")
        cid = str(cid) if cid else _stable_character_id(namespace, sp["name"])
        if not is_valid_character_id(cid):
            raise AnalysisValidationError("character_id namespace 不合法")
        aliases = original.get("aliases") if isinstance(original.get("aliases"), list) else []
        characters[cid] = {
            "character_id": cid,
            "canonical_name": str(original.get("canonical_name") or original.get("canonicalName") or sp["name"]),
            "aliases": [str(x) for x in aliases if str(x).strip()],
            "gender": sp["gender"], "age_group": sp["age"],
        }
        name_to_id[sp["name"]] = cid

    segments = []
    cursor = 0
    for i, seg in enumerate(segments_in):
        if not isinstance(seg, dict):
            raise AnalysisValidationError("segment 必須是物件")
        raw_text = seg.get("text", seg.get("content", seg.get("zh", "")))
        text = _clean_text(raw_text)
        surface_hint = seg.get("speaker_surface") or seg.get("speaker")
        typ = seg.get("type")
        if typ is None:
            typ = "dialogue" if surface_hint and str(surface_hint).strip() not in ("旁白", "narrator") else "narration"
        if typ not in ("narration", "dialogue"):
            typ = "narration"
        surface = seg.get("speaker_surface") or seg.get("speaker")
        surface = str(surface).strip() if isinstance(surface, str) and surface.strip() else None
        explicit_id = seg.get("speaker_id") or seg.get("speakerId")
        if typ == "narration":
            speaker_id = c.SPEAKER_ID_NARRATOR
            surface = surface or "旁白"
        elif explicit_id in (c.SPEAKER_ID_NARRATOR, c.SPEAKER_ID_UNRESOLVED):
            speaker_id = explicit_id
        elif explicit_id:
            speaker_id = str(explicit_id)
            if not is_valid_character_id(speaker_id):
                raise AnalysisValidationError("speaker_id namespace 不合法")
        elif surface:
            speaker_id = name_to_id.get(surface) or _stable_character_id(namespace, surface)
            if speaker_id not in characters:
                characters[speaker_id] = {
                    "character_id": speaker_id, "canonical_name": surface, "aliases": [],
                    "gender": "未知", "age_group": "未知",
                }
                name_to_id[surface] = speaker_id
        else:
            speaker_id = c.SPEAKER_ID_UNRESOLVED
        if speaker_id not in (c.SPEAKER_ID_NARRATOR, c.SPEAKER_ID_UNRESOLVED) and speaker_id not in characters:
            canonical = surface or speaker_id
            characters[speaker_id] = {
                "character_id": speaker_id, "canonical_name": canonical, "aliases": [],
                "gender": "未知", "age_group": "未知",
            }
        start, end = _offset_for(
            text, source, cursor,
            diagnostic_context={
                "analysis_id": analysis_id,
                "chapter_id": chapter_id,
                "chapter_key": chapter_key,
                "chunk_index": seg.get("_analysis_chunk_index"),
                "segment_index": seg.get("_analysis_segment_index", i),
                "segment_type": typ,
                "speaker_id": speaker_id,
            },
        )
        cursor = end
        attribution = _attribution(seg.get("attribution"), speaker_id=speaker_id, speaker_surface=surface)
        item = {
            "segment_id": str(seg.get("segment_id") or seg.get("id") or f"seg-{i}"),
            "text": text, "type": typ, "speaker_id": speaker_id,
            "speaker_surface": surface, "emotion": _emotion(seg.get("emotion")),
            "source_start": start, "source_end": end,
        }
        if typ == "dialogue":
            item["attribution"] = attribution
        for key in ("tone", "speaking_style"):
            if seg.get(key) is not None:
                item[key] = str(seg[key])
        segments.append(item)
    if not segments and segments_in:
        raise AnalysisValidationError("沒有任何可用的 segment")

    artifact = {
        "schemaVersion": c.ANALYSIS_SCHEMA_VERSION_V3,
        "chapterKey": chapter_key,
        "sourceTextHash": source_text_hash,
        "sourceLength": len(source),
        "characters": list(characters.values()),
        "segments": segments,
    }
    if raw.get("schemaVersion") in (c.ANALYSIS_SCHEMA_VERSION_LEGACY, c.ANALYSIS_SCHEMA_VERSION_V2):
        artifact["legacySource"] = True
    if on_stage:
        on_stage("validating")
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: dict) -> None:
    """驗證已產生的 artifact（安全載入後檢查形狀）。"""
    if not isinstance(artifact, dict):
        raise AnalysisValidationError("artifact 必須是物件")
    if artifact.get("schemaVersion") != c.ANALYSIS_SCHEMA_VERSION_V3:
        raise AnalysisValidationError("不支援的 schemaVersion")
    if not artifact.get("chapterKey") or not artifact.get("sourceTextHash"):
        raise AnalysisValidationError("artifact 缺少 chapterKey 或 sourceTextHash")
    forbidden_provider_fields = {
        "reference_audio", "reference_audio_path", "reference_text", "embedding",
        "prompt", "provider_payload", "provider_native", "cosyvoice",
    }
    if forbidden_provider_fields.intersection(artifact):
        raise AnalysisValidationError("canonical analysis 不得包含 provider-specific expressive 欄位")
    if not isinstance(artifact.get("characters"), list):
        raise AnalysisValidationError("artifact 缺少 characters 陣列")
    chars = {x.get("character_id") for x in artifact["characters"] if isinstance(x, dict)}
    if len(chars) != len(artifact["characters"]) or None in chars:
        raise AnalysisValidationError("character_id 不合法或重複")
    if any(not is_valid_character_id(cid) for cid in chars):
        raise AnalysisValidationError("character_id namespace 不合法")
    if not isinstance(artifact.get("segments"), list):
        raise AnalysisValidationError("artifact 缺少 segments 陣列")
    last_end = -1
    for i, seg in enumerate(artifact["segments"]):
        if not isinstance(seg, dict):
            raise AnalysisValidationError("artifact segment 必須是物件")
        if forbidden_provider_fields.intersection(seg):
            raise AnalysisValidationError("canonical analysis segment 不得包含 provider-specific expressive 欄位")
        required = ("segment_id", "text", "type", "speaker_id", "source_start", "source_end", "emotion")
        if any(key not in seg or not seg[key] and key in ("segment_id", "text", "speaker_id") for key in required):
            raise AnalysisValidationError("artifact segment 缺少 required 欄位")
        if seg["type"] not in ("narration", "dialogue"):
            raise AnalysisValidationError("artifact segment type 不合法")
        if seg["type"] == "narration" and seg["speaker_id"] != c.SPEAKER_ID_NARRATOR:
            raise AnalysisValidationError("narration 必須使用 narrator speaker_id")
        if seg["type"] == "dialogue" and "attribution" not in seg:
            raise AnalysisValidationError("dialogue 缺少 attribution")
        if seg["speaker_id"] not in (c.SPEAKER_ID_NARRATOR, c.SPEAKER_ID_UNRESOLVED) and seg["speaker_id"] not in chars:
            raise AnalysisValidationError("segment speaker_id 找不到 character")
        if not isinstance(seg["source_start"], int) or not isinstance(seg["source_end"], int):
            raise AnalysisValidationError("source offsets 必須是整數")
        if seg["source_start"] < 0 or seg["source_end"] <= seg["source_start"] or seg["source_start"] < last_end:
            raise AnalysisValidationError("source offsets 不合法或重疊")
        if isinstance(artifact.get("sourceLength"), int) and seg["source_end"] > artifact["sourceLength"]:
            raise AnalysisValidationError("source offsets 超出 sourceLength")
        last_end = seg["source_end"]
        emotion = seg.get("emotion") or {}
        if emotion.get("label") not in c.CANONICAL_EMOTIONS:
            raise AnalysisValidationError("artifact segment emotion 值不合法")
        try:
            intensity = float(emotion.get("intensity"))
        except (TypeError, ValueError):
            raise AnalysisValidationError("emotion intensity 不合法")
        if not 0.0 <= intensity <= 1.0:
            raise AnalysisValidationError("emotion intensity 超出範圍")
        if seg["type"] == "dialogue":
            attr = seg["attribution"]
            if attr.get("method") not in c.ANALYSIS_ATTRIBUTION_METHODS:
                raise AnalysisValidationError("attribution method 不合法")
            confidence = float(attr.get("confidence"))
            if not 0.0 <= confidence <= 1.0:
                raise AnalysisValidationError("attribution confidence 超出範圍")
            evidence_start = attr.get("evidence_start")
            evidence_end = attr.get("evidence_end")
            if evidence_start is not None or evidence_end is not None:
                if not isinstance(evidence_start, int) or not isinstance(evidence_end, int):
                    raise AnalysisValidationError("attribution evidence offsets 必須是整數")
                if evidence_start < 0 or evidence_end <= evidence_start:
                    raise AnalysisValidationError("attribution evidence offsets 不合法")
                if isinstance(artifact.get("sourceLength"), int) and evidence_end > artifact["sourceLength"]:
                    raise AnalysisValidationError("attribution evidence offsets 超出 sourceLength")


def legacy_compatibility_view(artifact: dict) -> dict:
    """Return a read-only v2-shaped projection for legacy player/TTS callers.

    The returned dictionary is a compatibility view, never a canonical write
    target. v1/v2 are accepted only as explicitly legacy-shaped payloads;
    unsupported or malformed version envelopes fail closed.
    """
    try:
        _, mode = version_policy.require_artifact(artifact)
    except version_policy.AnalysisVersionError as exc:
        raise AnalysisValidationError(str(exc)) from exc
    if mode == version_policy.LEGACY:
        if not isinstance(artifact.get("segments"), list) or not isinstance(artifact.get("speakers"), list):
            raise AnalysisValidationError("legacy analysis 缺少可讀的 speakers/segments")
        return {
            "schemaVersion": c.ANALYSIS_SCHEMA_VERSION_V2,
            "chapterKey": artifact.get("chapterKey"),
            "sourceTextHash": artifact.get("sourceTextHash"),
            "speakers": list(artifact.get("speakers") or []),
            "segments": [dict(item) for item in artifact.get("segments") if isinstance(item, dict)],
            "legacySource": True,
            "compatibilityOnly": True,
        }
    if mode in (version_policy.COMPATIBILITY, version_policy.NATIVE):
        names = {x.get("character_id"): x.get("canonical_name") for x in artifact.get("characters", [])}
        for seg in artifact.get("segments", []):
            sid = seg.get("speaker_id")
            if sid and sid not in (c.SPEAKER_ID_NARRATOR, c.SPEAKER_ID_UNRESOLVED) and sid not in names:
                names[sid] = seg.get("speaker_surface") or seg.get("speaker") or sid
        speakers = [{"name": "旁白", "speaker_id": c.SPEAKER_ID_NARRATOR, "gender": "未知", "age": "未知"}]
        known = {item["speaker_id"] for item in speakers}
        for character in artifact.get("characters", []):
            speaker_id = character.get("character_id")
            if speaker_id in known:
                continue
            speakers.append({"name": character.get("canonical_name"), "speaker_id": speaker_id,
                             "gender": character.get("gender", "未知"), "age": character.get("age_group", "未知")})
            known.add(speaker_id)
        for speaker_id, name in names.items():
            if speaker_id not in known and speaker_id != c.SPEAKER_ID_UNRESOLVED:
                speakers.append({"name": name, "speaker_id": speaker_id, "gender": "未知", "age": "未知"})
                known.add(speaker_id)
        segments = []
        for seg in artifact.get("segments", []):
            out = dict(seg)
            out["id"] = out.get("segment_id")
            out["speaker"] = names.get(out.get("speaker_id"), "旁白" if out.get("speaker_id") == c.SPEAKER_ID_NARRATOR else "未解析語者")
            emotion = dict(out.get("emotion") or {})
            emotion["value"] = emotion.get("label", c.EMOTION_NEUTRAL)
            out["emotion"] = emotion
            segments.append(out)
        return {"schemaVersion": c.ANALYSIS_SCHEMA_VERSION_V2, "chapterKey": artifact.get("chapterKey"),
                "sourceTextHash": artifact.get("sourceTextHash"), "speakers": speakers, "segments": segments}
    raise AnalysisValidationError("不支援的 analysis artifact version")


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    try:
        loaded = json.loads(value or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        return {}


def derive_roster_from_ready_artifact(bid: str, chapter_seq: int, artifact: dict, analysis_id: int | None = None) -> dict:
    """由已驗證的 ready v3 artifact 建立 legacy-compatible roster projection。

    canonical identity 仍是 character_id；books 的既有 raw-name map 是暫時
    compatibility view，且完全不使用 substring merge。
    """
    row = db.get_book_row(bid)
    if not row:
        raise AnalysisValidationError("找不到 book 記錄")
    if version_policy.is_native(artifact.get("schemaVersion")):
        chapter = db.get_chapter(row["id"], chapter_seq)
        if not chapter:
            raise AnalysisValidationError("找不到 chapter 記錄")
        source_faithful.validate_v4_artifact(artifact, chapter.get("text") or "")
    else:
        validate_artifact(artifact)
    voices = _json_object(row.get("voices"))
    voices.setdefault("_english", "")
    info = _json_object(row.get("speaker_info"))
    per_ch = _json_object(row.get("speaker_chapters"))
    characters = {x.get("character_id"): x for x in artifact.get("characters", []) if isinstance(x, dict)}
    resolved_map = {}
    if analysis_id is not None:
        for mapping in db.query("SELECT segment_id, resolved_speaker_id, status FROM character_segment_resolutions WHERE analysis_id=?",
                                (analysis_id,)):
            if mapping.get("status") == "accepted" and mapping.get("resolved_speaker_id"):
                resolved_map[str(mapping["segment_id"])] = mapping["resolved_speaker_id"]
    registry_names = {}
    for mapping in db.query("SELECT character_id, canonical_name FROM character_registry WHERE book_id=?", (row["id"],)):
        registry_names[mapping["character_id"]] = mapping["canonical_name"]
    names = {c.SPEAKER_ID_NARRATOR: "旁白", **registry_names}
    for cid, character in characters.items():
        names[cid] = character.get("canonical_name") or cid
    counts = {}
    for seg in artifact.get("segments", []):
        sid = resolved_map.get(str(seg.get("segment_id")), seg.get("speaker_id"))
        name = names.get(sid)
        if not name or sid == c.SPEAKER_ID_UNRESOLVED:
            continue
        counts[name] = counts.get(name, 0) + 1
    chapter_counts = {str(k): dict(v) for k, v in per_ch.items() if str(k) != str(chapter_seq)}
    chapter_counts[str(chapter_seq)] = counts
    active_names = {
        name
        for chapter in chapter_counts.values()
        for name in chapter
    }
    # Re-analysis of the same chapter replaces that chapter's projection.  Do
    # not leave names from a superseded ready artifact in the book-level
    # compatibility view when no other ready chapter still references them.
    info = {
        name: value
        for name, value in info.items()
        if name in active_names
    }
    voices = {
        name: value
        for name, value in voices.items()
        if name == "_english" or name in active_names
    }
    for name, count in counts.items():
        current = info.setdefault(name, {
            "gender": "未知", "age": "未知", "firstCh": chapter_seq,
            "judgements": [], "conflicts": [],
        })
        cid = next((sid for sid, label in names.items() if label == name), None)
        character = characters.get(cid) or {}
        current["gender"] = character.get("gender", current.get("gender", "未知"))
        current["age"] = character.get("age_group", current.get("age", "未知"))
        current["speaker_id"] = cid or c.SPEAKER_ID_NARRATOR
        current["count"] = sum(ch.get(name, 0) for ch in chapter_counts.values())
        voices.setdefault(name, "")
    db.update_book(bid, {
        "voices": json.dumps(voices, ensure_ascii=False),
        "speaker_info": json.dumps(info, ensure_ascii=False),
        "speaker_chapters": json.dumps(chapter_counts, ensure_ascii=False),
    })
    return {"voices": voices, "speakerInfo": info, "speakerChapters": chapter_counts}


def retry_roster_projection(analysis_id: int) -> dict:
    """從既有 ready artifact 重試 roster projection，不重新呼叫 AI。"""
    record = db.get_chapter_analysis(analysis_id)
    if not record or record.get("status") != c.ANALYSIS_STATUS_READY:
        raise AnalysisValidationError("只能從 ready analysis 重試 roster")
    rel = record.get("artifact_path") or ""
    if not rel:
        raise AnalysisValidationError("ready analysis 缺少 artifact path")
    book = db.get_book_by_rowid(record["book_id"])
    if not book:
        raise AnalysisValidationError("找不到 book 記錄")
    abs_path = os.path.join(settings.ROOT_DIR, rel)
    with open(abs_path, "r", encoding="utf-8") as f:
        artifact = json.load(f)
    # db.get_chapter 使用 seq；analysis record 的 chapter_id 需先以 id 查找。
    chapter_row = next((x for x in db.list_chapters(record["book_id"]) if x["id"] == record["chapter_id"]), None)
    if not chapter_row or chapter_row["text_hash"] != record["source_text_hash"]:
        raise AnalysisSourceChangedError("章節 source hash 已變更，略過 roster retry")
    if version_policy.is_native(artifact.get("schemaVersion")):
        source_faithful.validate_v4_artifact(artifact, chapter_row.get("text") or "")
    else:
        validate_artifact(artifact)
    try:
        # identity registry 只讀 validated ready artifact；不可在 ready 前建立 canonical mapping。
        from . import character_resolution
        character_resolution.register_ready_artifact(book["bid"], analysis_id, artifact)
        result = derive_roster_from_ready_artifact(book["bid"], int(chapter_row["seq"]), artifact, analysis_id=analysis_id)
    except Exception as exc:  # noqa: BLE001 — ready artifact 保持可 retry
        db.update_chapter_analysis(analysis_id, {"error": f"roster projection pending: {exc}"[:2000]})
        raise
    db.update_chapter(book["id"], int(chapter_row["seq"]), {"current_analysis_id": analysis_id})
    db.update_chapter_analysis(analysis_id, {"error": ""})
    return result


def compute_analysis_source_hash(chapter_key: str, text: str) -> str:
    """analysis 記錄的 source_text_hash（= 章節 text_hash 語意）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def create_analysis_row(*, book_id: int, chapter_id: int, source_text_hash: str,
                        analysis_profile: str = "speaker", prompt_version: str = "",
                        ai_provider_id: int | None = None, ai_model: str | None = None,
                        ai_config_version: int | None = None,
                        emotion_policy_version: str | None = None,
                        created_by: int | None = None,
                        schema_version: int = c.ANALYSIS_SCHEMA_VERSION,
                        batch_job_id: int | None = None) -> int:
    """建立 queued analysis 記錄，回傳 analysis id。"""
    try:
        schema_version = version_policy.require_execution_target(
            schema_version, context="analysis row schemaVersion")
    except version_policy.AnalysisVersionError as exc:
        raise AnalysisValidationError(str(exc)) from exc
    return db.create_chapter_analysis({
        "book_id": book_id,
        "chapter_id": chapter_id,
        "batch_job_id": batch_job_id,
        "source_text_hash": source_text_hash,
        "analysis_type": c.ANALYSIS_TYPE_SPEAKER,
        "schema_version": schema_version,
        "analysis_profile": analysis_profile,
        "status": c.ANALYSIS_STATUS_QUEUED,
        "prompt_version": prompt_version,
        "emotion_policy_version": emotion_policy_version,
        "ai_provider_id": ai_provider_id,
        "ai_model": ai_model,
        "ai_config_version": ai_config_version,
        "created_by": created_by,
    })


def save_ready_analysis(analysis_id: int, artifact: dict) -> str:
    """Persist an explicitly legacy/v3 artifact.

    New analysis must use :func:`save_ready_source_faithful_analysis`. Keeping
    this writer narrow prevents a v4 artifact from being silently
    down-converted through the old offset-based normalizer.
    """
    row = db.get_chapter_analysis(analysis_id)
    if not row:
        raise AnalysisValidationError("找不到 analysis 記錄")
    try:
        version, mode = version_policy.require_artifact(artifact)
    except version_policy.AnalysisVersionError as exc:
        raise AnalysisValidationError(str(exc)) from exc
    if mode == version_policy.NATIVE:
        raise AnalysisValidationError("v4 artifact 必須使用 source-faithful writer")
    if row.get("schema_version") not in (
        c.ANALYSIS_SCHEMA_VERSION_LEGACY,
        c.ANALYSIS_SCHEMA_VERSION_V2,
        c.ANALYSIS_SCHEMA_VERSION_V3,
        c.ANALYSIS_SCHEMA_VERSION_V4,
    ):
        raise AnalysisValidationError("analysis record schemaVersion 不支援")
    if row.get("schema_version") == c.ANALYSIS_SCHEMA_VERSION_V4:
        # An explicit legacy writer call is a compatibility fixture/adapter
        # boundary. Mark the record as v3 rather than leaving a v4 row pointing
        # at a down-converted artifact.
        db.update_chapter_analysis(analysis_id, {"schema_version": c.ANALYSIS_SCHEMA_VERSION_V3})
    if version != c.ANALYSIS_SCHEMA_VERSION_V3:
        artifact = normalize_analysis(
            artifact, chapter_key=artifact.get("chapterKey") or row.get("chapter_key") or "legacy",
            source_text_hash=artifact.get("sourceTextHash") or row["source_text_hash"],
        )
    validate_artifact(artifact)
    book = db.get_book_by_rowid(row["book_id"])
    if not book:
        raise AnalysisValidationError("找不到 book 記錄")
    chapter = next((x for x in db.list_chapters(row["book_id"]) if x["id"] == row["chapter_id"]), None)
    if not chapter or chapter["text_hash"] != artifact["sourceTextHash"]:
        error = AnalysisSourceChangedError("章節 source hash 在保存前已變更")
        mark_analysis_progress_failed(analysis_id, error)
        db.mark_analysis_failed(analysis_id, build_failure_error(error))
        raise AnalysisSourceChangedError("章節 source hash 已變更，拒絕保存 ready artifact")
    rel = storage.analysis_artifact_path(book["bid"], analysis_id)
    abs_path = os.path.join(settings.ROOT_DIR, rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp = abs_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2)
    os.replace(tmp, abs_path)
    return _publish_ready_artifact(analysis_id, rel, abs_path)


def save_ready_source_faithful_analysis(analysis_id: int, artifact: dict, source: str) -> str:
    """Persist v4 only after source-owned validation and the source hash gate.

    This writer never falls back to ``normalize_analysis`` or ``_offset_for``;
    v3 remains untouched if any v4 gate fails.
    """
    row = db.get_chapter_analysis(analysis_id)
    if not row:
        raise AnalysisValidationError("找不到 analysis 記錄")
    try:
        source_faithful.validate_v4_artifact(artifact, source)
    except source_faithful.SourceFaithfulnessError as exc:
        mark_analysis_progress_failed(analysis_id, exc)
        db.mark_analysis_failed(analysis_id, build_failure_error(exc))
        raise AnalysisValidationError(str(exc)) from exc
    chapter = next((item for item in db.list_chapters(row["book_id"]) if item["id"] == row["chapter_id"]), None)
    if not chapter or chapter.get("text_hash") != row.get("source_text_hash"):
        error = AnalysisSourceChangedError("章節 source hash 在 v4 保存前已變更")
        mark_analysis_progress_failed(analysis_id, error)
        db.mark_analysis_failed(analysis_id, build_failure_error(error))
        raise error
    book = db.get_book_by_rowid(row["book_id"])
    if not book:
        raise AnalysisValidationError("找不到 book 記錄")
    rel = storage.analysis_artifact_path(book["bid"], analysis_id)
    absolute = os.path.join(settings.ROOT_DIR, rel)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    temporary = absolute + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, absolute)
    # File is durable before the DB ready pointer is changed.  Existing v3
    # records are never overwritten by this writer.
    # File is durable before the DB ready pointer is changed.  The conditional
    # status update prevents a concurrent user cancellation from being
    # overwritten by this writer.
    if db.mark_analysis_ready_if_running(
            analysis_id, rel, schema_version=source_faithful.SCHEMA_VERSION):
        return rel
    try:
        os.remove(absolute)
    except FileNotFoundError:
        pass
    current = db.get_chapter_analysis(analysis_id) or {}
    failure = parse_failure_error(current.get("error"))
    if failure.get("code") == "analysis_cancelled":
        raise AnalysisCancelledError("分析已取消")
    raise AnalysisValidationError("分析狀態已變更，拒絕保存 ready artifact")
