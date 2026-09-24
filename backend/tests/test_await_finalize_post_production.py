"""Finalize must await (Vercel-safe) and must not qc_fail before Visit registration."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers import post_production as pp


def test_callbacks_and_reconcile_await_finalize_not_background_tasks():
    """Hollow media_delivery if _finalize_job_if_complete is only scheduled via BackgroundTasks."""
    src = Path(pp.__file__).read_text()
    assert "background_tasks.add_task(_finalize_job_if_complete" not in src
    assert "await _finalize_job_if_complete" in src
    # Callbacks + reconcile loop
    assert src.count("await _finalize_job_if_complete") >= 4
    # PIL-rescued jobs must be tracked for finalize (not only remaining stuck)
    assert "job_ids_to_finalize" in src


def test_finalize_source_picks_visit_before_qc_failed_gate():
    """Visit-only / all-stills-rejected must not early-return before visit-ready."""
    src = Path(pp.__file__).read_text()
    fn = src.split("async def _finalize_job_if_complete")[1].split("async def _patch_media_delivery")[0]
    # pick happens before qc_failed
    pick_idx = fn.find("splat_upload = pick_visit_upload")
    fail_idx = fn.find('status": "qc_failed"')
    assert pick_idx != -1 and fail_idx != -1
    assert pick_idx < fail_idx
    assert "not delivery_urls and not splat_upload" in fn
    assert "register_property_visit returned False" in fn
    assert "delivering Visit-only" in fn


def _uploads_chain(rows):
    """Fake supabase-style chain ending in .execute()."""
    result = MagicMock()
    result.data = rows
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.execute = AsyncMock(return_value=result)
    chain.update.return_value = chain
    chain.upsert.return_value = chain
    return chain


def test_finalize_visit_only_delivers_and_registers(monkeypatch):
    """Visit-only job: empty listing stills must still deliver + call visit-ready."""
    visit_row = {
        "id": "up-v",
        "filename": "room.ply",
        "file_url": "visits/job-v/room.ply",
        "processed_url": "visits/job-v/room.ply",
        "processing_status": "done",
        "qc_status": None,
        "provider": "visit",
    }

    db = MagicMock()
    # uploads select (twice: initial + post-QC), media_delivery maybe_single empty,
    # media_delivery upsert, jobs update
    uploads_tbl = _uploads_chain([visit_row])
    md_empty = MagicMock()
    md_empty.data = None
    md_tbl = MagicMock()
    md_tbl.select.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.maybe_single.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=md_empty)
    md_tbl.upsert.return_value = md_tbl

    jobs_tbl = MagicMock()
    jobs_tbl.update.return_value = jobs_tbl
    jobs_tbl.eq.return_value = jobs_tbl
    jobs_tbl.execute = AsyncMock(return_value=MagicMock(data=None))
    jobs_tbl.select.return_value = jobs_tbl
    jobs_tbl.maybe_single.return_value = jobs_tbl
    # booking lookup returns empty → skip notify
    jobs_tbl.execute = AsyncMock(side_effect=[
        MagicMock(data=None),  # update delivered
        MagicMock(data={}),    # select for notify
    ])

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "jobs":
            return jobs_tbl
        return MagicMock()

    db.table.side_effect = table

    register_calls = []

    async def fake_register(job_id, storage_path="", **kwargs):
        register_calls.append((job_id, storage_path, kwargs))
        return True

    async def fake_qc(_job_id):
        return {"approved": 0, "rejected": 0, "flagged": 0}

    async def fake_payout(_db, _job_id):
        return None

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)

    with patch("app.services.client_comms.register_property_visit", fake_register), \
         patch("app.services.payout_service.trigger_payout_for_job", fake_payout):
        asyncio.run(pp._finalize_job_if_complete("job-v"))

    assert register_calls == [("job-v", "visits/job-v/room.ply", {})]
    assert jobs_tbl.update.call_args[0][0]["status"] == "delivered"


def test_finalize_no_stills_no_visit_marks_qc_failed(monkeypatch):
    """Truly empty delivery (rejected still only) stays qc_failed."""
    rejected = {
        "id": "up-r",
        "filename": "bad.jpg",
        "file_url": "jobs/j/bad.jpg",
        "processed_url": "jobs/j/bad.jpg",
        "processing_status": "done",
        "qc_status": "rejected",
        "provider": "bricks_inhouse",
    }
    db = MagicMock()
    uploads_tbl = _uploads_chain([rejected])
    md_empty = MagicMock()
    md_empty.data = None
    md_tbl = MagicMock()
    md_tbl.select.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.maybe_single.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=md_empty)

    jobs_tbl = MagicMock()
    jobs_tbl.update.return_value = jobs_tbl
    jobs_tbl.eq.return_value = jobs_tbl
    jobs_tbl.execute = AsyncMock(return_value=MagicMock())

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "jobs":
            return jobs_tbl
        return MagicMock()

    db.table.side_effect = table
    register_calls = []

    async def fake_register(*a, **k):
        register_calls.append(1)
        return True

    async def fake_qc(_job_id):
        return {}

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)

    with patch("app.services.client_comms.register_property_visit", fake_register):
        asyncio.run(pp._finalize_job_if_complete("job-r"))

    assert register_calls == []
    update_payload = jobs_tbl.update.call_args[0][0]
    assert update_payload["status"] == "qc_failed"
