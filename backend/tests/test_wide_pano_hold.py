"""Wide stitched panoramas (≥2.5:1) are held uncropped, not 4:3 centre-cropped onto the listing."""
from __future__ import annotations

import io

from PIL import Image

from app.services import image_io
from app.services import post_production as pp
from tests.test_equirect_360_hold import _deliver, _install, _jpeg


def _jpeg_oriented(w, h, orientation: int) -> bytes:
    buf = io.BytesIO()
    img = Image.linear_gradient("L").resize((w, h)).convert("RGB")  # not flat (blank hold)
    exif = Image.Exif()
    exif[0x0112] = orientation
    img.save(buf, format="JPEG", quality=70, exif=exif.tobytes())
    return buf.getvalue()


def test_detects_phone_and_drone_panos():
    assert image_io.is_wide_panorama(_jpeg(3000, 1000))  # 3:1 phone pano
    assert image_io.is_wide_panorama(_jpeg(6000, 1000))  # 6:1 phone pano
    assert image_io.is_wide_panorama(_jpeg(2500, 1000))  # boundary


def test_ordinary_frames_not_wide_pano():
    assert not image_io.is_wide_panorama(_jpeg(4000, 3000))  # 4:3
    assert not image_io.is_wide_panorama(_jpeg(6000, 4000))  # 3:2
    assert not image_io.is_wide_panorama(_jpeg(6000, 3376))  # 16:9
    assert not image_io.is_wide_panorama(_jpeg(1600, 800))  # 2:1
    assert not image_io.is_wide_panorama(_jpeg(2100, 900))  # 21:9
    assert not image_io.is_wide_panorama(_jpeg(1000, 3000))  # tall portrait
    assert not image_io.is_wide_panorama(b"")
    assert not image_io.is_wide_panorama(b"not an image")


def test_exif_rotation_uses_displayed_shape():
    # Stored 1000x3000 but EXIF 6 rotates it to a 3000x1000 displayed pano.
    assert image_io.is_wide_panorama(_jpeg_oriented(1000, 3000, 6))
    # Stored 3000x1000 rotated to portrait for display — not a pano.
    assert not image_io.is_wide_panorama(_jpeg_oriented(3000, 1000, 8))


def _assert_pano_held(updates, calls, uid, raw):
    (got_uid, upd), = updates
    assert got_uid == uid
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.WIDE_PANO_QC_NOTE
    assert upd["processed_url"] == raw
    assert upd["processing_status"] == "done"
    assert upd["provider"] == "as_is"
    assert calls == {"enhance": 0, "export": 0}


def test_vendor_photo_pano_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(3000, 1000))
    _deliver({"id": "p1", "file_url": "raw/PANO_1.jpg", "filename": "PANO_1.jpg", "mime_type": "image/jpeg"})
    _assert_pano_held(updates, calls, "p1", "raw/PANO_1.jpg")


def test_drone_pano_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(4000, 1200))
    _deliver({"id": "p2", "file_url": "raw/DJI_0101.JPG", "filename": "DJI_0101.JPG", "mime_type": "image/jpeg"})
    _assert_pano_held(updates, calls, "p2", "raw/DJI_0101.JPG")


def test_twilight_pano_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(3000, 1000))
    _deliver({"id": "p3", "file_url": "raw/twilight_wide.jpg", "filename": "twilight_wide.jpg", "mime_type": "image/jpeg"})
    _assert_pano_held(updates, calls, "p3", "raw/twilight_wide.jpg")


def test_sixteen_nine_still_still_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(1600, 900))
    _deliver({"id": "p4", "file_url": "raw/IMG_2.jpg", "filename": "IMG_2.jpg", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.WIDE_PANO_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
