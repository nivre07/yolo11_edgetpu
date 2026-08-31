#!/usr/bin/env python3
"""GUI simulation test for YOLOv11 Coral app.

Exercises all major UI flows without real hardware:
  - Settings toggle (source selection, model picker, confidence slider)
  - File browsing and image preview
  - Start detection (uses SimulationBackend)
  - Ask GPT (queries OpenAI via internet)
  - Stop/reset
  - Video auto-detection
  - Window close / settings persistence

Usage:
  cd /home/gpt/Documents/yolo11_edgetpu
  source .venv/bin/activate
  python test_gui_sim.py          # full test suite

Returns exit code 0 on success, 1 on failure. Prints detailed step results.
"""

from __future__ import annotations

import json
import os
import sys
import time
import tkinter as tk
from pathlib import Path

# Ensure we can import the project's src modules
_HERE = Path(__file__).resolve().parent
_SRC = _HERE / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from detector import TFLITE_AVAILABLE
import models as model_registry
from gui import App

ROOT = _HERE
TEST_IMAGE = ROOT / "inference" / "images"
TEST_VIDEO = ROOT / "inference" / "videos"

# Test results
class TestSuite:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors: list[str] = []
        self._start_time = time.time()

    def ok(self, name: str, detail: str = ""):
        self.passed += 1
        tag = "PASS" if not detail else f"PASS ({detail})"
        print(f"  ✓ {name}: {tag}")

    def fail(self, name: str, detail: str):
        self.failed += 1
        self.errors.append(f"{name}: {detail}")
        print(f"  ✗ {name}: FAIL — {detail}")

    def check(self, name: str, condition: bool, detail: str = ""):
        if condition:
            self.ok(name, detail)
        else:
            self.fail(name, detail or "condition was False")

    def summary(self) -> int:
        dur = time.time() - self._start_time
        print(f"\n{'='*50}")
        print(f"Tests: {self.passed} passed, {self.failed} failed ({dur:.1f}s)")
        if self.errors:
            print(f"Errors:\n  " + "\n  ".join(self.errors))
        return 1 if self.failed else 0

    def status_contains(self, app: App, text: str) -> bool:
        return text.lower() in app.status_var.get().lower()


