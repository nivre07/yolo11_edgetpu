// Nav-triggered detail views: Settings (form) and full read-outs of
// Inventory / Recipes / Analytics / History (the dashboard panels show
// an at-a-glance summary; these show the complete picture).
(function () {
  "use strict";

  const overlay = document.getElementById("modalOverlay");
  const titleEl = document.getElementById("modalTitle");
  const bodyEl = document.getElementById("modalBody");
  const closeBtn = document.getElementById("modalCloseBtn");

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

  function iconImg(file, alt, className) {
    return h("img", { class: className || "icon-img", src: `vendor/icons/${file}.svg`, alt: alt || "" });
  }

  function close() {
    overlay.classList.remove("open");
    bodyEl.innerHTML = "";
  }

  function open(kind) {
    overlay.classList.add("open");
    const renderers = {
      settings: renderSettings,
      inventory: renderInventoryDetail,
      recipes: renderRecipesDetail,
      analytics: renderAnalyticsDetail,
      history: renderHistoryDetail,
    };
    const fn = renderers[kind];
    if (!fn) { close(); return; }
    fn();
  }

  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && overlay.classList.contains("open")) close(); });

  // ---------- Settings ----------
  async function renderSettings() {
    titleEl.textContent = "Settings";
    bodyEl.innerHTML = "<div class='detail-empty'>Loading…</div>";

    let settings, models;
    try {
      [settings, models] = await Promise.all([Api.getSettings(), Api.getModels()]);
    } catch (e) {
      bodyEl.innerHTML = `<div class="detail-empty">Failed to load settings: ${e.message}</div>`;
      return;
    }
    bodyEl.innerHTML = "";

    const sourceLabels = { usb: "USB camera" };
    const sourceRow = h("div", { class: "form-row" }, [
      h("label", {}, "Source:"),
      h(
        "div", { class: "form-radio-group" },
        Object.keys(sourceLabels).map((val) => {
          const id = "src_" + val;
          const input = h("input", { type: "radio", name: "source", id, value: val });
          input.checked = true;
          return h("label", { for: id }, [input, " " + sourceLabels[val]]);
        })
      ),
    ]);

    const filePathInput = h("input", { class: "form-input", type: "text", value: settings.last_file_path || "" });
    const browseBtn = h("button", { class: "btn secondary" }, "Browse…");
    const fileRow = h("div", { class: "form-row", style: "display:none" }, [h("label", {}, "File:"), filePathInput, browseBtn]);

    const usbInput = h("input", { class: "form-input", type: "number", min: "0", max: "10", value: settings.usb_index });
    const usbRow = h("div", { class: "form-row", style: "display:none" }, [h("label", {}, "USB index:"), usbInput]);

    const modelSelect = h(
      "select", { class: "form-select" },
      models.choices.map((c) => {
        const opt = h("option", { value: c }, c);
        if (c === settings.model) opt.selected = true;
        return opt;
      })
    );
    const modelRow = h("div", { class: "form-row", style: "display:none" }, [h("label", {}, "Model:"), modelSelect]);

    const confValueLabel = h("span", {}, String(settings.confidence));
    const confInput = h("input", {
      class: "form-range", type: "range", min: "0.1", max: "0.9", step: "0.05", value: settings.confidence,
    });
    confInput.addEventListener("input", (e) => (confValueLabel.textContent = e.target.value));
    const confRow = h("div", { class: "form-row" }, [h("label", {}, "Confidence:"), confInput, confValueLabel]);

    const browserWrap = h("div", { class: "form-section", style: "display:none" });
    const statusMsg = h("div", { class: "form-status-msg" });
    const saveBtn = h("button", { class: "btn" }, "Save Settings");
    const saveStateBtn = h("button", { class: "btn", style: "margin-top:var(--space-md)" }, "Save Current State");
    const resetBtn = h("button", { class: "btn secondary", style: "margin-top:var(--space-sm)" }, "Reset to Defaults");

    bodyEl.appendChild(h("div", { class: "form-section" }, [sourceRow, fileRow, usbRow, modelRow, confRow]));
    bodyEl.appendChild(browserWrap);
    bodyEl.appendChild(saveBtn);
    bodyEl.appendChild(saveStateBtn);
    bodyEl.appendChild(resetBtn);
    bodyEl.appendChild(statusMsg);

    async function openBrowser(path) {
      browserWrap.style.display = "";
      browserWrap.innerHTML = "<div class='detail-empty'>Loading…</div>";
      try {
        const listing = await Api.browse(path);
        browserWrap.innerHTML = "";
        browserWrap.appendChild(h("div", { class: "form-section-title" }, listing.path));
        const list = h("div", { class: "browse-list" });
        if (listing.parent && listing.parent !== listing.path) {
          list.appendChild(h("div", { class: "browse-entry", onclick: () => openBrowser(listing.parent) }, "⬅ .."));
        }
        listing.entries.forEach((entry) => {
          const iconFile = entry.type === "dir" ? "folder" : "image-file";
          const row = h("div", { class: "browse-entry" }, [iconImg(iconFile, entry.type), ` ${entry.name}`]);
          row.addEventListener("click", () => {
            if (entry.type === "dir") {
              openBrowser(entry.path);
            } else {
              filePathInput.value = entry.path;
              browserWrap.style.display = "none";
            }
          });
          list.appendChild(row);
        });
        browserWrap.appendChild(list);
      } catch (e) {
        browserWrap.innerHTML = `<div class="detail-empty">${e.message}</div>`;
      }
    }
    browseBtn.addEventListener("click", () => openBrowser());

    function currentPayload() {
      return {
        source: bodyEl.querySelector('input[name="source"]:checked').value,
        last_file_path: filePathInput.value,
        usb_index: Number(usbInput.value),
        model: modelSelect.value,
        confidence: Number(confInput.value),
      };
    }

    async function doSaveSettings() {
      await Api.postSettings(currentPayload());
    }

    function applyValuesToForm(values) {
      bodyEl.querySelectorAll('input[name="source"]').forEach((input) => {
        input.checked = input.value === values.source;
      });
      filePathInput.value = values.last_file_path || "";
      usbInput.value = values.usb_index;
      modelSelect.value = values.model;
      confInput.value = values.confidence;
      confValueLabel.textContent = String(values.confidence);
    }

    saveBtn.addEventListener("click", async () => {
      try {
        await doSaveSettings();
        statusMsg.textContent = "Saved.";
        statusMsg.className = "form-status-msg ok";
        window.Dialog && window.Dialog.show("Settings saved.");
      } catch (e) {
        statusMsg.textContent = "Error: " + e.message;
        statusMsg.className = "form-status-msg err";
      }
    });

    saveStateBtn.addEventListener("click", async () => {
      try {
        await doSaveSettings();
        window.Dashboard && window.Dashboard.persistLayout();
        window.Dashboard && window.Dashboard.lockLayout();
        statusMsg.textContent = "Current settings and panel layout saved as the startup default.";
        statusMsg.className = "form-status-msg ok";
        window.Dialog && window.Dialog.show("Current state saved as the startup default.");
      } catch (e) {
        statusMsg.textContent = "Error: " + e.message;
        statusMsg.className = "form-status-msg err";
      }
    });

    resetBtn.addEventListener("click", async () => {
      try {
        const defaults = await Api.resetSettings();
        applyValuesToForm(defaults);
        window.Dashboard && window.Dashboard.resetLayoutNow();
        window.Dashboard && window.Dashboard.unlockLayout();
        statusMsg.textContent = "Settings and panel layout reset to defaults.";
        statusMsg.className = "form-status-msg ok";
        window.Dialog && window.Dialog.show("Settings and layout reset to defaults.");
      } catch (e) {
        statusMsg.textContent = "Error: " + e.message;
        statusMsg.className = "form-status-msg err";
      }
    });
  }

  // ---------- Inventory detail ----------
  async function renderInventoryDetail() {
    titleEl.textContent = "Refrigerator Inventory";
    bodyEl.innerHTML = "<div class='detail-empty'>Loading…</div>";
    try {
      const data = await Api.getInventory();
      bodyEl.innerHTML = "";
      const cats = Object.keys(data.categories);

      // Stepper taps only stage a change locally (like the Detected Items
      // panel's own qty steppers) — nothing is written until "Save Changes"
      // is pressed, so quantity edits can be reviewed or abandoned instead
      // of hitting the backend on every tap.
      const pending = new Map(); // item.id -> staged quantity

      const clearBtn = h("button", { class: "btn secondary", style: "margin-right:8px" }, "Clear All");
      clearBtn.disabled = cats.length === 0;
      clearBtn.addEventListener("click", async () => {
        if (!window.confirm("Clear all inventory items? This cannot be undone.")) return;
        try {
          await Api.clearInventory();
          window.Dashboard && window.Dashboard.refreshInventory();
          window.Dialog && window.Dialog.show("Inventory cleared.");
          renderInventoryDetail();
        } catch (e) {
          window.Dialog && window.Dialog.show("Error: " + e.message);
        }
      });

      const saveBtn = h("button", { class: "btn", style: "margin-bottom:12px" }, "Save Changes");
      saveBtn.disabled = true;
      saveBtn.addEventListener("click", () => {
        if (pending.size === 0) return;
        const doSave = async () => {
          try {
            for (const [id, qty] of pending) {
              await Api.adjustItem(id, qty);
            }
            pending.clear();
            window.Dashboard && window.Dashboard.refreshInventory();
            window.Dialog && window.Dialog.show("Inventory changes saved.");
            renderInventoryDetail();
          } catch (e) {
            window.Dialog && window.Dialog.show("Error: " + e.message);
          }
        };
        if (window.Dialog && window.Dialog.confirm) {
          window.Dialog.confirm(`Save ${pending.size} pending change(s) to inventory?`, doSave);
        } else {
          doSave();
        }
      });

      bodyEl.appendChild(h("div", { style: "margin-bottom:12px" }, [clearBtn, saveBtn]));

      if (cats.length === 0) {
        bodyEl.appendChild(h("div", { class: "detail-empty" }, "No inventory yet — start the camera and save detections."));
        return;
      }
      cats.forEach((cat) => {
        bodyEl.appendChild(h("div", { class: "form-section-title" }, cat));
        data.categories[cat].forEach((item) => {
          const countEl = h("span", { class: "stepper-count" }, String(item.quantity));

          function adjustQty(delta) {
            const base = pending.has(item.id) ? pending.get(item.id) : item.quantity;
            const next = Math.max(0, base + delta);
            if (next === base) return;
            pending.set(item.id, next);
            countEl.textContent = String(next);
            saveBtn.disabled = false;
          }

          const stepper = h("div", { class: "stepper" }, [
            h("button", { class: "stepper-btn", onclick: () => adjustQty(-1) }, "−"),
            countEl,
            h("button", { class: "stepper-btn", onclick: () => adjustQty(1) }, "+"),
          ]);

          const delBtn = h("button", { class: "row-delete-btn", "aria-label": `Delete ${item.name}` }, "✕");
          delBtn.addEventListener("click", async () => {
            if (!window.confirm(`Delete ${item.name} entirely? This cannot be undone.`)) return;
            try {
              await Api.deleteItem(item.id);
              window.Dashboard && window.Dashboard.refreshInventory();
              window.Dialog && window.Dialog.show(`${item.name} deleted.`);
              renderInventoryDetail();
            } catch (e) {
              window.Dialog && window.Dialog.show("Error: " + e.message);
            }
          });

          bodyEl.appendChild(
            h("div", { class: "detail-list-row" }, [
              h("div", { class: "detail-list-row-info" }, [
                h("span", {}, item.name),
                h("span", { class: "muted" }, `${item.unit} · updated ${item.updated_at}`),
              ]),
              h("div", { class: "detail-list-row-actions" }, [stepper, delBtn]),
            ])
          );
        });
      });
    } catch (e) {
      bodyEl.innerHTML = `<div class="detail-empty">${e.message}</div>`;
    }
  }

  // ---------- Recipes detail ----------
  async function renderRecipesDetail() {
    titleEl.textContent = "AI Recipe Recommendations";
    bodyEl.innerHTML = "<div class='detail-empty'>Loading…</div>";

    const regenBtn = h("button", { class: "btn", style: "margin-bottom:12px" },
      [iconImg("reload", ""), " Regenerate from current inventory"]);
    const listWrap = h("div", {});

    function renderList(recipes) {
      listWrap.innerHTML = "";
      if (!recipes || recipes.length === 0) {
        listWrap.appendChild(h("div", { class: "detail-empty" }, "No recipes yet."));
        return;
      }
      recipes.slice().sort((a, b) => a.rank - b.rank).forEach((r) => {
        listWrap.appendChild(
          h("div", { class: "detail-list-row" }, [
            h("span", {}, `#${r.rank} ${r.name} — ${r.match_percentage}%`),
            h("span", { class: "muted" }, `Missing: ${(r.missing_ingredients || []).join(", ") || "none"}`),
          ])
        );
      });
    }

    regenBtn.addEventListener("click", async () => {
      regenBtn.disabled = true;
      regenBtn.textContent = "Generating…";
      try {
        const fresh = await Api.generateRecipes();
        renderList(fresh.recipes);
        window.Dashboard && window.Dashboard.refreshRecipes();
      } catch (e) {
        listWrap.innerHTML = `<div class="detail-empty">${e.message}</div>`;
      }
      regenBtn.disabled = false;
      regenBtn.replaceChildren(iconImg("reload", ""), document.createTextNode(" Regenerate from current inventory"));
    });

    try {
      const batch = await Api.getRecipes();
      bodyEl.innerHTML = "";
      bodyEl.appendChild(regenBtn);
      bodyEl.appendChild(listWrap);
      renderList(batch.recipes);
    } catch (e) {
      bodyEl.innerHTML = `<div class="detail-empty">${e.message}</div>`;
    }
  }

  // ---------- Analytics detail ----------
  async function renderAnalyticsDetail() {
    titleEl.textContent = "Analytics";
    bodyEl.innerHTML = "<div class='detail-empty'>Loading…</div>";
    try {
      const [lowStock, usage, breakdown] = await Promise.all([
        Api.getLowStock(), Api.getUsageFrequency(), Api.getCategoryBreakdown(),
      ]);
      bodyEl.innerHTML = "";

      bodyEl.appendChild(h("div", { class: "form-section-title" }, "Low stock / shopping list"));
      if (lowStock.alerts.length === 0) {
        bodyEl.appendChild(h("div", { class: "detail-empty" }, "Nothing low on stock."));
      }
      lowStock.alerts.forEach((a) =>
        bodyEl.appendChild(
          h("div", { class: "detail-list-row" }, [
            h("span", {}, a.name),
            h("span", { class: "muted" }, `${a.quantity}${a.unit} left — restock ~${a.suggested_restock}${a.unit}`),
          ])
        )
      );

      bodyEl.appendChild(h("div", { class: "form-section-title", style: "margin-top:16px" }, "Ingredient usage frequency"));
      if (usage.usage.length === 0) {
        bodyEl.appendChild(h("div", { class: "detail-empty" }, "No cooked recipes yet."));
      }
      usage.usage.forEach((u) =>
        bodyEl.appendChild(
          h("div", { class: "detail-list-row" }, [h("span", {}, u.name), h("span", { class: "muted" }, `used ${u.count}x`)])
        )
      );

      bodyEl.appendChild(h("div", { class: "form-section-title", style: "margin-top:16px" }, "Category breakdown"));
      if (breakdown.categories.length === 0) {
        bodyEl.appendChild(h("div", { class: "detail-empty" }, "No inventory yet."));
      }
      breakdown.categories.forEach((c) =>
        bodyEl.appendChild(
          h("div", { class: "detail-list-row" }, [
            h("span", {}, c.category),
            h("span", { class: "muted" }, `${c.item_count} item(s), ${c.total_quantity} total`),
          ])
        )
      );
    } catch (e) {
      bodyEl.innerHTML = `<div class="detail-empty">${e.message}</div>`;
    }
  }

  // ---------- History detail ----------
  async function renderHistoryDetail() {
    titleEl.textContent = "History";
    bodyEl.innerHTML = "";
    const listWrap = h("div", {}, [h("div", { class: "detail-empty" }, "Loading…")]);
    bodyEl.appendChild(listWrap);
    try {
      const data = await Api.getHistory({ type: "recipe_cooked" });
      listWrap.innerHTML = "";
      if (data.events.length === 0) {
        listWrap.appendChild(h("div", { class: "detail-empty" }, "No history yet."));
        return;
      }
      data.events.forEach((ev) => {
        listWrap.appendChild(
          h("div", { class: "detail-list-row" }, [
            h("span", {}, ev.summary),
            h("span", { class: "muted" }, `${ev.type} · ${ev.timestamp}`),
          ])
        );
      });
    } catch (e) {
      listWrap.innerHTML = `<div class="detail-empty">${e.message}</div>`;
    }
  }

  window.NavModals = { open, close };
})();
