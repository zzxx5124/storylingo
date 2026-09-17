import pytest
import json
import threading
from types import SimpleNamespace
from pathlib import Path

from backend import analyzer
from backend import db
from backend.services import analysis_executor
from backend.services import analysis as analysis_svc
from backend.services.analysis_partials import build_cache_identity

from backend.services.structured_output import (
    AnnotationSchemaDescriptor,
    StructuredOutputCapability,
    StructuredOutputError,
    capability_from_provider,
    build_annotation_request,
    cache_identity_inputs,
    select_output_mode,
    StructuredRequestBudget,
    run_annotation_request,
    validate_annotation_payload,
    build_openai_strict_schema,
    classify_probe_error,
    capability_state_cacheable,
    COVERAGE_POLICY_VERSION,
    TARGETED_COMPLETION_MIN_COVERAGE,
)
from backend.tests.conftest import new_admin, upload_book


def test_strict_capability_is_selected_and_schema_owns_annotations_only():
    schema = AnnotationSchemaDescriptor()
    capability = capability_from_provider({
        "supportsStrictJsonSchema": True,
        "supportedSchemaVersions": ["1"],
        "capabilityVersion": "cap-2",
    }, source="probed")

    selected = select_output_mode(capability, schema)

    assert selected.applied_mode == "strict_json_schema"
    assert selected.fallback_reason is None
    assert selected.capability_source == "probed"
    assert "text" not in schema.as_json_schema()["properties"]
    assert "source_start" not in schema.as_json_schema()["properties"]

    request = build_annotation_request(["seg_1"], selected, schema, read_only_context="previous context")
    assert request["segment_ids"] == ["seg_1"]
    assert request["read_only_context"] == "previous context"
    assert request["output_format"]["mode"] == "strict_json_schema"
    assert request["output_format"]["schema"]["properties"].get("text") is None


def test_openai_mapper_removes_draft_metadata_and_uses_strict_required_fields():
    schema = AnnotationSchemaDescriptor(required_fields=("segment_id", "type", "speaker"))
    mapped = build_openai_strict_schema(schema)
    assert set(mapped) == {"type", "additionalProperties", "properties", "required"}
    assert mapped["required"] == ["segments"]
    item_schema = mapped["properties"]["segments"]["items"]
    assert item_schema["required"] == ["segment_id", "type", "speaker"]
    assert mapped["additionalProperties"] is False
    assert item_schema["properties"]["speaker"]["type"] == ["string", "null"]
    assert "$schema" not in mapped and "$id" not in mapped


def test_openai_strict_schema_can_constrain_source_owned_segment_references():
    schema = AnnotationSchemaDescriptor(required_fields=("segment_id", "type"))
    mapped = build_openai_strict_schema(schema, segment_ids=["seg_a", "seg_b"])
    assert mapped["properties"]["segments"]["items"]["properties"]["segment_id"] == {
        "type": "string", "enum": ["seg_a", "seg_b"],
    }
    assert mapped["properties"]["segments"]["minItems"] == 2
    assert mapped["properties"]["segments"]["maxItems"] == 2


def test_annotation_schema_supports_provider_neutral_speaker_profile_fields():
    schema = AnnotationSchemaDescriptor(
        required_fields=("segment_id", "type", "speaker_gender", "speaker_age"),
    )
    mapped = build_openai_strict_schema(schema, segment_ids=["seg_1"])
    item = mapped["properties"]["segments"]["items"]
    assert item["properties"]["speaker_gender"]["type"] == ["string", "null"]
    assert item["properties"]["speaker_age"]["type"] == ["string", "null"]


def test_analyzer_request_profile_uses_gpt5_completion_tokens_and_omits_temperature():
    kwargs = analyzer._chat_request_kwargs(
        model="gpt-5-nano", messages=[], completion_tokens=256,
    )
    assert kwargs["max_completion_tokens"] == 2048
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


def test_analyzer_request_profile_preserves_gpt4o_mini_transport_parameters():
    kwargs = analyzer._chat_request_kwargs(
        model="gpt-4o-mini", messages=[], completion_tokens=256,
    )
    assert kwargs["max_tokens"] == 256
    assert kwargs["temperature"] == 0.2


