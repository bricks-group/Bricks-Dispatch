"""Drone/aerial JPEG leftovers must deliver as_is — no HDR merge / sky invent.

media_type is not a public.uploads column, so routing relies on filename inference
(DJI_*, aerial*, drone*). Without that, leftovers used to hit vendor_photo.
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
    """Filename-inferred drone JPEG: original path, provider as_is, never enhance."""
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
    assert update["processed_url"] == asset["file_url"]
    assert update["provider"] == "as_is"


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
