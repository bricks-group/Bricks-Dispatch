"""Autoenhance.ai v3 HDR brackets — sync enhance for Dispatch leftover delivery.

Kam chose Autoenhance as the Dispatch post-prod path (client absorbs download cost).
mls_safe preferences: window pull ON, sky replacement OFF (AS_SHOT).

Returns JPEG bytes on success. Raises AutoenhanceError subclasses on failure so
callers hold the stills for a re-run (Kam 2026-11-07: no in-house fallback
unless POSTPROD_STILLS_PROVIDER=inhouse is set explicitly).
"""
from __future__ import annotations

import logging
import os
import time
from typing import List, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

BASE = "https://api.autoenhance.ai/v3"
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 8 * 60  # serverless-friendly upper bound
# After a No Plan / 401-403 on full-res, skip AE on this instance for a short
# while (AUTOENHANCE_PLAN_BLOCK_TTL_SEC, default 300s). It expires on its own so
# a plan bought later is picked up without a redeploy/restart (Kam 2026-11-07).
_plan_blocked_until: float = 0.0
_plan_blocked_reason: str = ""


def _plan_block_ttl_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("AUTOENHANCE_PLAN_BLOCK_TTL_SEC") or 300))
    except ValueError:
        return 300.0


def plan_blocked() -> bool:
    return time.monotonic() < _plan_blocked_until


def clear_plan_block() -> None:
    global _plan_blocked_until, _plan_blocked_reason
    _plan_blocked_until = 0.0
    _plan_blocked_reason = ""


