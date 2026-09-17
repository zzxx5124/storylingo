"""Resumable analysis chunk partials; never a canonical analysis artifact."""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta

from .. import db, settings
from . import analysis_version_policy as version_policy

CHUNKING_VERSION = "source-faithful-6000x50-strict-cardinality-v3"
PARTIAL_SCHEMA_VERSION = 1
LEASE_SECONDS = 600
MAX_WAIT_SECONDS = 2.0
MAX_PARTIAL_BYTES = 2_000_000


class PartialCacheError(RuntimeError):
    pass


class PartialClaimBusy(PartialCacheError):
    pass


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_cache_identity(*, source_text_hash: str, chunk_index: int, chunk_input: str,
                         context: str, prompt_hash: str, prompt_version: str,
                         schema_version: int, analysis_profile: str, provider_identity: str,
                         model_identity: str, provider_config_version, fallback_models: list[str],
                         language: str, category: str, chunking_version: str = CHUNKING_VERSION,
                         segmentation_version: str = "legacy-text-v1",
                         span_hashes: list[str] | None = None,
                         output_mode: str = "legacy_json",
                         annotation_schema_id: str = "legacy",
                         annotation_schema_version: str = "legacy",
                         annotation_schema_hash: str = "legacy",
                         capability_version: str = "unknown",
                         request_profile_version: str = "unknown",
                         request_profile_hash: str = "unknown") -> dict:
    schema_version, _ = version_policy.require_version(
        schema_version, context="analysis partial cache")
    chunk_input_hash = hashlib.sha256(chunk_input.encode("utf-8")).hexdigest()
    context_hash = hashlib.sha256((context or "").encode("utf-8")).hexdigest()
    identity = {
        "sourceTextHash": source_text_hash,
        "chunkIndex": int(chunk_index),
        "chunkInputHash": chunk_input_hash,
        "chunkingVersion": chunking_version,
        "segmentationVersion": segmentation_version,
        "spanHashes": list(span_hashes or []),
        "outputMode": output_mode,
        "annotationSchemaId": annotation_schema_id,
        "annotationSchemaVersion": annotation_schema_version,
        "annotationSchemaHash": annotation_schema_hash,
        "capabilityVersion": capability_version,
        "requestProfileVersion": request_profile_version,
        "requestProfileHash": request_profile_hash,
        "contextHash": context_hash,
        "promptHash": prompt_hash,
        "promptVersion": prompt_version,
        "schemaVersion": int(schema_version),
        "analysisProfile": analysis_profile,
        "providerIdentity": provider_identity,
        "modelIdentity": model_identity,
        "providerConfigVersion": provider_config_version,
        "fallbackModels": list(fallback_models or []),
        "language": language or "",
        "category": category or "",
        "partialSchemaVersion": PARTIAL_SCHEMA_VERSION,
    }
    identity["cacheKey"] = _canonical_hash(identity)
    return identity


def _now() -> datetime:
    return datetime.now()


