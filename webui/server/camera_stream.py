"""StreamWorker: owns exactly one camera source + one detection backend,
runs the single shared inference loop that both /video_feed and /events
read from — multiple browser tabs never spawn extra inference.
"""

import datetime
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from camera_sources import FileSource, USBCameraSource, PiCameraSource, discover_camera_indices
from detector import FoodDetector, draw
import models as model_registry
import settings_store
import db
import event_bus
import status
from config import LABELS_PATH, STREAM_SAMPLE_SECONDS

# Consecutive predict() failures before we call it "stalled" rather than a
# one-off blip — at the 0.05s retry sleep below, 10 is ~0.5s of continuous
# failure, well past a single transient glitch.
INFER_STALL_THRESHOLD = 10


def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class StreamWorker:
    """Runs capture and inference as two independent threads so a slow (or
    hardware-degraded) Coral inference call can never block the live video.
    _capture_loop() continuously reads fresh frames and re-draws whatever
    detections _infer_loop() most recently produced onto each one, so the
    feed always advances at full camera rate; _infer_loop() runs predict()
    back-to-back on the freshest available frame, however long each call
    takes, without the capture side ever waiting on it.

    Previously both happened in one sequential loop: a multi-second Coral
    call blocked the entire iteration, including the next source.read(),
    so the video froze for the full duration of every slow inference and
    then jumped straight to a stale frame — visually indistinguishable
    from corruption, and always several seconds behind the real camera
    view since the JPEG and the detection list were only ever updated
    together, from the same stale capture.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._stop: Optional[threading.Event] = None
        self._source = None
        self._model: Optional[FoodDetector] = None
        self._class_list = LABELS_PATH.read_text().splitlines()
        self._latest_frame = None  # freshest raw capture, for the inference thread to consume
        self._latest_dets = []  # most recent detection results, for the capture thread to draw
        self._latest_jpeg: Optional[bytes] = None
        self._latest_summary: dict = {"items": [], "total_count": 0, "avg_confidence": None, "near_miss_confidence": None}
        self._session_id: Optional[str] = None
        self._last_good_frame = None  # diff baseline + fallback when a frame is torn
        self._recent_diffs: list = []
        self._consecutive_bad = 0
        self._corrupt_frames_dropped = 0
        self._consecutive_infer_fails = 0

    # ---------- public API ----------

    def is_running(self) -> bool:
        return self._capture_thread is not None and self._capture_thread.is_alive()

    def start(self) -> dict:
        if self.is_running():
            return {"already_running": True}

        settings = settings_store.load_settings()
        source = self._make_source(settings)

        model_name = settings["model"] or model_registry.DEFAULT
        if model_name not in model_registry.CHOICES:
            # Self-heal a stale on-disk model (e.g. a model file removed
            # since last session) rather than failing "Start Camera".
            model_name = model_registry.DEFAULT
        model_path = model_registry.resolve(model_name)
        model = FoodDetector(
            model_path, len(self._class_list), conf_thresh=float(settings["confidence"]),
        )

        self._source = source
        self._model = model
        self._latest_frame = None
        self._latest_dets = []
        self._last_good_frame = None
        self._recent_diffs = []
        self._consecutive_bad = 0
        self._corrupt_frames_dropped = 0
        self._consecutive_infer_fails = 0
        self._session_id = uuid.uuid4().hex[:12]
        self._stop = threading.Event()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._infer_thread = threading.Thread(target=self._infer_loop, daemon=True)
        self._capture_thread.start()
        self._infer_thread.start()

        status.set_camera_connected(True)
        status.set_yolo_active(True, backend=model.accelerator, model=model_name)
        return {"session_id": self._session_id, "backend": model.accelerator, "model": model_name}

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for thread in (self._capture_thread, self._infer_thread):
            if thread is not None and thread.is_alive():
                # Inference may be mid multi-second predict() call; this can't
                # be interrupted, so the join just waits out whatever's left
                # of the current call rather than forcing a hard timeout.
                thread.join(timeout=10)
        if self._source is not None:
            try:
                self._source.close()
            except Exception:
                pass
        if self._model is not None:
            self._model.close()
            self._model = None
        self._capture_thread = None
        self._infer_thread = None
        self._stop = None
        self._source = None
        with self._lock:
            self._latest_frame = None
            self._latest_dets = []
            self._latest_jpeg = None
            self._latest_summary = {"items": [], "total_count": 0, "avg_confidence": None, "near_miss_confidence": None}
        status.set_camera_connected(False)
        status.set_yolo_active(False)
        status.set_inference_stalled(False)
        status.set_camera_degraded(False)

    def get_latest_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def get_latest_summary(self) -> dict:
        with self._lock:
            return dict(self._latest_summary)

    # ---------- internals ----------

    def _make_source(self, settings: dict):
        kind = settings["source"]
        if kind == "usb":
            idx = int(settings["usb_index"])
            # Configured index first (fast path when nothing changed), then
            # any auto-discovered camera-like nodes (self-heals after a USB
            # re-enumeration shifts the index), then 0 as a last resort.
            candidates = list(dict.fromkeys([idx, *discover_camera_indices(), 0]))
            last_err = None
            for attempt in candidates:
                try:
                    return USBCameraSource(attempt)
                except Exception as e:
                    last_err = e
                    continue
            tried = ", ".join(f"/dev/video{i}" for i in candidates)
            raise RuntimeError(f"no USB camera found (tried {tried}): {last_err}")
        if kind == "picam":
            return PiCameraSource()
        if kind == "file":
            path = settings["last_file_path"]
            if not path:
                raise RuntimeError("no file configured — set one via Settings first")
            return FileSource(Path(path))
        raise RuntimeError(f"unknown source {kind}")

    def _reject_if_corrupt(self, frame):
        """Detect torn USB frames (garbled/shifted content from a corrupted
        isochronous transfer — a known hardware-level issue on this camera's
        USB 2.0 connection, reproduced via raw frame-diffing: see
        repro_distortion.py) and substitute the last known-good frame
        instead of ever serving or running inference on the torn one.

        A torn frame shows as a sudden large jump in mean pixel diff versus
        the last good frame, well above the recent baseline. Capped at 10
        consecutive substitutions so a genuine fast scene change (camera
        moved, lighting flipped) doesn't freeze the feed forever.
        """
        if self._last_good_frame is None:
            self._last_good_frame = frame
            return frame

        d = float(np.mean(cv2.absdiff(frame, self._last_good_frame)))
        recent = self._recent_diffs[-15:]
        recent_avg = float(np.mean(recent)) if recent else d
        is_corrupt = d > max(20.0, recent_avg * 4) and self._consecutive_bad < 10

        if is_corrupt:
            self._consecutive_bad += 1
            self._corrupt_frames_dropped += 1
            print(f"[capture] torn frame rejected (diff={d:.1f}, baseline={recent_avg:.1f}), "
                  f"total dropped this session={self._corrupt_frames_dropped}", flush=True)
            if self._consecutive_bad >= 10:
                # The cap was just hit — we're about to let a possibly-still-
                # corrupted frame through (see the docstring above) rather
                # than freeze forever. That's a real degradation, not a
                # one-off blip; make it visible instead of only logged.
                status.set_camera_degraded(True)
            return self._last_good_frame

        if self._consecutive_bad >= 10:
            print("[capture] camera link recovered after sustained corruption", flush=True)
            status.set_camera_degraded(False)
        self._consecutive_bad = 0
        self._recent_diffs.append(d)
        if len(self._recent_diffs) > 30:
            self._recent_diffs.pop(0)
        self._last_good_frame = frame
        return frame

    def _capture_loop(self) -> None:
        """Runs at full camera rate (~30fps) regardless of inference speed.
        Draws whatever _infer_loop() most recently produced onto each fresh
        frame — the overlay's box positions can lag behind the live scene
        by however stale the last inference result is, but the video image
        itself is always the current frame, never frozen or stale."""
        stop = self._stop
        source = self._source

        while stop is not None and not stop.is_set():
            try:
                frame = source.read()
            except Exception:
                status.set_camera_connected(False)
                break
            if frame is None:
                time.sleep(0.05)
                continue

            frame = self._reject_if_corrupt(frame)

            with self._lock:
                self._latest_frame = frame
                dets = self._latest_dets

            try:
                annotated = draw(frame.copy(), dets, self._class_list) if dets else frame
                ok, buf = cv2.imencode(".jpg", annotated)
                jpeg = buf.tobytes() if ok else None
            except Exception:
                jpeg = None

            if jpeg is not None:
                with self._lock:
                    self._latest_jpeg = jpeg

            time.sleep(1 / 30)

    def _infer_loop(self) -> None:
        """Runs predict() back-to-back on the freshest frame available,
        however long each call takes (the Coral hardware connection can
        currently make this multi-second) — never waits on or blocks the
        capture loop above."""
        stop = self._stop
        model = self._model
        last_sample = 0.0

        while stop is not None and not stop.is_set():
            with self._lock:
                frame = self._latest_frame

            if frame is None:
                time.sleep(0.05)
                continue

            try:
                t0 = time.monotonic()
                dets = model.predict(frame)
                inference_ms = (time.monotonic() - t0) * 1000.0
            except Exception as e:
                # This used to be silently swallowed — a flaky Coral USB
                # connection could stop producing any real detections while
                # every other signal (camera_connected, yolo_active, the
                # video feed itself) kept reporting healthy. Log it and,
                # once it's clearly not a one-off blip, surface it via
                # status.inference_stalled so the UI can actually show it.
                self._consecutive_infer_fails += 1
                if self._consecutive_infer_fails == 1:
                    print(f"[infer] predict() failed: {e}", file=sys.stderr)
                if self._consecutive_infer_fails == INFER_STALL_THRESHOLD:
                    print(
                        f"[infer] {INFER_STALL_THRESHOLD} consecutive predict() "
                        "failures — marking inference stalled",
                        file=sys.stderr,
                    )
                    status.set_inference_stalled(True)
                time.sleep(0.05)
                continue

            if self._consecutive_infer_fails >= INFER_STALL_THRESHOLD:
                print(
                    f"[infer] recovered after {self._consecutive_infer_fails} "
                    "consecutive failures",
                    file=sys.stderr,
                )
                status.set_inference_stalled(False)
            self._consecutive_infer_fails = 0

            with self._lock:
                self._latest_dets = dets

            counts = Counter(
                self._class_list[d.cls] for d in dets
                if 0 <= d.cls < len(self._class_list)
            )
            confs_by_name = {}
            for d in dets:
                if 0 <= d.cls < len(self._class_list):
                    confs_by_name.setdefault(self._class_list[d.cls], []).append(d.conf)
            items = [
                {
                    "name": name,
                    "confidence": round(max(confs_by_name.get(name, [0.0])) * 100),
                    "count": count,
                }
                for name, count in counts.most_common()
            ]
            avg_conf = (sum(d.conf for d in dets) / len(dets)) if dets else None

            # When nothing crosses conf_thresh, "saw nothing" and "saw
            # something too faint to count" look identical (both an empty
            # items list) — that ambiguity is exactly what made a too-high
            # confidence setting look like a broken camera/Coral earlier.
            # Surface the best raw score so the UI can tell them apart.
            near_miss = None
            if not dets:
                score = getattr(model, "last_max_score", None)
                if score is not None:
                    near_miss = round(score * 100)

            summary = {
                "items": items,
                "total_count": sum(counts.values()),
                "avg_confidence": avg_conf,
                "backend": model.accelerator,
                "inference_ms": round(inference_ms, 1),
                "near_miss_confidence": near_miss,
            }

            with self._lock:
                self._latest_summary = summary

            now = time.monotonic()
            if now - last_sample >= STREAM_SAMPLE_SECONDS:
                last_sample = now
                event_bus.publish("detection_update", {"timestamp": _now_iso(), **summary})
                try:
                    conn = db.get_conn()
                    conn.execute(
                        "INSERT INTO inference_frames "
                        "(session_id, num_detections, avg_confidence, inference_ms, backend) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (self._session_id, len(dets), avg_conf, inference_ms, model.accelerator),
                    )
                    conn.commit()
                except Exception:
                    pass


worker = StreamWorker()
