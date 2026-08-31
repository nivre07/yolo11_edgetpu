# Food GPT Dashboard

Food GPT Dashboard is a Raspberry Pi food-recognition and smart-inventory application. It uses a custom YOLO11 model with a Google Coral USB Edge TPU to identify ingredients from a USB camera, Pi camera, image, or video. Detected ingredients can be saved to a local inventory, analyzed over time, and sent to OpenAI to generate ranked Filipino recipe suggestions and cooking instructions.

The primary interface is a full-screen web dashboard backed by Flask. A legacy Tkinter interface is also retained in `src/gui.py`.

## What the project does

- Runs quantized YOLO11/TFLite object detection using a Coral USB accelerator.
- Recognizes 20 food classes, including chicken, pork, beef, egg, common vegetables, and Filipino ingredients such as calamansi, banana blossom, and santol.
- Accepts USB camera, Raspberry Pi camera, image, and video sources.
- Displays a live MJPEG camera feed with detection overlays.
- Adds detected items to a persistent inventory and supports quantity adjustment or removal.
- Generates 5–10 ranked Filipino recipe recommendations from the current inventory.
- Shows recipe match percentages, ready ingredients, missing ingredients, projected stock, and cooking steps.
- Deducts used ingredients after cooking is confirmed.
- Stores inventory history, inference performance, stock trends, and recipe activity in SQLite.
- Reports camera, inference, Coral, and OpenAI connectivity status in the dashboard.

## Main workflow

1. Choose a camera or media source and detection model in **Settings**.
2. Select **Start Camera** to begin capture and inference.
3. Review the recognized ingredients in **Detected Items**.
4. Save selected quantities to **Inventory**.
5. Generate recipe recommendations from the current inventory.
6. Select a recipe to inspect missing ingredients and projected stock.
7. Confirm cooking to deduct the ingredients and record the activity.
8. Use **Analytics** and **History** to review stock and system activity.

## Architecture

```text
Camera / image / video
        |
        v
src/camera_sources.py
        |
        +---- capture thread ----> live MJPEG feed
        |
        +---- inference thread --> src/detector.py
                                      |
                                      v
                         YOLO11 TFLite + Coral USB
                                      |
                                      v
                          detections and confidence
                                      |
                 +--------------------+-------------------+
                 |                                        |
                 v                                        v
        Flask dashboard/API                     SQLite inventory/history
                 |                                        |
                 +--------------------+-------------------+
                                      |
                                      v
                         OpenAI Filipino recipes
```

Important components:

| Path | Purpose |
| --- | --- |
| `webui/index.html` | Main dashboard interface |
| `webui/server/app.py` | Flask application entry point |
| `webui/server/camera_stream.py` | Shared capture, inference, and streaming worker |
| `webui/server/routes/` | Camera, settings, inventory, recipe, history, and analytics APIs |
| `webui/server/db.py` | SQLite schema and database helpers |
| `src/detector.py` | TFLite/Coral inference and simulation backend |
| `src/camera_sources.py` | USB camera, Pi camera, image, and video sources |
| `src/models.py` | Automatic discovery and selection of TFLite models |
| `src/recipe.py` | OpenAI recipe recommendation and instruction generation |
| `src/settings_store.py` | Shared persistent application settings |
| `src/gui.py` | Legacy Tkinter interface |
| `CHECKPOINT.md` | Detailed development and hardware troubleshooting journal |

## Hardware and platform

The project was built for a Raspberry Pi environment and expects:

- Raspberry Pi 5 or a compatible ARM64 Linux system
- Google Coral USB Accelerator
- USB/UVC camera or Raspberry Pi camera
- Chromium for the kiosk dashboard
- Python 3.9 for the Coral-compatible runtime
- Internet access and an OpenAI API key for recipe features

