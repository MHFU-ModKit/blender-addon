# Blender + a virtual display.
#
# 🔴 The stock `nytimes/blender` image has no X server, so Blender's Workbench and
# EEVEE engines die with "Unable to open a display" the moment they need a GL
# context. That left CYCLES-on-CPU as the only headless engine — minutes per frame,
# which rules out rendering a whole moveset. Xvfb costs ~40 MB and gives both fast
# engines back.
#
# ⚠️ `xvfb-run` is NOT used: it HANGS in this image when the container runs as a
# non-root uid (which blender-docker.sh does, so output files are owned by the
# caller). Starting Xvfb directly and exporting DISPLAY works and is one line.
FROM nytimes/blender:latest
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
COPY blender-xvfb /usr/local/bin/blender-xvfb
RUN chmod 0755 /usr/local/bin/blender-xvfb
