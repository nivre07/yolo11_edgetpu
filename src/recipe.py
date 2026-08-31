"""Recipe suggestions via OpenAI Chat Completions — structured JSON output.

Uses `requests` directly (no `openai` SDK) so the ABI-locked tflite_runtime
venv doesn't need the extra weight. Key lives in api.txt at the project root.

generate_recipes() is the structured engine the web backend consumes
(ranked cards: match %, ready/missing ingredients). suggest() is a thin
legacy wrapper kept so the Tkinter GUI's "Ask GPT" button keeps working
unchanged, formatting the new engine's top match as free text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
API_KEY_FILE = ROOT / "api.txt"
MODEL = "gpt-4o-mini"
ENDPOINT = "https://api.openai.com/v1/chat/completions"

MIN_RECIPES = 5
MAX_RECIPES = 10

RECIPE_SCHEMA = {
    "name": "recipe_recommendations",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "recipes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rank": {"type": "integer"},
                        "name": {"type": "string"},
                        "category": {"type": "string", "enum": ["meat", "vegetable", "seafood", "egg"]},
                        "match_percentage": {"type": "integer"},
                        "primary_ingredients": {"type": "array", "items": {"type": "string"}},
                        "ready_ingredients": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "amount_used": {"type": "number"},
                                    "unit": {"type": "string"},
                                },
                                "required": ["name", "amount_used", "unit"],
                                "additionalProperties": False,
                            },
                        },
                        "missing_ingredients": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "rank", "name", "category", "match_percentage",
                        "primary_ingredients", "ready_ingredients", "missing_ingredients",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["recipes"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You are a Filipino cuisine recipe recommendation engine embedded in a smart refrigerator inventory system. You will be given a JSON list of currently available ingredients (name, category, quantity, unit). Recommend Filipino dishes cookable from some subset of these.

Rules, follow exactly:
1. Only authentic, well-known Filipino dishes. Never other cuisines.
2. Return between 5 and 10 recipes -- never fewer than 5, never more than 10.
3. Prioritize recipes whose primary ingredient is chicken, beef, or pork -- rank these above vegetable-forward dishes whenever the inventory has a usable quantity of any meat or egg. If there is no chicken/beef/pork/egg in inventory at all, rank the best vegetable-forward Filipino dishes as top matches instead.
4. Each recipe must have 1-4 primary ingredients -- never design a recipe meant to consume "everything in the fridge." Across the batch, vary primary ingredients: no more than 2 recipes may share the same primary-ingredient set, and no single recipe's ready_ingredients may include more than half of the distinct items in the given inventory.
5. For each recipe list ready_ingredients (items from the given inventory this dish needs, with a realistic amount_used/unit for one typical preparation) and missing_ingredients (needed items NOT in inventory, by name only).
6. match_percentage = ready/(ready+missing) required ingredients x 100, rounded.
7. Assign rank 1 (best) through N per rules 3 and 6.
Respond only via the given JSON schema."""


def _key() -> Optional[str]:
    try:
        key = API_KEY_FILE.read_text().strip()
        return key or None
    except OSError:
        return None


def _post_chat(system_prompt: str, user_content: str, schema: dict, timeout: float) -> dict:
    key = _key()
    if not key:
        raise RuntimeError("no OpenAI API key found (api.txt missing or empty)")

    r = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_schema", "json_schema": schema},
            "temperature": 0.7,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def _call_gpt(inventory: list[dict], corrective_note: Optional[str] = None, timeout: float = 30.0) -> dict:
    user_content = "Current refrigerator inventory:\n" + json.dumps(inventory)
    if corrective_note:
        user_content += f"\n\n{corrective_note}"
    return _post_chat(SYSTEM_PROMPT, user_content, RECIPE_SCHEMA, timeout)


def generate_recipes(inventory: list[dict], timeout: float = 30.0) -> dict:
    """Return {"recipes": [...]} -- 5-10 ranked Filipino dishes for the given
    inventory (list of {"name","category","quantity","unit"} dicts).

    Retries once with a corrective note if the model returns a recipe
    count outside [MIN_RECIPES, MAX_RECIPES] -- strict-mode JSON schemas
    don't reliably enforce array length across API versions, so this is
    enforced primarily via the prompt with this as a backstop.
    """
    if not inventory:
        return {"recipes": []}

    result = _call_gpt(inventory, timeout=timeout)
    count = len(result.get("recipes", []))
    if not (MIN_RECIPES <= count <= MAX_RECIPES):
        note = f"You returned {count} recipes; return between {MIN_RECIPES} and {MAX_RECIPES}."
        try:
            result = _call_gpt(inventory, corrective_note=note, timeout=timeout)
        except Exception:
            pass  # keep the first (out-of-range) result rather than fail outright
    return result


INSTRUCTIONS_SCHEMA = {
    "name": "recipe_instructions",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["steps"],
        "additionalProperties": False,
    },
}

INSTRUCTIONS_SYSTEM_PROMPT = """You are a Filipino cuisine cooking instructor. You will be given the name of a Filipino dish plus its ready and missing ingredients (assume missing ingredients have already been acquired). Write clear, ordered step-by-step cooking instructions for preparing it. Each step should be one concise, actionable sentence. Respond only via the given JSON schema."""


def generate_instructions(recipe: dict, timeout: float = 30.0) -> dict:
    """Return {"steps": [...]} -- ordered cooking steps for one recipe dict
    (as produced by generate_recipes: name, ready_ingredients, missing_ingredients)."""
    user_content = "Recipe:\n" + json.dumps({
        "name": recipe.get("name"),
        "ready_ingredients": [i.get("name") for i in recipe.get("ready_ingredients", [])],
        "missing_ingredients": recipe.get("missing_ingredients", []),
    })
    return _post_chat(INSTRUCTIONS_SYSTEM_PROMPT, user_content, INSTRUCTIONS_SCHEMA, timeout)


def suggest(items: list[str], timeout: float = 20.0) -> str:
    """Legacy free-text wrapper for the Tkinter GUI's "Ask GPT" button.

    Builds a minimal inventory from a flat list of item names (no
    category/quantity info available from that flow) and formats the
    new engine's top match as a short human-readable string.
    """
    if not items:
        return "(no items detected)"
    inventory = [{"name": name, "category": "Other", "quantity": 1, "unit": "pcs"} for name in items]
    try:
        result = generate_recipes(inventory, timeout=timeout)
    except Exception as e:
        return f"[recipe error] {e}"

    recipes = result.get("recipes", [])
    if not recipes:
        return "(no matching Filipino dish found for these ingredients)"
    top = min(recipes, key=lambda r: r.get("rank", 999))
    ready = ", ".join(r["name"] for r in top.get("ready_ingredients", []))
    missing = ", ".join(top.get("missing_ingredients", []))
    lines = [f"{top['name']} ({top.get('match_percentage', '?')}% match)"]
    if ready:
        lines.append(f"Ready: {ready}")
    if missing:
        lines.append(f"Missing: {missing}")
    return "\n".join(lines)
