"""Gallery preview media for the delivery email.

What lands in the email:
  * up to 5 QC-*passed* listing stills (never flagged, unreviewed, rejected,
    failed, bracket siblings, Visit assets, camera RAW, floor plans or video),
  * resized server-side (hero 1200x750, tiles 560x373, JPEG),
  * an optional crossfade GIF hero (<= 1 MB, first frame == the hero still),
  * served from Dispatch by a non-expiring HMAC URL. The route re-checks QC on
    every request, so a still rejected after delivery disappears from old
    emails instead of living on in a 7-day signed link.

Assets are built at delivery (prewarm, bounded) and cached in storage under
bricks-go/jobs/{job_id}/email/; a cache miss is rebuilt on first open.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import os
import re
from typing import Iterable, Optional
from urllib.parse import quote

from PIL import Image

logger = logging.getLogger(__name__)

HERO_SIZE = (1200, 750)
TILE_SIZE = (560, 373)
GIF_BUDGET_BYTES = 1_000_000
# (width, height, blend frames between stills) — first that fits the budget wins.
GIF_LADDER = ((600, 375, 2), (560, 350, 2), (560, 350, 1), (480, 300, 2), (480, 300, 1))
GIF_HOLD_MS = 2800
MAX_PREVIEW = 8            # candidates loaded (hero + 4 tiles + 2 GIF-only frames)
SHOWN_STILLS = 5
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_ASSET_RE = re.compile(r"^(?:(?P<uid>[A-Za-z0-9_-]{1,80})-(?P<kind>hero|tile)\.jpg|crossfade\.gif)$")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff")
_SKIP_KINDS = {"floorplan", "reel", "visit", "splat", "tour"}


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------

def _secret() -> bytes:
    raw = (os.environ.get("DELIVERY_EMAIL_MEDIA_SECRET") or "").strip()
    if raw:
        return raw.encode("utf-8")
    from .storage import _hmac_secret

    return _hmac_secret()


def sign_asset(job_id: str, asset: str, ids: str = "") -> str:
    msg = f"delivery-email:{job_id}:{asset}:{ids}".encode("utf-8")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:40]


def verify_asset(job_id: str, asset: str, ids: str, sig: str) -> bool:
    if not sig or not isinstance(sig, str):
        return False
    try:
        expected = sign_asset(job_id, asset, ids)
    except RuntimeError:
        return False
    return hmac.compare_digest(expected, sig.strip().lower())


def public_base() -> str:
    from .storage import _dispatch_public_base

    return _dispatch_public_base()


def asset_url(job_id: str, asset: str, ids: str = "", *, base: Optional[str] = None) -> str:
    sig = sign_asset(job_id, asset, ids)
    q = f"ids={quote(ids, safe=',')}&" if ids else ""
    return f"{(base or public_base()).rstrip('/')}/api/public/delivery-email/{quote(job_id)}/{asset}?{q}s={sig}"


def parse_asset(asset: str) -> Optional[tuple[str, str]]:
    """('hero'|'tile', upload_id) or ('gif', '') for valid asset names."""
    m = _ASSET_RE.match(asset or "")
    if not m:
        return None
    if asset == "crossfade.gif":
        return "gif", ""
    return m.group("kind"), m.group("uid")


def parse_ids(ids: str) -> list[str]:
    out = [i for i in (ids or "").split(",") if i]
    if not out or len(out) > 4 or not all(_ID_RE.match(i) for i in out):
        return []
    return out


# ---------------------------------------------------------------------------
# Eligibility (QC holds)
# ---------------------------------------------------------------------------

def preview_candidate_ids(delivery_urls: Iterable | None) -> list[str]:
    """Upload ids of listing stills from media_delivery rows, in delivery order."""
    out: list[str] = []
    for u in delivery_urls or []:
        if not isinstance(u, dict):
            continue
        uid = str(u.get("upload_id") or "").strip()
        if not uid or uid in out or not _ID_RE.match(uid):
            continue
        if u.get("qc_flagged"):
            continue
        if str(u.get("kind") or "").lower() in _SKIP_KINDS:
            continue
        out.append(uid)
    return out


def is_preview_eligible(upload: dict | None) -> bool:
    """Strictest gate in the app: QC passed, done, a listing JPEG-able still."""
    from .media_intake import infer_media_type, is_listing_still_delivery_row, is_pdf_file
    from .raw_decode import is_camera_raw_name

    u = upload if isinstance(upload, dict) else {}
    if str(u.get("qc_status") or "").strip().lower() != "passed":
        return False
    if str(u.get("processing_status") or "").lower() in ("failed", "processing", "pending"):
        return False
    if not is_listing_still_delivery_row(u):
        return False
    path = str(u.get("processed_url") or u.get("file_url") or "")
    if not path or is_camera_raw_name(path):
        return False
    name = str(u.get("filename") or "")
    if is_pdf_file(name, u.get("mime_type")):
        return False
    mt = infer_media_type(name, u.get("mime_type"), u.get("media_type"), path)
    if mt in ("floorplan", "reel"):
        return False
    base = path.split("?", 1)[0].lower()
    return base.endswith(_IMAGE_EXTS) or "." not in base.rsplit("/", 1)[-1]


_UPLOAD_COLS = "id, job_id, filename, file_url, processed_url, processing_status, qc_status, provider"


async def load_eligible_uploads(job_id: str, ids: list[str]) -> list[dict]:
    """Fresh DB rows for ids (same job), filtered by is_preview_eligible, in ids order."""
    if not job_id or not ids:
        return []
    rows: list[dict] = []
    try:
        from ..shared.database import get_db

        db = await get_db()
        res = await db.table("uploads").select(_UPLOAD_COLS).eq("job_id", job_id).in_("id", ids).execute()
        rows = res.data or []
    except Exception as e:  # noqa: BLE001
        try:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_uploads_by_job_id

            if not _is_rest_quota_error(e):
                raise
            rows = [r for r in pg_select_uploads_by_job_id(job_id) if str(r.get("id")) in ids]
        except Exception as e2:  # noqa: BLE001
            logger.warning("delivery email: uploads load failed job=%s: %s", job_id, type(e2).__name__)
            return []
    by_id = {str(r.get("id")): r for r in rows if str(r.get("job_id") or job_id) == str(job_id)}
    return [by_id[i] for i in ids if i in by_id and is_preview_eligible(by_id[i])]


async def job_is_delivered(job_id: str) -> bool:
    try:
        from ..shared.database import get_db

        db = await get_db()
        res = await db.table("media_delivery").select("id").eq("job_id", job_id).limit(1).execute()
        return bool(res.data)
    except Exception as e:  # noqa: BLE001
        try:
            from ..shared.pg_fallback import _is_rest_quota_error, pg_select_media_delivery_by_job_id

            if _is_rest_quota_error(e):
                return bool(pg_select_media_delivery_by_job_id(job_id))
        except Exception:  # noqa: BLE001
            pass
        logger.warning("delivery email: media_delivery check failed job=%s: %s", job_id, type(e).__name__)
        return False


# ---------------------------------------------------------------------------
# Image building
# ---------------------------------------------------------------------------

def _open(image_bytes: bytes) -> Image.Image:
    from .image_io import open_rgb

    img = open_rgb(image_bytes)
    return img.convert("RGB") if img.mode != "RGB" else img


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    scale = max(w / img.width, h / img.height)
    rw, rh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
    if (img.width, img.height) != (rw, rh):
        # draft()-style two-step: cheap reduce, then Lanczos.
        if img.width > rw * 2:
            img = img.reduce(max(1, img.width // (rw * 2)))
            scale = max(w / img.width, h / img.height)
            rw, rh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
        img = img.resize((rw, rh), Image.LANCZOS)
    left, top = (rw - w) // 2, (rh - h) // 2
    return img.crop((left, top, left + w, top + h))


def make_jpeg(image_bytes: bytes, size: tuple[int, int], quality: int = 80) -> bytes:
    out = _cover(_open(image_bytes), size)
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def make_crossfade_gif(sources: list[bytes], budget: int = GIF_BUDGET_BYTES) -> Optional[bytes]:
    """Looping crossfade of 2–3 stills; first frame is sources[0] untouched.

    One shared palette (from the key stills) keeps blends from flickering.
    Returns None when nothing on the ladder fits the budget.
    """
    if len(sources) < 2:
        return None
    opened = [_open(b) for b in sources[:3]]
    for w, h, steps in GIF_LADDER:
        stills = [_cover(im, (w, h)) for im in opened]
        frames: list[Image.Image] = []
        durs: list[int] = []
        blend_ms = 110 if steps >= 2 else 180
        for i, a in enumerate(stills):
            b = stills[(i + 1) % len(stills)]
            frames.append(a)
            durs.append(GIF_HOLD_MS)
            for k in range(1, steps + 1):
                frames.append(Image.blend(a, b, k / (steps + 1)))
                durs.append(blend_ms)
        sheet = Image.new("RGB", (w, h * len(stills)))
        for i, a in enumerate(stills):
            sheet.paste(a, (0, h * i))
        pal = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
        q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
        buf = io.BytesIO()
        q[0].save(buf, format="GIF", save_all=True, append_images=q[1:], duration=durs, loop=0, optimize=True)
        data = buf.getvalue()
        if len(data) <= budget:
            return data
    return None


async def _source_bytes(upload: dict) -> Optional[bytes]:
    path = str(upload.get("processed_url") or upload.get("file_url") or "")
    if not path:
        return None
    if path.startswith("https://"):
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(path)
                r.raise_for_status()
                return r.content
        except Exception as e:  # noqa: BLE001
            logger.warning("delivery email: source fetch failed upload=%s: %s", upload.get("id"), type(e).__name__)
            return None
    from .storage import download_bytes

    return await download_bytes(path)


def cache_path(job_id: str, asset: str, ids: str = "") -> str:
    if asset == "crossfade.gif":
        h = hashlib.sha256(ids.encode("utf-8")).hexdigest()[:12]
        asset = f"crossfade-{h}.gif"
    return f"bricks-go/jobs/{job_id}/email/{asset}"


async def build_asset(job_id: str, asset: str, ids: str, uploads_by_id: dict[str, dict]) -> Optional[bytes]:
    """Build (or read cached) bytes for an already-authorised asset."""
    from .storage import download_bytes, upload_bytes

    parsed = parse_asset(asset)
    if not parsed:
        return None
    kind, uid = parsed
    path = cache_path(job_id, asset, ids)
    try:
        cached = await download_bytes(path)
    except Exception:  # noqa: BLE001
        cached = None
    if cached:
        return cached
    data: Optional[bytes] = None
    if kind in ("hero", "tile"):
        u = uploads_by_id.get(uid)
        src = await _source_bytes(u) if u else None
        if src:
            size = HERO_SIZE if kind == "hero" else TILE_SIZE
            data = await asyncio.to_thread(make_jpeg, src, size, 82 if kind == "hero" else 80)
    else:
        rows = [uploads_by_id[i] for i in parse_ids(ids) if i in uploads_by_id]
        srcs = [b for b in await asyncio.gather(*(_source_bytes(r) for r in rows)) if b]
        if len(srcs) >= 2:
            data = await asyncio.to_thread(make_crossfade_gif, srcs)
    if data:
        ctype = "image/gif" if kind == "gif" else "image/jpeg"
        try:
            await upload_bytes(path, data, content_type=ctype, upsert=True)
        except Exception as e:  # noqa: BLE001 - serve uncached
            logger.warning("delivery email: cache write failed %s: %s", path, type(e).__name__)
    return data


# ---------------------------------------------------------------------------
# What the email gets
# ---------------------------------------------------------------------------

async def gallery_preview(
    job_id: str,
    delivery_urls: list | None,
    *,
    address: str = "",
    prewarm: bool = True,
    timeout: float = 25.0,
) -> dict:
    """{"stills": [PreviewStill], "hero_gif_url": str, "total": int}.

    Never raises — the email goes out without a gallery rather than not at all.
    """
    from .delivery_email import PreviewStill

    empty = {"stills": [], "hero_gif_url": "", "total": 0}
    try:
        ids = preview_candidate_ids(delivery_urls)
        if not ids:
            return empty
        rows = await load_eligible_uploads(job_id, ids)
        total = len(rows)
        pick = rows[:MAX_PREVIEW]
        if not pick:
            return empty
        by_id = {str(r["id"]): r for r in pick}
        shown = pick[:SHOWN_STILLS]
        label = (address or "").strip()
        stills = [
            PreviewStill(
                tile_url=asset_url(job_id, f"{r['id']}-tile.jpg"),
                hero_url=asset_url(job_id, f"{r['id']}-hero.jpg") if i == 0 else "",
                alt=f"Photo {i + 1} of {total}" + (f", {label}" if label else ""),
            )
            for i, r in enumerate(shown)
        ]
        gif_ids: list[str] = []
        if len(pick) >= 2:
            extras = [str(r["id"]) for r in pick[SHOWN_STILLS:]]
            fill = [str(r["id"]) for r in shown[1:]]
            gif_ids = [str(shown[0]["id"])] + (extras + fill)[:2]
        gif_url = ""
        if prewarm:
            jobs = [build_asset(job_id, f"{shown[0]['id']}-hero.jpg", "", by_id)]
            jobs += [build_asset(job_id, f"{r['id']}-tile.jpg", "", by_id) for r in shown[1:]]
            gif_job = build_asset(job_id, "crossfade.gif", ",".join(gif_ids), by_id) if gif_ids else None
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*(jobs + ([gif_job] if gif_job else [])), return_exceptions=True),
                    timeout=timeout,
                )
                if gif_job and results and isinstance(results[-1], (bytes, bytearray)) and results[-1]:
                    gif_url = asset_url(job_id, "crossfade.gif", ",".join(gif_ids))
            except asyncio.TimeoutError:
                logger.info("delivery email: prewarm timed out job=%s (assets build on first open)", job_id)
        return {"stills": stills, "hero_gif_url": gif_url, "total": total}
    except Exception as e:  # noqa: BLE001
        logger.warning("delivery email: gallery preview failed job=%s: %s", job_id, type(e).__name__)
        return empty


# ---------------------------------------------------------------------------
# Services line
# ---------------------------------------------------------------------------

_VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")


def services_from_delivery(delivery_urls: list | None, products: Iterable[str] = (), *, photo_count: int = 0) -> list[str]:
    """Ordered, sentence-case labels for the "Included" row."""
    kinds: set[str] = set()
    video = False
    stills = 0
    for u in delivery_urls or []:
        if not isinstance(u, dict):
            continue
        k = str(u.get("kind") or "").lower()
        name = str(u.get("filename") or u.get("url") or "").lower().split("?", 1)[0]
        mime = str(u.get("mime_type") or "").lower()
        if k == "reel" or mime.startswith("video/") or name.endswith(_VIDEO_EXTS):
            video = True
            continue
        if k:
            kinds.add(k)
        if u.get("upload_id") and k not in _SKIP_KINDS:
            stills += 1
    for p in products or []:
        pl = str(p).strip().lower()
        if "visit" in pl:
            kinds.add("visit")
        elif "floor" in pl:
            kinds.add("floorplan")
        elif "tour" in pl:
            kinds.add("splat")
        elif "video" in pl or "reel" in pl:
            video = True
    n = photo_count or stills
    out: list[str] = []
    if n:
        out.append(f"{n} photo" + ("" if n == 1 else "s"))
    if "drone" in kinds:
        out.append("Drone")
    if "twilight" in kinds:
        out.append("Twilight")
    if "staging" in kinds:
        out.append("Virtual staging")
    if video:
        out.append("Video")
    if "visit" in kinds:
        out.append("Virtual Visit")
    if "splat" in kinds or "tour" in kinds:
        out.append("3D tour")
    if "floorplan" in kinds:
        out.append("Floor plan")
    return out
