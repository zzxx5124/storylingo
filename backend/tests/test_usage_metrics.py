from types import SimpleNamespace
import threading

from backend import analyzer, db
from backend.services import usage_metrics


def _response():
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        )
    )


def test_usage_aggregate_preserves_request_kinds_and_provider_details():
    metrics = {"lock": __import__("threading").Lock(), "cache_hit_chunks": 1,
               "saved_provider_requests": 2}
    for kind in ("primary", "targeted_completion", "repair", "reference_repair", "retry"):
        usage_metrics.record_request(metrics, _response(), kind=kind)

    result = usage_metrics.snapshot(metrics)
    assert result["providerRequestCount"] == 5
    assert result["usageObservedRequestCount"] == 5
    assert result["usageMissingRequestCount"] == 0
    assert result["inputTokens"] == 50
    assert result["outputTokens"] == 25
    assert result["totalTokens"] == 75
    assert result["cachedInputTokens"] == 10
    assert result["reasoningTokens"] == 15
    assert result["primaryRequestCount"] == 1
    assert result["targetedCompletionRequestCount"] == 1
    assert result["repairRequestCount"] == 1
    assert result["referenceRepairRequestCount"] == 1
    assert result["retryRequestCount"] == 1
    assert result["reusedChunks"] == 1
    assert result["savedProviderRequests"] == 2


def test_usage_missing_response_is_counted_without_fabricating_tokens():
    metrics = {"lock": __import__("threading").Lock()}
    usage_metrics.record_attempt(metrics, kind="primary")
    result = usage_metrics.snapshot(metrics)
    assert result["providerRequestCount"] == 1
    assert result["usageMissingRequestCount"] == 1
    assert result["usageAvailable"] is False
    assert result["inputTokens"] == 0


def test_usage_schema_columns_exist_on_fresh_database():
    for table in ("generation_jobs", "chapter_analyses", "analysis_partials"):
        columns = {row["name"] for row in db.query(f"PRAGMA table_info({table})")}
        assert "usage_metrics_json" in columns


def test_usage_merge_is_bounded_and_provider_neutral():
    merged = usage_metrics.merge(
        usage_metrics.empty(),
        {"inputTokens": 2_000_000_001, "providerRequestCount": 3,
         "unknownProviderField": 99},
    )
    assert merged["inputTokens"] == 2_000_000_000
    assert merged["providerRequestCount"] == 3
    assert "unknownProviderField" not in merged


def test_cumulative_progress_snapshots_do_not_double_count_parallel_callbacks():
    first = usage_metrics.empty()
    first.update({"providerRequestCount": 2, "inputTokens": 20})
    later = usage_metrics.empty()
    later.update({"providerRequestCount": 3, "inputTokens": 30})
    assert usage_metrics.max_snapshot(first, later)["providerRequestCount"] == 3
    assert usage_metrics.max_snapshot(first, later)["inputTokens"] == 30


def test_analyzer_request_path_records_provider_usage_without_response_body():
    metrics = {"lock": threading.Lock(), "usage": usage_metrics.empty(),
               "request_count": 0}
    budget = {"lock": threading.Lock(), "usage": usage_metrics.empty(),
              "requests": 0, "max_requests": 6, "malformed_retries": 0,
              "transient_retries": 0, "retries": 0}
    analyzer._request_budget_local.current = budget
    try:
        analyzer._invoke(lambda: _response(), metrics, request_kind="repair")
    finally:
        analyzer._request_budget_local.current = None
    assert usage_metrics.snapshot(metrics)["repairRequestCount"] == 1
    assert usage_metrics.snapshot(metrics)["totalTokens"] == 15
    assert usage_metrics.snapshot(budget)["totalTokens"] == 15
