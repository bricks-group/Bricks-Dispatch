"""Autoenhance v3 leftover path: prefer AE when available; fail-fast to inhouse; no async v2."""
from __future__ import annotations

from pathlib import Path

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import post_production as pp
from app.services import autoenhance_service as ae


def _run(coro):
    return asyncio.run(coro)


def test_process_job_assets_never_async_ae_for_vendor_photo():
    """Launch path: vendor_photo must go leftover (sync HDR), not async AE v2."""
    src = Path(pp.__file__).read_text(encoding="utf-8")
    # Guard against regressing to elif AUTOENHANCE_API_KEY: trigger_autoenhance_edit
    assert "trigger_autoenhance_edit(provider_url)" not in src
    assert "leftover.append(asset)" in src
    assert "sync leftover enhance" in src or "Do NOT submit vendor_photo to async" in src



def test_bracket_group_prefers_autoenhance_then_uploads(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(*_a, **_k):
        return "processed/path.jpg"

    async def fake_qc(*_a, **_k):
        return "qc/path.jpg"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("inhouse must not run when AE succeeds")
    )
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else -2.0
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(ae, "is_available", lambda: True)
    monkeypatch.setattr(
        ae,
        "enhance_brackets_sync",
        lambda *a, **k: (b"\xff\xd8ae", {"order_id": "ord-1", "provider": "autoenhance"}),
    )

    group = [
        {"id": "u1", "file_url": "p1", "filename": "room_-2.jpg"},
        {"id": "u2", "file_url": "p2", "filename": "room_0.jpg"},
        {"id": "u3", "file_url": "p3", "filename": "room_+2.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "job-x", group, apply_sky=True))
    providers = {uid: u["provider"] for uid, u in updates}
    assert providers["u2"] == "autoenhance"  # primary ~0 EV
    assert providers["u1"] == "bracket_sibling"
    assert providers["u3"] == "bracket_sibling"


def test_bracket_group_falls_back_inhouse_on_no_plan(monkeypatch):
    updates = []

    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_up(*_a, **_k):
        return "processed/path.jpg"

    async def fake_qc(*_a, **_k):
        return None

    def fake_enhance(*_a, **_k):
        return b"\xff\xd8inhouse", {"provider": "bricks_inhouse"}

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = fake_enhance
    fake_pes.exposure_bias_from_name = lambda n: 0.0
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)

    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)

    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)
    monkeypatch.setattr(ae, "is_available", lambda: True)

    def boom(*_a, **_k):
        raise ae.AutoenhanceNoPlan("No Plan")

    monkeypatch.setattr(ae, "enhance_brackets_sync", boom)

    group = [
        {"id": "a", "file_url": "p1", "filename": "x_-1.jpg"},
        {"id": "b", "file_url": "p2", "filename": "x_0.jpg"},
    ]
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "job-y", group, apply_sky=True))
    assert any(u.get("provider") == "bricks_inhouse" for _, u in updates)


def test_autoenhance_no_plan_marks_worker_blocked(monkeypatch):
    ae._plan_blocked = False
    monkeypatch.setattr(ae, "api_key", lambda: "k" * 36)

    class FakeResp:
        status_code = 402
        headers = {"Content-Type": "application/json"}
        content = b'{"message":"No Plan"}'
        text = '{"message":"No Plan"}'

        def json(self):
            return {"message": "No Plan", "detail": "The user has no plan"}

    class FakeClient:
        def get(self, *a, **k):
            return FakeResp()

    with pytest.raises(ae.AutoenhanceNoPlan):
        ae._download_enhanced(FakeClient(), "k" * 36, "img-1")
    assert ae._plan_blocked is True
    # reset for other tests
    ae._plan_blocked = False
