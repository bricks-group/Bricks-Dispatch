#!/bin/bash
# Starts the virtual display + window manager cgi_render.py's computer-use
# actions need, then runs the worker. Not meant to be run any other way —
# `docker run` this image directly, don't `python -m app.services.cgi_render`
# without this (there'd be no DISPLAY for pyautogui to attach to).
set -euo pipefail

Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!

# Give Xvfb a moment to actually create the display socket before anything
# tries to connect to it.
for _ in $(seq 1 20); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

fluxbox -display "$DISPLAY" &
FLUXBOX_PID=$!

cleanup() {
  kill "$FLUXBOX_PID" "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT

cd /app
exec python -m app.services.cgi_render
