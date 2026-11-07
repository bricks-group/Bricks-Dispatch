"""QC must review the still the client receives, not the intake raw variant.

Intake stores qc_variant_url = downsized vendor raw. QC prefers that variant.
When post-prod produced a new edit/export but no fresh variant, the stale raw
variant used to stay and QC scored the unedited source.
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock

from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch, *, qc_variant_ok, single=None, bracket=None):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(_storage, _job, uid, _data):
        return f"processed/{uid}.jpg"

    async def fake_qc(_storage, _job, uid, _data):
        return f"enhanced/qc_{uid}.jpg" if qc_variant_ok else None

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = bracket or (lambda *_a, **_k: (b"\xff\xd8merged", {}))
    fake_pes.enhance_image = single or (lambda *_a, **_k: (b"\xff\xd8single", {}))
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else (-2.0 if "-" in n else 2.0)
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (b, {"export": {}})
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    return updates


RAW_VARIANT = "uploads/job/qc_raw.jpg"


def test_single_edit_variant_failure_clears_raw_variant(monkeypatch):
    updates = _install(monkeypatch, qc_variant_ok=False)
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-1",
        {"id": "s1", "file_url": "raw/s1.jpg", "filename": "kitchen.jpg",
         "qc_variant_url": RAW_VARIANT},
    ))
    (uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert upd["processed_url"] == "processed/s1.jpg"
    assert "qc_variant_url" in upd and upd["qc_variant_url"] is None


def test_single_edit_variant_ok_uses_edit_variant(monkeypatch):
    updates = _install(monkeypatch, qc_variant_ok=True)
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-1",
        {"id": "s1", "file_url": "raw/s1.jpg", "filename": "kitchen.jpg",
         "qc_variant_url": RAW_VARIANT},
    ))
    (_uid, upd), = updates
    assert upd["qc_variant_url"] == "enhanced/qc_s1.jpg"


def test_held_original_keeps_raw_variant(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("enhance exploded")

    updates = _install(monkeypatch, qc_variant_ok=False, single=boom)
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-1",
        {"id": "s1", "file_url": "raw/s1.jpg", "filename": "kitchen.jpg",
         "qc_variant_url": RAW_VARIANT},
    ))
    (_uid, upd), = updates
    assert upd["provider"] == "original"
    assert upd["qc_status"] == "rejected"
    assert upd.get("qc_variant_url") == RAW_VARIANT


def test_twilight_edit_variant_failure_clears_raw_variant(monkeypatch):
    updates = _install(monkeypatch, qc_variant_ok=False)
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-1",
        {"id": "t1", "file_url": "raw/twilight_t1.jpg", "filename": "twilight_front.jpg",
         "media_type": "twilight", "qc_variant_url": RAW_VARIANT},
    ))
    (_uid, upd), = updates
    assert upd["provider"] == "bricks_inhouse"
    assert "qc_variant_url" in upd and upd["qc_variant_url"] is None


def test_bracket_primary_variant_failure_clears_raw_variant(monkeypatch):
    updates = _install(monkeypatch, qc_variant_ok=False)
    group = [
        {"id": "u1", "file_url": "p1", "filename": "lounge_-2.jpg", "qc_variant_url": "v1"},
        {"id": "u2", "file_url": "p2", "filename": "lounge_0.jpg", "qc_variant_url": "v2"},
        {"id": "u3", "file_url": "p3", "filename": "lounge_+2.jpg", "qc_variant_url": "v3"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-b", group, apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert by_id["u2"]["processed_url"] == "processed/u2.jpg"
    assert "qc_variant_url" in by_id["u2"] and by_id["u2"]["qc_variant_url"] is None


def test_bracket_primary_variant_ok(monkeypatch):
    updates = _install(monkeypatch, qc_variant_ok=True)
    group = [
        {"id": "u1", "file_url": "p1", "filename": "lounge_-2.jpg"},
        {"id": "u2", "file_url": "p2", "filename": "lounge_0.jpg"},
        {"id": "u3", "file_url": "p3", "filename": "lounge_+2.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-b", group, apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert by_id["u2"]["qc_variant_url"] == "enhanced/qc_u2.jpg"


def _as_is_install(monkeypatch, export_path):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def no_heic(*_a, **_k):
        return None

    async def fake_export(*_a, **_k):
        return export_path

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(pp, "_heic_as_is_jpeg_url", no_heic)
    monkeypatch.setattr(pp, "_listing_export_as_is_url", fake_export)
    return updates


def test_staging_listing_export_clears_uncropped_raw_variant(monkeypatch):
    updates = _as_is_install(monkeypatch, "enhanced/job/st1.jpg")
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-s",
        {"id": "st1", "file_url": "raw/staging_living.jpg", "filename": "staging_living.jpg",
         "media_type": "staging", "mime_type": "image/jpeg", "qc_variant_url": RAW_VARIANT},
    ))
    (_uid, upd), = updates
    assert upd["processed_url"] == "enhanced/job/st1.jpg"
    assert "qc_variant_url" in upd and upd["qc_variant_url"] is None


def test_drone_listing_export_clears_uncropped_raw_variant(monkeypatch):
    updates = _as_is_install(monkeypatch, "enhanced/job/d1.jpg")
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-d",
        {"id": "d1", "file_url": "raw/drone_aerial.jpg", "filename": "drone_aerial.jpg",
         "media_type": "drone", "mime_type": "image/jpeg", "qc_variant_url": RAW_VARIANT},
    ))
    (_uid, upd), = updates
    assert upd["provider"] == "as_is"
    assert upd["processed_url"] == "enhanced/job/d1.jpg"
    assert "qc_variant_url" in upd and upd["qc_variant_url"] is None


def test_as_is_unchanged_bytes_keep_variant(monkeypatch):
    updates = _as_is_install(monkeypatch, None)
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-d",
        {"id": "d1", "file_url": "raw/drone_aerial.jpg", "filename": "drone_aerial.jpg",
         "media_type": "drone", "mime_type": "image/jpeg", "qc_variant_url": RAW_VARIANT},
    ))
    (_uid, upd), = updates
    assert upd["processed_url"] == "raw/drone_aerial.jpg"
    assert "qc_variant_url" not in upd
