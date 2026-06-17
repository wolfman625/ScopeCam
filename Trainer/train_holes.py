#!/usr/bin/env python3
"""
train_holes.py — Train a YOLOv8 model to detect bullet holes, then export
a format you can compile for the Raspberry Pi AI HAT (Hailo) NPU.

This is the "zero-cost CPU proof-of-concept" step: you can run it on any
laptop (CPU works for the tiny 'n' model, a GPU just makes it faster) BEFORE
deciding whether the AI HAT is worth buying.

------------------------------------------------------------------------------
SETUP (one time)
------------------------------------------------------------------------------
    python -m venv .venv
    # Windows:
    .venv\\Scripts\\activate
    # Linux/Mac:
    source .venv/bin/activate

    pip install ultralytics

------------------------------------------------------------------------------
DATA
------------------------------------------------------------------------------
1. Collect images with scope_training.py (vary lighting/target/caliber/dist).
2. Label them (bounding box around each hole) in Roboflow or CVAT.
3. Export in "YOLOv8" format -> gives you images/ + labels/ folders.
4. Arrange as described in holes.yaml (train/val split ~80/20).

------------------------------------------------------------------------------
TRAIN
------------------------------------------------------------------------------
    python train_holes.py                 # train with defaults
    python train_holes.py --epochs 100    # train longer
    python train_holes.py --predict path/to/test.jpg   # try the trained model
    python train_holes.py --export onnx   # export best.pt to ONNX (for Hailo)

After training, your model is at:
    runs/detect/holes/weights/best.pt
------------------------------------------------------------------------------
"""

import argparse
import os
import sys


def train(args):
    from ultralytics import YOLO

    # 'yolov8n' = nano: smallest/fastest, ideal for an edge NPU and for a
    # CPU-only proof of concept. Step up to yolov8s if you have lots of data
    # and accuracy is lacking.
    model = YOLO(f"{args.model}.pt")  # downloads pretrained weights on first run

    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        patience=args.patience,   # early-stop if val stops improving
        project="runs/detect",
        name=args.name,
        exist_ok=True,
        # Augmentations help a lot when your dataset is small.
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,   # color/brightness jitter
        degrees=10, translate=0.1, scale=0.5, fliplr=0.5,
        mosaic=1.0,
    )

    best = os.path.join("runs/detect", args.name, "weights", "best.pt")
    print("\n" + "=" * 60)
    print(f"Done. Best weights: {best}")
    print("Validate:  python train_holes.py --predict some_target.jpg")
    print("Export:    python train_holes.py --export onnx")
    print("=" * 60)


def predict(args):
    from ultralytics import YOLO

    weights = args.weights or _default_best(args)
    if not os.path.exists(weights):
        sys.exit(f"Weights not found: {weights} (train first)")

    model = YOLO(weights)
    results = model.predict(
        source=args.predict,
        conf=args.conf,
        save=True,            # writes annotated image to runs/detect/predict/
    )
    for r in results:
        n = 0 if r.boxes is None else len(r.boxes)
        print(f"{r.path}: {n} hole(s) detected")
    print("Annotated images saved under runs/detect/predict/")


def export(args):
    from ultralytics import YOLO

    weights = args.weights or _default_best(args)
    if not os.path.exists(weights):
        sys.exit(f"Weights not found: {weights} (train first)")

    model = YOLO(weights)
    # ONNX is the bridge format: the Hailo Dataflow Compiler (DFC) ingests
    # ONNX and produces a .hef you load on the AI HAT. opset 11 is widely
    # compatible with the Hailo toolchain.
    path = model.export(format=args.export, opset=11, imgsz=args.imgsz)
    print(f"\nExported: {path}")
    if args.export == "onnx":
        print("Next: feed this ONNX to the Hailo DFC to compile a .hef:")
        print("  hailomz compile yolov8n --ckpt best.onnx --hw-arch hailo8l \\")
        print("      --calib-path dataset/images/train")


def _default_best(args):
    return os.path.join("runs/detect", args.name, "weights", "best.pt")


def main():
    p = argparse.ArgumentParser(description="Train/predict/export a bullet-hole YOLOv8 model")
    p.add_argument("--data", default="holes.yaml", help="dataset yaml")
    p.add_argument("--model", default="yolov8n", help="base model (yolov8n/s/m)")
    p.add_argument("--name", default="holes", help="run name under runs/detect")
    p.add_argument("--epochs", type=int, default=80)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--patience", type=int, default=20)
    p.add_argument("--conf", type=float, default=0.25, help="predict confidence threshold")
    p.add_argument("--weights", default=None, help="explicit weights for predict/export")
    p.add_argument("--predict", default=None, help="image/dir/glob to run inference on")
    p.add_argument("--export", default=None, choices=["onnx", "openvino", "tflite"],
                   help="export best.pt to this format")
    args = p.parse_args()

    if args.predict:
        predict(args)
    elif args.export:
        export(args)
    else:
        train(args)


if __name__ == "__main__":
    main()
