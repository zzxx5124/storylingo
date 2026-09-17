import hashlib
import json
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend import db, settings
from backend.services import analysis as analysis_svc
from backend.services.analysis_partials import (
    AnalysisPartialSession,
    PartialCacheError,
    build_cache_identity,
    cleanup_expired_partials,
)
from backend import analyzer
from backend import jobs
from backend.tests.conftest import new_admin, reg
from backend.tests.test_v4_analysis_executor import _make_provider


def _partial_fixture_session(client, text="第一段。\n第二段。\n" * 20):
    return _session(client, text=text)

def _session(client, text="第一段。\n第二段。\n" * 20):
    response = reg(client, "partialauthor", password="secret123")
    user = db.get_user_by_name("partialauthor")
    db.update_user_role(user["id"], "author")
    # Role mutation revokes the registration session; model the next request
    # with a fresh login before exercising the partial-cache fixture.
    login = client.post("/api/auth/login", json={"username": "partialauthor", "password": "secret123"})
    assert login.status_code == 200, login.text
    book_response = client.post("/api/books", files={"file": ("book.txt", text.encode("utf-8"), "text/plain")},
                                data={"category": "小说", "vocabLevel": "AUTO"})
    assert book_response.status_code == 200, book_response.text
    book = book_response.json()
    row = db.get_book_row(book["id"])
    chapter = db.get_chapter(row["id"], 0)
    analysis_id = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=chapter["id"], source_text_hash=chapter["text_hash"],
        prompt_version="prompt-v1", analysis_profile="speaker", schema_version=3)
    return row, chapter, AnalysisPartialSession(
        analysis_id=analysis_id, book_id=row["id"], chapter_id=chapter["id"],
        source_text_hash=chapter["text_hash"], prompt_version="prompt-v1", schema_version=3,
        analysis_profile="speaker", provider={"id": "p1", "model": "m1", "config_version": 1},
        language="zh", category="小說",
        source_hash_getter=lambda: (db.get_chapter(row["id"], 0) or {}).get("text_hash"),
    )


def test_cache_identity_excludes_analysis_id_and_includes_relevant_inputs(client):
    kwargs = dict(source_text_hash="source", chunk_index=0, chunk_input="chunk",
                  context="context", prompt_hash="prompt", prompt_version="p1",
                  schema_version=3, analysis_profile="speaker", provider_identity="p",
                  model_identity="m", provider_config_version=1, fallback_models=["m2"],
                  language="zh", category="小說")
    first = build_cache_identity(**kwargs)
    second = build_cache_identity(**kwargs)
    assert first["cacheKey"] == second["cacheKey"]
    changed = build_cache_identity(**{**kwargs, "prompt_version": "p2"})
    assert changed["cacheKey"] != first["cacheKey"]


def test_partial_publish_is_atomic_and_reusable_across_analysis_ids(client):
    _, _, session = _session(client)
    identity = session.identity_for(0, "第一段。", context="", prompt_hash="h")
    claim = session.claim(identity)
    result = {"segments": [{"type": "narration", "text": "第一段。"}]}
    session.publish(identity, claim["_owner_token"], result)
    row = db.get_analysis_partial(identity["cacheKey"])
    assert row["state"] == "succeeded"
    assert session.reuse(identity) == result
    assert row["analysis_id"] is not None
    assert not db.get_chapter_analysis(row["analysis_id"])["artifact_path"]


def test_matching_partial_is_reusable_by_a_new_analysis_owner(client):
    row, chapter, first = _session(client)
    identity = first.identity_for(0, "第一段。", context="", prompt_hash="h")
    claim = first.claim(identity)
    result = {"segments": [{"type": "narration", "text": "第一段。"}]}
    first.publish(identity, claim["_owner_token"], result)
    second = AnalysisPartialSession(
        analysis_id=999, book_id=row["id"], chapter_id=chapter["id"],
        source_text_hash=chapter["text_hash"], prompt_version="prompt-v1", schema_version=3,
        analysis_profile="speaker", provider={"id": "p1", "model": "m1", "config_version": 1},
        language="zh", category="小說",
        source_hash_getter=lambda: (db.get_chapter(row["id"], 0) or {}).get("text_hash"),
    )
    assert second.reuse(identity) == result
    assert second.reused_chunks == 1


def test_request_budget_is_shared_and_hard_capped(client):
    analyzer._request_budget_local.current = {"requests": 6, "max_requests": 6}
    try:
        with pytest.raises(analyzer.AnalysisRequestBudgetExceeded):
            analyzer._invoke(lambda: SimpleNamespace(), {})
    finally:
        analyzer._request_budget_local.current = None


