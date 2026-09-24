"""HDR bracket gate for Mark complete — parity with web/frontend bracket-require."""
from __future__ import annotations

from pathlib import Path

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.bracket_require import (
    BRACKET_HINT,
    HDR_BRACKET_MIN,
    basename,
    bracket_has_under_mid_over,
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




def test_bracket_has_under_mid_over():
    assert bracket_has_under_mid_over(["a_-2.jpg", "a_0.jpg", "a_+2.jpg"])
    assert bracket_has_under_mid_over(["kit_under.jpg", "kit_0.jpg", "kit_over.jpg"])
    assert not bracket_has_under_mid_over(["a_+1.jpg", "a_+2.jpg", "a_+3.jpg"])
    assert not bracket_has_under_mid_over(["a_-1.jpg", "a_-2.jpg", "a_0.jpg"])  # no over
    assert not bracket_has_under_mid_over(["a_1.jpg", "a_2.jpg", "a_0.jpg"])  # no under
    assert not bracket_has_under_mid_over(["plain.jpg", "plain2.jpg", "plain3.jpg"])


def test_validate_blocks_same_side_three_frame_bracket():
    """Count≥3 is not enough — need real − / 0 / + coverage."""
    issue = validate_hdr_uploads_for_complete(
        [
            {"filename": "room_+1.jpg", "media_type": "hdr"},
            {"filename": "room_+2.jpg", "media_type": "hdr"},
            {"filename": "room_+3.jpg", "media_type": "hdr"},
        ]
    )
    assert issue is not None
    assert issue["count"] == 3
    assert "under, mid, and over" in issue["message"]


def test_validate_ok_full_ev_ignores_incomplete_drone_stem():
    """GO/API parity: DJI frames must not false-block beside a full HDR set.

    media_type is not a DB column — infer drone from filename so Mark complete
    does not treat DJI_0123.jpg as an incomplete HDR stem.
    """
    issue = validate_hdr_uploads_for_complete(
        [
            {"filename": "living_-2.jpg", "mime_type": "image/jpeg"},
            {"filename": "living_0.jpg", "mime_type": "image/jpeg"},
            {"filename": "living_+2.jpg", "mime_type": "image/jpeg"},
            {"filename": "DJI_0123.jpg", "mime_type": "image/jpeg"},
        ]
    )
    assert issue is None


def test_list_uploads_annotates_inferred_media_type():
    from app.routers.uploads import _annotate_inferred_media_type

    rows = _annotate_inferred_media_type(
        [
            {"id": "1", "filename": "DJI_9.jpg", "mime_type": "image/jpeg"},
            {"id": "2", "filename": "living_0.jpg", "mime_type": "image/jpeg", "media_type": "hdr"},
            {"id": "3", "filename": "clip.mp4", "mime_type": "video/mp4"},
        ]
    )
    assert rows[0]["media_type"] == "drone"
    assert rows[1]["media_type"] == "hdr"
    assert rows[2]["media_type"] == "reel"


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
    post_prod = AsyncMock()
    monkeypatch.setattr(
        "app.services.post_production.process_job_assets",
        post_prod,
    )

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
    post_prod.assert_awaited_once_with("job-ok")


def test_mark_complete_awaits_process_job_assets_not_create_task():
    """Regression: Vercel freezes create_task after response — Mark complete must await."""
    src = Path(vos.__file__).read_text()
    # Narrow to the completed→post-prod block
    assert "await process_job_assets(job_id)" in src
    # The hollow fire-and-forget must not remain on this path
    assert "create_task(process_job_assets" not in src

