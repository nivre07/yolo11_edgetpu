# YOLOv11 + Coral Edge TPU — Final Checkpoint

**Date:** 2026-06-02 | **Model:** deepseek-v4-pro
**Status:** ✅ FULLY OPERATIONAL — Coral Edge TPU inference active on Raspberry Pi 5

---

## 0. Session Update — 2026-07-28 (webui/ dashboard overhaul)

**Everything below this section (§1 onward) describes the old Tkinter GUI (`src/gui.py`) and is out of date.** The primary interface is now the web dashboard at `webui/`, launched via the desktop icon (`~/Desktop/food-gpt-dashboard.desktop` → `webui/launch_webui.sh` → Chromium `--kiosk`). See git log on branch `webui-redesign` for the full history since this checkpoint was written; a safety branch `backup/pre-dashboard-overhaul` was created before today's work in case anything needs reverting.

**What changed today** (17 commits on `webui-redesign`, see `git log backup/pre-dashboard-overhaul..webui-redesign`):
- Panel grid rescaled to a much finer 120×80 GridStack resolution (was 24×16) so drag/resize snapping feels close to freeform.
- **Food Detection split into two fully independent panels**: `camera` (Live Camera Feed) and `detected` (Detected Items) — previously one GridStack item internally flex-split 55/45, which made either half's actual visible size depend on the combined panel's overall dimensions in a non-obvious way. Now 6 total grid items (was 5): `sidebar`, `camera`, `detected`, `panel2` (Inventory), `panel3` (Recipes), `panel4` (Pre-Cook). All independently draggable/resizable from any of 8 handles (n/ne/e/se/s/sw/w/nw — north was missing initially), each with a `gs-min-w`/`gs-min-h` floor.
- Panels tile flush (zero gap) with square corners on the grid (modal keeps rounded corners). Navbar shrunk 58px→40px.
- Save Layout button removed; layout auto-saves on every GridStack `change` event (debounced 400ms) to `localStorage`, key currently `webui.dashboard.layout.v6` (bumped repeatedly during today's troubleshooting — bump again if a similar "stuck bad state" issue ever recurs). `getSavedLayout()` also runs a sanity check (`isSaneLayout()` in dashboard.js) rejecting any cached layout that doesn't match the current panel set/min-sizes, falling back to HTML defaults instead of silently applying corruption.
- New minimize button (`—`, next to close `✕`) backed by `wmctrl` + `POST /api/app/minimize`, matched by PID against `webui_chrome.pid`. Requires `--ozone-platform=x11` on the Chromium launch in `launch_webui.sh` (without it, Chromium runs as a native-Wayland client under labwc, invisible to `wmctrl -l`) — confirmed working end-to-end.
- Camera caption shows the last-used source/model on boot without auto-starting capture — Start/Stop Camera remain the only lifecycle triggers, by explicit request (NOT auto-start, confirmed with user).
- `StreamWorker.stop()` now also releases the Coral USB delegate/interpreter (`FoodDetector.close()`), not just the camera. `close_app()` calls `worker.stop()` before its hard `os._exit(0)` for clean teardown on app close too (adds up to ~2s).
- **Performance fix, empirically confirmed via `~/.config/yolo11_coral_gui/inventory.db`'s `inference_frames` table**: `/video_feed`'s frame-serving loop had zero rate limiting, busy-spinning on a lock shared with the inference thread — this caused a ~25-40x Coral inference slowdown (4.8s avg vs the documented 125ms baseline) and the reported video tearing. Fixed with a sleep + change-detection + `Content-Length` header. Also decoupled Coral inference from raw capture rate (`INFER_EVERY_N_FRAMES = 6`, ~5fps, tunable).
- **Major layout bug, root-caused and fixed**: `computeCellHeight()` had `Math.max(20, ...)` — a 20px/row floor left over from when `ROWS` was 16, never revisited when `ROWS` became 80. That forced a minimum grid height of 1600px on any screen, silently overflowing every real display by ~2.5x and pushing content (buttons, whole panels) off-screen below the fold with no scrollbar to recover it. This — not localStorage corruption — was the real cause of most of today's "list view / button lost" reports. Floor lowered to `Math.max(2, ...)` (pure divide-by-near-zero guard). Verified via CDP that all 6 panels now render within actual viewport bounds.

**Reliable remote-verification recipe (finally worked, use this next time instead of re-discovering it)**: claude-in-chrome cannot reach the Pi's `127.0.0.1` (runs in a different network context) and `scrot`-based screenshots were unreliable in this session. What worked: launch an isolated **headless** Chromium instance — `chromium --headless=new --disable-gpu --user-data-dir=<throwaway dir> --no-first-run --remote-debugging-port=<port> --remote-allow-origins=* <url>` — then drive it over the Chrome DevTools Protocol websocket (`ws://127.0.0.1:<port>/devtools/page/<id>` from `/json`) using Python's `websocket-client` package (installed in a throwaway venv, since the system Python is externally-managed). `Runtime.evaluate` gets real computed layout/`getBoundingClientRect()` data; `Page.captureScreenshot` gets a real PNG. Non-headless windowed instances (even with `--ozone-platform=x11`) crashed unpredictably within seconds in this session — stick to headless. **Important**: never point a second launch at the shared `~/.config/yolo11_coral_gui/webui_chrome.pid` — it's not port-scoped, so a second `launch_webui.sh` invocation clobbers the PID the live kiosk session's close/minimize depend on. Use a manual `chromium --user-data-dir=<throwaway>` invocation instead of `launch_webui.sh` for any diagnostic instance, and never `pkill -f` by process-name substring from within this harness — the wrapping shell command's own text can self-match and kill unrelated things (killed the live Flask backend once this session this way).

**Resolved since the note above**: the small-panel content-hiding issue was real — measured actual CSS chrome overhead (header + internal sub-header + action button + padding) per panel and found `detected`'s old `minH=15` (~120px) left ~0px for the actual list once its three stacked fixed elements were accounted for. Raised every panel's `gs-min-w`/`gs-min-h` floor to guarantee real visible content at minimum size (verified programmatically that each new floor still fits under that panel's own default size); `PANEL_MINS` in `dashboard.js` bumped to match (`isSaneLayout()` uses it), `STORAGE_KEY` bumped to v7.

**Auto-discovery added for the USB camera**: a USB re-enumeration event (see below) can shift which `/dev/videoN` index the real camera lands on. `discover_camera_indices()` (new, in `src/camera_sources.py`, shared by both UIs) scans `/sys/class/video4linux/*/name` and excludes this Pi's known non-camera nodes (`pispbe`, `hevc-dec`, `bcm2835-isp/codec`). Both `_make_source()` (webui) and `_try_open_usb_camera()` (Tkinter GUI) now try configured index → auto-discovered candidates → 0, instead of only ever retrying a hardcoded 0.

**Coral hardware instability, root-caused (not a code bug)**: `dmesg` showed the Coral USB repeatedly resetting/protocol-erroring (`error -71`) over a long uptime window, eventually dropping to DFU mode (`1a6e:089a`) requiring a physical replug to recover — `vcgencmd get_throttled` ruled out power/thermal (`0x0`, clean). Even after a clean replug, `inference_ms` stayed at ~3000-3700ms (vs the ~125ms documented baseline for this model) — confirmed via direct `inference_frames` table queries before and after. **This is an unresolved hardware/connection-quality issue, not something fixable in software** — next step for whoever has physical access: try a different USB port and/or cable for the Coral Accelerator.

**Capture/inference decoupled into two threads** (`webui/server/camera_stream.py`, `_capture_loop()` + `_infer_loop()`): the old single sequential loop let a slow `model.predict()` call block frame capture entirely, freezing the video for the full duration of every slow inference (3+ seconds, given the hardware issue above) and delivering the JPEG + detection list together from the same stale frame — this, not a rendering bug, was the cause of "video looks delayed/distorted vs. detections." Capture now runs continuously at full camera rate regardless of inference speed, drawing whatever detection result is most recently available onto each fresh frame; inference runs back-to-back on the freshest frame with no blocking in either direction. Verified live: 91/91 frames decoded successfully over 6s of streaming (zero corruption at the data level, ~15fps), while inference in the same window still took ~3s/call, unaffected. Note: reaching "6fps+" for *inference* specifically is hard-capped by the hardware issue above, not something this threading fix (or any further code change) can overcome on its own.

**Close/minimize PID tracking, root-caused and fixed**: Chromium enforces one instance per profile; since every launch here shares the default profile (no `--user-data-dir`), once a kiosk window is already alive, subsequent `launch_webui.sh` runs just hand the URL off to it via IPC and exit quickly — so `$!` in `launch()` was capturing a short-lived launcher process, not the real window, silently breaking `wmctrl`-based close/minimize while an orphaned window kept running underneath. `launch()` now polls `wmctrl -lp` for a window matching `<title>Food GPT Dashboard</title>` and records that PID instead. Verified: PID file now exactly matches wmctrl's reported window PID, and both close and minimize return `{"ok":true}` and actually take effect.

**SUPERSEDED — icons resolved without needing the user's sudo access**: the emoji-font gap above (still true — no emoji font is installed, and that's still unfixable without sudo) turned out to be avoidable entirely. Replaced every Unicode emoji across the whole webui with 38 bundled SVG image icons (`webui/vendor/icons/`, from Twemoji CC-BY 4.0 + one from OpenMoji CC BY-SA 4.0 for Okra, a very recent emoji Twemoji doesn't have yet) fetched over this Pi's confirmed-working internet access. `<img class="icon-img" src="vendor/icons/X.svg">` renders regardless of what fonts are installed, so this permanently sidesteps the font dependency rather than waiting on it. Covers all 19 food-class icons, category/recipe icons, all 6 nav bar buttons, all 6 panel/sidebar titles, sidebar status rows, low-stock indicators, and the modal file-browser/regenerate icons. `.icon-img { width/height: 1em }` reuses each location's existing font-size so no per-location CSS tuning was needed. Verified every referenced filename has a matching file on disk (zero mismatches) and confirmed the live server actually serves them (HTTP 200, correct `image/svg+xml` content-type). Close/minimize (✕/—) and plain arrows (→/⬅) were deliberately left as text — ordinary punctuation, not color emoji, renders fine on any font.

---

## Session close — 2026-07-28, end of day

**Video distortion — reproduced and proven as genuine hardware-level frame corruption, not a code bug.** Captured raw frames directly from the camera driver via a bare `cv2.VideoCapture` script — completely bypassing the webui, the Coral, and all of this session's code — and measured real frame-to-frame pixel differences. Found 8 genuine corruption spikes in a 10-second window (`t=2.81s, 2.88s, 3.21s, 3.28s, 6.93s, 7.00s, 8.19s, 8.26s`), each a large, sudden jump in pixel content consistent with a torn/corrupted USB isochronous frame. This conclusively rules out the JPEG encoding, the `Content-Length` header, the capture/inference threading, and the frontend `<img>` handling — all independently verified clean earlier — as the cause. The corruption originates in the camera's own USB video stream, before any of this session's code ever touches the frame.

**USB topology finding, partially actioned**: `lsusb -t` revealed the Coral USB Accelerator was on **Bus 003, a USB 2.0 bus (480Mbps)**, sharing it with the camera, despite being a USB 3.1 SuperSpeed device — while two genuine USB 3.0 controllers (Bus 002, Bus 004, 5000Mbps) sat completely idle. User moved the Coral to a USB 3.0 port during this session: **confirmed inference dropped from ~3000-3700ms to ~1000-1140ms (~3x faster)** — a real, measured improvement, though still short of the ~125ms documented baseline. The camera also moved during the same physical rearrangement and landed on **Bus 001**, still USB 2.0, now sharing with the **SanDisk boot/storage drive** instead — a new contention source. Raw-capture anomalies (above) were measured *after* this move, on the camera's new bus.

**Open, unresolved at session end — pick up here next time:**
1. **Camera distortion persists** despite the Coral's move. Working theory: the camera is still on a USB 2.0 bus, now contending with the boot drive's I/O (this system has been swapping heavily all session — `free -h` repeatedly showed 300-650MB swap in use — swap activity on the same physical bus as the camera is a strong candidate for the trigger). Next steps, in order of ease: (a) if there's any other physical USB port available, move the camera off Bus 001 so it isn't sharing with the boot drive; (b) investigate/reduce this system's swap pressure (2GB RAM is tight for this workload — see §13 note 11 below for the known GUI-side memory/swap behavior, though that's about the old Tkinter app specifically); (c) try a different USB cable for the camera if available, since "cannot get freq at ep 0x84" appears in `dmesg` on *every* reconnect regardless of which port is used, suggesting it may be intrinsic to this camera/cable rather than purely bus-sharing.
2. **Coral still not at the ~125ms documented baseline** even on its own dedicated USB 3.0 bus (~1000-1140ms measured). The 3x improvement from isolating it confirms bus-sharing was a real contributor, but something is still adding latency beyond that. Worth re-measuring once the camera's bus-sharing situation is also resolved, in case there was still cross-bus USB controller contention; if the gap persists after that, would need fresh investigation (this session didn't get to it).

