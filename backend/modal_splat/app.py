"""
Bricks 3DGS Processing Service — Modal GPU App

Converts photographer-captured video/image-sequence zips into
Gaussian splat .ply files using nerfstudio's splatfacto model.

Deploy:
  modal deploy backend/modal_splat/app.py

Secrets required (modal secret named "bricks-splat"):
  SUPABASE_URL          — Supabase project URL
  SUPABASE_SERVICE_KEY  — Supabase service role key (bypasses RLS)
  DISPATCH_API_URL      — Bricks Dispatch API base URL
  SPLAT_API_KEY         — Shared secret (same as INTERNAL_API_KEY in Dispatch)

Architecture:
  POST /trigger  (web endpoint, no GPU)
    └─ spawns process_scene on A10G
          ├─ download source video from Supabase Storage
          ├─ ns-process-data  (COLMAP SfM, frame extraction)
          ├─ ns-train splatfacto  (3DGS training, 15k iterations)
          ├─ ns-export gaussian-splat  (.ply export)
          ├─ upload scene.ply to brxs-deliverables/splats/{job_id}/
          └─ POST /api/v1/post-production/splat-complete/{job_id}
"""

import os
import modal

# ---------------------------------------------------------------------------
# Container images
# ---------------------------------------------------------------------------

# Trigger image: minimal, no GPU needed
_TRIGGER_IMAGE = modal.Image.debian_slim().pip_install("httpx>=0.26", "pydantic>=2.0")

# Processing image: nerfstudio with CUDA + COLMAP + splatfacto
# Build once; cached by Modal between calls.
_PROCESS_IMAGE = (
    modal.Image.from_registry(
        "dromni/nerfstudio:main",
        add_python="3.10",
    )
    .apt_install("ffmpeg", "libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "httpx>=0.26",
        "supabase>=2.0",
        "Pillow>=10.0",
        "plyfile>=0.9",
    )
)

# ---------------------------------------------------------------------------
# Modal app
# ---------------------------------------------------------------------------

app = modal.App("bricks-splat-processor")

STORAGE_BUCKET = "brxs-deliverables"
_SIGNED_URL_EXPIRY = 604800  # 7 days

# ---------------------------------------------------------------------------
# Trigger endpoint (lightweight — no GPU, returns immediately)
# ---------------------------------------------------------------------------

@app.function(image=_TRIGGER_IMAGE, secrets=[modal.Secret.from_name("bricks-splat")])
@modal.web_endpoint(method="POST", docs=False)
def trigger(body: dict) -> dict:
    """
    Receives a splat processing request and spawns the GPU function.
    Returns immediately with {"status": "queued", "job_id": ...}.

    Expected body:
      {
        "job_id":    "<dispatch job id>",
        "source_url": "<signed Supabase Storage URL of source video or zip>"
      }

    Auth: X-Splat-Api-Key header must match SPLAT_API_KEY env var.
    """
    import httpx

    # Auth check
    api_key = os.environ.get("SPLAT_API_KEY", "")
    if not api_key:
        return {"error": "SPLAT_API_KEY not configured", "status": "error"}

    # We receive auth via the request context — Modal web endpoints don't expose
    # raw headers easily. We pass the key in the body for simplicity.
    if body.get("api_key") != api_key:
        return {"error": "Unauthorized", "status": "error"}

    job_id    = body.get("job_id", "")
    source_url = body.get("source_url", "")
    if not job_id or not source_url:
        return {"error": "job_id and source_url are required", "status": "error"}

    # Fire and forget — process_scene runs on GPU without blocking this response
    process_scene.spawn(job_id, source_url)

    return {"status": "queued", "job_id": job_id}


# ---------------------------------------------------------------------------
# GPU processing function
# ---------------------------------------------------------------------------

