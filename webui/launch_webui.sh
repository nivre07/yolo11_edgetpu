#!/usr/bin/env bash
# Launcher for the web dashboard. Starts the Flask backend, waits for it
# to be ready, then opens it in full Chromium kiosk mode: fullscreen, zero
# window decoration — the dashboard's own nav bar is the first pixel row
# on screen, like the old Tkinter GUI's maximized window. Since kiosk mode
# has no visible close button, the dashboard has its own in-app "X" that
# calls POST /api/app/close, which reads the Chromium PID this script
# records below and kills it, then exits the server too.
set -e
cd "$(dirname "$(readlink -f "$0")")/.."

PORT="${1:-5000}"
VENV=.venv39
if [ ! -f "$VENV/bin/activate" ]; then
    echo "error: $VENV not found — the web backend needs the Coral-compatible" >&2
    echo "Python 3.9 venv (it imports tflite_runtime/detector.py directly)." >&2
    exit 1
fi

PID_FILE="$HOME/.config/yolo11_coral_gui/webui_chrome.pid"
SERVER_PID_FILE="$HOME/.config/yolo11_coral_gui/webui_server.pid"
mkdir -p "$(dirname "$PID_FILE")"

# shellcheck disable=SC1090
source "$VENV/bin/activate"

# The Coral USB delegate can hit a fatal, unrecoverable libedgetpu USB
# error (abort() inside its C++ driver — no Python exception, nothing to
# catch) on flaky USB link resets, which are a known, root-caused hardware/
# cable issue on this device (see CHECKPOINT.md), not a code bug. Rather
# than leaving the whole dashboard dead until someone notices and manually
# relaunches, run the backend under a restart loop: any crash (non-zero
# exit) relaunches it after a short backoff; a clean exit (status 0, which
# is how /api/app/close's os._exit(0) signals an intentional shutdown)
# breaks the loop instead of respawning. The loop runs in a backgrounded
# subshell, so its SERVER_PID isn't visible to this parent shell — the pid
# file is how the cleanup trap below finds the *current* backend process.
run_server() {
    while true; do
        python webui/server/app.py --port "$PORT" &
        SERVER_PID=$!
        echo "$SERVER_PID" > "$SERVER_PID_FILE"
        set +e
        wait "$SERVER_PID"
        EXIT_CODE=$?
        set -e
        if [ "$EXIT_CODE" -eq 0 ]; then
            break
        fi
        echo "$(date -Iseconds) [launch_webui] backend exited with code $EXIT_CODE (crash) — restarting in 2s" >&2
        sleep 2
    done
}
run_server &
RUN_SERVER_PID=$!
trap 'kill "$RUN_SERVER_PID" 2>/dev/null; kill "$(cat "$SERVER_PID_FILE" 2>/dev/null)" 2>/dev/null; rm -f "$PID_FILE" "$SERVER_PID_FILE"' EXIT

echo "Waiting for backend on 127.0.0.1:$PORT ..."
for _ in $(seq 1 50); do
    if curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

URL="http://127.0.0.1:$PORT/"

launch() {
    # --ozone-platform=x11: forces Chromium through the running XWayland
    # session instead of native Wayland. Confirmed on-device that without
    # this, Chromium has zero EWMH-manageable windows (`wmctrl -l` connects
    # fine but lists nothing), so the minimize button's wmctrl-based
    # iconify has no window to target.
    "$1" --ozone-platform=x11 --kiosk "$URL" >/dev/null 2>&1 &
    LAUNCH_PID=$!

    # $! is NOT reliable as the window's PID: Chromium enforces one instance
    # per profile, so if a window from this profile is already alive, this
    # invocation just hands off to it over IPC and exits quickly — $! would
    # then point at that short-lived launcher process, not the real window,
    # leaving close/minimize targeting a PID that's already gone. Confirmed
    # on-device: this silently broke both buttons after a few relaunches.
    # Poll wmctrl for the actual window instead, matched by page <title>.
    WIN_PID=""
    for _ in $(seq 1 25); do
        WIN_PID=$(DISPLAY=:0 wmctrl -lp 2>/dev/null | awk '/Food GPT Dashboard/ {print $3; exit}')
        [ -n "$WIN_PID" ] && break
        sleep 0.2
    done
    echo "${WIN_PID:-$LAUNCH_PID}" > "$PID_FILE"
}

if command -v chromium-browser >/dev/null 2>&1; then
    launch chromium-browser
elif command -v chromium >/dev/null 2>&1; then
    launch chromium
elif command -v google-chrome >/dev/null 2>&1; then
    launch google-chrome
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 &
else
    echo "No browser launcher found; open manually: $URL"
fi

wait $RUN_SERVER_PID
