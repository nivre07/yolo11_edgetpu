"""Detection backends for YOLOv11 + Coral Edge TPU.

Backend auto-selection (priority):
  1. tflite_runtime  — real Coral USB Edge TPU inference (fastest)
  2. SimulationBackend  — fake detections for testing (no hardware needed)

The simulation backend generates realistic-looking bounding boxes on a test
image so the GUI can be exercised end-to-end without a Coral or a camera.
"""

from __future__ import annotations

import os
import platform
import random
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# ---------- common types ----------

@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float
    cls: int


# ---------- backend detection ----------

class Backend(ABC):
    """Abstract detection backend."""

    @abstractmethod
    def predict(self, frame_bgr: np.ndarray) -> list[Detection]:
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def using_edgetpu(self) -> bool:
        return False

    @property
    def accelerator(self) -> str:
        """Human-readable description of the hardware accelerator in use."""
        return f"{self.name} (no hardware accelerator)"


# ---------- backend 1: tflite_runtime (Coral USB) ----------
#
# On Python 3.13+ the tflite_runtime .so (compiled for 3.6–3.9) has ABI
# incompatibilities beyond just _PyThreadState_UncheckedGet → the patched
# binary segfaults.  We test import safety in a subprocess to avoid
# crashing the main process.

TFLITE_AVAILABLE = False
Interpreter = None  # type: ignore
load_delegate_fn = None  # type: ignore

def _safe_import_tflite() -> bool:
    """Probe whether tflite_runtime can be imported without crashing.

    The binary-patched .so causes SIGSEGV on Python 3.13, so we do this
    in a child process that we can kill safely.
    """
    probe = (
        "import sys; sys.path.insert(0, %r); "
        "from tflite_runtime.interpreter import Interpreter, load_delegate; "
        "print('OK')"
    ) % str(Path(__file__).resolve().parent)
    try:
        r = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=8,
        )
        return r.returncode == 0 and "OK" in r.stdout
    except Exception:
        return False

if _safe_import_tflite():
    try:
        import tflite_runtime.interpreter as _tfl_interp
        Interpreter = _tfl_interp.Interpreter
        load_delegate_fn = _tfl_interp.load_delegate
        TFLITE_AVAILABLE = True
        print("[detector] tflite_runtime available", file=sys.stderr)
    except Exception:
        pass
else:
    print(
        "[detector] tflite_runtime not available — "
        "using SimulationBackend (safe fallback)",
        file=sys.stderr,
    )


