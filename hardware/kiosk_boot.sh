#!/bin/bash
# ==============================================================================
# Katha AI - Raspberry Pi TFT & Speaker Kiosk Bootstrapper
# ==============================================================================
# Automatically launches the Katha AI Smart Counter Display in full-screen
# kiosk mode on boot, configuring audio routing to the external speaker.
# ==============================================================================

# Target Katha AI URL (Local backend or Render production URL)
KATHA_URL="${KATHA_URL:-http://localhost:8080}"

# Prevent TFT screen from blanking or turning off
xset s noblank 2>/dev/null || true
xset s off 2>/dev/null || true
xset -dpms 2>/dev/null || true

# Hide mouse cursor when idle
which unclutter >/dev/null 2>&1 && unclutter -idle 2 -root &

# Set speaker volume to 85%
amixer -q sset Master 85% 2>/dev/null || amixer -q sset PCM 85% 2>/dev/null || true

echo "[Katha AI] Launching counter terminal display to: $KATHA_URL"

# Clean any abnormal browser shutdown flags
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' ~/.config/chromium/Default/Preferences 2>/dev/null || true
sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/' ~/.config/chromium/Default/Preferences 2>/dev/null || true

# Launch Chromium in full-screen kiosk mode with audio autostart allowed
exec chromium-browser \
    --noerrdialogs \
    --disable-infobars \
    --kiosk \
    --check-for-update-interval=31536000 \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --autoplay-policy=no-user-gesture-required \
    "$KATHA_URL"

# Updated: 2026-11-22
