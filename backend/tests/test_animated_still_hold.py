"""Animated GIF / APNG / WebP uploaded as a listing still is held, not frame-0 delivered."""
from __future__ import annotations

import io
import os

from PIL import Image

from app.services import image_io
from app.services import post_production as pp
from tests.test_equirect_360_hold import _deliver, _install


def _frames(n: int = 3, w: int = 1600, h: int = 1200):
    return [Image.frombytes("RGB", (w, h), os.urandom(w * h * 3)) for _ in range(n)]


def _anim(fmt: str, n: int = 3) -> bytes:
    fr = _frames(n)
    buf = io.BytesIO()
    if n > 1:
        fr[0].save(buf, format=fmt, save_all=True, append_images=fr[1:], duration=100, loop=0)
    else:
        fr[0].save(buf, format=fmt)
    return buf.getvalue()


def _jpeg() -> bytes:
    buf = io.BytesIO()
    _frames(1)[0].save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def test_detects_animated_gif_apng_webp():
    for fmt in ("GIF", "PNG", "WEBP"):
        assert image_io.is_animated_still(_anim(fmt)), fmt
        assert not image_io.is_animated_still(_anim(fmt, 1)), fmt


def test_jpeg_and_unreadable_not_flagged():
    assert not image_io.is_animated_still(_jpeg())
    assert not image_io.is_animated_still(b"")
    assert not image_io.is_animated_still(None)
    assert not image_io.is_animated_still(b"not an image")


def test_multi_image_tiff_not_flagged():
    # Camera/scanner TIFFs may carry a reduced-res second IFD — not animation.
    fr = _frames(2, 1200, 900)
    buf = io.BytesIO()
    fr[0].save(buf, format="TIFF", save_all=True, append_images=fr[1:])
    assert not image_io.is_animated_still(buf.getvalue())


def test_vendor_photo_animated_gif_held(monkeypatch):
    updates, calls = _install(monkeypatch, _anim("GIF"))
    _deliver({"id": "an1", "file_url": "raw/clip.gif", "filename": "clip.gif", "mime_type": "image/gif"})
    (uid, upd), = updates
    assert uid == "an1"
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.ANIMATED_QC_NOTE
    assert upd["processed_url"] == "raw/clip.gif"
    assert upd["provider"] == "as_is"
    assert calls == {"enhance": 0, "export": 0}


def test_single_frame_png_still_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _anim("PNG", 1))
    _deliver({"id": "an2", "file_url": "raw/room.png", "filename": "room.png", "mime_type": "image/png"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.ANIMATED_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