class TFLiteBackend(Backend):
    """Real Coral USB Edge TPU backend via tflite_runtime."""

    def __init__(
        self,
        model_path: Path,
        num_classes: int,
        conf_thresh: float = 0.35,
        iou_thresh: float = 0.45,
        num_threads: int = 4,
    ):
        self.conf_thresh = conf_thresh
        self.iou_thresh = iou_thresh
        self.nc = num_classes
        # Best raw class score seen on the last predict() call, *before* the
        # conf_thresh cutoff — lets a caller tell "camera saw something, just
        # below your confidence threshold" apart from "saw nothing at all",
        # which otherwise both look identical (an empty detections list).
        self.last_max_score: Optional[float] = None

        if not TFLITE_AVAILABLE:
            raise RuntimeError(
                "tflite_runtime is not installed in this Python environment.\n"
                "Install it with:\n"
                "  pip install --extra-index-url https://google-coral.github.io/py-repo/ tflite_runtime\n"
                "For Coral USB, also: sudo apt install libedgetpu1-std"
            )

        delegates = []
        self._using_edgetpu = (
            model_path.name.endswith("_edgetpu.tflite") or
            model_path.name.endswith(".edgetpu.tflite")
        )
        if self._using_edgetpu:
            try:
                delegates.append(
                    load_delegate_fn("libedgetpu.so.1", options={"device": "usb"})
                )
                print(f"[detector] Coral USB delegate requested for {model_path.name}",
                      file=sys.stderr)
            except Exception as e:
                raise RuntimeError(
                    f"Coral USB Edge TPU delegate failed for {model_path.name}: {e}\n"
                    "Ensure the Coral USB is plugged in and libedgetpu1-std is installed."
                ) from e

        self.interp = Interpreter(
            model_path=str(model_path),
            experimental_delegates=delegates,
            num_threads=num_threads,
        )
        self.interp.allocate_tensors()

        # Verify delegate was applied — check how many ops run on TPU vs CPU
        import re as _re
        det_details = self.interp.get_delegate_details() if hasattr(self.interp, 'get_delegate_details') else []
        if self._using_edgetpu and det_details:
            for dd in det_details:
                dname = dd.get("delegate_name", dd.get("name", "unknown"))
                n_nodes = dd.get("node_size", dd.get("count", "?"))
                print(f"[detector] Delegate active: {dname}  ({n_nodes} nodes delegated)",
                      file=sys.stderr)
        elif self._using_edgetpu:
            # No delegate_details method — fall back to tensor allocation check
            print(f"[detector] Coral USB delegate loaded for {model_path.name}",
                  file=sys.stderr)

        in_det = self.interp.get_input_details()[0]
        out_det = self.interp.get_output_details()[0]
        self.in_idx = in_det["index"]
        self.out_idx = out_det["index"]
        self.in_scale, self.in_zp = in_det["quantization"]
        self.out_scale, self.out_zp = out_det["quantization"]
        _, self.ih, self.iw, _ = in_det["shape"]

    @classmethod
    def _create_cpu(
        cls,
        model_path: Path,
        num_classes: int,
        conf_thresh: float = 0.35,
        iou_thresh: float = 0.45,
        num_threads: int = 4,
    ) -> "TFLiteBackend":
        """Create a TFLiteBackend for CPU-only inference (no Edge TPU delegate).

        This is the factory that the ``create_backend(accelerator="cpu")``
        codepath calls at detector.py line ~344.  We avoid going through
        ``__init__`` because that constructor always tries to load the
        Coral delegate when the model filename contains ``_edgetpu``.
        """
        if not TFLITE_AVAILABLE:
            raise RuntimeError("tflite_runtime not installed")

        # Create an uninitialised instance, then set up manually.
        # We skip __init__ and use __new__ + manual attribute assignment
        # so we can pass an empty delegates list.
        backend = cls.__new__(cls)
        backend.conf_thresh = conf_thresh
        backend.iou_thresh = iou_thresh
        backend.nc = num_classes
        backend._using_edgetpu = False
        backend.last_max_score = None

        backend.interp = Interpreter(
            model_path=str(model_path),
            experimental_delegates=[],  # <-- no delegate = pure CPU
            num_threads=num_threads,
        )
        backend.interp.allocate_tensors()

        in_det = backend.interp.get_input_details()[0]
        out_det = backend.interp.get_output_details()[0]
        backend.in_idx = in_det["index"]
        backend.out_idx = out_det["index"]
        backend.in_scale, backend.in_zp = in_det["quantization"]
        backend.out_scale, backend.out_zp = out_det["quantization"]
        _, backend.ih, backend.iw, _ = in_det["shape"]

        print(f"[detector] CPU backend created [{model_path.name}]",
              file=sys.stderr)
        return backend

    @property
    def name(self) -> str:
        if self._using_edgetpu:
            return "TFLiteBackend (Coral USB Edge TPU)"
        return "TFLiteBackend (CPU via tflite_runtime)"

    @property
    def using_edgetpu(self) -> bool:
        return self._using_edgetpu

    @property
    def accelerator(self) -> str:
        if self._using_edgetpu:
            return "Edge TPU (Google Coral USB)"
        return "CPU (tflite_runtime, no Coral delegate)"

    def _letterbox(self, img: np.ndarray):
        h, w = img.shape[:2]
        r = min(self.ih / h, self.iw / w)
        new_h, new_w = int(round(h * r)), int(round(w * r))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        pad_top = (self.ih - new_h) // 2
        pad_bot = self.ih - new_h - pad_top
        pad_left = (self.iw - new_w) // 2
        pad_right = self.iw - new_w - pad_left
        out = cv2.copyMakeBorder(
            resized, pad_top, pad_bot, pad_left, pad_right,
            cv2.BORDER_CONSTANT, value=(114, 114, 114),
        )
        return out, r, (pad_left, pad_top)

    def predict(self, frame_bgr: np.ndarray) -> list[Detection]:
        h0, w0 = frame_bgr.shape[:2]
        lb, r, (pad_x, pad_y) = self._letterbox(frame_bgr)
        rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB)
        inp = (rgb.astype(np.int16) - 128).astype(np.int8)[None]
        self.interp.set_tensor(self.in_idx, inp)
        self.interp.invoke()
        raw = self.interp.get_tensor(self.out_idx)[0]
        deq = (raw.astype(np.float32) - self.out_zp) * self.out_scale

        xywh = deq[:4]
        scores = deq[4: 4 + self.nc]
        cls_scores = scores.max(axis=0)
        cls_ids = scores.argmax(axis=0)
        self.last_max_score = float(cls_scores.max()) if cls_scores.size else None
        keep = cls_scores > self.conf_thresh
        if not keep.any():
            return []

        bx = xywh[:, keep]
        confs = cls_scores[keep]
        cls = cls_ids[keep]

        cx = bx[0] * self.iw
        cy = bx[1] * self.ih
        bw = bx[2] * self.iw
        bh = bx[3] * self.ih
        x1 = (cx - bw / 2 - pad_x) / r
        y1 = (cy - bh / 2 - pad_y) / r
        x2 = (cx + bw / 2 - pad_x) / r
        y2 = (cy + bh / 2 - pad_y) / r
        x1 = np.clip(x1, 0, w0 - 1)
        y1 = np.clip(y1, 0, h0 - 1)
        x2 = np.clip(x2, 0, w0 - 1)
        y2 = np.clip(y2, 0, h0 - 1)

        boxes_xywh = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).astype(np.float32)
        idxs = cv2.dnn.NMSBoxes(
            boxes_xywh.tolist(), confs.astype(float).tolist(),
            self.conf_thresh, self.iou_thresh,
        )
        if len(idxs) == 0:
            return []
        idxs = np.asarray(idxs).flatten()
        return [
            Detection(
                float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i]),
                float(confs[i]), int(cls[i]),
            )
            for i in idxs
        ]