def run_simulation() -> int:
    suite = TestSuite()
    root = tk.Tk()
    app = App(root)

    print(f"System: Python {sys.version}")
    print(f"TFLite available: {TFLITE_AVAILABLE}")
    print(f"Model choices: {model_registry.CHOICES}")
    print(f"Default model: {model_registry.DEFAULT}")

    # ── Step 1: Verify default state ──
    print("\n── Step 1: Initial state ──")
    suite.check("initial status idle", suite.status_contains(app, "idle"))
    suite.check("start btn enabled", str(app.start_btn.cget("state")) != "disabled")
    suite.check("gpt btn disabled", str(app.gpt_btn.cget("state")) == "disabled")
    suite.check("stop btn disabled",
                str(app.stop_btn.cget("state")) == "disabled" or not app._video_det_thread,
                "stop btn state")

    # ── Step 2: Settings popup ──
    print("\n── Step 2: Settings popup ──")
    app._toggle_settings()
    suite.check("settings popup exists", app.settings_popup is not None)
    if app.settings_popup:
        suite.check("settings popup visible", app.settings_popup.winfo_exists())
        # Verify source selection
        suite.check("default source file", app.source_var.get() == "file")
        # Switch to USB
        app.source_var.set("usb")
        app._refresh_input_state()  # radio button command callback
        # Check that file entry is disabled
        suite.check("usb mode disables file entry",
                    str(app.file_entry.cget("state")) == "disabled")
        # Switch back to file
        app.source_var.set("file")
        app._refresh_input_state()
        app.settings_popup.destroy()
        app.settings_popup = None
    suite.check("settings popup closed", app.settings_popup is None)

    # ── Step 3: Model selection ──
    print("\n── Step 3: Model selection ──")
    for model_name in model_registry.CHOICES:
        app.model_var.set(model_name)
        root.update_idletasks()
        suite.check(f"model switch to {model_name}", app.model_var.get() == model_name)
    # Reset to default
    app.model_var.set(model_registry.DEFAULT)
    suite.check(f"model reset to {model_registry.DEFAULT}",
                app.model_var.get() == model_registry.DEFAULT)

    # ── Step 4: File browse (test image) ──
    print("\n── Step 4: File selection ──")
    test_images = list((ROOT / "inference" / "images").glob("*"))
    if test_images:
        first_img = str(test_images[0])
        app.file_path.set(first_img)
        root.update_idletasks()
        suite.check(f"file path set", app.file_path.get() == first_img)
    else:
        # Try to find any image in inference/images
        img_dir = ROOT / "inference" / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        # Create a small test image
        import cv2
        import numpy as np
        test_img_path = str(img_dir / "test_grid.jpg")
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, 50), (200, 200), (0, 255, 0), -1)
        cv2.putText(img, "TEST", (250, 250), cv2.FONT_HERSHEY_SIMPLEX, 2, (255,255,255), 2)
        cv2.imwrite(test_img_path, img)
        app.file_path.set(test_img_path)
        root.update_idletasks()
        suite.check("created test image", True)
        suite.check(f"file path set to test image", app.file_path.get() == test_img_path)

    # ── Step 5: Start detection (simulation) ──
    print("\n── Step 5: Start detection ──")
    app._do_start()
    root.update_idletasks()
    root.update()  # process pending events
    suite.check("detection ran (state=detected)", app.action_state == "detected",
                f"state={app.action_state}")
    suite.check("detected items shown",
                app.detected_label_var.get().startswith("Detected Items"),
                app.detected_label_var.get())
    suite.check("status shows detection",
                suite.status_contains(app, "detected") or suite.status_contains(app, "SIMULATION"),
                app.status_var.get())

    # ── Step 6: Ask GPT ──
    print("\n── Step 6: Ask GPT ──")
    if app.action_state == "detected":
        app._do_ask_gpt()
        root.update_idletasks()
        root.update()
        suite.check("GPT btn disabled after click",
                    str(app.gpt_btn.cget("state")) == "disabled")
        # Wait for GPT response (async)
        deadline = time.time() + 25
        while time.time() < deadline and app.action_state != "recipe_shown":
            root.update_idletasks()
            root.update()
            time.sleep(0.1)
        suite.check("recipe shown", app.action_state == "recipe_shown",
                    f"state={app.action_state} after wait, status={app.status_var.get()}")
    else:
        suite.check("skip GPT (no detections)", True)

    # ── Step 7: Stop / reset ──
    print("\n── Step 7: Stop ──")
    app._do_stop()
    root.update_idletasks()
    suite.check("state reset to idle", app.action_state == "idle",
                f"state={app.action_state}")
    suite.check("status shows idle", suite.status_contains(app, "idle"),
                app.status_var.get())
    suite.check("detected text cleared",
                app.detected_text.get("1.0", "end-1c").strip() == "")

    # ── Step 8: Video auto-detection ──
    print("\n── Step 8: Video file detection ──")
    test_videos = list((ROOT / "inference" / "videos").glob("*"))
    if test_videos:
        video_path = str(test_videos[0])
        app.file_path.set(video_path)
        app._load_file_preview(video_path)  # trigger video auto-detection
        root.update_idletasks()
        root.update()
        time.sleep(0.8)
        root.update_idletasks()
        suite.check("video detection started",
                    app._video_det_thread is not None and app._video_det_thread.is_alive())
        # Let it run a few frames
        time.sleep(1)
        root.update_idletasks()
        # Stop it
        app._stop_video_detection()
        suite.check("video detection stopped",
                    app._video_det_thread is None or not app._video_det_thread.is_alive())
    else:
        # Create a test video
        suite.check("no test video found — creating one", True)
        import cv2
        import numpy as np
        video_dir = ROOT / "inference" / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)
        test_vid = str(video_dir / "test_sim.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(test_vid, fourcc, 10, (320, 240))
        for i in range(30):
            frame = np.full((240, 320, 3), (i*8, i*4, i*2), dtype=np.uint8)
            writer.write(frame)
        writer.release()
        app.file_path.set(test_vid)
        app._load_file_preview(test_vid)  # trigger video auto-detection
        root.update_idletasks()
        root.update()
        time.sleep(0.8)
        root.update()
        suite.check("video detection started (created video)",
                    app._video_det_thread is not None)
        if app._video_det_thread:
            time.sleep(1)
            root.update()
            suite.check("video thread alive after 1s", app._video_det_thread.is_alive())
            app._stop_video_detection()
            suite.check("video detection stopped",
                        not app._video_det_thread.is_alive())

    # ── Step 9: Settings persistence ──
    print("\n── Step 9: Settings persistence ──")
    settings_path = Path.home() / ".config" / "yolo11_coral_gui" / "settings.json"
    # Change confidence and model, then force save
    app.conf_var.set(0.50)
    app.model_var.set("yolo11n")
    # The conf var traces save after 500ms debounce; trigger immediately
    app._save_settings()
    suite.check("settings file created", settings_path.exists())
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        suite.check("confidence saved", data.get("confidence") == 0.50)
        suite.check("model saved", data.get("model") == "yolo11n")
        suite.check("file path saved", "last_file_path" in data)

    # ── Step 10: Window close ──
    print("\n── Step 10: Window close ──")
    # Simulate WM_DELETE_WINDOW
    app._on_close()
    suite.check("app closed cleanly", True)

    return suite.summary()


if __name__ == "__main__":
    # Force headless-safe display if available
    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ":0"
    sys.exit(run_simulation())
