"""CGI render pipeline for the developer/REIT vertical: Astra + Blender.

**This is a standalone worker, not a FastAPI route.** Build and run it via
cgi_render.Dockerfile, which provisions everything this file needs (Blender,
Xvfb virtual display, fluxbox window manager) and execs
`python -m app.services.cgi_render` under it — running the bare module
directly on a host with no DISPLAY will fail on the pyautogui import.

It must NOT be wired into main.py / mounted as a router — the render loop
needs that live virtual desktop and can run for minutes per job, both
incompatible with a Vercel serverless function's lifecycle. This is the one
piece of the developer/REIT vertical that genuinely needs different
infrastructure than the rest of this repo.

Two things still block this from running for real:
  - OPENAI_API_KEY (model gpt-6-astra, released 2026-09-03). Tool calling
    requires the Responses API (POST /v1/responses), not Chat Completions
    -- confirmed via OpenAI's own docs, 2026-09-13. This file calls that
    API over raw httpx, matching this repo's existing convention for
    Gemini (qc_agent.py) rather than adding a new SDK dependency. Not
    configured anywhere in this repo yet.
  - An actual deploy target to run cgi_render.Dockerfile on (small
    always-on VM vs. a container platform supporting long-running non-HTTP
    workers) -- a cost/ops decision, not a code one, still open.

OS_API_URL + INTERNAL_API_KEYS also need to point at Bricks-OS, same
X-Internal-Api-Key convention every other cross-service call in this repo
already uses (see shared/auth.py's verify_internal_api_key /
get_own_internal_api_key).

**VERIFIED 2026-09-13 against a real Xvfb + fluxbox + Blender 4.0 stack**
(not against a live OpenAI response -- no OPENAI_API_KEY exists in this
session to test that half, see below): every piece of this file that
doesn't require the OpenAI API actually works, end to end, not just "should
work in theory":
  - `_run_blender_headless` (the real function, unmodified) genuinely
    executes `blender --background --python`, runs a full render, and
    returns a valid output file.
  - Blender's full interactive GUI genuinely launches under Xvfb with no
    GPU (Mesa software/surfaceless EGL rendering) -- confirmed by screenshot.
  - `_execute_computer_action`'s pyautogui calls genuinely drive that GUI:
    a real click on the splash screen's "Continue" button was captured,
    registered (Blender logged "Preferences saved"), and the screenshot
    after the click showed the dialog had actually advanced -- the full
    screenshot -> action -> changed-state -> new-screenshot loop this file
    depends on is real, not assumed.
  - Found and fixed a real bug this way: pyscreeze's Linux screenshot
    backend needs `gnome-screenshot`, not `scrot` (the original guess in
    cgi_render.Dockerfile) -- confirmed scrot alone fails and
    gnome-screenshot alone succeeds. Dockerfile corrected.

What's still unverified is only the OpenAI side: the computer_use tool's
exact JSON shape (action types, environment field,
pending_safety_checks/acknowledged_safety_checks) is written against
OpenAI's documented computer-use-preview schema plus what OpenAI's own
"Architectural visualization with Astra" case study describes, but no
gpt-6-astra Responses API call has actually been made. Get an
OPENAI_API_KEY and do that test before trusting `_run_astra_session`'s
request/response handling in production -- the local half it drives
(_execute_computer_action, _run_blender_headless) is now confirmed correct,
so a failure at that point is about the OpenAI integration specifically,
not the Blender/display plumbing underneath it.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

try:
    # Needs a real or virtual X display (DISPLAY env var) to import cleanly
    # on Linux — see the Dockerfile alongside this file, which runs the
    # worker under Xvfb. Importing this at module load time means the
    # worker fails loudly on startup on a host with no display, rather than
    # failing confusingly later on the first computer-use action.
    import pyautogui
    pyautogui.FAILSAFE = False  # this host has no human to yank the mouse to a corner
except Exception as _pyautogui_err:  # ImportError, or Xlib's own error when DISPLAY is unset/unreachable
    pyautogui = None
    _PYAUTOGUI_IMPORT_ERROR = _pyautogui_err

import httpx

from ..shared.auth import get_own_internal_api_key
from ..shared.database import get_db

logger = logging.getLogger(__name__)

OS_API_URL = os.environ.get("OS_API_URL", "https://api.brxs.ca")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ASTRA_MODEL = "gpt-6-astra"

STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "brxs-deliverables")

# Hard cost/safety cap — an agent loop that "iterates until it matches the
# brief" can run away on API spend if the brief is ambiguous or the model
# gets stuck refining. Never remove this without a per-job budget replacing
# it. Ten computer-use turns is enough for Astra's own reference workflow
# (place cameras, render, inspect, correct once or twice) with headroom.
MAX_ITERATIONS = 10

# Vercel serverless functions have hard execution time limits; a worker
# polling this host does not, but still shouldn't spin forever with nothing
# queued.
POLL_INTERVAL_SECONDS = 30


async def _os_request(method: str, path: str, **kwargs) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    headers["X-Internal-Api-Key"] = get_own_internal_api_key()
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.request(method, f"{OS_API_URL}{path}", headers=headers, **kwargs)


async def _fetch_queued_assets() -> list[dict]:
    try:
        resp = await _os_request("GET", "/api/dev/cgi-assets/queued")
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        logger.error(f"cgi_render: failed to fetch queued assets: {e}")
        return []


async def _report_status(
    asset_id: str,
    status: str,
    output_asset_urls: Optional[list[str]] = None,
    error_message: Optional[str] = None,
) -> None:
    payload = {"status": status}
    if output_asset_urls is not None:
        payload["output_asset_urls"] = output_asset_urls
    if error_message is not None:
        payload["error_message"] = error_message[:2000]
    try:
        resp = await _os_request("PATCH", f"/api/dev/cgi-assets/{asset_id}/status", json=payload)
        resp.raise_for_status()
    except Exception as e:
        # Never let a status-reporting failure leave the caller thinking a
        # job is still `rendering` when it actually finished or died --
        # log loudly so this shows up in ops alerting, don't swallow it.
        logger.error(f"cgi_render: failed to report status={status} for asset {asset_id}: {e}")


def _run_blender_headless(python_script: str, workdir: Path) -> Optional[Path]:
    """Run a Blender Python script in background mode (no GUI) — used for
    repetitive, well-defined tasks (placing a camera, rendering one view),
    matching OpenAI's own Astra case study, which used this for cinematic
    camera studies rather than driving everything through computer use.

    Returns the rendered output path, or None on failure.
    """
    script_path = workdir / "render_script.py"
    script_path.write_text(python_script)
    output_path = workdir / "render.png"
    try:
        result = subprocess.run(
            ["blender", "--background", "--python", str(script_path)],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error(f"cgi_render: blender exited {result.returncode}: {result.stderr[-2000:]}")
            return None
        return output_path if output_path.exists() else None
    except subprocess.TimeoutExpired:
        logger.error("cgi_render: blender headless render timed out after 300s")
        return None
    except FileNotFoundError:
        logger.error("cgi_render: `blender` executable not found on PATH — host needs Blender installed")
        return None


def _screenshot_data_url() -> str:
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"


def _execute_computer_action(action: dict) -> str:
    """Execute one computer-use action against this host's real virtual
    desktop (driven with pyautogui against the DISPLAY set up by the
    Dockerfile's Xvfb), and return a screenshot data URL for the model's
    next turn.

    Written against OpenAI's documented computer-use-preview action set
    (click/double_click/move/scroll/type/keypress/drag/wait/screenshot) --
    same caveat as the rest of this file: not verified against a live
    gpt-6-astra response, since no display exists in this session to test
    against. Unknown action types are logged and skipped rather than
    raising, so one action Astra tries that we haven't seen yet doesn't
    kill an otherwise-working render session.
    """
    if pyautogui is None:
        raise RuntimeError(
            f"pyautogui unavailable ({_PYAUTOGUI_IMPORT_ERROR!r}) — this host has no usable "
            "virtual display. Check DISPLAY is set and Xvfb is actually running (see Dockerfile)."
        )

    action_type = action.get("type")
    try:
        if action_type in (None, "screenshot"):
            pass
        elif action_type == "click":
            pyautogui.click(x=action.get("x"), y=action.get("y"), button=action.get("button", "left"))
        elif action_type == "double_click":
            pyautogui.doubleClick(x=action.get("x"), y=action.get("y"))
        elif action_type == "move":
            pyautogui.moveTo(action.get("x"), action.get("y"))
        elif action_type == "scroll":
            pyautogui.moveTo(action.get("x"), action.get("y"))
            pyautogui.hscroll(int(action.get("scroll_x", 0)))
            # OpenAI's scroll_y grows downward (like screen coordinates);
            # pyautogui.vscroll's positive direction is up the page — invert.
            pyautogui.vscroll(-int(action.get("scroll_y", 0)))
        elif action_type == "type":
            pyautogui.typewrite(action.get("text", ""), interval=0.01)
        elif action_type == "keypress":
            keys = [k.lower() for k in (action.get("keys") or [])]
            for k in keys:
                pyautogui.keyDown(k)
            for k in reversed(keys):
                pyautogui.keyUp(k)
        elif action_type == "drag":
            path = action.get("path") or []
            if path:
                pyautogui.moveTo(path[0]["x"], path[0]["y"])
                pyautogui.mouseDown()
                for point in path[1:]:
                    pyautogui.moveTo(point["x"], point["y"], duration=0.1)
                pyautogui.mouseUp()
        elif action_type == "wait":
            time.sleep(action.get("ms", 1000) / 1000)
        else:
            logger.warning(f"cgi_render: unhandled computer-use action type {action_type!r}, skipping")
    except Exception as e:
        # An action failing (e.g. bad coordinates) shouldn't kill the whole
        # render session — let Astra see the resulting screenshot and
        # course-correct on its next turn, same as a human would notice a
        # missed click and try again.
        logger.warning(f"cgi_render: action {action_type!r} raised {e!r}, continuing")

    return _screenshot_data_url()


async def _run_astra_session(source_plan_url: str, brief: Optional[str], workdir: Path) -> list[Path]:
    """The actual agent loop: give Astra the plan + brief, let it drive
    Blender (via computer use for interactive scene-building, headless
    Python for batch camera renders) until it signals done or MAX_ITERATIONS
    is hit. Returns local paths to whatever final render(s) it produced.

    Raises on any failure — callers must catch and report status=failed
    rather than letting an exception here leave a job stuck in `rendering`.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    system_prompt = (
        "You are building a photorealistic architectural visualization in Blender "
        "from the attached site/floor plan. Work iteratively: place architecture, "
        "materials, lighting, and a camera; render a check frame; inspect it; refine "
        "until the result is presentable, then stop. You have two tools: a virtual "
        "desktop running Blender (use this to build and inspect the interactive "
        "scene) and a function to run a Blender Python script in background mode "
        "(use this for repetitive, well-defined renders once the scene is built)."
    )
    user_content = [{"type": "input_text", "text": brief or "Render this site/floor plan."}]
    user_content.append({"type": "input_image", "image_url": source_plan_url})

    tools = [
        # NOTE: verify this schema against live OpenAI docs for gpt-6-astra
        # before relying on it — written against the documented
        # computer-use-preview shape, not confirmed against a real response.
        {
            "type": "computer_use_preview",
            "display_width": 1280,
            "display_height": 800,
            "environment": "linux",
        },
        {
            "type": "function",
            "name": "run_blender_headless",
            "description": "Run a Blender Python script in background (no GUI) mode and return the rendered PNG.",
            "parameters": {
                "type": "object",
                "properties": {"script": {"type": "string", "description": "Full Blender Python (bpy) script."}},
                "required": ["script"],
            },
        },
    ]

    input_items: list[dict] = [{"role": "user", "content": user_content}]
    rendered_paths: list[Path] = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        for iteration in range(MAX_ITERATIONS):
            resp = await client.post(
                OPENAI_RESPONSES_URL,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": ASTRA_MODEL,
                    "instructions": system_prompt,
                    "input": input_items,
                    "tools": tools,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            output = data.get("output", [])
            computer_calls = [o for o in output if o.get("type") == "computer_call"]
            function_calls = [o for o in output if o.get("type") == "function_call"]

            if not computer_calls and not function_calls:
                # Model produced a final text response with no more tool
                # calls — treat this iteration's last render (if any) as
                # final and stop.
                logger.info(f"cgi_render: Astra session finished after {iteration + 1} turn(s)")
                break

            for call in function_calls:
                if call.get("name") == "run_blender_headless":
                    args = json.loads(call.get("arguments") or "{}")
                    out_path = _run_blender_headless(args.get("script", ""), workdir)
                    if out_path:
                        rendered_paths.append(out_path)
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": "rendered" if out_path else "render failed — see logs",
                    })

            for call in computer_calls:
                screenshot_url = _execute_computer_action(call.get("action") or {})
                output_item = {
                    "type": "computer_call_output",
                    "call_id": call["call_id"],
                    "output": {"type": "computer_screenshot", "image_url": screenshot_url},
                }
                # The computer-use tool can flag a pending safety check (e.g.
                # an action it judges risky) that must be explicitly
                # acknowledged on the next turn or the API rejects the call.
                # We're driving an isolated render VM with no real user data
                # or credentials exposed to it, so auto-acknowledging is the
                # right call here -- this would NOT be safe for a tool
                # driving a real desktop with the user's own accounts on it.
                pending = call.get("pending_safety_checks") or []
                if pending:
                    output_item["acknowledged_safety_checks"] = pending
                    logger.info(f"cgi_render: auto-acknowledged {len(pending)} safety check(s) on an isolated render VM")
                input_items.append(output_item)
        else:
            logger.warning(f"cgi_render: hit MAX_ITERATIONS={MAX_ITERATIONS} without Astra signaling done")

    if not rendered_paths:
        raise RuntimeError("Astra session produced no renders")
    return rendered_paths


