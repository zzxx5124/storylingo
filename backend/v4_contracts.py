"""V4 執行期契約（Phase 0 baseline）：最終 domain 名稱、列舉與 API 合約常數。

此模組是 V4 最終資料模型的「可執行規格」。所有後續 Phase 的 schema、
service 與測試都應以這裡的常數為單一真源，避免各處硬編碼字串漂移。

本模組不載入 runtime 依賴、不接觸 DB、不改變任何行為。
"""

# ---------------------------------------------------------------------------
# Final domain names（不可混淆的唯一領域名稱）
# ---------------------------------------------------------------------------
DOMAIN_CHAPTER_ANALYSIS = "chapter_analysis"
DOMAIN_AUDIO_GENERATION = "audio_generation"
DOMAIN_AI_PROVIDER = "ai_provider"
DOMAIN_TTS_PROVIDER = "tts_provider"
DOMAIN_AUDIO_MODE = "audio_mode"

# 對應的 final table 名稱
TABLE_CHAPTER_ANALYSES = "chapter_analyses"
TABLE_AUDIO_GENERATIONS = "audio_generations"
TABLE_AI_PROVIDERS = "ai_providers"
TABLE_TTS_PROVIDERS = "tts_providers"
TABLE_GENERATION_JOBS = "generation_jobs"

# 唯一性：同一 domain 不得有多個重複名稱。
FINAL_DOMAINS = (
    DOMAIN_CHAPTER_ANALYSIS,
    DOMAIN_AUDIO_GENERATION,
    DOMAIN_AI_PROVIDER,
    DOMAIN_TTS_PROVIDER,
    DOMAIN_AUDIO_MODE,
)

# ---------------------------------------------------------------------------
# audio_mode
# ---------------------------------------------------------------------------
AUDIO_MODE_SINGLE = "single"
AUDIO_MODE_MULTI = "multi"
AUDIO_MODES = (AUDIO_MODE_SINGLE, AUDIO_MODE_MULTI)

# ---------------------------------------------------------------------------
# chapter_analyses
# ---------------------------------------------------------------------------
ANALYSIS_TYPE_SPEAKER = "speaker"
ANALYSIS_TYPES = (ANALYSIS_TYPE_SPEAKER,)

ANALYSIS_STATUS_QUEUED = "queued"
ANALYSIS_STATUS_RUNNING = "running"
ANALYSIS_STATUS_READY = "ready"
ANALYSIS_STATUS_FAILED = "failed"
ANALYSIS_STATUSES = (
    ANALYSIS_STATUS_QUEUED,
    ANALYSIS_STATUS_RUNNING,
    ANALYSIS_STATUS_READY,
    ANALYSIS_STATUS_FAILED,
)

# chapter analysis artifact versions are scoped to this namespace only.
# v4 is the source-faithful canonical writer; v3 remains a validated
# compatibility/legacy artifact and v1/v2 are historical legacy inputs.
ANALYSIS_SCHEMA_VERSION_V1 = 1
ANALYSIS_SCHEMA_VERSION_LEGACY = ANALYSIS_SCHEMA_VERSION_V1
ANALYSIS_SCHEMA_VERSION_V2 = 2
ANALYSIS_SCHEMA_VERSION_V3 = 3
ANALYSIS_SCHEMA_VERSION_V4 = 4
ANALYSIS_SCHEMA_VERSION = ANALYSIS_SCHEMA_VERSION_V4
SPEAKER_ID_NARRATOR = "speaker:narrator"
SPEAKER_ID_UNRESOLVED = "speaker:unresolved"
ANALYSIS_ATTRIBUTION_METHODS = ("explicit", "continuation", "contextual", "manual", "unresolved")

# ---------------------------------------------------------------------------
# Canonical emotion registry（code-defined，V4 不做 DB 管理的情感分類）
# ---------------------------------------------------------------------------
EMOTION_NEUTRAL = "neutral"
CANONICAL_EMOTIONS = (
    "neutral",
    "happy",
    "sad",
    "angry",
    "tense",
)
EXPRESSIVE_INTENSITY_DEFAULT = 0.5
EXPRESSIVE_INTENSITY_MIN = 0.0
EXPRESSIVE_INTENSITY_MAX = 1.0

# emotion source marker：ai | fallback | manual
EMOTION_SOURCE_AI = "ai"
EMOTION_SOURCE_FALLBACK = "fallback"
EMOTION_SOURCE_MANUAL = "manual"
EMOTION_SOURCES = (
    EMOTION_SOURCE_AI,
    EMOTION_SOURCE_FALLBACK,
    EMOTION_SOURCE_MANUAL,
)