def test_malformed_repair_is_bounded_to_one_chunk_request(client):
    calls = []

    def create():
        calls.append(1)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{not json"))])

    def repair(raw):
        calls.append(1)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"segments": []}'))])

    metrics = {"lock": __import__("threading").Lock(), "request_count": 0,
               "retry_count": 0, "chunk_latencies": []}
    analyzer._request_budget_local.current = {
        "requests": 0, "max_requests": 6, "malformed_retries": 0,
        "transient_retries": 0, "retries": 0,
    }
    try:
        assert analyzer._extract_with_repair(create, metrics, repair_create=repair) == {"segments": []}
        assert len(calls) == 2
        assert analyzer._request_budget_local.current["malformed_retries"] == 1
    finally:
        analyzer._request_budget_local.current = None


def test_missing_partial_file_is_safe_cache_miss(client):
    _, _, session = _session(client)
    identity = session.identity_for(0, "第一段。", context="", prompt_hash="h")
    claim = session.claim(identity)
    session.publish(identity, claim["_owner_token"], {"segments": []})
    path = os.path.join(settings.ROOT_DIR, db.get_analysis_partial(identity["cacheKey"])["storage_path"])
    os.remove(path)
    assert session.reuse(identity) is None
    assert db.get_analysis_partial(identity["cacheKey"])["state"] == "stale"


def test_stale_worker_cannot_publish_after_lease_recovery(client):
    _, _, session = _session(client)
    identity = session.identity_for(0, "第一段。", context="", prompt_hash="h")
    first = session.claim(identity)
    old_token = first["_owner_token"]
    expired = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")
    db.execute("UPDATE analysis_partials SET lease_expires_at=? WHERE cache_key=?",
               (expired, identity["cacheKey"]))
    second = session.claim(identity)
    assert second["_owner_token"] != old_token
    with __import__("pytest").raises(PartialCacheError):
        session.publish(identity, old_token, {"segments": []})


def test_source_change_blocks_partial_publish(client):
    row, _, session = _session(client)
    identity = session.identity_for(0, "第一段。", context="", prompt_hash="h")
    claim = session.claim(identity)
    db.execute("UPDATE chapters SET text_hash=? WHERE book_id=? AND seq=0", ("changed", row["id"]))
    with __import__("pytest").raises(PartialCacheError):
        session.publish(identity, claim["_owner_token"], {"segments": []})
    assert db.get_analysis_partial(identity["cacheKey"])["state"] == "failed"


def test_cleanup_is_bounded_and_does_not_delete_active_lease(client):
    _, _, session = _session(client)
    identity = session.identity_for(0, "第一段。", context="", prompt_hash="h")
    claim = session.claim(identity)
    db.execute("UPDATE analysis_partials SET updated_at=?, created_at=? WHERE cache_key=?",
               ("2000-01-01T00:00:00", "2000-01-01T00:00:00", identity["cacheKey"]))
    assert cleanup_expired_partials(limit=100) == []
    assert db.get_analysis_partial(identity["cacheKey"]) is not None


def test_two_chunks_only_failed_chunk_is_requested_on_retry(client, monkeypatch):
    row, chapter, session = _partial_fixture_session(client)
    monkeypatch.setattr(analyzer, "_split_text", lambda text: ["A", "B"])
    calls = []
    first_attempt = {"B": True}

    def fake_call(part, context="", system_prompt=None, metrics=None):
        calls.append(part)
        if part == "B" and first_attempt["B"]:
            first_attempt["B"] = False
            responses = iter(["{bad", "{still bad"])
            return analyzer._extract_with_repair(
                lambda: SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content=next(responses)))]), metrics)
        analyzer._request_budget_local.current["requests"] += 1
        return {"segments": [{"type": "narration", "text": part}]}

    monkeypatch.setattr(analyzer, "_call_llm", fake_call)
    book = {"id": row["id"], "voices": {}}
    with pytest.raises(Exception):
        analyzer.analyze_chapter(book, 0, chapter["text"], partial_cache=session)
    identity_a = session.identity_for(0, "A", context="", prompt_hash=analyzer.hashlib.sha256(
        analyzer._make_prompt(book).encode("utf-8")).hexdigest())
    identity_b = session.identity_for(1, "B", context="", prompt_hash=analyzer.hashlib.sha256(
        analyzer._make_prompt(book).encode("utf-8")).hexdigest())
    assert db.get_analysis_partial(identity_a["cacheKey"])["state"] == "succeeded"
    assert db.get_analysis_partial(identity_b["cacheKey"])["state"] == "failed"
    first_counts = {part: calls.count(part) for part in ("A", "B")}

    analyzer.analyze_chapter(book, 0, chapter["text"], partial_cache=session)
    assert calls.count("A") == first_counts["A"]
    assert calls.count("B") == first_counts["B"] + 1
    assert db.get_analysis_partial(identity_a["cacheKey"])["request_count"] == 1


