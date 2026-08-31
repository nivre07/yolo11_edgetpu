"""GET /api/analytics/* — low-stock+shopping-list, ingredient usage
frequency, category breakdown, detection/model performance, 7-day stock
trend. Usage-frequency is computed Python-side (Counter over
recipe_cooked payloads) rather than via SQLite JSON1, since JSON1
availability varies by build and this is a tiny single-user dataset.
"""

import json
from collections import Counter

from flask import Blueprint, jsonify, request

import db

bp = Blueprint("analytics", __name__)


@bp.route("/api/analytics/low-stock", methods=["GET"])
def low_stock():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, name, category, quantity, unit, low_stock_threshold FROM inventory_items "
        "WHERE quantity <= low_stock_threshold "
        "ORDER BY (quantity * 1.0 / NULLIF(low_stock_threshold, 0)) ASC"
    ).fetchall()
    alerts = []
    for r in rows:
        d = dict(r)
        d["suggested_restock"] = (r["low_stock_threshold"] or 1) * 2
        alerts.append(d)
    return jsonify({"alerts": alerts})


@bp.route("/api/analytics/usage-frequency", methods=["GET"])
def usage_frequency():
    conn = db.get_conn()
    rows = conn.execute("SELECT payload FROM history_events WHERE type = 'recipe_cooked'").fetchall()
    counter = Counter()
    for (payload,) in rows:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for d in data.get("deducted", []):
            counter[d.get("name", "?")] += 1
    return jsonify({"usage": [{"name": n, "count": c} for n, c in counter.most_common()]})


@bp.route("/api/analytics/category-breakdown", methods=["GET"])
def category_breakdown():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT category, COUNT(*) AS item_count, SUM(quantity) AS total_quantity "
        "FROM inventory_items GROUP BY category"
    ).fetchall()
    return jsonify({"categories": [dict(r) for r in rows]})


@bp.route("/api/analytics/performance", methods=["GET"])
def performance():
    conn = db.get_conn()
    sessions = conn.execute(
        "SELECT session_id, COUNT(*) frames, SUM(num_detections) total_detections, "
        "AVG(avg_confidence) avg_confidence, AVG(inference_ms) avg_latency_ms, "
        "MIN(timestamp) session_start, MAX(timestamp) session_end "
        "FROM inference_frames GROUP BY session_id ORDER BY session_start DESC LIMIT 20"
    ).fetchall()

    session_id = request.args.get("session_id")
    series = []
    if session_id:
        series_rows = conn.execute(
            "SELECT timestamp, inference_ms FROM inference_frames "
            "WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ).fetchall()
        series = [dict(r) for r in series_rows]

    return jsonify({"sessions": [dict(r) for r in sessions], "latency_series": series})


@bp.route("/api/analytics/stock-trend", methods=["GET"])
def stock_trend():
    item_id = request.args.get("item_id")
    conn = db.get_conn()
    if item_id:
        rows = conn.execute(
            "SELECT snapshot_date, quantity FROM inventory_snapshots "
            "WHERE item_id = ? AND snapshot_date >= date('now', '-7 days') "
            "ORDER BY snapshot_date ASC",
            (item_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT snapshot_date, SUM(quantity) AS quantity FROM inventory_snapshots "
            "WHERE snapshot_date >= date('now', '-7 days') "
            "GROUP BY snapshot_date ORDER BY snapshot_date ASC"
        ).fetchall()
    return jsonify({"trend": [dict(r) for r in rows]})
