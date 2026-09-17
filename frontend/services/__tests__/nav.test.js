import { describe, it, expect } from "vitest";
import "../../services/nav.js";

describe("NovelNav（Phase 12 導覽/工作區收斂）", () => {
  const N = window.NovelNav;

  it("角色工作區：作者僅一個 creation 入口（去重複）", () => {
    expect(N.roleWorkspaces("reader")).toEqual(["reading"]);
    expect(N.roleWorkspaces("author")).toEqual(["reading", "creation"]);
    expect(N.roleWorkspaces("admin")).toEqual(["reading", "creation", "admin"]);
  });

  it("visibleNav 依角色回傳 nav items", () => {
    const reader = N.visibleNav("reader");
    expect(reader.map((i) => i.id)).toEqual(["nav-shelf"]);
    const author = N.visibleNav("author");
    expect(author.map((i) => i.id)).toEqual(["nav-shelf", "nav-mine"]);
    const admin = N.visibleNav("admin");
    expect(admin.map((i) => i.id)).toEqual(["nav-shelf", "nav-mine", "nav-admin"]);
  });

  it("唯一作者入口為 #/mine（creation），#/bookshelf 非作者入口", () => {
    expect(N.isAuthorEntry("#/mine")).toBe(true);
    expect(N.isAuthorEntry("#/bookshelf")).toBe(false);
  });
});
