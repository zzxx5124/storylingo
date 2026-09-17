"""Provider-neutral structured analysis output boundary.

This module deliberately does not own source text, offsets, spans, or the
schemaVersion-4 canonical artifact.  It only negotiates annotation transport
and validates the provider-neutral annotation envelope before the
source-faithful pipeline consumes it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping


OUTPUT_MODES = ("strict_json_schema", "json_object", "legacy_json")
CAPABILITY_SOURCES = ("declared", "probed", "actual")
CAPABILITY_STATES = ("supported", "unsupported", "invalid_schema", "probe_timeout", "provider_error", "unknown")
COVERAGE_POLICY_VERSION = "annotation-coverage-v1"
TARGETED_COMPLETION_MIN_COVERAGE = 0.8
LOW_COVERAGE_MIN_EXPECTED_SEGMENTS = 16
DIAGNOSTIC_ID_SAMPLE_LIMIT = 32
ANNOTATION_FIELDS = (
    "segment_id",
    "type",
    "speaker",
    "speaker_candidate",
    "speaker_gender",
    "speaker_age",
    "attribution",
    "confidence",
    "emotion",
    "intensity",
    "tone",
    "speaking_style",
    "entity_hints",
    "vocab",
)
CANONICAL_OWNERSHIP_FIELDS = {
    "text", "source_start", "source_end", "span_hash", "source_hash",
}


class StructuredOutputError(ValueError):
    """Machine-readable structured-output contract failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.diagnostics = diagnostics or {}


@dataclass(frozen=True)
class StructuredOutputCapability:
    supports_strict_json_schema: bool = False
    supported_schema_versions: tuple[str, ...] = ()
    supports_json_object: bool = True
    max_completion_tokens: int | None = None
    capability_version: str = "unknown"
    source: str = "declared"
    provider: str | None = None
    model: str | None = None
    state: str = "unknown"

    def __post_init__(self):
        if self.source not in CAPABILITY_SOURCES:
            raise ValueError("invalid capability source")
        if self.max_completion_tokens is not None and self.max_completion_tokens < 1:
            raise ValueError("max_completion_tokens must be positive")
        if self.state not in CAPABILITY_STATES:
            raise ValueError("invalid capability state")


@dataclass(frozen=True)
class AnnotationSchemaDescriptor:
    schema_id: str = "storylingo.analysis.annotation"
    version: str = "1"
    profile: str = "chapter-analysis"
    required_fields: tuple[str, ...] = ("segment_id", "type")
    schema_hash: str = field(init=False)

    def __post_init__(self):
        unknown = set(self.required_fields) - set(ANNOTATION_FIELDS)
        if unknown:
            raise ValueError(f"unknown annotation fields: {sorted(unknown)}")
        object.__setattr__(self, "schema_hash", _hash_json(self.as_json_schema()))

    def as_json_schema(self) -> dict[str, Any]:
        """Return only the provider-neutral annotation schema.

        `text` and source span fields are intentionally absent.  Adapters may
        wrap this schema in their native protocol, but may not add canonical
        ownership fields here.
        """
        item_properties: dict[str, Any] = {
            "segment_id": {"type": "string", "minLength": 1},
            "type": {"type": "string", "enum": ["narration", "dialogue"]},
            "speaker": {"type": ["string", "null"]},
            "speaker_candidate": {"type": ["string", "null"]},
            "speaker_gender": {"type": ["string", "null"]},
            "speaker_age": {"type": ["string", "null"]},
            "attribution": {"type": "object"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "emotion": {"type": "object"},
            "intensity": {"type": "number", "minimum": 0, "maximum": 1},
            "tone": {"type": "string"},
            "speaking_style": {"type": "string"},
            "entity_hints": {"type": "array"},
            # Learning metadata is an annotation, not canonical source text.
            "vocab": {"type": ["object", "null"]},
        }
        item_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": list(self.required_fields),
            "properties": item_properties,
        }
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"{self.schema_id}:{self.version}",
            "type": "object",
            "additionalProperties": False,
            "required": ["segments"],
            "properties": {
                "segments": {"type": "array", "items": item_schema},
            },
        }


