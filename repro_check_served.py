"""Verify the fix: pull frames from the LIVE /video_feed MJPEG stream
(the actual served output, post-fix) and run the same corruption-spike
diff detector used in repro_distortion.py against them. If the fix works,
zero spikes should reach this stream even while corrupt_frames_dropped
climbs on the backend side (proven separately via backend_debug.log).
"""
import sys
import time
import urllib.request
import numpy as np
import cv2

DURATION = 15
url = "http://127.0.0.1:5000/video_feed"

resp = urllib.request.urlopen(url, timeout=5)
buf = b""
prev = None
diffs = []
spikes = []
frame_idx = 0
t_start = time.time()

while time.time() - t_start < DURATION:
    chunk = resp.read(4096)
    if not chunk:
        break
    buf += chunk
    while True:
        start = buf.find(b"\xff\xd8")
        end = buf.find(b"\xff\xd9")
        if start == -1 or end == -1 or end < start:
            break
        jpg = buf[start:end + 2]
        buf = buf[end + 2:]
        arr = np.frombuffer(jpg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            continue
        t = time.time() - t_start
        if prev is not None and prev.shape == frame.shape:
            d = float(np.mean(cv2.absdiff(frame, prev)))
            diffs.append(d)
            if len(diffs) > 5:
                recent_avg = float(np.mean(diffs[-15:-1])) if len(diffs) > 15 else float(np.mean(diffs[:-1]))
                if d > max(20.0, recent_avg * 4):
                    spikes.append((t, frame_idx, d))
                    print(f"[{t:.2f}s] frame {frame_idx}: SPIKE REACHED CLIENT diff={d:.1f}")
        prev = frame
        frame_idx += 1

resp.close()
print()
print(f"=== Served-stream check: {frame_idx} frames over {DURATION}s ===")
print(f"Spikes reaching the client: {len(spikes)}")
if diffs:
    print(f"Mean diff: {np.mean(diffs):.2f}  Max: {np.max(diffs):.2f}")
