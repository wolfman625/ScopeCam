# ScopeCam — Shooting Range Analysis Tool

A touchscreen application for analyzing rifle/pistol groups at the range. It runs
on a Raspberry Pi 4B with an IMX477 camera mounted behind a spotting scope, shows
a live preview with a crosshair, and measures your shot groups in inches and MOA.

Designed for a 1024×600 touchscreen.

---

## Features

- **Live preview** — ~30 fps video stream from the scope camera with a green
  crosshair overlay (toggleable).
- **Grid calibration** — tap two points 1″ apart on a target grid; the app learns
  the pixels-per-inch scale and converts every measurement to inches and **MOA**.
- **Hit detection**
  - **Auto:** captures a reference frame, then diffs new frames to find fresh
    bullet holes (uses SciPy connected-component labelling). Throttled to spare
    the Pi's CPU.
  - **Manual:** tap each bullet hole directly on the preview.
- **Audio shot trigger** — listens to a microphone and auto-records a shot when
  the sound level exceeds an adjustable sensitivity threshold (with a cooldown to
  avoid double-triggers).
- **Group statistics** — live **ES** (extreme spread), **MR** (mean radius), and
  **CEP** (circular error probable), shown in inches and MOA, with a group
  centroid marker.
- **Capture & record** — save annotated JPEG stills or H.264 MP4 video.
- **CSV export** — export all shots (pixel / inch / MOA coordinates) to a
  timestamped CSV.
- **Storage auto-detect** — saves to an external SSD (`/mnt/ssd/scope`) when
  mounted, otherwise falls back to `~/Pictures/scope`.
- **Touch-friendly UI** — large buttons with press feedback, plus on-screen
  Quit and Shutdown controls.

---

## Hardware

- Raspberry Pi 4B
- Raspberry Pi High Quality Camera (IMX477)
- Spotting scope (camera mounted to the eyepiece)
- 1024×600 touchscreen display
- *(optional)* USB microphone for the audio shot trigger
- *(optional)* External SSD mounted at `/mnt/ssd`

---

## Requirements

System packages (Raspberry Pi OS):

- `python3`
- `picamera2` (preinstalled on recent Raspberry Pi OS images)
- `ffmpeg` (required for MP4 recording)

Python packages:

```bash
# Core image handling and math
pip3 install pillow numpy

# Optional: audio shot trigger
pip3 install sounddevice

# Optional: automatic hit detection
pip3 install scipy
```

> `sounddevice` and `scipy` are optional. If they aren't installed, the app still
> runs — the audio trigger and auto hit-detection features are simply disabled.

---

## Running

```bash
python3 Scope.py
```

The window opens at the top-left of the screen at a fixed 1024×600 size.

---

## Usage

1. **Calibrate** — Press **Start Calibration**, then tap two points on the target
   grid that are exactly 1″ apart. The app computes pixels-per-inch and enables
   inch/MOA measurements.
2. **Set a reference** (for auto detection) — Press **Set Reference** to capture a
   clean baseline of the target, then enable **Auto: ON**. New bullet holes are
   detected automatically. You can also use **+ Manual** to tap holes yourself.
3. **(Optional) Audio trigger** — Pick your microphone, set the **Sensitivity**
   slider, and press **Start Listening**. The slider takes effect live.
4. **Review stats** — ES / MR / CEP update automatically as shots are added, both
   on the preview overlay and in the panel.
5. **Save** — Use **📷** for an annotated still, **Record** for video, or **CSV**
   to export the shot data.

### Keyboard shortcuts

| Key      | Action                         |
| -------- | ------------------------------ |
| `Space`  | Capture annotated still        |
| `X`      | Toggle crosshair               |
| `Delete` | Clear all shots                |
| `Escape` | Cancel calibration / manual hit|

---

## Output files

All files are timestamped and written to the active save directory
(`/mnt/ssd/scope` if an SSD is mounted, else `~/Pictures/scope`):

| Type     | Filename pattern        |
| -------- | ----------------------- |
| Still    | `scope_YYYYMMDD_HHMMSS.jpg` |
| Video    | `scope_YYYYMMDD_HHMMSS.mp4` |
| Data     | `session_YYYYMMDD_HHMMSS.csv` |

The CSV columns are: `Shot, X_px, Y_px, X_in, Y_in, X_moa, Y_moa`.

---

## Measurements

- **Calibration** establishes pixels-per-inch from two points 1″ apart.
- **MOA** is computed at a fixed distance of **100 yards** (see
  `CROSSHAIR_DISTANCE_YARDS`), using `1 MOA ≈ 1.047″ at 100 yd`.
- **ES** — largest center-to-center distance between any two shots.
- **MR** — average distance of all shots from the group centroid.
- **CEP** — median shot distance from the centroid.

> If you shoot at a distance other than 100 yards, update
> `CROSSHAIR_DISTANCE_YARDS` near the top of `Scope.py`.

---

## Shutdown button

The on-screen **Shutdown** button runs `sudo shutdown -h now`. For it to work
without a password prompt, add the following to `/etc/sudoers` (via `visudo`),
scoped narrowly to just the shutdown command:

```
pi ALL=(ALL) NOPASSWD: /sbin/shutdown
```

---

## Configuration

Key constants are defined near the top of `Scope.py`:

| Constant                   | Purpose                                        |
| -------------------------- | ---------------------------------------------- |
| `PREVIEW_W` / `PREVIEW_H`  | Camera preview resolution                      |
| `SSD_MOUNT`                | External SSD mount point (`/mnt/ssd`)          |
| `FALLBACK_DIR`             | Local save folder when no SSD is present       |
| `CROSSHAIR_DISTANCE_YARDS` | Distance used for MOA conversion (default 100) |
| `DETECT_INTERVAL`          | Minimum seconds between auto-detect passes     |
| `audio_cooldown`           | Minimum seconds between audio triggers         |

---

## Notes & limitations

- Auto hit-detection works best against a clean, evenly-lit target. Lighting
  changes or scope movement can produce false hits; re-set the reference frame if
  needed.
- This is a single-user, offline tool — it has no network features.
