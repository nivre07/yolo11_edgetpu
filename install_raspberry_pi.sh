#!/usr/bin/env bash
# Bootstrap Food GPT Dashboard on 64-bit Raspberry Pi OS/Debian.
# This script lets sudo prompt normally and never stores a password.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [ "$(uname -s)" != "Linux" ]; then
    echo "error: this installer supports Linux only" >&2
    exit 1
fi

case "$(uname -m)" in
    aarch64|arm64) ;;
    *)
        echo "warning: verified on 64-bit Raspberry Pi OS (aarch64); current architecture: $(uname -m)" >&2
        ;;
esac

if ! command -v sudo >/dev/null 2>&1; then
    echo "error: sudo is required to install system packages" >&2
    exit 1
fi

echo "Installing browser, build, and camera dependencies..."
sudo apt-get update
sudo apt-get install -y \
    build-essential curl git ca-certificates gnupg wmctrl chromium \
    libbz2-dev libffi-dev liblzma-dev libncursesw5-dev libreadline-dev \
    libsqlite3-dev libssl-dev libxml2-dev libxmlsec1-dev llvm make tk-dev \
    xz-utils zlib1g-dev

if ! dpkg-query -W -f='${Status}' libedgetpu1-std 2>/dev/null | grep -q "install ok installed"; then
    echo "Configuring the Google Coral package repository..."
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor \
        | sudo tee /usr/share/keyrings/coral-edgetpu-archive-keyring.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/coral-edgetpu-archive-keyring.gpg] https://packages.cloud.google.com/apt coral-edgetpu-stable main" \
        | sudo tee /etc/apt/sources.list.d/coral-edgetpu.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y libedgetpu1-std
fi

if ! command -v rpicam-vid >/dev/null 2>&1 && apt-cache show rpicam-apps >/dev/null 2>&1; then
    echo "Installing optional Raspberry Pi camera tools..."
    sudo apt-get install -y rpicam-apps
fi

if ! command -v pyenv >/dev/null 2>&1; then
    PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
    if [ ! -d "$PYENV_ROOT/.git" ]; then
        echo "Installing pyenv in $PYENV_ROOT..."
        git clone --depth 1 https://github.com/pyenv/pyenv.git "$PYENV_ROOT"
    fi
    export PYENV_ROOT
    export PATH="$PYENV_ROOT/bin:$PATH"
fi

if ! pyenv versions --bare | grep -qx "3.9.12"; then
    echo "Building Python 3.9.12. This can take several minutes on a Pi..."
    pyenv install 3.9.12
fi

./restore_env.sh

echo
echo "Installation complete."
echo "1. Connect the Coral USB Accelerator and camera."
echo "2. Optional: create api.txt with an OpenAI API key for recipes."
echo "3. Verify: .venv39/bin/python check_deps.py"
echo "4. Launch: ./webui/launch_webui.sh"
