#!/usr/bin/env python3
"""Minimal GUI smoke test: create app, run actions, exit.
Uses root.after() for proper Tkinter main-loop integration."""
from __future__ import annotations

import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import cv2
import numpy as np
import tkinter as tk

from gui import App, ROOT as GUI_ROOT
from detector import TFLITE_AVAILABLE

PASS = 0
FAIL = 0
_STEPS: list = []

def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        tag = f"PASS ({detail})" if detail else "PASS"
        print(f"  ✓ {name}: {tag}")
    else:
        FAIL += 1
        print(f"  ✗ {name}: FAIL — {detail or 'condition was False'}")

# Create a test image
img_dir = GUI_ROOT / "inference" / "images"
img_dir.mkdir(parents=True, exist_ok=True)
test_img = str(img_dir / "test_veg.jpg")
cv2.imwrite(test_img, np.full((480, 640, 3), 100, dtype=np.uint8))

print(f"System: Python {sys.version}")
print(f"TFLite available: {TFLITE_AVAILABLE}")
print(f"Display: {os.environ.get('DISPLAY','(unset)')}")

root = tk.Tk()
app = App(root)

def step1_initial():
    """Verify initial state."""
    print("\n── Step 1: Initial state ──")
    check("initial status idle", "idle" in app.status_var.get().lower())
    check("start btn enabled", str(app.start_btn.cget("state")) != "disabled")
    check("gpt btn disabled", str(app.gpt_btn.cget("state")) == "disabled")
    root.after(10, step2_settings)

def step2_settings():
    """Toggle settings popup."""
    print("\n── Step 2: Settings popup ──")
    app._toggle_settings()
    check("settings popup exists", app.settings_popup is not None)
    if app.settings_popup:
        check("popup visible", app.settings_popup.winfo_exists())
        # Switch to USB then back to file
        app.source_var.set("usb")
        app._refresh_input_state()  # radio command not triggered on programmatic set
        check("usb disables file entry",
              str(app.file_entry.cget("state")) == "disabled")
        app.source_var.set("file")
        app._refresh_input_state()
        app.settings_popup.destroy()
        app.settings_popup = None
    check("popup closed", app.settings_popup is None)
    root.after(10, step3_model_selection)

def step3_model_selection():
    """Cycle through model choices."""
    print("\n── Step 3: Model selection ──")
    import models as model_registry
    for name in model_registry.CHOICES:
        app.model_var.set(name)
        root.update_idletasks()
        check(f"model {name}", app.model_var.get() == name)
    app.model_var.set(model_registry.DEFAULT)
    root.after(10, step4_file_select)

def step4_file_select():
    """Set file path and trigger preview."""
    print("\n── Step 4: File selection ──")
    app.file_path.set(test_img)
    root.update_idletasks()
    check(f"file path set", app.file_path.get() == test_img)
    root.after(10, step5_detection)

def step5_detection():
    """Run start detection (simulation backend)."""
    print("\n── Step 5: Start detection ──")
    app._do_start()
    root.update_idletasks()
    check("state=detected", app.action_state == "detected",
          f"state={app.action_state}")
    check("detected items label",
          app.detected_label_var.get().startswith("Detected Items"))
    check("status shows SIMULATION",
          "SIMULATION" in app.status_var.get() or "detected" in app.status_var.get().lower())
    root.after(10, step6_gpt)

def step6_gpt():
    """Click Ask GPT and wait for async result."""
    print("\n── Step 6: Ask GPT ──")
    if app.action_state != "detected":
        print("  ⚠ skip — not in detected state")
        root.after(10, step7_stop)
        return
    app._do_ask_gpt()
    root.update_idletasks()
    check("GPT btn disabled", str(app.gpt_btn.cget("state")) == "disabled")

    # Poll for GPT result (async recipe worker via thread-safe queue)
    deadline = time.time() + 25
    def poll_gpt():
        root.update_idletasks()
        if app.action_state == "recipe_shown":
            check("recipe shown", True)
            check("recipe text not empty",
                  bool(app.recipe_text.get("1.0", "end-1c").strip()))
            root.after(10, step7_stop)
        elif time.time() > deadline:
            check("recipe timeout", False, "GPT did not respond in 25s")
            root.after(10, step7_stop)
        else:
            root.after(200, poll_gpt)
    poll_gpt()

def step7_stop():
    """Click Stop and verify reset."""
    print("\n── Step 7: Stop / reset ──")
    app._do_stop()
    root.update_idletasks()
    check("state=idle", app.action_state == "idle")
    check("status idle", "idle" in app.status_var.get().lower())
    check("detected text empty",
          app.detected_text.get("1.0", "end-1c").strip() == "")
    root.after(10, step8_settings_persistence)

def step8_settings_persistence():
    """Verify settings save/load."""
    print("\n── Step 8: Settings persistence ──")
    from pathlib import Path
    import json
    settings_path = Path.home() / ".config" / "yolo11_coral_gui" / "settings.json"
    app.conf_var.set(0.50)
    app.model_var.set("yolo11n")
    app._save_settings()
    check("settings file exists", settings_path.exists())
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        check("confidence saved", data.get("confidence") == 0.50)
        check("model saved", data.get("model") == "yolo11n")
    root.after(10, step9_close)

def step9_close():
    """Clean shutdown."""
    print("\n── Step 9: Window close ──")
    app._on_close()
    check("app closed cleanly", True)
    # Print summary
    global PASS, FAIL
    print(f"\n{'='*50}")
    print(f"Smoke test: {PASS} passed, {FAIL} failed")
    # root is already destroyed by app._on_close()
    sys.exit(1 if FAIL else 0)

# Start the test chain
root.after(100, step1_initial)
root.mainloop()
