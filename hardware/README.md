# Katha AI - Smart Hardware Terminal (Raspberry Pi + TFT + Speaker)

This module turns any **Raspberry Pi** into an autonomous, physical **Smart Counter Device** for retail stores and SMEs. It displays real-time financial health, cashflow KPIs, active voice calls, and allows hands-free voice interactions through an integrated speaker and microphone.

---

## 1. Hardware Architecture & Bill of Materials (BOM)

| Component | Recommended Models | Purpose |
|---|---|---|
| **SBC (Single Board Computer)** | Raspberry Pi 4 Model B (2GB/4GB/8GB) or Raspberry Pi 5 | Runs the local client / Chromium kiosk engine and audio daemon |
| **Display** | 3.5", 5", or 7" HDMI / SPI TFT Touchscreen Display | Renders live store metrics, collection progress, and live call transcripts |
| **Audio Speaker** | USB Powered Mini Speaker / 3.5mm AUX Speaker / I2S DAC (MAX98357A) | Emits voice responses from Meera, Kavya, Priya, and Aarav |
| **Microphone** | USB Gooseneck Mic / ReSpeaker 2-Mics Pi HAT | Captures customer & shopkeeper voice commands for Gnani STT |
| **Power Supply** | 5V / 3A USB-C Power Adapter | Delivers stable power to Pi, display, and speaker |

---

## 2. System Architecture

```
   ┌─────────────────────────────────────────────────────────┐
   │             Katha AI Smart Counter Terminal             │
   │                                                         │
   │   ┌────────────────────┐       ┌────────────────────┐   │
   │   │   TFT Touchscreen  │       │  Speaker & Mic     │   │
   │   │  (Counter Display) │       │  (Voice Gateway)   │   │
   │   └─────────▲──────────┘       └─────────▲──────────┘   │
   │             │                            │              │
   │             └─────────────┬──────────────┘              │
   │                           │                             │
   │                ┌──────────┴──────────┐                  │
   │                │   Raspberry Pi 4/5  │                  │
   │                │  (Chromium Kiosk)   │                  │
   │                └──────────┬──────────┘                  │
   └───────────────────────────┼─────────────────────────────┘
                               │ WiFi / Ethernet
                               ▼
        ┌──────────────────────────────────────────────┐
        │       Katha AI Backend (Render / Local)      │
        │                                              │
        │   FastAPI (Python)  ◄───►  Node.js (ExcelJS) │
        │   Voice AI & Gnani         Collections Log   │
        └──────────────────────────────────────────────┘
```

---

## 3. Quick Setup on Raspberry Pi OS

### Step 1: Install Dependencies
Open a terminal on your Raspberry Pi:
```bash
sudo apt update
sudo apt install -y chromium-browser x11-xserver-utils unclutter alsa-utils
```

### Step 2: Configure Display & Rotation
If using an SPI or HDMI TFT display, adjust rotation in `/boot/firmware/config.txt` (or `/boot/config.txt`):
```ini
# Landscape orientation (rotate 90 or 270 degrees if needed)
display_rotate=0
dtoverlay=vc4-kms-v3d
```

### Step 3: Test Audio Speaker Output
Verify your speaker is detected:
```bash
aplay -l
speaker-test -t wav -c 2
```
Set volume to 85%:
```bash
amixer sset Master 85%
```

### Step 4: Configure Kiosk Autostart on Boot
Make the boot script executable:
```bash
chmod +x hardware/kiosk_boot.sh
```

Create a systemd service to automatically launch the kiosk display on boot:
```bash
sudo nano /etc/systemd/system/katha-kiosk.service
```

Paste the following configuration:
```ini
[Unit]
Description=Katha AI Counter Terminal Kiosk
After=network-online.target graphical.target
Wants=network-online.target

[Service]
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/pi/.Xauthority
Environment=KATHA_URL=https://katha-ai.onrender.com
User=pi
ExecStart=/bin/bash /home/pi/Katha-AI-/hardware/kiosk_boot.sh
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable katha-kiosk.service
sudo systemctl start katha-kiosk.service
```

---

## 4. Operational Features
- **Hands-Free Operation**: Shop owners can speak directly to the terminal using the voice mic ("Call the customer", "Call the supplier", "Check udhaar").
- **Real-Time Sound Box**: Plays audible alerts whenever a customer payment is claimed or verified.
- **Always-On Dashboard**: Displays active revenue, overdue receivables, and low stock items on the shop counter.