**To resume this project in a new conversation**: point Claude at this repo (`~/Documents/yolo11_edgetpu`) and this file. `git log --oneline backup/pre-dashboard-overhaul..webui-redesign` shows the full 26-commit history of this session in order. The kiosk is left running (`webui/launch_webui.sh`, real Chromium PID tracked correctly in `~/.config/yolo11_coral_gui/webui_chrome.pid` per the PID-tracking fix above) — everything committed, working tree clean except the untracked `data_images/` (pre-existing, unrelated). This conversation itself also remains in your Claude Code session history and can be reopened directly from there at any time — this checkpoint is the project-side record in case a different session or agent picks it up instead.

---

## Session update — 2026-07-30 (ported to a new machine, distortion root-caused further)

**Context**: project folder copied from the original Pi (`/home/gpt/...`) to a different machine/user (`~/Documents/yolo11_edgetpu`, user `thesis`), no sudo password available in this session. `.venv`/`.venv39` don't survive a plain file copy (see `restore_env.sh`) and were fully broken (symlinks pointing at `/home/gpt/.pyenv/...`, which doesn't exist here).

**Environment rebuilt from scratch, entirely without sudo**: built Python 3.9.12 via pyenv (build deps were installable with sudo — user ran that one command). Everything else needing installation had no sudo path, so packages were extracted directly from their `.deb`s into user space (`dpkg-deb -x`, no root needed) and wired in via `.venv39/bin/activate` (`LD_LIBRARY_PATH` for shared libs, `PATH` for binaries) rather than system install locations:
- **`libedgetpu.so.1`** (Coral runtime, 16.0) — the pinned URL in §6/§11 above is now stale (Google rotated the content hash, 400s on direct fetch); the live apt-repo Packages index still resolves the same version/hash correctly, use that instead of the hardcoded `.deb` URL if repeating this.
- **`wmctrl`** and **`xdotool`+`libxdo3`** — needed for minimize (`wmctrl`) and, this session, for driving the UI like a real user during verification (`xdotool`, via the X11 XTest extension — works because Chromium runs `--ozone-platform=x11`/XWayland; `ydotool`'s uinput approach was tried first and rejected, `/dev/uinput` is root-only here).
- **`tflite_runtime` version pin matters**: `pip install --extra-index-url https://google-coral.github.io/py-repo/ tflite_runtime` installs **2.14.0** by default now, not the **2.5.0.post1** this project is pinned to (§6 table) and matches `libedgetpu1-std 16.0`'s delegate protocol. Install with `==2.5.0.post1` explicitly — confirmed available in the same index (`pip index versions tflite_runtime` lists back to 2.5.0.post1). Also re-force `numpy==1.26.4` after, same as always — installing this pinned tflite_runtime re-pulls numpy 2.x as a transitive dep.
- Coral auto-flashed DFU (`1a6e:089a`) → runtime (`18d1:9302`) on first `load_delegate()` call, exactly as §8 #3 describes — no manual `sudo` flash needed this time, `libedgetpu.so.1` handles it internally over `libusb` regardless of whether it's installed system-wide or just present via `LD_LIBRARY_PATH`.
- Desktop shortcut added: `~/Desktop/edgetpu-app.desktop` → `webui/launch_webui.sh`.

**Note for whoever restores this env next**: `restore_env.sh` doesn't know about the `libedgetpu`/`wmctrl`/`xdotool` user-space extraction trick above — if sudo is available next time, just `apt install libedgetpu1-std wmctrl xdotool` properly instead and skip the `LD_LIBRARY_PATH`/`PATH` shims in `.venv39/bin/activate`.

**Minimize button bug found and fixed**: `wmctrl` wasn't installed at all on this machine (`ENOENT`), so `/api/app/minimize` always failed. Not caught by `check_deps.py` (doesn't check for it). Fixed by the user-space extraction above; confirmed `{"ok": true}` and the window actually hides now.

