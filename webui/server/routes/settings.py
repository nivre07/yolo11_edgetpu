"""GET/POST /api/settings — same settings.json contract the Tkinter GUI
uses, so either UI can be used interchangeably without orphaning config.
GET /api/models — model choices from the model registry.
"""

from flask import Blueprint, jsonify, request

import models as model_registry
import settings_store
from db import clear_database

bp = Blueprint("settings", __name__)


@bp.route("/api/settings", methods=["GET"])
def get_settings():
    data = settings_store.load_settings()
    if data["model"] not in model_registry.CHOICES:
        data["model"] = model_registry.DEFAULT
    return jsonify(data)


@bp.route("/api/settings", methods=["POST"])
def post_settings():
    payload = request.get_json(force=True, silent=True) or {}
    merged = settings_store.load_settings()
    for key in ("confidence", "last_file_path", "model", "source", "usb_index"):
        if key in payload:
            merged[key] = payload[key]

    if merged["source"] not in ("file", "usb", "picam"):
        return jsonify({"ok": False, "error": "invalid source"}), 400
    if merged["model"] not in model_registry.CHOICES:
        # Self-heal a stale on-disk model (e.g. a model file removed since
        # last session) rather than blocking unrelated setting changes;
        # only reject when the caller explicitly requested the bad value.
        if "model" in payload:
            return jsonify({"ok": False, "error": "invalid model"}), 400
        merged["model"] = model_registry.DEFAULT
    try:
        merged["confidence"] = float(merged["confidence"])
        merged["usb_index"] = int(merged["usb_index"])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid confidence/usb_index"}), 400

    settings_store.save_settings(merged)
    return jsonify({"ok": True, **merged})


@bp.route("/api/settings/reset", methods=["POST"])
def post_settings_reset():
    defaults = dict(settings_store.DEFAULTS)
    if not defaults["model"] or defaults["model"] not in model_registry.CHOICES:
        defaults["model"] = model_registry.DEFAULT
    settings_store.save_settings(defaults)
    return jsonify({"ok": True, **defaults})


@bp.route("/api/database/clear", methods=["POST"])
def post_database_clear():
    deleted = clear_database()
    return jsonify({"ok": True, "deleted": deleted})


@bp.route("/api/models", methods=["GET"])
def get_models():
    model_registry.refresh()
    return jsonify({"choices": list(model_registry.CHOICES), "default": model_registry.DEFAULT})
