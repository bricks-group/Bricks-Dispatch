"""Twilight leftovers: enhance with apply_sky=False — never vendor_photo blue-sky invent.

Multi-frame dusk -/+ EV sets HDR-merge via deliver_bracket_group(apply_sky=False);
single-frame twilight still uses deliver_asset_without_vendor polish.
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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


def test_leftovers_twilight_never_enters_vendor_photo_bracket(monkeypatch):
    """Twilight must not enter vendor_photo group (apply_sky=True); own path apply_sky=False."""
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append(
            {"ids": [a["id"] for a in group], "apply_sky": apply_sky}
        )

    def fake_group(assets):
        ids = [a["id"] for a in assets]
        grouped_inputs.append(ids)
        # vendor_photo groups must never include twilight/dusk assets
        assert all(
            a.get("media_type") not in ("twilight", "dusk") for a in assets
        ) or all(
            a.get("media_type") in ("twilight", "dusk")
            or "twilight" in str(a.get("filename") or "").lower()
            or "dusk" in str(a.get("filename") or "").lower()
            for a in assets
        )
        if not assets:
            return []
        return [list(assets)]

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

    assert "up-dusk" in delivered  # single twilight → len<=1 polish path
    assert "up-drone" in delivered
    assert ["up-photo-m", "up-photo-0"] in grouped_inputs
    assert ["up-dusk"] in grouped_inputs
    photo_brackets = [c for c in bracket_calls if c["apply_sky"] is True]
    twilight_brackets = [c for c in bracket_calls if c["apply_sky"] is False]
    assert photo_brackets == [
        {"ids": ["up-photo-m", "up-photo-0"], "apply_sky": True}
    ]
    assert twilight_brackets == []  # single dusk → deliver_asset, not bracket group
    assert all("up-dusk" not in c["ids"] for c in photo_brackets)


def test_leftovers_twilight_alone_never_calls_bracket(monkeypatch):
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append([a["id"] for a in group])

    def fake_group(assets):
        grouped_inputs.append([a["id"] for a in assets])
        if not assets:
            return []
        return [list(assets)]

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
    # photos empty group + twilight single-asset group (len<=1 → deliver)
    assert grouped_inputs == [[], ["up-dusk"]]


def test_multi_frame_twilight_leftovers_merge_apply_sky_false(monkeypatch):
    """-1/0/+1 twilight leftovers → one enhance_bracket(apply_sky=False); siblings share URL."""
    updates = []
    bracket_calls = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, dict(update)))

    async def fake_download(path):
        return f"BYTES:{path}".encode()

    async def fake_upload_enhanced(_storage, job_id, upload_id, enhanced):
        assert enhanced == b"MERGED-DUSK"
        return f"jobs/{job_id}/{upload_id}/enhanced.jpg"

    async def fake_upload_qc(_storage, job_id, upload_id, enhanced):
        return f"jobs/{job_id}/{upload_id}/qc.jpg"

    def fake_enhance_bracket(frames, filenames=None, apply_sky=True, return_meta=False, **_k):
        bracket_calls.append(
            {
                "n_frames": len(frames),
                "filenames": list(filenames or []),
                "apply_sky": apply_sky,
                "return_meta": return_meta,
            }
        )
        assert apply_sky is False
        assert return_meta is True
        assert len(frames) == 3
        if return_meta:
            return b"MERGED-DUSK", {}
        return b"MERGED-DUSK"

    def boom_enhance_image(*_a, **_k):
        raise AssertionError("enhance_image must not be used for twilight brackets")

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = fake_enhance_bracket
    fake_pes.enhance_image = boom_enhance_image
    # Use real stem/bias helpers so -1/0/+1 group correctly
    from app.services.photo_enhance_service import (
        exposure_bias_from_name,
        group_assets_by_bracket,
    )

    fake_pes.exposure_bias_from_name = exposure_bias_from_name
    fake_pes.group_assets_by_bracket = group_assets_by_bracket
    fake_pes.upload_enhanced = fake_upload_enhanced
    fake_pes.upload_qc_variant = fake_upload_qc
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe_update)
    monkeypatch.setattr("app.services.storage.download_bytes", fake_download)

    leftover = [
        {
            "id": "up-tw-m",
            "filename": "twilight_front--1.jpg",
            "file_url": "p/twilight_front--1.jpg",
            "media_type": "twilight",
        },
        {
            "id": "up-tw-0",
            "filename": "twilight_front-0.jpg",
            "file_url": "p/twilight_front-0.jpg",
            "media_type": "twilight",
        },
        {
            "id": "up-tw-p",
            "filename": "twilight_front-+1.jpg",
            "file_url": "p/twilight_front-+1.jpg",
            "media_type": "twilight",
        },
    ]

    db = MagicMock()
    db.storage = MagicMock()
    _run(pp._deliver_leftovers_with_brackets(db, "job-1", leftover))

    assert len(bracket_calls) == 1
    assert bracket_calls[0]["apply_sky"] is False
    assert bracket_calls[0]["n_frames"] == 3
    assert all(c["apply_sky"] is False for c in bracket_calls)

    by_id = {uid: upd for uid, upd in updates}
    assert set(by_id) == {"up-tw-m", "up-tw-0", "up-tw-p"}
    shared = "jobs/job-1/up-tw-0/enhanced.jpg"
    assert by_id["up-tw-0"]["provider"] == "bricks_inhouse"
    assert by_id["up-tw-0"]["processed_url"] == shared
    assert by_id["up-tw-0"].get("qc_variant_url") == "jobs/job-1/up-tw-0/qc.jpg"
    assert by_id["up-tw-m"]["provider"] == "bracket_sibling"
    assert by_id["up-tw-p"]["provider"] == "bracket_sibling"
    assert by_id["up-tw-m"]["processed_url"] == shared
    assert by_id["up-tw-p"]["processed_url"] == shared
    assert by_id["up-tw-m"].get("qc_variant_url") is None
    assert by_id["up-tw-p"].get("qc_variant_url") is None


def test_leftovers_staging_and_aerial_never_enter_twilight_or_photo_brackets(monkeypatch):
    """Regression: staging / aerial JPEG stay out of both bracket paths."""
    delivered = []
    bracket_calls = []
    grouped_inputs = []

    async def fake_deliver(_db, job_id, asset):
        delivered.append(asset["id"])

    async def fake_bracket(_db, job_id, group, *, apply_sky=True):
        bracket_calls.append(
            {"ids": [a["id"] for a in group], "apply_sky": apply_sky}
        )

    def fake_group(assets):
        ids = [a["id"] for a in assets]
        grouped_inputs.append(ids)
        for a in assets:
            fn = str(a.get("filename") or "").lower()
            assert a.get("media_type") != "staging"
            assert not fn.startswith("staging_")
            assert "aerial" not in fn
            assert not str(a.get("filename") or "").upper().startswith("DJI")
        if not assets:
            return []
        return [list(assets)]

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
            "media_type": "hdr",
        },
        {
            "id": "up-stage",
            "filename": "staging_living.jpg",
            "file_url": "p/staging_living.jpg",
            "media_type": "staging",
        },
        {
            "id": "up-aerial",
            "filename": "aerial-front.jpg",
            "file_url": "p/aerial-front.jpg",
        },
        {
            "id": "up-tw-m",
            "filename": "dusk_front--1.jpg",
            "file_url": "p/dusk_front--1.jpg",
            "media_type": "twilight",
        },
        {
            "id": "up-tw-0",
            "filename": "dusk_front-0.jpg",
            "file_url": "p/dusk_front-0.jpg",
            "media_type": "twilight",
        },
    ]

    _run(pp._deliver_leftovers_with_brackets(MagicMock(), "job-1", leftover))

    assert "up-stage" in delivered
    assert "up-aerial" in delivered
    assert "up-photo-0" in delivered  # single photo → deliver
    assert ["up-photo-0"] in grouped_inputs
    assert ["up-tw-m", "up-tw-0"] in grouped_inputs
    assert all("up-stage" not in ids for ids in grouped_inputs)
    assert all("up-aerial" not in ids for ids in grouped_inputs)
    twilight_brackets = [c for c in bracket_calls if c["apply_sky"] is False]
    assert twilight_brackets == [
        {"ids": ["up-tw-m", "up-tw-0"], "apply_sky": False}
    ]
    assert all(c["apply_sky"] is not True or "up-tw" not in str(c["ids"]) for c in bracket_calls)
