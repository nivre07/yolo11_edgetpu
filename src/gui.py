"""Tkinter GUI: pick an image/video file, USB camera, or Pi camera and run
YOLOv11 detection on the Coral USB Edge TPU.

Single-shot capture model: Start grabs one frame and runs detection, Ask GPT
queries OpenAI for a recipe based on the detected items, Stop clears the panes.

When the Coral USB Edge TPU is not available (tflite_runtime not installed),
the app auto-falls back to SimulationBackend which generates fake detections
so the full UI and workflow can be tested without hardware.

Pi camera support uses `rpicam-vid` as a subprocess emitting MJPEG to stdout,
because the venv's OpenCV is built without GStreamer and picamera2 (system
package) targets system Python 3.13, not this 3.9 venv.
"""

import queue
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

from camera_sources import (
    FileSource, USBCameraSource, PiCameraSource, discover_camera_indices,
    IMG_EXTS, VID_EXTS, grab_one as _grab_one,
)
from detector import FoodDetector, TFLITE_AVAILABLE, draw
import models as model_registry
import recipe
import settings_store

# Google Coral USB Accelerator device IDs (before/after firmware load)
_CORAL_IDS = {("1a6e", "089a"), ("18d1", "9302")}

def _is_coral_connected() -> bool:
    """Check if Google Coral USB Accelerator is plugged in."""
    base = Path("/sys/bus/usb/devices")
    try:
        for dev in base.iterdir():
            try:
                vid = (dev / "idVendor").read_text().strip()
                pid = (dev / "idProduct").read_text().strip()
                if (vid, pid) in _CORAL_IDS:
                    return True
            except OSError:
                pass
    except OSError:
        pass
    return False

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "labels/coco1.txt"

PREVIEW_W, PREVIEW_H = 640, 540
RIGHT_PANE_W = 380


# ---------- helpers ----------

def _short_accel(model) -> str:
    """Short accelerator label for the GUI status bar."""
    if model is None:
        return "no model loaded"
    if model.using_edgetpu:
        return "Edge TPU (Coral USB)"
    if TFLITE_AVAILABLE:
        return "CPU via tflite_runtime"
    return "SIMULATION (no Coral USB)"


