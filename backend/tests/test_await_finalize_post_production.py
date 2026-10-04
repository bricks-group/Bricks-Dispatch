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
    assert "not delivery_urls and not splat_upload and not visit_bridge" in fn
    assert "register_property_visit returned False" in fn
    assert "delivering Visit-only" in fn
    assert "visit_bridge" in fn
    assert "Visit-bridge finalize" in fn



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
    assert "_fold_late_into_existing_delivery" in src
    assert "_fold_capture_into_existing_delivery" in src  # alias
    assert "_merge_missing_capture_urls" in src
    assert "_merge_missing_still_urls" in src
    assert "_listing_still_delivery_urls" in src


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


def test_merge_missing_still_urls_appends_by_upload_id():
    existing = [
        {"url": "https://api.brxs.ca/api/public/floorplans/j/sess-fp",
         "filename": "Floor plan", "session_id": "sess-fp", "kind": "floorplan"},
    ]
    stills = [
        {"upload_id": "u1", "url": "https://cdn.example/a.jpg", "filename": "a.jpg"},
        {"upload_id": "u2", "url": "https://cdn.example/b.jpg", "filename": "b.jpg"},
    ]
    merged = pp._merge_missing_still_urls(existing, stills)
    assert merged is not None
    assert len(merged) == 3
    assert merged[1]["upload_id"] == "u1"
    assert merged[2]["upload_id"] == "u2"
    # idempotent
    assert pp._merge_missing_still_urls(merged, stills) is None


def test_merge_missing_still_urls_skips_already_present():
    existing = [
        {"upload_id": "u1", "url": "https://cdn.example/a.jpg", "filename": "a.jpg"},
    ]
    stills = [
        {"upload_id": "u1", "url": "https://cdn.example/a-new.jpg", "filename": "a.jpg"},
        {"upload_id": "u2", "url": "https://cdn.example/b.jpg", "filename": "b.jpg"},
    ]
    merged = pp._merge_missing_still_urls(existing, stills)
    assert merged is not None
    assert len(merged) == 2
    assert merged[1]["upload_id"] == "u2"


def test_merge_still_urls_backfills_hollow_kind():
    """Late fold must stamp kind onto already-present hollow delivery_urls rows."""
    existing = [
        {"upload_id": "u-drone", "url": "https://cdn.example/dji.jpg", "filename": "DJI_0001.jpg"},
        {"upload_id": "u-photo", "url": "https://cdn.example/a.jpg", "filename": "a.jpg"},
    ]
    stills = [
        {"upload_id": "u-drone", "url": "https://cdn.example/dji.jpg",
         "filename": "DJI_0001.jpg", "kind": "drone", "qc_flagged": False},
        {"upload_id": "u-photo", "url": "https://cdn.example/a.jpg",
         "filename": "a.jpg", "qc_flagged": False},
    ]
    merged = pp._merge_missing_still_urls(existing, stills)
    assert merged is not None
    assert len(merged) == 2
    assert merged[0]["kind"] == "drone"
    assert merged[0].get("qc_flagged") is False
    assert "kind" not in merged[1]  # HDR/plain photo stays untagged
    # do not overwrite non-empty kind
    existing2 = [
        {"upload_id": "u-drone", "url": "https://cdn.example/dji.jpg",
         "filename": "DJI_0001.jpg", "kind": "reel"},
    ]
    stills2 = [
        {"upload_id": "u-drone", "url": "https://cdn.example/dji.jpg",
         "filename": "DJI_0001.jpg", "kind": "drone"},
    ]
    assert pp._merge_missing_still_urls(existing2, stills2) is None
    # idempotent after backfill
    assert pp._merge_missing_still_urls(merged, stills) is None


def test_merge_still_urls_prunes_qc_rejected():
    """QC-rejected upload_id must leave delivery_urls; session/Visit rows stay."""
    existing = [
        {"url": "https://api.brxs.ca/api/public/floorplans/j/sess-fp",
         "filename": "Floor plan", "session_id": "sess-fp", "kind": "floorplan"},
        {"upload_id": "u-bad", "url": "https://cdn.example/bad.jpg", "filename": "bad.jpg"},
        {"upload_id": "u-ok", "url": "https://cdn.example/ok.jpg", "filename": "ok.jpg"},
        {"url": "https://visit.example/v", "filename": "Virtual Visit", "kind": "visit"},
    ]
    stills = [
        {"upload_id": "u-ok", "url": "https://cdn.example/ok.jpg", "filename": "ok.jpg"},
    ]
    merged = pp._merge_missing_still_urls(
        existing, stills, rejected_ids={"u-bad"}
    )
    assert merged is not None
    uids = [u.get("upload_id") for u in merged if isinstance(u, dict)]
    assert "u-bad" not in uids
    assert "u-ok" in uids
    assert any(u.get("session_id") == "sess-fp" for u in merged)
    assert any(u.get("kind") == "visit" for u in merged)
    # idempotent
    assert pp._merge_missing_still_urls(merged, stills, rejected_ids={"u-bad"}) is None


