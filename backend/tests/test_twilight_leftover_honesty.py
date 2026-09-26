"""Twilight leftovers: enhance with apply_sky=False — never HDR photos group / blue-sky."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def test_deliver_twilight_calls_enhance_bracket_apply_sky_false(monkeypatch):
    """route=twilight: enhance_bracket([bytes], apply_sky=False); provider bricks_inhouse."""
    updates = []
    bracket_calls = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    async def fake_download(_path):
        return b"JPEG-BYTES"

    async def fake_upload_enhanced(_storage, job_id, upload_id, enhanced):
        assert enhanced == b"ENHANCED"
        return f"jobs/{job_id}/{upload_id}/enhanced.jpg"

    async def fake_upload_qc(_storage, job_id, upload_id, enhanced):
        return f"jobs/{job_id}/{upload_id}/qc.jpg"

    def fake_enhance_bracket(frames, filenames=None, apply_sky=True, return_meta=False, **_k):
        bracket_calls.append({"frames": frames, "apply_sky": apply_sky, "return_meta": return_meta})
        assert frames == [b"JPEG-BYTES"]
        assert apply_sky is False
        assert return_meta is True
        if return_meta:
            return b"ENHANCED", {}
        return b"ENHANCED"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = fake_enhance_bracket
    fake_pes.enhance_image = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("enhance_image must not be used for twilight")
    )
    fake_pes.upload_enhanced = fake_upload_enhanced
    fake_pes.upload_qc_variant = fake_upload_qc
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    asset = {
        "id": "up-dusk",
        "file_url": "bricks-go/jobs/j1/up-dusk/dusk-front.jpg",
        "filename": "dusk-front.jpg",
        "media_type": "twilight",
    }
    db = MagicMock()
    db.storage = MagicMock()

    _run(pp.deliver_asset_without_vendor(db, "job-1", asset))

    assert len(bracket_calls) == 1
    assert bracket_calls[0]["apply_sky"] is False
    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-dusk"
    assert update["processing_status"] == "done"
    assert update["provider"] == "bricks_inhouse"
    assert update["processed_url"] == "jobs/job-1/up-dusk/enhanced.jpg"
    assert update["qc_variant_url"] == "jobs/job-1/up-dusk/qc.jpg"


def test_deliver_twilight_failure_falls_back_to_original(monkeypatch):
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    async def fake_download(_path):
        return b"JPEG-BYTES"

    def boom(*_a, **_k):
        raise RuntimeError("enhance blew up")

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = boom
    fake_pes.upload_enhanced = AsyncMock()
    fake_pes.upload_qc_variant = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    asset = {
        "id": "up-dusk",
        "file_url": "bricks-go/jobs/j1/up-dusk/dusk.jpg",
        "filename": "dusk.jpg",
        "media_type": "dusk",
    }

    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-1", asset))

    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-dusk"
    assert update["processed_url"] == asset["file_url"]
    assert update["provider"] == "original"
    assert update["processing_status"] == "done"


def test_leftovers_route_twilight_to_others_not_hdr_photos(monkeypatch):
    """Twilight must not enter group_assets_by_bracket / deliver_bracket_group."""
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group):
        bracket_calls.append([a["id"] for a in group])

    def fake_group(photos):
        grouped_inputs.append([a["id"] for a in photos])
        assert all(a.get("media_type") not in ("twilight", "dusk") for a in photos)
        assert all(
            not str(a.get("filename") or "").lower().startswith("dusk")
            and "twilight" not in str(a.get("filename") or "").lower()
            for a in photos
        )
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
            "id": "up-photo-m",
            "filename": "living--1.jpg",
            "file_url": "p/living--1.jpg",
            "media_type": "hdr",
        },
        {
            "id": "up-photo-0",
            "filename": "living-0.jpg",
            "file_url": "p/living-0.jpg",
            "media_type": "hdr",
        },
        {
            "id": "up-dusk",
            "filename": "dusk-front.jpg",
            "file_url": "p/dusk-front.jpg",
            "media_type": "twilight",
        },
        {
            "id": "up-drone",
            "filename": "aerial.mp4",
            "file_url": "p/aerial.mp4",
            "media_type": "drone",
        },
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert "up-dusk" in delivered
    assert "up-drone" in delivered
    assert grouped_inputs == [["up-photo-m", "up-photo-0"]]
    assert bracket_calls == [["up-photo-m", "up-photo-0"]]
    assert all("up-dusk" not in g for g in bracket_calls)


def test_leftovers_twilight_alone_never_calls_bracket(monkeypatch):
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group):
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
            "id": "up-dusk",
            "filename": "twilight.jpg",
            "file_url": "p/twilight.jpg",
            "media_type": "twilight",
        }
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert delivered == ["up-dusk"]
    assert bracket_calls == []
    assert grouped_inputs == [[]]

