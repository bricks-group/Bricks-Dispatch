# Worker image for backend/app/services/cgi_render.py ONLY — not the main
# API (see backend/Dockerfile for that). Runs a real Blender + virtual X
# display so OpenAI's computer-use tool has something to actually drive;
# this is why cgi_render.py cannot run on the Vercel serverless deployment.
#
# Build:  docker build -f cgi_render.Dockerfile -t bricks-cgi-render .
# Run:    docker run -e OPENAI_API_KEY=... -e OS_API_URL=... -e INTERNAL_API_KEYS=... \
#           -e SUPABASE_URL=... -e SUPABASE_KEY=... bricks-cgi-render
#
# Deploy target is an open question (small always-on VM vs. a container
# platform that supports long-running, non-HTTP workers) — this image runs
# on any of them, the choice is a cost/ops decision, not a code one.

FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements-cgi-render.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements-cgi-render.txt

FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/bricks-group/bricks-dispatch"

# - blender: the actual render target Astra drives.
# - xvfb: virtual X display — there's no physical monitor on this host.
# - fluxbox: minimal window manager. pyautogui/computer-use need real
#   window focus/decoration behavior, not a bare X server with no WM.
# - gnome-screenshot: pyautogui's actual screenshot backend on Linux, via
#   pyscreeze. VERIFIED 2026-09-13 against a real Xvfb display — pyscreeze
#   raises explicitly demanding gnome-screenshot and does NOT fall back to
#   scrot; confirmed scrot alone (without gnome-screenshot) fails, and
#   gnome-screenshot alone (without scrot) works. Don't add scrot back
#   thinking it's the right tool here — it was the wrong guess.
# - libgl1-mesa-glx, libxrender1, libxi6, libsm6: Blender's runtime GL/X deps.
# NOTE: Debian's packaged Blender can lag well behind the latest release.
# If a specific newer Blender version matters, replace this with the
# official tarball from blender.org instead of apt.
RUN apt-get update && apt-get install -y --no-install-recommends \
    blender \
    xvfb \
    fluxbox \
    gnome-screenshot \
    x11-utils \
    libgl1-mesa-glx \
    libxrender1 \
    libxi6 \
    libsm6 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash bricks

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .
COPY cgi_render_entrypoint.sh /usr/local/bin/cgi_render_entrypoint.sh
RUN chmod +x /usr/local/bin/cgi_render_entrypoint.sh \
 && chown -R bricks:bricks /app

USER bricks

ENV DISPLAY=:99

ENTRYPOINT ["/usr/local/bin/cgi_render_entrypoint.sh"]
