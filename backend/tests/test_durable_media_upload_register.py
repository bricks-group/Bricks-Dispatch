"""Durable media_type via filename on upload register (no schema migration).

GO picks media_type on upload, but public.uploads has no media_type column —
_uploads_db_row strips it. Persist kind in the filename column so leftover
routing (delivery_route) stays honest after DB reload.
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

from fastapi import BackgroundTasks

from app.routers import uploads as up
from app.services.media_intake import delivery_route, durable_media_filename
from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def _complete_capturing_insert(monkeypatch, filename: str, media_type: str):
    """Run complete_stills_upload; return (response, inserted_db_row)."""
    job = {
        "id": "job-d",
        "uploaded_count": 0,
        "deliverables_count": 20,
        "status": "in_progress",
        "vendor_id": "default_vendor",
        "assignee_id": "photog-1",
    }
    upload_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    # storage_path keeps the original (signed) basename — not the durable name
    storage_path = f"bricks-go/jobs/job-d/{upload_id}/{filename}"
    inserted = []

    class Chain:
        def insert(self, row):
            inserted.append(dict(row))
            return self

        def update(self, *_a, **_k):
            return self

        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            class R:
                data = {**job, "uploaded_count": 1}
            return R()

    class Db:
        def table(self, name):
            return Chain()

    monkeypatch.setattr(
        up,
        "_actor_job",
        AsyncMock(return_value=(Db(), "photog-1", "default_vendor", job)),
    )
    monkeypatch.setattr(up, "_maybe_start_post", AsyncMock())

    out = _run(
        up.complete_stills_upload(
            BackgroundTasks(),
            {
                "job_id": "job-d",
                "upload_id": upload_id,
                "storage_path": storage_path,
                "filename": filename,
                "mime_type": "image/jpeg",
                "file_size": 1024,
                "media_type": media_type,
            },
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert len(inserted) == 1
    # Whitelist already applied inside _insert_upload_row — but Chain.insert
    # receives db_row from _insert_upload_row. complete uses _insert_upload_row
    # which calls _uploads_db_row. Capture via monkeypatch on _insert_upload_row.
    return out, inserted[0], storage_path


def test_complete_twilight_stores_durable_filename(monkeypatch):
    """media_type=twilight + IMG_0001.JPG → filename cues twilight for delivery_route."""
    captured = {}

    async def capture_insert(db, payload_row):
        captured["row"] = up._uploads_db_row(payload_row)
        captured["full"] = dict(payload_row)

    monkeypatch.setattr(up, "_insert_upload_row", capture_insert)
    monkeypatch.setattr(
        up,
        "_actor_job",
        AsyncMock(
            return_value=(
                MagicMock(),
                "photog-1",
                "default_vendor",
                {
                    "id": "job-d",
                    "uploaded_count": 0,
                    "deliverables_count": 20,
                    "status": "in_progress",
                    "vendor_id": "default_vendor",
                    "assignee_id": "photog-1",
                },
            )
        ),
    )
    monkeypatch.setattr(up, "_bump_uploaded_count", AsyncMock(return_value=1))
    monkeypatch.setattr(up, "_maybe_start_post", AsyncMock())

    upload_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    storage_path = f"bricks-go/jobs/job-d/{upload_id}/IMG_0001.JPG"
    out = _run(
        up.complete_stills_upload(
            BackgroundTasks(),
            {
                "job_id": "job-d",
                "upload_id": upload_id,
                "storage_path": storage_path,
                "filename": "IMG_0001.JPG",
                "mime_type": "image/jpeg",
                "file_size": 1024,
                "media_type": "twilight",
            },
            authorization="Bearer x",
            x_user_id=None,
        )
    )
    assert out["filename"] == "twilight_IMG_0001.JPG"
    assert captured["row"]["filename"] == "twilight_IMG_0001.JPG"
    assert "media_type" not in captured["row"]
    # signed path contract unchanged
    assert captured["row"]["file_url"] == storage_path
    # DB-shaped row (filename only) routes twilight
    assert delivery_route({
        "file_url": captured["row"]["file_url"],
        "filename": captured["row"]["filename"],
    }) == "twilight"


def test_complete_drone_staging_hdr_durable_names(monkeypatch):
    cases = [
        ("IMG_9.JPG", "drone", "aerial_IMG_9.JPG", "as_is"),
        ("living.jpg", "staging", "staging_living.jpg", "staging"),
        ("IMG_0001.JPG", "hdr", "IMG_0001.JPG", "vendor_photo"),
    ]
    for filename, media_type, expect_name, expect_route in cases:
        captured = {}

        async def capture_insert(db, payload_row, _c=captured):
            _c["row"] = up._uploads_db_row(payload_row)

        monkeypatch.setattr(up, "_insert_upload_row", capture_insert)
        monkeypatch.setattr(
            up,
            "_actor_job",
            AsyncMock(
                return_value=(
                    MagicMock(),
                    "photog-1",
                    "default_vendor",
                    {
                        "id": "job-d",
                        "uploaded_count": 0,
                        "deliverables_count": 20,
                        "status": "in_progress",
                        "vendor_id": "default_vendor",
                        "assignee_id": "photog-1",
                    },
                )
            ),
        )
        monkeypatch.setattr(up, "_bump_uploaded_count", AsyncMock(return_value=1))
        monkeypatch.setattr(up, "_maybe_start_post", AsyncMock())

        upload_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        storage_path = f"bricks-go/jobs/job-d/{upload_id}/{filename}"
        out = _run(
            up.complete_stills_upload(
                BackgroundTasks(),
                {
                    "job_id": "job-d",
                    "upload_id": upload_id,
                    "storage_path": storage_path,
                    "filename": filename,
                    "mime_type": "image/jpeg",
                    "file_size": 1024,
                    "media_type": media_type,
                },
                authorization="Bearer x",
                x_user_id=None,
            )
        )
        assert out["filename"] == expect_name, (media_type, out["filename"])
        assert captured["row"]["filename"] == expect_name
        assert captured["row"]["file_url"] == storage_path
        assert delivery_route({
            "file_url": storage_path,
            "filename": expect_name,
        }) == expect_route


def test_deliver_twilight_durable_name_apply_sky_false(monkeypatch):
    """DB row with durable twilight_ filename (no media_type) → apply_sky=False."""
    updates = []
    bracket_calls = []

    async def fake_safe_update(_db, upload_id, update):
        updates.append((upload_id, update))

    async def fake_download(_path):
        return b"JPEG-BYTES"

    async def fake_upload_enhanced(_storage, job_id, upload_id, enhanced):
        return f"jobs/{job_id}/{upload_id}/enhanced.jpg"

    async def fake_upload_qc(_storage, job_id, upload_id, enhanced):
        return f"jobs/{job_id}/{upload_id}/qc.jpg"

    def fake_enhance_bracket(frames, filenames=None, apply_sky=True, return_meta=False, **_k):
        bracket_calls.append({"apply_sky": apply_sky})
        assert apply_sky is False
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

    durable = durable_media_filename("IMG_0001.JPG", "twilight")
    # No media_type key — mirrors DB after whitelist strip
    asset = {
        "id": "up-dusk",
        "file_url": "bricks-go/jobs/j1/up-dusk/IMG_0001.JPG",
        "filename": durable,
    }
    assert delivery_route(asset) == "twilight"

    _run(pp.deliver_asset_without_vendor(MagicMock(storage=MagicMock()), "job-1", asset))

    assert len(bracket_calls) == 1
    assert bracket_calls[0]["apply_sky"] is False
    assert updates[0][1]["provider"] == "bricks_inhouse"


def test_complete_rejects_camera_raw(monkeypatch):
    """Hollow enhance hole closes at register — CR3 never inserts."""
    from fastapi import HTTPException

    job = {
        "id": "job-d",
        "uploaded_count": 0,
        "deliverables_count": 20,
        "status": "in_progress",
        "vendor_id": "default_vendor",
        "assignee_id": "photog-1",
    }
    upload_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    filename = "IMG_0001.CR3"
    storage_path = f"bricks-go/jobs/job-d/{upload_id}/{filename}"

    class Db:
        def table(self, name):
            raise AssertionError("DB must not be touched for RAW")

    monkeypatch.setattr(
        up,
        "_actor_job",
        AsyncMock(return_value=(Db(), "photog-1", "default_vendor", job)),
    )
    monkeypatch.setattr(up, "_maybe_start_post", AsyncMock())

    try:
        _run(
            up.complete_stills_upload(
                BackgroundTasks(),
                {
                    "job_id": "job-d",
                    "upload_id": upload_id,
                    "storage_path": storage_path,
                    "filename": filename,
                    "mime_type": "image/x-canon-cr3",
                    "media_type": "hdr",
                    "file_size": 12,
                },
            )
        )
        raise AssertionError("expected camera_raw_unsupported")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail["code"] == "camera_raw_unsupported"


def test_sign_rejects_camera_raw(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(
        up,
        "_actor_job",
        AsyncMock(side_effect=AssertionError("_actor_job must not run for RAW")),
    )

    try:
        _run(
            up.sign_stills_upload(
                {
                    "job_id": "job-d",
                    "filename": "bracket.DNG",
                    "mime_type": "image/x-adobe-dng",
                    "media_type": "hdr",
                }
            )
        )
        raise AssertionError("expected camera_raw_unsupported")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail["code"] == "camera_raw_unsupported"