def test_partial_identity_changes_when_request_profile_changes():
    common = dict(
        source_text_hash="source", chunk_index=0, chunk_input="text", context="",
        prompt_hash="prompt", prompt_version="v4", schema_version=4,
        analysis_profile="speaker", provider_identity="41", model_identity="gpt-5-nano",
        provider_config_version=1, fallback_models=[], language="zh", category="novel",
    )
    original = build_cache_identity(
        **common, request_profile_version="v1", request_profile_hash="hash-a",
    )
    changed = build_cache_identity(
        **common, request_profile_version="v2", request_profile_hash="hash-b",
    )
    assert original["cacheKey"] != changed["cacheKey"]


def test_probe_failure_classification_keeps_timeout_and_invalid_schema_distinct():
    class TimeoutError(Exception):
        pass

    class BadRequest(Exception):
        status_code = 400
        code = "invalid_json_schema"

    class Unsupported(Exception):
        status_code = 400
        code = "response_format_not_supported"

    assert classify_probe_error(TimeoutError("timed out")) == "probe_timeout"
    assert classify_probe_error(BadRequest("schema rejected")) == "invalid_schema"
    assert classify_probe_error(Unsupported("unsupported")) == "unsupported"
    assert capability_state_cacheable("supported") is True
    assert capability_state_cacheable("unsupported") is True
    assert capability_state_cacheable("invalid_schema") is False
    assert capability_state_cacheable("probe_timeout") is False

    assert classify_probe_error(StructuredOutputError(
        "structured_schema_invalid", "annotation segments must be an array"
    )) == "invalid_schema"


def test_best_effort_falls_back_once_and_strict_fails_machine_readably():
    schema = AnnotationSchemaDescriptor()
    capability = capability_from_provider({"supportsJsonObject": True})

    selected = select_output_mode(capability, schema)
    assert selected.applied_mode == "json_object"
    assert selected.fallback_reason == "unsupported_structured_output"

    with pytest.raises(StructuredOutputError) as error:
        select_output_mode(capability, schema, mode="strict")
    assert error.value.code == "structured_output_unsupported"
    assert error.value.retryable is False


def test_missing_capability_is_not_provider_unavailable_and_legacy_is_available():
    schema = AnnotationSchemaDescriptor()
    capability = capability_from_provider({"supportsJsonObject": False})
    selected = select_output_mode(capability, schema)
    assert selected.applied_mode == "legacy_json"
    assert selected.fallback_reason == "unsupported_structured_output"


def test_annotation_rejects_source_ownership_and_invalid_references():
    schema = AnnotationSchemaDescriptor()
    with pytest.raises(StructuredOutputError) as error:
        validate_annotation_payload({"segments": [{
            "segment_id": "seg_1", "type": "dialogue", "text": "forbidden"
        }]}, {"seg_1"}, schema)
    assert error.value.code == "source_ownership_violation"

    with pytest.raises(StructuredOutputError) as error:
        validate_annotation_payload({"segments": [{
            "segment_id": "unknown", "type": "narration"
        }]}, {"seg_1"}, schema)
    assert error.value.code == "invalid_segment_id"


def test_annotation_validation_requires_exact_segment_coverage():
    schema = AnnotationSchemaDescriptor()
    annotations = {"segments": [
        {"segment_id": "seg_1", "type": "narration"},
        {"segment_id": "seg_1", "type": "narration"},
    ]}
    with pytest.raises(StructuredOutputError) as error:
        validate_annotation_payload(annotations, {"seg_1", "seg_2"}, schema)
    assert error.value.code == "duplicate_segment_id"


