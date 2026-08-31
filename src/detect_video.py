import argparse
import sys
import time
from pathlib import Path

import cv2
import cvzone
import pandas as pd
from tqdm import tqdm

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit(
        "ultralytics is not installed in this environment.\n"
        "Install with: pip install ultralytics\n"
        "Or use detect_video_fast.py which uses raw tflite_runtime."
    )

sys.path.insert(0, str(Path(__file__).resolve().parent))
import models as model_registry

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "labels/coco1.txt"
DEFAULT_VIDEO = ROOT / "inference/videos/veg2.mp4"
DEFAULT_OUT = ROOT / "inference/videos/veg2_annotated.mp4"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", choices=model_registry.CHOICES,
                   default=model_registry.DEFAULT)
    p.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--show", action="store_true", help="cv2.imshow preview (needs display)")
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--stride", type=int, default=1, help="process every Nth frame")
    args = p.parse_args()

    class_list = LABELS.read_text().splitlines()
    model_path = model_registry.resolve(args.model)
    print(f"model  : {model_path.name}", flush=True)
    model = YOLO(str(model_path))

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"could not open {args.video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(args.out), fourcc, fps, (w, h))

    print(f"input  : {args.video}  {w}x{h}@{fps:.1f}fps  frames={total}", flush=True)
    print(f"output : {args.out}", flush=True)

    idx = 0
    written = 0
    t0 = time.time()
    last_boxes = []

    bar = tqdm(
        total=total, unit="frame", desc="detecting",
        dynamic_ncols=True, mininterval=0.5, file=sys.stdout,
    )

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            idx += 1

            if (idx - 1) % args.stride == 0:
                results = model.predict(frame, imgsz=640, conf=args.conf, verbose=False)
                last_boxes = pd.DataFrame(results[0].boxes.data).astype("float").values.tolist()

            for row in last_boxes:
                x1, y1, x2, y2 = map(int, row[:4])
                cls = int(row[5])
                label = class_list[cls]
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cvzone.putTextRect(frame, label, (x1, max(y1, 25)), 1, 1)

            writer.write(frame)
            written += 1
            bar.update(1)
            if idx % 10 == 0:
                bar.set_postfix(fps=f"{idx / max(time.time() - t0, 1e-6):.2f}")

            if args.show:
                cv2.imshow("detect_video", frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        bar.close()
        cap.release()
        writer.release()
        if args.show:
            cv2.destroyAllWindows()

    dur = time.time() - t0
    print(f"done: {written} frames written in {dur:.1f}s ({written / dur:.1f} fps)", flush=True)
    print(f"saved: {args.out}", flush=True)


if __name__ == "__main__":
    main()