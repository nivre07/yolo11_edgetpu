"""GET /video_feed (MJPEG), GET /events (SSE), POST /api/camera/start|stop."""

import json
import queue
import time

from flask import Blueprint, Response, jsonify, stream_with_context

import event_bus
import status
from camera_stream import worker

bp = Blueprint("stream", __name__)


@bp.route("/video_feed")
def video_feed():
    def gen():
        last_jpeg = None
        while True:
            jpeg = worker.get_latest_jpeg()
            if jpeg is None:
                time.sleep(0.05)
                continue
            # Identity check (not byte comparison): _latest_jpeg is always
            # reassigned to a fresh bytes object each inference pass, so
            # this cheaply detects "nothing new since last poll" and skips
            # re-sending an unchanged frame.
            if jpeg is not last_jpeg:
                last_jpeg = jpeg
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            # Without this sleep, once a frame exists this loop busy-spins
            # re-fetching the lock-protected latest frame as fast as the
            # CPU allows — starving the inference thread's GIL time (which
            # shares worker._lock) and flooding the browser's MJPEG decoder
            # with back-to-back parts, both of which were confirmed causes
            # of severely inflated inference latency and periodic torn/
            # half-updated frames in the live preview.
            time.sleep(1 / 30)

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@bp.route("/events")
def events():
    q = event_bus.subscribe()

    @stream_with_context
    def gen():
        yield f"event: status_update\ndata: {json.dumps(status.get_status())}\n\n"
        last_keepalive = time.monotonic()
        try:
            while True:
                try:
                    payload = q.get(timeout=5)
                    yield payload
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_keepalive >= 15:
                    yield ": keepalive\n\n"
                    last_keepalive = now
        finally:
            event_bus.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream")


@bp.route("/api/camera/start", methods=["POST"])
def camera_start():
    try:
        result = worker.start()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.route("/api/camera/stop", methods=["POST"])
def camera_stop():
    worker.stop()
    return jsonify({"ok": True})
