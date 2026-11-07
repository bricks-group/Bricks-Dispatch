"""Job QC reviews the merged HDR primary once, not every bracket exposure."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock

from app.services.qc_hold_notify import held_rows


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _rows():
    rows = [{"id": "p1", "file_url": "raw/k0.jpg", "processed_url": "out/k.jpg",
             "qc_variant_url": "qc/k.jpg", "qc_status": None, "processing_status": "done",
             "filename": "kitchen_0.jpg", "provider": "bricks_inhouse"}]
    for i, ev in enumerate(("-2", "-1", "+1", "+2")):
        rows.append({"id": f"s{i}", "file_url": f"raw/k{ev}.jpg", "processed_url": "out/k.jpg",
                     "qc_variant_url": None, "qc_status": None, "processing_status": "done",
                     "filename": f"kitchen_{ev}.jpg", "provider": "bracket_sibling"})
    rows.append({"id": "l1", "file_url": "raw/l.jpg", "processed_url": "out/l.jpg",
                 "qc_variant_url": "qc/l.jpg", "qc_status": None, "processing_status": "done",
                 "filename": "living.jpg", "provider": "bricks_inhouse"})
    return rows


def _install(monkeypatch, rows, verdict):
    from app.services import qc_agent
    import app.services.storage as storage

    writes, reviewed = [], []

    class Q:
        def __init__(self, table):
            self.table, self._upd = table, None

        def select(self, *_a, **_k):
            return self

        def update(self, data):
            self._upd = data
            return self

        def eq(self, _k, v):
            if self._upd is not None:
                writes.append((v, self._upd))
            return self

        def maybe_single(self):
            return self

        async def execute(self):
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

    fake_db_mod = ModuleType("app.shared.database")
    fake_db_mod.get_db = fake_get_db
    fake_db_mod.maybe_single_row = lambda res: (
        res.data if isinstance(getattr(res, "data", None), dict) else {}
    )
    monkeypatch.setitem(sys.modules, "app.shared.database", fake_db_mod)
    monkeypatch.setattr(storage, "get_signed_download_url", fake_sign)
    monkeypatch.setattr(qc_agent, "run_qc_on_upload", fake_review)
    return qc_agent, writes, reviewed


def test_siblings_are_not_reviewed_or_counted(monkeypatch):
    qc_agent, writes, reviewed = _install(monkeypatch, _rows(), "passed")
    summary = _run(qc_agent.run_qc_for_job("job-b"))
    assert [r[0] for r in reviewed] == ["p1", "l1"]
    # primary reviewed on its small QC variant, never the sibling full-res edit
    assert reviewed[0][1] == "https://signed/qc/k.jpg"
    assert summary["total"] == 2 and summary["passed"] == 2
    # 2 listing stills, not 6 — the standard package count check stays honest
    assert summary["count_check"]["actual"] == 2
    assert not any(uid.startswith("s") for uid, _ in writes)


def test_held_bracketed_still_counts_once(monkeypatch):
    qc_agent, writes, _ = _install(monkeypatch, _rows(), "rejected")
    summary = _run(qc_agent.run_qc_for_job("job-b"))
    assert summary["rejected"] == 2  # kitchen primary + living, not 6


def test_hold_notice_ignores_stale_sibling_verdicts():
    rows = [
        {"id": "p1", "filename": "kitchen_0.jpg", "qc_status": "rejected", "provider": "bricks_inhouse"},
        {"id": "s0", "filename": "kitchen_-2.jpg", "qc_status": "rejected", "provider": "bracket_sibling"},
        {"id": "s1", "filename": "kitchen_+2.jpg", "qc_status": "rejected", "provider": "Bracket_Sibling"},
        # Visit container holds are real (owner notice) — only bracket siblings are skipped.
        {"id": "v1", "filename": "scan.splat", "qc_status": "rejected", "provider": "visit",
         "qc_notes": "Visit file is empty."},
        {"id": "o1", "filename": "bath.jpg", "qc_status": "rejected", "provider": "original"},
    ]
    assert [u["id"] for u in held_rows(rows)] == ["p1", "v1", "o1"]
