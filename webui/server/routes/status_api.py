"""GET /api/status — one-shot status snapshot.
POST /api/app/close — the dashboard's in-app "X" (kiosk mode has no OS
window controls): stops the camera/Coral worker cleanly, closes the
Chromium window and exits the server, the same way closing the old
Tkinter GUI's window ended that whole process.
POST /api/app/minimize — the dashboard's in-app "-": iconifies the
Chromium kiosk window via wmctrl (kiosk mode has no OS minimize button
either), matched by PID against the same CHROME_PID_FILE close_app()
reads, so the server and camera/detection keep running underneath.
"""

import os
import signal
import subprocess
import threading

from flask import Blueprint, jsonify

import status
from camera_stream import worker
from config import CHROME_PID_FILE

bp = Blueprint("status_api", __name__)


@bp.route("/api/status")
def get_status():
    return jsonify(status.get_status())


@bp.route("/api/app/minimize", methods=["POST"])
def minimize_app():
    try:
        pid = CHROME_PID_FILE.read_text().strip()
    except OSError:
        return jsonify({"ok": False, "error": "chrome pid file missing"}), 400

    try:
        out = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=3)
        win_id = None
        for line in out.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 3 and parts[2] == pid:
                win_id = parts[0]
                break
        if win_id is None:
            return jsonify({"ok": False, "error": "chromium window not found via wmctrl"}), 404
        subprocess.run(["wmctrl", "-i", "-r", win_id, "-b", "add,hidden"], timeout=3)
        return jsonify({"ok": True})
    except (OSError, subprocess.SubprocessError) as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/app/close", methods=["POST"])
def close_app():
    # Graceful first: joins the inference thread, releases the camera and
    # the Coral USB delegate. Safe no-op if the camera was never started.
    # Guards against os._exit(0) below killing the Coral delegate mid-
    # invoke(), which can leave its USB firmware state dirty and require
    # a physical replug to recover.
    worker.stop()

    try:
        pid = int(CHROME_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass  # window already gone / PID file missing — still shut the server down

    # Let this response flush back before the process exits.
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify({"ok": True})