# ---------- backend 2: simulation (testing without hardware) ----------

_SIM_CLASSES = [
    "Banana Blossom", "Beef", "Calamansi", "Carrot", "Chicken", "Chili",
    "Egg", "Garlic", "Ginger", "Green Chili", "Guava", "Lettuce", "Okra",
    "Onion", "Pork", "Potato", "Red Chili Pepper", "Tomato", "Young Corn",
]


class SimulationBackend(Backend):
    """Generates plausible fake detections for testing the GUI without hardware.

    Draws random bounding boxes from the food_recipe class list on the input
    frame, emulating what a real detector would return.
    """

    def __init__(
        self,
        num_classes: int = 19,
        conf_thresh: float = 0.35,
        iou_thresh: float = 0.45,
        **_kwargs,
    ):
        self.nc = num_classes
        self.conf_thresh = conf_thresh
        self.iou_thresh = iou_thresh
        self._rng = random.Random(42)  # deterministic seed
        self.last_max_score = None

    @property
    def name(self) -> str:
        return "SimulationBackend (no Coral)"

    @property
    def accelerator(self) -> str:
        return "SIMULATION (no Coral USB — install tflite_runtime + libedgetpu for real inference)"

    def predict(self, frame_bgr: np.ndarray) -> list[Detection]:
        h, w = frame_bgr.shape[:2]
        n = self._rng.randint(1, 5)
        dets = []
        max_score = 0.0
        for _ in range(n):
            # Independent of conf_thresh, like a real model's per-box score —
            # only the conf_thresh filter below should decide what survives.
            conf = self._rng.uniform(0.4, 0.95)
            max_score = max(max_score, conf)
            if conf <= self.conf_thresh:
                continue
            cx = self._rng.uniform(0.1, 0.9) * w
            cy = self._rng.uniform(0.1, 0.9) * h
            bw = self._rng.uniform(0.05, 0.4) * w
            bh = self._rng.uniform(0.05, 0.4) * h
            x1 = max(0, cx - bw / 2)
            y1 = max(0, cy - bh / 2)
            x2 = min(w - 1, cx + bw / 2)
            y2 = min(h - 1, cy + bh / 2)
            cls = self._rng.randint(0, self.nc - 1)
            dets.append(Detection(x1, y1, x2, y2, conf, cls))
        self.last_max_score = max_score
        return dets


