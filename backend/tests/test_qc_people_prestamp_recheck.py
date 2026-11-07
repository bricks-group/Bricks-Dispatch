"""Enhance-time people pre-stamp is not a QC review.

post_production stamps qc_status=flagged (PropTx 5.01(d) persons/faces) when
auto-edit sees a face. run_qc_for_job / late-fold read that as an existing
verdict, so crooked/dark/blur QC never ran on the delivered edit and a bad
still with a face in it (mirror, artwork, photographer) reached the client.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.qc_media_kinds import has_qc_verdict, is_enhance_people_prestamp
from tests.test_qc_pending_default import _install, _run, _still

PRESTAMP = "PropTx 5.01(d) persons/faces — mls_quarantine soft-fail; qc_face_count=2"


def test_prestamp_detected_only_for_exact_enhance_note():
    row = _still(qc_status="flagged", qc_notes=PRESTAMP)
    assert has_qc_verdict(row) and is_enhance_people_prestamp(row)
    # QC's own people note (appended to its verdict) is a real review — keep.
    qc_own = _still(
        qc_status="flagged",
        qc_notes="Slight tilt PropTx 5.01(d) persons/faces — mls_quarantine soft-fail",
    )
    assert not is_enhance_people_prestamp(qc_own)
    qc_bare = _still(
        qc_status="flagged",
        qc_notes=" PropTx 5.01(d) persons/faces — mls_quarantine soft-fail",
    )
    assert not is_enhance_people_prestamp(qc_bare)
    # Other statuses / kinds untouched.
    assert not is_enhance_people_prestamp(_still(qc_status="passed", qc_notes=PRESTAMP))
    assert not is_enhance_people_prestamp(_still(qc_status="rejected", qc_notes=PRESTAMP))
    assert not is_enhance_people_prestamp(
        _still(qc_status="flagged", qc_notes=PRESTAMP, filename="tour.mp4")
    )


def test_run_qc_reviews_prestamped_still(monkeypatch):
    """Was: flagged pre-stamp counted as verdict → crooked/dark edit delivered."""
    qc_agent, writes, reviewed, _ = _install(
        monkeypatch, [_still(qc_status="flagged", qc_notes=PRESTAMP)], "rejected"
    )
    summary = _run(qc_agent.run_qc_for_job("job-1"))
    assert [r[0] for r in reviewed] == ["p1"]
    assert reviewed[0][1] == "https://signed/qc/k.jpg"
    assert writes and writes[0][1]["qc_status"] == "rejected"
    assert summary["rejected"] == 1 and summary.get("flagged", 0) == 0


def test_run_qc_keeps_real_flagged_verdict(monkeypatch):
    qc_agent, writes, reviewed, _ = _install(
        monkeypatch,
        [_still(qc_status="flagged", qc_notes="Slight tilt PropTx 5.01(d) persons/faces — mls_quarantine soft-fail")],
        "rejected",
    )
    summary = _run(qc_agent.run_qc_for_job("job-1"))
    assert reviewed == [] and writes == []
    assert summary["flagged"] == 1


def test_run_qc_skips_prestamped_bracket_sibling(monkeypatch):
    qc_agent, writes, reviewed, _ = _install(
        monkeypatch,
        [_still(qc_status="flagged", qc_notes=PRESTAMP, provider="bracket_sibling")],
        "rejected",
    )
    _run(qc_agent.run_qc_for_job("job-1"))
    assert reviewed == []


def test_late_fold_runs_qc_on_prestamped_still(monkeypatch):
    from app.routers import post_production as pp

    existing = {"id": "md", "delivery_urls": []}
    pre = {
        "id": "u1", "filename": "living.jpg", "file_url": "raw/living.jpg",
        "processed_url": "https://cdn.example/living.jpg", "processing_status": "done",
        "qc_status": "flagged", "qc_notes": PRESTAMP, "provider": "bricks_inhouse",
    }
    post = {**pre, "qc_status": "rejected", "qc_notes": "underexposed"}
    n = {"n": 0}

    uploads_tbl = MagicMock()
    uploads_tbl.select.return_value = uploads_tbl
    uploads_tbl.eq.return_value = uploads_tbl

    async def uploads_execute():
        n["n"] += 1
        return MagicMock(data=[pre] if n["n"] == 1 else [post])

    uploads_tbl.execute = uploads_execute
    md_tbl = MagicMock()
    md_tbl.update.return_value = md_tbl
    md_tbl.eq.return_value = md_tbl
    md_tbl.execute = AsyncMock(return_value=MagicMock(data=None))
    cap_tbl = MagicMock()
    cap_tbl.select.return_value = cap_tbl
    cap_tbl.eq.return_value = cap_tbl
    cap_tbl.execute = AsyncMock(return_value=MagicMock(data=[]))
    db = MagicMock()
    db.table.side_effect = lambda name: {
        "uploads": uploads_tbl, "media_delivery": md_tbl, "capture_sessions": cap_tbl,
    }.get(name, MagicMock())

    qc_calls = []

    async def fake_qc(job_id):
        qc_calls.append(job_id)
        return {"rejected": 1}

    monkeypatch.setattr(pp, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(pp, "run_qc_for_job", fake_qc)
    monkeypatch.setattr(pp, "_delivery_urls_from_capture_sessions", lambda *a, **k: [])
    import app.services.qc_hold_notify as qhn
    monkeypatch.setattr(qhn, "notify_owner_qc_hold", AsyncMock(return_value=False))

    asyncio.run(pp._fold_late_into_existing_delivery("job-late", existing))
    assert qc_calls == ["job-late"]