The detector can fall back to simulated detections for development when the TFLite runtime is unavailable, but real detection requires a compatible model and Coral setup.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/yolo11_edgetpu.git
cd yolo11_edgetpu
```

### 2. Run the Raspberry Pi installer

On 64-bit Raspberry Pi OS, the bootstrap script installs system packages, configures the Google Coral package repository when needed, installs Python 3.9.12 through pyenv, and creates the application environment:

```bash
./install_raspberry_pi.sh
```

The script uses normal interactive `sudo` prompts. It does not request, read, or store a password itself.

To install the components manually, continue with the steps below.

### 3. Install system dependencies manually

On Raspberry Pi OS/Debian, install the Coral runtime and dashboard utilities:

```bash
sudo apt update
sudo apt install libedgetpu1-std wmctrl chromium curl
```

Install `rpicam-apps` as well if a Raspberry Pi camera will be used:

```bash
sudo apt install rpicam-apps
```

The Google Coral package repository may need to be configured before `libedgetpu1-std` is available. Follow the Coral USB Accelerator setup instructions for the operating system in use.

### 4. Create the Python 3.9 environment manually

The provided restore script creates `.venv39` when Python 3.9 is available:

```bash
./restore_env.sh
```

Alternatively, create it manually:

```bash
python3.9 -m venv .venv39
.venv39/bin/pip install --upgrade pip
.venv39/bin/pip install -r requirements.txt -r requirements-coral.txt
```

Python 3.9 and NumPy 1.26 are used because the Coral-compatible `tflite_runtime` binary is not compatible with the project's newer Python 3.13 environment.

### 5. Verify the detection model

The production Edge TPU model is included so a normal clone is runnable. Models are organized under `models/`:

```text
models/
└── Final/
    └── exp-6.edgetpu.tflite
