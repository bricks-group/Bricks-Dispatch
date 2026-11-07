"""Staging leftovers must deliver as_is — no enhance/HDR; PropTx MLS crop OK."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def test_deliver_staging_skips_enhance_and_marks_as_is(monkeypatch):
    """route=staging: processed_url = file_path, provider as_is, never enhance_image."""
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    # If deliver ever imported enhance_image, fail hard.
    fake_pes = ModuleType("app.services.photo_enhance_service")

    def _boom(*_a, **_k):
        raise AssertionError("enhance_image must not be called for staging")

    fake_pes.enhance_image = _boom
    fake_pes.upload_enhanced = AsyncMock()
    fake_pes.upload_qc_variant = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    asset = {
        "id": "up-stage",
        "file_url": "bricks-go/jobs/j1/up-stage/living-stage.jpg",
        "filename": "living-stage.jpg",
        "media_type": "staging",
    }

    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))

    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-stage"
    assert update["processing_status"] == "done"
    assert update["processed_url"] == asset["file_url"]
    assert update["provider"] == "as_is"


def test_leftovers_route_staging_to_others_not_hdr_photos(monkeypatch):
    """Staging must not enter group_assets_by_bracket / deliver_bracket_group."""
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append([a["id"] for a in group])

    def fake_group(photos):
        grouped_inputs.append([a["id"] for a in photos])
        assert all(a.get("media_type") != "staging" for a in photos)
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
            "id": "up-stage",
            "filename": "living-stage.jpg",
            "file_url": "p/living-stage.jpg",
            "media_type": "staging",
        },
        {
            "id": "up-drone",
            "filename": "aerial.mp4",
            "file_url": "p/aerial.mp4",
            "media_type": "drone",
        },
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert "up-stage" in delivered
    assert "up-drone" in delivered
    assert grouped_inputs == [["up-photo-m", "up-photo-0"]]
    assert bracket_calls == [["up-photo-m", "up-photo-0"]]
    assert all("up-stage" not in g for g in bracket_calls)


def test_leftovers_staging_alone_never_calls_bracket(monkeypatch):
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
            "id": "up-stage",
            "filename": "stage.jpg",
            "file_url": "p/stage.jpg",
            "media_type": "staging",
        }
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert delivered == ["up-stage"]
    assert bracket_calls == []
    assert grouped_inputs == [[]]


def test_deliver_staging_jpeg_applies_listing_export(monkeypatch):
    """Staging still: listing_export runs; HDR enhance never; provider as_is."""
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    async def fake_download(path):
        assert path == "bricks-go/jobs/j1/up-stage/staging_living.jpg"
        return b"fake-staging-bytes"

    async def fake_upload(_storage, job_id, upload_id, data):
        assert job_id == "job-1"
        assert upload_id == "up-stage"
        assert data == b"exported-jpeg"
        return f"enhanced/{job_id}/{upload_id}.jpg"

    def fake_listing_export(data):
        assert data == b"fake-staging-bytes"
        return b"exported-jpeg", {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800},
        }

    def _boom(*_a, **_k):
        raise AssertionError("HDR enhance must not run for staging listing_export")

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = _boom
    fake_pes.enhance_bracket = _boom
    fake_pes.apply_listing_export = fake_listing_export
    fake_pes.upload_enhanced = fake_upload
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    asset = {
        "id": "up-stage",
        "file_url": "bricks-go/jobs/j1/up-stage/staging_living.jpg",
        "filename": "staging_living.jpg",
        "media_type": "staging",
        "mime_type": "image/jpeg",
    }

    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))

    assert len(updates) == 1
    uid, update = updates[0]
    assert uid == "up-stage"
    assert update["processing_status"] == "done"
    assert update["provider"] == "as_is"
    assert update["processed_url"] == "enhanced/job-1/up-stage.jpg"


def test_deliver_staging_filename_cue_applies_listing_export(monkeypatch):
    """Canada uploads strip media_type — staging_ filename still gets PropTx export."""
    updates = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)

    async def fake_download(path):
        return b"cue-bytes"

    async def fake_upload(_storage, job_id, upload_id, data):
        return f"enhanced/{job_id}/{upload_id}.jpg"

    def fake_listing_export(data):
        assert data == b"cue-bytes"
        return b"exported-jpeg", {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800},
        }

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_image = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("no enhance")
    )
    fake_pes.enhance_bracket = fake_pes.enhance_image
    fake_pes.apply_listing_export = fake_listing_export
    fake_pes.upload_enhanced = fake_upload
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    asset = {
        "id": "up-stage-cue",
        "file_url": "p/staging_kitchen.jpg",
        "filename": "staging_kitchen.jpg",
        # no media_type — durable filename cue
        "mime_type": "image/jpeg",
    }

    _run(pp.deliver_asset_without_vendor(MagicMock(), "job-1", asset))

    assert len(updates) == 1
    assert updates[0][1]["provider"] == "as_is"
    assert updates[0][1]["processed_url"] == "enhanced/job-1/up-stage-cue.jpg"


def test_listing_export_as_is_url_allows_staging_stills(monkeypatch):
    """Helper: staging JPEG eligible for PropTx (floorplan still skipped)."""
    async def fake_download(path):
        return b"img-bytes"

    async def fake_upload(_storage, job_id, upload_id, data):
        return f"enhanced/{job_id}/{upload_id}.jpg"

    def fake_listing_export(data):
        return b"exported", {
            "listing_export_applied": True,
            "export": {"width": 2400, "height": 1800},
        }

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.apply_listing_export = fake_listing_export
    fake_pes.upload_enhanced = fake_upload
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    staging = {
        "filename": "staging_living.jpg",
        "mime_type": "image/jpeg",
        "media_type": "staging",
        "file_url": "p/staging_living.jpg",
    }
    out = asyncio.run(
        pp._listing_export_as_is_url(
            MagicMock(), "job-1", "up-s", staging, "p/staging_living.jpg"
        )
    )
    assert out == "enhanced/job-1/up-s.jpg"
