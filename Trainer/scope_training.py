#!/usr/bin/env python3
"""
ScopeCam Training — Dataset Collection Tool
Raspberry Pi 4B + IMX477 + Spotting Scope
1024x600 display

Purpose: capture and organise images of targets/bullet holes for building a
YOLO training dataset. NO analysis (no calibration, hit-detection, stats, or
audio) — this tool exists only to gather varied, well-labelled-for-later images.

Each capture is written to a per-session folder and recorded in a CSV manifest
together with the shooting conditions (lighting / target / caliber / distance),
so you can later balance the dataset across those variations.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import time
import os
import csv
import glob
import shutil
import subprocess
import threading
from datetime import datetime
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, Quality
from picamera2.outputs import FfmpegOutput
from PIL import Image, ImageTk

# ── Constants ──────────────────────────────────────────────────────────────────
PREVIEW_W   = 720
PREVIEW_H   = 490
PANEL_W     = 304
WIN_H       = 600
WIN_W       = PREVIEW_W + PANEL_W

# Full-resolution still size for the IMX477 (good detail for labelling).
CAPTURE_W   = 2028
CAPTURE_H   = 1520

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
FALLBACK_DIR = os.path.expanduser("~/Pictures/scope_training")

# Metadata options — these drive dataset variety. Keep them short for the CSV.
LIGHTING_OPTS = ["bright-sun", "overcast", "indoor", "shade", "low-light", "glare"]
TARGET_OPTS   = ["paper-bull", "splatter", "black-bull", "colored", "steel", "other"]
CALIBER_OPTS  = [".22", ".223/5.56", ".308/7.62", "9mm", "12ga", "other"]
DISTANCE_OPTS = ["25yd", "50yd", "100yd", "200yd", "300yd", "other"]

# ── State ──────────────────────────────────────────────────────────────────────
picam2          = None
recording       = False
running         = True
_cam_lock       = threading.Lock()
_count_lock     = threading.Lock()   # guards capture_count increments

session_dir     = None    # active capture folder (set when a session starts)
session_name    = None    # human-readable session label
capture_count   = 0       # number of stills saved this session
_manifest_path  = None    # CSV manifest for the active session


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_base_dir():
    """Return base save path: SSD mount if present, else local fallback.
    Validates the resolved path stays within an allowed location.
    """
    home = os.path.expanduser("~")
    if os.path.ismount(SSD_MOUNT):
        candidate = os.path.join(SSD_MOUNT, "scope_training")
    else:
        candidate = FALLBACK_DIR
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

def sanitize_name(name):
    """Make a filesystem-safe session name; fall back to a timestamp."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name).strip("_")
    return safe or datetime.now().strftime("%Y%m%d_%H%M%S")


# ── Camera ─────────────────────────────────────────────────────────────────────