@dataclass(frozen=True)
class OutputSelection:
    requested_mode: str
    applied_mode: str
    fallback_reason: str | None
    capability_source: str
    capability_version: str
    schema_id: str
    schema_version: str
    schema_hash: str
    capability_state: str = "unknown"

    def metadata(self) -> dict[str, Any]:
        return {
            "requestedMode": self.requested_mode,
            "appliedMode": self.applied_mode,
            "fallbackReason": self.fallback_reason,
            "capabilitySource": self.capability_source,
            "capabilityVersion": self.capability_version,
            "schemaId": self.schema_id,
            "schemaVersion": self.schema_version,
            "schemaHash": self.schema_hash,
            "capabilityState": self.capability_state,
        }


@dataclass
class StructuredRequestBudget:
    """Shared per-chunk budget for structured request/repair attempts."""

    max_requests: int = 3
    requests: int = 0
    repairs: int = 0
    fallbacks: int = 0

    def consume(self):
        if self.requests >= self.max_requests:
            raise StructuredOutputError("request_budget_exhausted", "structured request budget exhausted")
        self.requests += 1


@dataclass(frozen=True)
class StructuredRunResult:
    annotations: list[dict[str, Any]]
    selection: OutputSelection
    request_count: int
    repair_count: int
    fallback_count: int


def run_annotation_request(
    *,
    segment_ids: list[str],
    schema: AnnotationSchemaDescriptor,
    capability: StructuredOutputCapability,
    transport,
    repair=None,
    mode: str = "best_effort",
    budget: StructuredRequestBudget | None = None,
) -> StructuredRunResult:
    """Execute one bounded annotation request through an adapter callback.

    ``transport`` is the provider adapter boundary.  It receives the selected
    provider-neutral mode and request plan and returns a decoded mapping.  It
    must not be given or return canonical source text/spans.  This runner never
    retries a deterministic schema failure as a full chapter.
    """
    budget = budget or StructuredRequestBudget()
    selection = select_output_mode(capability, schema, mode=mode)
    request = build_annotation_request(segment_ids, selection, schema)
    try:
        budget.consume()
        payload = transport(selection.applied_mode, request)
    except StructuredOutputError as error:
        if error.code != "structured_output_unsupported" or mode == "strict" or not capability.supports_json_object:
            raise
        budget.fallbacks += 1
        selection = OutputSelection(
            requested_mode=mode,
            applied_mode="json_object",
            fallback_reason="runtime_structured_output_unsupported",
            capability_source=capability.source,
            capability_version=capability.capability_version,
            schema_id=schema.schema_id,
            schema_version=schema.version,
            schema_hash=schema.schema_hash,
            capability_state=capability.state,
        )
        request = build_annotation_request(segment_ids, selection, schema)
        budget.consume()
        payload = transport(selection.applied_mode, request)

    try:
        annotations = validate_annotation_payload(payload, set(segment_ids), schema)
    except StructuredOutputError as error:
        if error.code != "structured_schema_invalid" or repair is None or budget.repairs >= 1:
            raise
        budget.repairs += 1
        budget.consume()
        repaired_payload = repair(payload)
        annotations = validate_annotation_payload(repaired_payload, set(segment_ids), schema)
    return StructuredRunResult(
        annotations=annotations,
        selection=selection,
        request_count=budget.requests,
        repair_count=budget.repairs,
        fallback_count=budget.fallbacks,
    )