def paid_disabled() -> bool:
    return (os.environ.get("PAID_STILLS_PROCESSING_DISABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def unavailable_reason() -> str:
    """Why is_available() is False ('' when it is available)."""
    if paid_disabled():
        return "PAID_STILLS_PROCESSING_DISABLED is on"
    if not api_key():
        return "AUTOENHANCE_API_KEY not set"
    if plan_blocked():
        return f"Autoenhance plan blocked: {_plan_blocked_reason or 'no plan / credits'}"
    return ""


class AutoenhanceError(Exception):
    """Base Autoenhance failure."""


class AutoenhanceNoPlan(AutoenhanceError):
    """Full-res download blocked — account has no active Pay Per Image / plan."""


class AutoenhanceNotConfigured(AutoenhanceError):
    """AUTOENHANCE_API_KEY missing."""


def api_key() -> str:
    return (os.environ.get("AUTOENHANCE_API_KEY") or "").strip()


def is_available() -> bool:
    # PAID_STILLS_PROCESSING_DISABLED kill switch (default off → unchanged).
    return not unavailable_reason()


def _headers(key: str, *, json_body: bool = False) -> dict:
    # Docs: x-api-key only. Bearer alongside causes HTTP 500.
    h = {"x-api-key": key}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _mark_plan_blocked(reason: str) -> None:
    global _plan_blocked_until, _plan_blocked_reason
    _plan_blocked_until = time.monotonic() + _plan_block_ttl_sec()
    _plan_blocked_reason = str(reason or "")[:200]
    logger.warning("Autoenhance plan blocked on this worker for %ss: %s", int(_plan_block_ttl_sec()), reason)


def enhance_brackets_sync(
    frames: Sequence[bytes],
    filenames: Optional[Sequence[str]] = None,
    *,
    apply_sky: bool = False,
    timeout_sec: float = POLL_TIMEOUT_SEC,
) -> Tuple[bytes, dict]:
    """HDR-merge + enhance via Autoenhance v3 (1+ frames; single still = 1 bracket).

    apply_sky must stay False for launch mls_safe (no invent sky/dusk).
    Twilight callers should not use this path.
    """
    if apply_sky:
        raise AutoenhanceError("sky_replacement forbidden on launch mls_safe path")
    if plan_blocked():
        raise AutoenhanceNoPlan(f"cached No Plan for this worker ({_plan_blocked_reason})")
    key = api_key()
    if not key:
        raise AutoenhanceNotConfigured("AUTOENHANCE_API_KEY not set")
    if len(frames) < 1:
        raise AutoenhanceError("need at least 1 frame")

    names = list(filenames or [])
    while len(names) < len(frames):
        i = len(names)
        names.append(f"bracket_{i}.jpg")

    with httpx.Client(timeout=60.0) as client:
        order_id = _create_order(client, key)
        for raw, name in zip(frames, names):
            _upload_bracket(client, key, order_id, name, raw)
        _process_order(client, key, order_id, n_brackets=len(frames))
        order = _poll_order(client, key, order_id, timeout_sec=timeout_sec)
        images = order.get("images") or []
        if not images:
            raise AutoenhanceError(f"order {order_id} finished with empty images[]")
        image_id = images[0].get("image_id") or images[0].get("id")
        if not image_id:
            raise AutoenhanceError(f"no image_id in order {order_id}")
        jpeg = _download_enhanced(client, key, image_id)
        meta = {
            "provider": "autoenhance",
            "order_id": order_id,
            "image_id": image_id,
            "window_pull_type": "ONLY_WINDOWS",
            "sky_replacement": False,
            "n_brackets": len(frames),
            "used_sky": False,
            "accept_claim": False,
        }
        return jpeg, meta


def _create_order(client: httpx.Client, key: str) -> str:
    r = client.post(f"{BASE}/orders/", headers=_headers(key, json_body=True), json={})
    if r.status_code not in (200, 201):
        _raise_http("create_order", r)
    data = r.json()
    order_id = data.get("order_id")
    if not order_id:
        raise AutoenhanceError(f"no order_id: {data!r}"[:400])
    return str(order_id)


def _upload_bracket(
    client: httpx.Client, key: str, order_id: str, name: str, raw: bytes
) -> None:
    r = client.post(
        f"{BASE}/brackets/",
        headers=_headers(key, json_body=True),
        json={"order_id": order_id, "name": name},
    )
    if r.status_code not in (200, 201):
        _raise_http("create_bracket", r)
    data = r.json()
    upload_url = data.get("upload_url") or data.get("s3PutObjectUrl")
    if not upload_url:
        raise AutoenhanceError(f"no upload_url for {name}")
    # Presigned S3 — do not send x-api-key (can cause InvalidArgument).
    put = client.put(
        upload_url,
        headers={"Content-Type": "application/octet-stream"},
        content=raw,
    )
    if put.status_code not in (200, 201, 204):
        _raise_http("upload_bracket", put)


def _process_order(
    client: httpx.Client, key: str, order_id: str, *, n_brackets: int
) -> None:
    preferences = {
        "ai_version": "5.x",
        "enhance": True,
        "enhance_type": "neutral",
        "number_of_brackets_per_image": int(n_brackets),
        "window_pull_type": "ONLY_WINDOWS",
        "sky_replacement": False,
        "restage": {"sky": "AS_SHOT"},
    }
    r = client.post(
        f"{BASE}/orders/{order_id}/process",
        headers=_headers(key, json_body=True),
        json=preferences,
    )
    if r.status_code not in (200, 201):
        _raise_http("process_order", r)


def _poll_order(
    client: httpx.Client, key: str, order_id: str, *, timeout_sec: float
) -> dict:
    deadline = time.time() + timeout_sec
    last: dict = {}
    url = f"{BASE}/orders/{order_id}"
    while time.time() < deadline:
        r = client.get(url, headers=_headers(key))
        if r.status_code != 200:
            _raise_http("poll_order", r)
        last = r.json()
        merging = bool(last.get("is_merging"))
        processing = bool(last.get("is_processing"))
        images = last.get("images") or []
        statuses = [img.get("status") for img in images]
        if any(s in ("error", "failed", "rejected") for s in statuses):
            raise AutoenhanceError(f"order image failed: {statuses}")
        if not merging and not processing and images:
            if all(
                s in ("processed", "enhanced", "completed", "done", None)
                for s in statuses
            ):
                # Prefer explicit done; allow None when flags quiet.
                if any(s in ("processed", "enhanced", "completed", "done") for s in statuses):
                    return last
                if all(s in (None, "waiting", "processing", "uploaded", "queued") for s in statuses):
                    pass
                else:
                    return last
        time.sleep(POLL_INTERVAL_SEC)
    raise AutoenhanceError(f"timed out waiting for order {order_id}")


def _download_enhanced(client: httpx.Client, key: str, image_id: str) -> bytes:
    url = f"{BASE}/images/{image_id}/enhanced?preview=false"
    r = client.get(url, headers=_headers(key), follow_redirects=True)
    if r.status_code in (401, 402, 403):
        # "The user has no plan" / No Plan — fail fast for this worker.
        detail = ""
        try:
            detail = str(r.json())
        except Exception:
            detail = r.text[:200]
        _mark_plan_blocked(detail or f"HTTP {r.status_code}")
        raise AutoenhanceNoPlan(detail or f"HTTP {r.status_code}")
    if r.status_code != 200:
        _raise_http("download_enhanced", r)

    ctype = (r.headers.get("Content-Type") or "").lower()
    body = r.content
    if "json" in ctype or (body[:1] in (b"{", b"[") and not body.startswith(b"\xff\xd8")):
        try:
            data = r.json()
        except Exception as e:
            raise AutoenhanceError(f"enhanced non-image: {e}") from e
        # Explicit no-plan shapes
        msg = str(data.get("message") or data.get("detail") or "").lower()
        if "no plan" in msg or "no_plan" in msg:
            _mark_plan_blocked(str(data))
            raise AutoenhanceNoPlan(str(data))
        dl = (
            data.get("url")
            or data.get("download_url")
            or data.get("enhanced_url")
            or data.get("image_url")
        )
        if not dl:
            raise AutoenhanceError(f"no download URL: {data!r}"[:400])
        r2 = client.get(dl, follow_redirects=True)
        if r2.status_code in (402, 403):
            _mark_plan_blocked(r2.text[:200])
            raise AutoenhanceNoPlan(r2.text[:200])
        if r2.status_code != 200 or not r2.content.startswith(b"\xff\xd8"):
            raise AutoenhanceError(f"signed download failed ({r2.status_code})")
        body = r2.content

    if not body.startswith(b"\xff\xd8"):
        raise AutoenhanceError("enhanced payload is not JPEG")
    return body


def _raise_http(step: str, r: httpx.Response) -> None:
    text = ""
    try:
        text = r.text[:400]
    except Exception:
        text = ""
    lower = text.lower()
    # 401 = bad/revoked key (Oct 4 batch); 402/403/No Plan = no credits/plan.
    # Either way AE will keep refusing — block for this worker, go in-house.
    if r.status_code in (401, 402, 403) or "no plan" in lower or "credit" in lower:
        _mark_plan_blocked(f"{step}: {text or r.status_code}")
        raise AutoenhanceNoPlan(f"{step}: {text or r.status_code}")
    raise AutoenhanceError(f"{step} failed HTTP {r.status_code}: {text}")