# ---------- app ----------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("YOLOv11 + Coral USB — Filipino Food Recipe")
        root.geometry(f"{PREVIEW_W + RIGHT_PANE_W + 60}x{PREVIEW_H + 140}")
        root.after(0, lambda: root.state("zoomed"))  # defer until window mapped

        self.class_list = LABELS.read_text().splitlines()
        self.model = None
        self.loaded_model_name = None

        # state machine: idle -> detected -> recipe_shown -> idle
        self.action_state = "idle"
        self.last_items: list[str] = []

        # settings vars
        self.source_var = tk.StringVar(value="file")
        self.file_path = tk.StringVar(value="")
        self.usb_index = tk.IntVar(value=0)
        self.conf_var = tk.DoubleVar(value=0.35)
        self.model_var = tk.StringVar(value=model_registry.DEFAULT)
        self.status_var = tk.StringVar(value="idle — open Settings, then press Start")

        self.settings_popup: tk.Toplevel | None = None

        # thread-safe message queue (for GPT worker -> main thread)
        self._msg_queue: queue.SimpleQueue = queue.SimpleQueue()

        # live camera preview
        self._preview_thread: Optional[threading.Thread] = None
        self._preview_stop: Optional[threading.Event] = None
        self._preview_source = None
        self._last_preview_frame = None
        self._preview_lock = threading.Lock()

        # video file detection (auto-plays with YOLO overlay)
        self._video_det_thread: Optional[threading.Thread] = None
        self._video_det_stop: Optional[threading.Event] = None
        self._video_det_source = None
        self._video_det_lock = threading.Lock()
        self._last_det_annotated = None
        self._last_det_dets = []
        # _video_det_paused tracks whether user hit Start while video was playing

        # Coral USB hotplug state (no popup on initial check — only on change events)
        self._coral_present: bool = _is_coral_connected()

        # restore persisted settings (before building UI to avoid trace storms)
        self._load_settings()
        self._setup_persist_traces()

        self._build_ui()
        self._update_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(66, self._preview_tick)
        self.root.after(2000, self._coral_poll)

    # ---------- UI build ----------

    def _build_ui(self):
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill=tk.X)

        self.capture_btn = tk.Button(
            bar, text="📷", bg="#27ae60", fg="white",
            relief=tk.FLAT, padx=4, pady=2,
            command=self._capture_image,
        )
        self.capture_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.settings_btn = ttk.Button(bar, text="Settings ▾", command=self._toggle_settings)
        self.settings_btn.pack(side=tk.LEFT)

        self.start_btn = ttk.Button(bar, text="Start", command=self._do_start)
        self.start_btn.pack(side=tk.LEFT, padx=4)

        self.gpt_btn = ttk.Button(bar, text="Ask GPT", command=self._do_ask_gpt)
        self.gpt_btn.pack(side=tk.LEFT, padx=4)

        self.stop_btn = ttk.Button(bar, text="Stop", command=self._do_stop)
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        ttk.Label(bar, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        # output area: left image, right text
        body = ttk.Frame(self.root, padding=(8, 0))
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)
        self.preview = tk.Label(left, background="#222", width=PREVIEW_W, height=PREVIEW_H)
        self.preview.pack()

        right = ttk.Frame(body, padding=(8, 0))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.detected_label_var = tk.StringVar(value="Detected Items:")
        ttk.Label(right, textvariable=self.detected_label_var,
                  font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        detected_frame = ttk.Frame(right)
        detected_frame.pack(fill=tk.BOTH, pady=(2, 10))
        self.detected_text = tk.Text(detected_frame, height=1, wrap=tk.WORD, state=tk.DISABLED,
                                     background="#f7f7f7")
        self.detected_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detected_scroll = ttk.Scrollbar(detected_frame, orient=tk.VERTICAL,
                                        command=self.detected_text.yview)
        detected_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detected_text.configure(yscrollcommand=detected_scroll.set)

        ttk.Label(right, text="Suggested Recipe:", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        recipe_frame = ttk.Frame(right)
        recipe_frame.pack(fill=tk.BOTH, expand=True, pady=(2, 4))
        self.recipe_text = tk.Text(recipe_frame, wrap=tk.WORD, state=tk.DISABLED,
                                   background="#f7f7f7", height=10)
        self.recipe_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        recipe_scroll = ttk.Scrollbar(recipe_frame, orient=tk.VERTICAL, command=self.recipe_text.yview)
        recipe_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.recipe_text.configure(yscrollcommand=recipe_scroll.set)

        ttk.Label(self.root, textvariable=self.status_var, padding=8,
                  relief=tk.SUNKEN, anchor="w").pack(fill=tk.X, side=tk.BOTTOM)

    # ---------- Settings popover ----------

    def _toggle_settings(self):
        if self.settings_popup is not None and self.settings_popup.winfo_exists():
            self.settings_popup.destroy()
            self.settings_popup = None
            return
        model_registry.refresh()
        popup = tk.Toplevel(self.root)
        popup.title("Settings")
        popup.transient(self.root)
        x = self.settings_btn.winfo_rootx()
        y = self.settings_btn.winfo_rooty() + self.settings_btn.winfo_height() + 4
        popup.geometry(f"+{x}+{y}")

        frm = ttk.Frame(popup, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        src_group = ttk.LabelFrame(frm, text="Source", padding=6)
        src_group.pack(fill=tk.X, pady=(0, 8))
        for label, val in [("File", "file"), ("USB camera", "usb"), ("Pi camera", "picam")]:
            ttk.Radiobutton(
                src_group, text=label, value=val, variable=self.source_var,
                command=self._refresh_input_state,
            ).pack(side=tk.LEFT, padx=4)

        file_row = ttk.Frame(frm)
        file_row.pack(fill=tk.X, pady=2)
        ttk.Label(file_row, text="File:").pack(side=tk.LEFT)
        self.file_entry = ttk.Entry(file_row, textvariable=self.file_path, width=30)
        self.file_entry.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        self.browse_btn = ttk.Button(file_row, text="Browse…", command=self._browse)
        self.browse_btn.pack(side=tk.LEFT)

        usb_row = ttk.Frame(frm)
        usb_row.pack(fill=tk.X, pady=2)
        ttk.Label(usb_row, text="USB index:").pack(side=tk.LEFT)
        self.usb_spin = ttk.Spinbox(usb_row, from_=0, to=10, width=4, textvariable=self.usb_index)
        self.usb_spin.pack(side=tk.LEFT, padx=4)

        model_row = ttk.Frame(frm)
        model_row.pack(fill=tk.X, pady=2)
        ttk.Label(model_row, text="Model:").pack(side=tk.LEFT)
        self.model_combo = ttk.Combobox(
            model_row, textvariable=self.model_var, values=list(model_registry.CHOICES),
            state="readonly", width=14,
        )
        self.model_combo.pack(side=tk.LEFT, padx=4)

        conf_row = ttk.Frame(frm)
        conf_row.pack(fill=tk.X, pady=2)
        ttk.Label(conf_row, text="Confidence:").pack(side=tk.LEFT)
        ttk.Scale(
            conf_row, from_=0.1, to=0.9, variable=self.conf_var,
            orient=tk.HORIZONTAL, length=160,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(conf_row, textvariable=self.conf_var).pack(side=tk.LEFT)

        ttk.Button(frm, text="Close", command=lambda: popup.destroy()).pack(pady=(8, 0))

        popup.protocol("WM_DELETE_WINDOW", lambda: popup.destroy())
        self.settings_popup = popup
        self._refresh_input_state()

    def _refresh_input_state(self):
        s = self.source_var.get()
        if self.settings_popup is not None and self.settings_popup.winfo_exists():
            self.file_entry.configure(state=tk.NORMAL if s == "file" else tk.DISABLED)
            self.browse_btn.configure(state=tk.NORMAL if s == "file" else tk.DISABLED)
            self.usb_spin.configure(state=tk.NORMAL if s == "usb" else tk.DISABLED)
        # restart/stop live preview to match the new source
        self._stop_preview()
        self._stop_video_detection()
        self._start_preview()

    def _browse(self):
        last = self.file_path.get().strip()
        init_dir = str(Path(last).parent) if last else str(ROOT / "inference")
        path = filedialog.askopenfilename(
            title="Choose image or video",
            filetypes=[
                ("Media", "*.jpg *.jpeg *.png *.bmp *.webp *.mp4 *.mov *.avi *.mkv *.m4v *.webm"),
                ("All files", "*.*"),
            ],
            initialdir=init_dir,
        )
        if path:
            self.file_path.set(path)
            self._load_file_preview(path)
            if self.settings_popup is not None and self.settings_popup.winfo_exists():
                self.settings_popup.destroy()
                self.settings_popup = None

    def _load_file_preview(self, path: str):
        """Render image preview or auto-start video detection with YOLO overlay."""
        p = Path(path)
        if p.suffix.lower() in VID_EXTS:
            # auto-start video detection with YOLO overlay
            self._stop_video_detection()
            self._start_video_detection(p)
            return

        frame = None
        try:
            if p.suffix.lower() in IMG_EXTS:
                frame = cv2.imread(str(p))
        except Exception:
            frame = None
        if frame is not None:
            self._show_image(frame)
            self.status_var.set(f"preview: {p.name} — press Start to detect")

    # ---------- action buttons (Start / Ask GPT / Stop) ----------

    def _coral_poll(self):
        """Poll for Coral USB hotplug events every 2 s; notify user on state change."""
        now = _is_coral_connected()
        if now != self._coral_present:
            self._coral_present = now
            if now:
                messagebox.showinfo("Coral USB", "Google Coral USB Available!")
            else:
                messagebox.showwarning("Coral USB", "No Google Coral USB Detected!")
        self.root.after(2000, self._coral_poll)

    def _update_button_states(self):
        """Enable/disable buttons based on current action_state."""
        if self.action_state == "idle":
            self.start_btn.configure(state=tk.NORMAL)
            self.gpt_btn.configure(state=tk.DISABLED)
            # enable Stop if video detection is playing (so user can stop it)
            if self._video_det_thread is not None and self._video_det_thread.is_alive():
                self.stop_btn.configure(state=tk.NORMAL)
            else:
                self.stop_btn.configure(state=tk.DISABLED)
        elif self.action_state == "detected":
            self.start_btn.configure(state=tk.NORMAL)
            self.gpt_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.NORMAL)
        elif self.action_state == "recipe_shown":
            self.start_btn.configure(state=tk.DISABLED)
            self.gpt_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)

    def _show_detection_results(self, dets):
        """Update the detected-items pane from a list of Detection objects."""
        counts = Counter(
            self.class_list[d.cls] for d in dets
            if 0 <= d.cls < len(self.class_list)
        )
        total = sum(counts.values())
        self.last_items = sorted(counts.keys())
        if total == 0:
            items_str = "(none)"
        else:
            parts = [f"{name} x{cnt}" if cnt > 1 else name
                     for name, cnt in counts.most_common()]
            items_str = ", ".join(parts)
        self._set_text(self.detected_text, items_str)
        self._auto_height(self.detected_text)
        self.detected_label_var.set(f"Detected Items ({total} total):")
        return total

    def _do_start(self):
        if not self._coral_present:
            messagebox.showwarning("Coral USB", "No Google Coral USB Detected!")
            return
        # If video detection is running, pause it and use the last frame+dets.
        if self._video_det_thread is not None and self._video_det_thread.is_alive():
            # capture the latest frame BEFORE stopping (stop clears shared state)
            with self._video_det_lock:
                annotated = self._last_det_annotated
                dets = list(self._last_det_dets)
            self._stop_video_detection()
            if annotated is not None and dets is not None:
                self._show_image(annotated)
                total = self._show_detection_results(dets)
                self.action_state = "detected"
                self._update_button_states()
                accel = _short_accel(self.model)
                if self.model.using_edgetpu:
                    print(f"[coral] ✓ Google Coral USB ACTIVE — Edge TPU inference", flush=True)
                else:
                    print(f"[coral] ✗ Coral USB NOT active — using {accel}", flush=True)
                self.status_var.set(
                    f"video paused — {total} item(s) on {accel}"
                )
                return

        # Prefer "snap" from the active live preview to avoid reopening the camera.
        snap = self._snap_preview_frame()
        if snap is not None:
            self._stop_preview()
            frame = snap
        else:
            try:
                source = self._make_source()
            except Exception as e:
                messagebox.showerror("Source error", str(e))
                return
            self.status_var.set("capturing…")
            self.root.update_idletasks()
            try:
                frame = _grab_one(source)
            finally:
                try:
                    source.close()
                except Exception:
                    pass
            if frame is None:
                messagebox.showerror("Capture error", "could not grab a frame from the source")
                self.status_var.set("capture failed")
                return

        wanted = self.model_var.get()
        if self.model is None or self.loaded_model_name != wanted:
            try:
                model_path = model_registry.resolve(wanted)
            except Exception as e:
                messagebox.showerror("Model error", str(e))
                return
            self.status_var.set(f"loading {wanted} ({model_path.name})…")
            self.root.update_idletasks()
            try:
                self.model = FoodDetector(model_path, len(self.class_list))
            except Exception as e:
                messagebox.showerror("Model load failed", str(e))
                self.status_var.set("model load failed")
                return
            self.loaded_model_name = wanted
            # Print hardware accelerator info to console
            print(f"[accelerator] {self.model.accelerator}  [{self.loaded_model_name}]",
                  flush=True)

        self.model.conf_thresh = float(self.conf_var.get())
        dets = self.model.predict(frame)
        annotated = draw(frame.copy(), dets, self.class_list)
        self._show_image(annotated)

        total = self._show_detection_results(dets)
        self.action_state = "detected"
        self._update_button_states()
        accel = _short_accel(self.model)
        # Explicit Coral USB status
        if self.model.using_edgetpu:
            print(f"[coral] ✓ Google Coral USB ACTIVE — inference running on Edge TPU", flush=True)
        else:
            print(f"[coral] ✗ Coral USB NOT active — using {accel}", flush=True)
        self.status_var.set(
            f"detected {total} item(s) on {accel}  [{self.loaded_model_name}]"
        )

    def _do_ask_gpt(self):
        self.start_btn.configure(state=tk.DISABLED)
        self.gpt_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status_var.set("asking GPT…")
        items = list(self.last_items)
        threading.Thread(target=self._gpt_worker, args=(items,), daemon=True).start()

    def _gpt_worker(self, items: list[str]):
        try:
            text = recipe.suggest(items)
        except Exception as e:
            text = f"[recipe error] {e}"
        # Thread-safe delivery: push to queue, main thread picks it up in _preview_tick
        self._msg_queue.put(("gpt_done", text))

    def _on_gpt_done(self, text: str):
        self._set_text(self.recipe_text, text)
        self._auto_height(self.recipe_text, min_lines=5)
        self.action_state = "recipe_shown"
        self._update_button_states()
        self.status_var.set("recipe ready — press Stop to reset")

    def _do_stop(self):
        # Stop everything: video detection, camera preview, clear all panes.
        self._stop_video_detection()
        self._stop_preview()
        self.preview.configure(image="")
        self.preview.image = None
        self._set_text(self.detected_text, "")
        self._auto_height(self.detected_text)
        self._set_text(self.recipe_text, "")
        self.detected_label_var.set("Detected Items:")
        self.last_items = []
        self.action_state = "idle"
        self._update_button_states()
        self.status_var.set("idle — open Settings, then press Start")

        # Restart live preview for camera sources so the feed comes back.
        src = self.source_var.get()
        if src in ("usb", "picam"):
            self._start_preview()

    # ---------- helpers ----------

    def _make_source(self):
        kind = self.source_var.get()
        if kind == "file":
            p = self.file_path.get().strip()
            if not p:
                raise RuntimeError("pick a file first (open Settings)")
            return FileSource(Path(p))
        if kind == "usb":
            return USBCameraSource(int(self.usb_index.get()))
        if kind == "picam":
            return PiCameraSource()
        raise RuntimeError(f"unknown source {kind}")

    def _show_image(self, frame):
        h, w = frame.shape[:2]
        scale = min(PREVIEW_W / w, PREVIEW_H / h)
        if scale != 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = ImageTk.PhotoImage(image=Image.fromarray(rgb))
        self.preview.configure(image=img)
        self.preview.image = img  # keep ref

    # ---------- live camera preview ----------

    # ---------- settings persistence ----------

    def _load_settings(self):
        """Restore all settings from the JSON file on disk (shared with the web backend)."""
        data = settings_store.load_settings()
        self.conf_var.set(data["confidence"])
        if data["last_file_path"]:
            self.file_path.set(data["last_file_path"])
        if data["model"] in model_registry.CHOICES:
            self.model_var.set(data["model"])
        self.source_var.set(data["source"])
        self.usb_index.set(data["usb_index"])

    def _save_settings(self):
        """Write current settings to the JSON file on disk (shared with the web backend)."""
        settings_store.save_settings({
            "confidence": self.conf_var.get(),
            "last_file_path": self.file_path.get(),
            "model": self.model_var.get(),
            "source": self.source_var.get(),
            "usb_index": self.usb_index.get(),
        })

    def _setup_persist_traces(self):
        """Attach write traces to all settings vars so changes save immediately."""
        def _on_conf_change(*_args):
            # debounce the slider — save 500ms after the last drag event
            if not hasattr(self, "_conf_save_id"):
                self._conf_save_id = None
            if self._conf_save_id is not None:
                self.root.after_cancel(self._conf_save_id)
            self._conf_save_id = self.root.after(500, self._save_settings)

        def _on_setting_change(*_args):
            self._save_settings()

        self.conf_var.trace_add("write", _on_conf_change)
        self.file_path.trace_add("write", _on_setting_change)
        self.model_var.trace_add("write", _on_setting_change)
        self.source_var.trace_add("write", _on_setting_change)
        self.usb_index.trace_add("write", _on_setting_change)

    def _start_preview(self):
        if self._preview_thread is not None and self._preview_thread.is_alive():
            return
        if self.action_state != "idle":
            return
        kind = self.source_var.get()
        if kind not in ("usb", "picam"):
            return

        # For USB cameras, try the configured index; fall back to 0 on failure.
        if kind == "usb":
            source = self._try_open_usb_camera()
            if source is None:
                return
        else:
            try:
                source = self._make_source()
            except Exception as e:
                self.status_var.set(f"preview error: {e}")
                return

        self._preview_source = source
        self._preview_stop = threading.Event()
        with self._preview_lock:
            self._last_preview_frame = None
        self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self._preview_thread.start()
        accel = _short_accel(self.model) if self.model else "no model"
        self.status_var.set(f"live preview ({kind}) — press Start to capture")

    def _try_open_usb_camera(self):
        """Open the USB camera at the configured index, falling back to
        auto-discovered camera-like nodes (self-heals after a USB
        re-enumeration shifts the index) and finally to 0."""
        idx = self.usb_index.get()
        candidates = list(dict.fromkeys([idx, *discover_camera_indices(), 0]))
        for attempt in candidates:
            try:
                source = USBCameraSource(attempt)
                if attempt != idx:
                    self.usb_index.set(attempt)
                    self.status_var.set(
                        f"camera /dev/video{idx} not available; "
                        f"fell back to /dev/video{attempt}"
                    )
                return source
            except Exception:
                continue
        tried = " and ".join(f"/dev/video{i}" for i in candidates)
        self.status_var.set(f"preview error: no USB camera found (tried {tried})")
        return None

    def _stop_preview(self):
        if self._preview_stop is not None:
            self._preview_stop.set()
        thread = self._preview_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        src = self._preview_source
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        self._preview_thread = None
        self._preview_stop = None
        self._preview_source = None
        with self._preview_lock:
            self._last_preview_frame = None

    def _preview_loop(self):
        stop = self._preview_stop
        src = self._preview_source
        while stop is not None and not stop.is_set():
            try:
                frame = src.read()
            except Exception:
                break
            if frame is None:
                time.sleep(0.05)
                continue
            with self._preview_lock:
                self._last_preview_frame = frame
            time.sleep(1 / 30)  # cap producer ~30 fps

    def _preview_tick(self):
        # Process thread-safe message queue (GPT results, etc.)
        while not self._msg_queue.empty():
            try:
                msg, payload = self._msg_queue.get_nowait()
                if msg == "gpt_done":
                    self._on_gpt_done(payload)
            except queue.Empty:
                break

        # video detection overlay (highest priority in idle state)
        if self.action_state == "idle" and self._video_det_thread is not None:
            with self._video_det_lock:
                frame = self._last_det_annotated
            if frame is not None:
                self._show_image(frame)
        elif self.action_state == "idle" and self._preview_thread is not None:
            with self._preview_lock:
                frame = self._last_preview_frame
            if frame is not None:
                self._show_image(frame)
        self.root.after(66, self._preview_tick)  # ~15 fps refresh

    def _snap_preview_frame(self):
        """Return the most recent live-preview frame, or None if no preview is active."""
        if self._preview_thread is None:
            return None
        with self._preview_lock:
            return self._last_preview_frame.copy() if self._last_preview_frame is not None else None

    def _capture_image(self):
        """Capture current preview frame and save to data_images/NNN.jpg."""
        frame = self._snap_preview_frame()
        if frame is None:
            messagebox.showwarning("Capture", "No live preview active — start the camera first.")
            return
        save_dir = Path(__file__).resolve().parent.parent / "data_images"
        save_dir.mkdir(exist_ok=True)
        existing = [
            int(p.stem) for p in save_dir.glob("*.jpg")
            if p.stem.isdigit()
        ]
        next_num = max(existing, default=0) + 1
        out_path = save_dir / f"{next_num}.jpg"
        cv2.imwrite(str(out_path), frame)
        self.status_var.set(f"Saved {out_path.name} → data_images/")

    # ---------- video file detection (auto-play with YOLO overlay) ----------

    def _start_video_detection(self, path: Path):
        """Load model, open the video file, and start a background detection loop."""
        self._stop_preview()  # kill any lingering camera preview
        # load / reload model if needed
        wanted = self.model_var.get()
        try:
            model_path = model_registry.resolve(wanted)
        except Exception as e:
            messagebox.showerror("Model error", str(e))
            return
        if self.model is None or self.loaded_model_name != wanted:
            self.status_var.set(f"loading {wanted} ({model_path.name})…")
            self.root.update_idletasks()
            try:
                self.model = FoodDetector(model_path, len(self.class_list))
            except Exception as e:
                messagebox.showerror("Model load failed", str(e))
                self.status_var.set("model load failed")
                return
            self.loaded_model_name = wanted

        self.model.conf_thresh = float(self.conf_var.get())

        try:
            self._video_det_source = FileSource(path)
        except Exception as e:
            messagebox.showerror("Video error", str(e))
            return

        self._video_det_stop = threading.Event()
        self._video_det_thread = threading.Thread(target=self._video_det_loop, daemon=True)
        self._video_det_thread.start()
        self._update_button_states()
        # Print accelerator info to console
        print(f"[accelerator] {self.model.accelerator}  [video: {path.name}]", flush=True)
        if self.model.using_edgetpu:
            print(f"[coral] ✓ Google Coral USB ACTIVE — Edge TPU video inference", flush=True)
        else:
            print(f"[coral] ✗ Coral USB NOT active — using CPU/simulation", flush=True)
        accel = _short_accel(self.model)
        self.status_var.set(
            f"video detection live ({path.name}) — {accel}"
        )

    def _stop_video_detection(self):
        """Stop the video detection thread and release the source."""
        if self._video_det_stop is not None:
            self._video_det_stop.set()
        thread = self._video_det_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        src = self._video_det_source
        if src is not None:
            try:
                src.close()
            except Exception:
                pass
        self._video_det_thread = None
        self._video_det_stop = None
        self._video_det_source = None
        # _video_det_paused tracks whether user hit Start while video was playing
        with self._video_det_lock:
            self._last_det_annotated = None
            self._last_det_dets = []

    def _video_det_loop(self):
        """Background thread: read frames, run YOLO, draw overlay, loop on EOF."""
        stop = self._video_det_stop
        src = self._video_det_source
        model = self.model
        fps_ema = 0.0
        prev_time = time.time()
        while stop is not None and not stop.is_set():
            try:
                frame = src.read()
            except Exception:
                break
            if frame is None:
                # loop the video
                try:
                    src.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                except Exception:
                    break
                continue
            try:
                dets = model.predict(frame)
                annotated = draw(frame.copy(), dets, self.class_list)
            except Exception:
                continue

            # FPS tracking (exponential moving average)
            now = time.time()
            instant_fps = 1.0 / max(now - prev_time, 0.001)
            prev_time = now
            if fps_ema == 0.0:
                fps_ema = instant_fps
            else:
                fps_ema = fps_ema * 0.9 + instant_fps * 0.1

            # draw FPS text on a black bar at the bottom of the frame (font doubled)
            h, w = annotated.shape[:2]
            fps_text = f"FPS: {fps_ema:.1f}"
            font_scale = 1.2
            font_thickness = 2
            (tw, th), baseline = cv2.getTextSize(
                fps_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness,
            )
            bar_h = th + 12
            cv2.rectangle(annotated, (0, h - bar_h), (w, h), (0, 0, 0), -1)
            cv2.putText(
                annotated, fps_text,
                ((w - tw) // 2, h - (bar_h - th) // 2 - 2),
                cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 0), font_thickness,
            )

            with self._video_det_lock:
                self._last_det_annotated = annotated
                self._last_det_dets = dets
            time.sleep(1 / 30)  # cap ~30 fps

    def _set_text(self, widget: tk.Text, text: str):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        if text:
            widget.insert("1.0", text)
        widget.configure(state=tk.DISABLED)

    def _auto_height(self, widget: tk.Text, min_lines: int = 3):
        """Resize a Text widget's height to fit its content — no items hidden."""
        widget.update_idletasks()
        lines = int(widget.index("end-1c").split(".")[0])
        widget.configure(height=max(min_lines, lines))

    def _on_close(self):
        self._stop_preview()
        self._stop_video_detection()
        if self.settings_popup is not None and self.settings_popup.winfo_exists():
            self.settings_popup.destroy()
        self._save_settings()

        # Release large objects so memory is freed before process exit.
        self.model = None
        self._last_preview_frame = None
        self._last_det_annotated = None
        self._last_det_dets = None

        import gc
        gc.collect()

        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
