"use strict";

/* Incremental Admin Console surface.  Domain renderers that still live in
 * app.js use the same tabs and NovelApi contract; this module owns the new
 * overview read model and shared status primitives so the legacy SPA can be
 * migrated one domain at a time without a second API client. */
(function installAdminConsole(global) {
  const api = (path, options = {}) => global.NovelApi.request(path, options);
  const esc = (value) => global.NovelEscape.esc(value);
  let overviewRequest = 0;

  function metric(label, value, hint, action) {
    return `<button class="admin-overview-card" type="button" ${action ? `data-admin-overview-tab="${esc(action)}"` : ""}>
      <span class="admin-overview-value">${esc(value ?? 0)}</span><span class="admin-overview-label">${esc(label)}</span>${hint ? `<span class="admin-overview-hint">${esc(hint)}</span>` : ""}
    </button>`;
  }

  function summaryFromLegacy(legacy) {
    const source = legacy?.overview || legacy || {};
    const books = source.books || {};
    const users = source.users || {};
    const chapters = source.chapters || {};
    return {
      asOf: source.asOf || new Date().toISOString(),
      users: { total: users.total || 0, byRole: users.byRole || users.by_role || {} },
      books: { total: books.total || 0, byStatus: books.byStatus || books.by_status || {}, published: books.published || (books.by_status || {}).approved || 0 },
      governance: { pendingAuthorApplications: source.governance?.pendingAuthorApplications || 0, activeContentRequests: source.governance?.activeContentRequests || 0 },
      generation: { queuedAI: source.generation?.queuedAI || 0, queuedTTS: source.generation?.queuedTTS || 0, summary: source.generation?.summary || { providers: [], workers: [], staleWorkerCount: 0 } },
      alerts: source.alerts || [], allClear: !(source.alerts || []).length,
      chapters,
    };
  }

  async function loadOverview() {
    try {
      return summaryFromLegacy(await api("/api/admin/overview"));
    } catch (error) {
      // Older deployments may not have the additive endpoint yet.  The
      // fallback remains bounded and keeps a safe upgrade path.
      try { return summaryFromLegacy(await api("/api/admin/dashboard")); }
      catch (fallback) { throw error || fallback; }
    }
  }

  function bindActions(panel) {
    panel.querySelectorAll("[data-admin-overview-tab]").forEach((button) => button.addEventListener("click", () => {
      const tab = button.dataset.adminOverviewTab;
      const target = [...panel.ownerDocument.querySelectorAll("[data-atab]")].find((item) => item.dataset.atab === tab);
      if (target) target.click();
    }));
  }

  async function renderOverview(panel, tab = "overview") {
    if (!panel) return;
    const requestId = ++overviewRequest;
    panel.innerHTML = `<div class="admin-status-panel" role="status" aria-live="polite"><strong>載入後台概覽中…</strong><span>只會載入概覽所需的摘要資料。</span></div>`;
    let data;
    try { data = await loadOverview(); }
    catch (error) {
      if (requestId !== overviewRequest) return;
      panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>概覽暫時無法載入</strong><span>${esc(error?.message || "請稍後再試")}</span><button class="btn" id="admin-overview-retry" type="button">重試</button></div>`;
      panel.querySelector("#admin-overview-retry")?.addEventListener("click", () => renderOverview(panel, tab));
      return;
    }
    if (requestId !== overviewRequest) return;
    const roles = data.users?.byRole || {};
    const generation = data.generation || {};
    const summary = generation.summary || {};
    const providers = summary.providers || [];
    const workers = summary.workers || [];
    const alerts = data.alerts || [];
    panel.innerHTML = `<div class="admin-overview" data-admin-overview>
      <div class="admin-section-heading"><div><h2>後台概覽</h2><p class="arev-sub">資料時間：${esc(data.asOf || "—")} · 由 canonical DB 摘要推導</p></div><button class="btn btn-ghost" id="admin-overview-refresh" type="button">重新整理</button></div>
      <div class="admin-overview-grid">
        ${metric("帳號", data.users?.total || 0, `${roles.admin || 0} Admin · ${roles.reviewer || 0} Reviewer`, "users")}
        ${metric("已公開作品", data.books?.published || 0, `作品總數 ${data.books?.total || 0}`, "books")}
        ${metric("待處理作者申請", data.governance?.pendingAuthorApplications || 0, "Users & Roles", "authors")}
        ${metric("進行中的內容申請", data.governance?.activeContentRequests || 0, "Review workspace", "review")}
        ${metric("AI 排隊／運行", generation.queuedAI || 0, "AI Services / Generation", "generation")}
        ${metric("TTS 排隊／運行", generation.queuedTTS || 0, "TTS Services / Generation", "generation")}
        ${metric("Provider", providers.length, `${providers.filter((item) => item.enabled).length} 啟用`, "ai")}
        ${metric("活躍 Worker", workers.filter((item) => item.active).length, `${summary.staleWorkerCount || 0} 個 stale`, "generation")}
      </div>
      <section class="admin-overview-alerts" aria-labelledby="admin-alert-heading">
        <div class="admin-section-heading"><h3 id="admin-alert-heading">需要注意</h3><span class="arev-sub">${alerts.length ? `${alerts.length} 項` : "目前無需處理事項"}</span></div>
        ${alerts.length ? alerts.map((alert) => `<button class="admin-alert admin-alert-${esc(alert.severity || "info").toLowerCase()}" type="button" data-admin-overview-tab="${esc((alert.href || "").includes("services") ? "ai" : (alert.href || "").includes("review") ? "review" : "generation")}"><span class="admin-alert-severity">${esc(alert.severity || "info")}</span><span><strong>${esc(alert.label || "需要檢查")}</strong><small>${esc(alert.scope || "")} · ${esc(alert.count ?? 0)} 筆</small></span><span aria-hidden="true">→</span></button>`).join("") : `<div class="admin-status-panel admin-status-empty"><strong>目前沒有 critical alert</strong><span>Provider、Worker、Queue 與治理摘要都沒有需要立即處理的訊號。</span></div>`}
      </section>
      <section class="admin-overview-health"><div class="admin-section-heading"><h3>服務摘要</h3><span class="arev-sub">AI 與 TTS 分開觀察</span></div><div class="admin-health-list">${providers.length ? providers.map((provider) => `<div class="admin-health-row"><span><strong>${esc(provider.name || provider.label || "Provider")}</strong><small>${esc(provider.serviceType || "")} · ${provider.enabled ? "啟用" : "停用"} · ${esc(provider.healthState || "unknown")}</small></span><span>${esc(provider.activeCount ?? 0)} / ${esc(provider.maxConcurrency ?? 1)} 執行中 · ${esc(provider.queueCount ?? 0)} 排隊</span></div>`).join("") : `<div class="admin-status-panel admin-status-empty">尚未設定服務 Provider。</div>`}</div></section>
    </div>`;
    panel.querySelector("#admin-overview-refresh")?.addEventListener("click", () => renderOverview(panel, tab));
    bindActions(panel);
  }

  const announcementUi = { page: 1, q: "", status: "", audience: "", editing: null, request: 0 };
  const announcementStatus = { active: "啟用中", scheduled: "已排程", expired: "已過期", disabled: "已停用", archived: "已封存" };
  function announcementForm(item = null) {
    const value = item || { title: "", bodyText: "", audienceMode: "everyone", roles: [], displayMode: "once_per_version", priority: 0, startAt: "", endAt: "", ctaLabel: "", ctaTarget: "", enabled: false };
    const localTime = (raw) => raw ? String(raw).replace(/Z$/, "").slice(0, 16) : "";
    return `<section class="admin-announcement-editor" aria-labelledby="announcement-editor-title"><div class="admin-section-heading"><h2 id="announcement-editor-title">${item ? "編輯公告" : "建立公告"}</h2><button class="btn btn-ghost" id="announcement-cancel" type="button">取消</button></div>
      <div class="admin-announcement-form"><label class="field"><span>標題</span><input class="field-input" id="announcement-title" maxlength="200" value="${esc(value.title)}" required></label>
      <label class="field"><span>純文字內容</span><textarea class="field-input" id="announcement-body" maxlength="10000" rows="7" required>${esc(value.bodyText)}</textarea><small class="field-help">不支援 HTML、Markdown 或圖片。</small></label>
      <div class="admin-announcement-form-grid"><label class="field"><span>受眾</span><select class="field-input" id="announcement-audience"><option value="everyone" ${value.audienceMode === "everyone" ? "selected" : ""}>所有人</option><option value="guest" ${value.audienceMode === "guest" ? "selected" : ""}>訪客</option><option value="all_authenticated" ${value.audienceMode === "all_authenticated" ? "selected" : ""}>所有已登入帳號</option><option value="specific_roles" ${value.audienceMode === "specific_roles" ? "selected" : ""}>指定角色</option></select></label>
      <label class="field"><span>顯示模式</span><select class="field-input" id="announcement-display"><option value="once_per_version" ${value.displayMode === "once_per_version" ? "selected" : ""}>每個版本一次</option><option value="once_per_session" ${value.displayMode === "once_per_session" ? "selected" : ""}>每個工作階段一次</option></select></label>
      <label class="field"><span>優先序（-100 至 100）</span><input class="field-input" id="announcement-priority" type="number" min="-100" max="100" value="${Number(value.priority || 0)}"></label></div>
      <fieldset class="field announcement-roles" id="announcement-roles-field"><legend>指定角色</legend>${["reader", "author", "reviewer", "admin", "super_admin"].map((role) => `<label><input type="checkbox" data-announcement-role="${role}" ${(value.roles || []).includes(role) ? "checked" : ""}> ${role}</label>`).join("")}</fieldset>
      <div class="admin-announcement-form-grid"><label class="field"><span>開始時間（UTC）</span><input class="field-input" id="announcement-start" type="datetime-local" value="${localTime(value.startAt)}"></label><label class="field"><span>結束時間（UTC）</span><input class="field-input" id="announcement-end" type="datetime-local" value="${localTime(value.endAt)}"></label></div>
      <div class="admin-announcement-form-grid"><label class="field"><span>CTA 標籤（選填）</span><input class="field-input" id="announcement-cta-label" maxlength="80" value="${esc(value.ctaLabel || "")}"></label><label class="field"><span>CTA 連結（站內 hash 或 HTTPS）</span><input class="field-input" id="announcement-cta-target" maxlength="500" value="${esc(value.ctaTarget || "")}"></label></div>
      <label class="announcement-enabled"><input type="checkbox" id="announcement-enabled" ${value.enabled ? "checked" : ""}> 建立／編輯後啟用</label>
      <div class="announcement-preview" id="announcement-preview" aria-live="polite"><strong>預覽</strong><span>填寫後可預覽純文字公告。</span></div>
      <div class="modal-actions"><button class="btn btn-ghost" id="announcement-preview-btn" type="button">更新預覽</button><button class="btn btn-accent" id="announcement-save" type="button">${item ? "儲存變更" : "建立公告"}</button></div></div></section>`;
  }
  function renderAnnouncementPreview(panel) {
    const preview = panel.querySelector("#announcement-preview"); if (!preview) return;
    preview.replaceChildren();
    const heading = document.createElement("strong"); heading.textContent = "預覽";
    const title = document.createElement("span"); title.className = "announcement-preview-title"; title.textContent = panel.querySelector("#announcement-title")?.value || "（尚無標題）";
    const body = document.createElement("pre"); body.className = "announcement-preview-body"; body.textContent = panel.querySelector("#announcement-body")?.value || "（尚無內容）";
    preview.append(heading, title, body);
  }
  function announcementPayload(panel) {
    const iso = (id) => { const value = panel.querySelector(id)?.value; return value ? new Date(`${value}:00Z`).toISOString() : null; };
    return { title: panel.querySelector("#announcement-title").value, bodyText: panel.querySelector("#announcement-body").value, audienceMode: panel.querySelector("#announcement-audience").value, roles: [...panel.querySelectorAll("[data-announcement-role]:checked")].map((node) => node.dataset.announcementRole), displayMode: panel.querySelector("#announcement-display").value, priority: Number(panel.querySelector("#announcement-priority").value || 0), startAt: iso("#announcement-start"), endAt: iso("#announcement-end"), ctaLabel: panel.querySelector("#announcement-cta-label").value, ctaTarget: panel.querySelector("#announcement-cta-target").value, enabled: panel.querySelector("#announcement-enabled").checked };
  }
  async function renderAnnouncements(panel, tab = "announcements") {
    if (!panel) return;
    const requestId = ++announcementUi.request;
    if (announcementUi.editing !== null) {
      panel.innerHTML = announcementForm(announcementUi.editing || null);
      bindAnnouncementForm(panel, tab);
      return;
    }
    const qs = new URLSearchParams({ page: announcementUi.page, page_size: 20, sort: "updated_at", order: "desc" });
    if (announcementUi.q) qs.set("q", announcementUi.q); if (announcementUi.status) qs.set("status", announcementUi.status); if (announcementUi.audience) qs.set("audience", announcementUi.audience);
    panel.innerHTML = `<div class="admin-status-panel" role="status"><strong>載入公告清單中…</strong></div>`;
    let data;
    try { data = await api(`/api/admin/announcements?${qs}`); } catch (error) { if (requestId !== announcementUi.request) return; panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>公告載入失敗</strong><span>${esc(error?.message || "請稍後再試")}</span><button class="btn" id="announcement-retry" type="button">重試</button></div>`; panel.querySelector("#announcement-retry")?.addEventListener("click", () => renderAnnouncements(panel, tab)); return; }
    if (requestId !== announcementUi.request) return;
    const rows = (data.items || []).map((item) => `<div class="arev-row admin-announcement-row"><div class="arev-info"><strong>${esc(item.title)}</strong><span class="chip status-chip ${esc(item.status)}">${announcementStatus[item.status] || esc(item.status)}</span><span class="arev-sub">受眾：${esc(item.audienceMode)} · ${esc(item.displayMode)} · priority ${esc(item.priority)} · display v${esc(item.displayVersion)} · config v${esc(item.configVersion)}</span><span class="arev-sub">${esc(item.startAt || "立即")} → ${esc(item.endAt || "不限")} · 更新 ${esc(item.updatedAt || "—")}</span></div><div class="arev-actions"><button class="btn" data-announcement-edit="${item.id}" type="button">編輯</button>${item.status === "archived" ? "" : `<button class="btn btn-ghost" data-announcement-${item.enabled ? "disable" : "enable"}="${item.id}" type="button">${item.enabled ? "停用" : "啟用"}</button><button class="btn btn-ghost" data-announcement-reannounce="${item.id}" type="button">重新發布</button><button class="btn btn-danger" data-announcement-archive="${item.id}" type="button">封存</button>`}</div></div>`).join("");
    const totalPages = data.totalPages || data.total_pages || 1;
    panel.innerHTML = `<div class="admin-section-heading"><div><h2>平台公告</h2><p class="arev-sub">公告與首頁輪播、通知中心分開管理；內容只支援純文字。</p></div><button class="btn btn-accent" id="announcement-new" type="button">＋ 建立公告</button></div><div class="arev-row cat-add review-filters"><div class="arev-info"><input class="field-input" id="announcement-q" placeholder="搜尋標題或內容" value="${esc(announcementUi.q)}"><select class="field-input" id="announcement-status"><option value="">全部狀態</option>${Object.entries(announcementStatus).map(([v, l]) => `<option value="${v}" ${announcementUi.status === v ? "selected" : ""}>${l}</option>`).join("")}</select><select class="field-input" id="announcement-audience-filter"><option value="">全部受眾</option><option value="everyone" ${announcementUi.audience === "everyone" ? "selected" : ""}>所有人</option><option value="guest" ${announcementUi.audience === "guest" ? "selected" : ""}>訪客</option><option value="all_authenticated" ${announcementUi.audience === "all_authenticated" ? "selected" : ""}>已登入</option><option value="specific_roles" ${announcementUi.audience === "specific_roles" ? "selected" : ""}>指定角色</option></select><button class="btn" id="announcement-filter" type="button">套用</button></div></div>${rows || `<div class="admin-status-panel"><strong>目前沒有公告</strong><span>建立後預設可先停用並透過預覽檢查。</span></div>`}<div class="arev-pager"><button class="btn btn-ghost" data-announcement-page="${announcementUi.page - 1}" ${announcementUi.page <= 1 ? "disabled" : ""}>‹ 上一頁</button><span class="pager-info">第 ${announcementUi.page} / ${totalPages} 頁 · 共 ${data.total || 0} 筆</span><button class="btn btn-ghost" data-announcement-page="${announcementUi.page + 1}" ${announcementUi.page >= totalPages ? "disabled" : ""}>下一頁 ›</button></div>`;
    panel.querySelector("#announcement-new")?.addEventListener("click", () => { announcementUi.editing = false; renderAnnouncements(panel, tab); });
    panel.querySelector("#announcement-filter")?.addEventListener("click", () => { announcementUi.q = panel.querySelector("#announcement-q").value.trim(); announcementUi.status = panel.querySelector("#announcement-status").value; announcementUi.audience = panel.querySelector("#announcement-audience-filter").value; announcementUi.page = 1; renderAnnouncements(panel, tab); });
    panel.querySelectorAll("[data-announcement-page]").forEach((button) => button.addEventListener("click", () => { const page = Number(button.dataset.announcementPage); if (page >= 1 && page <= totalPages) { announcementUi.page = page; renderAnnouncements(panel, tab); } }));
    panel.querySelectorAll("[data-announcement-edit]").forEach((button) => button.addEventListener("click", async () => { try { announcementUi.editing = await api(`/api/admin/announcements/${button.dataset.announcementEdit}`); renderAnnouncements(panel, tab); } catch (error) { global.NovelToast?.toast(error.message, "err"); } }));
    [["enable", "啟用"], ["disable", "停用"], ["reannounce", "重新發布"], ["archive", "封存"]].forEach(([action, label]) => panel.querySelectorAll(`[data-announcement-${action}]`).forEach((button) => button.addEventListener("click", async () => { if (action === "archive" && !global.confirm(`確定封存「${button.closest(".arev-row")?.querySelector("strong")?.textContent || "此公告"}」？`)) return; button.disabled = true; try { await api(`/api/admin/announcements/${button.dataset[`announcement${action[0].toUpperCase()}${action.slice(1)}`]}/${action}`, { method: "POST" }); global.NovelToast?.toast(`${label}完成`, "ok"); renderAnnouncements(panel, tab); } catch (error) { button.disabled = false; global.NovelToast?.toast(`${label}失敗：${error.message}`, "err"); } })));
  }
  function bindAnnouncementForm(panel, tab) {
    panel.querySelector("#announcement-cancel")?.addEventListener("click", () => { announcementUi.editing = null; renderAnnouncements(panel, tab); });
    panel.querySelector("#announcement-preview-btn")?.addEventListener("click", () => renderAnnouncementPreview(panel));
    panel.querySelector("#announcement-save")?.addEventListener("click", async () => { const button = panel.querySelector("#announcement-save"); button.disabled = true; try { const payload = announcementPayload(panel); let item; if (announcementUi.editing) { payload.expectedConfigVersion = announcementUi.editing.configVersion; item = await api(`/api/admin/announcements/${announcementUi.editing.id}`, { method: "PUT", body: payload }); } else item = await api("/api/admin/announcements", { method: "POST", body: payload }); global.NovelToast?.toast(announcementUi.editing ? "公告已更新" : "公告已建立", "ok"); announcementUi.editing = null; renderAnnouncements(panel, tab); } catch (error) { button.disabled = false; global.NovelToast?.toast(`儲存失敗：${error.message}`, "err"); } });
    renderAnnouncementPreview(panel);
  }

  global.StoryLingoAdminConsole = { renderOverview, renderAnnouncements, paging: { defaultSize: 20, maxSize: 100 } };
})(window);
