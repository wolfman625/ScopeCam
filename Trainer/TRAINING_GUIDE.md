# Bullet-Hole Detection — End-to-End Guide

This guide takes you from **collecting images** to a **trained model running on the
Raspberry Pi AI HAT (Hailo NPU)**. It uses the files in this folder:

| File | Role |
|------|------|
| `scope_training.py` | On-Pi tool to **collect** training images + video clips |
| `holes.yaml` | YOLO **dataset config** (tells training where data is + the class) |
| `train_holes.py` | **Train / test / export** the model on your laptop |
| `Scope.py` | The live analysis app the model will eventually plug into |

The whole thing can be done for **$0** first (CPU proof-of-concept). Only buy the
$350 AI HAT once Step 6 proves the model actually finds holes.

---

## Overview

```
┌──────────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. COLLECT   │ → │ 2. LABEL │ → │ 3. SPLIT │ → │ 4. TRAIN │ → │ 5. TEST  │
│ scope_       │   │ Roboflow │   │ train/val│   │ train_   │   │ --predict│
│ training.py  │   │ or CVAT  │   │ holes.yaml│  │ holes.py │   │          │
└──────────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                                                                      │
                                          ┌───────────────────────────┘
                                          ▼
                              ┌──────────┐   ┌──────────────┐
                              │ 6. EXPORT│ → │ 7. DEPLOY    │
                              │ --export │   │ Hailo .hef   │
                              │ onnx     │   │ on AI HAT    │
                              └──────────┘   └──────────────┘
```

---

## Step 1 — Collect images (on the Raspberry Pi)

Run the collection tool on the Pi:

```bash
python3 scope_training.py
```

In the app:

1. **Name a session** (e.g. `rangeday1`) and press **Start Session**.
   A timestamped folder is created on the SSD (or `~/Pictures/scope_training`).
2. Set the **Conditions** dropdowns (lighting / target / caliber / distance)
   to match what you're shooting. These are saved into `manifest.csv`.
3. Capture images:
   - **📷 CAPTURE** (or press **Space**) — one full-res 2028×1520 still.
   - **Burst** — auto-capture 5–50 frames at ~2 fps (great for many angles).
   - **⏺ Record Clip** — record an MP4, then **Extract Frames** pulls JPEGs
     from it at your chosen fps and adds them to the same session + manifest.

### How many images? Aim for variety, not just volume
- **Minimum useful:** ~150–300 labeled holes.
- **Good:** 500–1500 images.
- **Great:** 2000–3000+.

Vary **everything** by changing the Conditions dropdowns between sessions:
different lighting, target faces, calibers (hole sizes), and distances.
A model trained only on bright-sun paper bulls at 100 yd will fail on an
overcast steel target. The `manifest.csv` lets you check your coverage later.

**Copy the session folders off the Pi** (USB stick, `scp`, or the SSD) to the
laptop where you'll label and train.

---

## Step 2 — Label the images

Labeling = drawing a tight bounding box around **every** bullet hole in each
image and tagging it as class `hole`.

Pick one tool:

- **Roboflow** (https://roboflow.com) — easiest; web-based, free tier, has
  auto-augmentation and one-click "YOLOv8" export. **Recommended.**
- **CVAT** (https://cvat.ai) — free/open-source, self-hostable.
- **labelImg** — simple desktop app, exports YOLO txt directly.

Tips:
- Box the hole itself, not the surrounding tear. Be consistent.
- Label **every** hole in the frame — missed holes teach the model to ignore them.
- Don't skip blurry/edge cases; they make the model robust.

When done, **export in "YOLOv8" format**. You'll get matching folders:
```
images/   (your .jpg files)
labels/   (one .txt per image: "0 x_center y_center width height", normalized 0–1)
```

---

## Step 3 — Arrange the dataset (matches `holes.yaml`)

Create this exact structure next to `train_holes.py`:

```
dataset/
  images/
    train/   <- ~80% of your .jpg files
    val/     <- ~20% of your .jpg files
  labels/
    train/   <- the .txt files matching images/train (same basenames)
    val/     <- the .txt files matching images/val
```

`holes.yaml` already points at this layout:
```yaml
path: ./dataset
train: images/train
val: images/val
names:
  0: hole
```

> Roboflow can produce this split for you automatically on export — if so, just
> drop its `train/` and `valid/` folders in and adjust `holes.yaml` paths to match.

---

## Step 4 — Train (on your laptop, CPU is fine)

One-time setup:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install ultralytics
```

Train:

```bash
python train_holes.py                 # YOLOv8n, 80 epochs, sensible defaults
python train_holes.py --epochs 150    # train longer if accuracy is low
python train_holes.py --model yolov8s # bigger model if you have lots of data
```

When it finishes, your model is at:
```
runs/detect/holes/weights/best.pt
```

Watch the printed **mAP50** metric — higher is better. >0.8 on `val` is a strong
sign the model learned holes well. If it's low, collect/label more varied data.

---

## Step 5 — Test the model

```bash
python train_holes.py --predict path/to/a_target_photo.jpg
```

This saves an annotated image (boxes drawn on detected holes) under
`runs/detect/predict/` and prints how many holes it found. Try several photos
the model has **never seen**. If it reliably boxes the holes — the concept is
proven, and only now is the AI HAT worth buying.

---

## Step 6 — Export for the AI HAT

```bash
python train_holes.py --export onnx
```

This converts `best.pt` → `best.onnx` (opset 11), the format the Hailo
toolchain ingests. The script prints the next command.

---

## Step 7 — Compile + deploy on the Pi AI HAT (Hailo)

On a machine with the **Hailo Dataflow Compiler / Model Zoo** installed:

```bash
hailomz compile yolov8n \
    --ckpt best.onnx \
    --hw-arch hailo8l \
    --calib-path dataset/images/train
```

- `--hw-arch hailo8l` = the AI HAT 2 (Hailo-8L). Use `hailo8` for the 26-TOPS HAT.
- `--calib-path` points at a sample of your training images for quantization.

The output is a **`.hef`** file. Copy it to the Pi and load it with the Hailo
runtime (HailoRT / `degirum` / `picamera2` Hailo post-processing) to run
real-time inference on the NPU. From there you wire detections back into
`Scope.py` to auto-mark hits instead of the current pixel-difference detector.

---

## Quick reference — commands

```bash
# On the Pi: collect
python3 scope_training.py

# On the laptop: train / test / export
pip install ultralytics
python train_holes.py                              # train
python train_holes.py --predict target.jpg         # test
python train_holes.py --export onnx                # export

# Compile for the HAT
hailomz compile yolov8n --ckpt best.onnx --hw-arch hailo8l --calib-path dataset/images/train
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ultralytics` install slow/large | It pulls PyTorch; that's normal. CPU-only wheel is fine. |
| Low mAP / misses holes | More varied data + more labels; train more epochs. |
| Detects shadows as holes | Add negative/varied lighting images and re-label carefully. |
| `Extract Frames` says ffmpeg not found | `sudo apt install ffmpeg` on the Pi. |
| ONNX won't compile in Hailo | Keep `imgsz=640`, opset 11 (already set); check Hailo version notes. |
```