def test_duplicate_segment_reference_is_repaired_without_resending_first_annotation(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == 1:
                        data = {"segments": [
                            {"segment_id": "seg_1", "type": "narration"},
                            {"segment_id": "seg_1", "type": "dialogue"},
                        ]}
                    else:
                        data = {"segments": [{"segment_id": "seg_2", "type": "dialogue"}]}
                    return _Response(json.dumps(data))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    metrics = _analysis_metrics()
    result = analyzer._call_structured_annotations(
        "甲\n乙",
        [
            {"segment_id": "seg_1", "type": "narration", "text": "甲"},
            {"segment_id": "seg_2", "type": "dialogue", "text": "乙"},
        ],
        metrics=metrics,
    )

    assert [item["segment_id"] for item in result] == ["seg_1", "seg_2"]
    assert len(calls) == 2
    assert metrics["request_count"] == 2
    repair_prompt = calls[1]["messages"][-1]["content"]
    assert repair_prompt.count('"segment_id": "seg_1"') == 1
    assert '待補的合法 segment_id 只有：["seg_2"]' in repair_prompt


def test_duplicate_segment_reference_repair_exhaustion_is_deterministic(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _Response(json.dumps({"segments": [
                        {"segment_id": "seg_1", "type": "narration"},
                        {"segment_id": "seg_1", "type": "dialogue"},
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    with pytest.raises(StructuredOutputError) as error:
        analyzer._call_structured_annotations(
            "甲\n乙",
            [
                {"segment_id": "seg_1", "type": "narration", "text": "甲"},
                {"segment_id": "seg_2", "type": "dialogue", "text": "乙"},
            ],
        )

    assert error.value.code == "annotation_reference_invalid"
    assert error.value.retryable is False
    assert analysis_svc.is_deterministic_failure(error.value)
    assert len(calls) == 2


def test_annotation_coverage_failure_has_bounded_diagnostics():
    schema = AnnotationSchemaDescriptor()
    with pytest.raises(StructuredOutputError) as error:
        validate_annotation_payload(
            {"segments": [{"segment_id": "seg_1", "type": "narration"}]},
            {"seg_1", "seg_2"}, schema,
        )
    assert error.value.code == "annotation_coverage_incomplete"
    assert error.value.diagnostics["coveragePolicyVersion"] == COVERAGE_POLICY_VERSION
    assert error.value.diagnostics["expectedCount"] == 2
    assert error.value.diagnostics["returnedCount"] == 1
    assert error.value.diagnostics["missingCount"] == 1
    assert error.value.diagnostics["missingSegmentIdsSample"] == ["seg_2"]
    assert error.value.diagnostics["sampleTruncated"] is False


def test_coverage_policy_targets_near_complete_and_rejects_low_coverage():
    def validate(expected, returned):
        ids = {f"seg_{index}" for index in range(expected)}
        payload = {"segments": [{"segment_id": key, "type": "narration"}
                                 for key in sorted(ids)[:returned]]}
        with pytest.raises(StructuredOutputError) as caught:
            validate_annotation_payload(payload, ids)
        return caught.value

    near_complete = validate(151, 150)
    assert near_complete.code == "annotation_coverage_incomplete"
    assert near_complete.diagnostics["coverage"] >= TARGETED_COMPLETION_MIN_COVERAGE

    threshold_boundary = validate(151, 130)
    assert threshold_boundary.code == "annotation_coverage_incomplete"
    assert threshold_boundary.diagnostics["coverage"] >= TARGETED_COMPLETION_MIN_COVERAGE

    low = validate(151, 19)
    assert low.code == "annotation_coverage_low"
    assert low.retryable is False
    assert low.diagnostics["coverage"] < TARGETED_COMPLETION_MIN_COVERAGE
    assert len(low.diagnostics["missingSegmentIdsSample"]) == 32
    assert low.diagnostics["sampleTruncated"] is True


def test_large_coverage_failure_diagnostics_are_valid_json_and_bounded(client):
    admin = new_admin("coverdiag")
    book = upload_book(admin, text="第一章 診斷測試。\n" + "正文內容。\n" * 8)
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = db.create_chapter_analysis({
        "book_id": row["id"], "chapter_id": chapter["id"],
        "source_text_hash": chapter["text_hash"], "schema_version": 4,
        "status": "running",
    })
    failure = analysis_svc.build_failure_error(
        StructuredOutputError(
            "annotation_coverage_low", "coverage too low",
            diagnostics={"missingSegmentIdsSample": [f"seg_{i}" for i in range(200)]},
        ), attempts=1,
    )
    db.mark_analysis_failed(analysis_id, failure)
    stored = db.get_chapter_analysis(analysis_id)["error"]
    parsed = json.loads(stored)
    assert parsed["code"] == "annotation_coverage_low"
    assert len(parsed["diagnostics"]["missingSegmentIdsSample"]) <= 32
    assert len(stored) <= 2000


def test_invalid_annotation_type_reports_allowed_and_returned_value():
    schema = AnnotationSchemaDescriptor()
    with pytest.raises(StructuredOutputError) as error:
        validate_annotation_payload(
            {"segments": [{"segment_id": "seg_1", "type": "narrative"}]},
            {"seg_1"}, schema,
        )
    assert error.value.code == "structured_schema_invalid"
    assert error.value.diagnostics == {
        "allowedTypes": ["narration", "dialogue"],
        "returnedType": "narrative",
    }


def test_structured_coverage_completion_requests_only_missing_ids(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    ids = kwargs["messages"][-1]["content"]
                    if '"seg_1", "seg_2"' in ids:
                        data = {"segments": [{"segment_id": "seg_1", "type": "narration"}]}
                    else:
                        data = {"segments": [{"segment_id": "seg_2", "type": "dialogue"}]}
                    return _Response(json.dumps(data))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    metrics = _analysis_metrics()
    result = analyzer._call_structured_annotations(
        "甲\n乙",
        [
            {"segment_id": "seg_1", "type": "narration", "text": "甲"},
            {"segment_id": "seg_2", "type": "dialogue", "text": "乙"},
        ],
        metrics=metrics,
    )
    assert [item["segment_id"] for item in result] == ["seg_1", "seg_2"]
    assert len(calls) == 2
    assert '"seg_1", "seg_2"' in calls[0]["messages"][-1]["content"]
    assert '"seg_2"' in calls[1]["messages"][-1]["content"]
    assert '"seg_1", "seg_2"' not in calls[1]["messages"][-1]["content"]


def test_structured_coverage_completion_7_to_5_only_requests_missing_two(monkeypatch):
    ids = [f"seg_{index}" for index in range(1, 8)]
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    prompt = kwargs["messages"][-1]["content"]
                    requested = [segment_id for segment_id in ids if segment_id in prompt]
                    returned = requested[:5] if len(calls) == 1 else requested
                    return _Response(json.dumps({"segments": [
                        {"segment_id": segment_id, "type": "dialogue"}
                        for segment_id in returned
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    spans = [{"segment_id": segment_id, "type": "dialogue", "text": segment_id}
             for segment_id in ids]
    metrics = _analysis_metrics()
    result = analyzer._call_structured_annotations(
        "\n".join(ids), spans, metrics=metrics,
    )

    assert {item["segment_id"] for item in result} == set(ids)
    assert len(calls) == 2
    assert metrics["request_count"] == 2
    assert '"seg_1"' in calls[0]["messages"][-1]["content"]
    assert '"seg_5"' in calls[0]["messages"][-1]["content"]
    assert '"seg_6"' in calls[1]["messages"][-1]["content"]
    assert '"seg_7"' in calls[1]["messages"][-1]["content"]
    assert '"segment_id": "seg_1"' not in calls[1]["messages"][-1]["content"]


def test_unknown_segment_reference_is_repaired_without_resending_valid_annotations(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == 1:
                        data = {"segments": [
                            {"segment_id": "seg_1", "type": "narration"},
                            {"segment_id": "invented", "type": "dialogue"},
                        ]}
                    else:
                        data = {"segments": [{"segment_id": "seg_2", "type": "dialogue"}]}
                    return _Response(json.dumps(data))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    metrics = _analysis_metrics()
    result = analyzer._call_structured_annotations(
        "甲\n乙",
        [
            {"segment_id": "seg_1", "type": "narration", "text": "甲"},
            {"segment_id": "seg_2", "type": "dialogue", "text": "乙"},
        ],
        metrics=metrics,
    )

    assert {item["segment_id"] for item in result} == {"seg_1", "seg_2"}
    assert len(calls) == 2
    assert metrics["request_count"] == 2
    assert '"segment_id": "seg_1"' not in calls[1]["messages"][-1]["content"]
    assert '"invented"' in calls[1]["messages"][-1]["content"]


def test_unknown_segment_reference_repair_exhaustion_is_deterministic(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _Response(json.dumps({"segments": [
                        {"segment_id": "invented", "type": "dialogue"},
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    with pytest.raises(StructuredOutputError) as error:
        analyzer._call_structured_annotations(
            "甲", [{"segment_id": "seg_1", "type": "narration", "text": "甲"}],
        )

    assert error.value.code == "annotation_reference_invalid"
    assert error.value.diagnostics["segmentationVersion"] == "paragraph-quote-sentence-v2"
    assert analysis_svc.is_deterministic_failure(error.value)
    assert len(calls) == 2


def test_structured_coverage_completion_remaining_gap_is_non_job_retryable(monkeypatch):
    ids = [f"seg_{index}" for index in range(1, 8)]
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    prompt = kwargs["messages"][-1]["content"]
                    requested = [segment_id for segment_id in ids if segment_id in prompt]
                    returned = requested[:5] if len(calls) == 1 else requested[:1]
                    return _Response(json.dumps({"segments": [
                        {"segment_id": segment_id, "type": "narration"}
                        for segment_id in returned
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    spans = [{"segment_id": segment_id, "type": "narration", "text": segment_id}
             for segment_id in ids]
    with pytest.raises(StructuredOutputError) as error:
        analyzer._call_structured_annotations("\n".join(ids), spans)

    assert error.value.code == "annotation_coverage_incomplete"
    assert len(calls) == 2
    assert analysis_svc.is_deterministic_failure(error.value)


def test_timeout_budget_exhaustion_stops_json_plain_and_job_retry_amplification(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    raise TimeoutError("provider timeout")

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test",
    })
    monkeypatch.setattr(analyzer, "_model_chain", lambda: ["gpt-test"])
    monkeypatch.setattr(analyzer, "JSON_MODE_OK", True)
    monkeypatch.setattr(analyzer.time, "sleep", lambda _: None)

    with pytest.raises(analyzer.analysis_svc.AnalysisProviderTimeoutExhaustedError) as error:
        analyzer._call_llm("短文本")

    assert len(calls) == 3
    assert error.value.code == "provider_timeout_exhausted"
    assert analysis_svc.is_deterministic_failure(error.value)
    failure = json.loads(analysis_svc.build_failure_error(error.value, attempts=3))
    assert failure["code"] == "provider_timeout_exhausted"
    assert failure["retryable"] is False


def test_failure_progress_reconcile_uses_partial_and_job_execution_history(monkeypatch):
    record = {
        "progress_json": json.dumps({
            "totalChunks": 2, "providerRequestCount": 1,
            "requestedChunks": 1, "chunkRetryCount": 0,
        })
    }
    partials = [{
        "state": "failed", "request_count": 6, "retry_count": 2,
    }]
    jobs = [{
        "analysis_id": 62, "job_type": "speaker_analysis", "attempts": 3,
        "started_at": "2026-08-22T10:00:00",
        "finished_at": "2026-08-22T10:05:00",
    }]

    monkeypatch.setattr(analysis_svc.db, "get_chapter_analysis", lambda _id: record)
    monkeypatch.setattr(analysis_svc.db, "list_analysis_partials", lambda **_kwargs: partials)
    monkeypatch.setattr(analysis_svc.db, "list_generation_jobs", lambda _limit: jobs)

    def update(_id, fields):
        record.update(fields)

    monkeypatch.setattr(analysis_svc.db, "update_chapter_analysis", update)
    progress = analysis_svc.reconcile_progress_from_partials(62)

    assert progress["providerRequestCount"] == 6
    assert progress["requestedChunks"] == 1
    assert progress["chunkRetryCount"] == 2
    assert progress["jobRetryCount"] == 2
    assert progress["totalDuration"] == 300.0
    assert progress["completedChunks"] == 0


def test_cache_inputs_include_segmentation_span_schema_and_mode_without_analysis_id():
    schema = AnnotationSchemaDescriptor()
    selected = select_output_mode(
        capability_from_provider({
            "supportsStrictJsonSchema": True,
            "supportedSchemaVersions": ["1"],
            "capabilityVersion": "cap-1",
        }),
        schema,
    )
    inputs = cache_identity_inputs(
        source_hash="source", segmentation_version="seg-v1", span_hashes=["span-a"],
        schema=schema, output_mode=selected, prompt_hash="prompt", prompt_version="p1",
        profile="chapter", provider="openai", model="model", provider_config_version="cfg1",
        fallback_model_chain=["model"],
    )
    assert inputs["segmentationVersion"] == "seg-v1"
    assert inputs["spanHashes"] == ["span-a"]
    assert inputs["schemaHash"] == schema.schema_hash
    assert "analysis_id" not in inputs
    assert "analysisId" not in inputs


def test_runner_uses_selected_mode_and_validates_annotation_references():
    schema = AnnotationSchemaDescriptor()
    calls = []

    def transport(mode, request):
        calls.append((mode, request))
        return {"segments": [{"segment_id": "seg_1", "type": "narration"}]}

    result = run_annotation_request(
        segment_ids=["seg_1"], schema=schema,
        capability=capability_from_provider({
            "supportsStrictJsonSchema": True, "supportedSchemaVersions": ["1"]
        }), transport=transport,
    )
    assert result.selection.applied_mode == "strict_json_schema"
    assert result.request_count == 1
    assert calls[0][1]["segment_ids"] == ["seg_1"]


def test_runner_allows_one_bounded_schema_repair_without_chapter_retry():
    schema = AnnotationSchemaDescriptor()
    calls = []

    def transport(mode, request):
        calls.append(mode)
        return {"segments": [{"segment_id": "seg_1", "type": "not-valid"}]}

    def repair(_payload):
        return {"segments": [{"segment_id": "seg_1", "type": "dialogue"}]}

    budget = StructuredRequestBudget(max_requests=2)
    result = run_annotation_request(
        segment_ids=["seg_1"], schema=schema,
        capability=capability_from_provider({"supportsJsonObject": True}),
        transport=transport, repair=repair, budget=budget,
    )
    assert result.annotations[0]["type"] == "dialogue"
    assert result.request_count == 2
    assert result.repair_count == 1
    assert calls == ["json_object"]


def test_runner_does_not_fallback_in_strict_mode():
    schema = AnnotationSchemaDescriptor()

    with pytest.raises(StructuredOutputError) as error:
        run_annotation_request(
            segment_ids=["seg_1"], schema=schema,
            capability=capability_from_provider({"supportsJsonObject": True}),
            transport=lambda *_: None, mode="strict",
        )
    assert error.value.code == "structured_output_unsupported"


def test_best_effort_runtime_strict_decline_falls_back_once_with_reason():
    schema = AnnotationSchemaDescriptor()
    calls = []

    def transport(mode, request):
        calls.append(mode)
        if mode == "strict_json_schema":
            raise StructuredOutputError("structured_output_unsupported", "declined")
        return {"segments": [{"segment_id": "seg_1", "type": "narration"}]}

    result = run_annotation_request(
        segment_ids=["seg_1"], schema=schema,
        capability=capability_from_provider({
            "supportsStrictJsonSchema": True, "supportedSchemaVersions": ["1"],
            "supportsJsonObject": True,
        }), transport=transport,
    )
    assert calls == ["strict_json_schema", "json_object"]
    assert result.selection.fallback_reason == "runtime_structured_output_unsupported"
    assert result.fallback_count == 1


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _ProviderError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _analysis_metrics():
    return {"lock": threading.Lock(), "request_count": 0, "retry_count": 0}


def test_analyzer_structured_bridge_sends_annotation_schema_without_canonical_text(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _Response(json.dumps({"segments": [
                        {"segment_id": "seg_1", "type": "dialogue", "speaker": "char_1"}
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test",
        "supports_strict_json_schema": True,
        "supported_schema_versions": ["1"],
        "capability_version": "cap-1",
    })
    metrics = _analysis_metrics()
    result = analyzer._call_structured_annotations(
        "「你好」", [{"segment_id": "seg_1", "type": "dialogue"}], metrics=metrics,
    )
    assert result[0]["segment_id"] == "seg_1"
    request_schema = calls[0]["response_format"]["json_schema"]["schema"]
    assert "text" not in request_schema["properties"]
    assert "source_start" not in request_schema["properties"]
    assert metrics["structured_mode_applied"] == "strict_json_schema"


def test_vocab_annotation_schema_keeps_learning_metadata_out_of_canonical_ownership():
    schema = AnnotationSchemaDescriptor(
        required_fields=("segment_id", "type", "vocab"),
    )
    strict = build_openai_strict_schema(schema, segment_ids=["seg_1"])
    item = strict["properties"]["segments"]["items"]

    assert "text" not in item["properties"]
    assert "source_start" not in item["properties"]
    assert item["properties"]["vocab"]["type"] == ["object", "null"]
    assert validate_annotation_payload({
        "segments": [{
            "segment_id": "seg_1", "type": "narration",
            "vocab": {"zh": "書", "en": "book", "spelling": "B-O-O-K",
                      "example": "This is a book.", "level": "A1"},
        }],
    }, {"seg_1"}, schema)[0]["vocab"]["en"] == "book"


def test_vocab_prompt_bounds_learning_items_per_group(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _Response(json.dumps({"segments": [{
                        "segment_id": "seg_1", "type": "narration",
                        "speaker": None, "speaker_candidate": None,
                        "speaker_gender": None, "speaker_age": None,
                        "attribution": None, "confidence": None,
                        "vocab": {"zh": "書", "en": "book", "spelling": "B-O-O-K",
                                  "example": "This is a book.", "level": "A1"},
                    }]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test",
        "supports_strict_json_schema": True,
        "supported_schema_versions": ["1"],
        "capability_version": "cap-1",
    })
    analyzer._call_structured_annotations(
        "書。", [{"segment_id": "seg_1", "type": "narration", "text": "書。"}],
        include_vocab=True,
    )
    prompt = calls[0]["messages"][1]["content"]
    assert "合計最多提供 2 個 vocab" in prompt
    assert "不是每個 annotation 都要提供單字" in prompt


def test_v4_vocab_book_requests_learning_annotation_and_persists_projection(monkeypatch):
    calls = []

    def fake_annotations(source_text, spans, **kwargs):
        calls.append(kwargs)
        return [{
            "segment_id": span["segment_id"], "type": span["type"],
            "speaker": None, "speaker_candidate": None,
            "speaker_gender": None, "speaker_age": None,
            "attribution": None, "confidence": None,
            "vocab": ({"zh": "書", "en": "book", "spelling": "B-O-O-K",
                       "example": "This is a book.", "level": "A1"}
                      if index == 0 else None),
        } for index, span in enumerate(spans)]

    monkeypatch.setattr(analyzer, "_call_structured_annotations", fake_annotations)
    monkeypatch.setattr(analyzer, "_structured_capability", lambda: StructuredOutputCapability(
        supports_strict_json_schema=True, supported_schema_versions=("1",),
        supports_json_object=True, capability_version="cap-1", source="probed",
        state="supported",
    ))
    monkeypatch.setattr(analyzer, "_request_profile", lambda _model: SimpleNamespace(
        version="profile-1", hash="profile-hash",
    ))

    artifact = analyzer.analyze_source_faithful(
        "書。天空。", book={"category": "vocab", "vocabLevel": "A1"},
    )

    assert calls and all(call["include_vocab"] is True for call in calls)
    assert artifact["learning"]["vocab"][0]["en"] == "book"
    assert all(
        item["text"] == "書。天空。"[item["source_start"]:item["source_end"]]
        for item in artifact["segments"]
    )


def test_analyzer_structured_bridge_runtime_400_falls_back_to_json_object(monkeypatch):
    modes = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    modes.append((kwargs.get("response_format") or {}).get("type"))
                    if len(modes) == 1:
                        raise _ProviderError(400)
                    return _Response(json.dumps({"segments": [
                        {"segment_id": "seg_1", "type": "narration"}
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test",
        "supports_strict_json_schema": True,
        "supported_schema_versions": ["1"],
        "supports_json_object": True,
    })
    metrics = _analysis_metrics()
    result = analyzer._call_structured_annotations(
        "旁白", [{"segment_id": "seg_1", "type": "narration"}], metrics=metrics,
    )
    assert result[0]["type"] == "narration"
    assert modes == ["json_schema", "json_object"]
    assert metrics["structured_fallback_reason"] == "runtime_structured_output_unsupported"


def test_source_faithful_integration_reuses_succeeded_structured_partial(monkeypatch):
    segmentation = analyzer.source_faithful.segment_source("旁白。\n「你好」")
    ids = [item["segment_id"] for item in segmentation["segments"]]
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _Response(json.dumps({"segments": [
                        {"segment_id": ids[0], "type": "narration"},
                        {"segment_id": ids[1], "type": "dialogue"},
                    ]}))

    class PartialCache:
        def __init__(self):
            self.output_mode = "legacy_json"
            self.segmentation_version = "legacy-text-v1"
            self.annotation_schema_id = "legacy"
            self.annotation_schema_version = "legacy"
            self.annotation_schema_hash = "legacy"
            self.capability_version = "unknown"
            self.rows = {}
            self.claims = {}

        def identity_for(self, index, chunk, *, context, prompt_hash):
            key = (index, chunk, self.output_mode, self.annotation_schema_version,
                   self.annotation_schema_hash, self.capability_version,
                   self.segmentation_version)
            return {"cacheKey": repr(key), "outputMode": self.output_mode}

        def reuse(self, identity):
            row = self.rows.get(identity["cacheKey"])
            return row if row and row["state"] == "succeeded" else None

        def claim(self, identity):
            token = "owner"
            self.claims[identity["cacheKey"]] = token
            return {"state": "running", "_owner_token": token}

        def record_metrics(self, *args, **kwargs):
            pass

        def publish(self, identity, token, payload):
            self.rows[identity["cacheKey"]] = {"state": "succeeded", **payload}

        def fail(self, identity, token, reason):
            self.rows[identity["cacheKey"]] = {"state": "failed", "reason": reason}

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test",
        "supports_strict_json_schema": True, "supported_schema_versions": ["1"],
        "capability_version": "cap-1",
    })
    cache = PartialCache()
    first = analyzer.analyze_source_faithful("旁白。\n「你好」", partial_cache=cache)
    second = analyzer.analyze_source_faithful("旁白。\n「你好」", partial_cache=cache)
    assert first["schemaVersion"] == 4
    assert [s["text"] for s in second["segments"]] == ["旁白。", "「你好」"]
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [429, 529, "timeout"])
def test_analyzer_structured_bridge_preserves_bounded_transient_retry(monkeypatch, failure):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == 1:
                        if failure == "timeout":
                            raise TimeoutError("timeout")
                        raise _ProviderError(failure)
                    return _Response(json.dumps({"segments": [
                        {"segment_id": "seg_1", "type": "narration"}
                    ]}))

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test",
        "supports_json_object": True,
    })
    monkeypatch.setattr(analyzer.time, "sleep", lambda _: None)
    metrics = _analysis_metrics()
    analyzer._request_budget_local.current = {
        "requests": 0, "max_requests": 3, "malformed_retries": 0,
        "transient_retries": 0, "retries": 0,
    }
    try:
        result = analyzer._call_structured_annotations(
            "旁白", [{"segment_id": "seg_1", "type": "narration"}], metrics=metrics,
        )
    finally:
        analyzer._request_budget_local.current = None
    assert result[0]["segment_id"] == "seg_1"
    assert len(calls) == 2


def test_structured_progress_metadata_is_forwarded_without_overwriting_legacy_fields(monkeypatch):
    captured = {}

    def update(analysis_id, **kwargs):
        captured.update(kwargs.get("metrics") or {})

    monkeypatch.setattr(analysis_executor.analysis_svc, "update_analysis_progress", update)
    callback = analysis_executor._progress_callback(7)
    callback({
        "stage": "analyzing", "total_chunks": 1, "completed_chunks": 0,
        "running_chunks": 1, "retry_count": 0,
        "structured_mode_requested": "best_effort",
        "structured_mode_applied": "strict_json_schema",
        "structured_schema_version": "1",
        "structured_schema_hash": "hash",
        "structured_fallback_reason": None,
        "structured_capability_source": "probed",
        "structured_repair_count": 0,
    })
    assert captured["structuredModeRequested"] == "best_effort"
    assert captured["structuredModeApplied"] == "strict_json_schema"
    assert captured["structuredSchemaHash"] == "hash"
    assert captured["structuredCapabilitySource"] == "probed"


def test_analyzer_structured_invalid_segment_is_rejected_without_canonical_text():
    schema = AnnotationSchemaDescriptor()
    with pytest.raises(StructuredOutputError) as error:
        validate_annotation_payload({"segments": [{
            "segment_id": "missing", "type": "narration"
        }]}, {"seg_1"}, schema)
    assert error.value.code == "invalid_segment_id"


def test_analyzer_structured_malformed_repair_exhausts_without_full_chapter_retry(monkeypatch):
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _Response("{malformed")

    monkeypatch.setattr(analyzer, "_client", lambda: Client())
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: {
        "provider_type": "openai", "model": "gpt-test", "supports_json_object": True,
    })
    metrics = _analysis_metrics()
    with pytest.raises(analyzer.MalformedJSONError):
        analyzer._call_structured_annotations(
            "旁白", [{"segment_id": "seg_1", "type": "narration"}], metrics=metrics,
        )
    assert len(calls) == 2


def test_benchmark_fixture_is_evidence_not_a_runtime_threshold():
    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath(
        "structured_output_benchmark.json").read_text(encoding="utf-8"))
    runs = {(item["mode"], item["chunkSize"]): item for item in fixture["runs"]}
    assert runs[("json_object", 6000)]["malformed"] == 2
    assert runs[("json_object", 4000)]["success"] == 2
    assert runs[("json_object", 3000)]["success"] == 3
    assert runs[("json_schema", 3000)]["malformed"] == 0
    assert "not runtime requirements" in fixture["interpretation"]
