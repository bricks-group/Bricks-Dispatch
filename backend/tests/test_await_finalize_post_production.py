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



def _capture_sessions_chain(rows):
    result = MagicMock()
    result.data = rows
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.execute = AsyncMock(return_value=result)
    return chain

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

    cap_tbl = _capture_sessions_chain([])

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "jobs":
            return jobs_tbl
        if name == "capture_sessions":
            return cap_tbl
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

    cap_tbl = _capture_sessions_chain([])

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "jobs":
            return jobs_tbl
        if name == "capture_sessions":
            return cap_tbl
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


def test_finalize_capture_only_floorplan_delivers(monkeypatch):
    """Floor-plan-only: no uploads + delivered capture_session → delivered + media_delivery."""
    fp = {
        "id": "sess-fp",
        "kind": "floorplan",
        "status": "delivered",
        "output_storage_path": "capture/job-fp/sess-fp/plan.png",
    }
    db = MagicMock()
    uploads_tbl = _uploads_chain([])
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
    jobs_tbl.select.return_value = jobs_tbl
    jobs_tbl.maybe_single.return_value = jobs_tbl
    jobs_tbl.execute = AsyncMock(side_effect=[
        MagicMock(data=None),  # update delivered
        MagicMock(data={}),    # select for notify
    ])

    # floorplan kind query returns fp; splat kind returns []
    call_n = {"n": 0}
    def cap_execute():
        call_n["n"] += 1
        # two kinds queried; return fp on first (floorplan), empty on second
        if call_n["n"] == 1:
            return AsyncMock(return_value=MagicMock(data=[fp]))()
        return AsyncMock(return_value=MagicMock(data=[]))()

    cap_tbl = MagicMock()
    cap_tbl.select.return_value = cap_tbl
    cap_tbl.eq.return_value = cap_tbl
    async def cap_exec():
        call_n["n"] += 1
        if call_n["n"] % 2 == 1:
            return MagicMock(data=[fp])
        return MagicMock(data=[])
    cap_tbl.execute = cap_exec

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "jobs":
            return jobs_tbl
        if name == "capture_sessions":
            return cap_tbl
        return MagicMock()

    db.table.side_effect = table

    async def fake_qc(_job_id):
        return {}

    async def fake_payout(_db, _job_id):
        return None

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(
        pp,
        "_delivery_urls_from_capture_sessions",
        lambda job_id, sessions: [
            {"url": f"https://api.brxs.ca/api/public/floorplans/{job_id}/sess-fp",
             "filename": "Floor plan", "session_id": "sess-fp", "kind": "floorplan"}
        ] if sessions else [],
    )

    with patch("app.services.payout_service.trigger_payout_for_job", fake_payout):
        asyncio.run(pp._finalize_job_if_complete("job-fp"))

    assert jobs_tbl.update.call_args_list[0][0][0]["status"] == "delivered"
    upsert_row = md_tbl.upsert.call_args[0][0]
    assert upsert_row["job_id"] == "job-fp"
    assert any("floorplans" in (u.get("url") or "") for u in upsert_row["delivery_urls"])


def test_finalize_no_uploads_no_capture_unsticks_processing(monkeypatch):
    """Mark complete with nothing ready must leave completed, not processing."""
    db = MagicMock()
    uploads_tbl = _uploads_chain([])
    cap_tbl = _capture_sessions_chain([])

    jobs_tbl = MagicMock()
    jobs_tbl.update.return_value = jobs_tbl
    jobs_tbl.eq.return_value = jobs_tbl
    jobs_tbl.execute = AsyncMock(return_value=MagicMock())

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "capture_sessions":
            return cap_tbl
        if name == "jobs":
            return jobs_tbl
        return MagicMock()

    db.table.side_effect = table
    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))

    asyncio.run(pp._finalize_job_if_complete("job-empty"))

    # unstick path updates jobs status=completed (via _unstick)
    assert jobs_tbl.update.call_args[0][0]["status"] == "completed"


