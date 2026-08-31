"""GET /api/recipes (cached), POST /api/recipes/generate (fresh GPT call),
GET /api/recipes/<rank>/projection (live pre-cook preview),
POST /api/recipes/<rank>/confirm ("Confirm & Start Cooking" — deducts
inventory, logs a recipe_cooked history event),
GET /api/recipes/<rank>/instructions (GPT-generated cooking steps, cached
per batch so reselecting the same recipe doesn't re-hit GPT).
"""

import json

from flask import Blueprint, jsonify

import db
import recipe as recipe_engine

bp = Blueprint("recipes", __name__)


def _current_inventory_rows():
    conn = db.get_conn()
    return conn.execute(
        "SELECT id, name, category, quantity, unit FROM inventory_items ORDER BY name"
    ).fetchall()


def _latest_batch():
    conn = db.get_conn()
    row = conn.execute(
        "SELECT id, generated_at, recipes_json FROM recipe_batches ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "generated_at": row["generated_at"],
        "recipes": json.loads(row["recipes_json"]).get("recipes", []),
    }


@bp.route("/api/recipes", methods=["GET"])
def get_recipes():
    batch = _latest_batch()
    if batch is None:
        return jsonify({"recipes": [], "generated_at": None})
    return jsonify(batch)


def generate_and_store() -> dict:
    """Fresh GPT call from current inventory, triggered only by "Regenerate
    from current inventory" (POST /api/recipes/generate). Logs a system
    event on failure, then re-raises."""
    rows = _current_inventory_rows()
    inventory = [
        {"name": r["name"], "category": r["category"], "quantity": r["quantity"], "unit": r["unit"]}
        for r in rows
    ]
    try:
        result = recipe_engine.generate_recipes(inventory)
    except Exception as e:
        db.log_event("system", "Recipe generation failed", {"event": "gpt_unreachable", "error": str(e)})
        raise

    conn = db.get_conn()
    conn.execute(
        "INSERT INTO recipe_batches (inventory_snapshot, recipes_json) VALUES (?, ?)",
        (json.dumps(inventory), json.dumps(result)),
    )
    conn.commit()
    return result


@bp.route("/api/recipes/generate", methods=["POST"])
def post_generate():
    try:
        result = generate_and_store()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


def _find_recipe(rank: int):
    batch = _latest_batch()
    if batch is None:
        return None
    for r in batch["recipes"]:
        if r.get("rank") == rank:
            return r
    return None


@bp.route("/api/recipes/<int:rank>/projection", methods=["GET"])
def get_projection(rank):
    r = _find_recipe(rank)
    if r is None:
        return jsonify({"ok": False, "error": "recipe not found in latest batch"}), 404

    conn = db.get_conn()
    projected = []
    for ing in r.get("ready_ingredients", []):
        row = conn.execute(
            "SELECT quantity, unit FROM inventory_items WHERE name = ?", (ing["name"],)
        ).fetchone()
        current = row["quantity"] if row else 0
        after = max(0, current - ing["amount_used"])
        projected.append({
            "name": ing["name"], "current": current, "projected": after,
            "unit": row["unit"] if row else ing.get("unit", "pcs"),
        })
    return jsonify({
        "recipe_name": r["name"], "match_percentage": r["match_percentage"],
        "projected_stock": projected,
    })


@bp.route("/api/recipes/<int:rank>/confirm", methods=["POST"])
def confirm_cook(rank):
    r = _find_recipe(rank)
    if r is None:
        return jsonify({"ok": False, "error": "recipe not found in latest batch"}), 404

    conn = db.get_conn()
    deducted = []
    for ing in r.get("ready_ingredients", []):
        row = conn.execute(
            "SELECT id, quantity FROM inventory_items WHERE name = ?", (ing["name"],)
        ).fetchone()
        if row is None:
            continue
        after = max(0, row["quantity"] - ing["amount_used"])
        conn.execute(
            "UPDATE inventory_items SET quantity = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
            (after, row["id"]),
        )
        db.record_snapshot(conn, ing["name"])
        deducted.append({"name": ing["name"], "amount": ing["amount_used"], "unit": ing.get("unit", "pcs")})
    conn.commit()

    db.log_event(
        "recipe_cooked",
        f"Cooked {r['name']}",
        {"recipe_name": r["name"], "match_percentage": r["match_percentage"], "deducted": deducted},
    )
    return jsonify({"ok": True, "deducted": deducted})


_INSTRUCTIONS_CACHE: dict[tuple[int, int], dict] = {}


@bp.route("/api/recipes/<int:rank>/instructions", methods=["GET"])
def get_instructions(rank):
    batch = _latest_batch()
    if batch is None:
        return jsonify({"ok": False, "error": "no recipes generated yet"}), 404
    r = next((x for x in batch["recipes"] if x.get("rank") == rank), None)
    if r is None:
        return jsonify({"ok": False, "error": "recipe not found in latest batch"}), 404

    cache_key = (batch["id"], rank)
    cached = _INSTRUCTIONS_CACHE.get(cache_key)
    if cached is not None:
        return jsonify({"ok": True, "recipe_name": r["name"], **cached})

    try:
        result = recipe_engine.generate_instructions(r)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    _INSTRUCTIONS_CACHE[cache_key] = result
    return jsonify({"ok": True, "recipe_name": r["name"], **result})
