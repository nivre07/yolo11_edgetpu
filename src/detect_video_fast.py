"""Run YOLOv11 + Coral on a video using the raw tflite_runtime pipeline.

This skips ultralytics entirely. With this model, raw vs ultralytics is
about a wash (~+15% from multi-threaded CPU ops) because ~89% of CONV_2D
ops fall back to CPU — see models/food_recipe/*_edgetpu.log. The ceiling
is the model, not the pipeline. Use a smaller export (yolo11n/yolo11s)
or re-quantize the original PyTorch model for a real speedup.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import FoodDetector, draw
import models as model_registry

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "labels/coco1.txt"
DEFAULT_VIDEO = ROOT / "inference/videos/veg2.mp4"
DEFAULT_OUT = ROOT / "inference/videos/veg2_annotated_fast.mp4"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=model_registry.CHOICES,
                    default=model_registry.DEFAULT)
    ap.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--stride", type=int, default=1, help="process every Nth frame")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    class_list = LABELS.read_text().splitlines()
    model_path = model_registry.resolve(args.model)
    det = FoodDetector(model_path, len(class_list), conf_thresh=args.conf,
                       iou_thresh=args.iou, num_threads=args.threads)
    print(f"model  : {model_path.name} "
          f"({'Edge TPU' if det.using_edgetpu else 'CPU'})", flush=True)

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise SystemExit(f"could not open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(args.out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
    )

    print(f"input  : {args.video}  {w}x{h}@{fps:.1f}fps  frames={total}", flush=True)
    print(f"output : {args.out}", flush=True)
    print(f"threads={args.threads}  stride={args.stride}  conf={args.conf}", flush=True)

    bar = tqdm(total=total, unit="frame", dynamic_ncols=True, file=sys.stdout)
    t0 = time.time()
    idx = 0
    last_dets: list = []

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            idx += 1

            if (idx - 1) % args.stride == 0:
                last_dets = det.predict(frame)

            draw(frame, last_dets, class_list)
            writer.write(frame)
            bar.update(1)
            if idx % 10 == 0:
                bar.set_postfix(fps=f"{idx / max(time.time() - t0, 1e-6):.2f}")

            if args.show:
                cv2.imshow("detect_video_fast", frame)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        bar.close()
        cap.release()
        writer.release()
        if args.show:
            cv2.destroyAllWindows()

    dur = time.time() - t0
    print(f"done: {idx} frames in {dur:.1f}s ({idx / dur:.1f} fps)", flush=True)
    print(f"saved: {args.out}", flush=True)


if __name__ == "__main__":
    main()
