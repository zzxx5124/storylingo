import { describe, it, expect } from "vitest";
import "../../services/reader.js";

describe("NovelReader（Phase 11 讀者播放/進度/能力 UX）", () => {
  const R = window.NovelReader;
  const book = {
    chapters: [
      { seq: 0, title: "序", audio: "ready", status: "analyzed" },
      { seq: 1, title: "第一章", audio: "none", status: "pending" },
      { seq: 2, title: "第二章", audio: "ready", status: "analyzed" },
    ],
  };

  it("prevNext 提供章節導覽", () => {
    const mid = R.prevNext(book, 1);
    expect(mid.prev).toBe(0);
    expect(mid.next).toBe(2);
    expect(R.prevNext(book, 0).hasPrev).toBe(false);
    expect(R.prevNext(book, 2).hasNext).toBe(false);
    expect(R.prevNext(book, 99)).toEqual({ prev: null, next: null, hasPrev: false, hasNext: false });
  });

  it("toc 標示可讀章節", () => {
    const t = R.toc(book);
    expect(t.length).toBe(3);
    expect(t[0].viewable).toBe(true);
    expect(t[1].viewable).toBe(false);
  });

  it("learningAvailable 依分析產物決定，不承諾不存在能力", () => {
    expect(R.learningAvailable(null)).toBe(false);
    expect(R.learningAvailable({})).toBe(false);
    expect(R.learningAvailable({ segments: [] })).toBe(false);
    expect(R.learningAvailable({ segments: [{ id: "s0" }] })).toBe(true);
  });

  it("normalizeProgress 統一 read/listen 進度", () => {
    expect(R.normalizeProgress(null)).toEqual({ chapterSeq: 0, position: 0, percent: 0, audioPositionSeconds: 0, lastMode: "read" });
    const listen = R.normalizeProgress({ chapter_seq: 2, audio_position_seconds: 42.5, last_mode: "listen" });
    expect(listen.chapterSeq).toBe(2);
    expect(listen.audioPositionSeconds).toBe(42.5);
    expect(listen.lastMode).toBe("listen");
    const read = R.normalizeProgress({ chapterSeq: 1, position: 100 });
    expect(read.chapterSeq).toBe(1);
    expect(read.position).toBe(100);
  });

  it("限制播放速度、進度與 mode", () => {
    expect(R.normalizeSpeed("999")).toBe(1);
    expect(R.normalizeSpeed("1.5")).toBe(1.5);
    expect(R.clamp(-4, 0, 100)).toBe(0);
    expect(R.clamp(140, 0, 100)).toBe(100);
    expect(R.normalizeProgress({ chapterSeq: -2, position: -3, percent: 140, lastMode: "unknown" })).toEqual({
      chapterSeq: 0, position: 0, percent: 100, audioPositionSeconds: 0, lastMode: "read",
    });
  });

  it("只在自然結束且有下一章時允許 auto-next", () => {
    expect(R.shouldAutoAdvance({ enabled: true, ended: true, manualPause: false, next: 2 })).toBe(true);
    expect(R.shouldAutoAdvance({ enabled: false, ended: true, manualPause: false, next: 2 })).toBe(false);
    expect(R.shouldAutoAdvance({ enabled: true, ended: false, manualPause: false, next: 2 })).toBe(false);
    expect(R.shouldAutoAdvance({ enabled: true, ended: true, manualPause: true, next: 2 })).toBe(false);
    expect(R.shouldAutoAdvance({ enabled: true, ended: true, manualPause: false, next: null })).toBe(false);
  });

  it("只把 ready chapter 視為可播放，metadata 不含 private identity", () => {
    expect(R.isPlayableChapter({ audio: "ready" })).toBe(true);
    expect(R.isPlayableChapter({ audio: "pending" })).toBe(false);
    expect(R.safeMediaMetadata("很長的書名", "第一章")).toEqual({
      title: "第一章", artist: "語閱 StoryLingo", album: "很長的書名",
    });
    expect(R.progressPayload({ bid: "b", seq: 3, position: 10, percent: 50, audioPositionSeconds: 8, mode: "listen" })).toEqual({
      bookId: "b", chapterSeq: 3, position: 10, percent: 50, audioPositionSeconds: 8, lastMode: "listen",
    });
    expect(R.progressPayload({ bid: "b", seq: 3, mode: "reading" }).lastMode).toBe("read");
  });
});
