// Thin fetch wrapper for the Flask backend. Same-origin, no base URL needed.
(function () {
  "use strict";

  async function req(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok) {
      const msg = (data && data.error) || `${method} ${path} -> HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  window.Api = {
    getStatus: () => req("GET", "/api/status"),
    getInventory: () => req("GET", "/api/inventory"),
    syncInventory: (items) => req("POST", "/api/inventory/sync", { items }),
    adjustItem: (id, quantity) => req("PATCH", `/api/inventory/${id}`, { quantity }),
    deleteItem: (id) => req("DELETE", `/api/inventory/${id}`),
    clearInventory: () => req("DELETE", "/api/inventory"),
    browse: (path) => req("GET", `/api/browse${path ? "?path=" + encodeURIComponent(path) : ""}`),

    getSettings: () => req("GET", "/api/settings"),
    postSettings: (payload) => req("POST", "/api/settings", payload),
    resetSettings: () => req("POST", "/api/settings/reset"),
    clearDatabase: () => req("POST", "/api/database/clear"),
    getModels: () => req("GET", "/api/models"),

    getHistory: (params) => {
      const qs = new URLSearchParams(params || {}).toString();
      return req("GET", `/api/history${qs ? "?" + qs : ""}`);
    },

    getLowStock: () => req("GET", "/api/analytics/low-stock"),
    getUsageFrequency: () => req("GET", "/api/analytics/usage-frequency"),
    getCategoryBreakdown: () => req("GET", "/api/analytics/category-breakdown"),
    getPerformance: (sessionId) =>
      req("GET", `/api/analytics/performance${sessionId ? "?session_id=" + sessionId : ""}`),
    getStockTrend: (itemId) =>
      req("GET", `/api/analytics/stock-trend${itemId ? "?item_id=" + itemId : ""}`),

    getRecipes: () => req("GET", "/api/recipes"),
    generateRecipes: () => req("POST", "/api/recipes/generate"),
    getProjection: (rank) => req("GET", `/api/recipes/${rank}/projection`),
    confirmCook: (rank) => req("POST", `/api/recipes/${rank}/confirm`),
    getInstructions: (rank) => req("GET", `/api/recipes/${rank}/instructions`),

    startCamera: () => req("POST", "/api/camera/start"),
    stopCamera: () => req("POST", "/api/camera/stop"),

    minimizeApp: () => req("POST", "/api/app/minimize"),
    closeApp: () => req("POST", "/api/app/close"),
  };
})();
