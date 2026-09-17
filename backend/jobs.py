"""持久化 AI/TTS 工作佇列與單機 Worker（V4 canonical job types）。"""
import json
import logging
import os
import subprocess
import threading
import uuid
import time

from . import db
from . import settings
from .services import generation_orchestration as orchestration

_log = logging.getLogger("jobs")
_stop = threading.Event()
_worker = None
_consumer_threads = {}
_worker_identity = None
_worker_state = "stopped"
_worker_error = None
MAX_ATTEMPTS = 3
MAX_PROVIDER_RETRY_DELAY = 60
LEASE_SECONDS = db.DEFAULT_GENERATION_LEASE_SECONDS
HEARTBEAT_SECONDS = db.GENERATION_HEARTBEAT_INTERVAL_SECONDS


def _consumer_count(service_type: str) -> int:
    key = "GENERATION_AI_MAX_CONCURRENCY" if service_type == "AI" else "GENERATION_TTS_MAX_CONCURRENCY"
    try:
        return max(1, min(int(os.environ.get(key, "1")), 100))
    except (TypeError, ValueError):
        return 1


def _build_sha() -> str:
    configured = os.getenv("APP_BUILD_SHA", "").strip()
    if configured:
        return configured[:80]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=settings.ROOT_DIR,
            capture_output=True, text=True, timeout=1, check=False)
        value = (result.stdout or "").strip()
        return value[:80] or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _new_worker_identity() -> dict:
    build_sha = _build_sha()
    return {
        "instance_id": uuid.uuid4().hex,
        "worker_version": f"{settings.APP_VERSION}:{build_sha}",
        "build_sha": build_sha,
    }


def worker_status() -> dict:
    alive = bool((_worker is not None and _worker.is_alive()) or
                 any(thread.is_alive() for thread in _consumer_threads.values()))
    identity = dict(_worker_identity or {})
    active_instances = db.list_worker_instances(active_only=True)
    current_active = bool(identity.get("instance_id") and any(
        row.get("instance_id") == identity.get("instance_id") for row in active_instances
    ))
    consumer_ready = (not settings.WORKER_ENABLED) or (
        alive and current_active and _worker_state == "running"
    )
    return {
        "enabled": bool(settings.WORKER_ENABLED),
        "alive": alive,
        "consumerReady": consumer_ready,
        "state": _worker_state,
        "lastError": _worker_error,
        "currentInstanceActive": current_active,
        "instanceId": identity.get("instance_id"),
        "version": identity.get("worker_version"),
        "buildSha": identity.get("build_sha"),
        "activeInstanceCount": len(active_instances),
        "consumers": {key: bool(thread.is_alive()) for key, thread in _consumer_threads.items()},
    }


def _run(job: dict):
    payload = json.loads(job.get("payload") or "{}")
    job_type = job["job_type"]
    current_job = db.get_generation_job(job["id"])
    if job_type == "speaker_analysis" and job.get("analysis_id"):
        from .services import analysis as analysis_svc
        if current_job and current_job.get("status") == "cancelled":
            raise analysis_svc.AnalysisCancelledError("分析已取消")
    if not current_job or current_job.get("status") != "running":
        raise db.GenerationOwnershipConflict("工作已不在目前 worker 的執行狀態")
    if job_type == "speaker_analysis":
        from .services import analysis_executor
        from .services import analysis as analysis_svc
        if job.get("analysis_id"):
            analysis_svc.raise_if_cancelled(job["analysis_id"])
        result = analysis_executor.run_speaker_analysis(job)
        if result and result.get("status") == "failed":
            db.finalize_generation_job(job["id"], job.get("worker_claim_token"), "failed", error=result.get("error") or "分析失敗",
                                       failure_category="validation_failure")
            return
    elif job_type == "speaker_analysis_all":
        from .services import analysis_executor
        analysis_executor.run_speaker_analysis_all(job)
    elif job_type in ("audio_single", "audio_multi", "audio_generate_all"):
        from .services import generation_pipeline
        if job_type == "audio_single":
            generation_pipeline.run_single_generation(job)
        elif job_type == "audio_multi":
            generation_pipeline.run_multi_generation(job)
        else:
            generation_pipeline.run_generate_all(job)
    else:
        raise ValueError(f"未知生成任務：{job_type}")
    current = db.get_generation_job(job["id"])
    if current and current.get("status") == "cancelled":
        return
    return True


