"""GO /visit/deliver must QC Visit files before they land on the listing.

The Visit file bridge (storage_path from /visit/sign) creates no uploads row,
so job QC never sees it. Without a gate here an empty / error-page /
still-as-.splat file was registered on property_visits, folded into
media_delivery, emailed to the client, and paid out.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fn_src() -> str:
    cap = Path(__file__).resolve().parents[1] / "app/routers/capture.py"
    return cap.read_text()


def test_visit_deliver_source_runs_qc_before_register():
    """Container check must run before register_property_visit / notify / finalize."""
    src = _fn_src()
    fn = src.split("async def deliver_virtual_visit")[1].split("@router.put")[0]
    assert "_visit_file_qc" in fn
    assert "visit_file_rejected" in fn
    assert "visit_bridge=True" in fn
    qc_pos = fn.find("_visit_file_qc")
    reg_pos = fn.find("register_property_visit")
    notify_pos = fn.find("notify_capture_delivered")
    fin_pos = fn.find("visit_bridge=True")
    assert qc_pos != -1 and reg_pos != -1 and qc_pos < reg_pos
    assert notify_pos != -1 and qc_pos < notify_pos
    assert fin_pos != -1 and qc_pos < fin_pos
    # Polycam URL path must not call Visit file QC (no storage_path).
    assert "if storage_path:" in fn
    assert "_visit_file_get_url" in src
    assert "run_visit_qc" in src


def test_visit_file_qc_rejects_before_listing(monkeypatch):
    """Rejected Visit file → 422; no register / notify / finalize."""
    from app.routers import capture as cap
    from fastapi import HTTPException

    job = {"id": "job-v", "address": "1 Test St", "booking_id": "b1"}
    monkeypatch.setattr(
        cap, "_actor_and_job", AsyncMock(return_value=("actor", job))
    )
    monkeypatch.setattr(
        cap,
        "job_capture_entitlements",
        lambda _j: {"visit": True},
    )
    monkeypatch.setattr(cap, "kind_purchased", lambda _j, _k: True)

    async def fake_get_url(_path):
        return "https://example.test/visit.splat"

    async def fake_qc(_url, _path):
        return {
            "qc_status": "rejected",
            "qc_score": 0,
            "qc_notes": "Visit file is a still image, not a Visit asset.",
        }

    monkeypatch.setattr(cap, "_visit_file_get_url", fake_get_url)
    monkeypatch.setattr(cap, "_visit_file_qc", fake_qc)

    register = AsyncMock(return_value=True)
    notify = AsyncMock()
    finalize = AsyncMock()

    with patch(
        "app.services.client_comms.register_property_visit", register
    ), patch(
        "app.services.client_comms.notify_capture_delivered", notify
    ), patch(
        "app.routers.post_production._finalize_job_if_complete", finalize
    ):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(
                cap.deliver_virtual_visit(
                    payload={
                        "job_id": "job-v",
                        "storage_path": "visits/job-v/abc.splat",
                    }
                )
            )

    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "visit_file_rejected"
    assert "still image" in (detail.get("message") or "").lower()
    assert register.await_count == 0
    assert notify.await_count == 0
    assert finalize.await_count == 0


def test_visit_file_qc_pass_registers(monkeypatch):
    """Passed Visit file → register + notify + finalize (existing bridge)."""
    from app.routers import capture as cap

    job = {"id": "job-v", "address": "1 Test St", "booking_id": "b1"}
    monkeypatch.setattr(
        cap, "_actor_and_job", AsyncMock(return_value=("actor", job))
    )
    monkeypatch.setattr(
        cap,
        "job_capture_entitlements",
        lambda _j: {"visit": True},
    )
    monkeypatch.setattr(cap, "kind_purchased", lambda _j, _k: True)

    async def fake_get_url(_path):
        return "https://example.test/visit.ply"

    async def fake_qc(_url, _path):
        return {"qc_status": "passed", "qc_score": 100, "qc_notes": "Visit container OK (PLY)."}

    monkeypatch.setattr(cap, "_visit_file_get_url", fake_get_url)
    monkeypatch.setattr(cap, "_visit_file_qc", fake_qc)

    register = AsyncMock(return_value=True)
    notify = AsyncMock()
    finalize = AsyncMock()

    db = MagicMock()
    jobs_tbl = MagicMock()
    jobs_tbl.update.return_value = jobs_tbl
    jobs_tbl.eq.return_value = jobs_tbl
    jobs_tbl.execute = AsyncMock(return_value=MagicMock(data=None))
    db.table.return_value = jobs_tbl
    monkeypatch.setattr(cap, "get_db", AsyncMock(return_value=db))

    with patch(
        "app.services.client_comms.register_property_visit", register
    ), patch(
        "app.services.client_comms.notify_capture_delivered", notify
    ), patch(
        "app.routers.post_production._finalize_job_if_complete", finalize
    ):
        out = asyncio.run(
            cap.deliver_virtual_visit(
                payload={
                    "job_id": "job-v",
                    "storage_path": "visits/job-v/abc.ply",
                }
            )
        )

    assert out["ok"] is True
    assert out["mode"] == "file"
    assert register.await_count == 1
    assert register.await_args.kwargs.get("storage_path") == "visits/job-v/abc.ply"
    assert notify.await_count == 1
    delivery_urls = notify.await_args.kwargs.get("delivery_urls")
    assert delivery_urls and delivery_urls[0]["kind"] == "visit"
    assert delivery_urls[0]["url"] == "https://example.test/visit.ply"
    assert finalize.await_count == 1
    assert finalize.await_args.kwargs.get("visit_bridge") is True


def test_polycam_url_skips_visit_file_qc(monkeypatch):
    """Polycam / link-only Visit is not a file container — no Visit file QC."""
    from app.routers import capture as cap

    job = {"id": "job-v", "address": "1 Test St", "booking_id": "b1"}
    monkeypatch.setattr(
        cap, "_actor_and_job", AsyncMock(return_value=("actor", job))
    )
    monkeypatch.setattr(
        cap,
        "job_capture_entitlements",
        lambda _j: {"visit": True},
    )
    monkeypatch.setattr(cap, "kind_purchased", lambda _j, _k: True)
    monkeypatch.setattr(
        cap,
        "_normalize_visit_url",
        lambda u: u.strip() if u else "",
    )

    qc = AsyncMock(
        return_value={"qc_status": "rejected", "qc_notes": "should not run"}
    )
    monkeypatch.setattr(cap, "_visit_file_qc", qc)

    register = AsyncMock(return_value=True)
    notify = AsyncMock()
    finalize = AsyncMock()
    db = MagicMock()
    jobs_tbl = MagicMock()
    jobs_tbl.update.return_value = jobs_tbl
    jobs_tbl.eq.return_value = jobs_tbl
    jobs_tbl.execute = AsyncMock(return_value=MagicMock(data=None))
    db.table.return_value = jobs_tbl
    monkeypatch.setattr(cap, "get_db", AsyncMock(return_value=db))

    with patch(
        "app.services.client_comms.register_property_visit", register
    ), patch(
        "app.services.client_comms.notify_capture_delivered", notify
    ), patch(
        "app.routers.post_production._finalize_job_if_complete", finalize
    ):
        out = asyncio.run(
            cap.deliver_virtual_visit(
                payload={
                    "job_id": "job-v",
                    "polycam_url": "https://poly.cam/capture/abc",
                }
            )
        )

    assert out["ok"] is True
    assert out["mode"] == "url"
    assert qc.await_count == 0
    assert register.await_count == 1
    assert finalize.await_count == 1


def test_visit_file_qc_holds_no_url(monkeypatch):
    """No signed GET → hold (image_unavailable), same as run_visit_qc."""
    from app.routers import capture as cap

    out = asyncio.run(cap._visit_file_qc(None, "visits/j/a.ply"))
    assert out["qc_status"] == "rejected"
    assert "image_unavailable" in (out.get("qc_notes") or "").lower()
    assert "skipped" not in (out.get("qc_notes") or "").lower()


@pytest.mark.parametrize(
    "head,total,path,status",
    [
        (b"<!DOCTYPE html><html>Error</html>", 40, "visits/j/a.splat", "rejected"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 60, 64, "visits/j/a.splat", "rejected"),
        (b"", 0, "visits/j/a.ply", "rejected"),
        (b"ply\nformat binary_little_endian 1.0\n", 1024, "visits/j/a.ply", "passed"),
        (b"NGSP" + b"\x00" * 60, 64, "visits/j/a.spz", "passed"),
        (b"\x01" * 64, 64, "visits/j/a.splat", "passed"),
    ],
)
def test_visit_file_qc_container_check(monkeypatch, head, total, path, status):
    """_visit_file_qc uses the same first-bytes check as job Visit QC."""
    from app.routers import capture as cap
    from app.services import qc_media_kinds as qmk

    monkeypatch.setattr(qmk, "fetch_head", AsyncMock(return_value=(head, total)))
    out = asyncio.run(cap._visit_file_qc("https://example.test/x", path))
    assert out["qc_status"] == status


def test_visit_file_qc_fetch_failure_holds(monkeypatch):
    from app.routers import capture as cap
    from app.services import qc_media_kinds as qmk

    monkeypatch.setattr(qmk, "fetch_head", AsyncMock(return_value=(None, None)))
    out = asyncio.run(cap._visit_file_qc("https://example.test/x", "visits/j/a.ply"))
    assert out["qc_status"] == "rejected"
    assert "image_unavailable" in (out.get("qc_notes") or "").lower()


# ── Owned Visit pipeline (2026-10-11): .sog, SPZ v4 hold, multi-room ──

def _zip_head(name: bytes) -> bytes:
    hdr = b"PK\x03\x04" + b"\x14\x00" + b"\x00" * 20 + len(name).to_bytes(2, "little") + b"\x00\x00"
    return hdr + name + b"RIFF" + b"\x00" * 40


def test_visit_qc_sog_zip_passes():
    from app.services.qc_media_kinds import check_visit_head

    v = check_visit_head(_zip_head(b"means_l.webp"), 4_000_000, "visits/j/a.sog")
    assert v["qc_status"] == "passed", v
    v = check_visit_head(_zip_head(b"meta.json"), 4_000_000, "visits/j/a.sog")
    assert v["qc_status"] == "passed", v


def test_visit_qc_sog_rejects_non_sog_zip_and_junk():
    from app.services.qc_media_kinds import check_visit_head

    assert check_visit_head(_zip_head(b"photos/IMG_1.jpg"), 4_000_000, "visits/j/a.sog")["qc_status"] == "rejected"
    assert check_visit_head(b"\x00" * 64, 64, "visits/j/a.sog")["qc_status"] == "rejected"


def test_visit_qc_spz_v4_held_v2_passes():
    from app.services.qc_media_kinds import check_visit_head

    v4 = b"NGSP" + (4).to_bytes(4, "little") + b"\x00" * 56
    v = check_visit_head(v4, 64, "visits/j/a.spz")
    assert v["qc_status"] == "rejected" and ".sog" in v["qc_notes"]
    gz = b"\x1f\x8b" + b"\x00" * 62
    assert check_visit_head(gz, 64, "visits/j/a.spz")["qc_status"] == "passed"


def test_visit_deliver_accepts_sog_rooms_and_pipeline_qc():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "routers" / "capture.py").read_text()
    assert 'VISIT_FILE_EXTS = ("sog", "spz", "ply", "splat")' in src
    assert "visit_pipeline_qc_held" in src
    assert "rooms=rooms or None" in src
    assert "x_internal_api_key" in src.split("async def deliver_virtual_visit")[1][:600]


def test_visit_sign_pipeline_worker_rooms_and_floor_plan(monkeypatch):
    """Pipeline worker signs with X-Internal-Api-Key; floor plans get their own kind."""
    from app.routers import capture as cap
    from fastapi import HTTPException

    job = {"id": "job-p"}
    seen = {}

    async def fake_actor(auth, uid, job_id, key=None):
        seen["key"] = key
        return ("pipeline", job)

    async def fake_sign(path):
        return {"signed_url": "https://storage.test/" + path, "token": "t"}

    monkeypatch.setattr(cap, "_actor_and_job", fake_actor)
    monkeypatch.setattr(cap, "job_capture_entitlements", lambda _j: {"visit": True})
    monkeypatch.setattr(cap, "kind_purchased", lambda _j, _k: True)
    monkeypatch.setattr(cap, "create_signed_upload_url", fake_sign)

    room = asyncio.run(cap.sign_visit_upload({"job_id": "job-p", "filename": "room-0.sog"}, None, None, "k"))
    assert seen["key"] == "k"
    assert room["storage_path"].startswith("visits/job-p/") and room["storage_path"].endswith(".sog")
    plan = asyncio.run(
        cap.sign_visit_upload({"job_id": "job-p", "filename": "floor-0.svg", "kind": "floor_plan"}, None, None, "k")
    )
    assert plan["storage_path"].endswith("-floor.svg") and plan["content_type"] == "image/svg+xml"
    with pytest.raises(HTTPException):
        asyncio.run(cap.sign_visit_upload({"job_id": "job-p", "filename": "x.svg"}, None, None, "k"))
    with pytest.raises(HTTPException):
        asyncio.run(cap.sign_visit_upload({"job_id": "job-p", "filename": "x.exe", "kind": "floor_plan"}, None, None, "k"))
