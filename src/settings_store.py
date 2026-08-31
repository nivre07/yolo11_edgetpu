"""Read/write ~/.config/yolo11_coral_gui/settings.json.

Extracted out of gui.py so the Tkinter GUI and the web backend share one
implementation of this file's schema, instead of two that can drift.
Framework-agnostic (plain dicts) — the Tkinter GUI adapts these to/from
its tk.Variable objects; the web backend uses the dict directly.
"""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "yolo11_coral_gui" / "settings.json"

DEFAULTS = {
    "confidence": 0.35,
    "last_file_path": "",
    "model": None,
    "source": "usb",
    "usb_index": 0,
}


def load_settings() -> dict:
    """Return current settings merged with defaults; never raises.

    `model` validation against the live model registry is left to the
    caller (this module doesn't import models.py, to stay decoupled).
    """
    data = dict(DEFAULTS)
    try:
        raw = json.loads(SETTINGS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return data

    if isinstance(raw.get("confidence"), (int, float)):
        data["confidence"] = float(raw["confidence"])
    if isinstance(raw.get("last_file_path"), str) and raw["last_file_path"]:
        data["last_file_path"] = raw["last_file_path"]
    if isinstance(raw.get("model"), str) and raw["model"]:
        data["model"] = raw["model"]
    if raw.get("source") in ("file", "usb", "picam"):
        data["source"] = raw["source"]
    if isinstance(raw.get("usb_index"), int):
        data["usb_index"] = raw["usb_index"]
    return data


def save_settings(data: dict) -> None:
    """Write settings to disk. `data` must have the same keys as DEFAULTS."""
    payload = {
        "confidence": float(data["confidence"]),
        "last_file_path": str(data["last_file_path"]),
        "model": str(data["model"]),
        "source": str(data["source"]),
        "usb_index": int(data["usb_index"]),
    }
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2))
