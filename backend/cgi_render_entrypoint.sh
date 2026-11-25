#!/bin/bash
# Starts the virtual display + window manager cgi_render.py's computer-use
# actions need, then runs the worker. Not meant to be run any other way —
# `docker run` this image directly, don't `python -m app.services.cgi_render`
# without this (there'd be no DISPLAY for pyautogui to attach to).
#
# Runs as root only to start two users: the desktop the model drives runs as
# $DESKTOP_USER with an empty environment, so nothing launched from it (GUI
# Blender, its Python console, fluxbox's Run dialog) can see the worker's
# credentials or read the worker's /proc entries. The worker then drops to
# $WORKER_USER and keeps its environment.
set -euo pipefail

DESKTOP_USER=cgidesk
WORKER_USER=bricks

if [ "$(id -u)" -ne 0 ]; then
  echo "cgi_render_entrypoint.sh must start as root to separate the desktop user from the worker" >&2
  exit 1
fi

desktop() {
  env -i \
    DISPLAY="$DISPLAY" \
    HOME="/home/$DESKTOP_USER" \
    PATH=/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 \
    setpriv --reuid="$DESKTOP_USER" --regid="$DESKTOP_USER" --init-groups -- "$@"
}

cd "/home/$DESKTOP_USER"

desktop Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp &

# Give Xvfb a moment to actually create the display socket before anything
# tries to connect to it.
for _ in $(seq 1 20); do
  if desktop xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

desktop fluxbox -display "$DISPLAY" &

cd /app
exec setpriv --reuid="$WORKER_USER" --regid="$WORKER_USER" --init-groups -- python -m app.services.cgi_render
