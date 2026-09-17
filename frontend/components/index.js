"use strict";
/* 共用元件入口（FE-006）。
 * 聚合 Button / Field / Modal / Toast / BookCard，
 * 並在 window 上掛載 NovelUI 供既有的非 module 頁面（app.js / platform.js）使用。
 * 新的 module 頁面可直接 import { button, input, modal, toast, bookCard }。
 */
import button from "./Button.js";
import field, { input, select, textarea } from "./Field.js";
import modal, { closeModal } from "./Modal.js";
import toast from "./Toast.js";
import bookCard from "./BookCard.js";
import iconButton from "./IconButton.js";
import confirmDialog from "./ConfirmDialog.js";
import statusBadge from "./StatusBadge.js";
import statusState from "./StatusState.js";
import pageHeader from "./PageHeader.js";
import filterBar from "./FilterBar.js";
import pagination from "./Pagination.js";

const NovelUI = {
  button,
  input,
  select,
  textarea,
  field,
  modal,
  closeModal,
  toast,
  bookCard,
  iconButton,
  confirmDialog,
  statusBadge,
  statusState,
  pageHeader,
  filterBar,
  pagination,
};

if (typeof window !== "undefined") {
  window.NovelUI = { ...(window.NovelUI || {}), ...NovelUI };
}

export { button, input, select, textarea, field, modal, closeModal, toast, bookCard, iconButton, confirmDialog, statusBadge, statusState, pageHeader, filterBar, pagination };
export default NovelUI;
