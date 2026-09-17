import { describe, it, expect } from "vitest";
import "../../services/workflow.js";

describe("NovelWorkflow（Phase 10 作者工作流產品語言）", () => {
  const W = window.NovelWorkflow;

  it("nextAction 對映為產品語言，不含 provider jargon", () => {
    expect(W.nextActionLabel("generate")).toBe("生成音訊");
    expect(W.nextActionLabel("set_voice")).toBe("選擇朗讀聲線");
    expect(W.nextActionLabel("analyze")).toBe("開始分析角色");
    expect(W.nextActionLabel("configure_tts_provider")).toBe("請管理員設定朗讀服務");
    expect(W.nextActionLabel("configure_ai_provider")).toBe("請管理員設定 AI 服務");
  });

  it("stateLabel 對映為產品語言狀態", () => {
    expect(W.stateLabel("ready_to_generate")).toBe("可以生成音訊");
    expect(W.stateLabel("audio_ready")).toBe("音訊已就緒");
    expect(W.stateLabel("needs_analysis")).toBe("需要先分析角色");
    expect(W.stateLabel("provider_unavailable")).toBe("朗讀服務暫時無法使用");
  });

  it("bookNextStep 呈現整書下一步與 provider outage", () => {
    expect(W.bookNextStep({ providerUnavailable: true })).toContain("朗讀服務暫時無法使用");
    expect(W.bookNextStep({ providerUnavailable: false, nextAction: "generate" })).toBe("下一步：生成音訊");
    expect(W.bookNextStep({ providerUnavailable: false, nextAction: null })).toBe("已完成，可以預覽音訊。");
  });

  it("chapterAction 依章節 nextAction 提供動作按鈕", () => {
    expect(W.chapterAction({ nextAction: "set_voice" })).toEqual({ label: "選擇朗讀聲線", action: "set-voice" });
    expect(W.chapterAction({ nextAction: "generate" })).toEqual({ label: "生成音訊", action: "generate" });
    expect(W.chapterAction({ nextAction: null })).toBeNull();
    expect(W.chapterAction({})).toBeNull();
  });
});
