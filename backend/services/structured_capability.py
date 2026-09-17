"""Bounded structured-output capability discovery and persistence.

This service probes only a synthetic annotation request.  It never creates an
analysis/job and never stores provider credentials or raw responses.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from openai import OpenAI

from .. import db
from . import ai_provider, structured_output, ai_request_profile


PROBE_VERSION = "structured-probe-v2"
PROBE_TIMEOUT_SECONDS = 10.0
CAPABILITY_TTL_SECONDS = 24 * 60 * 60
TRANSIENT_TTL_SECONDS = 5 * 60
_REFRESH_LOCK = threading.Lock()


def _now() -> str:
    return db.ts()


def _expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= datetime.now()
    except (TypeError, ValueError):
        return True


def _schema() -> structured_output.AnnotationSchemaDescriptor:
    # Capability discovery proves the transport/schema mechanism with the
    # minimal canonical annotation envelope. Optional semantic fields remain
    # provider/model dependent and are validated when an analysis supplies
    # them; they must not make a healthy strict-capable model look unsupported.
    return structured_output.AnnotationSchemaDescriptor()


def _provider_snapshot(provider: dict, *, state: str, error: dict | None = None) -> dict:
    schema = _schema()
    profile = ai_request_profile.resolve(provider)
    return {
        "providerId": provider["id"],
        "provider": provider.get("provider_type"),
        "model": provider.get("model"),
        "configVersion": provider.get("config_version"),
        "schemaVersion": schema.version,
        "schemaHash": schema.schema_hash,
        "probeVersion": PROBE_VERSION,
        "supportsStrictJsonSchema": state == "supported",
        "supportedSchemaVersions": [schema.version] if state == "supported" else [],
        "supportsJsonObject": True,
        "requestProfileVersion": profile.version,
        "requestProfileHash": profile.hash,
        "capabilityVersion": f"{PROBE_VERSION}:{schema.version}:{profile.hash[:12]}",
        "source": "probed",
        "state": state,
        "error": error or None,
    }


def _persist(provider_id: int, snapshot: dict, *, error: dict | None = None) -> dict:
    checked = _now()
    state = snapshot["state"]
    expires = None
    ttl = CAPABILITY_TTL_SECONDS if structured_output.capability_state_cacheable(state) else TRANSIENT_TTL_SECONDS
    expires = (datetime.now() + timedelta(seconds=ttl)).isoformat(timespec="seconds")
    db.update_ai_provider(provider_id, {
        "structured_capability_json": json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        "structured_capability_status": state,
        "structured_capability_checked_at": checked,
        "structured_capability_expires_at": expires,
        "structured_capability_probe_version": PROBE_VERSION,
        "structured_capability_error": json.dumps(error or {}, ensure_ascii=False, separators=(",", ":"))[:1000],
    })
    return {**snapshot, "checkedAt": checked, "expiresAt": expires}


def refresh(provider_id: int, *, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict:
    """Run one bounded synthetic strict probe and persist its safe outcome."""
    provider = db.get_ai_provider(provider_id)
    if not provider:
        raise ValueError("找不到 AI provider")
    schema = _schema()
    profile = ai_request_profile.resolve(provider)
    try:
        capability = structured_output.StructuredOutputCapability(
            supports_strict_json_schema=True,
            supported_schema_versions=(schema.version,),
            supports_json_object=True,
            capability_version=f"{PROBE_VERSION}:{schema.version}",
            source="probed",
            provider=provider.get("provider_type"),
            model=provider.get("model"),
            state="supported",
        )
        selection = structured_output.select_output_mode(capability, schema, mode="strict")
        response_format = structured_output.build_adapter_response_format(
            selection, schema, adapter_key=provider.get("provider_type", "openai")
        )
        client = OpenAI(
            api_key=ai_provider.decrypt_secret(provider.get("secret_ciphertext", "")),
            base_url=provider["base_url"], timeout=timeout, max_retries=0,
        )
        response = client.chat.completions.create(**profile.request_kwargs(
            model=provider["model"], completion_tokens=256,
            response_format=response_format,
            messages=[
                {"role": "system", "content": "只輸出符合 schema 的 JSON 物件；segments 內的每個欄位都必須存在，沒有資料的欄位使用 null 或空陣列。"},
                {"role": "user", "content": "請標註 synthetic segment seg_probe_001，type 為 narration，並回傳完整 annotation envelope。"},
            ],
        ))
        content = response.choices[0].message.content or ""
        payload = json.loads(content)
        structured_output.validate_annotation_payload(
            payload, {"seg_probe_001"}, schema,
        )
        return _persist(provider_id, _provider_snapshot(provider, state="supported"))
    except Exception as error:
        state = structured_output.classify_probe_error(error)
        safe_error = structured_output.redact_probe_error(error)
        snapshot = _provider_snapshot(provider, state=state, error=safe_error)
        return _persist(provider_id, snapshot, error=safe_error)


def load(provider: dict) -> structured_output.StructuredOutputCapability:
    """Load a valid snapshot; never probe during analysis."""
    raw = provider.get("structured_capability_json")
    if not raw or _expired(provider.get("structured_capability_expires_at")):
        return structured_output.StructuredOutputCapability(
            source="probed", provider=provider.get("provider_type"), model=provider.get("model"), state="unknown",
        )
    try:
        snapshot = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(snapshot, dict):
            raise ValueError("invalid capability snapshot")
        if snapshot.get("providerId") != provider.get("id") \
                or snapshot.get("model") != provider.get("model") \
                or snapshot.get("configVersion") != provider.get("config_version") \
                or snapshot.get("probeVersion") != PROBE_VERSION \
                or snapshot.get("schemaVersion") != _schema().version \
                or snapshot.get("requestProfileVersion") != ai_request_profile.resolve(provider).version \
                or snapshot.get("requestProfileHash") != ai_request_profile.resolve(provider).hash:
            raise ValueError("stale capability snapshot")
        return structured_output.capability_from_provider(snapshot, source="probed")
    except (TypeError, ValueError, json.JSONDecodeError):
        return structured_output.StructuredOutputCapability(
            source="probed", provider=provider.get("provider_type"), model=provider.get("model"), state="unknown",
        )


def load_for_analysis(provider: dict) -> structured_output.StructuredOutputCapability:
    """Load a capability snapshot and refresh only when it is absent/expired.

    A capability probe is not part of every analysis request.  It is used as a
    bounded cache refresh when the previous snapshot has expired, preventing a
    previously verified provider from silently falling back to json_object
    forever while still keeping probe latency out of normal analyses.
    """
    raw = provider.get("structured_capability_json")
    if raw and not _expired(provider.get("structured_capability_expires_at")):
        return load(provider)
    provider_id = provider.get("id")
    if not provider_id:
        return load(provider)
    with _REFRESH_LOCK:
        latest = db.get_ai_provider(provider_id) or provider
        latest_raw = latest.get("structured_capability_json")
        if latest_raw and not _expired(latest.get("structured_capability_expires_at")):
            return load(latest)
        refreshed_snapshot = refresh(provider_id)
        refreshed = db.get_ai_provider(provider_id) or latest
        loaded = load(refreshed)
        if loaded.state != "unknown":
            return loaded
        if isinstance(refreshed_snapshot, dict):
            return structured_output.capability_from_provider(refreshed_snapshot, source="probed")
        return loaded


def invalidate(provider_id: int) -> None:
    db.update_ai_provider(provider_id, {
        "structured_capability_status": "unknown",
        "structured_capability_expires_at": None,
        "structured_capability_error": "",
    })