def _hash_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def capability_from_provider(raw: Mapping[str, Any] | None, *, source: str = "declared") -> StructuredOutputCapability:
    """Normalize provider capability data without treating missing data as down."""
    raw = raw or {}
    if source not in CAPABILITY_SOURCES:
        raise ValueError("invalid capability source")
    strict_versions = raw.get("supported_schema_versions") or raw.get("supportedSchemaVersions") or ()
    if isinstance(strict_versions, str):
        strict_versions = (strict_versions,)
    return StructuredOutputCapability(
        supports_strict_json_schema=bool(
            raw.get("supports_strict_json_schema", raw.get("supportsStrictJsonSchema", False))
        ),
        supported_schema_versions=tuple(str(v) for v in strict_versions),
        supports_json_object=bool(raw.get("supports_json_object", raw.get("supportsJsonObject", True))),
        max_completion_tokens=_positive_int(
            raw.get("max_completion_tokens", raw.get("maxCompletionTokens"))
        ),
        capability_version=str(raw.get("capability_version", raw.get("capabilityVersion", "unknown"))),
        source=source,
        provider=raw.get("provider"),
        model=raw.get("model"),
        state=str(raw.get("state", raw.get("capability_state", "unknown"))),
    )


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def select_output_mode(
    capability: StructuredOutputCapability,
    schema: AnnotationSchemaDescriptor,
    *,
    mode: str = "best_effort",
) -> OutputSelection:
    """Select transport mode; native provider mapping remains outside core."""
    if mode not in ("best_effort", "strict"):
        raise StructuredOutputError("invalid_output_mode", "unsupported output mode")
    strict_ok = (
        capability.supports_strict_json_schema
        and schema.version in capability.supported_schema_versions
        and capability.state not in ("invalid_schema", "probe_timeout", "provider_error")
    )
    if strict_ok:
        applied = "strict_json_schema"
        reason = None
    elif mode == "strict":
        raise StructuredOutputError(
            "structured_output_unsupported",
            "provider/model does not support the requested structured-output schema",
        )
    elif capability.supports_json_object:
        applied = "json_object"
        reason = {
            "probe_timeout": "capability_probe_timeout",
            "provider_error": "capability_provider_error",
            "invalid_schema": "capability_invalid_schema",
        }.get(capability.state, "unsupported_structured_output")
    else:
        applied = "legacy_json"
        reason = "unsupported_structured_output"
    return OutputSelection(
        requested_mode=mode,
        applied_mode=applied,
        fallback_reason=reason,
        capability_source=capability.source,
        capability_version=capability.capability_version,
        schema_id=schema.schema_id,
        schema_version=schema.version,
        schema_hash=schema.schema_hash,
        capability_state=capability.state,
    )


def build_annotation_request(
    segment_ids: list[str],
    selection: OutputSelection,
    schema: AnnotationSchemaDescriptor,
    *,
    read_only_context: str = "",
) -> dict[str, Any]:
    """Build the provider-neutral request plan.

    An adapter may map ``output_format`` to its native request field.  The
    plan intentionally carries segment references and read-only context, not
    canonical source text or source offsets.
    """
    if len(segment_ids) != len(set(segment_ids)) or not all(segment_ids):
        raise StructuredOutputError("invalid_segment_id", "segment references must be unique and non-empty")
    return {
        "output_format": {
            "mode": selection.applied_mode,
            "schema_id": schema.schema_id,
            "schema_version": schema.version,
            "schema_hash": schema.schema_hash,
            "schema": schema.as_json_schema() if selection.applied_mode == "strict_json_schema" else None,
        },
        "segment_ids": list(segment_ids),
        "read_only_context": read_only_context,
    }


def build_adapter_response_format(
    selection: OutputSelection,
    schema: AnnotationSchemaDescriptor,
    *,
    adapter_key: str,
    segment_ids: list[str] | None = None,
    require_exact_count: bool = True,
) -> dict[str, Any] | None:
    """Map the provider-neutral plan at the adapter boundary.

    The analysis contract never stores this result.  Current OpenAI-compatible
    adapters use the common chat-completions shape; other adapters may return
    their own equivalent mapping without changing the core schema.
    """
    if adapter_key not in ("openai", "openai_compatible", "deepseek", "nvidia"):
        raise StructuredOutputError("unsupported_provider_adapter", "structured output adapter is unsupported")
    if selection.applied_mode == "strict_json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema.schema_id.replace(".", "_"),
                "strict": True,
            "schema": build_openai_strict_schema(
                schema, segment_ids=segment_ids,
                require_exact_count=require_exact_count,
            ),
            },
        }
    if selection.applied_mode == "json_object":
        return {"type": "json_object"}
    return None


def _nullable(kind: str) -> dict[str, Any]:
    return {"type": [kind, "null"]}


