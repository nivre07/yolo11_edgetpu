"""/api/inventory* — list, additive sync from Detected Items, manual adjust.
/api/browse — server-side directory listing, the web analog of Tkinter's
native file dialog (browsers can't return real filesystem paths).
"""

from pathlib import Path

from flask import Blueprint, jsonify, request

import config as cfg
import db
from camera_sources import IMG_EXTS, VID_EXTS

bp = Blueprint("inventory", __name__)


@bp.route("/api/inventory", methods=["GET"])
def list_inventory():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT id, name, category, quantity, unit, low_stock_threshold, updated_at "
        "FROM inventory_items ORDER BY category, name"
    ).fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault(r["category"], []).append(dict(r))
    return jsonify({"categories": grouped})


@bp.route("/api/inventory/sync", methods=["POST"])
def sync_inventory():
    """Additive save from Detected Items — new stock on top of existing, per item."""
    payload = request.get_json(force=True, silent=True) or {}
    items = payload.get("items", [])
    if not items:
        return jsonify({"ok": False, "error": "no items provided"}), 400

    conn = db.get_conn()
    changes = []
    for item in items:
        name = str(item.get("name", "")).strip()
        qty = float(item.get("qty", 0))
        category = str(item.get("category", "Other"))
        if not name or qty <= 0:
            continue
        row = conn.execute("SELECT id, quantity FROM inventory_items WHERE name = ?", (name,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO inventory_items (name, category, quantity, unit) VALUES (?, ?, ?, 'pcs')",
                (name, category, qty),
            )
            before, after = 0.0, qty
        else:
            before = row["quantity"]
            after = before + qty
            conn.execute(
                "UPDATE inventory_items SET quantity = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
                (after, row["id"]),
            )
        changes.append({"name": name, "delta": qty, "unit": "pcs", "before": before, "after": after})
        db.record_snapshot(conn, name)
    conn.commit()

    if changes:
        db.log_event(
            "inventory_change",
            f"Synced {len(changes)} item(s) from detection",
            {"changes": changes, "reason": "sync"},
        )

    return jsonify({"ok": True, "changes": changes})


@bp.route("/api/inventory", methods=["DELETE"])
def clear_inventory():
    conn = db.get_conn()
    rows = conn.execute("SELECT name, quantity FROM inventory_items").fetchall()
    if not rows:
        return jsonify({"ok": True, "cleared": 0})

    conn.execute("DELETE FROM inventory_items")
    conn.commit()
    db.log_event(
        "inventory_change",
        f"Cleared all inventory ({len(rows)} item(s))",
        {"changes": [{"name": r["name"], "delta": -r["quantity"], "before": r["quantity"], "after": 0}
                      for r in rows], "reason": "clear_all"},
    )
    return jsonify({"ok": True, "cleared": len(rows)})


@bp.route("/api/inventory/<int:item_id>", methods=["PATCH", "DELETE"])
def modify_item(item_id):
    conn = db.get_conn()
    row = conn.execute("SELECT name, quantity FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "not found"}), 404

    if request.method == "DELETE":
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
        conn.commit()
        db.log_event(
            "inventory_change", f"Removed {row['name']}",
            {"changes": [{"name": row["name"], "delta": -row["quantity"],
                          "before": row["quantity"], "after": 0}], "reason": "manual_adjust"},
        )
        return jsonify({"ok": True})

    payload = request.get_json(force=True, silent=True) or {}
    if "quantity" not in payload:
        return jsonify({"ok": False, "error": "quantity required"}), 400
    before = row["quantity"]
    after = float(payload["quantity"])
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
        (after, item_id),
    )
    db.record_snapshot(conn, row["name"])
    conn.commit()
    db.log_event(
        "inventory_change", f"Adjusted {row['name']}",
        {"changes": [{"name": row["name"], "delta": after - before,
                      "before": before, "after": after}], "reason": "manual_adjust"},
    )
    return jsonify({"ok": True})


@bp.route("/api/browse", methods=["GET"])
def browse():
    req_path = request.args.get("path") or str(cfg.REPO_ROOT / "inference")
    p = Path(req_path).expanduser().resolve()
    try:
        if not p.is_dir():
            p = p.parent
        entries = []
        media_exts = IMG_EXTS | VID_EXTS
        for child in sorted(p.iterdir()):
            if child.is_dir():
                entries.append({"name": child.name, "path": str(child), "type": "dir"})
            elif child.suffix.lower() in media_exts:
                entries.append({"name": child.name, "path": str(child), "type": "file"})
        return jsonify({"path": str(p), "parent": str(p.parent), "entries": entries})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