def run_pending_once(service_type: str | None = None):
    # The public/test helper historically means "run one job now".  Real
    # consumer loops always pass an explicit AI/TTS service type, so keep the
    # no-argument path independent from worker lifecycle state and persisted
    # retry timing for compatibility callers.
    compatibility_direct = service_type is None
    job = db.claim_generation_job(_worker_identity, service_type=service_type, lease_seconds=LEASE_SECONDS,
                                  ignore_schedule=compatibility_direct)
    if not job:
        return False
    job_type = job["job_type"]
    heartbeat_stop = threading.Event()

    def renew_lease():
        while not heartbeat_stop.wait(HEARTBEAT_SECONDS):
            if not db.heartbeat_generation_job(job["id"], job.get("worker_claim_token"), lease_seconds=LEASE_SECONDS):
                return

    heartbeat_thread = threading.Thread(target=renew_lease, name=f"generation-lease-{job['id']}", daemon=True)
    heartbeat_thread.start()
    try:
        _run(job)
        finalized = db.finalize_generation_job(job["id"], job.get("worker_claim_token"), "success", progress=100)
        if finalized:
            from .services import notifications as notification_service
            try:
                notification_service.notify_generation_terminal(job["id"], "success")
            except Exception:
                _log.exception("generation success notification deferred id=%s", job["id"])
    except Exception as error:
        from .services import tts_adapter
        message = tts_adapter.error_envelope(error) if getattr(error, "code", None) else str(error)
        from .services import analysis as analysis_svc
        current_job = db.get_generation_job(job["id"]) or {}
        if (getattr(error, "code", "") == "analysis_cancelled"
                or current_job.get("status") == "cancelled"
                or current_job.get("cancel_requested")):
            # The cancel endpoint already converged the analysis row.  Never
            # turn that terminal user action into retry/failed job history.
            if current_job.get("status") != "cancelled":
                finalized = db.finalize_generation_job(job["id"], job.get("worker_claim_token"), "cancelled", error=message,
                                                       failure_category="cancelled")
                if finalized:
                    from .services import notifications as notification_service
                    try:
                        notification_service.notify_generation_terminal(job["id"], "cancelled")
                    except Exception:
                        _log.exception("generation cancellation notification deferred id=%s", job["id"])
            return True
        deterministic_analysis_failure = (
            job_type == "speaker_analysis" and analysis_svc.is_deterministic_failure(error))
        failure = orchestration.classify_error(error)
        # Preserve the old unknown-job retry contract while all known
        # orchestration categories use their explicit retry policy.
        unknown_compat_retry = job_type not in {"speaker_analysis", "speaker_analysis_all", "audio_single", "audio_multi", "audio_generate_all"}
        # Preserve the established bounded retry behavior for an unclassified
        # analysis/pipeline exception.  Explicit deterministic analysis
        # failures (schema/validation/source/config/cancel) still fail closed;
        # this branch is finite because max_attempts is enforced below.
        analysis_compat_retry = (
            job_type in {"speaker_analysis", "speaker_analysis_all"}
            and failure["category"] == "unknown"
        )
        should_retry = (
            failure["retryable"] or unknown_compat_retry or analysis_compat_retry
        ) and not deterministic_analysis_failure
        if job["attempts"] < min(MAX_ATTEMPTS, int(job.get("max_attempts") or MAX_ATTEMPTS)) and should_retry:
            _log.warning("generation job retry id=%s attempt=%s/%s", job["id"], job["attempts"], MAX_ATTEMPTS)
            delay = orchestration.backoff_seconds(job["attempts"], retry_after=failure.get("retry_after"), cap=MAX_PROVIDER_RETRY_DELAY)
            if compatibility_direct and failure.get("retry_after") is not None:
                # Preserve the historical synchronous helper contract used by
                # callers/tests; real consumers rely solely on persisted due time.
                time.sleep(min(MAX_PROVIDER_RETRY_DELAY, max(0.0, float(failure["retry_after"]))))
            db.finish_generation_attempt(job.get("attempt_id"), outcome="retry_scheduled",
                                         failure_category=failure["category"], retryable=True,
                                         diagnostics={"message": message, "nextAttemptIn": delay})
            db.requeue_generation_job(job["id"], message, next_attempt_at=orchestration.due_at(delay),
                                      failure_category=failure["category"], retryable=True,
                                      claim_token=job.get("worker_claim_token"))
            if job_type == "speaker_analysis" and job.get("analysis_id"):
                db.update_chapter_analysis(job["analysis_id"], {
                    "error": analysis_svc.build_retry_error(message, attempts=job["attempts"]),
                })
                analysis_svc.update_analysis_progress(
                    job["analysis_id"], stage="queued", retry_count=job["attempts"],
                    last_error=error,
                )
        else:
            _log.exception("generation job failed id=%s", job["id"])
            finalized = db.finalize_generation_job(job["id"], job.get("worker_claim_token"), "failed", error=message,
                                                   failure_category=failure["category"], retryable=False)
            if finalized:
                from .services import notifications as notification_service
                try:
                    notification_service.notify_generation_terminal(job["id"], "failed")
                except Exception:
                    _log.exception("generation failure notification deferred id=%s", job["id"])
            if job_type == "speaker_analysis" and job.get("analysis_id"):
                analysis_svc.reconcile_progress_from_partials(job["analysis_id"])
                analysis_svc.mark_analysis_progress_failed(job["analysis_id"], error)
                db.mark_analysis_failed(
                    job["analysis_id"],
                    analysis_svc.build_failure_error(error, attempts=job["attempts"]),
                )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
    return True