def test_merge_still_urls_append_kind_backfill_and_prune_together():
    """Append + kind backfill + prune each make a durable change."""
    existing = [
        {"url": "https://api.brxs.ca/api/public/tours/j/sess-t",
         "filename": "3D tour", "session_id": "sess-t", "kind": "splat"},
        {"upload_id": "u-rej", "url": "https://cdn.example/rej.jpg", "filename": "rej.jpg"},
        {"upload_id": "u-hollow", "url": "https://cdn.example/dji.jpg",
         "filename": "DJI_0002.jpg"},
    ]
    stills = [
        {"upload_id": "u-hollow", "url": "https://cdn.example/dji.jpg",
         "filename": "DJI_0002.jpg", "kind": "drone", "qc_flagged": False},
        {"upload_id": "u-new", "url": "https://cdn.example/new.jpg",
         "filename": "new.jpg"},
    ]
    merged = pp._merge_missing_still_urls(
        existing, stills, rejected_ids={"u-rej"}
    )
    assert merged is not None
    assert any(u.get("session_id") == "sess-t" for u in merged)
    assert not any(u.get("upload_id") == "u-rej" for u in merged)
    hollow = next(u for u in merged if u.get("upload_id") == "u-hollow")
    assert hollow["kind"] == "drone"
    assert any(u.get("upload_id") == "u-new" for u in merged)
    assert len(merged) == 3  # tour + hollow + new
    # second pass: nothing left
    assert pp._merge_missing_still_urls(
        merged, stills, rejected_ids={"u-rej"}
    ) is None


def test_finalize_folds_stills_when_capture_only_media_delivery_exists(monkeypatch):
    """Capture-first media_delivery: late enhance stills must patch delivery_urls."""
    existing_row = {
        "id": "md-cap",
        "delivery_urls": [
            {"url": "https://api.brxs.ca/api/public/floorplans/job-2/sess-fp",
             "filename": "Floor plan", "session_id": "sess-fp", "kind": "floorplan"},
        ],
    }
    still = {
        "id": "u-new",
        "filename": "living.jpg",
        "file_url": "raw/living.jpg",
        "processed_url": "https://cdn.example/living.jpg",
        "processing_status": "done",
        "qc_status": "accepted",
        "provider": None,
    }
    db = MagicMock()
    uploads_tbl = _uploads_chain([still])
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
        lambda job_id, sessions: [],
    )

    asyncio.run(pp._finalize_job_if_complete("job-2"))

    assert md_tbl.update.called
    patch_fields = md_tbl.update.call_args[0][0]
    urls = patch_fields["delivery_urls"]
    assert any(u.get("upload_id") == "u-new" for u in urls)
    assert any(u.get("session_id") == "sess-fp" for u in urls)
    assert not md_tbl.upsert.called


def test_finalize_folds_reconciled_still_into_partial_delivery(monkeypatch):
    """Partial stills already delivered: reconcile-done still must fold in."""
    existing_row = {
        "id": "md-partial",
        "delivery_urls": [
            {"upload_id": "u1", "url": "https://cdn.example/a.jpg", "filename": "a.jpg"},
        ],
    }
    rows = [
        {
            "id": "u1",
            "filename": "a.jpg",
            "file_url": "a.jpg",
            "processed_url": "https://cdn.example/a.jpg",
            "processing_status": "done",
            "qc_status": "accepted",
            "provider": None,
        },
        {
            "id": "u2",
            "filename": "b.jpg",
            "file_url": "b.jpg",
            "processed_url": "https://cdn.example/b.jpg",
            "processing_status": "done",
            "qc_status": "accepted",
            "provider": None,
        },
    ]
    db = MagicMock()
    uploads_tbl = _uploads_chain(rows)
    md_row = MagicMock()
    md_row.data = existing_row
    md_tbl = MagicMock()
    md_tbl.select.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.maybe_single.return_value = md_tbl
    md_tbl.update.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=md_row)

    cap_tbl = MagicMock()
    cap_tbl.select.return_value = cap_tbl
    cap_tbl.eq.return_value = cap_tbl
    cap_tbl.execute = AsyncMock(return_value=MagicMock(data=[]))

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
    monkeypatch.setattr(pp, "_delivery_urls_from_capture_sessions", lambda *a, **k: [])

    asyncio.run(pp._finalize_job_if_complete("job-3"))

    assert md_tbl.update.called
    urls = md_tbl.update.call_args[0][0]["delivery_urls"]
    assert {u.get("upload_id") for u in urls if u.get("upload_id")} == {"u1", "u2"}
    assert not md_tbl.upsert.called