def test_four_of_five_partials_resume_progress_and_metrics(client, monkeypatch):
    row, chapter, session = _partial_fixture_session(client)
    parts = ["A", "B", "C", "D", "E"]
    monkeypatch.setattr(analyzer, "_split_text", lambda text: parts)
    book = {"id": row["id"], "voices": {}}
    prompt_hash = analyzer.hashlib.sha256(analyzer._make_prompt(book).encode("utf-8")).hexdigest()
    for index, part in enumerate(parts[:4]):
        identity = session.identity_for(index, part, context="", prompt_hash=prompt_hash)
        claim = session.claim(identity)
        session.publish(identity, claim["_owner_token"], {"segments": [{"type": "narration", "text": part}]})
    events = []
    calls = []

    def progress(event):
        events.append(analysis_svc.update_analysis_progress(
            session.analysis_id, stage=event.get("stage"), total_chunks=event.get("total_chunks"),
            completed_chunks=event.get("completed_chunks"), running_chunks=event.get("running_chunks"),
            retry_count=event.get("retry_count")))

    def fake_call(part, context="", system_prompt=None, metrics=None):
        calls.append(part)
        return {"segments": [{"type": "narration", "text": part}]}

    monkeypatch.setattr(analyzer, "_call_llm", fake_call)
    result = analyzer.analyze_chapter(book, 0, chapter["text"], on_progress=progress,
                                      partial_cache=session)
    assert calls == ["E"]
    assert result["metrics"]["cacheHitChunks"] == 4
    assert result["metrics"]["reusedChunks"] == 4
    assert result["metrics"]["requestedChunks"] == 1
    assert result["metrics"]["providerRequestCount"] == 0
    assert any(e["completedChunks"] == 4 and e["progressPercent"] == 80 for e in events)
    assert events[0]["completedChunks"] > 0


@pytest.mark.parametrize("failure", ["429", "529", "timeout"])
def test_transient_provider_failures_are_bounded_by_shared_budget(monkeypatch, failure):
    calls = []

    class ProviderBusy(Exception):
        def __init__(self, status):
            self.status_code = status

    responses = [ProviderBusy(int(failure)) if failure != "timeout" else TimeoutError("timeout") for _ in range(2)]
    responses.append(SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"segments": []}'))]))

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(analyzer, "_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    monkeypatch.setattr(analyzer, "_model_chain", lambda: ["m1"])
    monkeypatch.setattr(analyzer, "JSON_MODE_OK", False)
    monkeypatch.setattr(analyzer.time, "sleep", lambda _: None)
    analyzer._request_budget_local.current = {
        "requests": 0, "max_requests": 6, "malformed_retries": 0,
        "transient_retries": 0, "retries": 0,
    }
    try:
        assert analyzer._call_llm("text", metrics={"lock": __import__("threading").Lock(),
                                                     "request_count": 0, "retry_count": 0}) == {"segments": []}
        budget = analyzer._request_budget_local.current
        assert len(calls) == 3
        assert budget["requests"] == 3
        assert budget["transient_retries"] == 2
        assert analyzer._is_transient_provider_error(
            TimeoutError("timeout") if failure == "timeout" else ProviderBusy(int(failure)))
    finally:
        analyzer._request_budget_local.current = None


def test_init_db_is_idempotent_and_preserves_ready_analysis(client):
    row, chapter, _ = _partial_fixture_session(client)
    analysis_id = analysis_svc.create_analysis_row(
        book_id=row["id"], chapter_id=chapter["id"], source_text_hash=chapter["text_hash"],
        prompt_version="p", analysis_profile="speaker")
    db.update_chapter_analysis(analysis_id, {"status": "ready", "artifact_path": "ready.json"})
    before = db.get_chapter_analysis(analysis_id)
    db.init_db()
    db.init_db()
    columns = {item["name"] for item in db.query("PRAGMA table_info(analysis_partials)")}
    after = db.get_chapter_analysis(analysis_id)
    assert {"cache_key", "state", "lease_expires_at", "storage_path"}.issubset(columns)
    assert after["status"] == "ready"
    assert after["artifact_path"] == before["artifact_path"]


def test_real_job_malformed_repair_keeps_a_and_only_repairs_b(client, monkeypatch):
    admin = new_admin("partialjobadmin")
    provider_payload = _make_provider(admin, name="partial-job-ai")
    provider = db.get_ai_provider(provider_payload["id"])
    row, chapter, session = _partial_fixture_session(client)
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=session.analysis_id, provider=provider, requested_by=None,
        payload={"bid": row["bid"], "seq": 0})
    monkeypatch.setattr(analyzer, "_split_text", lambda text: ["A", "B"])
    monkeypatch.setattr(analysis_svc, "normalize_analysis", lambda *args, **kwargs: {
        "schema_version": 3, "segments": [], "characters": {}, "source_text_hash": chapter["text_hash"]})
    monkeypatch.setattr(analysis_svc, "save_ready_analysis", lambda *args, **kwargs: None)
    monkeypatch.setattr(analysis_svc, "retry_roster_projection", lambda *args, **kwargs: None)
    calls = []
    def fake_call(part, context="", system_prompt=None, metrics=None):
        calls.append(part)
        if part == "B":
            return analyzer._extract_with_repair(
                lambda: SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="{bad"))]),
                metrics,
                repair_create=lambda raw: SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"segments": []}'))]),
            )
        analyzer._request_budget_local.current["requests"] += 1
        analyzer._metric_inc(metrics, "request_count")
        return {"segments": []}

    monkeypatch.setattr(analyzer, "_call_llm", fake_call)
    assert jobs.run_pending_once() is True
    assert db.get_generation_job(job_id)["status"] == "success"
    assert calls.count("A") == 1
    assert calls.count("B") == 1  # repair stays inside the same chunk invocation
    assert session.reused_chunks == 0  # executor owns the retry session; DB evidence follows
    partials = db.list_analysis_partials(analysis_id=session.analysis_id)
    by_chunk = {row["chunk_index"]: row for row in partials}
    assert by_chunk[0]["state"] == "succeeded"
    assert by_chunk[0]["request_count"] == 1
    assert by_chunk[1]["state"] == "succeeded"
    assert by_chunk[1]["request_count"] == 2
    progress = analysis_svc.parse_analysis_progress(db.get_chapter_analysis(session.analysis_id))
    assert progress["reusedChunks"] == 0
    assert progress["requestedChunks"] == 2
    assert progress["providerRequestCount"] == 3
    assert progress["savedProviderRequests"] == 0


