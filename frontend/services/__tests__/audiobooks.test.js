import { describe, expect, it } from "vitest";
import "../../services/audiobooks.js";

describe("StoryLingoAudiobooks", () => {
  const audiobooks = window.StoryLingoAudiobooks;

  it("normalizes supported filters and safely defaults invalid URL state", () => {
    expect(audiobooks.normalizeQuery("?q=%E6%95%85%E4%BA%8B&category_id=12&language=en&sort=title&page=3")).toEqual({
      q: "故事", category_id: "12", language: "en", sort: "title", page: 3,
    });
    expect(audiobooks.normalizeQuery("?language=secret&sort=provider&page=0")).toEqual({
      q: "", category_id: "", language: "", sort: "newest", page: 1,
    });
  });

  it("serializes canonical query state without leaking empty defaults", () => {
    expect(audiobooks.toQuery({ q: "  作品 ", category_id: "4", language: "zh", sort: "updated", page: 2 }))
      .toBe("q=%E4%BD%9C%E5%93%81&category_id=4&language=zh&sort=updated&page=2");
    expect(audiobooks.toQuery({})).toBe("");
  });

  it("labels availability explicitly and rejects unsafe listen routes", () => {
    expect(audiobooks.availabilityLabel({ availability: "available" })).toBe("可聆聽");
    expect(audiobooks.availabilityLabel({ availability: "partial" })).toBe("部分章節可聆聽");
    expect(audiobooks.safeListenRoute({ listenRoute: "#/read/book-1/3?mode=listen" })).toBe("#/read/book-1/3?mode=listen");
    expect(audiobooks.safeListenRoute({ listenRoute: "javascript:alert(1)" })).toBeNull();
    expect(audiobooks.safeListenRoute({ listenRoute: "#/read/../private/0?mode=listen" })).toBeNull();
  });
});
