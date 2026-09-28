"""Drone/aerial JPEG leftovers must deliver as_is — no HDR merge / sky invent.

media_type is not a public.uploads column, so routing relies on filename inference
(DJI_*, aerial*, drone*). Without that, leftovers used to hit vendor_photo.

Listing_export (4:3 / le2400 / sRGB JPEG) is allowed for PropTx MLS; HDR enhance
(enhance_image / enhance_bracket) must never run. Provider stays as_is.
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock

from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def test_deliver_aerial_jpeg_skips_enhance_and_marks_as_is(monkeypatch):
    """Filename-inferred drone JPEG: provider as_is, never HDR enhance.

    listing_export may rewrite processed_url to an enhanced/ path; that is format
    only — not HDR. When export is unavailable, original path is kept.
    """
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    fake_pes = ModuleType("app.services.photo_enhance_service")

    def _boom(*_a, **_k):
        raise AssertionError("enhance must not be called for aerial JPEG")

    fake_pes.enhance_image = _boom
    fake_pes.enhance_bracket = _boom
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    # No media_type key — mirrors DB row after insert whitelist.
    asset = {
        "id": "up-aerial",
        "file_url": "bricks-go/jobs/j1/up-aerial/aerial-front.jpg",
        "filename": "aerial-front.jpg",
    }

    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))

    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-aerial"
    assert update["processing_status"] == "done"
    assert update["provider"] == "as_is"
    # May be original or listing-export enhanced/ path — never empty.
    assert update["processed_url"]


def test_deliver_aerial_jpeg_applies_listing_export(monkeypatch):
    """Drone still: listing_export runs; HDR enhance never; provider as_is."""
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    async def fake_download(path):
        assert path == "bricks-go/jobs/j1/up-aerial/aerial-front.jpg"
        return b"fake-aerial-bytes"

    async def fake_upload(_storage, job_id, upload_id, data):
        assert job_id == "job-1"
        assert upload_id == "up-aerial"
        assert data == b"exported-jpeg"
        return f"enhanced/{job_id}/{upload_id}.jpg"

    def fake_listing_export(data):
        assert data == b"fake-aerial-bytes"
        return b"exported-jpeg", {"listing_export_applied": True, "export": {"width": 2400, "height": 1800}}

    def _boom(*_a, **_k):
        raise AssertionError("HDR enhance must not run for drone listing_export")

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = _boom
    fake_pes.enhance_bracket = _boom
    fake_pes.apply_listing_export = fake_listing_export
    fake_pes.upload_enhanced = fake_upload
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    asset = {
        "id": "up-aerial",
        "file_url": "bricks-go/jobs/j1/up-aerial/aerial-front.jpg",
        "filename": "aerial-front.jpg",
    }

    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))

    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-aerial"
    assert update["processing_status"] == "done"
    assert update["provider"] == "as_is"
    assert update["processed_url"] == "enhanced/job-1/up-aerial.jpg"


def test_leftovers_route_aerial_jpeg_to_others_not_hdr_photos(monkeypatch):
    """Aerial JPEG must not enter group_assets_by_bracket / deliver_bracket_group."""
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append([a["id"] for a in group])

    def fake_group(photos):
        grouped_inputs.append([a["id"] for a in photos])
        assert all("aerial" not in str(a.get("filename") or "").lower() for a in photos)
        assert all(not str(a.get("filename") or "").upper().startswith("DJI") for a in photos)
        if not photos:
            return []
        return [list(photos)]

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.group_assets_by_bracket = fake_group
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr(pp, "deliver_asset_without_vendor", fake_deliver)
    monkeypatch.setattr(pp, "deliver_bracket_group_without_vendor", fake_bracket)

    leftover = [
        {
            "id": "up-photo-0",
            "filename": "living-0.jpg",
            "file_url": "p/living-0.jpg",
        },
        {
            "id": "up-aerial",
            "filename": "aerial-front.jpg",
            "file_url": "p/aerial-front.jpg",
        },
        {
            "id": "up-dji",
            "filename": "DJI_0042.JPG",
            "file_url": "p/DJI_0042.JPG",
        },
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert "up-aerial" in delivered
    assert "up-dji" in delivered
    assert grouped_inputs == [["up-photo-0"]]
    assert bracket_calls == []  # single photo → deliver_asset, not bracket group
    assert "up-aerial" not in (grouped_inputs[0] if grouped_inputs else [])
    assert "up-dji" not in (grouped_inputs[0] if grouped_inputs else [])


def test_leftovers_aerial_alone_never_calls_bracket(monkeypatch):
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append([a["id"] for a in group])

    def fake_group(photos):
        grouped_inputs.append(list(photos))
        assert photos == []
        return []

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.group_assets_by_bracket = fake_group
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr(pp, "deliver_asset_without_vendor", fake_deliver)
    monkeypatch.setattr(pp, "deliver_bracket_group_without_vendor", fake_bracket)

    leftover = [
        {
            "id": "up-aerial",
            "filename": "aerial-front.jpg",
            "file_url": "p/aerial-front.jpg",
        }
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert delivered == ["up-aerial"]
    assert bracket_calls == []
    assert grouped_inputs == [[]]
