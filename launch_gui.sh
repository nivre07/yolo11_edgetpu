#!/usr/bin/env bash
# Launcher for the YOLOv11 + Coral GUI. Used by launch_gui.desktop.
set -e
cd "$(dirname "$(readlink -f "$0")")"

# The Coral tflite_runtime build used by this project requires Python 3.9.
VENV=.venv39
if [ ! -f "$VENV/bin/activate" ]; then
    echo "error: .venv39 is missing; run ./restore_env.sh first" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$VENV/bin/activate"
python src/gui.py "$@"
