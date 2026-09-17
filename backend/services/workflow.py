"""V4 derived author workflow state（§3.10 read model）。

從 canonical book/chapter/analysis/generation/provider state 推導「下一步動作」，
不另建持久化 state machine。依 §5.7，此資訊附加於既有 author book payload。

Phase 15b：狀態一律由 canonical 紀錄推導（chapter_analyses／audio_generations／
active_audio_generation_id），不再依賴 legacy chapters.status／audio 欄位。
"""
import json
import os

from .. import v4_contracts as c
from .. import db
from . import analysis as analysis_svc


def _generation_error(value: str | None) -> str:
    """將持久化的 TTS error envelope 轉成安全產品訊息。"""
    if not value:
        return "生成音訊失敗"
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value
    if not isinstance(parsed, dict):
        return value
    return str(parsed.get("message") or value)


def _artifact(record: dict) -> dict | None:
    if not record or record.get("status") != "ready" or not record.get("artifact_path"):
        return None
    from .. import settings
    p = os.path.join(settings.ROOT_DIR, record["artifact_path"])
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _multi_unassigned(row: dict, record: dict) -> list[str]:
    """multi 模式：ready analysis 的語者尚未指定聲線者。"""
    artifact = _artifact(record)
    if artifact is None:
        return []
    artifact = analysis_svc.legacy_compatibility_view(artifact)
    voices = json.loads(row.get("voices") or "{}")
    names = []
    for sp in artifact.get("speakers", []):
        name = (sp.get("name") or "").strip()
        if name and not (voices.get(name) or "").strip() and name not in names:
            names.append(name)
    return sorted(names)


def compute_book_workflow(row: dict, chapters: list[dict]) -> dict:
    """計算整本書的 derived workflow state（供 owner/admin）。

    回傳 {
      mode, hasDefaultVoice, aiProviderAvailable, ttsProviderAvailable,
      state, needsAction, nextAction, providerUnavailable,
      total, analyzed, ready,
         chapters: { seq: {state, nextAction, providerUnavailable, analysisProgress} }
    }
    """
    mode = (row.get("audio_mode") or c.AUDIO_MODE_SINGLE)
    if mode not in c.AUDIO_MODES:
        mode = c.AUDIO_MODE_SINGLE
    default_voice = row.get("default_voice_id") or None

    ai_available = db.get_default_ai_provider() is not None
    tts_available = db.get_active_tts_provider() is not None

    total = len(chapters)
    analyzed = 0
    ready = 0
    generating = 0
    chapter_map = {}
    needs_action = False
    next_action = None
    provider_unavailable = False

    for ch in chapters:
        seq = ch["seq"]
        state, action, pu, error, analysis_progress = _chapter_state(
            row, ch, mode, default_voice, ai_available, tts_available)
        if db.get_ready_chapter_analysis(ch["id"], ch["text_hash"]):
            analyzed += 1
        if _active_ready(row, ch):
            ready += 1
        if state == c.WORKFLOW_AUDIO_GENERATING:
            generating += 1
        if action:
            needs_action = True
            if next_action is None:
                next_action = action
        if pu:
            provider_unavailable = True
        chapter_map[seq] = {"state": state, "nextAction": action, "providerUnavailable": pu,
                            "error": error or "", "analysisProgress": analysis_progress}

    book_state = _book_state(mode, analyzed, ready, generating, total,
                             ai_available, tts_available, provider_unavailable)
    return {
        "mode": mode,
        "hasDefaultVoice": bool(default_voice),
        "aiProviderAvailable": ai_available,
        "ttsProviderAvailable": tts_available,
        "state": book_state,
        "needsAction": needs_action,
        "nextAction": next_action,
        "providerUnavailable": provider_unavailable,
        "total": total, "analyzed": analyzed, "ready": ready,
        "chapters": chapter_map,
    }


def _active_ready(row: dict, ch: dict) -> bool:
    """章節 active pointer 指向的 generation 是否為 ready、文字相符且 mode 相符。"""
    aid = ch.get("active_audio_generation_id")
    if not aid:
        return False
    gen = db.get_audio_generation(aid)
    if not gen or gen["status"] != "ready" or gen["source_text_hash"] != ch["text_hash"]:
        return False
    mode = ch.get("audio_mode_override") or row.get("audio_mode") or c.AUDIO_MODE_SINGLE
    return gen["mode"] == mode