# ---------- factory ----------

HARDWARE_CHOICES = ("coral", "cpu", "gpu")
HARDWARE_DEFAULT = "coral"


def create_backend(
    model_path: Optional[Path] = None,
    num_classes: int = 19,
    conf_thresh: float = 0.35,
    iou_thresh: float = 0.45,
    num_threads: int = 4,
    force_simulation: bool = False,
    accelerator: str = HARDWARE_DEFAULT,
) -> Backend:
    """Create a backend matching the requested *accelerator*.

    Parameters
    ----------
    accelerator : str
        One of ``HARDWARE_CHOICES``:
        - ``"coral"`` — Google Coral USB Edge TPU (default).
          Tries tflite_runtime with Edge TPU delegate; if unavailable,
          falls back to SimulationBackend.
        - ``"cpu"`` — CPU via tflite_runtime (no Edge TPU delegate).
          Falls back to SimulationBackend if tflite_runtime is missing.
        - ``"gpu"`` — GPU delegate (OpenCL / OpenGL ES 3.1).
          Not implemented on this platform; returns SimulationBackend.
    force_simulation : bool
        If True, return SimulationBackend regardless (test mode).
    """
    if accelerator not in HARDWARE_CHOICES:
        accelerator = HARDWARE_DEFAULT

    # --- forced test mode ---
    if force_simulation:
        backend = SimulationBackend(
            num_classes=num_classes, conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
        )
        print(f"[detector] BACKEND: {backend.accelerator} (force_simulation=True)",
              file=sys.stderr)
        return backend

    # --- GPU ---
    if accelerator == "gpu":
        print("[detector] GPU delegate not available on this platform — "
              "falling back to SimulationBackend.", file=sys.stderr)
        backend = SimulationBackend(
            num_classes=num_classes, conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
        )
        print(f"[detector] BACKEND: {backend.accelerator}  (GPU requested, not available)",
              file=sys.stderr)
        return backend

    # --- CPU (tflite_runtime, no Edge TPU delegate) ---
    if accelerator == "cpu":
        if TFLITE_AVAILABLE and model_path is not None and model_path.exists():
            try:
                # Create TFLiteBackend but pass an empty delegate list
                backend = TFLiteBackend._create_cpu(
                    model_path=model_path,
                    num_classes=num_classes,
                    conf_thresh=conf_thresh,
                    iou_thresh=iou_thresh,
                    num_threads=num_threads,
                )
                print(f"[detector] BACKEND: {backend.accelerator}  [{model_path.name}]",
                      file=sys.stderr)
                return backend
            except Exception as e:
                print(f"[detector] CPU TFLiteBackend failed ({e})", file=sys.stderr)
        else:
            reason = "tflite_runtime not installed" if not TFLITE_AVAILABLE else "model not found"
            print(f"[detector] CPU requested but {reason}", file=sys.stderr)
        backend = SimulationBackend(
            num_classes=num_classes, conf_thresh=conf_thresh,
            iou_thresh=iou_thresh,
        )
        print(f"[detector] BACKEND: {backend.accelerator}  (CPU unavailable)",
              file=sys.stderr)
        return backend

    # --- Coral USB (default) ---
    if not TFLITE_AVAILABLE:
        raise RuntimeError(
            "tflite_runtime is not installed — cannot run on Coral USB.\n"
            "Install with: pip install --extra-index-url "
            "https://google-coral.github.io/py-repo/ tflite_runtime\n"
            "For Coral USB, also: sudo apt install libedgetpu1-std"
        )
    if model_path is None or not model_path.exists():
        raise RuntimeError(f"Model file not found: {model_path}")

    backend = TFLiteBackend(
        model_path=model_path,
        num_classes=num_classes,
        conf_thresh=conf_thresh,
        iou_thresh=iou_thresh,
        num_threads=num_threads,
    )
    print(f"[detector] BACKEND: {backend.accelerator}  [{model_path.name}]",
          file=sys.stderr)
    return backend