```

The included model is approximately 40 MiB. Its SHA-256 checksum is `0d6e9c8d7eba16fa2f473ef25074ddf4caa6491e1f8724ea889bcbd7b5a57041`.

The model registry scans `models/*/*.tflite` automatically. It prefers files ending in `_edgetpu.tflite` or `.edgetpu.tflite`, then falls back to a plain `.tflite` model. Additional models remain ignored unless explicitly allow-listed in `.gitignore`.

The included `labels/coco1.txt` contains the 20 class names expected by the current model. A replacement model must use matching output dimensions and class ordering.

### 6. Configure recipe generation

Create `api.txt` in the repository root and place only the OpenAI API key in it:

```bash
printf '%s\n' 'YOUR_OPENAI_API_KEY' > api.txt
chmod 600 api.txt
```

`api.txt` is ignored by Git and must never be committed. Detection and inventory features can operate without it; recipe generation, cooking instructions, and the OpenAI status indicator cannot.

### 7. Check the environment

Run the dependency checker from the Coral environment:

```bash
source .venv39/bin/activate
python check_deps.py
```

The check covers Python packages, Coral hardware, model files, labels, camera devices, display availability, and supporting libraries.

## Running the application

### Full kiosk dashboard

```bash
./webui/launch_webui.sh
```

The launcher starts Flask on `127.0.0.1:5000`, waits for the API, and opens Chromium in kiosk mode. It also supervises the backend and restarts it after an unexpected crash. The dashboard's minimize and close controls use `wmctrl` and PID files stored in the application configuration directory.

To use a different port:

```bash
./webui/launch_webui.sh 5050
```

### Backend only

For development without kiosk mode:

```bash
source .venv39/bin/activate
python webui/server/app.py --port 5000
```

Then open `http://127.0.0.1:5000/` in a browser on the same machine.

### Legacy Tkinter interface

```bash
./launch_gui.sh
```

The web dashboard is the maintained primary interface. The Tkinter application remains for compatibility and testing of the shared detector, camera, settings, and recipe modules.

## Configuration and local data

Runtime data is stored outside the repository:

```text
~/.config/yolo11_coral_gui/
├── settings.json
├── inventory.db
├── webui_chrome.pid
└── webui_server.pid
```

- `settings.json` stores the selected source, USB camera index, model, media path, and confidence threshold.
- `inventory.db` stores inventory, daily snapshots, history events, inference samples, and generated recipe batches.
- The PID files allow the kiosk controls and launcher to manage the active Chromium and Flask processes.

The default detector confidence threshold is `0.35`. USB camera discovery tries the configured `/dev/videoN`, camera-like V4L2 devices discovered through sysfs, and `/dev/video0` as a final fallback.

## Detection classes

The current labels are:

```text
Calamansi, Carrot, Eggplant, Banana, Okra, Pork, Chilli, Egg,
Bell Pepper, Beef, Banana Blossom, Garlic, Santol, Onion, Tomato,
Green Chilli, Chicken, Lettuce, Potato, Young Corn
```

## Reliability features

- Capture and inference run on separate threads so slow inference does not freeze the live video.
- All connected browser tabs share one inference worker rather than loading multiple Coral delegates.
- The USB camera requests MJPEG at 640×480 to reduce USB 2.0 bandwidth pressure.
- Sudden torn/corrupted camera frames are filtered using recent frame differences.
- Repeated inference failures are surfaced as a stalled status instead of failing silently.
- The launcher restarts the Flask backend after native Coral crashes.
- The browser feed reconnects after a backend restart.
- Dashboard panel positions and sizes are saved in browser `localStorage`.

## Testing and diagnostics

Useful checks included in the repository:

```bash
# Fast environment check
source .venv39/bin/activate
python check_deps.py --quick

# Coral setup verification
python verify_coral_setup.py

# GUI smoke tests
python test_gui_smoke.py
python test_gui_sim.py

# Reproduce or investigate raw camera corruption
python repro_distortion.py
```

Some tests require the Raspberry Pi display session, camera, Coral accelerator, model files, or an active web backend.

## Troubleshooting

### No model choices appear

Confirm that `models/Final/exp-6.edgetpu.tflite` exists and matches the checksum documented above. The production model is included, while any additional local models are ignored by default.

### Coral delegate fails to load

- Confirm that the accelerator appears in `lsusb` as `1a6e:089a` or `18d1:9302`.
- Confirm that `libedgetpu.so.1` is installed and visible to the process.
- Run the application with Python 3.9 from `.venv39`.
- Try a USB 3.0 port and a different cable if inference is unusually slow or disconnects.

### Camera cannot be opened

List available devices with `ls -l /dev/video*`. The application automatically excludes known Raspberry Pi codec/ISP nodes and tries camera-like devices, but the USB index can also be set manually in **Settings**.

### Recipe generation is unavailable

Confirm that `api.txt` contains a valid OpenAI API key and that the Raspberry Pi has internet access. The backend checks OpenAI connectivity at startup and approximately once per minute.

### Dashboard minimize does not work

Install `wmctrl` and run the dashboard through `webui/launch_webui.sh`. The minimize operation depends on the Chromium window PID recorded by the launcher and an X11/XWayland-visible window.

### Hardware-specific camera or Coral instability

The project has previously encountered USB frame corruption and Coral latency caused by USB topology, bus bandwidth, ports, and cables. See `CHECKPOINT.md` for the detailed investigation record and measured behavior.

## Repository policy

The following are intentionally excluded from source control:

- `.venv/` and `.venv39/`
- `api.txt`
- additional model files not explicitly allow-listed in `.gitignore`
- inference images and videos
- Python caches and compiled objects

Keep model binaries in separate release assets, external storage, or Git LFS if they need to be distributed. Never add `api.txt` or other credentials to the repository.

## Current status

The `webui-redesign` branch contains the current dashboard implementation. The older `master` and backup branches preserve earlier development states. See `CHECKPOINT.md` and the Git history for detailed implementation notes and unresolved hardware observations.

## License

No project-wide software license has been selected yet. Third-party assets under `webui/vendor/` retain their own license files. Add a project license before encouraging external reuse or contributions.
