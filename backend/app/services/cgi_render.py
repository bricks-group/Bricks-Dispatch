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
  - Headless `blender --background --python` genuinely runs a full render
    and returns a valid output file.
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
(_execute_computer_action, _render_camera_view) is confirmed correct, so a
failure at that point is about the OpenAI integration specifically, not the
Blender/display plumbing underneath it.

Trust boundary: the brief and plan come from customers and steer the model,
so everything the model emits is untrusted.
  - The worker never runs model-written code. The headless tool is a fixed,
    parameterized camera render (`render_camera_view`): the model supplies
    validated numbers and an optional scene filename, and the repo-owned
    `cgi_render_blender/render_camera_view.py` does the rendering, with
    `--factory-startup --disable-autoexec` and a scrubbed environment.
  - The virtual desktop the model drives (Xvfb, fluxbox, anything launched
    from it, including GUI Blender and its Python console) runs as a
    separate OS user with an empty environment (cgi_render_entrypoint.sh).
    It cannot read this worker's credentials or its work directories. The
    only thing it hands back is scene files saved in CGI_SCENE_DIR, which
    this worker copies without following links and opens with autoexec off.
  - Computer-use safety checks are never auto-acknowledged; a pending check
    fails the asset.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

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

# Where the desktop session (a different OS user, see cgi_render_entrypoint.sh)
# saves .blend scenes for render_camera_view to pick up by filename.
CGI_SCENE_DIR = Path(os.environ.get("CGI_SCENE_DIR", "/srv/cgi-scenes"))
MAX_SCENE_BYTES = 512 * 1024 * 1024
BLENDER_TIMEOUT_SECONDS = 300

_RENDER_VIEW_SCRIPT = (
    Path(__file__).resolve().parents[2] / "cgi_render_blender" / "render_camera_view.py"
)
_SCENE_FILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.blend")
_RENDER_PARAM_KEYS = {
    "camera_location",
    "camera_target",
    "lens_mm",
    "resolution_x",
    "resolution_y",
    "samples",
    "scene_file",
}
_MAX_COORDINATE = 10_000.0


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


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return value


