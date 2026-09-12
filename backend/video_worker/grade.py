"""
In-house video grading / trim / stabilization -- classical ffmpeg-filtergraph
processing for real camera footage (walkthrough + drone video), NOT AI-generated
video and NOT a port of photo_enhance_service.py's per-pixel algorithm (running
that frame-by-frame over video would be far too slow -- see the module docstring
below for the actual mapping used instead).

STATUS: researched and built 2026-09-12, deliberately NOT wired into the live
pipeline (`app/services/post_production.py::process_job_assets`) or imported by
`app/main.py` / anything Vercel deploys. This module has zero import-time side
effects on the rest of the app -- it is safe to have on disk unused. See
CLAUDE.md's "Motion (Video) Enhancement -- Researched, Not Deployed" section for
the full reasoning; short version:

  - This backend deploys to Vercel as a single Python serverless function
    (`backend/vercel.json` -- legacy `@vercel/python` builder, `maxLambdaSize:
    250mb`, `maxDuration: 300`). Vercel's own guidance is that bundling an
    ffmpeg binary into a Python serverless function is "not recommended" --
    the static binary alone (~70-100MB) sits on top of this deployment's
    already-bundled opencv-python-headless/numpy/Pillow, real risk of blowing
    the configured 250MB uncompressed cap (even the newer, non-legacy 500MB
    Python default wouldn't leave much headroom once you also count cold-start
    reliability and every other route this same function serves).
  - Execution time is the harder blocker: `maxDuration: 300` matches Vercel
    Hobby's Fluid Compute ceiling (fixed, cannot be raised); Pro/Enterprise can
    reach 800s standard / 1800s beta, but nothing in this repo's config or
    CLAUDE.md indicates which plan is actually in use, and even a generous
    budget is a real risk for actual "drone footage shot on-site" (this task's
    own framing) rather than only the packaged 60-90s cinematic-cut deliverable
    -- raw multi-minute source footage plus two-pass stabilization (a real,
    CPU-heavy motion-analysis pass, timed below) is not a safe bet against any
    hard serverless ceiling, since a mid-encode kill leaves a stuck job with no
    partial-output recovery story.
  - This repo already ships a `backend/Dockerfile` (installs system packages
    via apt-get, runs `uvicorn app.main:app` directly, no serverless wrapper)
    -- unlike the Vercel path, a container host has no per-invocation size/time
    ceiling and could `apt-get install ffmpeg` trivially. Nothing in this
    repo's docs says that Dockerfile is an actual live deployment target today
    (CLAUDE.md's "Deployed:" line names only the two Vercel-fronted hosts) --
    so this finding is reported, not assumed, and this module is not wired to
    it. If it *is* (or becomes) a real host, or a dedicated worker / the
    Mac-inference-server infra a parallel image-generation effort is building
    gets stood up, that is the natural place to import and call this module
    from -- see `process_video()` below for the one function such an
    integration would call.

This module has no dependency on anything else in `app/` -- it shells out to a
system `ffmpeg`/`ffprobe` binary via subprocess and operates on local file
paths in and out. A future integration is expected to handle downloading the
source video from Supabase Storage and uploading the result back (mirroring
`app/services/storage.py`'s conventions, the same way `photo_enhance_service.py`
is wrapped by `post_production.py::_run_inhouse_cv_enhancement` for photos) --
that wrapping is deliberately NOT built here, since it doesn't have a real host
to run on yet.

Grading filtergraph -- translating photo_enhance_service.py's *intent* into
ffmpeg filters (not its exact per-pixel math; per-frame OpenCV over video is
the "too slow" case this task explicitly ruled out):

  photo_enhance_service.py step          ffmpeg filtergraph equivalent
  --------------------------------------  ----------------------------------
  1. CLAHE on L channel (LAB) -- local    `clahe` filter (same algorithm
     contrast / shadow-highlight recovery  family, available in most ffmpeg
                                            builds -- NOT this repo's own
                                            test-verified build, see
                                            `_HISTEQ_FALLBACK_NOTE` below).
                                            Falls back to `histeq`, ffmpeg's
                                            built-in *global* histogram
                                            equalizer, when `clahe` isn't
                                            compiled in -- weaker (global, not
                                            tile-based/local) but same
                                            contrast-recovery intent, and
                                            ships in every mainstream ffmpeg
                                            build (verified locally).
  2. Edge-aware detail enhancement         No single ffmpeg filter matches
     (cv2.detailEnhance)                   detailEnhance's edge-aware local-
                                            contrast behavior without extra
                                            libraries; folded into the final
                                            `unsharp` step below instead of a
                                            separate stage (unsharp already
                                            gives crispness/local-contrast; a
                                            second dedicated stage would be
                                            redundant for video, where per-
                                            frame cost matters more than for a
                                            single still).
  3. Grey-world white balance              `colorbalance` -- a fixed, mild
                                            shadow/midtone cool-cast reduction
                                            (small red-up / blue-down nudge),
                                            not a true per-shot grey-world
                                            average (that needs a frame
                                            analysis pass, i.e. the "too slow"
                                            case again). Real-estate footage
                                            skews cool under overcast daylight
                                            or fluorescent interior lighting
                                            far more often than it skews warm,
                                            so a fixed correction in that
                                            direction is a reasonable default,
                                            not a data-driven one.
  4. Unsharp mask (final crispness)        `unsharp` -- same role, ffmpeg-
                                            native.
  (new) mild saturation lift, matching     `eq=saturation=...` -- not present
  the "mild saturation/detail boost" this  as a discrete step in
  task asked for, and matching the         photo_enhance_service.py today,
  overall "slightly richer, not garish"    but explicitly requested for video
  look the photo pipeline produces         grading by this task's own spec.
  end-to-end.

None of this is per-shot adaptive (no scene analysis, no auto-exposure curve
fit) -- it is a fixed, conservative grade applied uniformly, by design: this
task explicitly asked for a translation of *intent* via ffmpeg's filtergraph,
not a port of the adaptive per-pixel algorithm.
"""

