# ScopeCam Setup Guide
### Raspberry Pi 4B + Arducam IMX477 + Spotting Scope
**Range Analysis System — Complete Setup Guide**

---

## Table of Contents
1. [Hardware Requirements](#hardware-requirements)
2. [Flashing Raspberry Pi OS](#flashing-raspberry-pi-os)
3. [Initial Pi Configuration](#initial-pi-configuration)
4. [Camera Setup](#camera-setup)
5. [SSD Setup](#ssd-setup)
6. [Installing ScopeCam](#installing-scopecam)
7. [Desktop Shortcut](#desktop-shortcut)
8. [Using ScopeCam at the Range](#using-scopecam-at-the-range)
9. [Troubleshooting](#troubleshooting)

---

## 1. Hardware Requirements

### Required
| Item | Notes |
|------|-------|
| Raspberry Pi 4B (2GB RAM minimum, 4GB recommended) | |
| Arducam IMX477 12.3MP Camera Module (C/CS-Lens Mount) | The CS-mount version |
| MicroSD card (16GB minimum, Class 10 or faster) | For the OS |
| USB-C power supply (5V 3A minimum) | Official Pi PSU recommended |
| HDMI display (1024x600 minimum) | |
| USB keyboard and mouse | |
| CSI ribbon cable | Usually included with camera |

### Recommended
| Item | Notes |
|------|-------|
| USB SSD or fast USB flash drive | For recording and saving captures |
| CS-mount lens (25mm f/1.4 recommended) | For focus through spotting scope |
| USB microphone | For automatic shot detection |
| Spotting scope with 20–60x magnification | Vortex Crossfire 2 or similar |
| Camera-to-eyepiece adapter | CS-mount to eyepiece slip-fit |

---

## 2. Flashing Raspberry Pi OS

### Download Raspberry Pi Imager
Download from: **https://www.raspberrypi.com/software/**

### Flash the OS
1. Insert your MicroSD card into your computer
2. Open Raspberry Pi Imager
3. Click **Choose Device** → select **Raspberry Pi 4**
4. Click **Choose OS** → **Raspberry Pi OS (64-bit)** (the full desktop version)
5. Click **Choose Storage** → select your MicroSD card
6. Click **Next**
7. When asked about customisation, click **Edit Settings**:
   - Set a **hostname** (e.g. `scopecam`)
   - Set a **username** and **password** (remember these)
   - Configure **WiFi** if needed
   - Enable **SSH** under the Services tab (useful for remote access)
8. Click **Save** then **Yes** to apply settings
9. Click **Yes** to confirm flashing — this will erase the card
10. Wait for flashing and verification to complete

---

## 3. Initial Pi Configuration

### First Boot
1. Insert the MicroSD card into the Pi
2. Connect your display, keyboard, mouse, and power
3. The Pi will boot to the desktop — this takes a minute on first boot
4. Complete the setup wizard if it appears

### Update the System
Open a terminal and run:
```bash
sudo apt update && sudo apt upgrade -y
```
This may take several minutes. Reboot when done:
```bash
sudo reboot
```

### Enable I2C Interface
The camera's focus motor uses I2C — enable it:
```bash
sudo raspi-config
```
Navigate to: **Interface Options → I2C → Enable → Finish**

### Configure SSH (optional but recommended)
If you want to control the Pi from another computer:
```bash
sudo systemctl enable ssh
sudo systemctl start ssh
```
Find your Pi's IP address with:
```bash
hostname -I
```
Then connect from another machine with:
```bash
ssh yourusername@192.168.x.x
```

---

## 4. Camera Setup

### Physical Connection
> ⚠️ **Always power off the Pi before connecting or disconnecting the camera.**

1. Power off the Pi completely
2. Locate the CSI camera port (the thin ribbon cable connector near the USB ports)
3. Gently lift the plastic latch on the CSI connector
4. Insert the ribbon cable with the **blue side facing the USB ports**
5. Press the latch down to lock the cable in place
6. Power the Pi back on

### Configure the Camera Overlay
Edit the boot configuration file:
```bash
sudo nano /boot/firmware/config.txt
```

Find the line that says `camera_auto_detect=1` and change it to:
```
camera_auto_detect=0
dtoverlay=imx477
```

Save with **Ctrl+X → Y → Enter**, then reboot:
```bash
sudo reboot
```

### Verify the Camera Works
After rebooting, test the camera:
```bash
rpicam-hello --list-cameras
```
You should see the IMX477 listed. If not, check the ribbon cable connection.

Test a live preview:
```bash
rpicam-hello --timeout 5000 --qt-preview
```
A preview window should appear for 5 seconds.

### Install Camera Software
```bash
sudo apt install -y rpicam-apps python3-picamera2 python3-pil python3-pil.imagetk python3-scipy
```

---

## 5. SSD Setup

Using a USB SSD gives you much faster write speeds for video recording and keeps your SD card from wearing out.

### Format the SSD
> ⚠️ **This will erase all data on the drive.**

Plug in your USB SSD, then identify it:
```bash
lsblk
```
It will appear as `sda` (or similar). Format it:
```bash
sudo mkfs.ext4 -L ScopeSSD /dev/sda1
```

### Create the Mount Point
```bash
sudo mkdir -p /mnt/ssd
sudo chown -R $USER:$USER /mnt/ssd
```

### Set Up Auto-Mount on Plug-In
Create a udev rule so it mounts automatically when plugged in:
```bash
sudo nano /etc/udev/rules.d/99-ssd.rules
```
Add these two lines:
```
ACTION=="add", SUBSYSTEM=="block", KERNEL=="sda1", RUN+="/bin/mount /dev/sda1 /mnt/ssd"
ACTION=="remove", SUBSYSTEM=="block", KERNEL=="sda1", RUN+="/bin/umount /mnt/ssd"
```
Save and reload the rules:
```bash
sudo udevadm control --reload-rules
```

### Test the Mount
Unplug and replug the SSD. Then verify:
```bash
df -h | grep ssd
```
You should see `/mnt/ssd` listed. ScopeCam will automatically detect this and save files there.

### Safe Ejection
Always unmount before unplugging to avoid data corruption:
```bash
sudo umount /mnt/ssd
```

---

## 6. Installing ScopeCam

### Install Dependencies
```bash
sudo apt install -y python3-scipy python3-pil python3-pil.imagetk ffmpeg python3-smbus2
pip3 install sounddevice --break-system-packages
```

### Download ScopeCam
Copy `scopecam.py` to your Pi's home directory. If transferring from another computer over SSH:
```bash
scp scopecam.py yourusername@192.168.x.x:/home/yourusername/
```
Or download it directly on the Pi using a USB drive or browser.

### Test the Application
```bash
python3 ~/scopecam.py
```
The application window should open filling most of your screen.

---

## 7. Desktop Shortcut

Create a desktop launcher so you can start ScopeCam with one click:
```bash
cat > ~/Desktop/ScopeCam.desktop << 'DESK'
[Desktop Entry]
Name=ScopeCam
Comment=Shooting Range Analysis
Exec=python3 /home/yourusername/scopecam.py
Icon=camera-photo
Terminal=false
Type=Application
Categories=Graphics;
DESK
chmod +x ~/Desktop/ScopeCam.desktop
```
> Replace `yourusername` with your actual username.

You can now double-click **ScopeCam** on the desktop to launch it.

### Auto-start on Boot (optional)
If you want ScopeCam to launch automatically when the Pi boots:
```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/scopecam.desktop << 'DESK'
[Desktop Entry]
Name=ScopeCam
Exec=python3 /home/yourusername/scopecam.py
Type=Application
DESK
```

---

## 8. Using ScopeCam at the Range

### Setup Workflow

#### Step 1 — Calibrate the Grid
The 1" × 1" grid squares on your target are your reference scale.

1. Point the scope at your target and adjust for a clear view
2. Click **Start Calibration** in the app
3. Click on one corner of a grid square on the preview
4. Click on the adjacent corner exactly 1" away
5. The app displays **✓ Calibrated: X px/inch** when successful
6. Recalibrate any time you change the scope zoom or distance

#### Step 2 — Set Reference Frame
Before firing any shots:

1. Make sure the target has no bullet holes yet
2. Click **Set Reference**
3. The app captures the clean target image for comparison

#### Step 3 — Enable Hit Detection
Choose your detection method:

| Method | When to use |
|--------|-------------|
| **Auto Detect** | Best for most situations — watches for new dark spots |
| **+ Manual** | Click directly on a hole — useful if auto-detect misses |
| **Audio Trigger** | Automatically captures on gunshot sound (requires USB mic) |

For automatic detection, enable both **Auto Detect** and (if you have a mic) **Audio Trigger**.

#### Step 4 — Audio Trigger Setup (USB Microphone)
1. Plug in your USB microphone before starting ScopeCam
2. Select your microphone from the **Mic** dropdown
3. Set sensitivity — start at **0.15** and adjust:
   - Too many false triggers → increase the value
   - Missing shots → decrease the value
4. Click **🎤 Start Listening**
5. The button turns red when active

#### Step 5 — Shoot and Analyse
- Numbered red dots appear on each detected hit
- Yellow crosshair marks the group center
- Statistics update live:

| Stat | Meaning |
|------|---------|
| **ES** | Extreme Spread — largest distance between any two shots |
| **MR** | Mean Radius — average distance from group center |
| **CEP** | Circular Error Probable — 50% of shots land within this radius |

All measurements shown in **inches** and **MOA** at 100 yards.

#### Step 6 — Save Your Results
| Action | Button | Keyboard |
|--------|--------|----------|
| Save annotated photo | 📷 | Space |
| Export shot data | CSV | — |
| Record video session | ⏺ Record | — |
| Toggle crosshair | Crosshair checkbox | X |
| Clear all shots | Clear | Delete |

---

## 9. Troubleshooting

### Camera not detected
```bash
rpicam-hello --list-cameras
```
- Check ribbon cable is fully seated with blue side toward USB ports
- Verify `camera_auto_detect=0` and `dtoverlay=imx477` are in `/boot/firmware/config.txt`
- Try a different ribbon cable if available

### Preview is blurry through the scope
- Adjust the diopter on the spotting scope eyepiece — it acts as a focus adjustment
- Try a 25mm f/1.4 CS-mount lens for better coupling
- Use the scope at 20–30x rather than maximum magnification — high zoom amplifies vibration and heat shimmer
- Ensure your eyepiece adapter is the right length (too long or short shifts the focal plane)

### SSD not mounting
```bash
lsblk
sudo mount /dev/sda1 /mnt/ssd
```
- Check the udev rule in `/etc/udev/rules.d/99-ssd.rules`
- Verify the SSD is formatted: `sudo mkfs.ext4 -L ScopeSSD /dev/sda1`

### Audio trigger not appearing
```bash
pip3 install sounddevice --break-system-packages
```
Then restart ScopeCam. If no devices appear in the dropdown, check:
```bash
arecord -l
```
This lists all detected recording devices.

### Auto-detect firing too often (false positives)
- Ensure the scope is on a stable tripod — movement causes false hits
- Avoid shooting in direct sunlight on the target face — shadows shift and trigger detection
- Increase the detection threshold by editing `threshold=30` in `scopecam.py` to a higher value like `50`

### App is slow or preview is laggy
- Close other applications running on the Pi
- Lower the preview resolution in `scopecam.py` by reducing `PREVIEW_W` and `PREVIEW_H`
- Make sure the Pi has adequate cooling — thermal throttling slows everything down

### ScopeCam won't start
```bash
python3 ~/scopecam.py
```
Read the error message. Common fixes:
```bash
# Missing picamera2
sudo apt install -y python3-picamera2

# Missing PIL
sudo apt install -y python3-pil python3-pil.imagetk

# Missing scipy
sudo apt install -y python3-scipy

# Missing ffmpeg
sudo apt install -y ffmpeg
```

---

## Quick Reference Card

```
KEYBOARD SHORTCUTS
──────────────────
Space      Save annotated capture
X          Toggle crosshair
Delete     Clear all shots
Escape     Cancel current action / Quit prompt

WORKFLOW AT THE RANGE
──────────────────────
1. Calibrate  →  click 2 points 1" apart on grid
2. Reference  →  capture clean target
3. Detect     →  enable Auto Detect + Audio Trigger
4. Shoot      →  hits appear automatically
5. Save       →  📷 photo  +  CSV export

STATISTICS
──────────
ES   Extreme Spread      (largest hole-to-hole distance)
MR   Mean Radius         (avg distance from group center)
CEP  Circular Error Prob (50% of shots inside this radius)
MOA  1 MOA ≈ 1.047" at 100 yards
```

---

*ScopeCam — Built for Raspberry Pi 4B + Arducam IMX477*
