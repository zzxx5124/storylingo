"use strict";
/* 統一 API Service（FE-005）。
 * 集中處理 fetch、CSRF header、credentials、JSON 序列化、錯誤映射、AbortController
 * 與「dev 模式 mock adapter」。所有新頁面一律透過 NovelApi.request 呼叫，不直接 fetch。
 *
 * 對外介面：
 *   NovelApi.get(path, options)
 *   NovelApi.post(path, body, options)
 *   NovelApi.put(path, body, options)
 *   NovelApi.del(path, options)
 *   NovelApi.request(path, options)   // 低階：{ method, headers, body, signal }
 *   NovelApi.setMock(adapterFn)        // dev：adapterFn(path, options) 回傳 Promise<data> 或擲錯
 */
(function () {
  const COOKIE = "novel_csrf";

  function readCsrf() {
    const item = document.cookie.split("; ").find((part) => part.startsWith(`${COOKIE}=`));
    return item ? decodeURIComponent(item.slice(COOKIE.length + 1)) : null;
  }

  class ApiError extends Error {
    constructor(message, status, data) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.data = data;
    }
  }

  const STATUS_MESSAGES = {
    400: "請求資料不正確",
    401: "請先登入",
    403: "你沒有權限執行此操作",
    404: "找不到此項目",
    409: "資料衝突，無法完成操作",
    422: "提交的資料格式有誤",
    429: "操作太頻繁，請稍後再試",
  };

  function makeMessage(status, data) {
    if (data && (data.detail || data.message)) {
      const detail = data.detail || data.message;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        const first = detail[0];
        return first && first.msg ? first.msg : String(first || "");
      }
      return JSON.stringify(detail);
    }
    return STATUS_MESSAGES[status] || `伺服器回應錯誤（HTTP ${status}）`;
  }

  async function request(path, options = {}) {
    if (_mockFn && !options.noMock) return _mockFn(path, options);

    const headers = { ...(options.headers || {}) };
    const csrf = readCsrf();
    if (csrf && !headers["X-CSRF-Token"]) headers["X-CSRF-Token"] = csrf;
    let body = options.body;
    if (body != null && typeof body === "object" && !(body instanceof FormData) &&
        !(typeof Blob !== "undefined" && body instanceof Blob) &&
        !(ArrayBuffer.isView(body))) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(path, { ...options, headers, body, credentials: "include", signal: options.signal });
    } catch (error) {
      if (error && (error.name === "AbortError" || /abort/i.test(String(error.name)))) {
        throw Object.assign(new Error("請求已取消"), { name: "CanceledError", status: 0 });
      }
      throw new ApiError("網路連線失敗，請檢查連線後重試", 0, null);
    }

    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("json") ? await response.json().catch(() => null) : null;
    if (!response.ok) {
      throw new ApiError(makeMessage(response.status, data), response.status, data);
    }
    if (options.responseType === "blob") return response.blob();
    if (response.status === 204) return null;
    return data || null;
  }

  function normalizeBody(body, options) {
    if (methodNeedsBody(options.method) && body !== undefined) {
      if (body instanceof FormData) return { ...options, body };
      if (body instanceof Blob || ArrayBuffer.isView(body)) return { ...options, body };
      return { ...options, body: body == null ? undefined : body };
    }
    return { ...options, body: undefined };
  }

  function methodNeedsBody(method) {
    return method === "POST" || method === "PUT" || method === "PATCH";
  }

  let _mockFn = null;

  const NovelApi = {
    get: (path, options = {}) => request(path, { ...options, method: "GET" }),
    post: (path, body, options = {}) => request(path, normalizeBody(body, { ...options, method: "POST" })),
    put: (path, body, options = {}) => request(path, normalizeBody(body, { ...options, method: "PUT" })),
    del: (path, options = {}) => request(path, { ...options, method: "DELETE" }),
    request,
    setMock: (fn) => { _mockFn = fn; },
    ApiError,
  };

  window.NovelApi = NovelApi;
})();