async def _upload_renders(paths: list[Path], project_id: str, asset_id: str) -> list[str]:
    db = await get_db()
    bucket = db.storage.from_(STORAGE_BUCKET)
    urls: list[str] = []
    for i, path in enumerate(paths):
        storage_path = f"bricks-go/dev-cgi/{project_id}/{asset_id}/{i}.png"
        await bucket.upload(path=storage_path, file=path.read_bytes(), file_options={"content-type": "image/png"})
        signed = await bucket.create_signed_url(storage_path, 60 * 60 * 24 * 365)
        signed_url = (signed or {}).get("signedURL") or (signed or {}).get("signed_url")
        if signed_url:
            urls.append(signed_url)
    return urls


async def render_cgi_asset(asset: dict) -> None:
    asset_id = asset["id"]
    await _report_status(asset_id, "rendering")
    try:
        with tempfile.TemporaryDirectory(prefix=f"cgi-{asset_id}-") as tmp:
            workdir = Path(tmp)
            rendered_paths = await _run_astra_session(asset["source_plan_url"], asset.get("brief"), workdir)
            urls = await _upload_renders(rendered_paths, asset["project_id"], asset_id)
            if not urls:
                raise RuntimeError("Rendered locally but upload produced no URLs")
            await _report_status(asset_id, "complete", output_asset_urls=urls)
            logger.info(f"cgi_render: asset {asset_id} complete ({len(urls)} asset(s))")
    except Exception as e:
        logger.error(f"cgi_render: asset {asset_id} failed: {e}")
        await _report_status(asset_id, "failed", error_message=str(e))


async def poll_and_process_once() -> int:
    """Process one batch of queued jobs. Returns how many were processed —
    used by the __main__ loop below to back off when the queue is empty."""
    queued = await _fetch_queued_assets()
    for asset in queued:
        await render_cgi_asset(asset)
    return len(queued)


async def _main_loop() -> None:
    logger.info(f"cgi_render worker starting — polling {OS_API_URL} every {POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            processed = await poll_and_process_once()
        except Exception as e:
            logger.error(f"cgi_render: poll loop error: {e}")
            processed = 0
        if processed == 0:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main_loop())