def test_finalize_source_visit_bridge_skips_unstick_and_double_notify():
    """Polycam /visit/deliver path must not unstick without media_delivery."""
    src = Path(pp.__file__).read_text()
    fn = src.split("async def _finalize_job_if_complete")[1].split("async def _patch_media_delivery")[0]
    assert "visit_bridge: bool = False" in src.split("async def _finalize_job_if_complete")[0][-80:] + src.split("async def _finalize_job_if_complete")[1][:200] or "visit_bridge: bool = False" in src
    assert "and not visit_bridge" in fn  # empty-uploads unstick gated
    assert "if splat_upload and not visit_bridge:" in fn
    assert "if not visit_bridge:" in fn
    assert "_send_delivery_notification" in fn


def test_visit_deliver_awaits_finalize_with_visit_bridge():
    """GO /visit/deliver must finalize like floor/tour compile (media_delivery + payout)."""
    cap = Path(__file__).resolve().parents[1] / "app/routers/capture.py"
    src = cap.read_text()
    fn = src.split("async def deliver_virtual_visit")[1].split("@router.put")[0]
    assert "visit_bridge=True" in fn
    assert "await _finalize_job_if_complete" in fn
    # After successful register, before return
    reg_fail = fn.find("visit_register_failed")
    fin = fn.find("visit_bridge=True")
    assert reg_fail != -1 and fin != -1 and fin > reg_fail


def test_finalize_visit_bridge_no_uploads_delivers(monkeypatch):
    """Visit Polycam bridge: no uploads → media_delivery + delivered + payout; no re-register/email."""
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
    jobs_tbl.execute = AsyncMock(return_value=MagicMock(data=None))

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
    notify_calls = []
    payout_calls = []

    async def fake_register(job_id, storage_path="", **kwargs):
        register_calls.append((job_id, storage_path, kwargs))
        return True

    async def fake_qc(_job_id):
        return {"approved": 0, "rejected": 0, "flagged": 0}

    async def fake_payout(_db, job_id):
        payout_calls.append(job_id)
        return None

    async def fake_notify(job_id, delivery_urls):
        notify_calls.append((job_id, delivery_urls))

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(pp, "_send_delivery_notification", fake_notify)

    with patch("app.services.client_comms.register_property_visit", fake_register), \
         patch("app.services.payout_service.trigger_payout_for_job", fake_payout):
        asyncio.run(pp._finalize_job_if_complete("job-vb", visit_bridge=True))

    assert register_calls == []
    assert notify_calls == []
    assert payout_calls == ["job-vb"]
    assert md_tbl.upsert.called
    upsert_row = md_tbl.upsert.call_args[0][0]
    assert upsert_row["job_id"] == "job-vb"
    assert upsert_row["delivery_urls"] == []
    assert jobs_tbl.update.call_args[0][0]["status"] == "delivered"


def test_normalize_and_merge_visit_delivery_urls():
    """Virtual Visit rows stamp kind=visit and fold once into delivery_urls."""
    from app.routers.post_production import (
        _merge_missing_visit_urls,
        _normalize_visit_delivery_urls,
    )

    norm = _normalize_visit_delivery_urls(
        [{"url": "https://poly.cam/capture/x", "filename": "Virtual Visit"}]
    )
    assert len(norm) == 1
    assert norm[0]["kind"] == "visit"
    assert norm[0]["url"] == "https://poly.cam/capture/x"

    existing = [
        {"upload_id": "up-1", "filename": "living.jpg", "url": "https://cdn/a.jpg"},
    ]
    merged = _merge_missing_visit_urls(existing, norm)
    assert merged is not None
    assert len(merged) == 2
    assert merged[-1]["kind"] == "visit"
    # Idempotent — already has visit
    assert _merge_missing_visit_urls(merged, norm) is None


