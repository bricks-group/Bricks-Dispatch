"""Thumbnail / screenshot-size stills are held, never HDR-edited and delivered to the listing."""
from __future__ import annotations

from app.services import image_io
from app.services import post_production as pp
from tests.test_equirect_360_hold import GPANO_EQ, _deliver, _install, _jpeg


def test_detects_low_res():
    assert image_io.is_low_resolution_still(_jpeg(640, 480))
    assert image_io.is_low_resolution_still(_jpeg(1023, 768))
    assert image_io.is_low_resolution_still(_jpeg(600, 1000))  # portrait thumb


def test_listing_grade_not_low_res():
    assert not image_io.is_low_resolution_still(_jpeg(1024, 768))  # boundary
    assert not image_io.is_low_resolution_still(_jpeg(4000, 3000))
    assert not image_io.is_low_resolution_still(_jpeg(1600, 900))
    assert not image_io.is_low_resolution_still(b"")
    assert not image_io.is_low_resolution_still(b"not an image")


def _assert_low_res_held(updates, calls, uid, raw):
    (got_uid, upd), = updates
    assert got_uid == uid
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.LOW_RES_QC_NOTE
    assert upd["processed_url"] == raw
    assert upd["processing_status"] == "done"
    assert upd["provider"] == "as_is"
    assert calls == {"enhance": 0, "export": 0}


def test_vendor_photo_thumbnail_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(640, 480))
    _deliver({"id": "l1", "file_url": "raw/IMG_9.jpg", "filename": "IMG_9.jpg", "mime_type": "image/jpeg"})
    _assert_low_res_held(updates, calls, "l1", "raw/IMG_9.jpg")


def test_twilight_thumbnail_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(800, 600))
    _deliver({"id": "l2", "file_url": "raw/twilight_front.jpg", "filename": "twilight_front.jpg", "mime_type": "image/jpeg"})
    _assert_low_res_held(updates, calls, "l2", "raw/twilight_front.jpg")


def test_drone_thumbnail_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(960, 540))
    _deliver({"id": "l3", "file_url": "raw/DJI_0042.JPG", "filename": "DJI_0042.JPG", "mime_type": "image/jpeg"})
    _assert_low_res_held(updates, calls, "l3", "raw/DJI_0042.JPG")


def test_small_360_still_routes_as_360(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(800, 400, GPANO_EQ))
    _deliver({"id": "l4", "file_url": "raw/R0010012.JPG", "filename": "R0010012.JPG", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["qc_notes"] == pp.EQUIRECT_360_QC_NOTE


def test_full_size_still_still_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(1600, 1200))
    _deliver({"id": "l5", "file_url": "raw/IMG_10.jpg", "filename": "IMG_10.jpg", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.LOW_RES_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
