"""GET /api/history — unified filterable timeline (detection, inventory_change,
recipe_cooked, system events all in one table, filtered by query params).
"""

import json

from flask import Blueprint, jsonify, request

import db

bp = Blueprint("history", __name__)


@bp.route("/api/history", methods=["GET"])
def get_history():
    type_ = request.args.get("type")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    q = request.args.get("q")
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))

    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, type, timestamp, summary, payload FROM history_events "
        "WHERE (:type IS NULL OR type = :type) "
        "AND (:date_from IS NULL OR timestamp >= :date_from) "
        "AND (:date_to IS NULL OR timestamp <= :date_to) "
        "AND (:q IS NULL OR summary LIKE '%' || :q || '%') "
        "ORDER BY timestamp DESC LIMIT :limit OFFSET :offset",
        {
            "type": type_, "date_from": date_from, "date_to": date_to,
            "q": q, "limit": limit, "offset": offset,
        },
    ).fetchall()

    events = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except (TypeError, json.JSONDecodeError):
            d["payload"] = {}
        events.append(d)
    return jsonify({"events": events})
