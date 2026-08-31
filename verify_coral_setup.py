#!/usr/bin/env python3
"""Verify Google Coral USB + YOLOv11 EdgeTPU setup."""

import sys
import subprocess
from pathlib import Path

def check_coral_usb_connected() -> bool:
    """Check if Google Coral USB is plugged in."""
    base = Path("/sys/bus/usb/devices")
    _CORAL_IDS = {("1a6e", "089a"), ("18d1", "9302")}
    try:
        for dev in base.iterdir():
            try:
                vid = (dev / "idVendor").read_text().strip()
                pid = (dev / "idProduct").read_text().strip()
                if (vid, pid) in _CORAL_IDS:
                    return True
            except OSError:
                pass
    except OSError:
        pass
    return False

def check_tflite_runtime() -> bool:
    """Check if tflite_runtime is installed."""
    try:
        import tflite_runtime.interpreter
        return True
    except ImportError:
        return False

def check_libedgetpu() -> bool:
    """Check if libedgetpu is installed."""
    result = subprocess.run(
        ["dpkg", "-l"], capture_output=True, text=True
    )
    return "libedgetpu1" in result.stdout

def check_model_files() -> dict:
    """Check if model files exist."""
    models_dir = Path(__file__).parent / "models"
    return {
        "Final": (models_dir / "Final" / "exp-6.edgetpu.tflite").exists(),
    }

def test_model_loading() -> tuple[bool, str]:
    """Test if the default model can be loaded."""
    try:
        from src.detector import create_backend, TFLITE_AVAILABLE
        from src import models

        if not TFLITE_AVAILABLE:
            return False, "tflite_runtime not available"

        model_path = models.resolve('Final')
        labels_path = Path(__file__).parent / "labels" / "coco1.txt"
        num_classes = len(labels_path.read_text().splitlines())
        backend = create_backend(
            model_path=model_path,
            num_classes=num_classes,
            accelerator="coral",
        )
        return backend.using_edgetpu, f"Backend: {backend.name}"
    except Exception as e:
        return False, str(e)

def main():
    print("=" * 60)
    print("YOLOv11 EdgeTPU + Google Coral USB Verification")
    print("=" * 60)
    print()

    # Check Coral USB hardware
    print("[1] Google Coral USB Hardware")
    coral_connected = check_coral_usb_connected()
    status = "✓ Connected" if coral_connected else "✗ Not detected"
    print(f"    {status}")
    if not coral_connected:
        print("    → Plug in the Coral USB device or check with: lsusb | grep coral")
    print()

    # Check tflite_runtime
    print("[2] tflite_runtime Python Module")
    tflite_ok = check_tflite_runtime()
    status = "✓ Installed" if tflite_ok else "✗ Not installed"
    print(f"    {status}")
    if not tflite_ok:
        print("    → Install: pip install --extra-index-url https://google-coral.github.io/py-repo/ tflite_runtime")
    print()

    # Check libedgetpu
    print("[3] libedgetpu System Package")
    libedgetpu_ok = check_libedgetpu()
    status = "✓ Installed" if libedgetpu_ok else "✗ Not installed"
    print(f"    {status}")
    if not libedgetpu_ok:
        print("    → Install: sudo apt install libedgetpu1-std")
    print()

    # Check model files
    print("[4] Model Files")
    models = check_model_files()
    for name, exists in models.items():
        status = "✓" if exists else "✗"
        print(f"    {status} models/{name}/exp-6.edgetpu.tflite")
    print()

    # Test model loading
    print("[5] Model Loading Test")
    can_load, msg = test_model_loading()
    if can_load:
        print(f"    ✓ Model loads on Edge TPU")
    else:
        print(f"    ✗ Model loads on CPU (no Coral): {msg}")
    print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_good = coral_connected and tflite_ok and libedgetpu_ok and all(models.values())

    if all_good and can_load:
        print("✓ All checks passed! You're ready to run inference on Coral USB.")
        print("  Launch the app: ./webui/launch_webui.sh")
        return 0
    elif tflite_ok and all(models.values()):
        print("⚠ Core setup is OK, but:")
        if not coral_connected:
            print("  - Coral USB is not connected (plug it in)")
        if not libedgetpu_ok:
            print("  - libedgetpu1-std is not installed (sudo apt install libedgetpu1-std)")
        print()
        print("  You can still test with a fallback (CPU or simulation) until")
        print("  you have the Coral USB and libedgetpu set up.")
        return 1
    else:
        print("✗ Setup incomplete. Follow the steps above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