def test_finalize_visit_bridge_folds_polycam_into_delivery(monkeypatch):
    """Visit-bridge finalize must put kind=visit polycam URL into media_delivery."""
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
    jobs_tbl.execute = AsyncMock(return_value=MagicMock(data=None))

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

    async def fake_qc(_job_id):
        return {"approved": 0, "rejected": 0, "flagged": 0}

    async def fake_payout(_db, job_id):
        return None

    async def fake_notify(job_id, delivery_urls):
        pass

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(pp, "_send_delivery_notification", fake_notify)

    visit_urls = [
        {"url": "https://poly.cam/capture/bridge99", "filename": "Virtual Visit", "kind": "visit"}
    ]
    with patch("app.services.client_comms.register_property_visit", AsyncMock(return_value=True)), \
         patch("app.services.payout_service.trigger_payout_for_job", fake_payout):
        asyncio.run(
            pp._finalize_job_if_complete(
                "job-vb2",
                visit_bridge=True,
                visit_delivery_urls=visit_urls,
            )
        )

    upsert_row = md_tbl.upsert.call_args[0][0]
    urls = upsert_row["delivery_urls"]
    assert len(urls) == 1
    assert urls[0]["kind"] == "visit"
    assert urls[0]["url"] == "https://poly.cam/capture/bridge99"


def test_visit_deliver_passes_visit_delivery_urls_to_finalize():
    from app.routers import capture as capture_mod

    src = Path(capture_mod.__file__).read_text()
    fn = src.split("async def deliver_virtual_visit")[1].split("@router.put")[0]
    assert "visit_delivery_urls=delivery_urls" in fn
    assert '"kind": "visit"' in fn or "'kind': 'visit'" in fn

def test_visit_file_deliver_always_folds_kind_visit():
    """GO .ply/.splat/.spz path must fold kind=visit (+ storage_path) like Polycam URL."""
    from app.routers import capture as capture_mod

    src = Path(capture_mod.__file__).read_text()
    fn = src.split("async def deliver_virtual_visit")[1].split("@router.put")[0]
    assert '"kind": "visit"' in fn or "'kind': 'visit'" in fn
    assert "storage_path" in fn
    assert "MAX_GET_EXPIRY" in fn
    assert "delivery_urls.append(entry)" in fn
    # Never leave file path with empty delivery_urls when register ok
    assert "delivery_urls = []" in fn  # init still there
    assert 'entry = {' in fn


def test_normalize_visit_keeps_storage_path_without_url():
    """File-bridge rows with only storage_path must still fold into media_delivery."""
    from app.routers.post_production import _normalize_visit_delivery_urls

    norm = _normalize_visit_delivery_urls(
        [{"storage_path": "visits/job-1/a.splat", "filename": "Virtual Visit", "kind": "visit"}]
    )
    assert len(norm) == 1
    assert norm[0]["kind"] == "visit"
    assert norm[0]["storage_path"] == "visits/job-1/a.splat"
    assert "url" not in norm[0] or not norm[0].get("url")


def test_visit_sign_returns_bucket():
    from app.routers import capture as capture_mod

    src = Path(capture_mod.__file__).read_text()
    fn = src.split("async def sign_visit_upload")[1].split("async def deliver_virtual_visit")[0]
    assert '"bucket": STORAGE_BUCKET' in fn or "'bucket': STORAGE_BUCKET" in fn



def test_late_fold_runs_qc_and_skips_rejected_still(monkeypatch):
    """Late-fold: done still missing qc_status → run_qc_for_job; rejected not folded."""
    existing = {
        "id": "md-late",
        "delivery_urls": [
            {
                "url": "https://api.brxs.ca/api/public/floorplans/job-qc/sess-fp",
                "filename": "Floor plan",
                "session_id": "sess-fp",
                "kind": "floorplan",
            },
        ],
    }
    still_pre = {
        "id": "u-late",
        "filename": "living.jpg",
        "file_url": "raw/living.jpg",
        "processed_url": "https://cdn.example/living.jpg",
        "processing_status": "done",
        "qc_status": None,
        "provider": None,
    }
    still_post = {**still_pre, "qc_status": "rejected"}

    fetch_n = {"n": 0}

    uploads_tbl = MagicMock()
    uploads_tbl.select.return_value = uploads_tbl
    uploads_tbl.eq.return_value = uploads_tbl

    async def uploads_execute():
        fetch_n["n"] += 1
        # first fetch: missing qc; second (post-QC): rejected
        data = [still_pre] if fetch_n["n"] == 1 else [still_post]
        return MagicMock(data=data)

    uploads_tbl.execute = uploads_execute

    md_tbl = MagicMock()
    md_tbl.update.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=MagicMock(data=None))

    cap_tbl = MagicMock()
    cap_tbl.select.return_value = cap_tbl
    cap_tbl.eq.return_value = cap_tbl
    cap_tbl.execute = AsyncMock(return_value=MagicMock(data=[]))

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "capture_sessions":
            return cap_tbl
        return MagicMock()

    db = MagicMock()
    db.table.side_effect = table

    qc_calls = []

    async def fake_qc(job_id):
        qc_calls.append(job_id)
        return {"approved": 0, "rejected": 1, "flagged": 0}

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(pp, "_delivery_urls_from_capture_sessions", lambda *a, **k: [])

    folded = asyncio.run(pp._fold_late_into_existing_delivery("job-qc", existing))

    assert qc_calls == ["job-qc"]
    # rejected still must not appear; floorplan-only merge may still return False
    # if nothing changed — but prune of nothing + no new stills → False is ok,
    # OR if somehow patch happens, u-late must be absent.
    if folded:
        urls = md_tbl.update.call_args[0][0]["delivery_urls"]
        assert not any(u.get("upload_id") == "u-late" for u in urls)
    else:
        assert not md_tbl.update.called
    assert fetch_n["n"] >= 2


