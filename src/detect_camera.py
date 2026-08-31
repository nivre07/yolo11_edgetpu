import argparse
import sys
from pathlib import Path

import cv2
import cvzone
import pandas as pd

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit(
        "ultralytics is not installed in this environment.\n"
        "Install with: pip install ultralytics\n"
        "Or use the GUI (python src/gui.py) which uses tflite_runtime."
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as model_registry

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "labels/coco1.txt"

ap = argparse.ArgumentParser()
ap.add_argument("--model", choices=model_registry.CHOICES,
                default=model_registry.DEFAULT)
ap.add_argument("--camera", type=int, default=0, help="/dev/video<N> index")
args = ap.parse_args()

model_path = model_registry.resolve(args.model)
print(f"model  : {model_path.name}", flush=True)
model = YOLO(str(model_path))
class_list = LABELS.read_text().splitlines()

cap = cv2.VideoCapture(args.camera)
frame_count = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break
    frame = cv2.resize(frame, (600, 600))

    frame_count += 1
    if frame_count % 3 != 0:
        continue

    results = model.predict(frame, imgsz=640, verbose=False)
    px = pd.DataFrame(results[0].boxes.data).astype("float")

    for _, row in px.iterrows():
        x1, y1, x2, y2 = map(int, row[:4])
        c = class_list[int(row[5])]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cvzone.putTextRect(frame, c, (x1, y1), 1, 1)

    cv2.imshow("FRAME", frame)
    if cv2.waitKey(1) & 0xFF == 27:
        break

cap.release()
cv2.destroyAllWindows()