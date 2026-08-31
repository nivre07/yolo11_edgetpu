"""Central paths/constants for the Flask backend."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = REPO_ROOT / "src"
WEBUI_DIR = REPO_ROOT / "webui"

CONFIG_DIR = Path.home() / ".config" / "yolo11_coral_gui"
DB_PATH = CONFIG_DIR / "inventory.db"
API_KEY_FILE = REPO_ROOT / "api.txt"
CHROME_PID_FILE = CONFIG_DIR / "webui_chrome.pid"

LABELS_PATH = REPO_ROOT / "labels" / "coco1.txt"

# Static assets whitelisted for direct serving (never serve server/ source).
STATIC_SUBDIRS = ("css", "js", "vendor")

GPT_STATUS_POLL_SECONDS = 60
STREAM_SAMPLE_SECONDS = 1.0