# emotion policy
EMOTION_POLICY_BEST_EFFORT = "best_effort"
EMOTION_POLICY_STRICT = "strict"
EMOTION_POLICIES = (EMOTION_POLICY_BEST_EFFORT, EMOTION_POLICY_STRICT)
EXPRESSIVE_CONTRACT_VERSION = 1
PROFILE_STATUS_ACTIVE = "active"
PROFILE_STATUS_ARCHIVED = "archived"
PROFILE_STATUS_PENDING = "pending"
PROFILE_STATUSES = (PROFILE_STATUS_ACTIVE, PROFILE_STATUS_ARCHIVED, PROFILE_STATUS_PENDING)

# ---------------------------------------------------------------------------
# audio_generations
# ---------------------------------------------------------------------------
GENERATION_STATUS_QUEUED = "queued"
GENERATION_STATUS_RUNNING = "running"
GENERATION_STATUS_READY = "ready"
GENERATION_STATUS_FAILED = "failed"
GENERATION_STATUS_FAILED_CAPABILITY = "failed_capability"
GENERATION_STATUSES = (
    GENERATION_STATUS_QUEUED,
    GENERATION_STATUS_RUNNING,
    GENERATION_STATUS_READY,
    GENERATION_STATUS_FAILED,
    GENERATION_STATUS_FAILED_CAPABILITY,
)

# ---------------------------------------------------------------------------
# ai_providers
# ---------------------------------------------------------------------------
AI_PROVIDER_TYPE_OPENAI = "openai"
AI_PROVIDER_TYPE_OPENAI_COMPATIBLE = "openai_compatible"
AI_PROVIDER_TYPE_DEEPSEEK = "deepseek"
AI_PROVIDER_TYPES = (
    AI_PROVIDER_TYPE_OPENAI,
    AI_PROVIDER_TYPE_OPENAI_COMPATIBLE,
    AI_PROVIDER_TYPE_DEEPSEEK,
)

# ---------------------------------------------------------------------------
# tts_providers / capabilities
# ---------------------------------------------------------------------------
# 已知 adapter keys；實作時若新增 adapter 需同步此清單。
TTS_ADAPTER_KEYS = ("generic_http", "openai_compatible", "cosyvoice_http")

TTS_CAPABILITIES_STATUS_UNKNOWN = "unknown"
TTS_CAPABILITIES_STATUS_OK = "ok"
TTS_CAPABILITIES_STATUS_ERROR = "error"
TTS_CAPABILITIES_STATUSES = (
    TTS_CAPABILITIES_STATUS_UNKNOWN,
    TTS_CAPABILITIES_STATUS_OK,
    TTS_CAPABILITIES_STATUS_ERROR,
)

# ---------------------------------------------------------------------------
# generation_jobs — canonical V4 job types
# ---------------------------------------------------------------------------
JOB_TYPE_SPEAKER_ANALYSIS = "speaker_analysis"
JOB_TYPE_SPEAKER_ANALYSIS_ALL = "speaker_analysis_all"
JOB_TYPE_AUDIO_SINGLE = "audio_single"
JOB_TYPE_AUDIO_MULTI = "audio_multi"
JOB_TYPE_AUDIO_GENERATE_ALL = "audio_generate_all"
JOB_TYPES = (
    JOB_TYPE_SPEAKER_ANALYSIS,
    JOB_TYPE_SPEAKER_ANALYSIS_ALL,
    JOB_TYPE_AUDIO_SINGLE,
    JOB_TYPE_AUDIO_MULTI,
    JOB_TYPE_AUDIO_GENERATE_ALL,
)

# ---------------------------------------------------------------------------
# Derived author workflow state（§3.10 read model）
# ---------------------------------------------------------------------------
WORKFLOW_NEEDS_CONTENT_REVIEW = "needs_content_review"
WORKFLOW_NEEDS_ANALYSIS = "needs_analysis"
WORKFLOW_ANALYSIS_RUNNING = "analysis_running"
WORKFLOW_ANALYSIS_FAILED = "analysis_failed"
WORKFLOW_NEEDS_VOICE_CONFIGURATION = "needs_voice_configuration"
WORKFLOW_READY_TO_GENERATE = "ready_to_generate"
WORKFLOW_AUDIO_GENERATING = "audio_generating"
WORKFLOW_AUDIO_READY = "audio_ready"
WORKFLOW_AUDIO_STALE = "audio_stale"
WORKFLOW_AUDIO_FAILED = "audio_failed"
WORKFLOW_PROVIDER_UNAVAILABLE = "provider_unavailable"
WORKFLOW_READY_TO_SUBMIT = "ready_to_submit"
WORKFLOW_STATES = (
    WORKFLOW_NEEDS_CONTENT_REVIEW,
    WORKFLOW_NEEDS_ANALYSIS,
    WORKFLOW_ANALYSIS_RUNNING,
    WORKFLOW_ANALYSIS_FAILED,
    WORKFLOW_NEEDS_VOICE_CONFIGURATION,
    WORKFLOW_READY_TO_GENERATE,
    WORKFLOW_AUDIO_GENERATING,
    WORKFLOW_AUDIO_READY,
    WORKFLOW_AUDIO_STALE,
    WORKFLOW_AUDIO_FAILED,
    WORKFLOW_PROVIDER_UNAVAILABLE,
    WORKFLOW_READY_TO_SUBMIT,
)