class AnalysisPartialSession:
    """Analysis-scoped facade over identity, claim, publish and reuse."""

    def __init__(self, *, analysis_id: int, book_id: int, chapter_id: int,
                 source_text_hash: str, prompt_version: str, schema_version: int,
                 analysis_profile: str, provider: dict, language: str, category: str,
                 source_hash_getter, segmentation_version: str = "legacy-text-v1",
                 span_hashes_by_chunk: dict[int, list[str]] | None = None,
                 output_mode: str = "legacy_json",
                 annotation_schema_id: str = "legacy",
                 annotation_schema_version: str = "legacy",
                 annotation_schema_hash: str = "legacy",
                 capability_version: str = "unknown",
                 request_profile_version: str = "unknown",
                 request_profile_hash: str = "unknown"):
        self.analysis_id = analysis_id
        self.book_id = book_id
        self.chapter_id = chapter_id
        self.source_text_hash = source_text_hash
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self.analysis_profile = analysis_profile
        self.provider = provider
        self.language = language
        self.category = category
        self.source_hash_getter = source_hash_getter
        self.segmentation_version = segmentation_version
        self.span_hashes_by_chunk = span_hashes_by_chunk or {}
        self.output_mode = output_mode
        self.annotation_schema_id = annotation_schema_id
        self.annotation_schema_version = annotation_schema_version
        self.annotation_schema_hash = annotation_schema_hash
        self.capability_version = capability_version
        self.request_profile_version = request_profile_version
        self.request_profile_hash = request_profile_hash
        self.reused_chunks = 0
        self.requested_chunks = 0
        self.saved_provider_requests = 0

    def identity_for(self, index: int, chunk: str, *, context: str, prompt_hash: str) -> dict:
        fallback = []
        if self.provider.get("fallback_model"):
            fallback.append(self.provider["fallback_model"])
        return build_cache_identity(
            source_text_hash=self.source_text_hash, chunk_index=index, chunk_input=chunk,
            context=context, prompt_hash=prompt_hash, prompt_version=self.prompt_version,
            schema_version=self.schema_version, analysis_profile=self.analysis_profile,
            provider_identity=str(self.provider.get("id") or self.provider.get("base_url") or "unknown"),
            model_identity=str(self.provider.get("model") or ""),
            provider_config_version=self.provider.get("config_version"),
            fallback_models=fallback, language=self.language, category=self.category,
            segmentation_version=self.segmentation_version,
            span_hashes=self.span_hashes_by_chunk.get(index, []),
            output_mode=self.output_mode,
            annotation_schema_id=self.annotation_schema_id,
            annotation_schema_version=self.annotation_schema_version,
            annotation_schema_hash=self.annotation_schema_hash,
            capability_version=self.capability_version,
            request_profile_version=self.request_profile_version,
            request_profile_hash=self.request_profile_hash,
        )

    def _partial_row(self, identity: dict) -> dict:
        row = db.get_analysis_partial(identity["cacheKey"])
        if row:
            return row
        return db.create_analysis_partial({
            "cache_key": identity["cacheKey"], "analysis_id": self.analysis_id,
            "book_id": self.book_id, "chapter_id": self.chapter_id,
            "source_text_hash": identity["sourceTextHash"], "chunk_index": identity["chunkIndex"],
            "chunk_input_hash": identity["chunkInputHash"], "chunking_version": identity["chunkingVersion"],
            "context_hash": identity["contextHash"], "prompt_hash": identity["promptHash"],
            "prompt_version": identity["promptVersion"], "schema_version": identity["schemaVersion"],
            "analysis_profile": identity["analysisProfile"], "provider_identity": identity["providerIdentity"],
            "model_identity": identity["modelIdentity"],
            "provider_config_version": identity["providerConfigVersion"],
            "fallback_models_json": json.dumps(identity["fallbackModels"], ensure_ascii=False),
            "language": identity["language"], "category": identity["category"],
        })

    def reuse(self, identity: dict):
        row = self._partial_row(identity)
        if row.get("state") != "succeeded":
            return None
        if self.source_hash_getter() != self.source_text_hash:
            db.mark_analysis_partial_stale(identity["cacheKey"], "source_changed")
            return None
        path = row.get("storage_path") or ""
        absolute = os.path.join(settings.ROOT_DIR, path) if path else ""
        try:
            with open(absolute, "rb") as fh:
                raw = fh.read(MAX_PARTIAL_BYTES + 1)
            if len(raw) > MAX_PARTIAL_BYTES:
                raise ValueError("partial payload too large")
            if hashlib.sha256(raw).hexdigest() != row.get("storage_sha256"):
                raise ValueError("partial checksum mismatch")
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
                raise ValueError("partial payload invalid")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            db.mark_analysis_partial_stale(identity["cacheKey"], "partial_missing_or_corrupt")
            return None
        db.touch_analysis_partial(identity["cacheKey"])
        self.reused_chunks += 1
        self.saved_provider_requests += max(0, int(row.get("request_count") or 0))
        return result

    def claim(self, identity: dict):
        row = self._partial_row(identity)
        if row.get("state") == "succeeded":
            return row
        token = uuid.uuid4().hex
        lease = (_now() + timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="seconds")
        claimed = db.claim_analysis_partial(identity["cacheKey"], owner_token=token,
                                            lease_expires_at=lease, analysis_id=self.analysis_id)
        if claimed and claimed.get("state") == "running" and claimed.get("owner_token") == token:
            self.requested_chunks += 1
            return {**claimed, "_owner_token": token}
        deadline = time.time() + MAX_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(0.05)
            found = db.get_analysis_partial(identity["cacheKey"])
            if found and found.get("state") == "succeeded":
                reused = self.reuse(identity)
                if reused is not None:
                    return {**found, "_reused_result": reused}
                break
        raise PartialClaimBusy("partial chunk is owned by another worker")

    def publish(self, identity: dict, owner_token: str, result: dict) -> None:
        if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
            raise PartialCacheError("partial payload invalid")
        if self.source_hash_getter() != self.source_text_hash:
            db.mark_analysis_partial_failed(identity["cacheKey"], owner_token=owner_token,
                                            failure_code="source_changed")
            raise PartialCacheError("source changed before partial publish")
        rel = os.path.join("storage", "analysis_partials", identity["cacheKey"][:2],
                           f"{identity['cacheKey']}-{owner_token}.json")
        absolute = os.path.join(settings.ROOT_DIR, rel)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        raw = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_PARTIAL_BYTES:
            raise PartialCacheError("partial payload too large")
        temp = absolute + ".tmp"
        with open(temp, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, absolute)
        published = db.publish_analysis_partial(
            identity["cacheKey"], owner_token=owner_token, storage_path=rel,
            storage_sha256=hashlib.sha256(raw).hexdigest(), source_text_hash=self.source_text_hash)
        if not published:
            try:
                os.remove(absolute)
            except OSError:
                pass
            raise PartialCacheError("partial publish CAS rejected")

    def fail(self, identity: dict, owner_token: str, code: str):
        db.increment_analysis_partial_metrics(
            identity["cacheKey"], request_count=0, retry_count=0)
        db.mark_analysis_partial_failed(identity["cacheKey"], owner_token=owner_token,
                                        failure_code=code)

    def record_metrics(self, identity: dict, *, request_count: int, retry_count: int,
                       usage_metrics: dict | None = None):
        db.increment_analysis_partial_metrics(
            identity["cacheKey"], request_count=request_count, retry_count=retry_count,
            usage_metrics=usage_metrics)


def cleanup_expired_partials(*, succeeded_days: int = 7, other_hours: int = 24, limit: int = 100):
    now = _now()
    rows = db.cleanup_analysis_partials(
        cutoff_succeeded=(now - timedelta(days=succeeded_days)).isoformat(timespec="seconds"),
        cutoff_other=(now - timedelta(hours=other_hours)).isoformat(timespec="seconds"),
        limit=min(limit, 100),
    )
    for row in rows:
        path = row.get("storage_path") or ""
        if path:
            try:
                os.remove(os.path.join(settings.ROOT_DIR, path))
            except OSError:
                pass
    return rows
