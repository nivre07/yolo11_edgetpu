"""Reproduce camera frame corruption: raw cv2.VideoCapture, no webui/Coral
in the loop. Measures frame-to-frame pixel diff over a capture window and
flags large sudden jumps as corruption (torn/garbled USB frame), same
methodology as CHECKPOINT.md's prior investigation. Saves the corrupted
frames plus their preceding good frame so the tear is visible.
"""
import sys
import time
import cv2
import numpy as np

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "/tmp/repro"
import os
os.makedirs(OUT_DIR, exist_ok=True)

cap = cv2.VideoCapture("/dev/video0")
if not cap.isOpened():
    print("FAILED to open /dev/video0")
    sys.exit(1)

prev = None
diffs = []
spikes = []
t_start = time.time()
frame_idx = 0
DURATION = 15

while time.time() - t_start < DURATION:
    ret, frame = cap.read()
    t = time.time() - t_start
    if not ret:
        print(f"[{t:.2f}s] frame {frame_idx}: READ FAILED")
        frame_idx += 1
        continue
    if prev is not None:
        d = float(np.mean(cv2.absdiff(frame, prev)))
        diffs.append(d)
        if len(diffs) > 5:
            recent_avg = float(np.mean(diffs[-15:-1])) if len(diffs) > 15 else float(np.mean(diffs[:-1]))
            if d > max(20.0, recent_avg * 4):
                spikes.append((t, frame_idx, d))
                cv2.imwrite(f"{OUT_DIR}/spike_{frame_idx:04d}_prev.jpg", prev)
                cv2.imwrite(f"{OUT_DIR}/spike_{frame_idx:04d}_bad.jpg", frame)
                print(f"[{t:.2f}s] frame {frame_idx}: CORRUPTION SPIKE diff={d:.1f} (recent avg={recent_avg:.1f})")
    prev = frame
    frame_idx += 1

cap.release()

print()
print(f"=== Summary: {frame_idx} frames over {DURATION}s ===")
print(f"Corruption spikes: {len(spikes)}")
if diffs:
    print(f"Mean frame diff: {np.mean(diffs):.2f}  Max: {np.max(diffs):.2f}")
for t, idx, d in spikes:
    print(f"  t={t:.2f}s frame={idx} diff={d:.1f}")
