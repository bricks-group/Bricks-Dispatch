"""HDR bracket gate for Mark complete — parity with web/frontend bracket-require."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.bracket_require import (
    BRACKET_HINT,
    HDR_BRACKET_MIN,
    basename,
    group_by_bracket_stem,
    is_image_name,
    validate_hdr_uploads_for_complete,
)
from app.routers import vendor_os as vos
from app.shared.schemas import JobStatusUpdate


def test_constants_match_client():
    assert HDR_BRACKET_MIN == 3
    assert "bracketed" in BRACKET_HINT.lower() or "−" in BRACKET_HINT or "-" in BRACKET_HINT


def test_basename_and_image():
    assert basename("a/b/living_-2.jpg") == "living_-2.jpg"
    assert is_image_name("living.jpg")
    assert is_image_name("x.HEIC")
    assert not is_image_name("scene.splat")
    assert not is_image_name("mesh.ply")
    assert not is_image_name("cloud.spz")
    assert not is_image_name("clip.mp4")


def test_group_by_bracket_stem():
    g = group_by_bracket_stem(["living_-2.jpg", "living_0.jpg", "living_+2.jpg", "kit_under.jpg"])
    assert "living" in g
    assert len(g["living"]) == 3
    assert any(k.startswith("kit") for k in g)


def test_validate_blocks_incomplete_bracket():
    issue = validate_hdr_uploads_for_complete(
        [
            {"filename": "a_-1.jpg", "media_type": "hdr"},
            {"filename": "a_0.jpg", "media_type": "hdr"},
        ]
    )
    assert issue is not None
    assert issue["count"] == 2
    assert "Cannot complete" in issue["message"]
    assert issue["stem"] == "a"


def test_validate_ok_full_bracket_ignores_reel_and_visit():
    issue = validate_hdr_uploads_for_complete(
        [
            {"filename": "a_-1.jpg", "media_type": "hdr"},
            {"filename": "a_0.jpg", "media_type": "hdr"},
            {"filename": "a_+1.jpg", "media_type": "hdr"},
            {"filename": "clip.mp4", "media_type": "reel"},
            {"filename": "tour.splat"},
            {"filename": "mesh.ply"},
            {"filename": "cloud.spz"},
        ]
    )
    assert issue is None


def test_validate_skips_drone_and_inferred_non_hdr():
    # Explicit drone
    assert (
        validate_hdr_uploads_for_complete(
            [{"filename": "roof.jpg", "media_type": "drone"}]
        )
        is None
    )
    # Inferred DJI → drone (no media_type column on DB rows)
    assert (
        validate_hdr_uploads_for_complete(
            [{"filename": "DJI_0123.jpg", "mime_type": "image/jpeg"}]
        )
        is None
    )


def test_validate_infers_hdr_when_media_type_missing():
    issue = validate_hdr_uploads_for_complete(
        [{"filename": "living.jpg", "mime_type": "image/jpeg"}]
    )
    assert issue is not None
    assert issue["count"] == 1


def test_validate_allow_incomplete_bypass():
    assert (
        validate_hdr_uploads_for_complete(
            [{"filename": "solo.jpg", "media_type": "hdr"}],
            allow_incomplete=True,
        )
        is None
    )


def test_validate_empty_uploads_ok():
    assert validate_hdr_uploads_for_complete([]) is None
    assert validate_hdr_uploads_for_complete(None) is None


class _QuotaErr(Exception):
    def __str__(self):
        return "402 Payment Required: exceed_storage_size_quota"


def _chain(data=None, raise_on_execute=None):
    class Chain:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def limit(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            if raise_on_execute:
                raise raise_on_execute
            out = MagicMock()
            out.data = data
            return out

    return Chain()


def test_update_vendor_job_status_rejects_incomplete_brackets(monkeypatch):
    job = {
        "id": "job-br",
        "assignee_id": "photog-1",
        "vendor_id": "default_vendor",
        "status": "in_progress",
        "deliverables_count": 0,
        "uploaded_count": 2,
    }
    uploads = [
        {"id": "u1", "filename": "living_-2.jpg", "mime_type": "image/jpeg"},
        {"id": "u2", "filename": "living_0.jpg", "mime_type": "image/jpeg"},
    ]

    class Db:
        def table(self, name):
            if name == "uploads":
                return _chain(data=uploads)
            return _chain(data=job)

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(vos, "ensure_assigned_to_actor", lambda *a, **k: None)
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))

    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            vos.update_vendor_job_status(
                "job-br",
                JobStatusUpdate(status="completed"),
                authorization=None,
                x_user_id="photog-1",
            )
        )
    assert ei.value.status_code == 400
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "incomplete_hdr_brackets"
    assert "Cannot complete" in detail["message"]


def test_update_vendor_job_status_allows_full_brackets(monkeypatch):
    job = {
        "id": "job-ok",
        "assignee_id": "photog-1",
        "vendor_id": "default_vendor",
        "status": "in_progress",
        "deliverables_count": 0,
        "uploaded_count": 3,
        "booking_id": None,
    }
    uploads = [
        {"id": "u1", "filename": "living_-2.jpg", "mime_type": "image/jpeg"},
        {"id": "u2", "filename": "living_0.jpg", "mime_type": "image/jpeg"},
        {"id": "u3", "filename": "living_+2.jpg", "mime_type": "image/jpeg"},
        {"id": "u4", "filename": "tour.splat"},
    ]
    updated = []

    class Db:
        def table(self, name):
            if name == "uploads":
                return _chain(data=uploads)

            class JobChain:
                def update(self, fields):
                    updated.append(fields)
                    return self

                def eq(self, *_a, **_k):
                    return self

                async def execute(self):
                    out = MagicMock()
                    out.data = [{**job, **(updated[-1] if updated else {})}]
                    return out

                def select(self, *_a, **_k):
                    return self

                def maybe_single(self):
                    return self

            return JobChain()

    monkeypatch.setattr(vos, "resolve_vendor_actor_id", AsyncMock(return_value="photog-1"))
    monkeypatch.setattr(
        vos,
        "get_vendor_member",
        AsyncMock(return_value={"id": "photog-1", "vendor_id": "default_vendor"}),
    )
    monkeypatch.setattr(vos, "_load_visible_job", AsyncMock(return_value=job))
    monkeypatch.setattr(vos, "ensure_assigned_to_actor", lambda *a, **k: None)
    monkeypatch.setattr(vos, "get_db", AsyncMock(return_value=Db()))
    # Avoid background post-prod / webhook side effects
    monkeypatch.setattr(vos, "send_dispatch_status_update_webhook", AsyncMock())

    # After update the handler continues into on_time_rate etc. — stub member select path
    # by making vendor_members lookups soft-fail via empty member.

    result = asyncio.run(
        vos.update_vendor_job_status(
            "job-ok",
            JobStatusUpdate(status="completed"),
            authorization=None,
            x_user_id="photog-1",
        )
    )
    assert updated and updated[0]["status"] == "completed"
    assert result is not None
