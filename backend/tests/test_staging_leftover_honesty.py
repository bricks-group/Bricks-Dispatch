"""Staging leftovers must deliver original (as_is) — no enhance, no HDR merge."""
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
