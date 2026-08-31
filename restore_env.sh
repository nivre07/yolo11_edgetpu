#!/usr/bin/env bash
# Recreate the Python 3.9 environment used by both application interfaces.
# Virtual environments are intentionally not committed because their paths
# and compiled binaries are tied to the machine that created them.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== .venv39 (Python 3.9.12, Coral-compatible — used by src/gui.py and webui/server/app.py) ==="
if command -v pyenv >/dev/null && pyenv versions --bare | grep -qx "3.9.12"; then
    PY39="$(pyenv root)/versions/3.9.12/bin/python3.9"
elif command -v python3.9 >/dev/null; then
    PY39="$(command -v python3.9)"
else
    echo "No Python 3.9 found. Install it (e.g. 'pyenv install 3.9.12') and re-run this script." >&2
    PY39=""
fi

if [ -z "$PY39" ]; then
    exit 1
fi

"$PY39" -m venv .venv39
.venv39/bin/pip install --upgrade pip
.venv39/bin/pip install -r requirements.txt -r requirements-coral.txt

echo
echo "Environment restored. Verify it with:"
echo "  .venv39/bin/python check_deps.py"
echo
echo "The Coral USB runtime is a system dependency. If it is missing, install:"
echo "  sudo apt install libedgetpu1-std"
