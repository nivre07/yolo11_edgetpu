#!/usr/bin/env python3
"""Quick test: verify Tkinter and basic module imports work."""
import sys
import os
sys.path.insert(0, 'src')

# 1. Tkinter
print(f"  DISPLAY={os.environ.get('DISPLAY','(unset)')}")
import tkinter as tk
print("  tkinter imported OK")
try:
    root = tk.Tk()
    print(f"  root window created (screen={root.winfo_screenwidth()}x{root.winfo_screenheight()})")
    root.destroy()
    print("  root destroyed OK")
except Exception as e:
    print(f"  tkinter root error: {e}")
    sys.exit(1)

# 2. Detector (simulation)
from detector import create_backend, SimulationBackend, TFLITE_AVAILABLE, draw
import numpy as np
b = create_backend(force_simulation=True)
print(f"  simulation backend: {b.name}")
frame = np.zeros((480, 640, 3), dtype=np.uint8)
dets = b.predict(frame)
print(f"  simulated detections: {len(dets)}")
ann = draw(frame.copy(), dets, ['test']*19)
print(f"  annotated shape: {ann.shape}")

# 3. Models
import models as model_registry
for m in model_registry.CHOICES:
    p = model_registry.resolve(m)
    print(f"  model {m} -> {p.name} (exists={p.exists()})")

# 4. Recipe import
import recipe
print(f"  recipe module OK")

print("ALL BASIC TESTS PASSED")
