"""V4 執行期契約測試：確認 final domain 名稱與列舉無歧義、無衝突。

此測試鎖定 V4 最終資料模型與 API 合約常數，以 `backend/v4_contracts.py` 為單一真源。
"""
from backend import v4_contracts as c


def test_final_domains_are_unambiguous():
    assert len(set(c.FINAL_DOMAINS)) == len(c.FINAL_DOMAINS)
    assert "chapter_analysis" in c.FINAL_DOMAINS
    assert "audio_generation" in c.FINAL_DOMAINS
    assert "ai_provider" in c.FINAL_DOMAINS
    assert "tts_provider" in c.FINAL_DOMAINS
    assert "audio_mode" in c.FINAL_DOMAINS
    # domain 名稱不得與 table 名稱混淆為同一 identity。
    assert set(c.FINAL_DOMAINS).isdisjoint({c.TABLE_CHAPTER_ANALYSES, c.TABLE_AUDIO_GENERATIONS, c.TABLE_AI_PROVIDERS, c.TABLE_TTS_PROVIDERS})


def test_audio_mode_enum():
    assert c.AUDIO_MODES == ("single", "multi")
    assert c.AUDIO_MODE_SINGLE == "single"
    assert c.AUDIO_MODE_MULTI == "multi"


def test_analysis_contracts():
    assert c.ANALYSIS_TYPES == ("speaker",)
    assert c.ANALYSIS_STATUSES == ("queued", "running", "ready", "failed")
    assert c.ANALYSIS_SCHEMA_VERSION == 4
    assert c.ANALYSIS_SCHEMA_VERSION_V3 == 3
    assert c.ANALYSIS_STATUS_READY in c.ANALYSIS_STATUSES


def test_canonical_emotion_registry():
    # 有限、無重複、以 neutral 為預設。
    assert c.CANONICAL_EMOTIONS == ("neutral", "happy", "sad", "angry", "tense")
    assert len(set(c.CANONICAL_EMOTIONS)) == len(c.CANONICAL_EMOTIONS)
    assert c.EMOTION_NEUTRAL in c.CANONICAL_EMOTIONS
    assert set(c.EMOTION_SOURCES) == {"ai", "fallback", "manual"}
    assert c.EMOTION_POLICIES == ("best_effort", "strict")


def test_audio_generation_statuses():
    assert c.GENERATION_STATUSES == ("queued", "running", "ready", "failed", "failed_capability")
    assert c.GENERATION_STATUS_FAILED_CAPABILITY in c.GENERATION_STATUSES


def test_ai_provider_types_are_allowlisted():
    assert c.AI_PROVIDER_TYPES == ("openai", "openai_compatible", "deepseek")
    assert len(set(c.AI_PROVIDER_TYPES)) == len(c.AI_PROVIDER_TYPES)


def test_canonical_job_types():
    assert c.JOB_TYPES == ("speaker_analysis", "speaker_analysis_all", "audio_single", "audio_multi", "audio_generate_all")
    assert len(set(c.JOB_TYPES)) == len(c.JOB_TYPES)


def test_workflow_read_model_states():
    expected = {"needs_content_review", "needs_analysis", "analysis_running", "analysis_failed", "needs_voice_configuration",
                "ready_to_generate", "audio_generating", "audio_ready", "audio_stale", "audio_failed",
                "provider_unavailable", "ready_to_submit"}
    assert set(c.WORKFLOW_STATES) == expected
    assert len(set(c.WORKFLOW_STATES)) == len(c.WORKFLOW_STATES)


def test_analysis_artifact_v2_shape():
    artifact = c.CANONICAL_ANALYSIS_ARTIFACT_V2
    assert artifact["schemaVersion"] == 2
    segment = artifact["segments"][0]
    assert "id" in segment
    assert "text" in segment
    assert "speaker" in segment
    emotion = segment["emotion"]
    assert emotion["value"] == "neutral"
    assert emotion["source"] in {"ai", "fallback", "manual"}


def test_api_response_contracts():
    assert set(c.ANALYSIS_CREATE_RESPONSE) == {"analysisId", "jobId", "status"}
    assert set(c.AUDIO_GENERATION_CREATE_RESPONSE) == {"generationId", "jobId", "status"}
    assert c.AUDIO_GENERATION_CREATE_REQUEST_SINGLE["mode"] == "single"
    assert c.AI_PROVIDER_CREATE_REQUEST["providerType"] in c.AI_PROVIDER_TYPES
    assert "secret_ciphertext" not in c.AI_PROVIDER_PUBLIC_FIELDS
    assert "apiKey" not in c.AI_PROVIDER_PUBLIC_FIELDS


def test_no_duplicate_enum_values_across_domains():
    # analysis 與 generation 的 status 值域不同，不得共用同一列舉常數集。
    assert set(c.ANALYSIS_STATUSES) != set(c.GENERATION_STATUSES)
    # domain 名稱彼此不得重複。
    assert c.DOMAIN_CHAPTER_ANALYSIS != c.DOMAIN_AUDIO_GENERATION
    assert c.DOMAIN_AI_PROVIDER != c.DOMAIN_TTS_PROVIDER
    assert c.DOMAIN_AUDIO_MODE not in (c.DOMAIN_CHAPTER_ANALYSIS, c.DOMAIN_AUDIO_GENERATION, c.DOMAIN_AI_PROVIDER, c.DOMAIN_TTS_PROVIDER)
