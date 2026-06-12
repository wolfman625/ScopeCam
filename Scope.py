#!/usr/bin/env python3
"""
ScopeCam - Shooting Range Analysis Tool
Raspberry Pi 4B + IMX477 + Spotting Scope
1024x600 display — with audio shot trigger
"""

import tkinter as tk
from tkinter import ttk, messagebox
import time
import os
import math
import csv
import subprocess
import threading
from datetime import datetime
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, Quality
from picamera2.outputs import FfmpegOutput
from PIL import Image, ImageTk, ImageDraw
import numpy as np

# optional audio — install with: pip3 install sounddevice
try:
    import sounddevice as sd
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

# optional auto hit-detection — install with: pip3 install scipy
try:
    from scipy import ndimage as _ndimage
    SCIPY_AVAILABLE = True
except ImportError:
    _ndimage = None
    SCIPY_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────
PREVIEW_W   = 720
PREVIEW_H   = 490
PANEL_W     = 304
WIN_H       = 600
WIN_W       = PREVIEW_W + PANEL_W

BG      = "#f5f5f5"
PANEL   = "#e0e0e0"
ACCENT  = "#0057b7"
TEXT    = "#111111"
MUTED   = "#555555"
GREEN   = "#1a6e1a"
BLUE    = "#0057b7"
RED     = "#b91c1c"
DARK    = "#c8c8c8"
ORANGE  = "#c45c00"

SSD_MOUNT    = "/mnt/ssd"
FALLBACK_DIR = os.path.expanduser("~/Pictures/scope")
CROSSHAIR_DISTANCE_YARDS = 100   # used when converting pixels → MOA

# ── State ──────────────────────────────────────────────────────────────────────
picam2          = None
recording       = False
running         = True
show_crosshair  = True
_cam_lock       = threading.Lock()

pixels_per_inch  = None    # set after grid calibration
calibration_pts  = []      # up to 2 click points for calibration
shots            = []      # list of recorded shot dicts
reference_frame  = None    # baseline frame for auto hit-detection
detecting        = False   # whether auto hit-detection is active

# audio state
audio_listening  = False
audio_cooldown   = 2.0     # minimum seconds between triggers
_last_shot_time  = 0.0
_audio_stream    = None

# auto hit-detection runs at most this often (seconds) to spare the Pi's CPU
DETECT_INTERVAL  = 0.25

# Cached group statistics — recomputed only when shots list changes, not every frame
_group_stats     = None    # dict with keys: cx, cy, es_px, mr_px, cep_px


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_save_dir():
    """Return save path: SSD mount if present, otherwise ~/Pictures/scope.
    Validates the resolved path stays within the home directory.
    """
    home     = os.path.expanduser("~")
    ssd_path = os.path.join(SSD_MOUNT, "scope")
    if os.path.ismount(SSD_MOUNT):
        candidate = ssd_path
    else:
        candidate = FALLBACK_DIR
    # Resolve to catch any symlink tricks
    resolved = os.path.realpath(os.path.abspath(candidate))
    if not (resolved.startswith(os.path.realpath(home)) or
            resolved.startswith(os.path.realpath(SSD_MOUNT))):
        raise ValueError(f"Save directory outside allowed locations: {candidate}")
    os.makedirs(resolved, exist_ok=True)
    return resolved

def _lighten(hex_color, amount=32):
    """Return a slightly lighter version of a hex colour string."""
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(
        min(255, r+amount), min(255, g+amount), min(255, b+amount))

def px_to_inches(px):
    """Convert pixel distance to inches using the current calibration."""
    return px / pixels_per_inch if pixels_per_inch else None

def inches_to_moa(inches, yards=CROSSHAIR_DISTANCE_YARDS):
    """Convert an inch measurement at a given distance to MOA."""
    return inches / (1.047 * yards / 100)

def dist_px(a, b):
    """Euclidean distance between two (x, y) pixel points."""
    return math.hypot(a[0]-b[0], a[1]-b[1])

