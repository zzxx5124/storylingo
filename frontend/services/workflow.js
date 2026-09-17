"use strict";
/* V4 Author Workflow（Phase 10）：
 * 把後端 derived workflow state（book.workflow）對映到產品語言 UI 動作，
 * 讓作者不需 provider/analyzer jargon 即知下一步。
 * 掛在 window.NovelWorkflow。
 */
(function () {
  // nextAction → 產品語言
  const NEXT_ACTION_LABEL = {
    set_voice: "選擇朗讀聲線",
    generate: "生成音訊",
    configure_voices: "設定各角色聲線",
    analyze: "開始分析角色",
    configure_ai_provider: "請管理員設定 AI 服務",
    configure_tts_provider: "請管理員設定朗讀服務",
  };

  // state → 產品語言狀態描述
  const STATE_LABEL = {
    needs_voice_configuration: "尚未選擇聲線",
    ready_to_generate: "可以生成音訊",
    audio_ready: "音訊已就緒",
    audio_generating: "音訊生成中",
    audio_stale: "音訊需重新生成",
    needs_analysis: "需要先分析角色",
    analysis_running: "角色分析中",
    provider_unavailable: "朗讀服務暫時無法使用",
    needs_content_review: "需要檢視內容",
    ready_to_submit: "可以送審",
  };

  // state → 建議動作（data-action）
  const STATE_ACTION = {
    set_voice: "set-voice",
    generate: "generate",
    configure_voices: "configure-voices",
    analyze: "analyze",
    provider_unavailable: "view-providers",
  };

  function nextActionLabel(action) {
    return NEXT_ACTION_LABEL[action] || "處理中";
  }

  function stateLabel(state) {
    return STATE_LABEL[state] || "處理中";
  }

  function stateAction(state) {
    return STATE_ACTION[state] || null;
  }

  // 給整本書的「下一步」文案
  function bookNextStep(workflow) {
    if (!workflow) return "處理中…";
    if (workflow.providerUnavailable) {
      return "朗讀服務暫時無法使用，請稍後再試或聯絡管理員。";
    }
    if (workflow.nextAction) {
      return "下一步：" + nextActionLabel(workflow.nextAction);
    }
    return "已完成，可以預覽音訊。";
  }

  // 給單一章節的動作按鈕文案與 data-action
  function chapterAction(ch) {
    const next = ch && ch.nextAction;
    if (!next) return null;
    return { label: nextActionLabel(next), action: stateAction(next) || next };
  }

  window.NovelWorkflow = {
    nextActionLabel,
    stateLabel,
    stateAction,
    bookNextStep,
    chapterAction,
  };
})();