@app.function(
    image=_PROCESS_IMAGE,
    gpu="A10G",
    timeout=2700,       # 45 minutes — COLMAP ~10 min + training ~25 min on A10G
    retries=modal.Retries(max_retries=1, backoff_coefficient=2.0, initial_delay=30.0),
    secrets=[modal.Secret.from_name("bricks-splat")],
    # Ephemeral disk large enough for video + frames + training data
    ephemeral_disk=50 * 1024,  # 50 GiB
)
def process_scene(job_id: str, source_url: str) -> dict:
    """
    Full 3DGS pipeline:
    1. Download source video / image-sequence zip
    2. ns-process-data  (COLMAP SfM + frame extraction)
    3. ns-train splatfacto (3DGS training)
    4. ns-export gaussian-splat (.ply)
    5. Upload to Supabase Storage
    6. Notify Dispatch API with signed URL

    Runs on NVIDIA A10G GPU (~20–35 min for a typical property capture).
    """
    import subprocess
    import tempfile
    import shutil
    from pathlib import Path
    import httpx
    from supabase import create_client

    logger = _get_logger(job_id)
    logger("pipeline start")

    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_SERVICE_KEY"]
    dispatch_url = os.environ.get("DISPATCH_API_URL", "")
    api_key      = os.environ.get("SPLAT_API_KEY", "")

    with tempfile.TemporaryDirectory() as _tmp:
        tmp = Path(_tmp)

        # ── 1. Download source ──────────────────────────────────────────────
        source_dir = tmp / "source"
        source_dir.mkdir()
        logger(f"downloading source from {source_url[:60]}…")
        try:
            r = httpx.get(source_url, follow_redirects=True, timeout=180.0)
            r.raise_for_status()
        except Exception as e:
            _notify_failure(dispatch_url, api_key, job_id, f"Download failed: {e}")
            raise

        content_type = r.headers.get("content-type", "")
        ext = ".zip" if "zip" in content_type or source_url.endswith(".zip") else ".mp4"
        source_path = source_dir / f"capture{ext}"
        source_path.write_bytes(r.content)
        mb = len(r.content) / 1e6
        logger(f"downloaded {mb:.1f} MB as {ext}")

        # ── 2. ns-process-data ──────────────────────────────────────────────
        proc_dir = tmp / "processed"
        logger("running ns-process-data (COLMAP SfM)…")
        try:
            if ext == ".zip":
                cmd = [
                    "ns-process-data", "images",
                    "--data", str(source_path),
                    "--output-dir", str(proc_dir),
                ]
            else:
                cmd = [
                    "ns-process-data", "video",
                    "--data", str(source_path),
                    "--output-dir", str(proc_dir),
                    "--num-frames-target", "250",
                    "--matching-method", "sequential",
                ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                err = result.stderr[-2000:]
                logger(f"ns-process-data stderr: {err}")
                _notify_failure(dispatch_url, api_key, job_id, f"COLMAP failed: {err}")
                raise RuntimeError("ns-process-data failed")
        except subprocess.TimeoutExpired:
            _notify_failure(dispatch_url, api_key, job_id, "COLMAP timed out after 15 min")
            raise
        logger("COLMAP SfM complete")

        # ── 3. ns-train splatfacto ──────────────────────────────────────────
        train_dir = tmp / "trained"
        logger("training splatfacto (15k iterations)…")
        try:
            train_result = subprocess.run([
                "ns-train", "splatfacto",
                "--data", str(proc_dir),
                "--output-dir", str(train_dir),
                "--max-num-iterations", "15000",
                "--pipeline.model.densify-grad-thresh", "0.0008",
                "--pipeline.model.cull-alpha-thresh", "0.005",
                "--steps-per-save", "15000",  # only save at end
                "nerfstudio-data",
                "--eval-mode", "all",
            ], capture_output=True, text=True, timeout=2100)
            if train_result.returncode != 0:
                err = train_result.stderr[-2000:]
                logger(f"ns-train stderr: {err}")
                _notify_failure(dispatch_url, api_key, job_id, f"Training failed: {err}")
                raise RuntimeError("ns-train failed")
        except subprocess.TimeoutExpired:
            _notify_failure(dispatch_url, api_key, job_id, "Training timed out after 35 min")
            raise
        logger("splatfacto training complete")

        # ── 4. Export .ply ──────────────────────────────────────────────────
        run_dirs = sorted((train_dir / "splatfacto").iterdir()) if (train_dir / "splatfacto").exists() else []
        if not run_dirs:
            _notify_failure(dispatch_url, api_key, job_id, "No training run directory found")
            raise RuntimeError("No splatfacto run found")
        config_yml = run_dirs[-1] / "config.yml"

        export_dir = tmp / "export"
        export_dir.mkdir()
        logger("exporting .ply…")
        export_result = subprocess.run([
            "ns-export", "gaussian-splat",
            "--load-config", str(config_yml),
            "--output-dir", str(export_dir),
        ], capture_output=True, text=True, timeout=300)
        if export_result.returncode != 0:
            err = export_result.stderr[-2000:]
            _notify_failure(dispatch_url, api_key, job_id, f"Export failed: {err}")
            raise RuntimeError("ns-export failed")

        ply_candidates = list(export_dir.rglob("*.ply"))
        if not ply_candidates:
            _notify_failure(dispatch_url, api_key, job_id, "No .ply file after export")
            raise RuntimeError("No .ply produced by ns-export")
        ply_path = ply_candidates[0]
        ply_bytes = ply_path.read_bytes()
        logger(f".ply exported: {len(ply_bytes) / 1e6:.1f} MB")

        # ── 5. Upload to Supabase Storage ───────────────────────────────────
        storage_path = f"splats/{job_id}/scene.ply"
        sb = create_client(supabase_url, supabase_key)
        sb.storage.from_(STORAGE_BUCKET).upload(
            storage_path,
            ply_bytes,
            {"content-type": "model/ply", "upsert": "true"},
        )
        logger(f"uploaded to {STORAGE_BUCKET}/{storage_path}")

        # Signed URL (7 days)
        result = sb.storage.from_(STORAGE_BUCKET).create_signed_url(
            storage_path, _SIGNED_URL_EXPIRY
        )
        signed_url = (
            result.get("signedURL") or result.get("signedUrl") or result.get("signed_url")
        )
        if not signed_url:
            _notify_failure(dispatch_url, api_key, job_id, f"Signed URL missing: {result}")
            raise RuntimeError("Could not generate signed URL")
        logger(f"signed URL ready")

        # ── 6. Notify Dispatch API ──────────────────────────────────────────
        if dispatch_url and api_key:
            _notify_complete(dispatch_url, api_key, job_id, signed_url)
        else:
            logger("DISPATCH_API_URL or SPLAT_API_KEY not set — skipping notification")

        logger("pipeline complete ✓")
        return {"job_id": job_id, "splat_url": signed_url, "storage_path": storage_path}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_logger(job_id: str):
    def log(msg: str) -> None:
        print(f"[bricks-splat][{job_id}] {msg}", flush=True)
    return log


def _notify_complete(dispatch_url: str, api_key: str, job_id: str, splat_url: str) -> None:
    import httpx
    try:
        resp = httpx.post(
            f"{dispatch_url.rstrip('/')}/api/v1/post-production/splat-complete/{job_id}",
            json={"splat_url": splat_url},
            headers={"X-Internal-Api-Key": api_key},
            timeout=15.0,
        )
        print(f"[bricks-splat][{job_id}] notify complete → {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[bricks-splat][{job_id}] notify failed: {e}", flush=True)


def _notify_failure(dispatch_url: str, api_key: str, job_id: str, reason: str) -> None:
    if not dispatch_url or not api_key:
        return
    import httpx
    try:
        httpx.post(
            f"{dispatch_url.rstrip('/')}/api/v1/post-production/splat-failed/{job_id}",
            json={"reason": reason[:500]},
            headers={"X-Internal-Api-Key": api_key},
            timeout=15.0,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Local entrypoint for testing
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(source_url: str = "", job_id: str = "local-test"):
    """
    Test locally (requires modal auth):
      modal run backend/modal_splat/app.py --source-url=<url>
    """
    if not source_url:
        print("Usage: modal run backend/modal_splat/app.py --source-url=<url> --job-id=<id>")
        return
    result = process_scene.remote(job_id, source_url)
    print(f"Result: {result}")
