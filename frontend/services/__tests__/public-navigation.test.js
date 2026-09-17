import { beforeAll, beforeEach, describe, expect, it } from "vitest";

describe("公開導覽 disclosure", () => {
  beforeAll(async () => {
    document.body.innerHTML = `
      <header id="topbar">
        <button id="nav-toggle" aria-expanded="false" aria-label="開啟導覽選單" aria-controls="main-nav">選單</button>
        <nav id="main-nav">
          <a data-public-nav="home" href="#/home">探索</a>
          <a data-public-nav="search" href="#/search">搜尋</a>
          <a class="notification-nav" id="nav-notifications" href="#/notifications">通知</a>
        </nav>
      </header>`;
    await import("../public-navigation.js?public-navigation-unit");
    document.dispatchEvent(new Event("DOMContentLoaded"));
  });

  beforeEach(() => {
    location.hash = "#/home";
    document.querySelector("#main-nav").classList.remove("nav-open");
    document.querySelector("#nav-toggle").setAttribute("aria-expanded", "false");
    document.querySelector("#nav-toggle").setAttribute("aria-label", "開啟導覽選單");
    window.StoryLingoPublicNav.sync();
  });

  it("以 accessible state 開關選單並支援 Escape", () => {
    const toggle = document.querySelector("#nav-toggle");
    toggle.click();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector("#main-nav")).toHaveClass("nav-open");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelector("#main-nav")).not.toHaveClass("nav-open");
  });

  it("依目前 hash 標示公開目的地，通知入口不會關閉其 popover ancestor", () => {
    location.hash = "#/search?q=故事";
    window.StoryLingoPublicNav.sync();
    expect(document.querySelector("[data-public-nav=search]"))
      .toHaveAttribute("aria-current", "page");
    const toggle = document.querySelector("#nav-toggle");
    toggle.click();
    document.querySelector("#nav-notifications").click();
    expect(document.querySelector("#main-nav")).toHaveClass("nav-open");
  });
});
