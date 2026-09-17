import json
from datetime import datetime, timedelta

from backend import analyzer
from backend.services import ai_request_profile, structured_capability, structured_output


def _provider(snapshot, *, model="gpt-4o-mini", config=1):
    return {
        "id": 32, "provider_type": "openai", "model": model, "config_version": config,
        "structured_capability_json": json.dumps(snapshot),
        "structured_capability_expires_at": (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds"),
    }


def _snapshot(state, *, model="gpt-4o-mini", config=1):
    profile = ai_request_profile.resolve({"provider_type": "openai", "model": model})
    return {
        "providerId": 32, "provider": "openai", "model": model, "configVersion": config,
        "schemaVersion": "1", "probeVersion": structured_capability.PROBE_VERSION,
        "supportsStrictJsonSchema": state == "supported",
        "supportedSchemaVersions": ["1"] if state == "supported" else [],
        "supportsJsonObject": True, "capabilityVersion": "probe-v1", "source": "probed", "state": state,
        "requestProfileVersion": profile.version, "requestProfileHash": profile.hash,
    }


def test_supported_snapshot_drives_analyzer_to_strict(monkeypatch):
    provider = _provider(_snapshot("supported"))
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: provider)
    selected = structured_output.select_output_mode(
        analyzer._structured_capability(), structured_output.AnnotationSchemaDescriptor(),
    )
    assert selected.applied_mode == "strict_json_schema"
    assert selected.capability_state == "supported"


def test_expired_supported_snapshot_refreshes_before_analysis(monkeypatch):
    provider = _provider(_snapshot("supported"))
    provider["structured_capability_expires_at"] = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")

    def fake_refresh(provider_id):
        provider["structured_capability_expires_at"] = (datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
        return json.loads(provider["structured_capability_json"])

    monkeypatch.setattr(analyzer, "_bound_provider", lambda: provider)
    monkeypatch.setattr(structured_capability, "refresh", fake_refresh)
    selected = structured_output.select_output_mode(
        analyzer._structured_capability(), structured_output.AnnotationSchemaDescriptor(),
    )
    assert selected.applied_mode == "strict_json_schema"


def test_timeout_and_invalid_schema_snapshot_fallback_without_becoming_unsupported(monkeypatch):
    for state in ("probe_timeout", "invalid_schema", "provider_error"):
        provider = _provider(_snapshot(state))
        monkeypatch.setattr(analyzer, "_bound_provider", lambda provider=provider: provider)
        cap = analyzer._structured_capability()
        selected = structured_output.select_output_mode(cap, structured_output.AnnotationSchemaDescriptor())
        assert cap.state == state
        assert selected.applied_mode == "json_object"
        assert cap.state != "unsupported"


def test_schema_or_provider_change_invalidates_snapshot(monkeypatch):
    provider = _provider(_snapshot("supported"), model="new-model", config=2)
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: provider)
    cap = analyzer._structured_capability()
    assert cap.state == "unknown"
    assert structured_output.select_output_mode(cap, structured_output.AnnotationSchemaDescriptor()).applied_mode == "json_object"


def test_request_profile_change_invalidates_capability_snapshot(monkeypatch):
    provider = _provider(_snapshot("supported", model="gpt-5-nano"), model="gpt-5-nano")
    snapshot = json.loads(provider["structured_capability_json"])
    snapshot["requestProfileHash"] = "old-profile"
    provider["structured_capability_json"] = json.dumps(snapshot)
    monkeypatch.setattr(analyzer, "_bound_provider", lambda: provider)

    assert analyzer._structured_capability().state == "unknown"


def test_gpt5_and_gpt4_request_profiles_keep_transport_rules_separate():
    gpt5 = ai_request_profile.resolve({"provider_type": "openai", "model": "gpt-5-nano"})
    gpt4 = ai_request_profile.resolve({"provider_type": "openai", "model": "gpt-4o-mini"})
    payload5 = gpt5.request_kwargs(model="gpt-5-nano", messages=[], completion_tokens=256)
    payload4 = gpt4.request_kwargs(model="gpt-4o-mini", messages=[], completion_tokens=256)

    assert payload5["max_completion_tokens"] == 2048
    assert "max_tokens" not in payload5
    assert "temperature" not in payload5
    assert payload4["max_tokens"] == 256
    assert payload4["temperature"] == 0.2
    assert gpt5.structured_output_capability == "probe_required"
    assert gpt4.structured_output_capability == "probe_required"