def _recompute_group_stats():
    """Recompute centroid, ES, MR and CEP from the current shots list.
    Result stored in _group_stats; called only when shots change.
    """
    global _group_stats
    if len(shots) < 2:
        _group_stats = None
        return
    xs = [s["x"] for s in shots]
    ys = [s["y"] for s in shots]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    radii  = [dist_px((s["x"], s["y"]), (cx, cy)) for s in shots]
    es_px  = max(dist_px((shots[i]["x"], shots[i]["y"]),
                          (shots[j]["x"], shots[j]["y"]))
                 for i in range(len(shots))
                 for j in range(i + 1, len(shots)))
    mr_px  = sum(radii) / len(radii)
    cep_px = sorted(radii)[len(radii) // 2]
    _group_stats = {"cx": cx, "cy": cy,
                    "es_px": es_px, "mr_px": mr_px, "cep_px": cep_px}


# ── Drawing ────────────────────────────────────────────────────────────────────

def draw_crosshair(img):
    """Draw a green crosshair with black shadow at the centre of img."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    cx, cy = w//2, h//2
    gap, arm, tick, lw = 18, 55, 8, 2
    # Draw black shadow offset by (1,1) then green on top
    for (dx, dy), col in [((1,1),(0,0,0)), ((0,0),(0,220,0))]:
        draw.line([(cx-gap-arm+dx,cy+dy),(cx-gap+dx,cy+dy)], fill=col, width=lw)
        draw.line([(cx+gap+dx,cy+dy),(cx+gap+arm+dx,cy+dy)], fill=col, width=lw)
        draw.line([(cx+dx,cy-gap-arm+dy),(cx+dx,cy-gap+dy)], fill=col, width=lw)
        draw.line([(cx+dx,cy+gap+dy),(cx+dx,cy+gap+arm+dy)], fill=col, width=lw)
        draw.ellipse([(cx-2+dx,cy-2+dy),(cx+2+dx,cy+2+dy)], fill=col)
        for tx,ty,bx,by in [
            (cx-gap-arm,cy-tick//2,cx-gap-arm,cy+tick//2),
            (cx+gap+arm,cy-tick//2,cx+gap+arm,cy+tick//2),
            (cx-tick//2,cy-gap-arm,cx+tick//2,cy-gap-arm),
            (cx-tick//2,cy+gap+arm,cx+tick//2,cy+gap+arm),
        ]:
            draw.line([(tx+dx,ty+dy),(bx+dx,by+dy)], fill=col, width=lw)
    return img

def draw_calibration(img):
    """Overlay calibration click points and connecting line on img."""
    draw = ImageDraw.Draw(img)
    for i, (x, y) in enumerate(calibration_pts):
        r = 6
        draw.ellipse([(x-r,y-r),(x+r,y+r)], outline=(255,165,0), width=2)
        draw.text((x+8, y-8), f"P{i+1}", fill=(255,165,0))
    if len(calibration_pts) == 2:
        draw.line([calibration_pts[0], calibration_pts[1]], fill=(255,165,0), width=1)
    return img

def draw_shots(img):
    """Draw all recorded shot markers and group statistics overlay on img.
    Group stats are read from the _group_stats cache (updated on shot changes).
    """
    if not shots:
        return img
    draw = ImageDraw.Draw(img)
    for s in shots:
        x, y, n = s["x"], s["y"], s["n"]
        r = 8
        draw.ellipse([(x-r+1,y-r+1),(x+r+1,y+r+1)], fill=(0,0,0))
        draw.ellipse([(x-r,y-r),(x+r,y+r)], fill=(220,30,30))
        draw.ellipse([(x-r,y-r),(x+r,y+r)], outline=(255,255,255), width=1)
        draw.text((x-4, y-6), str(n), fill=(255,255,255))

    if pixels_per_inch and _group_stats:
        cx, cy = _group_stats["cx"], _group_stats["cy"]
        es_px, mr_px, cep_px = (_group_stats["es_px"],
                                 _group_stats["mr_px"],
                                 _group_stats["cep_px"])
        # Group centroid crosshair
        draw.line([(cx-6,cy),(cx+6,cy)], fill=(255,255,0), width=2)
        draw.line([(cx,cy-6),(cx,cy+6)], fill=(255,255,0), width=2)

        lines = [
            f"Shots: {len(shots)}",
            f"ES:  {px_to_inches(es_px):.2f}\"  {inches_to_moa(px_to_inches(es_px)):.2f} MOA",
            f"MR:  {px_to_inches(mr_px):.2f}\"  {inches_to_moa(px_to_inches(mr_px)):.2f} MOA",
            f"CEP: {px_to_inches(cep_px):.2f}\"  {inches_to_moa(px_to_inches(cep_px)):.2f} MOA",
        ]
        bx, by = 8, PREVIEW_H - 12 - len(lines)*16
        draw.rectangle([(bx-4,by-4),(bx+205,by+len(lines)*16+4)], fill=(0,0,0,160))
        for i, line in enumerate(lines):
            draw.text((bx, by+i*16), line, fill=(255,255,255))
    return img

def detect_hits(old_frame, new_frame, threshold=30, min_area=40):
    """Compare two frames and return (cx, cy) of new dark regions (bullet holes).
    Uses scipy ndimage for connected-component labelling.
    Returns empty list if scipy is not installed.
    """
    if not SCIPY_AVAILABLE:
        return []
    old_g = np.mean(old_frame, axis=2)
    new_g = np.mean(new_frame, axis=2)
    diff  = old_g.astype(int) - new_g.astype(int)
    mask  = (diff > threshold).astype(np.uint8)
    labeled, num = _ndimage.label(mask)
    hits = []
    for i in range(1, num+1):
        region = np.where(labeled == i)
        if len(region[0]) >= min_area:
            cy = int(np.mean(region[0]))
            cx = int(np.mean(region[1]))
            hits.append((cx, cy))
    return hits


# ── Audio ──────────────────────────────────────────────────────────────────────

def list_input_devices():
    """Return list of (index, name) for all available audio input devices."""
    if not AUDIO_AVAILABLE:
        return []
    devices = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devices.append((i, d["name"]))
    except Exception:
        pass
    return devices

def start_audio_listen(device_index, threshold_getter, on_shot_cb):
    """Start a background audio input stream.
    Calls on_shot_cb() when RMS amplitude exceeds the current threshold,
    subject to audio_cooldown between consecutive triggers.
    threshold_getter is a callable returning the live trigger level, so the
    sensitivity slider takes effect immediately without restarting the stream.
    """
    global _audio_stream, _last_shot_time, audio_listening
    if not AUDIO_AVAILABLE:
        return False

    RATE       = 44100
    BLOCK_SIZE = 1024

    def callback(indata, frames, time_info, status):
        global _last_shot_time
        rms = float(np.sqrt(np.mean(indata**2)))
        now = time.monotonic()
        if rms > threshold_getter() and (now - _last_shot_time) > audio_cooldown:
            _last_shot_time = now
            on_shot_cb()

    try:
        _audio_stream = sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=RATE,
            blocksize=BLOCK_SIZE,
            callback=callback,
        )
        _audio_stream.start()
        audio_listening = True
        return True
    except Exception as e:
        print(f"Audio error: {e}")
        return False

def stop_audio_listen():
    """Stop and close the active audio input stream."""
    global _audio_stream, audio_listening
    if _audio_stream:
        try:
            _audio_stream.stop()
            _audio_stream.close()
        except Exception:
            pass
        _audio_stream = None
    audio_listening = False


# ── Camera ─────────────────────────────────────────────────────────────────────

def camera_init():
    """Create and start the Picamera2 instance in video mode.
    Video mode is used even for stills so the preview stream stays active.
    """
    global picam2
    picam2 = Picamera2()
    config = picam2.create_video_configuration(
        main={"size": (PREVIEW_W, PREVIEW_H), "format": "RGB888"},
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(0.5)  # allow the sensor and AGC to settle

def capture_annotated():
    """Grab a frame and save it as a JPEG with crosshair and shot overlays.
    Acquires _cam_lock so the preview loop cannot run concurrently.
    """
    save_dir = get_save_dir()
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(save_dir, f"scope_{ts}.jpg")
    with _cam_lock:
        frame = picam2.capture_array("main")
    img = Image.fromarray(frame)
    if show_crosshair:
        img = draw_crosshair(img)
    img = draw_shots(img)
    img.save(path, quality=95)
    return path

def export_csv():
    """Write all shots to a timestamped CSV file and return the path."""
    if not shots:
        return None
    save_dir = get_save_dir()
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(save_dir, f"session_{ts}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Shot","X_px","Y_px","X_in","Y_in","X_moa","Y_moa"])
        def fmt(v): return f"{v:.3f}" if v is not None else ""
        for s in shots:
            w.writerow([s["n"], s["x"], s["y"],
                        fmt(s.get("inch_x")), fmt(s.get("inch_y")),
                        fmt(s.get("moa_x")),  fmt(s.get("moa_y"))])
    return path

def start_recording():
    """Begin H264 MP4 recording. Acquires _cam_lock while attaching encoder."""
    save_dir = get_save_dir()
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    path    = os.path.join(save_dir, f"scope_{ts}.mp4")
    encoder = H264Encoder(bitrate=10000000)
    output  = FfmpegOutput(path)
    with _cam_lock:
        picam2.start_encoder(encoder, output, quality=Quality.HIGH)
    return path

def stop_recording():
    """Stop the H264 encoder and finalise the MP4. Acquires _cam_lock."""
    with _cam_lock:
        picam2.stop_encoder()


# ── App ────────────────────────────────────────────────────────────────────────

class ScopeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ScopeCam — Range Analysis")
        self.configure(bg=BG)
        self.geometry(f"{WIN_W}x{WIN_H}+0+0")
        self.resizable(False, False)
        self._mode         = "normal"   # current interaction mode
        self._build_ui()
        self._bind_keys()
        threading.Thread(target=self._hw_init, daemon=True).start()
        self._check_ssd()

    # ── hardware init ──────────────────────────────────────────────────────────

    def _hw_init(self):
        try:
            camera_init()
            self.after(0, lambda: self.status("Ready — calibrate grid first"))
            self._start_preview_loop()
            self.after(500, self._populate_audio_devices)
        except Exception as e:
            self.after(0, lambda: self.status(f"Init error: {e}"))

    # ── SSD monitor ───────────────────────────────────────────────────────────

    def _check_ssd(self):
        """Poll every 3 seconds and update the SSD/local storage indicator.
        Stops automatically when the app is closed.
        """
        if not running:
            return
        if os.path.ismount(SSD_MOUNT):
            self.ssd_var.set("💾 SSD")
            self.ssd_label.config(fg=GREEN)
        else:
            self.ssd_var.set("📂 Local")
            self.ssd_label.config(fg=MUTED)
        self.after(3000, self._check_ssd)

    # ── preview loop ───────────────────────────────────────────────────────────

    def _start_preview_loop(self):
        """Daemon thread: grab frames at ~30fps, overlay annotations, push to UI."""
        def loop():
            global reference_frame
            last_detect = 0.0
            while running:
                try:
                    with _cam_lock:
                        frame = picam2.capture_array("main")
                    # Auto hit-detection: throttled so it doesn't run on every
                    # ~30fps frame (full-frame diff + labelling is CPU-heavy).
                    now = time.monotonic()
                    if (detecting and reference_frame is not None
                            and now - last_detect >= DETECT_INTERVAL):
                        last_detect = now
                        hits = detect_hits(reference_frame, frame)
                        if hits:
                            for (hx, hy) in hits:
                                self.after(0, self._add_hit, hx, hy)
                            reference_frame = frame.copy()
                    # Build annotated preview image
                    img = Image.fromarray(frame)
                    if show_crosshair:
                        img = draw_crosshair(img)
                    if calibration_pts:
                        img = draw_calibration(img)
                    img = draw_shots(img)
                    imgtk = ImageTk.PhotoImage(image=img)
                    self.after(0, self._update_preview, imgtk)
                except Exception as e:
                    self.after(0, lambda msg=str(e): self.status(f"Preview error: {msg}"))
                    time.sleep(0.5)
                time.sleep(0.033)  # ~30fps
        threading.Thread(target=loop, daemon=True).start()

    def _update_preview(self, imgtk):
        """Swap latest frame into the preview label (main thread only)."""
        self.preview_label.imgtk = imgtk  # prevent GC from freeing the image
        self.preview_label.config(image=imgtk)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Left: full-height preview area
        left = tk.Frame(self, bg="black", width=PREVIEW_W, height=WIN_H)
        left.pack(side=tk.LEFT, fill=tk.BOTH)
        left.pack_propagate(False)
        self.preview_label = tk.Label(left, bg="black",
                                      text="Initialising…",
                                      fg="#888", font=("Courier New", 11))
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        self.preview_label.bind("<Button-1>", self._on_preview_click)

        # Right: control panel
        panel = tk.Frame(self, bg=PANEL, width=PANEL_W, height=WIN_H)
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)
        pad = {"padx": 12}

        # Title + SSD indicator
        tf = tk.Frame(panel, bg=PANEL)
        tf.pack(fill=tk.X, pady=(8,0), **pad)
        tk.Label(tf, text="⬡ SCOPECAM", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 11, "bold")).pack(side=tk.LEFT)
        self.ssd_var = tk.StringVar(value="…")
        self.ssd_label = tk.Label(tf, textvariable=self.ssd_var,
                                  bg=PANEL, fg=MUTED, font=("Courier New", 7))
        self.ssd_label.pack(side=tk.RIGHT)

        self._sep(panel)

        # ── 1. CALIBRATION ─────────────────────────────────────────────────────
        tk.Label(panel, text="1  GRID CALIBRATION", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)
        tk.Label(panel, text="Click 2 points exactly 1\" apart on grid",
                 bg=PANEL, fg=MUTED, font=("Courier New", 7)).pack(anchor="w", **pad)
        self.cal_status = tk.StringVar(value="Not calibrated")
        tk.Label(panel, textvariable=self.cal_status, bg=PANEL, fg=ORANGE,
                 font=("Courier New", 7)).pack(anchor="w", **pad)
        cbf = tk.Frame(panel, bg=PANEL)
        cbf.pack(anchor="w", pady=(2,0), **pad)
        self.cal_btn = tk.Button(cbf, text="Start Calibration",
                                 bg=ORANGE, fg="white", relief="flat",
                                 font=("Courier New", 8, "bold"),
                                 padx=6, pady=3, command=self._start_calibration)
        self.cal_btn.pack(side=tk.LEFT, padx=(0,4))
        self._add_pop(self.cal_btn)
        b = tk.Button(cbf, text="Reset", bg=DARK, fg=TEXT, relief="flat",
                      font=("Courier New", 8), padx=6, pady=3,
                      command=self._reset_calibration)
        b.pack(side=tk.LEFT)
        self._add_pop(b)

        self._sep(panel)

        # ── 2. HIT DETECTION ───────────────────────────────────────────────────
        tk.Label(panel, text="2  HIT DETECTION", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)
        hbf = tk.Frame(panel, bg=PANEL)
        hbf.pack(anchor="w", pady=(2,0), **pad)
        self.ref_btn = tk.Button(hbf, text="Set Reference",
                                 bg=BLUE, fg="white", relief="flat",
                                 font=("Courier New", 8, "bold"),
                                 padx=6, pady=3, command=self._set_reference)
        self.ref_btn.pack(side=tk.LEFT, padx=(0,4))
        self._add_pop(self.ref_btn)
        self.detect_btn = tk.Button(hbf, text="Auto: OFF",
                                    bg=DARK, fg=TEXT, relief="flat",
                                    font=("Courier New", 8, "bold"),
                                    padx=6, pady=3, command=self._toggle_detect)
        self.detect_btn.pack(side=tk.LEFT, padx=(0,4))
        self._add_pop(self.detect_btn)
        mb = tk.Button(hbf, text="+ Manual", bg=DARK, fg=TEXT, relief="flat",
                       font=("Courier New", 8, "bold"), padx=6, pady=3,
                       command=self._start_manual_hit)
        mb.pack(side=tk.LEFT)
        self._add_pop(mb)
        self.ref_status = tk.StringVar(value="No reference set")
        tk.Label(panel, textvariable=self.ref_status, bg=PANEL, fg=MUTED,
                 font=("Courier New", 7)).pack(anchor="w", **pad)

        self._sep(panel)

        # ── 3. AUDIO SHOT TRIGGER ───────────────────────────────────────────────
        tk.Label(panel, text="3  AUDIO SHOT TRIGGER", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)

        if not AUDIO_AVAILABLE:
            tk.Label(panel, text="Install sounddevice:  pip3 install sounddevice",
                     bg=PANEL, fg=RED, font=("Courier New", 7),
                     wraplength=280).pack(anchor="w", **pad)
        else:
            # Microphone device selector
            df = tk.Frame(panel, bg=PANEL)
            df.pack(anchor="w", pady=(2,0), **pad)
            tk.Label(df, text="Mic:", bg=PANEL, fg=MUTED,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            self.audio_dev_var = tk.StringVar(value="No devices found")
            self.audio_menu = ttk.Combobox(df, textvariable=self.audio_dev_var,
                                           width=22, font=("Courier New", 7),
                                           state="readonly")
            self.audio_menu.pack(side=tk.LEFT, padx=(4,0))

            # Sensitivity slider (lower = more sensitive)
            tf2 = tk.Frame(panel, bg=PANEL)
            tf2.pack(anchor="w", pady=(2,0), **pad)
            tk.Label(tf2, text="Sensitivity:", bg=PANEL, fg=MUTED,
                     font=("Courier New", 7)).pack(side=tk.LEFT)
            self.threshold_var = tk.DoubleVar(value=0.15)
            tk.Scale(tf2, variable=self.threshold_var,
                     from_=0.05, to=0.5, resolution=0.01,
                     orient=tk.HORIZONTAL, length=150,
                     bg=PANEL, fg=TEXT, highlightthickness=0,
                     font=("Courier New", 7)).pack(side=tk.LEFT, padx=(4,0))

            # Listen toggle button
            abf = tk.Frame(panel, bg=PANEL)
            abf.pack(anchor="w", pady=(2,0), **pad)
            self.audio_btn = tk.Button(abf, text="🎤 Start Listening",
                                       bg=DARK, fg=GREEN, relief="flat",
                                       font=("Courier New", 8, "bold"),
                                       padx=6, pady=3,
                                       command=self._toggle_audio)
            self.audio_btn.pack(side=tk.LEFT, padx=(0,4))
            self._add_pop(self.audio_btn)
            self.audio_status = tk.StringVar(value="Not listening")
            tk.Label(panel, textvariable=self.audio_status, bg=PANEL, fg=MUTED,
                     font=("Courier New", 7)).pack(anchor="w", **pad)

        self._sep(panel)

        # ── 4. GROUP STATISTICS ────────────────────────────────────────────────
        tk.Label(panel, text="4  GROUP STATISTICS", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)
        self.stats_var = tk.StringVar(value="No shots yet")
        tk.Label(panel, textvariable=self.stats_var, bg=PANEL, fg=TEXT,
                 font=("Courier New", 7), justify=tk.LEFT,
                 wraplength=280).pack(anchor="w", **pad)

        sbf = tk.Frame(panel, bg=PANEL)
        sbf.pack(anchor="w", pady=(3,0), **pad)
        for label, fg_, cmd in [
            ("Clear", RED,   self._clear_shots),
            ("CSV",   GREEN, self._export_csv),
            ("📷",    BLUE,  self._capture),
        ]:
            b = tk.Button(sbf, text=label,
                          bg=DARK, fg=fg_, relief="flat",
                          font=("Courier New", 8, "bold"),
                          padx=8, pady=3, command=cmd)
            b.pack(side=tk.LEFT, padx=(0,3))
            self._add_pop(b)

        self._sep(panel)

        # ── Record + Crosshair toggle ──────────────────────────────────────────
        bf2 = tk.Frame(panel, bg=PANEL)
        bf2.pack(anchor="w", pady=(0,2), **pad)
        self.rec_btn = tk.Button(bf2, text="⏺ Record",
                                 bg=DARK, fg=GREEN, relief="flat",
                                 font=("Courier New", 8, "bold"),
                                 padx=8, pady=3, command=self._toggle_record)
        self.rec_btn.pack(side=tk.LEFT, padx=(0,6))
        self._add_pop(self.rec_btn)
        self.xhair_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bf2, text="Crosshair", variable=self.xhair_var,
                       bg=PANEL, fg=TEXT, selectcolor=DARK,
                       activebackground=PANEL, font=("Courier New", 8),
                       command=self._toggle_crosshair).pack(side=tk.LEFT)

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Initialising…")
        tk.Label(panel, textvariable=self.status_var, bg=PANEL, fg=MUTED,
                 font=("Courier New", 7), wraplength=280).pack(
                 anchor="w", pady=(3,0), **pad)

        # ── Bottom: Quit + Shutdown side by side ──────────────────────────────
        bottom_frame = tk.Frame(panel, bg=PANEL)
        bottom_frame.pack(side=tk.BOTTOM, pady=5, padx=12, fill=tk.X)

        quit_btn = tk.Button(bottom_frame, text="✕  QUIT", bg=DARK, fg=RED,
                             relief="flat", font=("Courier New", 9, "bold"),
                             width=10, pady=4,
                             activebackground=_lighten(DARK),
                             command=self.on_close)
        quit_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        self._add_pop(quit_btn)

        shut_btn = tk.Button(bottom_frame, text="⏻  SHUTDOWN", bg=RED, fg="white",
                             relief="flat", font=("Courier New", 9, "bold"),
                             width=10, pady=4,
                             activebackground="#8b1111",
                             command=self._shutdown)
        shut_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))
        self._add_pop(shut_btn)

    def _sep(self, parent):
        ttk.Separator(parent, orient="horizontal").pack(
            fill=tk.X, pady=3, padx=12)

    @staticmethod
    def _add_pop(btn):
        """Touchscreen press/release visual pop: lighten + sunken on press."""
        def on_press(e):
            btn._pop_bg = btn.cget("bg")
            btn.config(relief="sunken", bg=_lighten(btn.cget("bg")))
        def on_release(e):
            btn.config(relief="flat", bg=btn._pop_bg)
        btn.bind("<ButtonPress-1>",   on_press,   add="+")
        btn.bind("<ButtonRelease-1>", on_release, add="+")

    # ── key bindings ───────────────────────────────────────────────────────────

    def _bind_keys(self):
        self.bind("<space>",  lambda e: self._capture())
        self.bind("<Escape>", lambda e: self._cancel_mode())
        self.bind("<x>",      lambda e: self._toggle_crosshair_key())
        self.bind("<Delete>", lambda e: self._clear_shots())

    # ── audio ──────────────────────────────────────────────────────────────────

    def _populate_audio_devices(self):
        """Fill the microphone dropdown with available input devices."""
        if not AUDIO_AVAILABLE:
            return
        devs = list_input_devices()
        if devs:
            names = [f"{i}: {n}" for i, n in devs]
            self.audio_menu["values"] = names
            self.audio_menu.current(0)
            self._audio_device_list = devs
        else:
            self.audio_menu["values"] = ["No input devices found"]
            self._audio_device_list = []

    def _toggle_audio(self):
        """Start or stop the audio shot-trigger listener."""
        if not audio_listening:
            devs = getattr(self, "_audio_device_list", [])
            if not devs:
                self.status("No microphone found")
                return
            sel = self.audio_menu.current()
            if sel < 0:
                sel = 0
            dev_idx = devs[sel][0]
            ok = start_audio_listen(dev_idx, self.threshold_var.get,
                                    self._on_audio_shot)
            if ok:
                self.audio_btn.config(text="🔇 Stop Listening", bg=RED, fg="white")
                self.audio_status.set("Listening for shots…")
                self.status("Audio trigger active — fire when ready")
            else:
                self.status("Failed to open microphone")
        else:
            stop_audio_listen()
            self.audio_btn.config(text="🎤 Start Listening", bg=DARK, fg=GREEN)
            self.audio_status.set("Not listening")
            self.status("Audio trigger stopped")

    def _on_audio_shot(self):
        """Called from the audio thread; marshals to UI thread."""
        self.after(0, self._handle_audio_shot)

    def _handle_audio_shot(self):
        """UI thread: react to an audio-detected shot."""
        self.status("🔊 Shot detected!")
        # Auto-detect will pick up the hole via frame diff automatically;
        # if it's off, save an annotated still as a record of the shot
        if not detecting:
            def _do():
                try:
                    path = capture_annotated()
                    self.after(0, lambda: self.status(
                        f"🔊 Shot! Saved: {os.path.basename(path)}"))
                except Exception as e:
                    self.after(0, lambda: self.status(f"Shot capture error: {e}"))
            threading.Thread(target=_do, daemon=True).start()

    # ── calibration ────────────────────────────────────────────────────────────

    def _start_calibration(self):
        global calibration_pts
        calibration_pts = []
        self._mode = "calibrate"
        self.cal_status.set("Click point 1 on preview…")
        self.status("CALIBRATION: click 2 points exactly 1\" apart on grid")
        self.cal_btn.config(text="Cancel", command=self._reset_calibration)

    def _reset_calibration(self):
        global calibration_pts, pixels_per_inch
        calibration_pts = []
        pixels_per_inch = None
        self._mode = "normal"
        self.cal_status.set("Not calibrated")
        self.cal_btn.config(text="Start Calibration", command=self._start_calibration)
        self.status("Calibration reset")
        self._update_stats()

    def _finish_calibration(self):
        global pixels_per_inch
        d = dist_px(calibration_pts[0], calibration_pts[1])
        pixels_per_inch = d
        self._mode = "normal"
        self.cal_status.set(f"✓ Calibrated: {d:.1f} px/inch")
        self.cal_btn.config(text="Recalibrate", command=self._start_calibration)
        self.status(f"Calibrated — {d:.1f} px = 1\".  MOA at {CROSSHAIR_DISTANCE_YARDS}yds")
        self._update_stats()

    # ── reference frame ────────────────────────────────────────────────────────

    def _set_reference(self):
        """Capture the current frame as the baseline for auto hit-detection."""
        global reference_frame
        with _cam_lock:
            reference_frame = picam2.capture_array("main").copy()
        self.ref_status.set("✓ Reference set — shoot when ready")
        self.status("Reference captured. Enable Auto Detect or add hits manually.")

    def _toggle_detect(self):
        """Toggle automatic hit-detection on/off."""
        global detecting
        if reference_frame is None:
            self.status("Set a reference frame first!")
            return
        detecting = not detecting
        if detecting:
            self.detect_btn.config(text="Auto: ON", bg=GREEN, fg="white")
            self.status("Auto detection active…")
        else:
            self.detect_btn.config(text="Auto: OFF", bg=DARK, fg=TEXT)
            self.status("Auto detection paused")

    # ── hit management ─────────────────────────────────────────────────────────

    def _start_manual_hit(self):
        self._mode = "manual_hit"
        self.status("MANUAL HIT: click the bullet hole on the preview")

    def _add_hit(self, x, y):
        """Record a new hit at pixel coordinates (x, y) and update stats."""
        cx, cy = PREVIEW_W//2, PREVIEW_H//2
        dx_px, dy_px = x-cx, y-cy
        if pixels_per_inch:
            inch_x = dx_px/pixels_per_inch
            inch_y = dy_px/pixels_per_inch
            moa_x  = inches_to_moa(inch_x)
            moa_y  = inches_to_moa(inch_y)
        else:
            inch_x = inch_y = moa_x = moa_y = None
        n = len(shots)+1
        shots.append({"n":n,"x":x,"y":y,
                      "inch_x":inch_x,"inch_y":inch_y,
                      "moa_x":moa_x,"moa_y":moa_y})
        _recompute_group_stats()
        self._update_stats()
        msg = f"Shot {n}"
        if inch_x is not None:
            msg += f"  {inch_x:+.2f}\" H  {inch_y:+.2f}\" V"
        self.status(msg)

    def _clear_shots(self):
        shots.clear()
        _recompute_group_stats()
        self._update_stats()
        self.status("Shots cleared")

    # ── statistics ─────────────────────────────────────────────────────────────

    def _update_stats(self):
        """Recompute and display ES / MR / CEP for the current shot group."""
        if not shots:
            self.stats_var.set("No shots yet")
            return
        if len(shots) == 1:
            s = shots[0]
            if s["inch_x"] is not None:
                txt = (f"Shot 1:  {s['inch_x']:+.2f}\" H  {s['inch_y']:+.2f}\" V\n"
                       f"         {s['moa_x']:+.2f} MOA H  {s['moa_y']:+.2f} MOA V")
            else:
                txt = "Shot 1 (calibrate for measurements)"
            self.stats_var.set(txt)
            return
        # Reuse the cached group statistics (kept in sync by _recompute_group_stats)
        if not _group_stats:
            self.stats_var.set("No shots yet")
            return
        es_px  = _group_stats["es_px"]
        mr_px  = _group_stats["mr_px"]
        cep_px = _group_stats["cep_px"]
        if pixels_per_inch:
            es_in, mr_in, cep_in = (px_to_inches(v) for v in (es_px, mr_px, cep_px))
            txt = (f"Shots: {len(shots)}\n"
                   f"ES:   {es_in:.2f}\"  ({inches_to_moa(es_in):.2f} MOA)\n"
                   f"MR:   {mr_in:.2f}\"  ({inches_to_moa(mr_in):.2f} MOA)\n"
                   f"CEP:  {cep_in:.2f}\"  ({inches_to_moa(cep_in):.2f} MOA)")
        else:
            txt = (f"Shots: {len(shots)}\n"
                   f"ES:  {es_px:.0f}px  (calibrate for MOA)\n"
                   f"MR:  {mr_px:.0f}px\n"
                   f"CEP: {cep_px:.0f}px")
        self.stats_var.set(txt)

    # ── preview click ──────────────────────────────────────────────────────────

    def _on_preview_click(self, event):
        """Handle tap/click on the preview: calibration point or manual hit."""
        x, y = event.x, event.y
        if self._mode == "calibrate":
            calibration_pts.append((x, y))
            if len(calibration_pts) == 1:
                self.cal_status.set("Click point 2 on preview…")
            elif len(calibration_pts) == 2:
                self._finish_calibration()
        elif self._mode == "manual_hit":
            self._mode = "normal"
            self._add_hit(x, y)

    # ── callbacks ──────────────────────────────────────────────────────────────

    def _cancel_mode(self):
        """Escape key: cancel any active calibrate/manual_hit mode."""
        if self._mode != "normal":
            self._mode = "normal"
            self.status("Cancelled")

    def _toggle_crosshair(self):
        global show_crosshair
        show_crosshair = self.xhair_var.get()

    def _toggle_crosshair_key(self):
        global show_crosshair
        show_crosshair = not show_crosshair
        self.xhair_var.set(show_crosshair)

    def _capture(self):
        """Save an annotated JPEG in a background thread (non-blocking)."""
        def _do():
            try:
                path = capture_annotated()
                self.after(0, lambda: self.status(f"Saved: {os.path.basename(path)}"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Capture failed", str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def _export_csv(self):
        path = export_csv()
        self.status(f"CSV: {os.path.basename(path)}" if path else "No shots to export")

    def _toggle_record(self):
        """Start or stop H264 video recording."""
        global recording
        if not recording:
            try:
                path = start_recording()
                recording = True
                self.rec_btn.config(text="⏹ Stop", bg=RED, fg="white")
                self._rec_start = time.monotonic()
                self._tick_timer()
                self.status(f"Recording → {os.path.basename(path)}")
            except Exception as e:
                messagebox.showerror("Record failed", str(e))
        else:
            try:
                stop_recording()
                recording = False
                if hasattr(self, "_rec_timer_id"):
                    self.after_cancel(self._rec_timer_id)
                self.rec_btn.config(text="⏺ Record", bg=DARK, fg=GREEN)
                self.status("Recording saved.")
            except Exception as e:
                messagebox.showerror("Stop failed", str(e))

    def _tick_timer(self):
        """Update status bar with elapsed recording time every second."""
        if recording:
            elapsed = int(time.monotonic() - self._rec_start)
            m, s = divmod(elapsed, 60)
            self.status(f"● REC  {m:02d}:{s:02d}")
            self._rec_timer_id = self.after(1000, self._tick_timer)

    def _shutdown(self):
        """Confirm then cleanly close the app and shut down the Pi.
        Requires passwordless sudo — add to /etc/sudoers:
            pi ALL=(ALL) NOPASSWD: /sbin/shutdown
        """
        if not messagebox.askyesno("Shutdown", "Shut down the Raspberry Pi?"):
            return
        self.on_close()
        subprocess.run(["sudo", "shutdown", "-h", "now"], check=False)

    def status(self, msg):
        self.status_var.set(msg)

    def on_close(self):
        """Clean shutdown: stop threads, encoder, audio, then camera."""
        global running
        running = False
        stop_audio_listen()
        if hasattr(self, "_rec_timer_id"):
            self.after_cancel(self._rec_timer_id)
        if recording:
            try:
                stop_recording()  # acquires _cam_lock internally
            except Exception:
                pass
        if picam2:
            try:
                with _cam_lock:   # wait for any in-flight frame grab to finish
                    picam2.stop()
            except Exception:
                pass
        self.destroy()


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ScopeApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
