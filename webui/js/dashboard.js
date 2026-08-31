(function () {
  "use strict";

  const STORAGE_KEY = "webui.dashboard.layout.v8";
  const LOCK_KEY = "webui.dashboard.locked";
  const ROWS = 80;
  const GRID_MARGIN = 0;

  // Known food classes (labels/coco1.txt) mapped to a display icon + a
  // sensible inventory category, so detected items land in the right
  // Refrigerator Inventory bucket without needing a backend taxonomy call.
  // Icon values are filenames under webui/vendor/icons/ (bundled SVGs, see
  // iconImg()) rather than Unicode emoji, which depend on a system emoji
  // font that isn't guaranteed to be installed.
  const ICONS = {
    Tomato: "tomato", Onion: "onion", Garlic: "garlic", Carrot: "carrot", Eggplant: "eggplant",
    Okra: "okra", Lettuce: "lettuce", Potato: "potato", "Young Corn": "young-corn",
    "Bell Pepper": "bell-pepper", Chilli: "chilli", "Green Chilli": "chilli", Calamansi: "calamansi",
    Santol: "santol", Banana: "banana", "Banana Blossom": "banana-blossom",
    Chicken: "chicken", Beef: "beef", Pork: "pork", Egg: "egg",
  };
  const CATEGORIES = {
    Tomato: "Vegetables", Onion: "Vegetables", Garlic: "Vegetables", Carrot: "Vegetables",
    Eggplant: "Vegetables", Okra: "Vegetables", Lettuce: "Vegetables", Potato: "Vegetables",
    "Young Corn": "Vegetables", "Bell Pepper": "Vegetables", Chilli: "Vegetables",
    "Green Chilli": "Vegetables", Calamansi: "Vegetables", Santol: "Vegetables",
    Banana: "Vegetables", "Banana Blossom": "Vegetables",
    Chicken: "Meat/Poultry", Beef: "Meat/Poultry", Pork: "Meat/Poultry", Egg: "Meat/Poultry",
  };
  const CATEGORY_ICONS = { "Vegetables": "vegetables", "Meat/Poultry": "chicken", "Seasonings": "seasonings", "Other": "other" };
  const RECIPE_ICONS = { meat: "chicken", vegetable: "lettuce", seafood: "seafood", egg: "egg" };

  function iconFor(name) { return ICONS[name] || "other"; }
  function categoryFor(name) { return CATEGORIES[name] || "Other"; }
  function iconImg(file, alt, className) {
    return h("img", { class: className || "icon-img", src: `vendor/icons/${file}.svg`, alt: alt || "" });
  }

  function h(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") el.className = v;
      else if (k === "onclick") el.addEventListener("click", v);
      else if (k === "style") el.style.cssText = v;
      else el.setAttribute(k, v);
    }
    for (const child of [].concat(children)) {
      if (child == null) continue;
      el.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return el;
  }

  // ============ GridStack init + layout persistence ============
  function computeCellHeight() {
    const wrap = document.querySelector(".grid-wrap");
    const totalMargin = GRID_MARGIN * (ROWS + 1);
    const height = (wrap.clientHeight - totalMargin) / ROWS;
    // Floor is just a divide-by-near-zero guard, not a real minimum — with
    // ROWS=80 (see Step 1) a 20px/row floor forces a 1600px-tall grid on any
    // normal screen, pushing everything below the fold off-screen with no
    // scrollbar to recover it (this was the actual cause of "list view /
    // button lost" reports all session — confirmed via CDP: every panel was
    // rendering ~2.5x taller than the viewport).
    return Math.max(2, Math.floor(height));
  }

  // Mirrors the gs-min-w/gs-min-h floors set in index.html. A saved layout
  // that violates these (e.g. from a drag/resize interaction that predates
  // the floors being added, or any other corruption) gets discarded rather
  // than applied — better to fall back to the known-good HTML defaults than
  // silently render a panel with its content squeezed out of existence.
  const PANEL_MINS = {
    camera: { minW: 30, minH: 20 },
    detected: { minW: 25, minH: 25 },
    panel2: { minW: 15, minH: 15 },
    panel3: { minW: 15, minH: 22 },
    panel4: { minW: 15, minH: 15 },
    cookpanel: { minW: 15, minH: 22 },
  };

  function isSaneLayout(layout) {
    if (!Array.isArray(layout) || layout.length !== Object.keys(PANEL_MINS).length) return false;
    return layout.every((item) => {
      const mins = PANEL_MINS[item.id];
      return mins && item.w >= mins.minW && item.h >= mins.minH;
    });
  }

  function getSavedLayout() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const layout = JSON.parse(raw);
      if (!isSaneLayout(layout)) {
        console.warn("[layout] saved layout failed sanity check, ignoring and using defaults", layout);
        return null;
      }
      return layout;
    } catch (e) {
      return null;
    }
  }

  let grid = null;
  let DEFAULT_LAYOUT = null;

  function persistLayout() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(grid.save(false)));
    } catch (e) {
      console.warn("[layout] save failed", e);
    }
  }

  function resetLayoutNow() {
    if (!grid || !DEFAULT_LAYOUT) return;
    grid.load(DEFAULT_LAYOUT);
    persistLayout();
  }

  // Locking is separate from layout persistence — "Save Current State"
  // both saves the layout and locks it in (no more drag/resize) until
  // "Reset to Defaults" explicitly unlocks it again. Persisted so a locked
  // layout stays locked across an app restart, not just for this session.
  function lockLayout() {
    if (!grid) return;
    grid.setStatic(true);
    try {
      localStorage.setItem(LOCK_KEY, "1");
    } catch (e) {
      console.warn("[layout] lock persist failed", e);
    }
  }

  function unlockLayout() {
    if (!grid) return;
    grid.setStatic(false);
    try {
      localStorage.removeItem(LOCK_KEY);
    } catch (e) {
      console.warn("[layout] unlock persist failed", e);
    }
  }

  function initGrid() {
    grid = GridStack.init(
      {
        column: 145,
        cellHeight: computeCellHeight(),
        float: true,
        margin: GRID_MARGIN,
        disableOneColumnMode: true,
        resizable: { handles: "n, ne, e, se, s, sw, w, nw" },
        draggable: { handle: ".panel-drag-handle" },
      },
      "#dashboardGrid"
    );

    // Captured before any saved layout is applied, so it always reflects
    // the HTML-authored defaults — used by "Reset to Defaults" in Settings.
    DEFAULT_LAYOUT = grid.save(false);

    const saved = getSavedLayout();
    if (saved && saved.length) grid.load(saved);

    if (localStorage.getItem(LOCK_KEY) === "1") {
      grid.setStatic(true);
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        grid.cellHeight(computeCellHeight());
      }, 150);
    });

    // Auto-save on every layout change (drag/resize/add/remove), debounced
    // since "change" can fire in rapid bursts mid-drag.
    let saveTimer = null;
    grid.on("change", () => {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(persistLayout, 400);
    });
  }

  // ============ Sidebar: real system status ============
  const STATUS_ROWS = [
    { key: "camera_connected", icon: "camera-status", label: "Camera" },
    { key: "yolo_active", icon: "robot", label: "Yolov11" },
    { key: "gpt_synced", icon: "brain", label: "ChatGPT" },
  ];

  function renderSidebarShell() {
    const list = document.getElementById("statusList");
    list.innerHTML = "";
    STATUS_ROWS.forEach((row) => {
      list.appendChild(
        h("div", { class: "status-row" }, [
          h("span", { class: "status-icon" }, iconImg(row.icon, row.label)),
          h("span", { class: "status-label" }, row.label),
          h("span", { class: "status-dot", "data-status": row.key, "data-state": "bad" }),
        ])
      );
    });
  }

  // Start Camera connection light: red while pressed/connecting/lost, green
  // once the backend confirms camera_connected, off (default nav style)
  // when the camera isn't running at all.
  function setStartCameraConn(connState) {
    const navItem = document.querySelector('.nav-item[data-nav="start-camera"]');
    if (!navItem) return;
    navItem.classList.toggle("conn-connecting", connState === "connecting");
    navItem.classList.toggle("conn-connected", connState === "connected");
  }

  function applyStatus(status) {
    STATUS_ROWS.forEach((row) => {
      const dot = document.querySelector(`.status-dot[data-status="${row.key}"]`);
      if (dot) dot.dataset.state = status[row.key] ? "ok" : "bad";
    });
    const startBtn = document.querySelector('.nav-item[data-nav="start-camera"] .nav-label');
    if (startBtn) startBtn.textContent = status.camera_connected ? "Stop Camera" : "Start Camera";
    if (state.cameraRunning) {
      // "Connected" only when the camera link AND inference are both
      // actually healthy — camera_connected alone doesn't mean detections
      // are still being produced (a stalled Coral connection can leave the
      // feed running with the model silently stuck). A stall reuses the
      // same red "not really working" light as still-connecting.
      const healthy = status.camera_connected && !status.inference_stalled && !status.camera_degraded;
      setStartCameraConn(healthy ? "connected" : "connecting");
    } else {
      setStartCameraConn(null);
    }
  }

  // ============ Panel 1: Food Detection ============
  const state = {
    detections: new Map(), // name -> {icon,name,confidence,qty,touched}
    selectedRank: null,
    recipes: [],
    cameraRunning: false,
  };

  function redrawDetections() {
    const list = document.getElementById("detectedList");
    list.innerHTML = "";
    const rows = Array.from(state.detections.values());
    rows.forEach((det) => {
      list.appendChild(
        h("div", { class: "detected-row", "data-id": det.name }, [
          h("span", { class: "detected-icon" }, iconImg(iconFor(det.name), det.name)),
          h("span", { class: "detected-name" }, det.name),
          h("div", { class: "stepper" }, [
            h("button", { class: "stepper-btn", onclick: () => adjustQty(det.name, -1) }, "−"),
            h("span", { class: "stepper-count", "data-count-for": det.name }, String(det.qty)),
            h("button", { class: "stepper-btn", onclick: () => adjustQty(det.name, 1) }, "+"),
          ]),
        ])
      );
    });
    document.getElementById("detectedCount").textContent = String(rows.length);
  }

  function adjustQty(name, delta) {
    const det = state.detections.get(name);
    if (!det) return;
    det.qty = Math.max(0, det.qty + delta);
    det.touched = true;
    const countEl = document.querySelector(`[data-count-for="${name}"]`);
    if (countEl) countEl.textContent = String(det.qty);
    console.log(`[stepper] ${name} -> ${det.qty}`);
  }

  function applyDetectionUpdate(payload) {
    const seen = new Set();
    (payload.items || []).forEach((item) => {
      seen.add(item.name);
      const existing = state.detections.get(item.name);
      if (existing && existing.touched) {
        existing.confidence = item.confidence; // keep user's staged qty
      } else {
        state.detections.set(item.name, {
          name: item.name, confidence: item.confidence, qty: item.count, touched: false,
        });
      }
    });
    // Drop untouched rows no longer seen; keep touched ("staged") rows regardless.
    for (const [name, det] of Array.from(state.detections.entries())) {
      if (!seen.has(name) && !det.touched) state.detections.delete(name);
    }
    redrawDetections();

    // "Saw nothing" and "saw something too faint to count" both show as an
    // empty list otherwise — make the latter visible instead of looking
    // like a broken camera/Coral (this is exactly what a too-high
    // confidence setting looked like before this hint existed).
    const nearMissEl = document.getElementById("detectedNearMiss");
    if (nearMissEl) {
      if (payload.total_count === 0 && payload.near_miss_confidence != null) {
        nearMissEl.textContent =
          `Something's there but below your confidence threshold (~${payload.near_miss_confidence}%) — lower it in Settings if this keeps happening.`;
        nearMissEl.style.display = "";
      } else {
        nearMissEl.style.display = "none";
      }
    }
  }

  // ============ Panel 2: Refrigerator Inventory ============
  function renderInventory(data) {
    const body = document.getElementById("inventoryBody");
    body.innerHTML = "";
    const categories = (data && data.categories) || {};
    const names = Object.keys(categories);
    if (names.length === 0) {
      body.appendChild(h("div", { class: "detail-empty" }, "No inventory yet — start the camera and save detections."));
      return;
    }
    names.forEach((cat) => {
      const items = categories[cat];
      const lowStock = items.some((it) => it.quantity <= it.low_stock_threshold && it.low_stock_threshold > 0);
      const titleChildren = [iconImg(CATEGORY_ICONS[cat] || "other", cat), " " + cat + ":"];
      if (lowStock) titleChildren.push(h("span", { class: "low-stock-tag" }, [iconImg("low-stock", "Low stock"), " Low Stock"]));
      body.appendChild(
        h("div", { class: "inv-category" }, [
          h("div", { class: "inv-category-title" }, titleChildren),
          h(
            "div", { class: "chip-row" },
            items.map((it) => h("span", { class: "chip" }, `${it.name} (${it.quantity} ${it.unit})`))
          ),
        ])
      );
    });
  }

  // ============ Panel 3: AI Recipe Recommendations ============
  function renderRecipeCard(recipe) {
    const readyNames = (recipe.ready_ingredients || []).map((i) => i.name);
    const lines = [h("div", { class: "recipe-line" }, "Ready: " + (readyNames.join(", ") || "—"))];
    if (recipe.missing_ingredients && recipe.missing_ingredients.length) {
      lines.push(
        h("div", { class: "recipe-line" }, [
          "Missing: ",
          h("span", { class: "missing" }, recipe.missing_ingredients.join(", ")),
        ])
      );
    }
    const card = h(
      "div",
      { class: "recipe-card" + (state.selectedRank === recipe.rank ? " selected" : ""), "data-rank": recipe.rank },
      [
        h("div", { class: "recipe-title" }, [
          iconImg(RECIPE_ICONS[recipe.category] || "other", recipe.category),
          ` ${recipe.name} (${recipe.match_percentage}% Match):`,
        ]),
        ...lines,
      ]
    );
    card.addEventListener("click", () => selectRecipe(recipe.rank));
    return card;
  }

  function renderRecipes(batch) {
    state.recipes = (batch && batch.recipes) || [];
    const list = document.getElementById("recipeList");
    list.innerHTML = "";
    if (state.recipes.length === 0) {
      list.appendChild(h("div", { class: "detail-empty" }, "No recipes yet — save some detected items to inventory first."));
      return;
    }
    state.recipes
      .slice()
      .sort((a, b) => a.rank - b.rank)
      .forEach((r) => list.appendChild(renderRecipeCard(r)));
  }

  async function selectRecipe(rank) {
    state.selectedRank = rank;
    document.querySelectorAll(".recipe-card").forEach((c) => {
      c.classList.toggle("selected", Number(c.dataset.rank) === rank);
    });
    try {
      const proj = await Api.getProjection(rank);
      renderProjection(proj);
    } catch (e) {
      console.warn("[recipe] projection failed", e);
    }
    renderCookInstructions(rank);
  }

  // ============ Panel 6: How to Cook ============
  async function renderCookInstructions(rank) {
    const body = document.getElementById("cookBody");
    body.innerHTML = "";
    body.appendChild(h("div", { class: "detail-empty" }, "Loading instructions…"));
    try {
      const result = await Api.getInstructions(rank);
      body.innerHTML = "";
      const steps = result.steps || [];
      if (steps.length === 0) {
        body.appendChild(h("div", { class: "detail-empty" }, "No instructions available."));
        return;
      }
      body.appendChild(h("div", { class: "form-section-title" }, result.recipe_name || ""));
      const list = h("ol", { class: "cook-steps" });
      steps.forEach((step) => list.appendChild(h("li", {}, step)));
      body.appendChild(list);
    } catch (e) {
      body.innerHTML = "";
      body.appendChild(h("div", { class: "detail-empty" }, `Failed to load instructions: ${e.message}`));
    }
  }

  // ============ Panel 4: Pre-Cook Analytics Projection ============
  function renderProjection(proj) {
    const body = document.getElementById("analyticsBody");
    let box = body.querySelector(".selected-recipe-box");
    if (!box) return refreshAnalytics(); // panel not built yet
    box.querySelector(".recipe-name").textContent = proj.recipe_name;

    const rowsWrap = body.querySelector(".stock-rows");
    rowsWrap.innerHTML = "";
    (proj.projected_stock || []).forEach((row) => {
      rowsWrap.appendChild(
        h("div", { class: "stock-row" }, [
          h("span", { class: "name" }, row.name + ":"),
          `${row.current}${row.unit} `,
          h("span", { class: "arrow" }, "→"),
          h("span", { class: "after" }, `${row.projected}${row.unit}`),
        ])
      );
    });
  }

  function sparklinePoints(trend) {
    if (!trend || trend.length === 0) return "0,20 100,20";
    const qtys = trend.map((r) => r.quantity || 0);
    const min = Math.min(...qtys), max = Math.max(...qtys);
    const range = max - min || 1;
    return trend
      .map((r, i) => {
        const x = (i / Math.max(1, trend.length - 1)) * 100;
        const y = 34 - ((r.quantity - min) / range) * 30;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }

  async function refreshAnalytics() {
    const body = document.getElementById("analyticsBody");
    body.innerHTML = "";

    body.appendChild(
      h("div", { class: "selected-recipe-box" }, [
        "Selected Recipe:",
        h("span", { class: "recipe-name" }, "—"),
      ])
    );
    body.appendChild(h("div", { class: "stock-section-title" }, "PROJECTED STOCK:"));
    body.appendChild(h("div", { class: "stock-rows" }));

    // Fire both concurrently instead of sequentially (they're independent);
    // Promise.allSettled preserves the previous per-call best-effort
    // semantics — one endpoint failing doesn't blank out the other.
    const [alertsResult, trendResult] = await Promise.allSettled([
      Api.getLowStock(),
      Api.getStockTrend(),
    ]);
    const alerts = alertsResult.status === "fulfilled" ? (alertsResult.value.alerts || []) : [];
    const trend = trendResult.status === "fulfilled" ? (trendResult.value.trend || []) : [];

    body.appendChild(
      h("div", { class: "alerts-section" }, [
        h("div", { class: "stock-section-title" }, "Projected Alerts:"),
        ...(alerts.length
          ? alerts.map((a) => h("div", { class: "alert-row" },
              [iconImg("low-stock", "Low stock"), ` Low Stock: ${a.name} (${a.quantity}${a.unit} left)`]))
          : [h("div", { class: "detail-empty" }, "No low-stock alerts")]),
      ])
    );

    body.appendChild(h("div", { class: "stock-section-title", style: "margin-top:6px" }, "STOCK TREND (7-Day)"));
    const svgWrap = h("div", { class: "sparkline-wrap" });
    svgWrap.innerHTML = `<svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true">
      <polyline fill="none" stroke="var(--color-green)" stroke-width="2.5" points="${sparklinePoints(trend)}" />
    </svg>`;
    body.appendChild(svgWrap);

    if (state.selectedRank != null) {
      try {
        renderProjection(await Api.getProjection(state.selectedRank));
      } catch (e) { /* keep placeholder */ }
    }
  }

  // ============ Nav bar + action buttons ============
  function wireNav() {
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const nav = btn.dataset.nav;
        if (nav === "start-camera") {
          toggleCamera();
          return;
        }
        window.NavModals && window.NavModals.open(nav);
      });
    });
  }

  function reconnectFeed() {
    const feed = document.getElementById("cameraFeed");
    if (!state.cameraRunning) return;
    // The MJPEG stream connection dies if the backend process restarts
    // (e.g. after a Coral USB reset) — the <img> otherwise stays blank
    // forever since nothing else re-requests it. Retry with backoff,
    // bailing out once the backend actually confirms the camera is up.
    clearTimeout(state.feedRetryTimer);
    state.feedRetryTimer = setTimeout(async () => {
      if (!state.cameraRunning) return;
      try {
        const status = await Api.getStatus();
        if (status.camera_connected) {
          feed.src = "/video_feed?t=" + Date.now();
          setStartCameraConn("connected");
        } else {
          setStartCameraConn("connecting");
          reconnectFeed();
        }
      } catch (e) {
        setStartCameraConn("connecting");
        reconnectFeed();
      }
    }, 2000);
  }

  function startFeedWatchdog() {
    // Belt-and-suspenders: a dead multipart/x-mixed-replace stream does
    // not reliably fire the <img> element's onerror (confirmed on this
    // Chromium/backend combo — a killed-and-restarted Flask process left
    // the feed blank with no error event at all). Periodically force a
    // fresh connection regardless, so the feed self-heals within one
    // interval of any backend restart even if onerror never fires.
    clearInterval(state.feedWatchdog);
    state.feedWatchdog = setInterval(async () => {
      if (!state.cameraRunning) return;
      try {
        const status = await Api.getStatus();
        if (status.camera_connected) {
          document.getElementById("cameraFeed").src = "/video_feed?t=" + Date.now();
          setStartCameraConn("connected");
        } else {
          setStartCameraConn("connecting");
        }
      } catch (e) {
        // backend unreachable this tick — next interval will retry, but
        // flip the light red now so a dead backend is visible immediately
        // instead of silently freezing the last frame.
        setStartCameraConn("connecting");
      }
    }, 15000);
  }

  async function toggleCamera() {
    const feed = document.getElementById("cameraFeed");
    const placeholder = document.getElementById("cameraPlaceholder");
    try {
      if (state.cameraRunning) {
        await Api.stopCamera();
        state.cameraRunning = false;
        clearTimeout(state.feedRetryTimer);
        clearInterval(state.feedWatchdog);
        feed.onerror = null;
        feed.style.display = "none";
        feed.removeAttribute("src");
        placeholder.style.display = "";
        placeholder.textContent = "Camera not started";
        setStartCameraConn(null);
        // Detected Items reflects the live feed only — once the camera
        // stops, nothing is being detected, so the panel should read zero
        // rather than keep showing whatever was last seen.
        state.detections.clear();
        redrawDetections();
      } else {
        // Light up red the instant it's pressed, before the request even
        // resolves — applyStatus()/the feed watchdog flip it green once
        // camera_connected is actually confirmed.
        setStartCameraConn("connecting");
        await Api.startCamera();
        state.cameraRunning = true;
        placeholder.style.display = "none";
        feed.style.display = "";
        feed.onerror = reconnectFeed;
        feed.src = "/video_feed?t=" + Date.now();
        startFeedWatchdog();
      }
    } catch (e) {
      placeholder.textContent = `Camera error: ${e.message}`;
      console.error("[camera]", e);
      setStartCameraConn(null);
    }
  }

  function wireActionButtons() {
    const saveInv = document.getElementById("saveInventoryBtn");
    saveInv.addEventListener("click", async () => {
      const items = Array.from(state.detections.values())
        .filter((d) => d.qty > 0)
        .map((d) => ({ name: d.name, qty: d.qty, category: categoryFor(d.name) }));
      if (items.length === 0) {
        console.log("[action] nothing to save — no detected items with qty > 0");
        return;
      }
      saveInv.classList.add("pulsed");
      setTimeout(() => saveInv.classList.remove("pulsed"), 450);
      try {
        const result = await Api.syncInventory(items);
        console.log("[action] saved to inventory", result);
        state.detections.forEach((d) => (d.touched = false));
        await Promise.all([refreshInventory(), refreshRecipes(), refreshAnalytics()]);
        window.Dialog && window.Dialog.show("Saved to inventory.");
      } catch (e) {
        console.error("[action] save to inventory failed", e);
      }
    });

    const confirmCook = document.getElementById("confirmCookBtn");
    confirmCook.addEventListener("click", async () => {
      if (state.selectedRank == null) {
        console.log("[action] select a recipe card first");
        return;
      }
      confirmCook.classList.add("pulsed");
      setTimeout(() => confirmCook.classList.remove("pulsed"), 450);
      try {
        const result = await Api.confirmCook(state.selectedRank);
        console.log("[action] confirmed & cooked", result);
        await Promise.all([refreshInventory(), refreshAnalytics()]);
        window.Dialog && window.Dialog.show("Cooking confirmed!");
      } catch (e) {
        console.error("[action] confirm cook failed", e);
      }
    });
  }

  function wireCloseButton() {
    const closeBtn = document.getElementById("closeAppBtn");
    closeBtn.addEventListener("click", async () => {
      closeBtn.disabled = true;
      try {
        await Api.closeApp();
      } catch (e) {
        // The server may tear itself down before the response finishes —
        // that's the expected/successful path here, not an error to surface.
        console.log("[app] close requested", e.message);
      }
    });
  }

  function wireMinimizeButton() {
    const btn = document.getElementById("minimizeAppBtn");
    btn.addEventListener("click", async () => {
      try {
        await Api.minimizeApp();
      } catch (e) {
        console.warn("[app] minimize failed", e.message);
      }
    });
  }

  // ============ Data refresh + SSE ============
  async function refreshInventory() {
    try {
      renderInventory(await Api.getInventory());
    } catch (e) {
      console.warn("[inventory] refresh failed", e);
    }
  }

  async function refreshRecipes() {
    try {
      renderRecipes(await Api.getRecipes());
    } catch (e) {
      console.warn("[recipes] refresh failed", e);
    }
  }

  function connectEvents() {
    const es = new EventSource("/events");
    es.addEventListener("status_update", (ev) => applyStatus(JSON.parse(ev.data)));
    es.addEventListener("detection_update", (ev) => applyDetectionUpdate(JSON.parse(ev.data)));
    es.onerror = () => console.warn("[sse] connection error — browser will auto-retry");
  }

  // ============ Boot ============
  document.addEventListener("DOMContentLoaded", async () => {
    renderSidebarShell();
    redrawDetections();
    wireNav();
    wireActionButtons();
    wireCloseButton();
    wireMinimizeButton();
    initGrid();

    try {
      applyStatus(await Api.getStatus());
    } catch (e) { /* stay at default "bad" state */ }

    await refreshAnalytics();
    await Promise.all([refreshInventory(), refreshRecipes()]);
    connectEvents();
  });

  window.Dashboard = {
    refreshInventory, refreshRecipes, refreshAnalytics, categoryFor, iconFor,
    persistLayout, resetLayoutNow, lockLayout, unlockLayout,
  };
})();
