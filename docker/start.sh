#!/usr/bin/env bash
#
# Boots the display stack, then launches CoverUP into it.
# Helper daemons log to /var/log/*.log; the app itself logs to the container's
# stdout so `docker logs coverup-web` shows any Python errors.

set -u

GEOM="${SCREEN_GEOMETRY:-1440x900x24}"
DISP="${DISPLAY:-:0}"
VNC_PORT=5900
WEB_PORT=6080

echo "[start] launching virtual display ${DISP} (${GEOM})"

# 1. Headless X server (a framebuffer with no physical monitor).
Xvfb "$DISP" -screen 0 "$GEOM" -ac +extension GLX +render -noreset \
    >/var/log/xvfb.log 2>&1 &

# Wait for the X socket to appear before anything tries to connect to it.
for _ in $(seq 1 60); do
    [ -S "/tmp/.X11-unix/X${DISP#:}" ] && break
    sleep 0.2
done

# 2. Window manager: gives the Tk window a title bar and lets it be maximized.
fluxbox >/var/log/fluxbox.log 2>&1 &

# 3. Publish the X screen over VNC (no password; only reachable via the
#    container's mapped port, which you control with `docker run -p`).
x11vnc -display "$DISP" -forever -shared -nopw -rfbport "$VNC_PORT" -noxdamage \
    >/var/log/x11vnc.log 2>&1 &

# 4. Bridge VNC -> browser (noVNC web client + websockify proxy).
websockify --web=/usr/share/novnc "$WEB_PORT" "localhost:${VNC_PORT}" \
    >/var/log/websockify.log 2>&1 &

echo "[start] CoverUP is now reachable at:"
echo "        http://localhost:${WEB_PORT}/vnc.html?autoconnect=true&resize=scale"

# 5. Run the app. Restart it if the window is closed so the URL stays live.
#    File dialogs default to /files (mount a host folder there to import/export).
cd /files 2>/dev/null || cd /app
while true; do
    echo "[start] launching CoverUP..."
    coverup "$@"
    echo "[start] CoverUP exited; restarting in 1s (close the browser tab to stop using it)"
    sleep 1
done