# ---------------------------------------------------------------------------
# Config versions（任何會改變生成輸出的設定都必須 increment）
# ---------------------------------------------------------------------------
DEFAULT_AUDIO_SETTINGS_VERSION = 1
DEFAULT_AI_CONFIG_VERSION = 1
DEFAULT_TTS_CONFIG_VERSION = 1

# ---------------------------------------------------------------------------
# Canonical analysis artifact v2（最小 shape；emotion 為選用）
# ---------------------------------------------------------------------------
CANONICAL_ANALYSIS_ARTIFACT_V2 = {
    "schemaVersion": ANALYSIS_SCHEMA_VERSION_V2,
    "chapterKey": "...",
    "sourceTextHash": "...",
    "speakers": [],
    "segments": [
        {
            "id": "stable-within-artifact",
            "text": "...",
            "speaker": "narrator",
            "emotion": {
                "value": EMOTION_NEUTRAL,
                "intensity": 0.0,
                "source": EMOTION_SOURCE_FALLBACK,
            },
        }
    ],
}

CANONICAL_ANALYSIS_ARTIFACT_V3 = {
    "schemaVersion": ANALYSIS_SCHEMA_VERSION_V3,
    "chapterKey": "...",
    "sourceTextHash": "...",
    "characters": [{"character_id": "char_x", "canonical_name": "...", "aliases": []}],
    "segments": [{
        "segment_id": "seg-0", "type": "narration", "text": "...",
        "speaker_id": SPEAKER_ID_NARRATOR, "speaker_surface": "旁白",
        "emotion": {"label": EMOTION_NEUTRAL, "intensity": 0.0, "source": EMOTION_SOURCE_FALLBACK},
        "source_start": 0, "source_end": 3,
    }],
}

# ---------------------------------------------------------------------------
# Canonical API request/response contracts（§5）
# ---------------------------------------------------------------------------

# POST /api/books/{bid}/chapters/{seq}/analysis
ANALYSIS_CREATE_REQUEST = {
    # mode 固定為 speaker；request 不帶 provider 機密。
    "analysisType": ANALYSIS_TYPE_SPEAKER,
}

ANALYSIS_CREATE_RESPONSE = {
    "analysisId": 123,
    "jobId": 456,
    "status": ANALYSIS_STATUS_QUEUED,
}

# GET /api/books/{bid}/chapters/{seq}/analysis
ANALYSIS_GET_RESPONSE = {
    "analysisId": 123,
    "status": ANALYSIS_STATUS_READY,
    "schemaVersion": ANALYSIS_SCHEMA_VERSION,
    "chapterKey": "...",
    "sourceTextHash": "...",
    "speakers": [],
    "segments": [],
}

# POST /api/books/{bid}/chapters/{seq}/audio-generations
AUDIO_GENERATION_CREATE_REQUEST_SINGLE = {
    "mode": AUDIO_MODE_SINGLE,
    "voiceId": "voice-123",
    "emotionPolicy": EMOTION_POLICY_BEST_EFFORT,
}

AUDIO_GENERATION_CREATE_RESPONSE = {
    "generationId": 789,
    "jobId": 456,
    "status": GENERATION_STATUS_QUEUED,
}

# GET /api/books/{bid}/chapters/{seq}/audio-generations/{generation_id}
AUDIO_GENERATION_GET_RESPONSE = {
    "generationId": 789,
    "mode": AUDIO_MODE_SINGLE,
    "status": GENERATION_STATUS_READY,
    "audioUrl": "/api/books/{bid}/audio/{seq}",
}

# POST /api/admin/ai/providers
AI_PROVIDER_CREATE_REQUEST = {
    "name": "...",
    "providerType": AI_PROVIDER_TYPE_OPENAI,
    "baseUrl": "https://...",
    "model": "...",
    "fallbackModel": None,
    "apiKey": "only-on-create",
    "enabled": True,
    "isDefault": False,
}

# 管理員讀取回傳：secret 永不回傳。
AI_PROVIDER_PUBLIC_FIELDS = (
    "id",
    "name",
    "providerType",
    "baseUrl",
    "model",
    "fallbackModel",
    "enabled",
    "isDefault",
    "configVersion",
    "lastStatus",
    "lastError",
    "lastCheckedAt",
    "lastUsedAt",
    "createdAt",
    "updatedAt",
    "deletedAt",
    "hasSecret",
)