def _book_state(mode, analyzed, ready, generating, total, ai_available, tts_available,
                provider_unavailable):
    if provider_unavailable:
        return c.WORKFLOW_PROVIDER_UNAVAILABLE
    if ready == total and total:
        return c.WORKFLOW_AUDIO_READY
    if generating:
        return c.WORKFLOW_AUDIO_GENERATING
    if mode == c.AUDIO_MODE_SINGLE:
        return c.WORKFLOW_READY_TO_GENERATE
    if analyzed == total and total:
        return c.WORKFLOW_READY_TO_GENERATE
    return c.WORKFLOW_NEEDS_ANALYSIS


def _chapter_state(row, ch, mode, default_voice, ai_available, tts_available):
    """回傳 (state, nextAction, providerUnavailable, error, analysisProgress)。"""
    effective_mode = ch.get("audio_mode_override") or mode
    if effective_mode not in c.AUDIO_MODES:
        effective_mode = mode

    # ---- newest analysis attempt takes precedence over older ready output ----
    # A forced re-analysis creates a new analysis while an older ready artifact
    # may still be usable.  The latest attempt must nevertheless be visible so
    # the UI does not report "completed" before that job actually finishes.
    latest_analysis = db.get_latest_chapter_analysis(ch["id"]) \
        if effective_mode == c.AUDIO_MODE_MULTI else None
    if latest_analysis and latest_analysis["status"] in ("queued", "running"):
        progress = analysis_svc.parse_analysis_progress(latest_analysis)
        retry_message = "正在自動重試" if progress.get("retryCount", 0) else ""
        if latest_analysis["status"] == "running" and progress.get("legacyProgress"):
            retry_message = "分析狀態待恢復"
        return c.WORKFLOW_ANALYSIS_RUNNING, None, False, retry_message, progress
    if latest_analysis and latest_analysis["status"] == "failed":
        progress = analysis_svc.parse_analysis_progress(latest_analysis)
        return c.WORKFLOW_ANALYSIS_FAILED, "analyze", False, progress.get("lastError") or "分析失敗", progress

    # ---- canonical audio state ----
    active_id = ch.get("active_audio_generation_id")
    gen = db.get_audio_generation(active_id) if active_id else None
    latest = db.list_audio_generations(ch["id"])
    if latest:
        head = latest[0]
        if head["status"] in ("queued", "running") \
                and head["source_text_hash"] == ch["text_hash"] and head["mode"] == effective_mode:
            return c.WORKFLOW_AUDIO_GENERATING, None, False, "", None
        if head["status"] in ("failed", "failed_capability") \
                and head["source_text_hash"] == ch["text_hash"] and head["mode"] == effective_mode:
            return c.WORKFLOW_AUDIO_FAILED, "generate", False, _generation_error(head.get("error")), None
    if gen and gen["status"] == "ready" and gen["source_text_hash"] == ch["text_hash"] \
            and gen["mode"] == effective_mode:
        return c.WORKFLOW_AUDIO_READY, None, False, "", None
    if gen and gen["status"] in ("queued", "running") and gen["mode"] == effective_mode:
        return c.WORKFLOW_AUDIO_GENERATING, None, False, "", None
    if gen and gen["status"] in ("failed", "failed_capability") \
            and gen["source_text_hash"] == ch["text_hash"] and gen["mode"] == effective_mode:
        return c.WORKFLOW_AUDIO_FAILED, "generate", False, _generation_error(gen.get("error")), None

    # ---- provider outage 呈現為 platform state ----
    if effective_mode == c.AUDIO_MODE_MULTI and not ai_available:
        return c.WORKFLOW_PROVIDER_UNAVAILABLE, "configure_ai_provider", True, "", None
    if not tts_available:
        return c.WORKFLOW_PROVIDER_UNAVAILABLE, "configure_tts_provider", True, "", None

    if effective_mode == c.AUDIO_MODE_SINGLE:
        # single：不需 analysis，直接生成；voice 若未設需先設
        if not default_voice and not ch.get("voice_override_id"):
            return c.WORKFLOW_NEEDS_VOICE_CONFIGURATION, "set_voice", False, "", None
        return c.WORKFLOW_READY_TO_GENERATE, "generate", False, "", None

    # ---- multi：analyze → voice mapping → generate ----
    ready_analysis = db.get_ready_chapter_analysis(ch["id"], ch["text_hash"])
    if ready_analysis:
        unassigned = _multi_unassigned(row, ready_analysis)
        if unassigned:
            return c.WORKFLOW_NEEDS_VOICE_CONFIGURATION, "configure_voices", False, "", None
        return c.WORKFLOW_READY_TO_GENERATE, "generate", False, "", None
    # latest_analysis was handled before canonical audio state so an older
    # ready artifact cannot mask a newer failed/running analysis.
    return c.WORKFLOW_NEEDS_ANALYSIS, "analyze", False, "", None
