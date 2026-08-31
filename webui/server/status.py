"""Live status flags shown in the sidebar: Camera Connected, YOLOv11
Active, ChatGPT Synced. These are real checks, not decorative —
camera/yolo reflect camera_stream.py's actual state; GPT is validated
once at startup then silently revalidated every ~60s in the background.
"""

import threading
from typing import Optional

import requests

import db
import event_bus
from config import API_KEY_FILE, GPT_STATUS_POLL_SECONDS

_state = {
    "camera_connected": False,
    "yolo_active": False,
    "gpt_synced": False,
    "backend": None,
    "model": None,
    # True once predict() has failed several times in a row — distinct from
    # yolo_active (which only reflects whether the model *loaded*): the
    # model can load fine and still stop producing results mid-session if
    # the Coral USB connection glitches. This is what makes that visible
    # instead of silent (see camera_stream.py's _infer_loop).
    "inference_stalled": False,
    # True once a sustained streak of torn/corrupted camera frames forced
    # the corruption filter to give up and let a possibly-still-bad frame
    # through (see camera_stream.py's _reject_if_corrupt) — a hardware-level
    # USB link issue, previously only visible in the server's own log.
    "camera_degraded": False,
}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kwargs) -> dict:
    changed = {}
    with _lock:
        for k, v in kwargs.items():
            if _state.get(k) != v:
                changed[k] = v
                _state[k] = v
    if changed:
        event_bus.publish("status_update", get_status())
    return changed


def set_camera_connected(connected: bool) -> None:
    changed = _set(camera_connected=connected)
    if "camera_connected" in changed:
        db.log_event(
            "system",
            "Camera connected" if connected else "Camera disconnected",
            {"event": "camera_connected" if connected else "camera_disconnected"},
        )


def set_yolo_active(active: bool, backend: Optional[str] = None, model: Optional[str] = None) -> None:
    kwargs = {"yolo_active": active}
    if backend is not None:
        kwargs["backend"] = backend
    if model is not None:
        kwargs["model"] = model
    changed = _set(**kwargs)
    if "yolo_active" in changed:
        db.log_event(
            "system",
            "Model load succeeded" if active else "Model load failed",
            {"event": "model_load_success" if active else "model_load_failed",
             "backend": backend, "model": model},
        )


def set_inference_stalled(stalled: bool) -> None:
    changed = _set(inference_stalled=stalled)
    if "inference_stalled" in changed:
        db.log_event(
            "system",
            "Inference stalled (Coral USB not producing results)" if stalled else "Inference recovered",
            {"event": "inference_stalled" if stalled else "inference_recovered"},
        )


def set_camera_degraded(degraded: bool) -> None:
    changed = _set(camera_degraded=degraded)
    if "camera_degraded" in changed:
        db.log_event(
            "system",
            "Camera USB link degraded (sustained frame corruption)" if degraded else "Camera link recovered",
            {"event": "camera_degraded" if degraded else "camera_recovered"},
        )


def _load_api_key() -> Optional[str]:
    try:
        key = API_KEY_FILE.read_text().strip()
        return key or None
    except OSError:
        return None


def check_gpt_reachable(timeout: float = 5.0) -> bool:
    key = _load_api_key()
    if not key:
        return False
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def _gpt_poll_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        reachable = check_gpt_reachable()
        changed = _set(gpt_synced=reachable)
        if "gpt_synced" in changed:
            db.log_event(
                "system",
                "ChatGPT reachable" if reachable else "ChatGPT unreachable",
                {"event": "gpt_reachable" if reachable else "gpt_unreachable"},
            )
        stop.wait(GPT_STATUS_POLL_SECONDS)


_poll_thread: Optional[threading.Thread] = None
_poll_stop: Optional[threading.Event] = None


def start_gpt_polling() -> None:
    global _poll_thread, _poll_stop
    if _poll_thread is not None and _poll_thread.is_alive():
        return
    _poll_stop = threading.Event()
    _poll_thread = threading.Thread(target=_gpt_poll_loop, args=(_poll_stop,), daemon=True)
    _poll_thread.start()