def _loop(service_type: str | None = None):
    global _worker_state, _worker_error
    try:
        _worker_state = "running"
        _worker_error = None
        last_heartbeat = 0.0
        while not _stop.is_set():
            # heartbeat is deliberately lightweight and independent of job execution.
            if _worker_identity:
                current = time.monotonic()
                if current - last_heartbeat >= HEARTBEAT_SECONDS:
                    db.heartbeat_worker_instance(_worker_identity["instance_id"])
                    last_heartbeat = current
            try:
                if not run_pending_once(service_type):
                    _stop.wait(0.5)
            except Exception as error:
                _worker_state = "degraded"
                _worker_error = str(error)[:300]
                _log.exception("generation worker loop iteration failed")
                _stop.wait(1.0)
                if not _stop.is_set():
                    _worker_state = "running"
    finally:
        # Worker 使用自己的 thread-local SQLite connection，主執行緒無法代關。
        try:
            db.close_all()
        finally:
            if _worker_identity and not _consumer_threads:
                db.unregister_worker_instance(_worker_identity["instance_id"])


def start_worker():
    global _worker, _worker_identity, _worker_state, _worker_error, _consumer_threads
    if not settings.WORKER_ENABLED:
        _worker_state = "disabled"
        _log.info("worker disabled by WORKER_ENABLED=false; web process will not consume queue")
        return False
    if _worker and _worker.is_alive():
        return True
    if _worker_identity:
        try:
            db.unregister_worker_instance(_worker_identity["instance_id"])
        except Exception:
            _log.exception("failed to clear previous worker identity")
    _worker_identity = _new_worker_identity()
    _worker_state = "starting"
    _worker_error = None
    if not db.register_worker_instance(
        instance_id=_worker_identity["instance_id"],
        worker_version=_worker_identity["worker_version"],
        build_sha=_worker_identity["build_sha"],
        process_id=os.getpid(), db_path=settings.DB_PATH, worker_enabled=True,
        service_types="AI,TTS",
    ):
        _log.error("worker refused: another active worker with a different build owns the queue")
        _worker_state = "blocked"
        _worker_error = "incompatible_active_worker"
        _worker_identity = None
        return False
    _stop.clear()
    _consumer_threads = {}
    for service in ("AI", "TTS"):
        for index in range(_consumer_count(service)):
            key = f"{service}-{index + 1}"
            _consumer_threads[key] = threading.Thread(
                target=_loop, args=(service,), name=f"generation-worker-{service.lower()}-{index + 1}", daemon=True,
            )
    # Keep _worker as a compatibility handle for health checks/tests.
    _worker = next(iter(_consumer_threads.values()))
    for thread in _consumer_threads.values():
        thread.start()
    return True


def stop_worker():
    global _worker, _worker_identity, _worker_state, _consumer_threads
    _stop.set()
    for thread in list(_consumer_threads.values()):
        if thread.is_alive():
            thread.join(timeout=3)
    if _worker_identity:
        db.unregister_worker_instance(_worker_identity["instance_id"])
    _worker = None
    _consumer_threads = {}
    _worker_state = "stopped"
