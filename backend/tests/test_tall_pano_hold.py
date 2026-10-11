"""Tall vertical panoramas (≥1:2.5) are held uncropped, not landscape-4:3 cropped to a thin band."""
from __future__ import annotations

from app.services import image_io
from app.services import post_production as pp
from tests.test_equirect_360_hold import _deliver, _install, _jpeg
from tests.test_wide_pano_hold import _jpeg_oriented


def test_detects_vertical_panos():
    assert image_io.is_tall_panorama(_jpeg(1000, 3000))  # 1:3 vertical phone pano
    assert image_io.is_tall_panorama(_jpeg(1000, 2500))  # boundary
    assert image_io.is_tall_panorama(_jpeg(1200, 5000))  # vertical drone pano


def test_ordinary_portrait_not_tall_pano():
    assert not image_io.is_tall_panorama(_jpeg(3000, 4000))  # 3:4
    assert not image_io.is_tall_panorama(_jpeg(4000, 6000))  # 2:3
    assert not image_io.is_tall_panorama(_jpeg(2250, 4000))  # 9:16
    assert not image_io.is_tall_panorama(_jpeg(3000, 1000))  # wide pano (other hold)
    assert not image_io.is_tall_panorama(b"")
    assert not image_io.is_tall_panorama(b"not an image")


def test_exif_rotation_uses_displayed_shape():
    assert image_io.is_tall_panorama(_jpeg_oriented(3000, 1000, 6))
    assert not image_io.is_tall_panorama(_jpeg_oriented(1000, 3000, 8))


def test_vendor_photo_tall_pano_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(1000, 3000))
    _deliver({"id": "t1", "file_url": "raw/PANO_V.jpg", "filename": "PANO_V.jpg", "mime_type": "image/jpeg"})
    (uid, upd), = updates
    assert uid == "t1"
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.TALL_PANO_QC_NOTE
    assert upd["processed_url"] == "raw/PANO_V.jpg"
    assert upd["provider"] == "as_is"
    assert calls == {"enhance": 0, "export": 0}


def test_portrait_still_still_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(1500, 2000))
    _deliver({"id": "t2", "file_url": "raw/IMG_P.jpg", "filename": "IMG_P.jpg", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.TALL_PANO_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
