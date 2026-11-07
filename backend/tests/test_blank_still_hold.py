"""A blank (one flat tone) listing still is held, not HDR-edited and delivered."""
from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image

from app.services import image_io
from app.services import post_production as pp
from tests.test_equirect_360_hold import _deliver, _install


def _jpeg_from(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype("uint8"), "RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _flat(level: int, noise: int = 0, w: int = 1600, h: int = 1200) -> bytes:
    rng = np.random.default_rng(7)
    arr = np.full((h, w, 3), level, dtype=np.int16)
    if noise:
        arr = arr + rng.integers(-noise, noise + 1, size=arr.shape)
    return _jpeg_from(np.clip(arr, 0, 255))


def _scene(w: int = 1600, h: int = 1200) -> bytes:
    return _jpeg_from(np.frombuffer(os.urandom(w * h * 3), dtype=np.uint8).reshape(h, w, 3))


def _dark_with_window(w: int = 1600, h: int = 1200) -> bytes:
    # Under-exposed interior: mostly near-black, but a bright window remains.
    arr = np.full((h, w, 3), 8, dtype=np.uint8)
    arr[200:500, 600:1000] = 230
    return _jpeg_from(arr)


def test_detects_lens_cap_white_and_grey():
    assert image_io.is_blank_still(_flat(2, noise=4))  # lens cap with sensor noise
    assert image_io.is_blank_still(_flat(255))  # blown white
    assert image_io.is_blank_still(_flat(128, noise=2))  # grey placeholder


def test_real_and_dark_scenes_not_flagged():
    assert not image_io.is_blank_still(_scene())
    assert not image_io.is_blank_still(_dark_with_window())


def test_unreadable_not_flagged():
    assert not image_io.is_blank_still(b"")
    assert not image_io.is_blank_still(None)
    assert not image_io.is_blank_still(b"not an image")


def test_vendor_photo_blank_frame_held(monkeypatch):
    updates, calls = _install(monkeypatch, _flat(1, noise=3))
    _deliver({"id": "bl1", "file_url": "raw/IMG_0001.jpg", "filename": "IMG_0001.jpg", "mime_type": "image/jpeg"})
    (uid, upd), = updates
    assert uid == "bl1"
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.BLANK_QC_NOTE
    assert upd["processed_url"] == "raw/IMG_0001.jpg"
    assert upd["provider"] == "as_is"
    assert calls == {"enhance": 0, "export": 0}


def test_real_still_still_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _scene())
    _deliver({"id": "bl2", "file_url": "raw/room.jpg", "filename": "room.jpg", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.BLANK_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
