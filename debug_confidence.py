"""One-shot diagnostic: run a real frame through the Coral backend and dump
the raw per-box class-score bytes, to check whether distinct detections are
really getting distinct confidence scores or whether the model/quantization
is collapsing them to the same value.

Usage (on the Pi, with the Coral plugged in):
    cd src && python debug_confidence.py [path/to/image.jpg]
If no image path is given, grabs one frame from USB camera index 0.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import cv2
import numpy as np

from detector import TFLiteBackend
import models as model_registry

model_path = model_registry.resolve(model_registry.DEFAULT)
print(f"model: {model_path}")

backend = TFLiteBackend(model_path, num_classes=19, conf_thresh=0.05)
print(f"accelerator: {backend.accelerator}")
print(f"out_scale={backend.out_scale}  out_zp={backend.out_zp}")

if len(sys.argv) > 1:
    img = cv2.imread(sys.argv[1])
    assert img is not None, f"could not read {sys.argv[1]}"
else:
    cap = cv2.VideoCapture(0)
    ok, img = cap.read()
    cap.release()
    assert ok, "could not read from USB camera 0"

lb, r, (pad_x, pad_y) = backend._letterbox(img)
rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB)
inp = (rgb.astype(np.int16) - 128).astype(np.int8)[None]
backend.interp.set_tensor(backend.in_idx, inp)
backend.interp.invoke()
raw = backend.interp.get_tensor(backend.out_idx)[0]
print(f"raw output shape: {raw.shape} dtype={raw.dtype}")
print(f"raw min/max: {raw.min()} / {raw.max()}")

deq = (raw.astype(np.float32) - backend.out_zp) * backend.out_scale
scores = deq[4:4 + backend.nc]
cls_scores = scores.max(axis=0)
cls_ids = scores.argmax(axis=0)

print(f"cls_scores min/max/mean: {cls_scores.min():.4f} / {cls_scores.max():.4f} / {cls_scores.mean():.4f}")
print(f"distinct cls_scores (rounded to 4dp): {len(np.unique(np.round(cls_scores, 4)))} out of {cls_scores.size} boxes")

keep = cls_scores > 0.3
print(f"\nboxes with cls_score > 0.3: {keep.sum()}")
order = np.argsort(cls_scores[keep])[::-1] if keep.any() else []
kept_idx = np.nonzero(keep)[0]
for rank, oi in enumerate(order[:20]):
    i = kept_idx[oi]
    raw_byte = raw[4 + cls_ids[i], i]
    print(f"  #{rank:2d}  box_idx={i:5d}  cls={cls_ids[i]:2d}  raw_byte={raw_byte:4d}  score={cls_scores[i]:.4f}")
