import { describe, it, expect } from "vitest";
import { bookCard } from "../BookCard.js";

describe("BookCard 元件", () => {
  it("渲染書名、作者、封面 alt 與連結", () => {
    document.body.innerHTML = bookCard({ bookId: "sword", title: "劍起蒼穹", author: "清風道人" });
    const link = document.querySelector(".book-card");
    expect(link).toHaveAttribute("href", "/book/sword");
    expect(document.querySelector(".book-title").textContent).toBe("劍起蒼穹");
    expect(document.querySelector(".book-author").textContent).toBe("清風道人");
    expect(document.querySelector(".book-cover")).toHaveAttribute("alt", "劍起蒼穹 封面");
  });

  it("有分數時顯示 score，無分數時隱藏", () => {
    document.body.innerHTML =
      bookCard({ bookId: "a", title: "A", score: 8.9 }) +
      bookCard({ bookId: "b", title: "B", score: 0 });
    const scores = document.querySelectorAll(".book-score");
    expect(scores.length).toBe(1);
    expect(scores[0].textContent).toBe("8.9");
  });

  it("最多顯示 3 個標籤", () => {
    document.body.innerHTML = bookCard({ bookId: "a", title: "A", tags: ["奇幻", "修仙", "爽文", "hot"] });
    expect(document.querySelectorAll(".book-tag").length).toBe(3);
  });

  it("顯示最新章節標題，缺失時省略", () => {
    document.body.innerHTML =
      bookCard({ bookId: "a", title: "A", lastChapterTitle: "第 10 章" }) +
      bookCard({ bookId: "b", title: "B" });
    expect(document.querySelector(".book-last-chapter").textContent).toContain("第 10 章");
    expect(document.querySelectorAll(".book-last-chapter").length).toBe(1);
  });

  it("無封面時使用內建佔位 cover", () => {
    document.body.innerHTML = bookCard({ bookId: "a", title: "A" });
    const cover = document.querySelector(".book-cover");
    expect(cover.getAttribute("src")).toMatch(/^data:image\/svg\+xml/i);
    expect(cover).toHaveAttribute("data-fallback", cover.getAttribute("src"));
  });

  it("封面路徑失效時回退到內建佔位 cover", () => {
    document.body.innerHTML = bookCard({ bookId: "a", title: "A", cover: "/missing-cover.jpg" });
    const cover = document.querySelector(".book-cover");
    const fallback = cover.getAttribute("data-fallback");
    cover.dispatchEvent(new Event("error"));
    expect(cover.getAttribute("src")).toBe(fallback);
  });

  it("書名與作者跳脫避免 XSS", () => {
    document.body.innerHTML = bookCard({ bookId: "a", title: "<script>x</script>", author: "<b>y</b>" });
    expect(document.querySelector(".book-card script")).toBeNull();
    expect(document.querySelector(".book-title b")).toBeNull();
    expect(document.querySelector(".book-author b")).toBeNull();
  });

  it("bookId 用於連結與 data-book-id", () => {
    document.body.innerHTML = bookCard({ bookId: "star sea", title: "星海" });
    const link = document.querySelector(".book-card");
    expect(link).toHaveAttribute("href", "/book/star%20sea");
    expect(link).toHaveAttribute("data-book-id", "star sea");
  });
});