def test_finalize_source_mentions_capture_only():
    src = Path(pp.__file__).read_text()
    assert "_list_delivered_capture_sessions" in src
    assert "capture-only finalize" in src
    assert "_unstick_processing_without_delivery" in src
    assert "_fold_capture_into_existing_delivery" in src
    assert "_merge_missing_capture_urls" in src


def test_merge_missing_capture_urls_appends_by_session_id():
    existing = [
        {"upload_id": "u1", "url": "https://cdn.example/a.jpg", "filename": "a.jpg"},
        {"url": "https://api.brxs.ca/api/public/floorplans/j/sess-old",
         "filename": "Floor plan", "session_id": "sess-old", "kind": "floorplan"},
    ]
    capture = [
        {"url": "https://api.brxs.ca/api/public/floorplans/j/sess-old",
         "filename": "Floor plan", "session_id": "sess-old", "kind": "floorplan"},
        {"url": "https://api.brxs.ca/api/public/tours/j/sess-tour",
         "filename": "3D tour", "session_id": "sess-tour", "kind": "splat"},
    ]
    merged = pp._merge_missing_capture_urls(existing, capture)
    assert merged is not None
    assert len(merged) == 3
    assert merged[-1]["session_id"] == "sess-tour"
    assert pp._merge_missing_capture_urls(merged, capture) is None


def test_merge_missing_capture_urls_parses_json_string():
    import json
    existing = json.dumps([{"upload_id": "u1", "url": "https://cdn.example/a.jpg"}])
    capture = [{
        "url": "https://api.brxs.ca/api/public/floorplans/j/sess-fp",
        "filename": "Floor plan", "session_id": "sess-fp", "kind": "floorplan",
    }]
    merged = pp._merge_missing_capture_urls(existing, capture)
    assert merged is not None
    assert len(merged) == 2
    assert merged[1]["kind"] == "floorplan"


def test_finalize_folds_capture_when_media_delivery_exists(monkeypatch):
    """Stills already delivered: late floorplan must patch media_delivery, not skip forever."""
    existing_row = {
        "id": "md-1",
        "delivery_urls": [
            {"upload_id": "u1", "url": "https://cdn.example/kitchen.jpg", "filename": "kitchen.jpg"},
        ],
    }
    fp = {
        "id": "sess-fp",
        "kind": "floorplan",
        "status": "delivered",
        "output_storage_path": "capture/job-1/sess-fp/plan.png",
    }
    db = MagicMock()
    uploads_tbl = _uploads_chain([
        {"id": "u1", "filename": "kitchen.jpg", "file_url": "k.jpg",
         "processed_url": "https://cdn.example/kitchen.jpg",
         "processing_status": "done", "qc_status": "accepted", "provider": None},
    ])
    md_row = MagicMock()
    md_row.data = existing_row
    md_tbl = MagicMock()
    md_tbl.select.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.maybe_single.return_value = md_tbl
    md_tbl.update.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=md_row)

    call_n = {"n": 0}
    cap_tbl = MagicMock()
    cap_tbl.select.return_value = cap_tbl
    cap_tbl.eq.return_value = cap_tbl
    async def cap_exec():
        call_n["n"] += 1
        if call_n["n"] % 2 == 1:
            return MagicMock(data=[fp])
        return MagicMock(data=[])
    cap_tbl.execute = cap_exec

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "capture_sessions":
            return cap_tbl
        return MagicMock()

    db.table.side_effect = table
    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(
        pp,
        "_delivery_urls_from_capture_sessions",
        lambda job_id, sessions: [
            {"url": f"https://api.brxs.ca/api/public/floorplans/{job_id}/sess-fp",
             "filename": "Floor plan", "session_id": "sess-fp", "kind": "floorplan"}
        ] if sessions else [],
    )

    asyncio.run(pp._finalize_job_if_complete("job-1"))

    # Must UPDATE delivery_urls (fold), never upsert a brand-new delivery / QC / payout
    assert md_tbl.update.called
    patch_fields = md_tbl.update.call_args[0][0]
    assert "delivery_urls" in patch_fields
    urls = patch_fields["delivery_urls"]
    assert any(u.get("session_id") == "sess-fp" for u in urls)
    assert any(u.get("upload_id") == "u1" for u in urls)
    assert not md_tbl.upsert.called