def _strict_property(name: str) -> dict[str, Any]:
    if name in ("speaker", "speaker_candidate", "speaker_gender", "speaker_age", "tone", "speaking_style"):
        return _nullable("string")
    if name in ("confidence", "intensity"):
        return {"type": ["number", "null"], "minimum": 0, "maximum": 1}
    if name == "entity_hints":
        return {"type": ["array", "null"], "items": {"type": "string"}}
    if name == "vocab":
        return {
            "type": ["object", "null"], "additionalProperties": False,
            "properties": {
                "zh": {"type": "string", "minLength": 1},
                "en": {"type": "string", "minLength": 1},
                "spelling": {"type": "string", "minLength": 1},
                "example": {"type": "string", "minLength": 1},
                "level": {"type": "string", "minLength": 1},
            },
            "required": ["zh", "en", "spelling", "example", "level"],
        }
    if name == "attribution":
        return {
            "type": ["object", "null"], "additionalProperties": False,
            "properties": {"evidence": _nullable("string"), "source": _nullable("string")},
            "required": ["evidence", "source"],
        }
    if name == "emotion":
        return {
            "type": ["object", "null"], "additionalProperties": False,
            "properties": {"label": _nullable("string"), "intensity": {"type": ["number", "null"]}},
            "required": ["label", "intensity"],
        }
    if name == "segment_id":
        return {"type": "string", "minLength": 1}
    if name == "type":
        return {"type": "string", "enum": ["narration", "dialogue"]}
    raise StructuredOutputError("unsupported_schema_feature", f"unsupported strict field: {name}")


def build_openai_strict_schema(
    schema: AnnotationSchemaDescriptor,
    *,
    segment_ids: list[str] | None = None,
    require_exact_count: bool = True,
) -> dict[str, Any]:
    """Map provider-neutral annotations to OpenAI's strict JSON subset."""
    fields = tuple(dict.fromkeys(schema.required_fields))
    properties = {name: _strict_property(name) for name in fields}
    normalized_ids = None
    if segment_ids is not None:
        normalized_ids = [str(segment_id) for segment_id in segment_ids]
        if not normalized_ids or len(normalized_ids) != len(set(normalized_ids)):
            raise StructuredOutputError("invalid_segment_id", "strict segment references must be unique and non-empty")
        # The allowed IDs are source-owned references, not canonical spans or
        # text.  Constraining this transport field lets strict-capable
        # providers reject a truncated/invented ID before it reaches the
        # source-faithful validator.
        properties["segment_id"] = {"type": "string", "enum": normalized_ids}
    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(fields),
    }
    segments_schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if segment_ids is not None and require_exact_count:
        # A strict provider must return one annotation for every source-owned
        # reference in this request. Without cardinality, a syntactically valid
        # prefix (for example 12 of 50 segments) can pass the provider schema
        # and only fail after costly local coverage validation.
        segments_schema["minItems"] = len(normalized_ids)
        segments_schema["maxItems"] = len(normalized_ids)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"segments": segments_schema},
        "required": ["segments"],
    }


_VOCAB_FIELDS = ("zh", "en", "spelling", "example", "level")


def _validate_vocab_metadata(value: Any) -> None:
    """Validate bounded learning metadata without accepting source ownership."""
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise StructuredOutputError("structured_schema_invalid", "vocab annotation must be an object or null")
    unknown = set(value) - set(_VOCAB_FIELDS)
    if unknown:
        raise StructuredOutputError("structured_schema_invalid", "vocab annotation contains unknown fields")
    missing = [key for key in _VOCAB_FIELDS if not isinstance(value.get(key), str) or not value[key].strip()]
    if missing:
        raise StructuredOutputError("structured_schema_invalid", f"vocab annotation missing fields: {missing}")
    limits = {"zh": 120, "en": 120, "spelling": 240, "example": 600, "level": 32}
    if any(len(value[key]) > limit for key, limit in limits.items()):
        raise StructuredOutputError("structured_schema_invalid", "vocab annotation field is too long")


def redact_probe_error(error: BaseException, *, limit: int = 240) -> dict[str, Any]:
    status = getattr(error, "status_code", None)
    code = getattr(error, "code", None)
    message = re.sub(
        r"(?i)(authorization|api[-_ ]?key|bearer)\s*[:=]?\s*[^\s,;]+",
        "[REDACTED]", str(error),
    )
    return {"status": status, "code": code, "type": type(error).__name__, "message": message[:limit]}


def classify_probe_error(error: BaseException) -> str:
    """Classify probe failure; HTTP 400 is not automatically unsupported."""
    name = type(error).__name__.lower()
    status = getattr(error, "status_code", None)
    code = str(getattr(error, "code", "") or "").lower()
    message = str(error).lower()
    if code in {"structured_schema_invalid", "source_ownership_violation", "invalid_segment_id"}:
        return "invalid_schema"
    if "timeout" in name or "timeout" in message:
        return "probe_timeout"
    if status in (404, 405, 501) or "not_supported" in code or "unsupported" in message:
        return "unsupported"
    if status == 400:
        if any(token in (code + " " + message) for token in ("schema", "response_format", "json_schema", "structured")):
            return "invalid_schema"
        return "provider_error"
    if status is not None or "api" in name or "provider" in name:
        return "provider_error"
    return "unknown"


