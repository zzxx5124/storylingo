"""Generation orchestration primitives shared by API and workers.

The SQLite database remains the source of truth.  This module deliberately
contains policy and serialization helpers only; provider calls never happen
while a database transaction is open.
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta
from typing import Any

from .. import db

SERVICE_AI = "AI"
SERVICE_TTS = "TTS"
SERVICES = (SERVICE_AI, SERVICE_TTS)

AUTO_RETRY_CATEGORIES = frozenset({
    "provider_busy", "rate_limited", "network_transient", "timeout", "worker_lost",
})
MANUAL_RETRY_CATEGORIES = frozenset({
    "schema_failure", "invalid_provider_response", "invalid_configuration",
    "validation_failure", "source_changed", "missing_voice_mapping", "unknown",
})


class GenerationDomainConflict(RuntimeError):
    def __init__(self, message: str, *, code: str = "generation_conflict"):
        super().__init__(message)
        self.code = code


def service_type_for_job(job_type: str) -> str | None:
    return db.service_type_for_job_type(job_type)


def classify_error(error: BaseException) -> dict[str, Any]:
    """Normalize provider/worker failures without persisting raw payloads."""
    code = str(getattr(error, "code", "") or "").strip().lower()
    status = getattr(error, "status", None)
    retryable = bool(getattr(error, "retryable", False))
    if code in ("queue_full", "provider_busy", "busy"):
        category = "provider_busy"
    elif code in ("rate_limited", "rate_limit", "too_many_requests") or status == 429:
        category = "rate_limited"
    elif code in ("timeout", "timed_out") or isinstance(error, TimeoutError):
        category = "timeout"
    elif code in ("invalid_response", "provider_response_invalid"):
        category = "invalid_provider_response"
    elif code in ("invalid_configuration", "provider_unavailable", "provider_not_configured"):
        category = "invalid_configuration"
    elif code in ("source_changed", "stale_revision") or any(
        marker in str(error).lower() for marker in ("source changed", "source revision")
    ):
        category = "source_changed"
    elif code in ("schema_failure", "structured_schema_failure"):
        category = "schema_failure"
    elif code in ("missing_voice_mapping", "voice_missing"):
        category = "missing_voice_mapping"
    elif code in ("network", "network_error", "connection_error"):
        category = "network_transient"
    elif isinstance(error, (ConnectionError, OSError)):
        category = "network_transient"
    else:
        category = "unknown"
    if category in AUTO_RETRY_CATEGORIES:
        retryable = True
    if category in MANUAL_RETRY_CATEGORIES:
        retryable = False
    return {
        "category": category,
        "retryable": retryable,
        "message": str(error)[:2000],
        "retry_after": _bounded_retry_after(getattr(error, "retry_after", None)),
    }


def _bounded_retry_after(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return max(0.0, min(float(value), 3600.0))
    except (TypeError, ValueError):
        return None


def backoff_seconds(attempt_number: int, *, retry_after: float | None = None,
                    jitter: float | None = None, cap: int = 3600) -> float:
    """Bounded exponential backoff with injectable jitter for deterministic tests."""
    base = min(float(cap), 2.0 ** max(0, min(int(attempt_number), 12)))
    if retry_after is not None:
        base = max(base, min(float(cap), max(0.0, float(retry_after))))
    noise = random.uniform(0.0, min(1.0, base * 0.25)) if jitter is None else max(0.0, float(jitter))
    return min(float(cap), base + noise)


def due_at(seconds: float, *, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return (now + timedelta(seconds=max(0.0, float(seconds)))).isoformat(timespec="seconds")


def safe_provider_snapshot(provider: dict | None, *, service_type: str) -> dict:
    provider = provider or {}
    capability = provider.get("structured_capability_json") or provider.get("capabilities_json") or {}
    if isinstance(capability, str):
        try:
            capability = json.loads(capability)
        except (TypeError, ValueError, json.JSONDecodeError):
            capability = {}
    return {
        "serviceType": service_type,
        "providerId": provider.get("id"),
        "providerLabel": provider.get("name") or provider.get("provider_type") or "",
        "providerType": provider.get("provider_type") or "",
        "model": provider.get("model") or "",
        "configVersion": provider.get("config_version") or 1,
        "adapterKey": provider.get("adapter_key") or "",
        "capabilityVersion": provider.get("capability_snapshot_version") or provider.get("structured_capability_probe_version") or "",
        "capabilityHash": provider.get("capabilities_hash") or "",
        "capability": capability if isinstance(capability, dict) else {},
    }


def source_revision(*parts: Any) -> str:
    values = []
    for part in parts:
        if isinstance(part, (dict, list)):
            values.append(part)
        else:
            values.append(str(part or ""))
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def book_text_revision(book: dict) -> str:
    """Stable aggregate of chapter text hashes for operation-level metadata."""
    chapters = db.list_chapters(book["id"])
    return source_revision([
        {"seq": chapter.get("seq"), "textHash": chapter.get("text_hash") or ""}
        for chapter in chapters
    ])


def dependencies_ready(job: dict | None, *, book: dict | None = None) -> bool:
    """Return whether a TTS job is actually executable.

    Waiting dependency is intentionally checked before queue claim so a
    multi-speaker operation cannot consume a worker attempt merely to report
    that analysis or voice mapping is still missing.  Single-speaker jobs do
    not acquire an artificial AI dependency.
    """
    if not job or (job.get("service_type") or service_type_for_job(job.get("job_type"))) != SERVICE_TTS:
        return True
    if job.get("job_type") not in {"audio_multi", "audio_generate_all"}:
        return True
    try:
        payload = json.loads(job.get("payload") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    bid = payload.get("bid") or job.get("book_id")
    if not bid:
        return False
    book = book or db.get_book_row(bid)
    if not book:
        return False
    from . import audio_generation as audio_generation_service
    from . import workflow

    chapters = db.list_chapters(book["id"])
    if job.get("job_type") == "audio_multi":
        seq = payload.get("seq")
        chapters = [db.get_chapter(book["id"], int(seq))] if seq is not None else []
    for chapter in chapters:
        if not chapter:
            return False
        if audio_generation_service.effective_mode(book, chapter) != "multi":
            continue
        analysis = db.get_ready_chapter_analysis(chapter["id"], chapter.get("text_hash") or "")
        if not analysis or not analysis.get("artifact_path"):
            return False
        try:
            if workflow._multi_unassigned(book, analysis):
                return False
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
    return True


def create_job(*, job_type: str, book_id: int | None = None, chapter_id: int | None = None,
               requested_by: int | None = None, payload: dict | None = None,
               provider: dict | None = None, operation_id: int | None = None,
               source_revision_value: str = "", source_text_hash: str = "",
               voice_snapshot: dict | None = None, configuration_snapshot: dict | None = None,
               capability_snapshot: dict | None = None, max_attempts: int = 3) -> int:
    service_type = service_type_for_job(job_type)
    snapshot = safe_provider_snapshot(provider, service_type=service_type or "UNKNOWN")
    return db.create_generation_job(
        job_type, book_id=book_id, chapter_id=chapter_id, requested_by=requested_by,
        payload=payload, operation_id=operation_id, service_type=service_type,
        provider=provider, source_revision=source_revision_value, source_text_hash=source_text_hash,
        voice_snapshot=voice_snapshot,
        configuration_snapshot=configuration_snapshot or snapshot,
        capability_snapshot=capability_snapshot or snapshot,
        max_attempts=max_attempts,
    )


def safe_operation(operation_id: int) -> dict | None:
    return db.get_generation_operation(operation_id)


def safe_job(job_id: int) -> dict | None:
    job = db.get_generation_job(job_id)
    if not job:
        return None
    result = db._safe_generation_job(job)
    result["attemptsHistory"] = db.list_generation_attempts(job_id)
    return result


def enqueue_audiobook_operation(*, request: dict, book: dict, actor_id: int,
                                provider: dict, source_revision_value: str,
                                payload: dict | None = None) -> tuple[int, int]:
    """Create operation and parent TTS job with creation-time binding."""
    aggregate_text_hash = book_text_revision(book)
    operation_id = db.create_generation_operation(
        book_id=book["id"], operation_type="audiobook", requested_by=actor_id,
        request_id=request["id"], source_revision=source_revision_value,
        source_text_hash=aggregate_text_hash,
        metadata={"requestType": "audiobook", "authorizationState": "AUTHORIZED_FOR_GENERATION"},
    )
    job_id = create_job(
        job_type="audio_generate_all", book_id=book["id"], requested_by=actor_id,
        operation_id=operation_id, provider=provider, source_revision_value=source_revision_value,
        source_text_hash=aggregate_text_hash,
        payload={**(payload or {}), "contentRequestId": request["id"], "operationId": operation_id,
                 "bookId": book["id"], "authorized": True},
        configuration_snapshot={"providerConfigVersion": provider.get("config_version"),
                                "operationType": "audiobook"},
    )
    job = db.get_generation_job(job_id)
    if not dependencies_ready(job, book=book):
        db.mark_generation_job_waiting_dependency(
            job_id, "等待相符的 AI analysis 與 voice mapping")
    return operation_id, job_id
