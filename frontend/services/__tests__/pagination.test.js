import { describe, expect, it } from "vitest";
import "../../services/pagination.js";

describe("StoryLingoPagination", () => {
  const pagination = window.StoryLingoPagination;

  it("normalizes the canonical envelope and legacy arrays", () => {
    expect(pagination.normalize({ items: [1], page: 2, page_size: 20, total: 41, total_pages: 3 }).has_prev).toBe(true);
    const legacy = pagination.normalize([1, 2]);
    expect(legacy.items).toEqual([1, 2]);
    expect(legacy.total_pages).toBe(1);
    expect(legacy.legacy).toBe(true);
  });

  it("renders labelled keyboard-friendly boundary controls", () => {
    const html = pagination.render({ items: [1], page: 2, page_size: 20, total: 41, total_pages: 3 }, {
      label: "搜尋結果分頁",
      hrefForPage: (page) => `#/search?page=${page}`,
    });
    expect(html).toContain('aria-label="搜尋結果分頁"');
    expect(html).toContain('aria-current="page"');
    expect(html).toContain("#/search?page=1");
    expect(html).toContain("#/search?page=3");
  });

  it("does not show misleading controls for a single page", () => {
    expect(pagination.render({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 })).toBe("");
  });
});
