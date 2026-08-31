"""GET / and whitelisted static asset serving.

Never a blanket static_folder — that would happily serve server/ source
over HTTP. Only css/js/vendor paths are served.
"""

from flask import Blueprint, abort, send_from_directory

from config import STATIC_SUBDIRS, WEBUI_DIR

bp = Blueprint("pages", __name__)


@bp.route("/")
def index():
    return send_from_directory(WEBUI_DIR, "index.html")


@bp.route("/<path:subpath>")
def static_files(subpath):
    top = subpath.split("/")[0]
    if top not in STATIC_SUBDIRS:
        abort(404)
    return send_from_directory(WEBUI_DIR, subpath)