def test_late_fold_skips_qc_when_stills_already_have_qc_status(monkeypatch):
    """Late-fold: every ready listing still already has qc_status → no QC call."""
    existing = {
        "id": "md-ok",
        "delivery_urls": [
            {
                "url": "https://api.brxs.ca/api/public/floorplans/job-ok/sess-fp",
                "filename": "Floor plan",
                "session_id": "sess-fp",
                "kind": "floorplan",
            },
        ],
    }
    still = {
        "id": "u-ok",
        "filename": "living.jpg",
        "file_url": "raw/living.jpg",
        "processed_url": "https://cdn.example/living.jpg",
        "processing_status": "done",
        "qc_status": "accepted",
        "provider": None,
    }

    uploads_tbl = _uploads_chain([still])
    md_tbl = MagicMock()
    md_tbl.update.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=MagicMock(data=None))

    cap_tbl = MagicMock()
    cap_tbl.select.return_value = cap_tbl
    cap_tbl.eq.return_value = cap_tbl
    cap_tbl.execute = AsyncMock(return_value=MagicMock(data=[]))

    def table(name):
        if name == "uploads":
            return uploads_tbl
        if name == "media_delivery":
            return md_tbl
        if name == "capture_sessions":
            return cap_tbl
        return MagicMock()

    db = MagicMock()
    db.table.side_effect = table
    qc_calls = []

    async def fake_qc(job_id):
        qc_calls.append(job_id)
        return {}

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(pp, "_delivery_urls_from_capture_sessions", lambda *a, **k: [])

    folded = asyncio.run(pp._fold_late_into_existing_delivery("job-ok", existing))

    assert qc_calls == []
    assert folded is True
    urls = md_tbl.update.call_args[0][0]["delivery_urls"]
    assert any(u.get("upload_id") == "u-ok" for u in urls)


def test_late_fold_capture_only_no_listing_stills_skips_qc(monkeypatch):
    """Capture-only fold (no listing stills needing QC) → no run_qc_for_job."""
    existing = {
        "id": "md-cap",
        "delivery_urls": [],
    }
    fp = {
        "id": "sess-fp",
        "kind": "floorplan",
        "status": "delivered",
        "output_storage_path": "capture/job-cap/sess-fp/plan.png",
    }

    uploads_tbl = _uploads_chain([])
    md_tbl = MagicMock()
    md_tbl.update.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=MagicMock(data=None))

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

    db = MagicMock()
    db.table.side_effect = table
    qc_calls = []

    async def fake_qc(job_id):
        qc_calls.append(job_id)
        return {}

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(
        pp,
        "_delivery_urls_from_capture_sessions",
        lambda job_id, sessions: [
            {
                "url": f"https://api.brxs.ca/api/public/floorplans/{job_id}/sess-fp",
                "filename": "Floor plan",
                "session_id": "sess-fp",
                "kind": "floorplan",
            }
        ]
        if sessions
        else [],
    )

    folded = asyncio.run(pp._fold_late_into_existing_delivery("job-cap", existing))

    assert qc_calls == []
    assert folded is True
    urls = md_tbl.update.call_args[0][0]["delivery_urls"]
    assert any(u.get("session_id") == "sess-fp" for u in urls)


def test_late_fold_source_documents_qc_before_merge():
    src = Path(pp.__file__).read_text()
    fn = src.split("async def _fold_late_into_existing_delivery")[1].split(
        "async def _fold_capture_into_existing_delivery"
    )[0]
    assert "Runs QC on listing stills missing qc_status before fold" in fn
    assert "still no payout/email" in fn
    assert "run_qc_for_job" in fn
    # QC before building still delivery urls
    assert fn.find("run_qc_for_job") < fn.find("_listing_still_delivery_urls")
