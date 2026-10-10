"""uploads.qc_status defaults to 'pending' — that is not a QC verdict.

Bricks Canada public.uploads inserts qc_status='pending'. run_qc_for_job and
late-fold read any non-empty qc_status as "already reviewed", so every fresh
listing still reached the client with no QC. Under Fair-Use PostgREST 402 the
QC reads/writes also raised (no PG fallback) and finalize delivered anyway.
"""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

from app.services.qc_media_kinds import has_qc_verdict


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _still(**kw):
    row = {
        "id": "p1", "file_url": "raw/k.jpg", "processed_url": "out/k.jpg",
        "qc_variant_url": "qc/k.jpg", "qc_status": "pending", "qc_notes": None,
        "processing_status": "done", "filename": "fuze_0.jpg", "provider": "autoenhance",
    }
    row.update(kw)
    return row


def _install(monkeypatch, rows, verdict="rejected", rest_402=False):
    from app.services import qc_agent
    import app.services.storage as storage
    import app.shared.pg_fallback as pgf

    writes, reviewed, pg_writes = [], [], []

    class Q:
        def __init__(self, table):
            self.table, self._upd = table, None

        def select(self, *_a, **_k):
            return self

        def update(self, data):
            self._upd = data
            return self

        def eq(self, _k, v):
            if self._upd is not None and not rest_402:
                writes.append((v, self._upd))
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            if rest_402:
                raise Exception("402 exceed_storage_size_quota")
            if self.table == "jobs":
                return MagicMock(data={"package_name": "standard"})
            return MagicMock(data=rows)

    class DB:
        def table(self, name):
            return Q(name)

    async def fake_get_db():
        return DB()

    async def fake_sign(path, expiry=300):
        return f"https://signed/{path}"

    async def fake_review(upload_id, url, **_k):
        reviewed.append((upload_id, url))
        return {"qc_status": verdict, "qc_score": 40 if verdict == "rejected" else 95,
                "qc_notes": "x", "qc_source": "opencv"}

    def fake_pg_update(uid, fields):
        pg_writes.append((uid, fields))
        return True

    fake_db_mod = ModuleType("app.shared.database")
    fake_db_mod.get_db = fake_get_db
    fake_db_mod.maybe_single_row = lambda res: (
        res.data if isinstance(getattr(res, "data", None), dict) else {}
    )
    monkeypatch.setitem(sys.modules, "app.shared.database", fake_db_mod)
    monkeypatch.setattr(storage, "get_signed_download_url", fake_sign)
    monkeypatch.setattr(qc_agent, "run_qc_on_upload", fake_review)
    monkeypatch.setattr(pgf, "pg_select_job_by_id", lambda jid: {"package_name": "standard"})
    monkeypatch.setattr(pgf, "pg_select_uploads_by_job_id", lambda jid, **k: rows)
    monkeypatch.setattr(pgf, "pg_update_upload", fake_pg_update)
    return qc_agent, writes, reviewed, pg_writes


def test_has_qc_verdict_ignores_db_default_pending():
    assert not has_qc_verdict({"qc_status": "pending"})
    assert not has_qc_verdict({"qc_status": None})
    assert not has_qc_verdict({"qc_status": " "})
    assert not has_qc_verdict({})
    assert not has_qc_verdict({"qc_status": "Pending "})
    # real + legacy verdicts (e.g. manual accepted/approved) stay untouched
    for v in ("passed", "flagged", "rejected", "Rejected", "accepted", "approved"):
        assert has_qc_verdict({"qc_status": v})


def test_pending_default_still_is_reviewed_and_held(monkeypatch):
    """Was: 'pending' counted as an existing verdict → never reviewed → delivered."""
    qc_agent, writes, reviewed, _ = _install(monkeypatch, [_still()], "rejected")
    summary = _run(qc_agent.run_qc_for_job("job-1"))
    assert [r[0] for r in reviewed] == ["p1"]
    assert writes and writes[0][0] == "p1" and writes[0][1]["qc_status"] == "rejected"
    assert summary["rejected"] == 1 and "pending" not in summary
    assert summary["count_check"]["actual"] == 1


def test_real_verdict_still_kept(monkeypatch):
    qc_agent, writes, reviewed, _ = _install(
        monkeypatch, [_still(qc_status="passed")], "rejected"
    )
    summary = _run(qc_agent.run_qc_for_job("job-1"))
    assert reviewed == [] and writes == []
    assert summary["passed"] == 1


def test_in_flight_upload_not_reviewed_on_raw(monkeypatch):
    """processing_status pending/processing: a raw-file verdict would stick forever."""
    rows = [
        _still(id="raw1", processed_url=None, qc_variant_url=None, processing_status="pending"),
        _still(id="raw2", processed_url=None, qc_variant_url=None, processing_status="processing"),
        _still(id="p1"),
    ]
    qc_agent, writes, reviewed, _ = _install(monkeypatch, rows, "passed")
    _run(qc_agent.run_qc_for_job("job-1"))
    assert [r[0] for r in reviewed] == ["p1"]
    assert {w[0] for w in writes} == {"p1"}


def test_fair_use_402_reads_and_writes_via_postgres(monkeypatch):
    """PostgREST 402: QC used to raise → finalize delivered with no verdict."""
    qc_agent, writes, reviewed, pg_writes = _install(
        monkeypatch, [_still()], "rejected", rest_402=True
    )
    summary = _run(qc_agent.run_qc_for_job("job-1"))
    assert [r[0] for r in reviewed] == ["p1"]
    assert pg_writes and pg_writes[0][0] == "p1"
    assert pg_writes[0][1]["qc_status"] == "rejected"
    assert summary["rejected"] == 1


def test_late_fold_runs_qc_on_pending_default_still(monkeypatch):
    from app.routers import post_production as pp

    existing = {"id": "md", "delivery_urls": []}
    pre = {
        "id": "u1", "filename": "living.jpg", "file_url": "raw/living.jpg",
        "processed_url": "https://cdn.example/living.jpg", "processing_status": "done",
        "qc_status": "pending", "provider": "autoenhance",
    }
    post = {**pre, "qc_status": "rejected"}
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

    folded = asyncio.run(pp._fold_late_into_existing_delivery("job-late", existing))
    assert qc_calls == ["job-late"]
    assert folded is False  # held still never lands on the listing
