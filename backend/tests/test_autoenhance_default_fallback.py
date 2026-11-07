"""Kam Oct 31: Autoenhance is the stills default; no credits / 401 → in-house, job never fails."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import autoenhance_service as ae
from app.services import post_production as pp


def _run(coro):
    return asyncio.run(coro)


def _fake_bracket_env(monkeypatch, updates, uploaded):
    async def fake_safe(_db, uid, update):
        updates.append((uid, update))

    async def fake_dl(_path):
        return b"\xff\xd8fakejpg"

    async def fake_qc(*_a, **_k):
        return None

    async def fake_up(_storage, _job, _uid, data):
        uploaded.append(data)
        return "processed/path.jpg"

    fake_pes = ModuleType("app.services.photo_enhance_service")
    fake_pes.enhance_bracket = lambda *_a, **_k: (b"\xff\xd8inhouse", {"provider": "bricks_inhouse"})
    fake_pes.exposure_bias_from_name = lambda n: 0.0 if "_0" in n else -2.0
    fake_pes.upload_enhanced = fake_up
    fake_pes.upload_qc_variant = fake_qc
    fake_pes.apply_listing_export = lambda b: (b, {"listing_export_applied": True, "export": {}})
    monkeypatch.setitem(sys.modules, "app.services.photo_enhance_service", fake_pes)
    fake_storage = ModuleType("app.services.storage")
    fake_storage.download_bytes = fake_dl
    monkeypatch.setitem(sys.modules, "app.services.storage", fake_storage)
    monkeypatch.setattr(pp, "_safe_update_upload", fake_safe)


GROUP = [
    {"id": "u1", "file_url": "p1", "filename": "room_-2.jpg"},
    {"id": "u2", "file_url": "p2", "filename": "room_0.jpg"},
]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("POSTPROD_STILLS_PROVIDER", raising=False)
    ae.clear_plan_block()
    monkeypatch.setattr(pp, "_ae_deadline", None)

    async def _no_alert(*_a, **_k):
        return True
    monkeypatch.setattr(pp, "alert_autoenhance_hold", _no_alert)
    yield
    ae.clear_plan_block()


def test_default_env_calls_autoenhance(monkeypatch):
    updates, uploaded, calls = [], [], []
    _fake_bracket_env(monkeypatch, updates, uploaded)
    monkeypatch.setattr(ae, "is_available", lambda: True)

    def fake_ae(frames, names, apply_sky=False, timeout_sec=None):
        calls.append(timeout_sec)
        return b"\xff\xd8ae", {"provider": "autoenhance"}

    monkeypatch.setattr(ae, "enhance_brackets_sync", fake_ae)
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "j", GROUP, apply_sky=True))
    assert calls and calls[0] <= pp._AE_DEFAULT_CALL_SEC
    assert any(u.get("provider") == "autoenhance" for _, u in updates)
    assert uploaded[0] == b"\xff\xd8ae"


def _http_resp(status, body='{"message":"Unauthorized"}'):
    class R:
        status_code = status
        headers = {"Content-Type": "application/json"}
        text = body
        content = body.encode()

        def json(self):
            import json
            return json.loads(body)

    return R()


@pytest.mark.parametrize("status,body", [
    (401, '{"message":"Unauthorized"}'),
    (402, '{"message":"Insufficient credits"}'),
    (403, '{"message":"The user has no plan"}'),
])
def test_ae_refusal_on_create_order_blocks_worker_and_holds(monkeypatch, status, body):
    """Real enhance_brackets_sync against a refusing AE: job still delivers in-house."""
    updates, uploaded = [], []
    _fake_bracket_env(monkeypatch, updates, uploaded)
    monkeypatch.setattr(ae, "api_key", lambda: "k" * 36)

    class FakeClient:
        def __init__(self, *a, **k):
            self.posts = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _http_resp(status, body)

        def get(self, *a, **k):
            raise AssertionError("must not poll after refusal")

        def put(self, *a, **k):
            raise AssertionError("must not upload after refusal")

    monkeypatch.setattr(ae, "httpx", SimpleNamespace(Client=FakeClient))
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "j", GROUP, apply_sky=True))
    assert ae.plan_blocked() is True
    assert ae.is_available() is False
    # Kam 2026-11-07: held for Autoenhance, never the in-house fallback.
    prim = [u for uid, u in updates if uid == "u2"]
    assert prim and prim[0]["provider"] == "autoenhance_held"
    assert prim[0]["processing_status"] == "held_autoenhance"
    assert uploaded == []


def test_no_credits_on_download_holds_and_blocks(monkeypatch):
    updates, uploaded = [], []
    _fake_bracket_env(monkeypatch, updates, uploaded)
    monkeypatch.setattr(ae, "api_key", lambda: "k" * 36)
    monkeypatch.setattr(ae, "_create_order", lambda *a, **k: "ord")
    monkeypatch.setattr(ae, "_upload_bracket", lambda *a, **k: None)
    monkeypatch.setattr(ae, "_process_order", lambda *a, **k: None)
    monkeypatch.setattr(ae, "_poll_order", lambda *a, **k: {"images": [{"image_id": "img"}]})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _http_resp(402, '{"message":"No Plan"}')

    monkeypatch.setattr(ae, "httpx", SimpleNamespace(Client=FakeClient))
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "j", GROUP, apply_sky=True))
    assert ae.plan_blocked() is True
    assert updates and all(u["processing_status"] == "held_autoenhance" for _, u in updates)
    assert not any(u.get("provider") == "bricks_inhouse" for _, u in updates)


def test_ae_budget_spent_skips_autoenhance(monkeypatch):
    updates, uploaded = [], []
    _fake_bracket_env(monkeypatch, updates, uploaded)
    monkeypatch.setattr(ae, "is_available", lambda: True)
    monkeypatch.setattr(ae, "enhance_brackets_sync", lambda *a, **k: (_ for _ in ()).throw(AssertionError("budget spent")))
    monkeypatch.setenv("AUTOENHANCE_RUN_BUDGET_SEC", "0")
    pp.start_ae_budget()
    assert pp.ae_call_timeout_sec() is None
    _run(pp.deliver_bracket_group_without_vendor(MagicMock(storage=MagicMock()), "j", GROUP, apply_sky=True))
    # Budget spent: held quietly and re-run on the next sweep (no in-house).
    assert updates and all(u["processing_status"] == "held_autoenhance" for _, u in updates)
    assert pp.AE_BUDGET_REASON in updates[0][1]["qc_notes"]


def test_ae_budget_caps_call_timeout(monkeypatch):
    monkeypatch.setenv("AUTOENHANCE_RUN_BUDGET_SEC", "60")
    pp.start_ae_budget()
    t = pp.ae_call_timeout_sec()
    assert t is not None and 55 <= t <= 60
    assert pp._ae_run_budget_sec() == 60
    monkeypatch.setenv("AUTOENHANCE_RUN_BUDGET_SEC", "junk")
    assert pp._ae_run_budget_sec() == 120.0
    # poll default fits well under the 300s Vercel maxDuration
    monkeypatch.delenv("AUTOENHANCE_RUN_BUDGET_SEC")
    assert pp._ae_run_budget_sec() < 300
