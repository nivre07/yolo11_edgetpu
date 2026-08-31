"""Flask app entrypoint. Run directly: python webui/server/app.py [--port 5000]

Wires src/ (detector, models, recipe, camera_sources, settings_store) onto
sys.path so this backend imports the exact same detection/settings logic
the Tkinter GUI uses — one implementation, not two that can drift.
"""

import sys
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SERVER_DIR.parent.parent
_SRC_DIR = _REPO_ROOT / "src"

for _p in (str(_SERVER_DIR), str(_SRC_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from flask import Flask

import db as db_mod
import status as status_mod
from routes import analytics, history, inventory, pages, recipes, settings as settings_routes, status_api, stream


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.register_blueprint(pages.bp)
    app.register_blueprint(status_api.bp)
    app.register_blueprint(stream.bp)
    app.register_blueprint(inventory.bp)
    app.register_blueprint(settings_routes.bp)
    app.register_blueprint(history.bp)
    app.register_blueprint(analytics.bp)
    app.register_blueprint(recipes.bp)

    db_mod.init_db()
    status_mod.start_gpt_polling()
    return app


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = create_app()
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
