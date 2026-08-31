#!/usr/bin/env python3
"""Automated dependency checker for YOLOv11 + Coral Edge TPU project.

Checks system dependencies, Python packages, model files, Coral hardware,
and resource constraints.  Run before first use on any new machine.

Usage:
    python check_deps.py           # full check
    python check_deps.py --quick   # skip model file checks
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import ctypes.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _run(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    """Run a command and return (returncode, stdout)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except Exception as exc:
        return 1, str(exc)


def _file_exists(path: str | Path) -> bool:
    return Path(path).exists()


def _libedgetpu_path() -> str | None:
    """Find the Edge TPU runtime in standard or explicitly configured paths."""
    found = ctypes.util.find_library("edgetpu")
    if found:
        return found
    candidates = [
        Path("/usr/lib/aarch64-linux-gnu/libedgetpu.so.1"),
        Path("/usr/lib/x86_64-linux-gnu/libedgetpu.so.1"),
        Path("/usr/local/lib/libedgetpu.so.1"),
    ]
    for directory in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
        if directory:
            candidates.append(Path(directory) / "libedgetpu.so.1")
    return str(next((p for p in candidates if p.exists()), "")) or None


def _model_files() -> list[Path]:
    return sorted((ROOT / "models").glob("*/*.tflite"))


# ---------------------------------------------------------------------------
# check definitions
# ---------------------------------------------------------------------------

CHECKS = [
    # ---- Python ----
    {
        "id": "python_version",
        "label": "Python ≥ 3.9",
        "category": "python",
        "fn": lambda: sys.version_info >= (3, 9),
        "detail": lambda: f"Python {sys.version}",
    },
    {
        "id": "python_coral",
        "label": "Python 3.9 (Coral-compatible)",
        "category": "python",
        "fn": lambda: sys.version_info >= (3, 9) and sys.version_info < (3, 10),
        "detail": lambda: "3.9 — Coral native" if sys.version_info < (3, 10) else f"3.13 — needs .venv39",
    },
    {
        "id": "tkinter",
        "label": "tkinter",
        "category": "python",
        "fn": lambda: _try_import("tkinter"),
    },
    {
        "id": "numpy",
        "label": "numpy",
        "category": "python",
        "fn": lambda: _try_import("numpy"),
    },
    {
        "id": "cv2",
        "label": "OpenCV (cv2)",
        "category": "python",
        "fn": lambda: _try_import("cv2"),
    },
    {
        "id": "PIL",
        "label": "Pillow (PIL)",
        "category": "python",
        "fn": lambda: _try_import("PIL"),
    },
    {
        "id": "requests",
        "label": "requests",
        "category": "python",
        "fn": lambda: _try_import("requests"),
    },
    {
        "id": "tflite_runtime",
        "label": "tflite_runtime",
        "category": "python",
        "fn": lambda: _try_import("tflite_runtime"),
        "detail": lambda: "import OK" if _try_import("tflite_runtime") else "not installed",
    },

    # ---- system libraries ----
    {
        "id": "libedgetpu_so",
        "label": "libedgetpu.so.1",
        "category": "system",
        "fn": lambda: _libedgetpu_path() is not None,
        "detail": lambda: _libedgetpu_path() or "missing",
    },

    # ---- Coral USB hardware ----
    {
        "id": "coral_usb",
        "label": "Coral USB (lsusb)",
        "category": "hardware",
        "fn": lambda: "1a6e:089a" in _run(["lsusb"])[1] or "18d1:9302" in _run(["lsusb"])[1],
        "detail": lambda: _coral_detail(),
    },

    # ---- project files ----
    {
        "id": "api_key",
        "label": "OpenAI API key (api.txt)",
        "category": "project",
        "fn": lambda: (ROOT / "api.txt").exists() and len((ROOT / "api.txt").read_text().strip()) > 30,
    },
    {
        "id": "labels",
        "label": "Class labels (coco1.txt)",
        "category": "project",
        "fn": lambda: (ROOT / "labels" / "coco1.txt").exists(),
    },
    {
        "id": "models",
        "label": "TFLite detection model",
        "category": "models",
        "fn": lambda: bool(_model_files()),
        "detail": lambda: ", ".join(str(p.relative_to(ROOT)) for p in _model_files()) or "none",
    },

    # ---- environment ----
    {
        "id": "display",
        "label": "DISPLAY / WAYLAND_DISPLAY",
        "category": "environment",
        "fn": lambda: bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        "detail": lambda: os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or "(unset)",
    },
    {
        "id": "disk_space",
        "label": "Disk ≥ 500 MB free",
        "category": "environment",
        "fn": lambda: shutil.disk_usage("/").free >= 500_000_000,
        "detail": lambda: f"{shutil.disk_usage('/').free / 1e9:.1f} GB free",
    },
    {
        "id": "camera",
        "label": "Camera (/dev/video*)",
        "category": "environment",
        "fn": lambda: bool(list(Path("/dev").glob("video*"))),
        "detail": lambda: ", ".join(p.name for p in Path("/dev").glob("video*")) or "none",
    },
]


def _coral_detail() -> str:
    _, out = _run(["lsusb"])
    for line in out.splitlines():
        if "1a6e:089a" in line:
            return "1a6e:089a (uninitialised — normal)"
        if "18d1:9302" in line:
            return "18d1:9302 (active)"
    return "not detected"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    quick = "--quick" in sys.argv
    passed = 0
    failed = 0
    skipped = 0

    print(f"YOLOv11 + Coral Edge TPU – Dependency Check")
    print(f"Python: {sys.version}")
    print(f"Root:   {ROOT}")
    print()

    categories: dict[str, list] = {}
    for c in CHECKS:
        categories.setdefault(c["category"], []).append(c)

    for cat, checks in categories.items():
        print(f"── {cat} ──")
        for c in checks:
            if quick and c["category"] == "models":
                skipped += 1
                print(f"  ⊘ {c['label']}: SKIPPED (--quick)")
                continue
            try:
                ok = c["fn"]()
            except Exception as exc:
                ok = False
                detail = f"error: {exc}"
            if ok:
                passed += 1
                detail = c.get("detail", lambda: "OK")()
                print(f"  ✓ {c['label']}  ({detail})")
            else:
                failed += 1
                detail = c.get("detail", lambda: "FAIL")()
                print(f"  ✗ {c['label']}  ({detail})")
        print()

    print(f"{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    if failed:
        print("\nFix the failed checks above before running the GUI.")
        return 1
    print("All checks passed. Ready to run: ./webui/launch_webui.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
