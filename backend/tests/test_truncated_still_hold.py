"""Listing stills cut off mid-upload are held, not edited and delivered with a grey band."""
from __future__ import annotations

import io
import os

from PIL import Image

from app.services import image_io
from app.services import post_production as pp
from tests.test_equirect_360_hold import _deliver, _install


def _noisy(fmt: str = "JPEG", w: int = 2000, h: int = 1500) -> bytes:
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": 80} if fmt == "JPEG" else {}))
    return buf.getvalue()


def test_detects_cut_off_jpeg_and_png():
    full = _noisy()
    assert not image_io.is_truncated_still(full)
    assert image_io.is_truncated_still(full[: int(len(full) * 0.6)])
    png = _noisy("PNG", 1200, 900)
    assert not image_io.is_truncated_still(png)
    assert image_io.is_truncated_still(png[: int(len(png) * 0.6)])


def test_motion_photo_trailer_not_truncated():
    # Samsung/Pixel motion photos append an MP4 after the JPEG EOI marker.
    assert not image_io.is_truncated_still(_noisy() + b"\x00\x00\x00\x18ftypmp42" + os.urandom(200_000))


def test_unreadable_or_empty_not_flagged():
    assert not image_io.is_truncated_still(b"")
    assert not image_io.is_truncated_still(None)
    assert not image_io.is_truncated_still(b"not an image")


def test_global_truncation_flag_restored():
    from PIL import ImageFile

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    try:
        full = _noisy()
        assert image_io.is_truncated_still(full[: len(full) // 2])
        assert ImageFile.LOAD_TRUNCATED_IMAGES is True
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = False


def test_vendor_photo_truncated_held(monkeypatch):
    full = _noisy()
    updates, calls = _install(monkeypatch, full[: int(len(full) * 0.6)])
    _deliver({"id": "tr1", "file_url": "raw/IMG_9001.jpg", "filename": "IMG_9001.jpg", "mime_type": "image/jpeg"})
    (uid, upd), = updates
    assert uid == "tr1"
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.TRUNCATED_QC_NOTE
    assert upd["processed_url"] == "raw/IMG_9001.jpg"
    assert upd["provider"] == "as_is"
    assert calls == {"enhance": 0, "export": 0}


def test_complete_still_still_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _noisy())
    _deliver({"id": "tr2", "file_url": "raw/IMG_9002.jpg", "filename": "IMG_9002.jpg", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.TRUNCATED_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