def capability_state_cacheable(state: str) -> bool:
    """Only definitive states may be retained as a capability decision."""
    return state in ("supported", "unsupported")


def validate_annotation_payload(
    payload: Mapping[str, Any],
    allowed_segment_ids: set[str],
    schema: AnnotationSchemaDescriptor | None = None,
) -> list[dict[str, Any]]:
    """Validate provider annotations without accepting source ownership data."""
    schema = schema or AnnotationSchemaDescriptor()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("segments"), list):
        raise StructuredOutputError("structured_schema_invalid", "annotation segments must be an array")
    annotations = payload["segments"]
    raw_segment_ids = [
        str(item.get("segment_id"))
        for item in annotations
        if isinstance(item, Mapping) and "segment_id" in item
    ]
    counts = {}
    for segment_id in raw_segment_ids:
        counts[segment_id] = counts.get(segment_id, 0) + 1
    duplicate_ids = {segment_id for segment_id, count in counts.items() if count > 1}
    unknown_ids = {segment_id for segment_id in raw_segment_ids if segment_id not in allowed_segment_ids}
    valid_ids = {segment_id for segment_id in raw_segment_ids if segment_id in allowed_segment_ids}
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in annotations:
        if not isinstance(item, Mapping):
            raise StructuredOutputError("structured_schema_invalid", "annotation must be an object")
        forbidden = CANONICAL_OWNERSHIP_FIELDS.intersection(item)
        if forbidden:
            raise StructuredOutputError(
                "source_ownership_violation",
                "structured annotation cannot define source text or spans",
            )
        # Required means the annotation contract must carry the key.  Nullable
        # fields (for example an unresolved dialogue speaker) are valid values.
        missing = [key for key in schema.required_fields if key not in item]
        if missing:
            raise StructuredOutputError("structured_schema_invalid", f"missing annotation fields: {missing}")
        if "vocab" in item:
            _validate_vocab_metadata(item.get("vocab"))
        segment_id = item["segment_id"]
        if segment_id not in allowed_segment_ids:
            raise StructuredOutputError(
                "invalid_segment_id", "annotation references an unknown segment",
                diagnostics=_coverage_diagnostics(
                    expected_count=len(allowed_segment_ids),
                    returned_count=len(annotations),
                    missing=allowed_segment_ids - valid_ids,
                    duplicate=duplicate_ids,
                    unknown=unknown_ids,
                ),
            )
        if segment_id in seen:
            raise StructuredOutputError(
                "duplicate_segment_id", "annotation segment_id is duplicated",
                diagnostics=_coverage_diagnostics(
                    expected_count=len(allowed_segment_ids),
                    returned_count=len(annotations),
                    missing=allowed_segment_ids - valid_ids,
                    duplicate=duplicate_ids,
                    unknown=unknown_ids,
                ),
            )
        if item["type"] not in ("narration", "dialogue"):
            raise StructuredOutputError(
                "structured_schema_invalid", "invalid annotation type",
                diagnostics={
                    "allowedTypes": ["narration", "dialogue"],
                    "returnedType": str(item.get("type"))[:80],
                },
            )
        seen.add(segment_id)
        result.append(dict(item))
    if seen != allowed_segment_ids:
        missing = allowed_segment_ids - seen
        diagnostics = _coverage_diagnostics(
            expected_count=len(allowed_segment_ids), returned_count=len(annotations),
            missing=missing,
        )
        coverage = len(seen) / len(allowed_segment_ids) if allowed_segment_ids else 1.0
        if (len(allowed_segment_ids) >= LOW_COVERAGE_MIN_EXPECTED_SEGMENTS
                and coverage < TARGETED_COMPLETION_MIN_COVERAGE):
            raise StructuredOutputError(
                "annotation_coverage_low", "annotation coverage is below targeted completion threshold",
                diagnostics={**diagnostics, "coverage": round(coverage, 4)},
            )
        raise StructuredOutputError(
            "annotation_coverage_incomplete", "not every source segment was annotated",
            diagnostics={**diagnostics, "coverage": round(coverage, 4)},
        )
    return result