from __future__ import annotations

import functools
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

FFMPEG_BIN = os.environ.get("FFMPEG_BINARY", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BINARY", "ffprobe")

# Fixed, conservative grading parameters -- see the module docstring's mapping
# table for what each maps to in photo_enhance_service.py's intent.
_CLAHE_CLIP_LIMIT = 2.0  # matches cv2.createCLAHE(clipLimit=2.0) in photo_enhance_service.py
_HISTEQ_STRENGTH = 0.15  # ffmpeg histeq fallback when `clahe` isn't compiled in
_HISTEQ_INTENSITY = 0.35
_COLORBALANCE_ARGS = "rs=0.02:gs=0.0:bs=-0.03:rm=0.02:gm=0.0:bm=-0.03"
_EQ_CONTRAST = 1.03
_EQ_SATURATION = 1.08
_UNSHARP_ARGS = "5:5:0.6:5:5:0.0"

_DEFAULT_CRF = 20
_DEFAULT_PRESET = "medium"
_DEFAULT_STAB_SHAKINESS = 5
_DEFAULT_STAB_ACCURACY = 15
_DEFAULT_STAB_SMOOTHING = 10


class VideoGradeError(RuntimeError):
    """Raised on any ffmpeg/ffprobe invocation failure. Carries the tail of
    stderr so a caller/log line has something actionable without needing to
    re-run manually."""


@dataclass
class ProcessResult:
    output_path: str
    graded: bool = True
    stabilized: bool = False
    trimmed: bool = False
    source_duration_s: Optional[float] = None
    output_duration_s: Optional[float] = None
    warnings: list = field(default_factory=list)


def ffmpeg_available() -> bool:
    """True if both `ffmpeg` and `ffprobe` binaries are on PATH (or the
    FFMPEG_BINARY/FFPROBE_BINARY env overrides point at real executables)."""
    return shutil.which(FFMPEG_BIN) is not None and shutil.which(FFPROBE_BIN) is not None


def _run(cmd: list, *, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    logger.info("video_worker: running %s", " ".join(shlex.quote(c) for c in cmd))
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise VideoGradeError(f"binary not found: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise VideoGradeError(f"timed out after {timeout}s: {' '.join(cmd)}") from e

    if proc.returncode != 0:
        stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise VideoGradeError(
            f"command failed (exit {proc.returncode}): {' '.join(cmd)}\n{stderr_tail}"
        )
    return proc


@functools.lru_cache(maxsize=1)
def _available_filters() -> frozenset:
    """Parses `ffmpeg -filters` once per process. Feature-detection, not a
    version check -- different ffmpeg builds (and build flags, e.g.
    --enable-libvidstab) genuinely vary in what's compiled in; this repo's own
    verified local build has libvidstab but NOT the `clahe` filter, which is
    exactly the kind of variance this guards against instead of assuming."""
    if not ffmpeg_available():
        return frozenset()
    try:
        proc = subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except Exception:
        logger.warning("video_worker: could not enumerate ffmpeg filters", exc_info=True)
        return frozenset()

    names = set()
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        # Filter list lines look like: " T.C eq               V->V   Adjust ..."
        # -- 3-char capability flags, then name, then an I/O arrow (e.g.
        # "V->V", "A->A", "AA->A"). Requiring the arrow token excludes the
        # legend lines above the list (e.g. " T.. = Timeline support"), which
        # would otherwise also match the flag-column shape.
        if (
            len(parts) >= 3
            and len(parts[0]) == 3
            and parts[0].strip("T.SDC") == ""
            and "->" in parts[2]
        ):
            names.add(parts[1])
    return frozenset(names)


def stabilization_available() -> bool:
    filters = _available_filters()
    return "vidstabdetect" in filters and "vidstabtransform" in filters


def probe_duration_seconds(path: str) -> Optional[float]:
    """Returns the container duration in seconds, or None if it can't be
    determined (e.g. a malformed/empty file) -- callers should treat that as
    "unknown", not "zero-length"."""
    try:
        proc = _run(
            [
                FFPROBE_BIN,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            timeout=30,
        )
    except VideoGradeError:
        return None
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    try:
        return float(raw)
    except ValueError:
        return None


def build_grade_filtergraph(available_filters: Optional[frozenset] = None) -> str:
    """
    Builds the fixed grading filtergraph -- see the module docstring's mapping
    table. Feature-detects `clahe` and falls back to `histeq` when it isn't
    compiled into the ffmpeg build being used (confirmed to matter: this
    repo's own tested build lacks `clahe`).
    """
    filters = _available_filters() if available_filters is None else available_filters

    stages = []
    if "clahe" in filters:
        stages.append(f"clahe=clip_limit={_CLAHE_CLIP_LIMIT}")
    else:
        stages.append(f"histeq=strength={_HISTEQ_STRENGTH}:intensity={_HISTEQ_INTENSITY}")
    stages.append(f"colorbalance={_COLORBALANCE_ARGS}")
    stages.append(f"eq=contrast={_EQ_CONTRAST}:saturation={_EQ_SATURATION}")
    stages.append(f"unsharp={_UNSHARP_ARGS}")
    return ",".join(stages)


def _stabilize_pass1(input_path: str, transforms_path: str, *, shakiness: int, accuracy: int) -> None:
    _run(
        [
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-i", input_path,
            "-vf", f"vidstabdetect=shakiness={shakiness}:accuracy={accuracy}:result={transforms_path}",
            "-f", "null", "-",
        ],
        timeout=600,
    )


def process_video(
    input_path: str,
    output_path: str,
    *,
    max_duration_s: Optional[float] = None,
    stabilize: bool = False,
    crf: int = _DEFAULT_CRF,
    preset: str = _DEFAULT_PRESET,
    shakiness: int = _DEFAULT_STAB_SHAKINESS,
    accuracy: int = _DEFAULT_STAB_ACCURACY,
    smoothing: int = _DEFAULT_STAB_SMOOTHING,
    timeout_s: float = 900.0,
) -> ProcessResult:
    """
    Grades (always), optionally stabilizes, and optionally trims `input_path`
    into `output_path` as a delivery-ready H.264/AAC mp4 (`-movflags
    +faststart`). Single final encode pass -- stabilization's transform
    filter (when available) and the grading filtergraph are chained together
    rather than run as two separate re-encodes.

    Raises VideoGradeError if ffmpeg/ffprobe aren't available or any ffmpeg
    invocation fails. ffmpeg only replaces `output_path` once it has fully
    finished encoding, so a raised VideoGradeError means either no file was
    written at that path at all, or (if a caller reused an output path from a
    prior successful run) a stale file from that earlier run is still there --
    callers should treat a raised VideoGradeError as "no new output was
    produced" and not read output_path afterward without checking for that.

    `max_duration_s`: this module intentionally hardcodes no per-package
    target length -- that constant (e.g. Companion's "60-90s cinematic
    walkthrough") lives in a different repo's config, not this one. Pass it
    in explicitly from whatever orchestration knows the package's expected
    length; omit it to grade/stabilize without trimming.
    """
    if not ffmpeg_available():
        raise VideoGradeError(
            f"ffmpeg/ffprobe not found on PATH (checked '{FFMPEG_BIN}' / '{FFPROBE_BIN}')"
        )

    warnings: list = []
    source_duration = probe_duration_seconds(input_path)

    filters = _available_filters()
    filter_stages = []
    stabilized = False

    if stabilize:
        if "vidstabdetect" in filters and "vidstabtransform" in filters:
            with tempfile.TemporaryDirectory(prefix="video_worker_stab_") as tmp_dir:
                transforms_path = os.path.join(tmp_dir, "transforms.trf")
                _stabilize_pass1(input_path, transforms_path, shakiness=shakiness, accuracy=accuracy)
                # NOTE: vidstabtransform reads the .trf file at filtergraph-build
                # time in the final encode below, so the tempdir (and the .trf
                # file inside it) must still exist when that command runs --
                # this is why the final encode is invoked from within this
                # `with` block rather than after it exits.
                filter_stages.append(
                    f"vidstabtransform=input={transforms_path}:zoom=0:smoothing={smoothing}"
                )
                filter_stages.append(build_grade_filtergraph(filters))
                stabilized = True
                _encode(
                    input_path, output_path, filter_stages,
                    max_duration_s=max_duration_s, crf=crf, preset=preset, timeout_s=timeout_s,
                )
        else:
            warnings.append(
                "stabilization requested but this ffmpeg build has no vidstabdetect/"
                "vidstabtransform (needs --enable-libvidstab) -- graded without stabilizing"
            )
            logger.warning("video_worker: %s", warnings[-1])
            filter_stages.append(build_grade_filtergraph(filters))
            _encode(
                input_path, output_path, filter_stages,
                max_duration_s=max_duration_s, crf=crf, preset=preset, timeout_s=timeout_s,
            )
    else:
        filter_stages.append(build_grade_filtergraph(filters))
        _encode(
            input_path, output_path, filter_stages,
            max_duration_s=max_duration_s, crf=crf, preset=preset, timeout_s=timeout_s,
        )

    output_duration = probe_duration_seconds(output_path)
    return ProcessResult(
        output_path=output_path,
        graded=True,
        stabilized=stabilized,
        trimmed=bool(max_duration_s and source_duration and source_duration > max_duration_s),
        source_duration_s=source_duration,
        output_duration_s=output_duration,
        warnings=warnings,
    )


def _encode(
    input_path: str,
    output_path: str,
    filter_stages: list,
    *,
    max_duration_s: Optional[float],
    crf: int,
    preset: str,
    timeout_s: float,
) -> None:
    cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y", "-i", input_path]
    if max_duration_s is not None:
        cmd += ["-t", str(max_duration_s)]
    cmd += [
        "-vf", ",".join(filter_stages),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd, timeout=timeout_s)


def trim_only(input_path: str, output_path: str, max_duration_s: float) -> None:
    """Fast, lossless trim (stream copy, no re-encode) -- for when a caller
    wants length enforcement without grading. `process_video(...,
    max_duration_s=...)` does grading and trimming together in one encode;
    this is the standalone alternative when only trimming is needed."""
    if not ffmpeg_available():
        raise VideoGradeError(
            f"ffmpeg not found on PATH (checked '{FFMPEG_BIN}')"
        )
    _run(
        [
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-i", input_path,
            "-t", str(max_duration_s),
            "-c", "copy",
            output_path,
        ],
        timeout=120,
    )


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Standalone video grade/trim/stabilize worker. Not wired into the "
            "live Bricks-Dispatch pipeline -- see this module's docstring."
        )
    )
    parser.add_argument("input", help="source video path")
    parser.add_argument("output", help="destination mp4 path")
    parser.add_argument("--max-duration", type=float, default=None, help="trim to N seconds")
    parser.add_argument("--stabilize", action="store_true", help="apply two-pass vidstab if available")
    parser.add_argument("--crf", type=int, default=_DEFAULT_CRF)
    parser.add_argument("--preset", default=_DEFAULT_PRESET)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = process_video(
        args.input,
        args.output,
        max_duration_s=args.max_duration,
        stabilize=args.stabilize,
        crf=args.crf,
        preset=args.preset,
    )
    for w in result.warnings:
        print(f"WARNING: {w}")
    print(
        f"done: {result.output_path} "
        f"(graded={result.graded} stabilized={result.stabilized} trimmed={result.trimmed} "
        f"source_duration_s={result.source_duration_s} output_duration_s={result.output_duration_s})"
    )


if __name__ == "__main__":
    _cli()