**Video feed goes permanently black after any backend restart — found and fixed**: the `<img id="cameraFeed">` only ever sets `src` once, on Start Camera click. If the Flask process dies and restarts (backend crash, or this session's own repeated dev restarts) the browser's open MJPEG connection breaks and nothing re-requests it — confirmed the `onerror` event does **not** reliably fire for a dead `multipart/x-mixed-replace` stream on this Chromium/backend combo (reproduced: killed the backend with zero browser interaction, feed stayed black indefinitely). Fixed in `webui/js/dashboard.js` with two layers: `onerror` → `reconnectFeed()` (fast path when the event does fire) plus a 15s `startFeedWatchdog()` interval that force-refreshes `src` whenever `/api/status` confirms the camera is healthy (catches the case where `onerror` doesn't fire at all). Verified: killed the backend, waited 18s untouched, feed recovered on its own with live detections, no manual click.

**Camera distortion — reproduced fresh on this machine (topology differs from §session-close-07-28) and mitigated two ways.** This machine boots from SD card (`mmcblk0`, not a USB boot drive) and swaps via `zram` (RAM-compressed, not disk I/O), so the old "boot drive + swap I/O on the same USB bus" theory doesn't directly apply here — re-investigated rather than assumed. `lsusb -t` here: Coral alone on Bus 002 (USB 3.0), camera on Bus 003 (USB 2.0) sharing only with an HID receiver, Bus 004 idle. `vcgencmd get_throttled` clean (`0x0`) again, ruling out power/thermal.
- Reproduced with the same raw-`cv2.VideoCapture` methodology as before (now saved as `repro_distortion.py` in the repo root, reusable) — 9 corruption spikes in 15s, landing almost exactly ~1s apart. Saved the actual before/after frame pair for one spike (`spike_0117_prev.jpg` / `spike_0117_bad.jpg` pattern, in scratch output) — visually a torn frame split into shifted quadrants, textbook corrupted USB isochronous transfer, not encoding/rendering.
- **New finding, not previously checked**: `v4l2-ctl --get-fmt-video` showed the camera was negotiating raw **YUYV** (614KB/frame uncompressed) — `src/camera_sources.py`'s `USBCameraSource` never requested a pixel format, so V4L2 fell back to the camera's raw default instead of its native MJPEG mode. At 640×480@30fps that's ~18MB/s of raw traffic on a shared USB 2.0 bus. Likely why a PC doesn't show this symptom (webcam apps typically negotiate MJPEG automatically). Fixed: `cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))` before the width/height calls. Confirmed via `v4l2-ctl` the camera now actually negotiates MJPG. Re-ran `repro_distortion.py`: **9 → 4 spikes per 15s (~55% reduction)** — a real, measured improvement, but corruption still occurs at the source sometimes, consistent with §session-close-07-28's note that "cannot get freq at ep 0x84" appears in `dmesg` regardless of port/format and may be intrinsic to this camera/cable.
- Since the remaining corruption can't be eliminated in software, added a second layer in `webui/server/camera_stream.py`: `StreamWorker._reject_if_corrupt()` diffs every captured frame against the last known-good frame; a sudden jump (>4x the recent rolling baseline, min threshold 20) gets silently replaced with the last good frame instead of being encoded, streamed, or fed to the Coral detector — capped at 10 consecutive substitutions so genuine fast motion doesn't freeze the feed. Logs `[capture] torn frame rejected ...` with a running session count for diagnostics.
- **Proof, not just claimed**: wrote `repro_check_served.py` (pulls real frames from the live `/video_feed` endpoint, runs the same spike detector against the actual served output). Two independent 15s runs: **0 spikes reached the client** in both, while the backend log showed corrupted frames being caught and dropped in the background (29 in one session). This is the client-facing proof the fix works, not just the source-side improvement.

**Open, still unresolved**:
1. Corruption still happens at the source sometimes (reduced, not eliminated) — needs the same physical remediation §session-close-07-28 already identified (different USB port/cable for the camera), which no session without physical access can action. The two-layer software fix above means it no longer reaches the user regardless.
2. `/etc/apt/sources.list.d/coral-edgetpu.list` (added this session for the Coral apt repo) is malformed — a paste artifact split the `deb [...] ... main` line across two lines, which breaks `apt update` generally on this machine until someone with sudo fixes it (`sudo sed -z -i 's/stable\n *main/stable main/' /etc/apt/sources.list.d/coral-edgetpu.list`). Not blocking anything in this project (worked around via direct `.deb`/mirror downloads throughout), but worth fixing since it breaks unrelated `apt` usage on the machine.
3. Coral USB reset instability (§8 #3, §session-close-07-28) is still present — several `reset SuperSpeed USB device` lines appeared in `dmesg` during this session's testing too — but inference itself was repeatedly confirmed working end-to-end (~1.1-1.15s/frame, consistent with the post-move measurement from 07-28, still above the ~125ms documented baseline).

**"Save to Inventory" no longer triggers a GPT recipe call — by request, decoupled from "Regenerate from current inventory".** Previously `POST /api/inventory/sync` (backing the "SAVE TO INVENTORY & SYNC WITH GPT" button) called `recipes.generate_and_store()` itself as a side effect after every save, meaning saving detections silently spent a GPT call every time. Removed that call from `inventory.sync_inventory()` in `webui/server/routes/inventory.py` (and the now-unused `recipes_error` field from its response) — it now only writes to `inventory_items` and logs the event, nothing else. GPT recipe generation is now reachable **only** via the pre-existing "Regenerate from current inventory" button/modal (`POST /api/recipes/generate`, `modals.js`), which already called the same `generate_and_store()` function independently. Button label changed from "SAVE TO INVENTORY & SYNC WITH GPT" → "SAVE TO INVENTORY" (`webui/index.html`) since it no longer touches GPT; docstring on `generate_and_store()` updated to match. Verified via direct API calls: `/api/recipes` batch `id` stayed at `5` across an `/api/inventory/sync` call, then advanced to `6` with a fresh timestamp after `/api/recipes/generate` — confirming the save path is now GPT-free and the regenerate path still works.

**Taskbar keyboard shortcut disappeared — `wf-panel-pi.ini` got silently reset, not a code/project issue.** `~/.config/wf-panel-pi/wf-panel-pi.ini` was found stripped back to just `position`/`icon_size`/`window-list_max_width`/`monitor` — the `widgets_left`, `launchers` (which includes `wvkbd-toggle`, §wvkbd session), and `widgets_right` keys were gone entirely, taking the on-screen-keyboard icon and the minimize-button-relevant right-side widgets with it. `icon_size`/`window-list_max_width` had also changed values (16→24, 150→200) from what was set earlier, suggesting the panel's own settings dialog was opened and saved at some point — that dialog only persists the keys it has UI for, dropping hand-edited ones like the custom `launchers` list. Fixed by re-adding the three missing keys (keeping the new 24/200 values) and restarting the panel (`pkill -x wf-panel-pi`; `lwrespawn` auto-relaunches it with the updated config). **If this recurs**: don't use the wf-panel-pi settings/preferences GUI if it's ever opened — it will wipe the custom `launchers`/`widgets_left`/`widgets_right` lines again; restore from this note instead of rediscovering it.

**System clock was on the wrong timezone (`America/Adak`, Alaska) — fixed to `Asia/Manila`.** NTP sync (`systemd-timesyncd`) was already active and the clock already synchronized, just under the wrong zone, throwing off every timestamp in the app (inventory `updated_at`, recipe `generated_at`, history events, etc. are all wall-clock based). Confirmed the correct zone via IP geolocation (resolved to Philippines) — also matches the recipe engine's own Filipino-cuisine output (Adobo, Sinigang, Mechado), so `Asia/Manila` lines up on two independent signals. Fixed with `timedatectl set-timezone Asia/Manila` — worked without sudo (polkit allows `org.freedesktop.timedate1.set-timezone` for the active local session on this system). No project-code changes involved.

**wvkbd on-screen keyboard size increased.** Default height (~130px) was too small; bumped to 170px. **Gotcha**: this screen (1024×600) is landscape, and wvkbd's `-H` flag only controls *portrait* height — landscape uses the separate `-L` flag. Setting only `-H` had no visible effect until `-L 170` was added too. Both flags now set in the two places wvkbd gets launched: `~/.config/autostart/wvkbd.desktop` (`Exec=/usr/bin/wvkbd-mobintl --hidden -H 170 -L 170`) and `~/.local/bin/wvkbd-toggle.sh`'s fallback launch line, so the size is consistent whether it starts at login or gets launched on-demand by the toggle script.

---

## 1. Quick Start (STALE — Tkinter GUI, superseded by webui/ above)

```bash
cd ~/Documents/yolo11_edgetpu
source .venv39/bin/activate
python src/gui.py                    # GUI with live Coral inference
python check_deps.py                 # 20/20 dependency checks
python -u test_gui_smoke.py          # 24/24 smoke test
```

---

## 2. Hardware Specifications (This Machine)

| Component | Detail |
|-----------|--------|
| **Board** | Raspberry Pi 5 Model B Rev 1.1 |
| **SoC** | BCM2712, 4× Cortex-A76 @ 2.4 GHz (ondemand governor, 1.6 GHz idle) |
| **Architecture** | aarch64 (64-bit ARMv8-A), CPU part 0xd0b |
| **CPU Features** | fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp |
| **RAM** | 2 GB LPDDR4X (2,054,336 kB total, ~1,090 MB available at idle) |
| **GPU Memory Split** | ARM: 1016 MB / GPU: 8 MB |
| **Swap** | 2 GB zram (compressed RAM swap, in-memory) |
| **Storage** | 14.9 GB SanDisk Cruzer Blade (USB 2.0 SSD) — 796 MB free |
| **Storage Layout** | sda1: 512M `/boot/firmware`, sda2: 14.4G `/` |
| **Display** | 1024×768 @ 59.92 Hz (HDMI) |
| **OS** | Debian GNU/Linux 13 (trixie), Debian 13.5 |
| **Kernel** | Linux 6.18.29+rpt-rpi-2712 aarch64 |
| **Desktop** | labwc:wlroots (Wayland) |
| **USB Camera** | EMEET SmartCam C950 (328f:00ea) — `/dev/video0` at 640×480 |
| **Pi ISP Pipeline** | `/dev/video20–35` — virtual/metadata devices (not usable for capture) |
| **Coral USB** | Google Inc. 18d1:9302 — USB 3.1 SuperSpeed (5 Gbps), 896 mA max |
| **Keyboard/Mouse** | Logitech Nano Receiver (046d:c534) |

---

## 3. Features

- **Edge TPU inference** — real-time object detection on Coral USB via tflite_runtime 2.5
- **3 YOLOv11 models** — nano (117 ms, ~8.5 fps), small (322 ms), large (943 ms)
- **Settings persistence** — source (file/usb/picam), camera index, file path, model, confidence — all saved to `~/.config/yolo11_coral_gui/settings.json` and restored on launch
- **Live camera preview** — auto-restarts after Stop for USB/PiCam sources; auto-fallback index on failure
- **Video file detection** — auto-plays with real-time YOLO overlay + FPS counter; Start pauses and runs detection on current frame
- **GPT recipe** — OpenAI gpt-4o-mini via `requests`; thread-safe queue delivery to main thread; Filipino cuisine with main-ingredient priority
- **Safe fallback** — `_safe_import_tflite()` probes tflite_runtime in a subprocess to prevent SIGSEGV; falls back to `SimulationBackend` when Coral is unavailable
- **State machine** — idle → detected → recipe_shown → idle, button labels cycle: Start → Ask GPT → Stop
- **Detection labels with filled backgrounds** — each label on the annotated image gets a solid green background rectangle with black text (high contrast on any image content); labels that overflow the top edge are drawn inside the bounding box; horizontal overflow clamps to the image boundary
- **Auto-sizing text panes** — detected-items textbox auto-resizes to fit all items (min 3 lines, wraps long lists); recipe textbox starts at 10 lines (was ~24 default) and auto-grows via `_auto_height()` when GPT returns a long recipe; both shrink on Stop

---

## 4. Test Results (2026-06-02)

| Test | Checks | Result | Backend |
|------|--------|--------|---------|
| `check_deps.py` | 20 | **20/20 ✓** | — |
| `test_quick.py` | 4 | **4/4 ✓** | SimulationBackend |
| `test_gui_smoke.py` | 24 | **24/24 ✓** | **Edge TPU (Coral USB)** |
| `test_gui_sim.py` | 29 | **29/29 ✓** | **Edge TPU (Coral USB)** |

## 5. Performance Benchmark (Raspberry Pi 5, 2 GB RAM)

### Inference Speed

| Model | Load Time | Avg Inference | Min | Max | FPS | .tflite Size |
|-------|-----------|--------------|-----|-----|-----|-------------|
| `yolo11n` | 3 ms | **125 ms** | 122 ms | 128 ms | **8.0** | 3.5 MB |
| `yolo11s` | 8 ms | **376 ms** | 371 ms | 392 ms | **2.7** | 13 MB |
| `yolo11L` | 18 ms | **1152 ms** | 1144 ms | 1188 ms | **0.9** | 39 MB |

> Measured over 10 warm inferences per model (first inference discarded as cold start).
> Input: 640×640×3 int8 tensor. Output: 1×23×8400 int8 tensor.

### Resource Consumption (RSS = Resident Set Size)

| Stage | RSS | Delta | System RAM Used |
|-------|-----|-------|----------------|
| Baseline (Python + imports) | 32 MB | — | 970 MB / 2006 MB |
| tflite_runtime imported | 34 MB | +2 MB | — |
| Coral delegate loaded | 36 MB | +2 MB | — |
| **yolo11n** loaded | 43 MB | +7 MB | — |
| **yolo11s** loaded | 58 MB | +22 MB | — |
| **yolo11L** loaded | 116 MB | +80 MB | — |
| All 3 models loaded + inference | **175 MB** | **+143 MB** | **1052 MB** |

> Baseline system RAM usage (970 MB) includes Debian 13 desktop, labwc compositor, and background services.
> The application itself adds at most 143 MB (all 3 models loaded simultaneously).
> Peak total: 1052 MB / 2006 MB (52% utilization). Remaining headroom: ~950 MB.

### Cold Start (First Inference)

The very first inference after model load has a one-time warmup cost as the Edge TPU
initializes its internal state. Measured: ~**4150 ms** for the first frame, then
125–1152 ms for all subsequent frames. The test harness accounts for this with
adequate timeouts; the GUI's first Start press after launch will be noticeably
slower (~4 seconds), but subsequent detections are at full speed.

### CPU Governor

`ondemand` — scales from 1.6 GHz (idle) to 2.4 GHz (under load). All benchmarks
were run with the CPU at load frequency (2.4 GHz) during inference.

---

## 6. Exact Software Stack

| Component | Version | Source | Why This Version |
|-----------|---------|--------|-----------------|
| **Python** | 3.9.12 | pyenv (built from source) | **Only version with native tflite_runtime ABI.** Python ≥3.10 changes C API struct layouts incompatible with the pre-compiled `.so`. |
| **tflite_runtime** | 2.5.0.post1 | Bundled with libedgetpu1-std 16.0 | Matches libedgetpu 16.0 delegate protocol. The `.so` files are compiled for Python 3.6–3.9 aarch64. |
| **libedgetpu1-std** | 16.0 | Google Coral apt repo (`cloud.google.com`) | Provides `libedgetpu.so.1` — the Coral USB delegate library. Handles DFU→runtime firmware flashing internally. |
| **numpy** | **1.26.4** | pip | **Must be <2.0.** tflite_runtime 2.5 uses the numpy 1.x C API (`_ARRAY_API`). numpy ≥2 changes this, causing `AttributeError: _ARRAY_API not found`. opencv declares `numpy>=2` but works fine with 1.x at runtime. |
| **opencv-python-headless** | 4.13.0.92 | pip | Frame capture, image preprocessing, NMS, drawing. Headless build (no GUI deps). |
| **Pillow** | 12.2.0 | pip | ImageTk conversion for Tkinter display. |
| **requests** | 2.34.2 | pip | OpenAI Chat Completions API for GPT recipes. |
| **pyenv** | 2.6.32 | git clone | Python version manager. Builds Python 3.9.12 from source with correct `--enable-shared`. |
| **dfu-util** | 0.11 | apt | USB firmware flashing tool. Used by libedgetpu internally for Coral DFU→runtime transition. |
| **patchelf** | (system) | apt | ELF binary patcher. Used to add `libpyshim.so` as NEEDED dependency on tflite_runtime `.so` (for Python 3.13 fallback). |
| **build-essential** | 14.2.0 | apt | GCC, make, libc-dev — needed to compile Python 3.9 and the ABI shim. |

### Python 3.9 Build Dependencies
```
libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev
libffi-dev liblzma-dev tk-dev libncurses-dev
```

---

## 7. GUI Response Messages (all captured during simulation testing)

### Settings Popover

| User Action | Status Bar Message | Console Output |
|-------------|-------------------|----------------|
| Open Settings | (unchanged) | — |
| Select "File" source | — | file_entry enabled, usb_spin disabled |
| Select "USB camera" | `live preview (usb) — press Start to capture` | file_entry disabled, usb_spin enabled |
| USB camera fails at index N | `camera /dev/video{N} not available; fell back to /dev/video0` | `[ WARN:0] global cap_v4l.cpp:914 open VIDEOIO(V4L2:/dev/video{N}): can't open camera by index` |
| No USB cameras at all | `preview error: no USB camera found (tried /dev/video{N} and /dev/video0)` | — |
| Select "Pi camera" | `live preview (picam) — press Start to capture` | — |
| Change model dropdown | (immediate save to disk) | `[models] yolo11s -> yolo11s_best_full_integer_quant_edgetpu.tflite  (Edge-TPU compiled)` |
| Change confidence slider | (debounced save, 500 ms) | — |
| Browse file (image) | `preview: {filename} — press Start to detect` | image loaded to preview pane |
| Browse file (video) | `video detection live ({filename}) — Edge TPU (Coral USB)` | `[accelerator] Edge TPU (Google Coral USB)  [video: {filename}]`<br>`[coral] ✓ Google Coral USB ACTIVE — Edge TPU video inference` |

### Detection Flow

| User Action | Status Bar | Console | Right Pane |
|-------------|-----------|---------|------------|
| Press **Start** (image) | `loading yolo11s (yolo11s_..._edgetpu.tflite)…` → `detected {N} item(s) on Edge TPU (Coral USB)  [yolo11s]` | `[accelerator] Edge TPU (Google Coral USB)  [yolo11s]`<br>`[coral] ✓ Google Coral USB ACTIVE — inference running on Edge TPU` | Annotated image with filled label backgrounds (green bg, black text) displayed left<br>`Detected Items ({N} total):` — auto-sized (min 3 lines, wraps long lists) |
| Press **Start** (during video playback) | `video paused — {N} item(s) on Edge TPU (Coral USB)` | same as above | same as above |
| Press **Ask GPT** | `asking GPT…` → `recipe ready — press Stop to reset` | (none) | Recipe text from gpt-4o-mini<br>Recipe pane auto-sizes to content (min 5 lines), detected-items pane stays visible |
| Error during GPT | `recipe ready — press Stop to reset` | (none) | `[recipe error] {message}` |
| Press **Stop** (file source) | `idle — open Settings, then press Start` | — | Both panes cleared |
| Press **Stop** (camera source) | preview restarts: `live preview (usb) — press Start to capture` | — | Both panes cleared, camera feed resumes |

### Internal State Transitions

```
idle ──Start──▶ detected ──Ask GPT──▶ recipe_shown ──Stop──▶ idle
 │                  │                      │                   │
 │ Start enabled     │ Start + GPT enabled  │ only Stop         │ Start enabled
 │ GPT disabled      │ Stop enabled         │ enabled           │ GPT disabled
 │ Stop (if video)   │                      │                   │ preview restarts
```

---

## 8. Troubleshooting Journal (Complete Session Log)

### #1 — tflite_runtime import fails: `undefined symbol: _PyThreadState_UncheckedGet`
- **Symptom:** Python 3.13: `ImportError: undefined symbol: _PyThreadState_UncheckedGet`
- **Root cause:** Python 3.13 renamed this internal function to `PyThreadState_GetUnchecked`. The pre-compiled tflite_runtime `.so` (built for 3.6–3.9) calls the old name.
- **Attempted fix 1:** Wrote `py_shim.c` (inline assembly trampoline `_PyThreadState_UncheckedGet → PyThreadState_GetUnchecked`), compiled to `libpyshim.so`, applied `patchelf --add-needed`. **Result:** shim loaded, symbol resolved.
- **Attempted fix 2:** Copied cpython-39 `.so` → cpython-313, binary-patched version string `3.9` → `3.13.5`. **Result:** version check bypassed, but import causes **SIGSEGV** (exit code -11).
- **Final solution:** **Abandoned binary patching. Installed Python 3.9.12 via pyenv.** The tflite_runtime `.so` is native to 3.9 — no patching needed.

### #2 — `check_deps.py` showed `tflite_runtime: import OK` but GUI smoke test said `TFLite available: False`
- **Symptom:** `check_deps.py` runs `import tflite_runtime` in the venv Python and succeeds. But when `detector.py` tries the import at module level, it crashes.
- **Root cause:** The `import` succeeds in some contexts but the patched `.so` causes SIGSEGV when loaded alongside other modules (cv2, numpy) due to conflicting symbol resolution.
- **Fix:** Replaced module-level `try/except ImportError` with `_safe_import_tflite()` — probes the import in a **subprocess** with `subprocess.run()`. If the subprocess exits 0, the import is safe and `TFLITE_AVAILABLE = True`. If it crashes (SIGSEGV), the subprocess dies silently and we fall back to `SimulationBackend`.

### #3 — Coral delegate fails: `ValueError: Failed to load delegate from libedgetpu.so.1`
- **Symptom:** `load_delegate('libedgetpu.so.1')` returns NULL with empty error message.
- **Root cause:** The Coral USB was in **DFU mode** (`1a6e:089a` — "before first use"). `libedgetpu.so.1` calls `tflite_plugin_create_delegate` which tries to flash firmware via dfu-util and initialize the device. It returns NULL without setting `capture.message`.
- **Diagnosis:** Ran `lsusb` → `1a6e:089a Global Unichip Corp.` (DFU mode). Ran with `sudo strace -e trace=open,ioctl` → saw USB ioctls succeeding. Ran with `sudo` → delegate loaded and device switched to `18d1:9302`.
- **Fix:** Ran `sudo` once to flash firmware (device now permanently at `18d1:9302`). Added **udev rule** `/etc/udev/rules.d/99-coral-usb.rules` with `MODE="0666"` so user-space can access without sudo. Reloaded with `udevadm control --reload-rules && udevadm trigger`.

### #4 — Model loading fails: `AttributeError: _ARRAY_API not found`
- **Symptom:** `Interpreter(model_path=..., experimental_delegates=[d])` raises `SystemError` → `AttributeError: _ARRAY_API not found`.
- **Root cause:** numpy 2.x changed its C API (`_ARRAY_API` removed). tflite_runtime 2.5's C extension calls the numpy 1.x C API. The `.venv39` had numpy 2.0.2 installed.
- **Fix:** `pip install numpy==1.26.4`. **Critical:** `opencv-python-headless` declares `numpy>=2` as a dependency and will upgrade numpy on install. Must `pip install --force-reinstall numpy==1.26.4` after installing opencv. **opencv 4.13 works fine with numpy 1.26.4 at runtime** despite the declared dependency.

### #5 — USB camera preview error on startup
- **Symptom:** On GUI launch with source=usb, status bar shows `preview error: could not open /dev/video2`.
- **Root cause:** The persisted `usb_index` was 2, but only `/dev/video0` (EMEET SmartCam) is a functional camera. `/dev/video1` and above are metadata/output-only V4L2 devices.
- **Diagnosis:** `cv2.VideoCapture(0)` → OK (640×480). `cv2.VideoCapture(1)` → `can't open camera by index`. 19 `/dev/video*` nodes exist; only index 0 works.
- **Fix:** Added `_try_open_usb_camera()` method with auto-fallback: tries `usb_index` → tries 0 → shows clear error. Also updates `usb_index` to 0 on fallback so the setting self-corrects.

### #6 — GUI Stop clears preview, camera feed stays blank
- **Symptom:** After Start→detect→Stop with USB camera, the preview pane stays black. Camera preview doesn't resume.
- **Root cause:** `_do_stop()` calls `_stop_preview()` and clears the image, but never restarts the live feed.
- **Fix:** Added at end of `_do_stop()`: if `self.source_var.get() in ("usb", "picam")`, call `self._start_preview()`. Camera feed now resumes immediately after Stop.

### #7 — `TFLiteBackend._create_cpu` undefined (dead code path)
- **Symptom:** `create_backend(accelerator="cpu")` calls `TFLiteBackend._create_cpu(...)` which doesn't exist. Would raise `AttributeError`.
- **Fix:** Added `@classmethod _create_cpu()` to `TFLiteBackend`. Uses `cls.__new__` + manual attribute assignment to bypass the normal `__init__` which always tries to load the Coral delegate when model name contains `_edgetpu`. Passes `experimental_delegates=[]` for pure CPU inference.

### #8 — `detect_video.py` / `detect_camera.py` crash with ImportError
- **Symptom:** `from ultralytics import YOLO` fails. ultralytics + torch + torchvision ≈ 1.5 GB — won't fit on 15 GB drive.
- **Fix:** Wrapped imports in `try/except ImportError` with clear message: `"ultralytics is not installed. Use detect_video_fast.py or the GUI."`

### #9 — Dead variable: `_video_det_paused`
- **Symptom:** `self._video_det_paused` set to `True` at line 428, initialized to `False` at lines 201 and 759. Never read anywhere.
- **Fix:** Removed all three occurrences (2 assignments + 1 initialization).

### #10 — Settings not fully persisted
- **Symptom:** Only `confidence`, `file_path`, `model` were saved. `source` (file/usb/picam) and `usb_index` were lost on restart.
- **Fix:** Added `source` and `usb_index` to `_save_settings()` and `_load_settings()`. Added trace handlers in `_setup_persist_traces()`. Now all 5 settings survive restart.

### #11 — GUI sim test: "default source file" check failed
- **Symptom:** `test_gui_sim.py` step 2: `app.source_var.get() == "file"` returned False.
- **Root cause:** Settings persisted from previous test run had `source: "picam"` or `source: "usb"`. The `_load_settings()` call in `App.__init__` overrode the default.
- **Fix:** Reset `settings.json` to defaults before each test run.

### #12 — GUI sim test: "usb mode disables file entry" check failed
- **Symptom:** After `app.source_var.set("usb")`, `app.file_entry.cget("state")` was not `"disabled"`.
- **Root cause:** Setting `source_var` programmatically doesn't trigger `_refresh_input_state()` (only the radio button's `command` callback does). The file_entry state only changes in `_refresh_input_state`.
- **Fix:** Added explicit `app._refresh_input_state()` calls in the test after `source_var.set()`.

### #13 — GUI sim test: "video detection started" check failed
- **Symptom:** After `app.file_path.set(video_path)`, `app._video_det_thread` was `None`.
- **Root cause:** Setting `file_path` programmatically doesn't trigger `_load_file_preview()`. Only the Browse button's callback does.
- **Fix:** Added explicit `app._load_file_preview(video_path)` call in the test.

### #14 — `test_gui_sim.py` timing: model load on cold start
- **Observation:** First detection on Coral takes ~4 seconds (model allocation + delegate init). Subsequent inferences are much faster (117–943 ms). The test has adequate timeouts (25 s for GPT, 1 s for detection) and handles this correctly.

### #15 — System slowdown after closing the GUI
- **Symptom:** After running the GUI and closing it, the Raspberry Pi 5 becomes sluggish — desktop lag, slow app switching, high latency.
- **Diagnosis:** `free -h` showed hundreds of MB of swap in use, but no python processes running. Scanned `/proc/[pid]/status` for VmSwap — found background services pushed into swap by the GUI's memory pressure. The specific services vary by system, but on this Pi the top were: TeamViewer (305 MB), codewhale-tui (55 MB), labwc (31 MB), Xwayland (25 MB).
- **Root cause:** The 2 GB Pi 5 with a desktop environment has a ~970 MB baseline. Loading the GUI with models (~175 MB peak RSS) + camera frames pushes past available RAM, forcing the kernel to swap out whatever background services are running. When the GUI exits, those swapped pages stay in zram because the owning services are still alive.
- **Fix 1 (GUI):** Added explicit cleanup in `_on_close()`: nulls `self.model`, frame buffers, calls `gc.collect()`. This freed ~200 MB immediately.
- **Fix 2 (launcher):** `launch_gui.sh` now scans `/proc` for all processes using >1 MB swap (not hardcoded to specific apps), displays the top 10 offenders, then runs `swapoff -a && swapon -a` to reclaim all swapped pages regardless of which process owns them. Shows before/after memory stats.

### #16 — Detection labels hard to read; text panes don't resize to content
- **Symptom:** Detection labels drawn with `cv2.putText` directly on the image — no background fill, text cut off near image edges, illegible on cluttered backgrounds. Detected-items pane at fixed height=1 forces scrolling to see all items. Recipe pane takes all remaining space (~24 lines default), squeezing the detected-items area.
- **Root cause:** `draw()` in `detector.py` used bare `cv2.putText` with only a `max(y, 14)` clamp. The detected `tk.Text` widget had `height=1` and its parent frame was packed with `fill=tk.X` (no vertical growth). The recipe pane had no explicit height (Tkinter default ~24 lines) and `expand=True` — it greedily took all window space.
- **Fix (detector.py — `draw()`):** Rewrote to measure text with `cv2.getTextSize()`, draw a filled green background rectangle, draw black text on top, clamp label boxes to stay fully within image boundaries, and draw labels inside the bounding box when they would overflow the top edge.
- **Fix (gui.py — detected pane):** Changed `detected_frame.pack(fill=tk.X)` to `fill=tk.BOTH` so the frame grows vertically. The existing `_auto_height()` method already counted wrapped lines and set the text widget height — it just couldn't expand because its parent was locked to X-only fill.
- **Fix (gui.py — recipe pane):** Set `height=10` on `recipe_text` (was default ~24). Added `self._auto_height(self.recipe_text, min_lines=5)` in `_on_gpt_done()` so the pane grows when GPT returns a long recipe but stays compact otherwise.
- **Tests:** `_test_draw.py` (7/7 — edge cases: normal above, top edge inside, left clamp, right clamp, multiple, empty, green bg pixel check). `test_gui_smoke.py` (24/24 passes). `test_quick.py` (4/4 passes).

---

## 9. GUI Simulation Test — Full Trace (29/29 passed)

```
System: Python 3.9.12  |  TFLite available: True  |  Display: :0

Step 1: Initial state
  ✓ initial status idle
  ✓ start btn enabled
  ✓ gpt btn disabled
  ✓ stop btn disabled

Step 2: Settings popup
  ✓ settings popup exists
  ✓ settings popup visible
  ✓ default source file
  ✓ usb mode disables file entry
  ✓ settings popup closed

Step 3: Model selection
  ✓ model switch to yolo11L
  ✓ model switch to yolo11n
  ✓ model switch to yolo11s
  ✓ model reset to yolo11s

Step 4: File selection
  ✓ file path set

Step 5: Start detection
  [models] yolo11s -> yolo11s_best_full_integer_quant_edgetpu.tflite (Edge-TPU compiled)
  [accelerator] Edge TPU (Google Coral USB) [yolo11s]
  [coral] ✓ Google Coral USB ACTIVE — inference running on Edge TPU
  ✓ detection ran (state=detected)
  ✓ detected items shown
  ✓ status shows detection

Step 6: Ask GPT
  ✓ GPT btn disabled after click
  ✓ recipe shown (state=recipe_shown)

Step 7: Stop
  ✓ state reset to idle
  ✓ status shows idle
  ✓ detected text cleared

Step 8: Video file detection
  [accelerator] Edge TPU (Google Coral USB) [video: veg2_annotated.mp4]
  [coral] ✓ Google Coral USB ACTIVE — Edge TPU video inference
  ✓ video detection started
  ✓ video detection stopped

Step 9: Settings persistence
  ✓ settings file created
  ✓ confidence saved
  ✓ model saved
  ✓ file path saved

Step 10: Window close
  ✓ app closed cleanly

Tests: 29 passed, 0 failed (7.5s)
```

---

## 10. Architecture

```
src/gui.py            Tkinter GUI — Settings popover, 3-state machine,
                      live camera preview thread, video detection thread,
                      auto-height detected-items pane,
                      Coral auto-detect via detector.py, GPT via recipe.py
src/detector.py       Backend abstraction: Backend → TFLiteBackend (Coral) |
                      SimulationBackend. _safe_import_tflite() subprocess
                      probe. create_backend() factory. draw() annotation.
src/models.py         Model registry — short name → _edgetpu.tflite path
src/recipe.py         OpenAI gpt-4o-mini Chat Completions via requests; Filipino cuisine prompt
src/capture.py        Standalone frame capture to inference/images/
src/detect_video_fast.py  CLI: raw tflite_runtime pipeline (no ultralytics)
src/detect_video.py   CLI: ultralytics wrapper (guarded import)
src/detect_camera.py  CLI: USB webcam detection (guarded import)
```

### Settings Persistence

`~/.config/yolo11_coral_gui/settings.json` — auto-saved on any change via Tk variable traces:
```json
{"confidence": 0.5, "last_file_path": "/path/to/file",
 "model": "yolo11n", "source": "file", "usb_index": 0}
```

### USB Camera Auto-Fallback

```
_try_open_usb_camera():
  try index N → success? return
                → fail? try index 0 → success? update usb_index, return
                                     → fail? status: "no USB camera found"
```

---

## 11. Raspberry Pi 5 — Fresh Installation Guide

### Prerequisites (sudo password required once)

```bash
# System packages
sudo apt update
sudo apt install -y build-essential libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev libffi-dev liblzma-dev tk-dev \
    libncurses-dev dfu-util git curl patchelf

# Coral USB runtime library
curl -sL -o /tmp/libedgetpu.deb \
    "https://packages.cloud.google.com/apt/pool/coral-edgetpu-stable/libedgetpu1-std_16.0_arm64_86fb6586830bf93d50323b24ab349ca2.deb"
sudo dpkg -i /tmp/libedgetpu.deb

# Coral USB permissions (no sudo needed for inference)
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1a6e", MODE="0666"' | sudo tee /etc/udev/rules.d/99-coral-usb.rules
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="18d1", MODE="0666"' | sudo tee -a /etc/udev/rules.d/99-coral-usb.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Python 3.9.12

```bash
# Install pyenv
git clone --depth 1 https://github.com/pyenv/pyenv.git ~/.pyenv
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"

# Build Python 3.9.12 (5-10 minutes on Pi 5)
pyenv install 3.9.12
```

### Project Setup

```bash
cd ~/Documents/yolo11_edgetpu

# Create Python 3.9 venv
~/.pyenv/versions/3.9.12/bin/python3.9 -m venv .venv39
source .venv39/bin/activate

# Install packages (order matters!)
pip install numpy==1.26.4
pip install opencv-python-headless Pillow requests
pip install --force-reinstall numpy==1.26.4   # opencv upgrades numpy to 2.x

# Copy tflite_runtime from existing .venv OR extract from Coral .deb:
# cp -r .venv/lib/python3.13/site-packages/tflite_runtime .venv39/lib/python3.9/site-packages/
# cp -r .venv/lib/python3.13/site-packages/flatbuffers .venv39/lib/python3.9/site-packages/

# Verify
python check_deps.py                          # should show 20/20
python -u test_gui_smoke.py                   # should show 24/24
```

### Desktop Shortcut

```bash
# The launch_gui.sh script auto-selects .venv39 if present
cp launch_gui.sh ~/Desktop/
# Or use the .desktop file already at ~/Desktop/yolo11-coral-gui.desktop
```

---

## 12. Raspberry Pi Compatibility Matrix

| Pi Model | RAM | OS | Coral | Status | Notes |
|----------|-----|----|-------|--------|-------|
| **Pi 5** (BCM2712, Cortex-A76) | 2 GB | Debian 13 arm64 | USB 3.0 5 Gbps | **✓ Tested** | yolo11n @ 117 ms. 2 GB sufficient. |
| Pi 5 | 4/8 GB | Debian 13 arm64 | USB 3.0 | **✓ Should work** | More RAM, identical arch. |
| Pi 4 (BCM2711, Cortex-A72) | 2+ GB | Debian 13 arm64 | USB 3.0 | **✓ Should work** | Same aarch64 arch. Slightly slower CPU. |
| Pi 4 | 2+ GB | Raspberry Pi OS arm64 | USB 3.0 | **✓ Should work** | Need to install pyenv + build deps. |
| Pi 4 | 1/2 GB | Raspberry Pi OS armhf | USB 2.0 | **⚠ Untested** | 32-bit — need armhf tflite_runtime `.so`. USB 2.0 → slower data transfer. |
| Pi 3 (BCM2837, Cortex-A53) | 1 GB | Raspberry Pi OS arm64 | USB 2.0 | **⚠ Untested** | USB 2.0 bottleneck. 1 GB may be tight with GUI + model. |
| Pi Zero 2 W | 512 MB | Raspberry Pi OS arm64 | USB 2.0 | **✗ Not recommended** | 512 MB insufficient for GUI + model + OS. |

### Porting Checklist for New Pi

1. **Same arch (aarch64):** Copy the entire `yolo11_edgetpu/` folder. Run `check_deps.py` to verify.
2. **Different arch (armhf):** Need armhf build of Python 3.9 + armhf tflite_runtime `.so`. Rebuild `.venv39` from scratch.
3. **No Coral USB:** The GUI auto-falls back to `SimulationBackend`. All features work (detection, GPT, settings) — just with fake detections.
4. **No camera:** File source and video detection still work. USB camera preview will show error message.
5. **No display:** Set `DISPLAY=:0` or use Xvfb. Tkinter needs an X server. Tests work headless with `DISPLAY=:0`.

---

## 13. Key Experiences (What Made This Work)

1. **Python 3.9 is mandatory.** The tflite_runtime `.so` is compiled for Python 3.6–3.9 ABI. Python 3.10+ changes struct layouts (PyTypeObject, PyModuleDef) in ways the binary can't handle. Binary patching only bypasses the version check — the actual ABI is incompatible.

2. **numpy must be <2.0.** tflite_runtime 2.5 was built against numpy 1.x C API. The `_ARRAY_API` symbol was removed in numpy 2.x. opencv declares `numpy>=2` but works fine with 1.26.4 at runtime — force-reinstall after opencv.

3. **USB permissions are silent killers.** The Coral delegate returns NULL with an **empty error message** when it can't access the USB device. No indication it's a permissions issue. `sudo strace` revealed the USB ioctls succeeding → confirmed it was permissions.

4. **DFU → runtime transition is automatic.** The first time `tflite_plugin_create_delegate` is called (with USB access), `libedgetpu.so.1` uses dfu-util internally to flash the runtime firmware. The device changes from `1a6e:089a` to `18d1:9302`. No manual dfu-util commands needed.

5. **Safe subprocess probing prevents crashes.** SIGSEGV from an incompatible `.so` can't be caught by Python's `try/except`. The `_safe_import_tflite()` function spawns a child process — if it crashes, the parent survives. This is the difference between "graceful fallback" and "GUI vanishes."

6. **Not all `/dev/video*` are cameras.** On this Pi, 19 video devices exist but only index 0 is a real camera. The rest are V4L2 metadata/output nodes. Auto-fallback to index 0 is essential.

7. **Tkinter trace handlers don't simulate UI clicks.** Setting `source_var` programmatically doesn't trigger the radio button's `command` callback. Tests must call `_refresh_input_state()` explicitly after changing source.

8. **Settings auto-save on trace is sufficient.** No manual "Save" button needed. Every change to the 5 settings vars triggers `_save_settings()` via Tk `trace_add("write")`. Restored on next launch before UI build.

9. **First Coral inference is slow (cold start).** Model allocation + delegate init takes ~4 seconds. Subsequent inferences are 117–943 ms. Tests must account for this with adequate timeouts.

10. **Disk space is tight on 15 GB.** ultralytics (~1.5 GB) won't fit. The `detect_video.py` and `detect_camera.py` CLI scripts are guarded with clear ImportError messages pointing to `detect_video_fast.py` or the GUI.

11. **Swap persists after process exit.** On memory-constrained devices (2 GB Pi 5 with desktop), running the GUI forces background services into swap. After closing, those pages stay in zram because the services are still alive. Cycling swap (`swapoff -a && swapon -a`) reclaims everything instantly regardless of which process owns the pages. The launcher now scans `/proc` for all swap consumers before clearing.

---

## 14. Project Layout (Final)

```
yolo11_edgetpu/
├── src/                     Source (8 files)
│   ├── gui.py               Tkinter GUI — 902 lines
│   ├── detector.py          Backend + _safe_import_tflite — 515 lines
│   ├── models.py            Model registry — 45 lines
│   ├── recipe.py            GPT recipe (Filipino, main-ingredient priority) — 50 lines
│   ├── capture.py           Frame capture
│   ├── detect_video_fast.py CLI: tflite_runtime pipeline
│   ├── detect_video.py      CLI: ultralytics (guarded)
│   └── detect_camera.py     CLI: USB webcam (guarded)
├── models/                  Only _edgetpu.tflite (1 file per model)
│   ├── yolo11L/yolo11L_edgetpu.tflite           (39 MB)
│   ├── yolo11n/yolo11n_best_full_integer_quant_edgetpu.tflite  (3.5 MB)
│   └── yolo11s/yolo11s_best_full_integer_quant_edgetpu.tflite  (13 MB)
├── labels/coco1.txt         19 food classes
├── inference/               Test images + videos
│   ├── images/GPT_Food/     Training dataset images
│   ├── images/test_veg.jpg  Test image for smoke test
│   └── videos/veg2.mp4      Test video for detection
├── test_quick.py            4 checks, SimulationBackend
├── test_gui_smoke.py        24 checks, Coral Edge TPU
├── test_gui_sim.py          29 checks, full UI simulation
├── check_deps.py            20-point dependency checker
├── launch_gui.sh            Auto-selects .venv39
├── libpyshim.so             Python 3.13 ABI shim (for .venv fallback)
├── py_shim.c                Shim source (assembly trampoline)
├── api.txt                  OpenAI API key
├── CHECKPOINT.md            This file
├── .venv39/                 Python 3.9 venv (active, Coral-compatible)
├── .venv/                   Python 3.13 venv (fallback, simulation only)
└── _archive/                Unused: old shims, non-edgetpu models, training data, docs, setup script
```

---

## 15. Key Commands

```bash
cd ~/Documents/yolo11_edgetpu && source .venv39/bin/activate

python src/gui.py                         # GUI with Coral inference
python check_deps.py                      # 20 dependency checks
python -u test_gui_smoke.py               # 24-step GUI verification
python -u test_gui_sim.py                 # 29-step full simulation

# Coral diagnostics
lsusb | grep 18d1                         # should show "Google Inc."
python -c "from tflite_runtime.interpreter import load_delegate; print(load_delegate('libedgetpu.so.1'))"

# Permissions fix (if delegate fails without sudo)
sudo udevadm control --reload-rules && sudo udevadm trigger

# Rebuild ABI shim (only needed for Python 3.13 .venv fallback)
gcc -shared -fPIC -O2 -I/usr/include/python3.13 -o libpyshim.so py_shim.c
```