# ---------- drawing ----------

def draw(frame: np.ndarray, dets: list[Detection], class_list: list[str]) -> np.ndarray:
    """Draw detection bounding boxes and labels on a copy of *frame*.

    Each label gets a filled background rectangle so the text remains readable
    regardless of the image content behind it.  Positions are clamped to stay
    fully inside the image — labels that would overflow the top edge are drawn
    inside the bounding box instead, and horizontal overflow is clamped at the
    image boundary.
    """
    H, W = frame.shape[:2]
    FONT = cv2.FONT_HERSHEY_SIMPLEX
    FONT_SCALE = 0.6
    THICKNESS = 2
    PAD_X = 4   # horizontal padding inside the label box
    PAD_Y = 3   # vertical padding inside the label box
    GAP = 4     # gap between label box and the detection rectangle

    for d in dets:
        name = class_list[d.cls] if 0 <= d.cls < len(class_list) else str(d.cls)
        label = name

        # ---- measure the text ----
        (tw, th), baseline = cv2.getTextSize(label, FONT, FONT_SCALE, THICKNESS)
        box_h = th + baseline + 2 * PAD_Y
        box_w = tw + 2 * PAD_X

        p1 = (int(d.x1), int(d.y1))
        p2 = (int(d.x2), int(d.y2))

        # ---- decide label placement ----
        # Prefer above the detection box, but fall inside if there isn't room.
        if p1[1] - box_h - GAP >= 0:
            # above
            box_y1 = p1[1] - box_h - GAP
            box_y2 = p1[1] - GAP
        else:
            # inside the top of the bounding box
            box_y1 = p1[1] + GAP
            box_y2 = box_y1 + box_h

        # Horizontal: clamp so the box stays entirely within the image.
        box_x1 = max(p1[0], 0)
        if box_x1 + box_w > W:
            box_x1 = W - box_w
        box_x2 = box_x1 + box_w

        # ---- draw the filled background ----
        cv2.rectangle(frame, (box_x1, box_y1), (box_x2, box_y2), (0, 255, 0), -1)

        # ---- draw the text in black on the green background ----
        text_x = box_x1 + PAD_X
        text_y = box_y1 + th + PAD_Y
        cv2.putText(
            frame, label, (text_x, text_y),
            FONT, FONT_SCALE, (0, 0, 0), THICKNESS, cv2.LINE_AA,
        )

        # ---- draw the detection bounding box (on top, so it's never obscured) ----
        cv2.rectangle(frame, p1, p2, (0, 255, 0), 2)

    return frame


# ---------- legacy alias ----------

class FoodDetector:
    """Legacy class name wrapping create_backend for backward compatibility.

    Usage::
        det = FoodDetector(model_path, len(class_list), conf_thresh=0.35)
        detections = det.predict(frame)
    """

    def __init__(self, *args, **kwargs):
        backend = create_backend(*args, **kwargs)
        self._backend = backend
        self.iou_thresh = kwargs.get("iou_thresh", 0.45)
        self.nc = kwargs.get("num_classes", 19)
        # Forward attributes for consumers
        self.using_edgetpu = backend.using_edgetpu
        self.accelerator = backend.accelerator
        self.backend_name = backend.name

    @property
    def conf_thresh(self) -> float:
        return self._backend.conf_thresh

    @conf_thresh.setter
    def conf_thresh(self, value: float) -> None:
        # Forwarded straight to the backend — it's the one predict() actually
        # reads, so a plain instance attribute here would be a silent no-op.
        self._backend.conf_thresh = value

    def predict(self, frame_bgr: np.ndarray) -> list[Detection]:
        return self._backend.predict(frame_bgr)

    @property
    def last_max_score(self) -> Optional[float]:
        """Best raw class score from the last predict() call, before the
        conf_thresh cutoff — None if the backend doesn't track this."""
        return getattr(self._backend, "last_max_score", None) if self._backend else None

    def close(self) -> None:
        """Explicitly drop the underlying backend (interpreter + delegate)."""
        self._backend = None