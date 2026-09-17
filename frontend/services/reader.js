"use strict";
/* V4 Reader UX（Phase 11）：
 * - 章節導覽（prev/next/TOC）。
 * - 依分析產物是否存在，決定是否提供 learning / read-along 能力（不可承諾不存在的能力）。
 * - 統一 read / listen 進度的讀取輔助。
 * 掛在 window.NovelReader。
 */
(function () {
  function chapters(book) {
    return Array.isArray(book && book.chapters) ? book.chapters : [];
  }

  function viewable(ch) {
    // 有音訊 ready 或（作者/admin）已分析即可閱讀
    return !!(ch && (ch.audio === "ready" || ch.status === "analyzed"));
  }

  function prevNext(book, seq) {
    const chs = chapters(book);
    const i = chs.findIndex((c) => Number(c.seq) === Number(seq));
    if (i < 0) return { prev: null, next: null, hasPrev: false, hasNext: false };
    const prev = i > 0 ? chs[i - 1] : null;
    const next = i < chs.length - 1 ? chs[i + 1] : null;
    return {
      prev: prev ? prev.seq : null,
      next: next ? next.seq : null,
      hasPrev: !!prev,
      hasNext: !!next,
    };
  }

  function toc(book) {
    return chapters(book).map((c) => ({ seq: c.seq, title: c.title, viewable: viewable(c) }));
  }

  // learning / read-along 能力是否可用（需有分析產物 segments）
  function learningAvailable(analysis) {
    const segs = analysis && Array.isArray(analysis.segments) ? analysis.segments : [];
    return segs.length > 0;
  }

  // 統一進度讀取：回傳 {chapterSeq, position, audioPositionSeconds, lastMode}
  const MODES = ["reading", "listen", "learning"];
  const SPEEDS = [0.75, 0.85, 1, 1.25, 1.5, 2];

  function numberOr(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
    return Math.min(maximum, Math.max(minimum, numberOr(value, minimum)));
  }

  function normalizeMode(value, fallback = "listen") {
    const mode = String(value || "").toLowerCase();
    return MODES.includes(mode) ? mode : fallback;
  }

  function normalizeSpeed(value, fallback = 1) {
    const speed = numberOr(value, fallback);
    return SPEEDS.includes(speed) ? speed : fallback;
  }

  function normalizeProgress(raw) {
    if (!raw) return { chapterSeq: 0, position: 0, percent: 0, audioPositionSeconds: 0, lastMode: "read" };
    const seqVal = raw.chapter_seq != null ? raw.chapter_seq : raw.chapterSeq != null ? raw.chapterSeq : raw.seq || 0;
    const audioVal = raw.audio_position_seconds != null ? raw.audio_position_seconds : raw.audioPositionSeconds != null ? raw.audioPositionSeconds : 0;
    const mode = raw.last_mode != null ? raw.last_mode : raw.lastMode != null ? raw.lastMode : "read";
    return {
      chapterSeq: Math.max(0, Math.floor(numberOr(seqVal, 0))),
      position: clamp(raw.position, 0),
      percent: clamp(raw.percent, 0, 100),
      audioPositionSeconds: clamp(audioVal, 0),
      lastMode: normalizeMode(mode, "read"),
    };
  }

  function progressPayload(reader) {
    const mode = normalizeMode(reader?.mode || reader?.lastMode, "read");
    return {
      bookId: String(reader?.bid || ""),
      chapterSeq: Math.max(0, Math.floor(numberOr(reader?.seq, 0))),
      position: clamp(reader?.position, 0),
      percent: clamp(reader?.percent, 0, 100),
      audioPositionSeconds: clamp(reader?.audioPositionSeconds, 0),
      lastMode: mode === "reading" ? "read" : mode,
    };
  }

  function storageKey(bookId) {
    return "novel-reader:" + encodeURIComponent(String(bookId || "")).slice(0, 180);
  }

  function autoNextKey(bookId) {
    return "novel-reader-auto-next:" + encodeURIComponent(String(bookId || "")).slice(0, 180);
  }

  function isAutoNextEnabled(value) {
    return value === true || value === "1" || value === "true";
  }

  function shouldAutoAdvance(options = {}) {
    return Boolean(options.enabled && options.ended && !options.manualPause && options.next != null);
  }

  function isPlayableChapter(chapter) {
    return Boolean(chapter && (chapter.audio === "ready" || chapter.playable === true));
  }

  function safeMediaMetadata(bookTitle, chapterTitle) {
    const trim = (value, fallback) => String(value || fallback).trim().slice(0, 160);
    return {
      title: trim(chapterTitle, "章節"),
      artist: "語閱 StoryLingo",
      album: trim(bookTitle, "公開作品"),
    };
  }

  window.NovelReader = {
    chapters, viewable, prevNext, toc, learningAvailable, normalizeProgress,
    numberOr, clamp, normalizeMode, normalizeSpeed, progressPayload, storageKey,
    autoNextKey, isAutoNextEnabled, shouldAutoAdvance, isPlayableChapter, safeMediaMetadata,
    MODES, SPEEDS,
  };
})();
