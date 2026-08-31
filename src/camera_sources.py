"""Camera/file frame sources shared by the Tkinter GUI and the web backend.

Extracted out of gui.py so both UIs get byte-identical camera behavior
(USB fallback logic, Pi-camera MJPEG parsing, warm-up timeout) driven by
the same settings.json, instead of two copies that can drift apart.

Pi camera support uses `rpicam-vid` as a subprocess emitting MJPEG to
stdout, because the venv's OpenCV is built without GStreamer and
picamera2 (system package) targets system Python 3.13, not this 3.9 venv.
"""

import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VID_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}


class FileSource:
    def __init__(self, path: Path):
        self.path = path
        self.is_image = path.suffix.lower() in IMG_EXTS
        if self.is_image:
            self.img = cv2.imread(str(path))
            if self.img is None:
                raise RuntimeError(f"could not read image {path}")
        else:
            self.cap = cv2.VideoCapture(str(path))
            if not self.cap.isOpened():
                raise RuntimeError(f"could not open video {path}")

    def read(self):
        if self.is_image:
            return self.img.copy()
        ret, frame = self.cap.read()
        return frame if ret else None

    def close(self):
        if not self.is_image:
            self.cap.release()


def discover_camera_indices() -> list[int]:
    """USB/UVC camera device indices, preferring ones whose V4L2 sysfs name
    doesn't match this Pi's non-camera video nodes (ISP pipeline, HEVC
    decoder — see CHECKPOINT.md, "not all /dev/video* are cameras").

    Exists because a USB re-enumeration (replug, bus reset/power-cycle)
    can shift which /dev/videoN index the actual camera lands on, leaving
    a `usb_index` saved in settings.json pointing at nothing.
    """
    NON_CAMERA_NAME_HINTS = ("pispbe", "hevc-dec", "bcm2835-isp", "bcm2835-codec")
    indices = []
    for name_path in sorted(Path("/sys/class/video4linux").glob("video*/name")):
        try:
            name = name_path.read_text().strip().lower()
        except OSError:
            continue
        if any(hint in name for hint in NON_CAMERA_NAME_HINTS):
            continue
        indices.append(int(name_path.parent.name.removeprefix("video")))
    return indices


class USBCameraSource:
    def __init__(self, index: int = 0):
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open /dev/video{index}")
        # Request MJPEG explicitly — without this, V4L2 falls back to this
        # camera's default raw YUYV mode (~600KB/frame uncompressed vs. a
        # few tens of KB compressed), which saturates a shared USB 2.0 bus
        # and was the direct cause of periodic torn/corrupted frames
        # reproduced via repro_distortion.py. Must be set before width/
        # height so the driver negotiates resolution within MJPEG mode.
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    def read(self):
        ret, frame = self.cap.read()
        return frame if ret else None

    def close(self):
        self.cap.release()


class PiCameraSource:
    """Stream MJPEG from rpicam-vid via stdout and yield decoded frames."""

    def __init__(self, width: int = 640, height: int = 480, fps: int = 15):
        if not shutil.which("rpicam-vid"):
            raise RuntimeError("rpicam-vid not found; install rpicam-apps")
        self.proc = subprocess.Popen(
            [
                "rpicam-vid", "-t", "0", "--codec", "mjpeg", "-n",
                "--width", str(width), "--height", str(height),
                "--framerate", str(fps), "-o", "-",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
        )
        self.buf = b""

    def read(self):
        while True:
            if self.proc.poll() is not None:
                return None
            chunk = self.proc.stdout.read(8192)
            if not chunk:
                return None
            self.buf += chunk
            soi = self.buf.find(b"\xff\xd8")
            if soi == -1:
                self.buf = b""
                continue
            eoi = self.buf.find(b"\xff\xd9", soi + 2)
            if eoi == -1:
                continue
            jpeg = self.buf[soi : eoi + 2]
            self.buf = self.buf[eoi + 2 :]
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                return frame

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def grab_one(source, timeout_s: float = 3.0):
    """Read frames until we get a non-None one or hit the timeout (for camera warm-up)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = source.read()
        if frame is not None:
            return frame
    return None