def camera_init():
    """Create and start Picamera2 with a low-res preview stream plus a
    high-res still stream, so captures are full detail for labelling while
    the live preview stays light.
    """
    global picam2
    picam2 = Picamera2()
    config = picam2.create_video_configuration(
        main={"size": (CAPTURE_W, CAPTURE_H), "format": "RGB888"},
        lores={"size": (PREVIEW_W, PREVIEW_H), "format": "RGB888"},
        display="lores",
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(0.5)  # allow the sensor and AGC to settle

def capture_still(meta):
    """Grab a full-resolution frame and save it as a JPEG in the session folder.
    meta is a dict of shooting conditions; the row is appended to the manifest.
    Returns the saved file path. Acquires _cam_lock during the grab.
    """
    global capture_count
    if session_dir is None:
        raise RuntimeError("Start a session before capturing")
    with _cam_lock:
        frame = picam2.capture_array("main")
    with _count_lock:
        capture_count += 1
        n = capture_count
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    fname = f"{session_name}_{n:04d}_{ts}.jpg"
    path  = os.path.join(session_dir, fname)
    Image.fromarray(frame).save(path, quality=95)
    _append_manifest(fname, meta)
    return path

def _append_manifest(filename, meta):
    """Append one capture row (filename + conditions) to the session CSV."""
    new_file = not os.path.exists(_manifest_path)
    with open(_manifest_path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["filename", "timestamp", "lighting",
                        "target", "caliber", "distance", "notes"])
        w.writerow([filename, datetime.now().isoformat(timespec="seconds"),
                    meta.get("lighting", ""), meta.get("target", ""),
                    meta.get("caliber", ""), meta.get("distance", ""),
                    meta.get("notes", "")])

def start_recording():
    """Begin H264 MP4 recording to the session folder (for later frame
    extraction with ffmpeg). Acquires _cam_lock while attaching the encoder.
    """
    if session_dir is None:
        raise RuntimeError("Start a session before recording")
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    path    = os.path.join(session_dir, f"{session_name}_clip_{ts}.mp4")
    encoder = H264Encoder(bitrate=10000000)
    output  = FfmpegOutput(path)
    with _cam_lock:
        picam2.start_encoder(encoder, output, quality=Quality.HIGH)
    return path

def stop_recording():
    """Stop the H264 encoder and finalise the MP4. Acquires _cam_lock."""
    with _cam_lock:
        picam2.stop_encoder()


# ── Frame extraction ───────────────────────────────────────────────────────────

def ffmpeg_available():
    """Return True if an ffmpeg binary is on PATH."""
    return shutil.which("ffmpeg") is not None

def list_session_clips():
    """Return MP4 clip paths in the active session folder (newest first)."""
    if session_dir is None:
        return []
    clips = glob.glob(os.path.join(session_dir, "*.mp4"))
    clips.sort(key=os.path.getmtime, reverse=True)
    return clips

def extract_frames(clip_path, fps, meta, progress_cb=None):
    """Extract JPEG frames from clip_path at the given fps using ffmpeg.
    Frames are written into the session folder with the standard naming so
    they sit alongside stills, and each is appended to the manifest with the
    supplied conditions metadata. Returns the number of frames written.
    Raises RuntimeError if ffmpeg is missing or fails.
    """
    global capture_count
    if session_dir is None:
        raise RuntimeError("No active session")
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH")

    base = os.path.splitext(os.path.basename(clip_path))[0]
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Extract to a temp subfolder first so we can rename into the standard scheme.
    tmp  = os.path.join(session_dir, f"_extract_{ts}")
    os.makedirs(tmp, exist_ok=True)
    try:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-i", clip_path,
               "-vf", f"fps={fps}",
               "-q:v", "2",
               os.path.join(tmp, "f_%04d.jpg")]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")

        frames = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
        written = 0
        for src in frames:
            with _count_lock:
                capture_count += 1
                n = capture_count
            fname = f"{session_name}_{n:04d}_{base}_x{written:03d}.jpg"
            dst   = os.path.join(session_dir, fname)
            shutil.move(src, dst)
            row_meta = dict(meta)
            row_meta["notes"] = (row_meta.get("notes", "") +
                                 f" [from {os.path.basename(clip_path)}]").strip()
            _append_manifest(fname, row_meta)
            written += 1
            if progress_cb:
                progress_cb(written, len(frames))
        return written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── App ────────────────────────────────────────────────────────────────────────

class TrainingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ScopeCam Training — Dataset Collection")
        self.configure(bg=BG)
        self.geometry(f"{WIN_W}x{WIN_H}+0+0")
        self.resizable(False, False)
        self._burst_job = None
        self._burst_active = False
        self._build_ui()
        self._bind_keys()
        threading.Thread(target=self._hw_init, daemon=True).start()
        self._check_ssd()

    # ── hardware init ──────────────────────────────────────────────────────────

    def _hw_init(self):
        try:
            camera_init()
            self.after(0, lambda: self.status("Ready — name a session, then capture"))
            self._start_preview_loop()
        except Exception as e:
            self.after(0, lambda: self.status(f"Init error: {e}"))

    # ── SSD monitor ───────────────────────────────────────────────────────────

    def _check_ssd(self):
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
        """Daemon thread: grab low-res frames at ~30fps and push to the UI."""
        def loop():
            while running:
                try:
                    with _cam_lock:
                        frame = picam2.capture_array("lores")
                    img   = Image.fromarray(frame)
                    imgtk = ImageTk.PhotoImage(image=img)
                    self.after(0, self._update_preview, imgtk)
                except Exception as e:
                    self.after(0, lambda msg=str(e): self.status(f"Preview error: {msg}"))
                    time.sleep(0.5)
                time.sleep(0.033)  # ~30fps
        threading.Thread(target=loop, daemon=True).start()

    def _update_preview(self, imgtk):
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

        # Right: control panel
        panel = tk.Frame(self, bg=PANEL, width=PANEL_W, height=WIN_H)
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)
        pad = {"padx": 12}

        # Title + SSD indicator
        tf = tk.Frame(panel, bg=PANEL)
        tf.pack(fill=tk.X, pady=(8,0), **pad)
        tk.Label(tf, text="⬡ TRAINING", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 11, "bold")).pack(side=tk.LEFT)
        self.ssd_var = tk.StringVar(value="…")
        self.ssd_label = tk.Label(tf, textvariable=self.ssd_var,
                                  bg=PANEL, fg=MUTED, font=("Courier New", 7))
        self.ssd_label.pack(side=tk.RIGHT)

        self._sep(panel)

        # ── 1. SESSION ──────────────────────────────────────────────────────────
        tk.Label(panel, text="1  SESSION", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)
        sf = tk.Frame(panel, bg=PANEL)
        sf.pack(anchor="w", pady=(2,0), fill=tk.X, **pad)
        tk.Label(sf, text="Name:", bg=PANEL, fg=MUTED,
                 font=("Courier New", 8)).pack(side=tk.LEFT)
        self.session_var = tk.StringVar(value="")
        tk.Entry(sf, textvariable=self.session_var, bg="white", fg=TEXT,
                 relief="flat", font=("Courier New", 9), width=16).pack(
                 side=tk.LEFT, padx=(4,0))
        self.session_btn = tk.Button(panel, text="Start Session",
                                     bg=ORANGE, fg="white", relief="flat",
                                     font=("Courier New", 9, "bold"),
                                     padx=8, pady=9, command=self._toggle_session)
        self.session_btn.pack(anchor="w", pady=(3,0), **pad)
        self._add_pop(self.session_btn)
        self.session_status = tk.StringVar(value="No active session")
        tk.Label(panel, textvariable=self.session_status, bg=PANEL, fg=ORANGE,
                 font=("Courier New", 7), wraplength=280).pack(anchor="w", **pad)

        self._sep(panel)

        # ── 2. CONDITIONS (metadata) ────────────────────────────────────────────
        tk.Label(panel, text="2  CONDITIONS", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)
        self.meta_vars = {}
        for label, opts in [("lighting", LIGHTING_OPTS), ("target", TARGET_OPTS),
                            ("caliber", CALIBER_OPTS), ("distance", DISTANCE_OPTS)]:
            row = tk.Frame(panel, bg=PANEL)
            row.pack(anchor="w", pady=(2,0), fill=tk.X, **pad)
            tk.Label(row, text=f"{label.capitalize():9}", bg=PANEL, fg=MUTED,
                     font=("Courier New", 8)).pack(side=tk.LEFT)
            var = tk.StringVar(value=opts[0])
            cb = ttk.Combobox(row, textvariable=var, values=opts,
                              width=14, font=("Courier New", 8), state="readonly")
            cb.pack(side=tk.LEFT, padx=(4,0))
            self.meta_vars[label] = var

        self._sep(panel)

        # ── 3. CAPTURE ──────────────────────────────────────────────────────────
        tk.Label(panel, text="3  CAPTURE", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)

        self.cap_btn = tk.Button(panel, text="📷  CAPTURE",
                                 bg=BLUE, fg="white", relief="flat",
                                 font=("Courier New", 12, "bold"),
                                 pady=14, command=self._capture)
        self.cap_btn.pack(anchor="w", pady=(2,0), fill=tk.X, **pad)
        self._add_pop(self.cap_btn)

        # Burst capture row
        brow = tk.Frame(panel, bg=PANEL)
        brow.pack(anchor="w", pady=(4,0), fill=tk.X, **pad)
        tk.Label(brow, text="Burst:", bg=PANEL, fg=MUTED,
                 font=("Courier New", 8)).pack(side=tk.LEFT)
        self.burst_n = tk.IntVar(value=10)
        ttk.Combobox(brow, textvariable=self.burst_n, values=[5,10,20,30,50],
                     width=4, font=("Courier New", 8), state="readonly").pack(
                     side=tk.LEFT, padx=(4,6))
        self.burst_btn = tk.Button(brow, text="Start Burst",
                                   bg=DARK, fg=TEXT, relief="flat",
                                   font=("Courier New", 9, "bold"),
                                   padx=8, pady=9, command=self._toggle_burst)
        self.burst_btn.pack(side=tk.LEFT)
        self._add_pop(self.burst_btn)

        self.count_var = tk.StringVar(value="Captured: 0")
        tk.Label(panel, textvariable=self.count_var, bg=PANEL, fg=GREEN,
                 font=("Courier New", 9, "bold")).pack(anchor="w", pady=(3,0), **pad)

        self._sep(panel)

        # ── 4. VIDEO CLIP ───────────────────────────────────────────────────────
        tk.Label(panel, text="4  VIDEO CLIP", bg=PANEL, fg=ACCENT,
                 font=("Courier New", 8, "bold")).pack(anchor="w", **pad)
        tk.Label(panel, text="Record clip → extract frames later",
                 bg=PANEL, fg=MUTED, font=("Courier New", 7)).pack(anchor="w", **pad)
        self.rec_btn = tk.Button(panel, text="⏺ Record Clip",
                                 bg=DARK, fg=GREEN, relief="flat",
                                 font=("Courier New", 9, "bold"),
                                 padx=12, pady=9, command=self._toggle_record)
        self.rec_btn.pack(anchor="w", pady=(2,0), **pad)
        self._add_pop(self.rec_btn)

        # Extract frames from the most recent clip in this session
        erow = tk.Frame(panel, bg=PANEL)
        erow.pack(anchor="w", pady=(4,0), fill=tk.X, **pad)
        tk.Label(erow, text="Extract fps:", bg=PANEL, fg=MUTED,
                 font=("Courier New", 8)).pack(side=tk.LEFT)
        self.extract_fps = tk.DoubleVar(value=2.0)
        ttk.Combobox(erow, textvariable=self.extract_fps,
                     values=[0.5, 1, 2, 5, 10],
                     width=4, font=("Courier New", 8), state="readonly").pack(
                     side=tk.LEFT, padx=(4,6))
        self.extract_btn = tk.Button(erow, text="Extract Frames",
                                     bg=DARK, fg=TEXT, relief="flat",
                                     font=("Courier New", 9, "bold"),
                                     padx=8, pady=9, command=self._extract_frames)
        self.extract_btn.pack(side=tk.LEFT)
        self._add_pop(self.extract_btn)

        # ── Status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Initialising…")
        tk.Label(panel, textvariable=self.status_var, bg=PANEL, fg=MUTED,
                 font=("Courier New", 7), wraplength=280).pack(
                 anchor="w", pady=(3,0), **pad)

        # ── Bottom: Quit + Shutdown side by side ──────────────────────────────
        bottom_frame = tk.Frame(panel, bg=PANEL)
        bottom_frame.pack(side=tk.BOTTOM, pady=5, padx=12, fill=tk.X)

        quit_btn = tk.Button(bottom_frame, text="✕  QUIT", bg=DARK, fg=RED,
                             relief="flat", font=("Courier New", 10, "bold"),
                             width=10, pady=10,
                             activebackground=_lighten(DARK),
                             command=self.on_close)
        quit_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        self._add_pop(quit_btn)

        shut_btn = tk.Button(bottom_frame, text="⏻  SHUTDOWN", bg=RED, fg="white",
                             relief="flat", font=("Courier New", 10, "bold"),
                             width=10, pady=10,
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
        self.bind("<space>", lambda e: self._capture())

    # ── session ──────────────────────────────────────────────────────────────

    def _toggle_session(self):
        """Start a new capture session or end the current one."""
        global session_dir, session_name, capture_count, _manifest_path
        if session_dir is None:
            try:
                base = get_base_dir()
            except Exception as e:
                self.status(f"Storage error: {e}")
                return
            name = sanitize_name(self.session_var.get())
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_name   = f"{name}_{stamp}"
            session_dir    = os.path.join(base, session_name)
            os.makedirs(session_dir, exist_ok=True)
            _manifest_path = os.path.join(session_dir, "manifest.csv")
            capture_count  = 0
            self.session_btn.config(text="End Session", bg=RED, fg="white")
            self.session_status.set(f"● {session_name}")
            self.count_var.set("Captured: 0")
            self.status(f"Session started → {session_dir}")
        else:
            if self._burst_active:
                self._stop_burst()
            if recording:
                self._stop_record()
            ended = session_name
            n = capture_count
            session_dir = session_name = _manifest_path = None
            self.session_btn.config(text="Start Session", bg=ORANGE, fg="white")
            self.session_status.set("No active session")
            self.status(f"Session ended: {ended} ({n} images)")

    def _current_meta(self):
        return {k: v.get() for k, v in self.meta_vars.items()}

    # ── capture ────────────────────────────────────────────────────────────────

    def _capture(self, on_done=None):
        """Save one full-res still in a background thread (non-blocking).
        on_done(ok) is invoked on the UI thread when finished (used by burst)."""
        if session_dir is None:
            self.status("Start a session first!")
            if on_done is not None:
                on_done(False)
            return
        meta = self._current_meta()
        def _do():
            ok = True
            try:
                path = capture_still(meta)
                self.after(0, lambda: (
                    self.count_var.set(f"Captured: {capture_count}"),
                    self.status(f"Saved: {os.path.basename(path)}")))
            except Exception as e:
                ok = False
                self.after(0, lambda: self.status(f"Capture error: {e}"))
            finally:
                if on_done is not None:
                    self.after(0, lambda: on_done(ok))
        threading.Thread(target=_do, daemon=True).start()

    def _toggle_burst(self):
        """Start or stop automatic timed burst capture (sequential, ~2 fps)."""
        if not self._burst_active:
            if session_dir is None:
                self.status("Start a session first!")
                return
            self._burst_active = True
            self._burst_left = self.burst_n.get()
            self.burst_btn.config(text="Stop Burst", bg=RED, fg="white")
            self.status(f"Burst: capturing {self._burst_left} frames…")
            self._burst_next()
        else:
            self._stop_burst()

    def _burst_next(self):
        """Capture one burst frame; the next is scheduled only after it
        finishes, preventing thread pile-up on slow full-res saves."""
        if not self._burst_active or self._burst_left <= 0:
            self._stop_burst()
            return
        self._burst_left -= 1
        self._capture(on_done=self._burst_after_capture)

    def _burst_after_capture(self, ok):
        if not self._burst_active:
            return
        if self._burst_left <= 0:
            self._stop_burst()
        else:
            self._burst_job = self.after(500, self._burst_next)  # ~2 fps

    def _stop_burst(self):
        self._burst_active = False
        if self._burst_job is not None:
            try:
                self.after_cancel(self._burst_job)
            except Exception:
                pass
        self._burst_job = None
        self.burst_btn.config(text="Start Burst", bg=DARK, fg=TEXT)
        self.status("Burst finished")

    # ── video ──────────────────────────────────────────────────────────────────

    def _toggle_record(self):
        """Start or stop H264 clip recording into the session folder."""
        global recording
        if session_dir is None:
            self.status("Start a session first!")
            return
        if not recording:
            try:
                path = start_recording()
                recording = True
                self.rec_btn.config(text="⏹ Stop Clip", bg=RED, fg="white")
                self._rec_start = time.monotonic()
                self._tick_timer()
                self.status(f"Recording → {os.path.basename(path)}")
            except Exception as e:
                messagebox.showerror("Record failed", str(e))
        else:
            self._stop_record()

    def _stop_record(self):
        """Stop recording if active and reset the record button."""
        global recording
        if not recording:
            return
        try:
            stop_recording()
        except Exception as e:
            messagebox.showerror("Stop failed", str(e))
            return
        recording = False
        if hasattr(self, "_rec_timer_id"):
            self.after_cancel(self._rec_timer_id)
        self.rec_btn.config(text="⏺ Record Clip", bg=DARK, fg=GREEN)
        self.status("Clip saved.")

    def _tick_timer(self):
        if recording:
            elapsed = int(time.monotonic() - self._rec_start)
            m, s = divmod(elapsed, 60)
            self.status(f"● REC  {m:02d}:{s:02d}")
            self._rec_timer_id = self.after(1000, self._tick_timer)

    def _extract_frames(self):
        """Extract frames from the newest clip in the session (background thread)."""
        if session_dir is None:
            self.status("Start a session first!")
            return
        if recording:
            self.status("Stop recording before extracting")
            return
        if not ffmpeg_available():
            self.status("ffmpeg not found — install it first")
            return
        clips = list_session_clips()
        if not clips:
            self.status("No clips in this session to extract")
            return
        clip = clips[0]
        fps  = self.extract_fps.get()
        meta = self._current_meta()
        self.extract_btn.config(state=tk.DISABLED)
        self.status(f"Extracting {fps} fps from {os.path.basename(clip)}…")

        def _do():
            try:
                def prog(done, total):
                    self.after(0, lambda: self.count_var.set(
                        f"Captured: {capture_count}"))
                n = extract_frames(clip, fps, meta, progress_cb=prog)
                self.after(0, lambda: (
                    self.count_var.set(f"Captured: {capture_count}"),
                    self.status(f"Extracted {n} frames from "
                                f"{os.path.basename(clip)}")))
            except Exception as e:
                self.after(0, lambda: self.status(f"Extract error: {e}"))
            finally:
                self.after(0, lambda: self.extract_btn.config(state=tk.NORMAL))
        threading.Thread(target=_do, daemon=True).start()

    # ── system ─────────────────────────────────────────────────────────────────

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
        """Clean shutdown: stop threads, burst, encoder, then camera."""
        global running
        running = False
        self._burst_active = False
        if self._burst_job is not None:
            try:
                self.after_cancel(self._burst_job)
            except Exception:
                pass
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
    app = TrainingApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
