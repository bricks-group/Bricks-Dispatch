"""Listing-still QC must not pass when image bytes cannot be fetched.

Honesty hole: run_qc_on_upload used to return passed / "QC skipped (image
unavailable)" when _fetch_image_bytes missed, so an unreviewed file could
reach the client. Match download_bytes miss → unedited_original_hold: hold
(reject) with a clear image_unavailable reason that late-fold prune and
hold notices already understand (qc_status=rejected).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import app.services.qc_agent as qc


def _run(coro):
    return asyncio.run(coro)


def test_run_qc_on_upload_fetch_miss_holds_not_passes(monkeypatch):
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    monkeypatch.setattr(qc, "_vision_qc_configured", lambda: True)
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=None))

    def boom(*_a, **_k):
        raise AssertionError("OpenCV must not run when bytes are missing")

    monkeypatch.setattr("app.services.qc_opencv.analyze_image_cv", boom)

    result = _run(qc.run_qc_on_upload("up-miss", "https://example.test/gone.jpg"))
    assert result["qc_status"] == "rejected"
    assert result["qc_score"] == 0
    assert "image_unavailable" in (result.get("qc_notes") or "").lower()
    assert "passed" != result["qc_status"]
    assert "skipped (image unavailable)" not in (result.get("qc_notes") or "").lower()


def test_run_qc_on_upload_empty_bytes_holds(monkeypatch):
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=b""))

    result = _run(qc.run_qc_on_upload("up-empty", "https://example.test/empty.jpg"))
    assert result["qc_status"] == "rejected"
    assert "image_unavailable" in (result.get("qc_notes") or "").lower()


def test_run_qc_for_job_persists_image_unavailable_hold(monkeypatch):
    """Fetch miss on a listing still must land as rejected on the uploads row."""
    monkeypatch.setattr(qc, "QC_ENABLED", True)

    writes = []

    class _Upd:
        def __init__(self, fields):
            self._fields = fields

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            return MagicMock(data=[])

    class _Tbl:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            return MagicMock(data={"package_name": "standard"})

        def update(self, fields):
            return _Upd(fields)

    class _Db:
        def table(self, name):
            if name == "jobs":
                return _Tbl()
            if name == "uploads":

                class _Uploads:
                    def select(self, *_a, **_k):
                        return self

                    def eq(self, *_a, **_k):
                        return self

                    async def execute(self):
                        return MagicMock(
                            data=[
                                {
                                    "id": "still-1",
                                    "filename": "kitchen.jpg",
                                    "file_url": "jobs/j1/kitchen.jpg",
                                    "processed_url": "jobs/j1/kitchen_out.jpg",
                                    "qc_variant_url": None,
                                    "qc_status": "pending",
                                    "qc_notes": None,
                                    "processing_status": "done",
                                    "provider": "bricks_inhouse",
                                }
                            ]
                        )

                    def update(self, fields):
                        class _W:
                            def eq(self, _col, uid):
                                writes.append((uid, fields))
                                return self

                            async def execute(self):
                                return MagicMock(data=[])

                        return _W()

                return _Uploads()
            raise AssertionError(name)

    async def fake_get_db():
        return _Db()

    monkeypatch.setattr("app.shared.database.get_db", fake_get_db)
    monkeypatch.setattr(
        "app.services.storage.get_signed_download_url",
        AsyncMock(return_value="https://signed.example/kitchen.jpg"),
    )
    monkeypatch.setattr(qc, "_fetch_image_bytes", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.services.qc_opencv.verify_deliverable_count",
        lambda *_a, **_k: {"ok": True, "expected": None, "actual": 0, "issue": None},
    )

    counts = _run(qc.run_qc_for_job("j1"))
    assert counts.get("rejected", 0) >= 1
    assert writes, "expected QC persist write"
    uid, fields = writes[0]
    assert uid == "still-1"
    assert fields["qc_status"] == "rejected"
    assert "image_unavailable" in (fields.get("qc_notes") or "").lower()


def _job_db(rows, writes):
    """Minimal get_db stub for run_qc_for_job (jobs + uploads)."""

    class _Upd:
        def __init__(self, fields):
            self._fields = fields

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            return MagicMock(data=[])

    class _Jobs:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        def maybe_single(self):
            return self

        async def execute(self):
            return MagicMock(data={"package_name": "standard"})

        def update(self, fields):
            return _Upd(fields)

    class _Uploads:
        def select(self, *_a, **_k):
            return self

        def eq(self, *_a, **_k):
            return self

        async def execute(self):
            return MagicMock(data=list(rows))

        def update(self, fields):
            class _W:
                def eq(self, _col, uid):
                    writes.append((uid, fields))
                    return self

                async def execute(self):
                    return MagicMock(data=[])

            return _W()

    class _Db:
        def table(self, name):
            if name == "jobs":
                return _Jobs()
            if name == "uploads":
                return _Uploads()
            raise AssertionError(name)

    return _Db()


def test_run_qc_for_job_https_processed_url_passthrough_not_re_signed(monkeypatch):
    """HTTPS Autoenhance/Blob URL must be reviewed, not signed as a Storage path.

    Before: get_signed_download_url(https://…) returned None → continue left
    qc_status=pending → finalize delivered the still unreviewed.
    """
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    writes = []
    https_url = "https://cdn.autoenhance.ai/orders/abc/out.jpg"
    rows = [
        {
            "id": "ae-1",
            "filename": "living.jpg",
            "file_url": "jobs/j1/living.jpg",
            "processed_url": https_url,
            "qc_variant_url": None,
            "qc_status": "pending",
            "qc_notes": None,
            "processing_status": "done",
            "provider": "autoenhance",
        }
    ]

    async def fake_get_db():
        return _job_db(rows, writes)

    async def boom_sign(path, expiry=300):
        raise AssertionError(f"must not re-sign HTTPS path: {path!r}")

    monkeypatch.setattr("app.shared.database.get_db", fake_get_db)
    monkeypatch.setattr("app.services.storage.get_signed_download_url", boom_sign)
    monkeypatch.setattr(
        qc,
        "run_qc_on_upload",
        AsyncMock(
            return_value={
                "qc_status": "passed",
                "qc_score": 95,
                "qc_notes": "ok",
                "qc_source": "opencv",
            }
        ),
    )
    monkeypatch.setattr(
        "app.services.qc_opencv.verify_deliverable_count",
        lambda *_a, **_k: {"ok": True, "expected": None, "actual": 1, "issue": None},
    )

    counts = _run(qc.run_qc_for_job("j1"))
    assert counts.get("passed", 0) >= 1
    assert writes, "expected QC persist"
    uid, fields = writes[0]
    assert uid == "ae-1"
    assert fields["qc_status"] == "passed"
    # run_qc_on_upload received the HTTPS URL directly
    assert qc.run_qc_on_upload.await_args.args[1] == https_url


def test_run_qc_for_job_sign_miss_holds_not_leaves_pending(monkeypatch):
    """Storage sign miss must reject (image_unavailable), not skip as pending."""
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    writes = []
    rows = [
        {
            "id": "still-sign",
            "filename": "kitchen.jpg",
            "file_url": "jobs/j1/kitchen.jpg",
            "processed_url": "jobs/j1/kitchen_out.jpg",
            "qc_variant_url": None,
            "qc_status": "pending",
            "qc_notes": None,
            "processing_status": "done",
            "provider": "bricks_inhouse",
        }
    ]

    async def fake_get_db():
        return _job_db(rows, writes)

    monkeypatch.setattr("app.shared.database.get_db", fake_get_db)
    monkeypatch.setattr(
        "app.services.storage.get_signed_download_url",
        AsyncMock(return_value=None),
    )

    def boom_review(*_a, **_k):
        raise AssertionError("run_qc_on_upload must not run without a URL")

    monkeypatch.setattr(qc, "run_qc_on_upload", boom_review)
    monkeypatch.setattr(
        "app.services.qc_opencv.verify_deliverable_count",
        lambda *_a, **_k: {"ok": True, "expected": None, "actual": 0, "issue": None},
    )

    counts = _run(qc.run_qc_for_job("j1"))
    assert counts.get("rejected", 0) >= 1
    assert writes
    uid, fields = writes[0]
    assert uid == "still-sign"
    assert fields["qc_status"] == "rejected"
    assert "image_unavailable" in (fields.get("qc_notes") or "").lower()
    assert "sign" in (fields.get("qc_notes") or "").lower()


def test_run_qc_for_job_missing_path_holds(monkeypatch):
    """Done listing still with no file/processed path must hold, not stay pending."""
    monkeypatch.setattr(qc, "QC_ENABLED", True)
    writes = []
    rows = [
        {
            "id": "still-nopath",
            "filename": "den.jpg",
            "file_url": None,
            "processed_url": None,
            "qc_variant_url": None,
            "qc_status": "pending",
            "qc_notes": None,
            "processing_status": "done",
            "provider": "bricks_inhouse",
        }
    ]

    async def fake_get_db():
        return _job_db(rows, writes)

    monkeypatch.setattr("app.shared.database.get_db", fake_get_db)
    monkeypatch.setattr(
        "app.services.storage.get_signed_download_url",
        AsyncMock(side_effect=AssertionError("must not sign empty path")),
    )
    monkeypatch.setattr(
        "app.services.qc_opencv.verify_deliverable_count",
        lambda *_a, **_k: {"ok": True, "expected": None, "actual": 0, "issue": None},
    )

    counts = _run(qc.run_qc_for_job("j1"))
    assert counts.get("rejected", 0) >= 1
    assert writes
    uid, fields = writes[0]
    assert uid == "still-nopath"
    assert fields["qc_status"] == "rejected"
    assert "image_unavailable" in (fields.get("qc_notes") or "").lower()