def test_malformed_repair_exhausted_fails_without_job_retry_or_raw_persistence(client, monkeypatch):
    admin = new_admin("partialfailadmin")
    provider_payload = _make_provider(admin, name="partial-fail-ai")
    provider = db.get_ai_provider(provider_payload["id"])
    row, chapter, session = _partial_fixture_session(client)
    job_id = db.create_analysis_job(
        "speaker_analysis", book_id=row["id"], chapter_id=chapter["id"],
        analysis_id=session.analysis_id, provider=provider, requested_by=None,
        payload={"bid": row["bid"], "seq": 0})
    monkeypatch.setattr(analyzer, "_split_text", lambda text: ["A", "B"])
    monkeypatch.setattr(analysis_svc, "normalize_analysis", lambda *args, **kwargs: {
        "schema_version": 3, "segments": [], "characters": {}, "source_text_hash": chapter["text_hash"]})

    def fake_call(part, context="", system_prompt=None, metrics=None):
        if part == "A":
            analyzer._request_budget_local.current["requests"] += 1
            analyzer._metric_inc(metrics, "request_count")
            return {"segments": []}
        return analyzer._extract_with_repair(
            lambda: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"raw_secret_marker": [}'))]),
            metrics,
            repair_create=lambda raw: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="{still malformed"))]),
        )

    monkeypatch.setattr(analyzer, "_call_llm", fake_call)
    assert jobs.run_pending_once() is True
    assert db.get_generation_job(job_id)["status"] == "failed"
    record = db.get_chapter_analysis(session.analysis_id)
    assert record["status"] == "failed"
    failure = analysis_svc.parse_failure_error(record["error"])
    assert failure["code"] == "malformed_json"
    assert "raw_secret_marker" not in record["error"]
    progress = analysis_svc.parse_analysis_progress(record)
    assert progress["providerRequestCount"] == 3
    assert progress["requestedChunks"] == 2
    assert progress["chunkRetryCount"] == 1
    assert progress["runningChunks"] == 0
    partials = {row["chunk_index"]: row for row in db.list_analysis_partials(analysis_id=session.analysis_id)}
    assert partials[0]["state"] == "succeeded"
    assert partials[1]["state"] == "failed"
    assert not partials[1]["storage_path"]