def _bounded_ids(values: set[str], *, limit: int = 64) -> list[str]:
    return sorted(str(value) for value in values)[:limit]


def _coverage_diagnostics(*, expected_count: int, returned_count: int,
                          missing: set[str], duplicate: set[str] | None = None,
                          unknown: set[str] | None = None) -> dict[str, Any]:
    """Build a compact, serializable coverage diagnostic envelope."""
    sample = sorted(str(value) for value in missing)[:DIAGNOSTIC_ID_SAMPLE_LIMIT]
    return {
        "coveragePolicyVersion": COVERAGE_POLICY_VERSION,
        "expectedCount": int(expected_count),
        "returnedCount": int(returned_count),
        "missingCount": len(missing),
        "duplicateCount": len(duplicate or set()),
        "unknownCount": len(unknown or set()),
        "missingSegmentIdsSample": sample,
        "sampleTruncated": len(missing) > len(sample),
        # Kept as bounded compatibility aliases for existing callers.
        "expectedSegmentCount": int(expected_count),
        "returnedSegmentCount": int(returned_count),
        "missingSegmentIds": sample,
        "missingSegmentCount": len(missing),
        "duplicateSegmentIds": _bounded_ids(duplicate or set(), limit=DIAGNOSTIC_ID_SAMPLE_LIMIT),
        "unknownSegmentIds": _bounded_ids(unknown or set(), limit=DIAGNOSTIC_ID_SAMPLE_LIMIT),
    }


def validate_annotation_subset(
    payload: Mapping[str, Any],
    allowed_segment_ids: set[str],
    schema: AnnotationSchemaDescriptor | None = None,
) -> list[dict[str, Any]]:
    """Validate and return the usable subset without requiring full coverage."""
    schema = schema or AnnotationSchemaDescriptor()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("segments"), list):
        raise StructuredOutputError("structured_schema_invalid", "annotation segments must be an array")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload["segments"]:
        if not isinstance(item, Mapping):
            raise StructuredOutputError("structured_schema_invalid", "annotation must be an object")
        forbidden = CANONICAL_OWNERSHIP_FIELDS.intersection(item)
        if forbidden:
            raise StructuredOutputError("source_ownership_violation", "structured annotation cannot define source text or spans")
        missing = [key for key in schema.required_fields if key not in item]
        if missing:
            raise StructuredOutputError("structured_schema_invalid", f"missing annotation fields: {missing}")
        if "vocab" in item:
            _validate_vocab_metadata(item.get("vocab"))
        segment_id = str(item["segment_id"])
        if segment_id not in allowed_segment_ids:
            raise StructuredOutputError("invalid_segment_id", "annotation references an unknown segment")
        if segment_id in seen:
            raise StructuredOutputError("duplicate_segment_id", "annotation segment_id is duplicated")
        if item["type"] not in ("narration", "dialogue"):
            raise StructuredOutputError(
                "structured_schema_invalid", "invalid annotation type",
                diagnostics={
                    "allowedTypes": ["narration", "dialogue"],
                    "returnedType": str(item.get("type"))[:80],
                },
            )
        seen.add(segment_id)
        result.append(dict(item))
    return result


def cache_identity_inputs(
    *,
    source_hash: str,
    segmentation_version: str,
    span_hashes: list[str],
    schema: AnnotationSchemaDescriptor,
    output_mode: OutputSelection,
    prompt_hash: str,
    prompt_version: str,
    profile: str,
    provider: str,
    model: str,
    provider_config_version: str,
    fallback_model_chain: list[str],
) -> dict[str, Any]:
    """Return explicit cache inputs; analysis_id is intentionally absent."""
    return {
        "sourceHash": source_hash,
        "segmentationVersion": segmentation_version,
        "spanHashes": list(span_hashes),
        "schemaId": schema.schema_id,
        "schemaVersion": schema.version,
        "schemaHash": schema.schema_hash,
        "outputMode": output_mode.applied_mode,
        "capabilityVersion": output_mode.capability_version,
        "capabilityState": output_mode.capability_state,
        "promptHash": prompt_hash,
        "promptVersion": prompt_version,
        "analysisProfile": profile,
        "provider": provider,
        "model": model,
        "providerConfigVersion": provider_config_version,
        "fallbackModelChain": list(fallback_model_chain),
    }
