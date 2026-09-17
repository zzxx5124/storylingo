"""Provider-neutral, bounded token usage accounting for AI analysis."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


USAGE_SCHEMA_VERSION = "ai-usage-v1"
MAX_METRIC_VALUE = 2_000_000_000


def empty() -> dict[str, Any]:
    return {
        "schemaVersion": USAGE_SCHEMA_VERSION,
        "usageAvailable": False,
        "usageObservedRequestCount": 0,
        "usageMissingRequestCount": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "cachedInputTokens": 0,
        "reasoningTokens": 0,
        "providerRequestCount": 0,
        "primaryRequestCount": 0,
        "targetedCompletionRequestCount": 0,
        "repairRequestCount": 0,
        "referenceRepairRequestCount": 0,
        "retryRequestCount": 0,
        "reusedChunks": 0,
        "savedProviderRequests": 0,
    }


def _value(value: Any) -> int:
    try:
        return max(0, min(int(value), MAX_METRIC_VALUE))
    except (TypeError, ValueError):
        return 0


def _get(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        result = getattr(value, name, None)
        if result is not None:
            return result
    return None


def _usage_from_response(response: Any) -> tuple[Any, bool]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage")
    return usage, usage is not None


def _kind_key(aggregate: dict[str, Any], kind: str) -> None:
    key = {
        "primary": "primaryRequestCount",
        "targeted_completion": "targetedCompletionRequestCount",
        "repair": "repairRequestCount",
        "reference_repair": "referenceRepairRequestCount",
        "retry": "retryRequestCount",
    }.get(kind, "primaryRequestCount")
    aggregate[key] = _value(aggregate[key] + 1)


def record_attempt(metrics: dict[str, Any] | None, *, kind: str = "primary") -> None:
    """Record an attempted provider request, even when it has no usage block."""
    if metrics is None:
        return
    with metrics["lock"]:
        aggregate = metrics.setdefault("usage", empty())
        aggregate["providerRequestCount"] = _value(aggregate["providerRequestCount"] + 1)
        _kind_key(aggregate, kind)
        aggregate["usageMissingRequestCount"] = _value(aggregate["usageMissingRequestCount"] + 1)


def record_response(metrics: dict[str, Any] | None, response: Any) -> None:
    """Add bounded provider usage to the already-counted request."""
    if metrics is None:
        return
    usage, available = _usage_from_response(response)
    with metrics["lock"]:
        aggregate = metrics.setdefault("usage", empty())
        if not available:
            return
        aggregate["usageMissingRequestCount"] = max(0, aggregate["usageMissingRequestCount"] - 1)
        aggregate["usageObservedRequestCount"] = _value(aggregate["usageObservedRequestCount"] + 1)
        aggregate["usageAvailable"] = True
        aggregate["inputTokens"] = _value(aggregate["inputTokens"] + _value(_get(usage, "prompt_tokens", "input_tokens")))
        aggregate["outputTokens"] = _value(aggregate["outputTokens"] + _value(_get(usage, "completion_tokens", "output_tokens")))
        aggregate["totalTokens"] = _value(aggregate["totalTokens"] + _value(_get(usage, "total_tokens")))
        prompt_details = _get(usage, "prompt_tokens_details", "input_tokens_details")
        completion_details = _get(usage, "completion_tokens_details", "output_tokens_details")
        aggregate["cachedInputTokens"] = _value(aggregate["cachedInputTokens"] + _value(_get(prompt_details, "cached_tokens")))
        aggregate["reasoningTokens"] = _value(aggregate["reasoningTokens"] + _value(_get(completion_details, "reasoning_tokens")))


def record_request(metrics: dict[str, Any] | None, response: Any, *, kind: str = "primary") -> None:
    """Record one provider response without retaining its body or prompt."""
    record_attempt(metrics, kind=kind)
    record_response(metrics, response)


def snapshot(metrics: dict[str, Any] | None) -> dict[str, Any]:
    with (metrics["lock"] if metrics else _NullLock()):
        return snapshot_unlocked(metrics)


def snapshot_unlocked(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Snapshot for callers that already hold ``metrics['lock']``."""
    result = dict((metrics or {}).get("usage") or empty())
    result["reusedChunks"] = max(0, int((metrics or {}).get("cache_hit_chunks", 0)))
    result["savedProviderRequests"] = max(0, int((metrics or {}).get("saved_provider_requests", 0)))
    return result


def merge(base: dict[str, Any] | None, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(base or empty())
    if not extra:
        return result
    for key in empty():
        if key == "schemaVersion":
            continue
        if key == "usageAvailable":
            result[key] = bool(result.get(key) or extra.get(key))
        else:
            result[key] = _value(_value(result.get(key)) + _value(extra.get(key)))
    result["schemaVersion"] = USAGE_SCHEMA_VERSION
    return result


def max_snapshot(base: dict[str, Any] | None, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """Merge cumulative progress snapshots without double-counting parallel callbacks."""
    result = dict(base or empty())
    if not extra:
        return result
    for key in empty():
        if key == "schemaVersion":
            continue
        if key == "usageAvailable":
            result[key] = bool(result.get(key) or extra.get(key))
        else:
            result[key] = max(_value(result.get(key)), _value(extra.get(key)))
    result["schemaVersion"] = USAGE_SCHEMA_VERSION
    return result


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
