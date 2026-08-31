import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "inference/images"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_FRAMES = 60

cap = cv2.VideoCapture(0)
captured = 0
seen = 0

while captured < MAX_FRAMES:
    ret, frame = cap.read()
    if not ret:
        break
    seen += 1
    if seen % 3 != 0:
        continue
    frame = cv2.resize(frame, (1020, 600))
    cv2.imshow("capture", frame)
    cv2.imwrite(str(OUT_DIR / f"frame_{captured:03d}.jpg"), frame)
    time.sleep(0.01)
    captured += 1
    if cv2.waitKey(5) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()