def _integer(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must be a list of three numbers")
    return [_number(v, f"{name}[{i}]", -_MAX_COORDINATE, _MAX_COORDINATE) for i, v in enumerate(value)]


def _parse_render_params(raw_arguments: Optional[str]) -> dict:
    """Validate render_camera_view arguments into plain numbers and a bare
    scene filename. Anything else the model sends is refused, never run."""
    try:
        args = json.loads(raw_arguments or "{}")
    except (TypeError, ValueError):
        raise ValueError("arguments must be a JSON object")
    if not isinstance(args, dict):
        raise ValueError("arguments must be a JSON object")
    unknown = set(args) - _RENDER_PARAM_KEYS
    if unknown:
        raise ValueError(f"unsupported argument(s): {', '.join(sorted(map(str, unknown)))}")

    params = {
        "camera_location": _vector(args.get("camera_location"), "camera_location"),
        "camera_target": _vector(args.get("camera_target"), "camera_target"),
        "lens_mm": _number(args.get("lens_mm", 35), "lens_mm", 8, 300),
        "resolution_x": _integer(args.get("resolution_x", 1920), "resolution_x", 64, 4096),
        "resolution_y": _integer(args.get("resolution_y", 1080), "resolution_y", 64, 4096),
        "samples": _integer(args.get("samples", 128), "samples", 1, 1024),
    }
    if math.dist(params["camera_location"], params["camera_target"]) < 1e-6:
        raise ValueError("camera_target must differ from camera_location")

    scene_file = args.get("scene_file")
    if scene_file is not None:
        if not isinstance(scene_file, str) or not _SCENE_FILE_RE.fullmatch(scene_file):
            raise ValueError("scene_file must be a bare .blend filename saved in the scene directory")
    params["scene_file"] = scene_file
    return params


def _copy_scene_file(name: str, dest: Path) -> None:
    """Copy a scene the desktop user saved into this worker's private workdir.

    The scene directory is writable by the desktop user, so the file is
    opened once, without following links, and checked through that same
    descriptor; Blender only ever sees the private copy.
    """
    dir_fd = os.open(CGI_SCENE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    with os.fdopen(fd, "rb") as src:
        st = os.fstat(src.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("scene_file is not a regular file")
        if st.st_size > MAX_SCENE_BYTES:
            raise ValueError("scene_file is too large")
        copied = 0
        with open(dest, "xb") as dst:
            while chunk := src.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_SCENE_BYTES:
                    raise ValueError("scene_file is too large")
                dst.write(chunk)


def _blender_env(workdir: Path) -> dict[str, str]:
    # Nothing from the worker's own environment (Supabase, internal and
    # OpenAI keys) may reach Blender.
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
    }


def _render_camera_view(params: dict, workdir: Path, index: int) -> Optional[Path]:
    """Render one camera view headlessly — used for repetitive, well-defined
    renders, matching OpenAI's own Astra case study, which used this for
    cinematic camera studies rather than driving everything through computer
    use. Runs only the repo-owned render script; `params` must come from
    `_parse_render_params`.

    Returns the rendered output path, or None on failure.
    """
    blender = shutil.which("blender")
    if blender is None:
        logger.error("cgi_render: `blender` executable not found on PATH — host needs Blender installed")
        return None

    output_path = workdir / f"render-{index}.png"
    cmd = [
        blender,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code", "1",
    ]
    if params.get("scene_file"):
        scene_copy = workdir / f"scene-{index}.blend"
        try:
            _copy_scene_file(params["scene_file"], scene_copy)
        except (OSError, ValueError) as e:
            logger.error(f"cgi_render: could not load scene {params['scene_file']!r}: {e}")
            return None
        cmd.append(str(scene_copy))

    render_args = {k: params[k] for k in _RENDER_PARAM_KEYS if k != "scene_file"}
    render_args["output_path"] = str(output_path)
    cmd += ["--python", str(_RENDER_VIEW_SCRIPT), "--", json.dumps(render_args)]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(workdir),
            env=_blender_env(workdir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=BLENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"cgi_render: blender headless render timed out after {BLENDER_TIMEOUT_SECONDS}s")
        return None
    if result.returncode != 0:
        logger.error(f"cgi_render: blender exited {result.returncode}: {result.stderr[-2000:]}")
        return None
    try:
        st = os.lstat(output_path)
    except FileNotFoundError:
        return None
    return output_path if stat.S_ISREG(st.st_mode) else None


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
        "scene) and render_camera_view, which renders one camera view of a scene "
        "in background mode (use this for repetitive, well-defined renders once "
        f"the scene is built). Save the scene as a .blend file in {CGI_SCENE_DIR} "
        "and pass its filename as scene_file."
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
            "name": "render_camera_view",
            "description": (
                "Render one still PNG of a saved scene from a camera at camera_location "
                "aimed at camera_target (Cycles, CPU)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_file": {
                        "type": "string",
                        "description": f"Bare .blend filename saved in {CGI_SCENE_DIR}. Omit for an empty scene.",
                    },
                    "camera_location": {
                        "type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                        "description": "Camera position [x, y, z] in metres.",
                    },
                    "camera_target": {
                        "type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                        "description": "Point [x, y, z] the camera looks at.",
                    },
                    "lens_mm": {"type": "number", "minimum": 8, "maximum": 300},
                    "resolution_x": {"type": "integer", "minimum": 64, "maximum": 4096},
                    "resolution_y": {"type": "integer", "minimum": 64, "maximum": 4096},
                    "samples": {"type": "integer", "minimum": 1, "maximum": 1024},
                },
                "required": ["camera_location", "camera_target"],
                "additionalProperties": False,
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
                if call.get("name") != "render_camera_view":
                    result = f"unknown tool {call.get('name')!r}"
                else:
                    try:
                        params = _parse_render_params(call.get("arguments"))
                    except ValueError as e:
                        logger.warning(f"cgi_render: rejected render_camera_view arguments: {e}")
                        result = f"rejected: {e}"
                    else:
                        out_path = _render_camera_view(params, workdir, len(rendered_paths))
                        if out_path:
                            rendered_paths.append(out_path)
                        result = "rendered" if out_path else "render failed — see logs"
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": result,
                })

            for call in computer_calls:
                # A pending safety check means the model flagged this action
                # as needing human confirmation. No human watches this
                # worker, and the brief steering the model is customer
                # input, so the action is refused rather than acknowledged.
                if call.get("pending_safety_checks"):
                    raise RuntimeError(
                        "Astra requested an action that needs safety-check confirmation; refusing it"
                    )
                screenshot_url = _execute_computer_action(call.get("action") or {})
                input_items.append({
                    "type": "computer_call_output",
                    "call_id": call.get("call_id"),
                    "output": {"type": "computer_screenshot", "image_url": screenshot_url},
                })
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


def _make_worker_non_dumpable() -> None:
    """Stop processes running as this worker's own user (e.g. Blender) from
    reading /proc/<worker pid>/environ, where the credentials live."""
    try:
        import ctypes

        pr_set_dumpable = 4
        if ctypes.CDLL(None, use_errno=True).prctl(pr_set_dumpable, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")
    except Exception as e:
        logger.warning(f"cgi_render: could not mark worker non-dumpable: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _make_worker_non_dumpable()
    asyncio.run(_main_loop())
