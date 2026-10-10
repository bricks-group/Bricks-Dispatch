"""360° equirect spheres uploaded as stills are held uncropped, never 4:3-cropped onto the listing."""
from __future__ import annotations

import asyncio
import io
import sys
from types import ModuleType
from unittest.mock import MagicMock

from PIL import Image

from app.services import image_io
from app.services import post_production as pp


def _jpeg(w, h, xmp: bytes = b"") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 130, 140)).save(buf, format="JPEG", quality=70)
    data = buf.getvalue()
    if xmp:
        # Splice an APP1 XMP segment after SOI (header-level, like real cameras).
        payload = b"http://ns.adobe.com/xap/1.0/\x00" + xmp
        seg = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
        data = data[:2] + seg + data[2:]
    return data


GPANO_EQ = b'<x:xmpmeta><rdf:Description GPano:ProjectionType="equirectangular"/></x:xmpmeta>'
GPANO_CYL = b'<x:xmpmeta><rdf:Description GPano:ProjectionType="cylindrical"/></x:xmpmeta>'


def test_detects_gpano_equirect_even_small():
    assert image_io.is_equirect_360(_jpeg(800, 400, GPANO_EQ))


def test_detects_xmpless_2to1_sphere_at_4k():
    assert image_io.is_equirect_360(_jpeg(5760, 2880))


def test_ordinary_and_phone_pano_stills_not_equirect():
    assert not image_io.is_equirect_360(_jpeg(4000, 3000))  # 4:3 listing still
    assert not image_io.is_equirect_360(_jpeg(6000, 3376))  # 16:9
    assert not image_io.is_equirect_360(_jpeg(1600, 800))  # small 2:1 crop, no XMP
    assert not image_io.is_equirect_360(_jpeg(8000, 4000, GPANO_CYL))  # explicit cylindrical
    assert not image_io.is_equirect_360(b"")
    assert not image_io.is_equirect_360(b"not an image")


def _install(monkeypatch, src: bytes):
    updates, calls = [], {"enhance": 0, "export": 0}

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return src

    async def fake_up(_storage, _job, uid, _data):
        return f"processed/{uid}.jpg"

    async def fake_qc(*_a, **_k):
        return None

    def fake_enhance(*_a, **_k):
        calls["enhance"] += 1
        return b"\xff\xd8edited", {}

    def fake_export(b):
        calls["export"] += 1
        return b, {"export": {}}

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = fake_enhance
    fake_pes.enhance_image = fake_enhance
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = fake_export
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)
    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    return updates, calls


def _deliver(asset):
    asyncio.run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-360", asset))


def _assert_held(updates, calls, uid, raw):
    (got_uid, upd), = updates
    assert got_uid == uid
    assert upd["qc_status"] == "rejected"
    assert upd["qc_notes"] == pp.EQUIRECT_360_QC_NOTE
    assert upd["processed_url"] == raw  # uncropped original, not an edit
    assert upd["processing_status"] == "done"
    assert upd["provider"] != "original"  # not re-stamped as an auto-edit failure
    assert calls == {"enhance": 0, "export": 0}


def test_vendor_photo_sphere_held_not_enhanced_or_cropped(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(800, 400, GPANO_EQ))
    _deliver({"id": "u1", "file_url": "raw/R0010012.JPG", "filename": "R0010012.JPG", "mime_type": "image/jpeg"})
    _assert_held(updates, calls, "u1", "raw/R0010012.JPG")


def test_drone_sphere_held_not_listing_exported(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(800, 400, GPANO_EQ))
    _deliver({"id": "u2", "file_url": "raw/DJI_0042.JPG", "filename": "DJI_0042.JPG", "mime_type": "image/jpeg"})
    _assert_held(updates, calls, "u2", "raw/DJI_0042.JPG")


def test_twilight_sphere_held(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(800, 400, GPANO_EQ))
    _deliver({"id": "u3", "file_url": "raw/twilight_pano.jpg", "filename": "twilight_pano.jpg", "mime_type": "image/jpeg"})
    _assert_held(updates, calls, "u3", "raw/twilight_pano.jpg")


def test_ordinary_still_still_auto_edited(monkeypatch):
    updates, calls = _install(monkeypatch, _jpeg(1600, 1200))
    _deliver({"id": "u4", "file_url": "raw/IMG_1.jpg", "filename": "IMG_1.jpg", "mime_type": "image/jpeg"})
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd.get("qc_notes") != pp.EQUIRECT_360_QC_NOTE
    assert calls["enhance"] == 1 and calls["export"] == 1
