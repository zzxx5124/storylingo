import { describe, it, expect } from "vitest";
import { iconButton } from "../IconButton.js";
import { statusBadge } from "../StatusBadge.js";
import { statusState } from "../StatusState.js";
import { pageHeader } from "../PageHeader.js";
import { filterBar } from "../FilterBar.js";
import { pagination } from "../Pagination.js";
import { confirmDialog } from "../ConfirmDialog.js";
import { vi } from "vitest";

describe("Design System foundation primitives", () => {
  it("IconButton 必須有獨立 accessible label", () => {
    document.body.innerHTML = iconButton({ label: "關閉對話框", icon: "✕" });
    expect(document.querySelector("button")).toHaveAttribute("aria-label", "關閉對話框");
    expect(document.querySelector("button")).toHaveClass("ui-icon-button");
    expect(() => iconButton({ icon: "✕" })).toThrow(/accessible label/);
  });

  it("StatusBadge 以文字呈現狀態且限制 tone", () => {
    document.body.innerHTML = statusBadge({ label: "待審核", tone: "pending", icon: "!" });
    expect(document.querySelector(".status-badge-label")).toHaveTextContent("待審核");
    expect(document.querySelector(".status-badge")).toHaveAttribute("data-status-tone", "pending");
    document.body.innerHTML = statusBadge({ label: "未知", tone: "custom" });
    expect(document.querySelector(".status-badge")).toHaveAttribute("data-status-tone", "neutral");
  });

  it("error/conflict state 使用 alert 且內容會跳脫", () => {
    document.body.innerHTML = statusState({ type: "conflict", title: "衝突", message: "<script>x</script>" });
    expect(document.querySelector(".status-state")).toHaveAttribute("role", "alert");
    expect(document.querySelector(".status-state-message")).toHaveTextContent("<script>x</script>");
    expect(document.querySelector("script")).toBeNull();
  });

  it("PageHeader / FilterBar / Pagination 保留語意與 bounded navigation", () => {
    document.body.innerHTML = pageHeader({ title: "作品申請", description: "檢視目前狀態", level: 2 }) +
      filterBar({ label: "作品篩選", content: '<label>狀態<select><option>全部</option></select></label>' }) +
      pagination({ page: 2, totalPages: 3, total: 42, hrefForPage: (page) => `#page-${page}` });
    expect(document.querySelector(".foundation-page-header h2")).toHaveTextContent("作品申請");
    expect(document.querySelector(".ui-filter-bar")).toHaveAttribute("aria-label", "作品篩選");
    expect(document.querySelector(".ui-pagination")).toHaveAttribute("aria-label", "分頁");
    expect(document.querySelector("[aria-current='page']")).toHaveTextContent("第 2 頁");
    expect(document.querySelector(".foundation-pagination-status")).toHaveTextContent("共 42 項");
  });

  it("Pagination 單頁時不產生無用控制", () => {
    expect(pagination({ page: 1, totalPages: 1, total: 2 })).toBe("");
  });

  it("ConfirmDialog 需要原因時先阻止空白提交，再回傳原因", () => {
    const onConfirm = vi.fn();
    const instance = confirmDialog({ title: "危險操作", message: "請確認", requireReason: true, onConfirm });
    const confirm = instance.element.querySelector(".confirm-dialog-confirm");
    confirm.click();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(instance.element.querySelector(".confirm-dialog-error")).toBeVisible();
    const reason = instance.element.querySelector("textarea");
    reason.value = "已完成審核";
    confirm.click();
    expect(onConfirm).toHaveBeenCalledWith("已完成審核");
  });
});
