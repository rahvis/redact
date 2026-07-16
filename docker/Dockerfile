# WorkOnward Read-in-the-browser
#
# WorkOnward Read is a Tkinter (FreeSimpleGUI) DESKTOP app with no web server of its own.
# This image runs the real GUI on a headless virtual X display (Xvfb) and streams
# it to the browser with x11vnc + noVNC, so the app is reachable at a plain URL.
#
#   Virtual display : Xvfb   (:0)
#   Window manager  : fluxbox
#   Screen -> VNC   : x11vnc  (:5900)
#   VNC   -> Browser: websockify + noVNC (:6080)  <-- open this in your browser
#
# Build context is the repo root:  docker build -f docker/Dockerfile -t workonward-read-desktop .

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:0 \
    SCREEN_GEOMETRY=1440x900x24 \
    PYTHONUNBUFFERED=1

# System deps:
#   python3 + python3-tk  -> the app + its Tkinter GUI toolkit
#   xvfb                  -> headless X server (a screen with no monitor)
#   x11vnc                -> exposes that screen over VNC
#   novnc + websockify    -> serves VNC to a web browser over websockets
#   fluxbox               -> minimal window manager (window frame / maximize)
#   fonts-*               -> so Tk and the PDFs render with real fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-tk \
        xvfb x11vnc fluxbox \
        novnc websockify \
        fonts-dejavu-core fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Make the noVNC client the landing page so the bare URL "just works".
RUN ln -sf /usr/share/novnc/vnc.html /usr/share/novnc/index.html

WORKDIR /app
COPY . /app

# Install WorkOnward Read + its Python dependencies (FreeSimpleGUI, pypdfium2,
# Pillow, fpdf2, appdirs) from the project metadata.
RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir .

RUN chmod +x /app/docker/start.sh \
    && mkdir -p /files

# Folder for getting PDFs/images in and out of the app (mount a host dir here).
VOLUME ["/files"]

EXPOSE 6080
CMD ["/app/docker/start.sh"]
