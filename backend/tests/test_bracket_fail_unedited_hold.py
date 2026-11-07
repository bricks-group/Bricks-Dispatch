"""Bracket merge failure folds to one auto-edited primary; unedited originals are QC-held."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock

from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def _install(monkeypatch, *, bracket, single):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(_storage, _job, uid, _data):
        return f"processed/{uid}.jpg"

    async def fake_qc(*_a, **_k):
        return None

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = bracket
    fake_pes.enhance_image = single
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else (-2.0 if "-" in n else 2.0)
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (b, {"export": {}})
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setenv("POSTPROD_STILLS_PROVIDER", "inhouse")
    return updates


GROUP = [
    {"id": "u1", "file_url": "p1", "filename": "lounge_-2.jpg"},
    {"id": "u2", "file_url": "p2", "filename": "lounge_0.jpg"},
    {"id": "u3", "file_url": "p3", "filename": "lounge_+2.jpg"},
]


def _boom(*_a, **_k):
    raise RuntimeError("merge exploded")


def test_bracket_failure_delivers_one_edited_primary_not_every_exposure(monkeypatch):
    updates = _install(
        monkeypatch,
        bracket=_boom,
        single=lambda *_a, **_k: (b"\xff\xd8single", {}),
    )
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-b", list(GROUP), apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert by_id["u2"]["provider"] == "bricks_inhouse"
    assert by_id["u2"]["processed_url"] == "processed/u2.jpg"
    for sib in ("u1", "u3"):
        assert by_id[sib]["provider"] == "bracket_sibling"
        assert by_id[sib]["processed_url"] == "processed/u2.jpg"
    assert "qc_status" not in by_id["u2"]


def test_bracket_and_single_failure_holds_unedited_primary(monkeypatch):
    updates = _install(monkeypatch, bracket=_boom, single=_boom)
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-c", list(GROUP), apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert by_id["u2"]["provider"] == "original"
    assert by_id["u2"]["qc_status"] == "rejected"
    assert "unedited original held" in by_id["u2"]["qc_notes"]
    assert by_id["u1"]["provider"] == "bracket_sibling"
    assert by_id["u3"]["provider"] == "bracket_sibling"


def test_single_vendor_photo_enhance_failure_is_held(monkeypatch):
    updates = _install(monkeypatch, bracket=_boom, single=_boom)
    out = _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-d",
        {"id": "s1", "file_url": "raw/s1.jpg", "filename": "kitchen.jpg"},
    ))
    (uid, upd), = updates
    assert uid == "s1" and upd["provider"] == "original"
    assert upd["qc_status"] == "rejected" and upd["qc_score"] == 0
    assert out == "raw/s1.jpg"


def test_run_qc_preserves_unedited_original_hold(monkeypatch):
    from app.services import qc_agent

    writes = []
    rows = [
        {"id": "o1", "file_url": "f", "processed_url": "f", "qc_variant_url": None,
         "qc_status": "rejected", "processing_status": "done",
         "filename": "kitchen.jpg", "provider": "original"},
    ]

    class Q:
        def __init__(self, table):
            self.table = table
            self._upd = None

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
                return MagicMock(data={"package_name": "Essentials"})
            return MagicMock(data=rows)

    class DB:
        def table(self, name):
            return Q(name)

    async def fake_get_db():
        return DB()

    async def no_review(*_a, **_k):
        raise AssertionError("held unedited original must not be re-scored")

    fake_db_mod = ModuleType("app.shared.database")
    fake_db_mod.get_db = fake_get_db
    fake_db_mod.maybe_single_row = lambda res: (
        res.data if isinstance(getattr(res, "data", None), dict) else {}
    )
    monkeypatch.setitem(sys.modules, "app.shared.database", fake_db_mod)
    monkeypatch.setattr(qc_agent, "run_qc_on_upload", no_review)

    summary = _run(qc_agent.run_qc_for_job("job-q"))
    assert summary["rejected"] == 1
    assert writes and writes[0][1]["qc_status"] == "rejected"


# --- Source download miss (Storage 402 + Blob miss) ---------------------------


def _install_dl(monkeypatch, missing, *, bracket, single):
    updates = _install(monkeypatch, bracket=bracket, single=single)

    async def dl(path):
        return None if path in missing else b"\xff\xd8fakejpg"

    sys.modules["app.services.storage"].download_bytes = dl
    return updates


def _never(*_a, **_k):
    raise AssertionError("enhance must not run without source bytes")


def test_single_vendor_photo_download_miss_is_held_not_as_is(monkeypatch):
    """No source bytes → nothing auto-edited → hold the raw, never provider=as_is."""
    updates = _install_dl(monkeypatch, {"raw/s1.jpg"}, bracket=_never, single=_never)
    out = _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-dl",
        {"id": "s1", "file_url": "raw/s1.jpg", "filename": "kitchen.jpg"},
    ))
    (uid, upd), = updates
    assert uid == "s1"
    assert upd["provider"] == "original"
    assert upd["qc_status"] == "rejected" and upd["qc_score"] == 0
    assert upd["processing_status"] == "done"
    assert out == "raw/s1.jpg"


def test_twilight_download_miss_is_held_not_as_is(monkeypatch):
    updates = _install_dl(monkeypatch, {"raw/dusk.jpg"}, bracket=_never, single=_never)
    _run(pp.deliver_asset_without_vendor(
        MagicMock(storage=MagicMock()), "job-dl",
        {"id": "t1", "file_url": "raw/dusk.jpg", "filename": "dusk.jpg",
         "media_type": "twilight"},
    ))
    (uid, upd), = updates
    assert uid == "t1"
    assert upd["provider"] == "original"
    assert upd["qc_status"] == "rejected"


def test_bracket_partial_download_miss_folds_missing_exposure(monkeypatch):
    """Merged primary delivers; the exposure whose download missed is not left pending."""
    updates = _install_dl(
        monkeypatch, {"p3"},
        bracket=lambda frames, *_a, **_k: (b"\xff\xd8merged", {}),
        single=_never,
    )
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-p", list(GROUP), apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert set(by_id) == {"u1", "u2", "u3"}
    assert by_id["u2"]["provider"] == "bricks_inhouse"
    for sib in ("u1", "u3"):
        assert by_id[sib]["provider"] == "bracket_sibling"
        assert by_id[sib]["processing_status"] == "done"
        assert by_id[sib]["processed_url"] == "processed/u2.jpg"


def test_bracket_one_frame_loaded_folds_the_rest(monkeypatch):
    updates = _install_dl(
        monkeypatch, {"p1", "p3"},
        bracket=_never,
        single=lambda *_a, **_k: (b"\xff\xd8single", {}),
    )
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-o", list(GROUP), apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert set(by_id) == {"u1", "u2", "u3"}
    assert by_id["u2"]["provider"] == "bricks_inhouse"
    for sib in ("u1", "u3"):
        assert by_id[sib]["provider"] == "bracket_sibling"
        assert by_id[sib]["processed_url"] == "processed/u2.jpg"


def test_bracket_all_downloads_miss_holds_primary_folds_siblings(monkeypatch):
    """Used to return with every exposure still pending (finalize blocked)."""
    updates = _install_dl(monkeypatch, {"p1", "p2", "p3"}, bracket=_never, single=_never)
    _run(pp.deliver_bracket_group_without_vendor(
        MagicMock(storage=MagicMock()), "job-a", list(GROUP), apply_sky=True
    ))
    by_id = {uid: u for uid, u in updates}
    assert set(by_id) == {"u1", "u2", "u3"}
    assert by_id["u2"]["provider"] == "original"
    assert by_id["u2"]["qc_status"] == "rejected"
    for sib in ("u1", "u3"):
        assert by_id[sib]["provider"] == "bracket_sibling"
        assert by_id[sib]["processing_status"] == "done"
